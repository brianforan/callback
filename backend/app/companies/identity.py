"""Company identity for the Companies engine: one catalog record per company, however it's written.

A company is known by its website domain (and, from chunk 2, its hiring-system ID). Its names are
aliases, kept as matching keys, so "Acme, Inc.", "ACME" and "acme inc" are one company once any of
them is seen. A name that's only close to a known one, such as "Clair" and "Claire", never merges
by itself: a second signal settles it, or the pair waits for one small model check, and every
answer is stored so no pair is asked twice.

Agency wording is split off before matching, so "Syllo (via TLA Tech)" and "TLA Tech (Syllo)" are
the employer Syllo, posted by the agency TLA Tech.

Nothing here commits. Callers commit when their own work is done.
"""

import logging
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    Company,
    CompanyAlias,
    CompanyKind,
    CompanyMatchCheck,
    CompanySource,
    utcnow,
)
from app.postings.parser import company_key

logger = logging.getLogger(__name__)

# Trigram similarity at which two names are worth checking. "clair" and "claire" score 0.625;
# "loop" and "loopio" score 0.5.
SIMILAR_NAMES = 0.6
# Names shorter than this are too short for similarity to mean anything.
_MIN_FUZZY_KEY = 4
_MAX_CANDIDATES = 3
# Aliases read when looking for similar names. Each is scored in Python.
_MAX_PREFILTERED = 500
# Unclear pairs sent to the model in one call.
PAIRS_PER_CHECK = 20

_MAX_NAME = 200

# Staffing and recruiting agencies often named on job boards. A company marked as an agency in
# the catalog counts too, as does a name with one of the words in _AGENCY_WORDS.
_KNOWN_AGENCIES = (
    "Robert Half",
    "TEKsystems",
    "Insight Global",
    "Randstad",
    "Kforce",
    "Aerotek",
    "Adecco",
    "Hays",
    "Michael Page",
    "Jobot",
    "Motion Recruitment",
    "CyberCoders",
    "Apex Systems",
    "Akkodis",
    "Modis",
    "Kelly Services",
    "ManpowerGroup",
    "Manpower",
    "Experis",
    "Beacon Hill Staffing Group",
    "Collabera",
    "Judge Group",
)
_AGENCY_WORDS = re.compile(
    r"(?:\b(?:staffing|recruit(?:ing|ment|ers)?|personnel|talents?|search partners|placement|"
    r"resourcing|headhunt(?:ers|ing)?|hunters?)\b|talent\b|hunt\b)",
    re.I,
)
# "Robert Half (client employer unnamed)": the agency, with no employer to take from it.
_UNNAMED = re.compile(r"\b(?:client|unnamed|confidential|undisclosed|stealth)\b", re.I)

# Agency wording, tried in order. "always" patterns are agency wording whoever is named; the
# others only when the agency side is a known agency, since "Code for America" is one company.
_VIA = re.compile(r"^(?P<employer>.+?)\s*\(\s*via\s+(?P<agency>[^)]+?)\s*\)\s*$", re.I)
_BARE_VIA = re.compile(r"^(?P<employer>.+?)\s+via\s+(?P<agency>.+)$", re.I)
_ON_BEHALF = re.compile(r"^(?P<agency>.+?)\s+on behalf of\s+(?P<employer>.+)$", re.I)
_PARENS = re.compile(r"^(?P<agency>.+?)\s*\(\s*(?P<employer>[^)]+?)\s*\)\s*$")
_FOR = re.compile(r"^(?P<agency>.+?)\s+for\s+(?P<employer>.+)$", re.I)

# Hosts that are where companies list jobs, not company websites.
_PLATFORM_HOSTS = (
    "greenhouse.io",
    "lever.co",
    "ashbyhq.com",
    "myworkdayjobs.com",
    "workday.com",
    "icims.com",
    "smartrecruiters.com",
    "bamboohr.com",
    "jobvite.com",
    "workable.com",
    "linkedin.com",
    "indeed.com",
    "glassdoor.com",
    "ziprecruiter.com",
    "wellfound.com",
)
# Second-level labels under a country code, such as acme.co.uk, kept with the label before them.
_SECOND_LEVEL = {"co", "com", "org", "net", "ac", "gov", "edu"}
_HOST = re.compile(r"^[a-z0-9-]+(?:\.[a-z0-9-]+)+$")


@dataclass(frozen=True)
class CompanyName:
    """A listing's company name split into the employer and the agency that posted for it.

    employer is None when the agency names no employer.
    """

    employer: str | None
    agency: str | None


@dataclass(frozen=True)
class ResolvedCompany:
    """The company a listing is for, and the agency that posted it, if one did.

    When an agency names no employer, company is the agency itself and agency is None.
    """

    company: Company
    agency: Company | None


def _clean(name: str) -> str:
    return " ".join(name.split())[:_MAX_NAME]


def provisional_name(token: str) -> str:
    """A name for a company known only by its hiring-system token, such as "Acme Labs" for
    "acme-labs". A real name replaces it the first time a listing gives one."""
    return " ".join(part.capitalize() for part in token.replace("_", "-").split("-") if part)


def matching_key(name: str) -> str:
    """The key names are matched on: company_key with "&" read as "and", a leading "the" dropped,
    and spaces removed, so "The Open & Co" and "open and" meet, as do "Open AI" and "OpenAI"."""
    key = company_key(name.replace("&", " and "))
    if key.startswith("the "):
        key = key[len("the ") :]
    return key.replace(" ", "")


def _trigrams(key: str) -> set[str]:
    padded = f"  {key} "
    return {padded[index : index + 3] for index in range(len(padded) - 2)}


def trigram_similarity(a: str, b: str) -> float:
    """Shared trigrams over all trigrams, as Postgres's pg_trgm scores one word."""
    first, second = _trigrams(a), _trigrams(b)
    if not first or not second:
        return 0.0
    return len(first & second) / len(first | second)


def company_domain(website: str | None) -> str | None:
    """The domain a company is known by, from its website or a link to it.

    "https://careers.acme.com/jobs" is "acme.com". Hiring systems and job boards are where a
    company lists jobs, not its website, so their links give None.
    """
    if not website:
        return None
    text = website.strip().lower()
    host = urlsplit(text if "//" in text else f"//{text}").hostname or ""
    host = host.removeprefix("www.").rstrip(".")
    if not _HOST.match(host):
        return None
    if any(host == platform or host.endswith(f".{platform}") for platform in _PLATFORM_HOSTS):
        return None
    labels = host.split(".")
    keep = 3 if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in _SECOND_LEVEL else 2
    return ".".join(labels[-keep:])[:255]


_KNOWN_AGENCY_KEYS = frozenset(matching_key(name) for name in _KNOWN_AGENCIES)


def looks_like_agency(db: Session, name: str) -> bool:
    """A known agency: a large one by name, a name with agency words, or one marked so."""
    key = matching_key(name)
    if key in _KNOWN_AGENCY_KEYS or _AGENCY_WORDS.search(name):
        return True
    alias = db.get(CompanyAlias, key)
    return alias is not None and alias.company.kind == CompanyKind.AGENCY.value


def parse_company_name(name: str, is_agency: Callable[[str], bool]) -> CompanyName:
    """Split agency wording off a listing's company name. is_agency says whether a name is a
    known agency, which "Agency (Employer)" and "Agency for Employer" need."""
    text = _clean(name)
    for pattern, always in (
        (_VIA, True),
        (_ON_BEHALF, True),
        (_BARE_VIA, True),
        (_PARENS, False),
        (_FOR, False),
    ):
        found = pattern.match(text)
        if found is None:
            continue
        agency = found.group("agency").strip()
        employer = found.group("employer").strip()
        if not agency or not employer or not (always or is_agency(agency)):
            continue
        if _UNNAMED.search(employer):
            return CompanyName(employer=None, agency=agency)
        return CompanyName(employer=employer, agency=agency)
    return CompanyName(employer=text, agency=None)


def _ordered(first: str, second: str) -> tuple[str, str]:
    return (first, second) if first < second else (second, first)


def _add_alias(db: Session, company: Company, key: str, decided_by: str) -> None:
    if db.get(CompanyAlias, key) is None:
        db.add(CompanyAlias(alias_key=key, company_id=company.id, decided_by=decided_by))


def _similar(db: Session, key: str) -> list[tuple[Company, str, float]]:
    """Up to three other companies with a name like this one, best first, with the alias that
    matched and its score. Read with a cheap prefix-or-suffix filter, then scored in Python."""
    if len(key) < _MIN_FUZZY_KEY:
        return []
    shortest = int(len(key) * 0.6)
    longest = int(len(key) / 0.6) + 1
    rows = db.execute(
        select(CompanyAlias.alias_key, CompanyAlias.company_id)
        .where(
            or_(
                CompanyAlias.alias_key.like(f"{key[:3]}%"),
                CompanyAlias.alias_key.like(f"%{key[-3:]}"),
            ),
            func.length(CompanyAlias.alias_key).between(shortest, longest),
            CompanyAlias.alias_key != key,
        )
        .limit(_MAX_PREFILTERED)
    ).all()
    best: dict[uuid.UUID, tuple[str, float]] = {}
    for alias_key, company_id in rows:
        score = trigram_similarity(key, alias_key)
        if score >= SIMILAR_NAMES and score > best.get(company_id, ("", 0.0))[1]:
            best[company_id] = (alias_key, score)
    ranked = sorted(best.items(), key=lambda item: -item[1][1])[:_MAX_CANDIDATES]
    return [
        (company, alias_key, score)
        for company_id, (alias_key, score) in ranked
        if (company := db.get(Company, company_id)) is not None
    ]


def _created(company: Company) -> datetime:
    """When a company was added, comparable whether or not the database kept the time zone."""
    return company.created_at.replace(tzinfo=None)


def _by_source(db: Session, source: tuple[str, str] | None) -> Company | None:
    """The company whose own hiring system this is, such as ("greenhouse", "acme")."""
    if source is None:
        return None
    row = db.scalar(
        select(CompanySource).where(
            CompanySource.source == source[0], CompanySource.identifier == source[1]
        )
    )
    return db.get(Company, row.company_id) if row is not None else None


def _by_domain(db: Session, domain: str | None) -> Company | None:
    if domain is None:
        return None
    return db.scalar(select(Company).where(Company.domain == domain))


def _by_name(db: Session, key: str) -> Company | None:
    alias = db.get(CompanyAlias, key)
    return alias.company if alias is not None else None


def _known(db: Session, key: str, domain: str | None) -> Company | None:
    """The company this name or domain already belongs to, by its domain first, then its name."""
    return _by_domain(db, domain) or _by_name(db, key)


def _match_similar(
    db: Session, key: str, domain: str | None
) -> tuple[Company, str] | tuple[None, None]:
    """A company whose name is close to this one and is known to be the same, with what decided
    it. Pairs that can't be settled by their domains are queued for a model check, and this name
    gets its own company meanwhile."""
    for company, alias_key, score in _similar(db, key):
        pair = _ordered(key, alias_key)
        check = db.get(CompanyMatchCheck, pair)
        if check is not None and check.same is not None:
            if check.same:
                return company, check.decided_by or "model"
            continue
        if domain is not None and company.domain is not None:
            # Both have a website and they differ (the same one would have matched already).
            if check is None:
                check = CompanyMatchCheck(key_a=pair[0], key_b=pair[1])
                db.add(check)
            check.same = False
            check.decided_by = "source"
            check.decided_at = utcnow()
            continue
        if check is None:
            db.add(CompanyMatchCheck(key_a=pair[0], key_b=pair[1]))
            logger.info("Queued a check: is %r the company %r? (%.2f)", key, alias_key, score)
    return None, None


def _resolve_one(
    db: Session,
    name: str,
    domain: str | None,
    kind: CompanyKind,
    source: tuple[str, str] | None = None,
) -> Company:
    cleaned = _clean(name)
    key = matching_key(cleaned)
    if not key:
        raise ValueError("A company needs a name with a letter or digit in it")
    by_source = _by_source(db, source)
    if by_source is not None:
        # Its own hiring system settles it, whatever name this listing gives.
        named = _by_name(db, key)
        if named is None:
            _add_alias(db, by_source, key, "source")
        if source is not None and by_source.name == provisional_name(source[1]):
            # Never a name that's already another company's.
            if cleaned != by_source.name and named in (None, by_source):
                logger.info("Named %r %r from a listing", by_source.name, cleaned)
                by_source.name, by_source.key = cleaned, company_key(cleaned)
        if domain is not None and by_source.domain is None and _by_domain(db, domain) is None:
            by_source.domain = domain
        db.flush()
        return by_source
    by_domain, by_name = _by_domain(db, domain), _by_name(db, key)
    if by_domain is not None:
        company = by_domain
        if by_name is not None and by_name.id != by_domain.id and by_name.domain is None:
            # The website shows a name seen before without one is this company.
            company, drop = sorted((by_domain, by_name), key=_created)
            merge_companies(db, company, drop)
        decided_by = "source"
    elif by_name is not None:
        if domain is not None and by_name.domain is not None:
            # The same name with another website is another company, such as two Mercurys.
            # The name stays with the first one seen.
            return _create(db, cleaned, key, domain, kind, alias=False)
        company, decided_by = by_name, "exact"
    else:
        similar, decided_by = _match_similar(db, key, domain)
        if similar is None:
            return _create(db, cleaned, key, domain, kind, alias=True)
        company = similar
    _add_alias(db, company, key, decided_by)
    if domain is not None and company.domain is None:
        company.domain = domain
    if kind == CompanyKind.AGENCY:
        company.kind = CompanyKind.AGENCY.value
    db.flush()
    return company


def _create(
    db: Session, name: str, key: str, domain: str | None, kind: CompanyKind, alias: bool
) -> Company:
    company = Company(
        name=name, key=company_key(name), compact_key=key, domain=domain, kind=kind.value
    )
    try:
        # A savepoint, so losing a race undoes only this insert and not the caller's work.
        with db.begin_nested():
            db.add(company)
            db.flush()
            if alias:
                db.add(CompanyAlias(alias_key=key, company_id=company.id, decided_by="exact"))
                db.flush()
    except IntegrityError:
        found = _known(db, key, domain)
        if found is None:
            raise
        logger.info("Another request added the company %r first", name)
        return found
    logger.info("Added the company %r to the catalog", name)
    return company


def resolve_company(
    db: Session,
    name: str,
    *,
    domain: str | None = None,
    source: tuple[str, str] | None = None,
) -> ResolvedCompany:
    """Find or add the company a listing names, and the agency that posted it, if one did.

    domain is the company's website or a link on it. source is the hiring system the listing is
    on, such as ("greenhouse", "acme"), which is the company's own. With agency wording in the
    name, it isn't known whose site or system it is, so both are left out.
    """
    parsed = parse_company_name(name, lambda part: looks_like_agency(db, part))
    agency = _resolve_one(db, parsed.agency, None, CompanyKind.AGENCY) if parsed.agency else None
    if parsed.employer is None:
        if agency is None:
            raise ValueError("A company needs a name with a letter or digit in it")
        return ResolvedCompany(company=agency, agency=None)
    employer_domain = company_domain(domain) if agency is None else None
    employer_source = source if agency is None else None
    employer = _resolve_one(
        db, parsed.employer, employer_domain, CompanyKind.EMPLOYER, employer_source
    )
    return ResolvedCompany(company=employer, agency=agency)


def find_company(db: Session, name: str, source: tuple[str, str] | None = None) -> Company | None:
    """The company a name already belongs to, without adding one: by its own hiring system,
    then by its name. A name with agency wording and no employer finds the agency."""
    parsed = parse_company_name(name, lambda part: looks_like_agency(db, part))
    if parsed.employer is None:
        key = matching_key(_clean(parsed.agency or ""))
        return _by_name(db, key) if key else None
    by_source = _by_source(db, source) if parsed.agency is None else None
    key = matching_key(_clean(parsed.employer))
    return by_source or (_by_name(db, key) if key else None)


def mark_agency(db: Session, name: str) -> Company:
    """Mark a company as an agency, as when a judge says an agency posted a listing."""
    company = _resolve_one(db, name, None, CompanyKind.AGENCY)
    logger.info("Marked %r as an agency", company.name)
    return company


def merge_companies(db: Session, keep: Company, drop: Company) -> None:
    """Make two companies one: drop's names and website move to keep, and drop is deleted.

    Merging is the only way two companies become one. Its sources and jobs move too, and jobs
    that turn out to be the same are merged.
    """
    # Here, not at the top: the catalog resolves companies through this module.
    from app.companies.catalog import move_company_rows

    if keep.id == drop.id:
        return
    logger.info("Merging the company %r into %r", drop.name, keep.name)
    for alias in list(drop.aliases):
        alias.company = keep
    domain = drop.domain
    if drop.kind == CompanyKind.AGENCY.value:
        keep.kind = CompanyKind.AGENCY.value
    db.flush()
    move_company_rows(db, keep, drop)
    db.delete(drop)
    db.flush()
    if keep.domain is None and domain is not None:
        keep.domain = domain
        db.flush()


def _describe(company: Company) -> dict[str, Any]:
    return {"name": company.name, "domain": company.domain or ""}


def check_pending_pairs(
    db: Session,
    ask: Callable[[dict[str, Any]], dict[str, Any]],
    limit: int = PAIRS_PER_CHECK,
    company_ids: set[uuid.UUID] | None = None,
) -> dict[str, Any] | None:
    """Settle up to limit queued pairs with one model check, merging those that are the same.

    ask sends {"pairs": [{"a": {...}, "b": {...}}]} to tailor's /companies/same_company and
    returns its answer: "answers" in the same order, plus the call's cost fields. Returns those
    cost fields, so the run that asked can count them, or None when nothing was waiting. A failed
    call raises from ask (TailorUnavailable) and leaves the pairs waiting. With company_ids, only
    pairs with a side among those companies are checked, so a run pays for its own.
    """
    checks = db.scalars(
        select(CompanyMatchCheck)
        .where(CompanyMatchCheck.same.is_(None))
        .order_by(CompanyMatchCheck.created_at, CompanyMatchCheck.key_a)
        .limit(limit if company_ids is None else _MAX_PREFILTERED)
    ).all()
    if company_ids is not None:
        involved = []
        for check in checks:
            sides = {_by_name(db, check.key_a), _by_name(db, check.key_b)}
            if any(side is not None and side.id in company_ids for side in sides):
                involved.append(check)
        checks = involved[:limit]
    asking: list[tuple[CompanyMatchCheck, Company, Company]] = []
    for check in checks:
        first, second = _by_name(db, check.key_a), _by_name(db, check.key_b)
        if first is not None and second is not None and first.id != second.id:
            asking.append((check, first, second))
            continue
        # One company already (a merge settled it), or a name no longer in the catalog.
        check.same = first is not None and first is second
        check.decided_by = "source"
        check.decided_at = utcnow()
    db.flush()
    if not asking:
        return None
    answer = ask({"pairs": [{"a": _describe(a), "b": _describe(b)} for _, a, b in asking]})
    answers = answer.get("answers") or []
    spent = {
        name: answer[name]
        for name in ("model", "usage", "cost_usd", "cost_breakdown", "request_costs")
        if name in answer
    }
    if len(answers) != len(asking):
        logger.error(
            "The company check answered %d of %d pairs, so none were settled",
            len(answers),
            len(asking),
        )
        return spent
    for (check, _, _), verdict in zip(asking, answers, strict=True):
        same = bool(verdict.get("same"))
        check.same = same
        check.decided_by = "model"
        check.decided_at = utcnow()
        # Looked up again, since an earlier pair in this batch may have merged either side.
        first, second = _by_name(db, check.key_a), _by_name(db, check.key_b)
        if same and first is not None and second is not None and first.id != second.id:
            keep, drop = sorted((first, second), key=_created)
            merge_companies(db, keep, drop)
    db.flush()
    return spent
