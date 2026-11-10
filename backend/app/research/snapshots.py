"""Company research snapshots, API side (docs/rejection_research_spec.md, chunk 2): the checks
every fact passes before it's stored, queueing a snapshot when a rejection or the drawer asks
for one, and what the drawer shows. No model calls here: the tailor worker runs each queued
snapshot (app.research.snapshot_worker) through the tailor service.

Facts about a company are shared by every user. A snapshot records who asked only for the cost
record and the daily limit, and never stores anything about their application.
"""

import logging
import re
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.app_config import daily_limit, get_value
from app.companies.catalog import hiring_ref
from app.companies.identity import find_company, resolve_company
from app.models import (
    TAILOR_ACTIVE,
    Application,
    ApplicationStatus,
    Company,
    CompanyKind,
    CompanyResearch,
    RejectAnalysis,
    TailorStatus,
)
from app.postings.parser import canonical_technology
from app.research.analysis import midnight_utc, queue_analysis
from app.schemas import CompanyResearchOut, CompanyResearchView

logger = logging.getLogger(__name__)

MAX_SOURCES = 15
MAX_TEXT = 300
MAX_LIST = 10
MAX_HEADCOUNT = 5_000_000
MAX_USD = 10**13
MAX_YEARS = 60
EARLIEST_FOUNDED = 1600

SECTIONS = (
    "profile",
    "ownership",
    "funding",
    "size",
    "events",
    "engineering",
    "workforce",
    "interviewing",
)
CONFIDENCE = ("stated", "estimated", "unknown")
OWNERSHIP = (
    "public",
    "private_vc",
    "private_pe",
    "private_bootstrapped",
    "subsidiary",
    "nonprofit",
    "government",
    "unknown",
)
FUNDING_STAGES = (
    "pre_seed",
    "seed",
    "series_a",
    "series_b",
    "series_c",
    "series_d_or_later",
    "growth",
    "public",
    "not_funded",
    "unknown",
)
EVENT_KINDS = (
    "layoff",
    "hiring_freeze",
    "reorg",
    "acquisition",
    "acquired",
    "ipo",
    "leadership_change",
    "office_opened",
    "office_closed",
)
TRENDS = ("growing", "flat", "shrinking", "unknown")
EVIDENCE = ("blog", "github", "other_postings", "talk")
SENIORITY = ("mostly_junior", "mixed", "mostly_senior", "unknown")
EMPLOYER_KINDS = ("big_tech", "startup", "consulting", "agency", "enterprise")
REMOTE = ("remote", "hybrid", "onsite", "mixed", "unknown")
TAKE_HOME = ("yes", "no", "unknown")

# --- Personal data -------------------------------------------------------------------------

# Sites that exist to look people up. Every page on them is about a person.
PEOPLE_SEARCH_HOSTS = frozenset(
    {
        "spokeo.com",
        "whitepages.com",
        "beenverified.com",
        "truepeoplesearch.com",
        "fastpeoplesearch.com",
        "peoplefinders.com",
        "radaris.com",
        "intelius.com",
        "instantcheckmate.com",
        "truthfinder.com",
        "mylife.com",
        "peekyou.com",
        "pipl.com",
        "thatsthem.com",
        "zabasearch.com",
        "usphonebook.com",
        "contactout.com",
        "lusha.com",
        "rocketreach.co",
        "signalhire.com",
    }
)
# Social sites whose pages are mostly people's own, so none is used as a source.
SOCIAL_HOSTS = frozenset(
    {"facebook.com", "instagram.com", "tiktok.com", "x.com", "twitter.com", "threads.net"}
)
# Paths that are one person's profile on sites that also have company pages.
PROFILE_PATHS: dict[str, tuple[str, ...]] = {
    "linkedin.com": ("/in/", "/pub/", "/profile/"),
    "crunchbase.com": ("/person/",),
    "zoominfo.com": ("/p/",),
    "apollo.io": ("/people/",),
    "theorg.com": ("/org-chart/", "/people/"),
    "wellfound.com": ("/u/",),
    "angel.co": ("/u/",),
}
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# Ten to fifteen digits with the usual separators: a phone number, not a year or an amount.
_PHONE = re.compile(r"(?<![\w])\+?\d(?:[\s().-]*\d){9,14}(?![\w])")
_SPACES = re.compile(r"\s+")
_TICKER = re.compile(r"^[A-Z0-9.\-]{1,12}$")
_DATE = re.compile(r"^(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?$")


def _site(host: str, names: frozenset[str] | dict[str, Any]) -> str | None:
    """The listed site this host is, itself or a subdomain of it."""
    return next((name for name in names if host == name or host.endswith(f".{name}")), None)


def is_person_source(url: str) -> bool:
    """A page about a person: a profile, a people-search site, or a social site."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if _site(host, PEOPLE_SEARCH_HOSTS) or _site(host, SOCIAL_HOSTS):
        return True
    site = _site(host, PROFILE_PATHS)
    path = parts.path.lower()
    return site is not None and any(part in path for part in PROFILE_PATHS[site])


def has_contact_details(text: str) -> bool:
    return bool(_EMAIL.search(text) or _PHONE.search(text))


# --- Checking a snapshot ----------------------------------------------------------------------


@dataclass
class CleanResearch:
    """A snapshot's facts after the checks, ready to store, and what was dropped by reason."""

    sources: list[dict[str, str]]
    sections: dict[str, Any]
    confidence: dict[str, str]
    dropped: dict[str, int]


class _Checker:
    """Checks one snapshot, counting what it drops. A fact that fails is dropped, never the
    snapshot."""

    def __init__(self, raw: dict[str, Any], today: date) -> None:
        self.raw = raw
        self.today = today
        self.dropped: Counter[str] = Counter()
        # The model's source index -> the kept source's index, or a reason it was dropped.
        self.source_map: dict[int, int | str] = {}

    def drop(self, reason: str) -> None:
        self.dropped[reason] += 1

    def text(self, value: Any) -> str:
        if not isinstance(value, str):
            return ""
        cleaned = _SPACES.sub(" ", value).strip()
        if cleaned and has_contact_details(cleaned):
            self.drop("contact_details")
            return ""
        if len(cleaned) > MAX_TEXT:
            cleaned = cleaned[: MAX_TEXT - 1].rstrip() + "…"
        return cleaned

    def texts(self, values: Any) -> list[str]:
        if not isinstance(values, list):
            return []
        kept = [text for value in values if (text := self.text(value))]
        return list(dict.fromkeys(kept))[:MAX_LIST]

    def number(self, value: Any, low: int, high: int) -> int:
        """A whole number in range, else 0 (unknown). Floats from the model are rounded."""
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return 0
        number = round(value)
        if number == 0:
            return 0
        if not low <= number <= high:
            self.drop("out_of_range")
            return 0
        return number

    def choice(self, value: Any, allowed: tuple[str, ...], default: str = "unknown") -> str:
        return value if isinstance(value, str) and value in allowed else default

    def when(self, value: Any) -> str:
        """YYYY-MM-DD, YYYY-MM or YYYY, not in the future. Empty when unknown or invalid."""
        if not isinstance(value, str) or not value.strip():
            return ""
        found = _DATE.match(value.strip())
        if found is None:
            self.drop("bad_date")
            return ""
        year, month, day = int(found[1]), int(found[2] or 1), int(found[3] or 1)
        try:
            start = date(year, month, day)
        except ValueError:
            self.drop("bad_date")
            return ""
        if start > self.today or year < EARLIEST_FOUNDED:
            self.drop("bad_date")
            return ""
        return found[0]

    def sources(self) -> list[dict[str, str]]:
        raw = self.raw.get("sources")
        kept: list[dict[str, str]] = []
        for index, item in enumerate(raw if isinstance(raw, list) else []):
            url = item.get("url") if isinstance(item, dict) else None
            if not isinstance(url, str) or not url.lower().startswith(("http://", "https://")):
                self.source_map[index] = "bad_source"
                self.drop("bad_source")
                continue
            if len(url) > 2_048 or not urlsplit(url).hostname:
                self.source_map[index] = "bad_source"
                self.drop("bad_source")
                continue
            if is_person_source(url):
                self.source_map[index] = "person_source"
                self.drop("person_source")
                continue
            if len(kept) >= MAX_SOURCES:
                self.source_map[index] = "too_many_sources"
                self.drop("too_many_sources")
                continue
            self.source_map[index] = len(kept)
            kept.append(
                {
                    "url": url,
                    "title": self.text(item.get("title")),
                    "published_on": self.when(item.get("published_on")),
                }
            )
        return kept

    def source(self, value: Any) -> int | None:
        """The kept source a fact cites, or None (counted) when it cites none that was kept."""
        usable = isinstance(value, int) and not isinstance(value, bool)
        mapped = self.source_map.get(value) if usable else None
        if isinstance(mapped, int):
            return mapped
        # A fact citing only a dropped person source is dropped for that reason.
        self.drop(f"fact_{mapped}" if isinstance(mapped, str) else "fact_unsourced")
        return None

    def section(self, name: str) -> dict[str, Any]:
        value = self.raw.get(name)
        return value if isinstance(value, dict) else {}


def _profile(check: _Checker) -> dict[str, Any]:
    raw = check.section("profile")
    if not raw or (source := check.source(raw.get("source"))) is None:
        return {}
    return {
        "what_it_does": check.text(raw.get("what_it_does")),
        "industry": check.text(raw.get("industry")),
        "hq_city": check.text(raw.get("hq_city")),
        "hq_country": check.text(raw.get("hq_country")),
        "founded_year": check.number(raw.get("founded_year"), EARLIEST_FOUNDED, check.today.year),
        "website_domain": check.text(raw.get("website_domain")).lower()[:255],
        "source": source,
    }


def _ownership(check: _Checker) -> dict[str, Any]:
    raw = check.section("ownership")
    if not raw or (source := check.source(raw.get("source"))) is None:
        return {}
    ticker = check.text(raw.get("ticker")).upper()
    return {
        "kind": check.choice(raw.get("kind"), OWNERSHIP),
        "ticker": ticker if _TICKER.match(ticker) else "",
        "exchange": check.text(raw.get("exchange"))[:40],
        "parent": check.text(raw.get("parent")),
        "source": source,
    }


def _funding(check: _Checker) -> dict[str, Any]:
    raw = check.section("funding")
    if not raw:
        return {}
    rounds = []
    for item in raw.get("rounds") if isinstance(raw.get("rounds"), list) else []:
        if not isinstance(item, dict) or (source := check.source(item.get("source"))) is None:
            continue
        when = check.when(item.get("date"))
        if not when:
            check.drop("round_without_date")
            continue
        rounds.append(
            {
                "date": when,
                "round": check.text(item.get("round")),
                "amount_usd": check.number(item.get("amount_usd"), 1, MAX_USD),
                "source": source,
            }
        )
    funding: dict[str, Any] = {"rounds": sorted(rounds, key=lambda item: item["date"])}
    if raw.get("source") is not None and (source := check.source(raw.get("source"))) is not None:
        funding.update(
            stage=check.choice(raw.get("stage"), FUNDING_STAGES),
            total_raised_usd=check.number(raw.get("total_raised_usd"), 1, MAX_USD),
            source=source,
        )
    return funding if funding.get("source") is not None or rounds else {}


def _size(check: _Checker) -> dict[str, Any]:
    raw = check.section("size")
    if not raw or (source := check.source(raw.get("source"))) is None:
        return {}
    return {
        "headcount": check.number(raw.get("headcount"), 1, MAX_HEADCOUNT),
        "as_of": check.when(raw.get("as_of")),
        "trend": check.choice(raw.get("trend"), TRENDS),
        "source": source,
    }


def _events(check: _Checker) -> list[dict[str, Any]]:
    raw = check.raw.get("events")
    events = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict) or item.get("kind") not in EVENT_KINDS:
            check.drop("bad_event")
            continue
        if (source := check.source(item.get("source"))) is None:
            continue
        when = check.when(item.get("date"))
        if not when:
            check.drop("event_without_date")
            continue
        events.append(
            {
                "date": when,
                "kind": item["kind"],
                "detail": check.text(item.get("detail")),
                "count": check.number(item.get("count"), 1, MAX_HEADCOUNT),
                "percent": check.number(item.get("percent"), 1, 100),
                "source": source,
            }
        )
    return sorted(events, key=lambda item: item["date"], reverse=True)


def _engineering(check: _Checker) -> dict[str, Any]:
    raw = check.section("engineering")
    if not raw:
        return {}
    stack: dict[str, dict[str, Any]] = {}
    for item in raw.get("stack") if isinstance(raw.get("stack"), list) else []:
        if not isinstance(item, dict) or (source := check.source(item.get("source"))) is None:
            continue
        name = check.text(item.get("name"))
        if not name:
            continue
        # The posting parser's name, so it lines up with checklist names.
        name = canonical_technology(name)
        stack.setdefault(
            name.lower(),
            {
                "name": name,
                "evidence": check.choice(item.get("evidence"), EVIDENCE, "other_postings"),
                "source": source,
            },
        )
    engineering: dict[str, Any] = {"stack": sorted(stack.values(), key=lambda item: item["name"])}
    if raw.get("source") is not None and (source := check.source(raw.get("source"))) is not None:
        engineering.update(
            eng_blog=raw.get("eng_blog") is True,
            open_source=raw.get("open_source") is True,
            team_size=check.number(raw.get("team_size"), 1, MAX_HEADCOUNT),
            source=source,
        )
    return engineering if engineering.get("source") is not None or stack else {}


def _workforce(check: _Checker) -> dict[str, Any]:
    raw = check.section("workforce")
    if not raw or (source := check.source(raw.get("source"))) is None:
        return {}
    low = check.number(raw.get("typical_years_low"), 1, MAX_YEARS)
    high = check.number(raw.get("typical_years_high"), 1, MAX_YEARS)
    if low and high and low > high:
        low, high = high, low
    kinds = raw.get("prior_employer_kinds")
    return {
        "typical_years_low": low,
        "typical_years_high": high,
        "seniority_mix": check.choice(raw.get("seniority_mix"), SENIORITY),
        "prior_industries": check.texts(raw.get("prior_industries")),
        "prior_employer_kinds": [
            kind for kind in EMPLOYER_KINDS if isinstance(kinds, list) and kind in kinds
        ],
        "cs_degree_percent": check.number(raw.get("cs_degree_percent"), 1, 100),
        "team_locations": check.texts(raw.get("team_locations")),
        "remote_policy": check.choice(raw.get("remote_policy"), REMOTE),
        "source": source,
    }


def _interviewing(check: _Checker) -> dict[str, Any]:
    raw = check.section("interviewing")
    if not raw or (source := check.source(raw.get("source"))) is None:
        return {}
    return {
        "stages": check.texts(raw.get("stages")),
        "take_home": check.choice(raw.get("take_home"), TAKE_HOME),
        "typical_weeks": check.number(raw.get("typical_weeks"), 1, 52),
        "weighs": check.texts(raw.get("weighs")),
        "source": source,
    }


_CLEANERS: dict[str, Callable[[_Checker], Any]] = {
    "profile": _profile,
    "ownership": _ownership,
    "funding": _funding,
    "size": _size,
    "events": _events,
    "engineering": _engineering,
    "workforce": _workforce,
    "interviewing": _interviewing,
}


def clean_research(raw: dict[str, Any], today: date) -> CleanResearch:
    """Every check the spec asks for before a snapshot is stored: personal data, sources,
    dates, ranges, lengths and enumerations. Sources no kept fact cites are kept too, since
    the model read them."""
    check = _Checker(raw if isinstance(raw, dict) else {}, today)
    sources = check.sources()
    sections = {name: cleaner(check) for name, cleaner in _CLEANERS.items()}
    stated = check.section("confidence")
    confidence = {
        name: check.choice(stated.get(name), CONFIDENCE) if sections[name] else "unknown"
        for name in SECTIONS
    }
    return CleanResearch(sources, sections, confidence, dict(check.dropped))


# --- Queueing ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Queued:
    """What asking for a snapshot did: the row queued or already waiting, or why none was.
    code is agency, no_name, recent or over_limit when none was."""

    research: CompanyResearch | None
    company: Company | None
    reason: str | None = None
    code: str | None = None


AGENCY = "This company is a staffing agency, so the employer behind the posting is unknown."
NO_NAME = "This application's company name can't be looked up."
RUNNING = "Research is already running for this company."


def over_limit(limit: int) -> str:
    return (
        f"You've started {limit} company research snapshots today. More can start after "
        "midnight UTC."
    )


def recently(until: datetime) -> str:
    return f"Researched recently; it can be researched again from {until:%Y-%m-%d}."


def newest_snapshot(db: Session, company_id: uuid.UUID) -> CompanyResearch | None:
    return db.scalar(
        select(CompanyResearch)
        .where(
            CompanyResearch.company_id == company_id,
            CompanyResearch.status == TailorStatus.DONE.value,
        )
        .order_by(CompanyResearch.finished_at.desc(), CompanyResearch.id)
        .limit(1)
    )


def active_research(db: Session, company_id: uuid.UUID) -> CompanyResearch | None:
    return db.scalar(
        select(CompanyResearch)
        .where(CompanyResearch.company_id == company_id, CompanyResearch.status.in_(TAILOR_ACTIVE))
        .limit(1)
    )


def started_today(db: Session, user_id: uuid.UUID, now: datetime) -> int:
    """Snapshots this user's rejections and clicks started since midnight UTC."""
    query = select(func.count(CompanyResearch.id)).where(
        CompanyResearch.requested_by == user_id, CompanyResearch.created_at >= midnight_utc(now)
    )
    return db.scalar(query) or 0


def _aware(moment: datetime | None) -> datetime | None:
    if moment is None:
        return None
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment


def fresh_until(db: Session, snapshot: CompanyResearch | None) -> datetime | None:
    """When a company's newest snapshot stops being reused, or None with no snapshot."""
    finished = _aware(snapshot.finished_at) if snapshot is not None else None
    if finished is None:
        return None
    return finished + timedelta(days=get_value(db, "company_research_max_age_days"))


def hiring_source(url: str | None) -> tuple[str, str] | None:
    """The (system, token) of a Greenhouse, Lever or Ashby link, which identifies a company."""
    ref = hiring_ref(url)
    return (ref.system, ref.token) if ref is not None and ref.token else None


def queue_research(
    db: Session, user_id: uuid.UUID, company_name: str, url: str | None, now: datetime
) -> Queued:
    """Queue a snapshot of the company an application names, unless it has a fresh one, one is
    already waiting, it's an agency, or the user is over today's limit. Finds or adds the
    company in the catalog. Changes the session only."""
    try:
        company = resolve_company(db, company_name, source=hiring_source(url)).company
    except ValueError:
        return Queued(None, None, NO_NAME, "no_name")
    if company.kind == CompanyKind.AGENCY.value:
        return Queued(None, company, AGENCY, "agency")
    until = fresh_until(db, newest_snapshot(db, company.id))
    if until is not None and until > now:
        return Queued(None, company, recently(until), "recent")
    active = active_research(db, company.id)
    if active is not None:
        return Queued(active, company)
    limit = daily_limit(db, user_id, "company_research_daily_limit")
    if limit is not None and started_today(db, user_id, now) >= limit:
        return Queued(None, company, over_limit(limit), "over_limit")
    research = CompanyResearch(
        company_id=company.id,
        requested_by=user_id,
        status=TailorStatus.QUEUED.value,
        sources=[],
        profile={},
        ownership={},
        funding={},
        size={},
        events=[],
        engineering={},
        workforce={},
        interviewing={},
        confidence={},
        dropped={},
        usage={},
        cost_usd=0.0,
        created_at=now,
    )
    db.add(research)
    logger.info("Queued company research for %s", company.name)
    return Queued(research, company)


def research_at_rejection(db: Session, application: Application, now: datetime) -> None:
    """Start a snapshot of a rejected application's company, when the rules allow one. A
    failure here is logged and never fails the status change."""
    url = application.job_url or (application.posting.url if application.posting else None)
    try:
        with db.begin_nested():
            queued = queue_research(db, application.user_id, application.company, url, now)
    except SQLAlchemyError:
        logger.exception("Couldn't queue company research for application %s", application.id)
        return
    if queued.research is None:
        logger.info("No company research for application %s: %s", application.id, queued.reason)


def start_report(db: Session, application: Application, now: datetime) -> RejectAnalysis | None:
    """The one way a rejection report is asked for, whether by the rejection itself, Analyze or
    Analyze them: research the company when the rules allow, then queue the report. The worker
    runs queued research before any queued report, so the report is written with it. None when
    the application isn't rejected or already has a report. Changes the session only."""
    if application.status != ApplicationStatus.REJECTED.value:
        return None
    existing = db.scalar(
        select(RejectAnalysis.id).where(RejectAnalysis.application_id == application.id)
    )
    if existing is not None:
        return None
    research_at_rejection(db, application, now)
    return queue_analysis(db, application)


# --- What the drawer shows ------------------------------------------------------------------


def to_out(research: CompanyResearch) -> CompanyResearchOut:
    return CompanyResearchOut(
        id=research.id,
        status=research.status,
        note=research.note,
        sources=research.sources or [],
        profile=research.profile or {},
        ownership=research.ownership or {},
        funding=research.funding or {},
        size=research.size or {},
        events=research.events or [],
        engineering=research.engineering or {},
        workforce=research.workforce or {},
        interviewing=research.interviewing or {},
        confidence=research.confidence or {},
        searches=research.searches,
        created_at=research.created_at,
        finished_at=research.finished_at,
    )


def research_view(db: Session, application: Application, now: datetime) -> CompanyResearchView:
    """An application's company: its newest snapshot, one waiting or running, the newest
    failure since, and whether another can start now. Never adds a company."""
    url = application.job_url or (application.posting.url if application.posting else None)
    company = find_company(db, application.company, hiring_source(url))
    used = started_today(db, application.user_id, now)
    limit = daily_limit(db, application.user_id, "company_research_daily_limit")
    base = {"used_today": used, "daily_limit": limit}
    if company is None:
        blocked = None if limit is None or used < limit else over_limit(limit)
        return CompanyResearchView(company=None, **base, blocked=blocked)
    if company.kind == CompanyKind.AGENCY.value:
        return CompanyResearchView(
            company=company.name, company_id=company.id, agency=True, **base, blocked=AGENCY
        )
    latest = newest_snapshot(db, company.id)
    active = active_research(db, company.id)
    newest = db.scalar(
        select(CompanyResearch)
        .where(CompanyResearch.company_id == company.id)
        .order_by(CompanyResearch.created_at.desc(), CompanyResearch.id)
        .limit(1)
    )
    failed = newest if newest is not None and newest.status == TailorStatus.FAILED.value else None
    until = fresh_until(db, latest)
    blocked = None
    if active is not None:
        blocked = RUNNING
    elif until is not None and until > now:
        blocked = recently(until)
    elif limit is not None and used >= limit:
        blocked = over_limit(limit)
    return CompanyResearchView(
        company=company.name,
        company_id=company.id,
        latest=to_out(latest) if latest else None,
        active=active.status if active else None,
        failure=failed.note if failed else None,
        **base,
        blocked=blocked,
    )
