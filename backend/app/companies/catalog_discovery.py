"""Finding companies for a profile from the catalog, with no model call.

The catalog holds every company Job listings and Scout have seen, most of them only on job
boards. Those whose open jobs match the brief are candidates: one with a known board of its own
joins the profile's list at once; for the rest, code tries the company's name as a Greenhouse,
Lever and Ashby board. A board counts as the company's only when its jobs share a title with the
jobs the catalog already has for that company (or, on Greenhouse, it names the company), so a
same-named stranger isn't taken for it. A company tried and not found isn't tried again for 30
days.

This runs before Haiku looks for companies on the web, which then runs only when the run's
scan of the catalog came up short.
"""

import logging
import re
import uuid
from collections import Counter
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import timedelta

import httpx
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.companies.catalog import HIRING_SYSTEMS, company_source, title_key
from app.companies.catalog_worker import apply_read
from app.companies.feeds import FeedGone, FeedJob
from app.companies.identity import looks_like_agency, matching_key
from app.companies.lists import add_to_profile
from app.models import (
    CatalogJob,
    Company,
    CompanyKind,
    CompanySource,
    ProfileCompany,
    ProfileCompanyOrigin,
    utcnow,
)
from app.postings.parser import company_key
from app.scout.filters import _abroad, board_location

logger = logging.getLogger(__name__)

# Companies tried per run, the most matching jobs first, and how often one is tried again.
MAX_PROBED = 60
MAX_ADDED_WITH_BOARD = 40
PROBE_AGAIN_AFTER = timedelta(days=30)
_PROBES_AT_ONCE = 8
_TOKEN = re.compile(r"^[a-z0-9_-]{2,100}$")
_READ_ERRORS = (FeedGone, httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError)
_MAX_NAMED = 6

ReadFeed = Callable[[str, str], list[FeedJob]]


@dataclass
class CatalogFinds:
    """What the catalog gave a run, for its notes."""

    added: list[str] = field(default_factory=list)
    probed: int = 0
    found: list[str] = field(default_factory=list)

    def notes(self) -> list[str]:
        if not self.added and not self.probed:
            return []
        parts = [f"{len(self.added)} added to your list" if self.added else "none new to add"]
        if self.probed:
            parts.append(
                f"tried {self.probed} companies' names on Greenhouse, Lever and Ashby, and found "
                f"{len(self.found)}"
            )
        named = ", ".join(self.added[:_MAX_NAMED])
        more = len(self.added) - _MAX_NAMED
        listed = f" ({named}{f' and {more} more' if more > 0 else ''})" if self.added else ""
        said = "; ".join(parts)
        return [f"Looked for companies in Callback's catalog, with no searches: {said}{listed}."]


def board_tokens(name: str) -> list[str]:
    """Board names a company is likely to use: "Acme Labs, Inc." gives acmelabs and acme-labs,
    then acmelabsinc and acme-labs-inc, since some boards keep the suffix."""
    tokens = []
    plain = name.replace("&", " and ").lower()
    for words in (company_key(plain).split(), re.sub(r"[^a-z0-9]+", " ", plain).split()):
        if words and words[0] == "the":
            words = words[1:]
        tokens += ["".join(words), "-".join(words)]
    return [token for token in dict.fromkeys(tokens) if _TOKEN.match(token)]


def _terms(queries: Iterable[str]) -> list[str]:
    """The plain words and phrases of the brief's board searches, for a SQL prefilter."""
    return [term for query in queries if (term := query.strip().strip('"').strip())]


def _candidates(
    db: Session, profile_id: uuid.UUID, terms: list[str], hidden: set[str], location: str = ""
) -> list[tuple[Company, int, bool]]:
    """Companies whose open catalog jobs mention the brief, most matching jobs first, with
    whether they have a board of their own. Only jobs the run's location filter would allow
    count, so a US brief isn't sent to companies hiring only abroad. Agencies, hidden companies
    and ones already readable on the list are left out."""
    if not terms:
        return []
    us_only = board_location(location) == "us"
    matches = or_(
        *(
            condition
            for term in terms
            for condition in (
                CatalogJob.title.ilike(f"%{term}%"),
                CatalogJob.text.ilike(f"%{term}%"),
            )
        )
    )
    counts: Counter[uuid.UUID] = Counter()
    for company_id, job_location in db.execute(
        select(CatalogJob.company_id, CatalogJob.location)
        .join(Company, Company.id == CatalogJob.company_id)
        .where(
            CatalogJob.closed_at.is_(None),
            Company.kind == CompanyKind.EMPLOYER.value,
            matches,
        )
    ):
        if not _abroad(job_location or "", us_only):
            counts[company_id] += 1
    companies = (
        {
            company.id: company
            for company in db.scalars(select(Company).where(Company.id.in_(list(counts))))
        }
        if counts
        else {}
    )
    rows = sorted(
        ((companies[company_id], count) for company_id, count in counts.items()),
        key=lambda row: (-row[1], row[0].name),
    )
    own = set(
        db.scalars(
            select(CompanySource.company_id).where(
                CompanySource.is_own.is_(True), CompanySource.status != "dead"
            )
        )
    )
    listed = set(
        db.scalars(select(ProfileCompany.company_id).where(ProfileCompany.profile_id == profile_id))
    )
    found = []
    for company, count in rows:
        readable = company.id in own
        if company.key in hidden or (company.id in listed and readable):
            continue
        if looks_like_agency(db, company.name):
            continue
        found.append((company, count, readable))
    return found


def _probe(
    company: Company, titles: set[str], read_feed: ReadFeed
) -> tuple[str, str, list[FeedJob]] | None:
    """The company's own board, found by its name and checked against its known jobs."""
    for token in board_tokens(company.name):
        for system in HIRING_SYSTEMS:
            try:
                jobs = read_feed(system, token)
            except _READ_ERRORS:
                continue
            if not jobs:
                continue
            named = system == "greenhouse" and any(
                job.company and matching_key(job.company) == company.compact_key for job in jobs
            )
            if named or {title_key(job.title) for job in jobs} & titles:
                return system, token, jobs
    return None


def discover_from_catalog(
    db: Session,
    user_id: uuid.UUID,
    profile_id: uuid.UUID,
    queries: list[str],
    hidden: set[str],
    read_feed: ReadFeed,
    location: str = "",
) -> CatalogFinds:
    """Add the catalog's companies that match the brief to the profile's list. Commits."""
    finds = CatalogFinds()
    candidates = _candidates(db, profile_id, _terms(queries), hidden, location)
    now = utcnow()
    for company, _, readable in candidates:
        if len(finds.added) >= MAX_ADDED_WITH_BOARD:
            break
        if readable and db.get(ProfileCompany, (profile_id, company.id)) is None:
            add_to_profile(db, user_id, profile_id, company.id, ProfileCompanyOrigin.DISCOVERED)
            finds.added.append(company.name)
    db.commit()

    cutoff = now - PROBE_AGAIN_AFTER
    to_probe = [
        company
        for company, _, readable in candidates
        if not readable
        and (
            company.board_probed_at is None
            or company.board_probed_at.replace(tzinfo=None) < cutoff.replace(tzinfo=None)
        )
    ][:MAX_PROBED]
    if not to_probe:
        return finds
    titles = {
        company.id: set(
            db.scalars(select(CatalogJob.title_key).where(CatalogJob.company_id == company.id))
        )
        for company in to_probe
    }
    with ThreadPoolExecutor(max_workers=_PROBES_AT_ONCE) as pool:
        results = list(
            pool.map(lambda company: _probe(company, titles[company.id], read_feed), to_probe)
        )
    for company, result in zip(to_probe, results, strict=True):
        company.board_probed_at = now
        finds.probed += 1
        if result is None:
            continue
        system, token, jobs = result
        company_source(db, company, system, token, is_own=True)
        db.flush()
        source = db.scalar(
            select(CompanySource).where(
                CompanySource.source == system, CompanySource.identifier == token
            )
        )
        if source is None or source.company_id != company.id:
            # The board already belongs to another company in the catalog.
            continue
        finds.found.append(company.name)
        apply_read(db, source.id, jobs, utcnow())
        if db.get(ProfileCompany, (profile_id, company.id)) is None:
            add_to_profile(db, user_id, profile_id, company.id, ProfileCompanyOrigin.DISCOVERED)
            finds.added.append(company.name)
        logger.info("Found %s's board by its name: %s:%s", company.name, system, token)
    db.commit()
    return finds
