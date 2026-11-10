"""Scout's one engine: open jobs from the shared catalog, a strict code gate, then Haiku
screens and Sonnet verifies. docs/scout_spec.md.

A run, in order:

1. Harvest: Callback's job boards are searched for the brief's stack (free), and what they
   return is recorded in the catalog.
2. Companies in the catalog that match the brief join the profile's list, and their boards are
   found by name where they can be (free).
3. Scan: every fresh, open job in the whole catalog that could match, not only the profile's
   list, goes through the code gate (app.scout.match and app.scout.filters).
4. Discover: only when the scan left too few, Haiku searches the web for more companies, and the
   scan runs again.
5. Refresh: the boards behind the first candidates are read again when stale, so closed jobs
   drop out before anything is paid to judge them.
6. Screen and verify: Haiku judges one posting per call; each one it keeps gets a second look
   from Sonnet. A lead is kept only when both keep it. Briefs are written when asked for.

Every verdict is remembered per profile, and what the judge read from a posting alone (its level,
whether the US can hire) is stored on the shared catalog job for every later run.
"""

import logging
import re
import time
import uuid
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import httpx
from sqlalchemy import or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from app.companies.catalog import catalog_url
from app.companies.catalog_discovery import discover_from_catalog
from app.companies.catalog_worker import (
    MAX_FAILURES,
    READ_EVERY,
    apply_read,
    request_reads,
    wait_for_reads,
)
from app.companies.discovery import ReadFeed, describe, discover
from app.companies.feeds import FeedGone, FeedJob
from app.companies.identity import check_pending_pairs
from app.listings.query import parse_query
from app.models import (
    CatalogJob,
    Company,
    CompanyKind,
    CompanySource,
    PostingLevel,
    ProfileCompany,
    utcnow,
)
from app.postings.parser import company_key
from app.schemas import ScoutServiceResult
from app.scout.filters import (
    _TITLE_FAMILY_QUERIES,
    _abroad,
    _known,
    _Tally,
    _too_old,
    _wrong_mode,
    board_location,
    board_queries,
)
from app.scout.judgment_memory import (
    Memory,
    brief_key,
    job_level,
    load_memory,
    remember,
    store_facts,
    text_key,
)
from app.scout.match import (
    MIN_TEXT,
    LevelFit,
    MatchSpec,
    StackPlace,
    brief_lines,
    level_fit,
    level_from_value,
    ruled_out,
    spec_from_payload,
    stack_passes,
    stack_place,
)
from app.scout.metros import in_major_metro
from app.workers import TailorUnavailable

logger = logging.getLogger(__name__)

Stage = Callable[[dict[str, Any]], dict[str, Any]]

# How long a run waits for listed companies' boards that have never been read.
WAIT_FOR_READS_SECONDS = 60.0
# A job not seen on any board or feed for this long is taken as gone. Board-only jobs are never
# closed by a company's own feed, so this is their only way out.
FRESH_DAYS = 30
# Fewer candidates than this many times the lead target, and the run looks on the web too.
SHORT_FACTOR = 3
# The boards behind this many of the first candidates are read again before judging, from at
# most this many companies.
REFRESH_JOBS = 120
REFRESH_COMPANIES = 40
_READS_AT_ONCE = 8
_READ_ERRORS = (FeedGone, httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError)
# Costs guessed for a model call before one has been made, in US dollars.
_FIRST_SCREEN_USD = 0.01
_FIRST_VERIFY_USD = 0.03
_MAX_FAILURES_IN_A_ROW = 3
_MAX_POSTING_TEXT = 12_000
# A company whose leads for this profile were all turned down, at least this many, is skipped.
_DISMISSED_TO_SKIP = 3
_ON_SITE = ("onsite", "hybrid")
# Plain engineering titles, kept for the judge when a brief has no stack rule and no title
# families, since a "Senior Software Engineer" posting rarely puts the work in its title.
_ENGINEERING_TITLE = re.compile(
    r"\b(?:developer|programmer|swe"
    r"|(?:software|back[- ]?end|front[- ]?end|full[- ]?stack|web|application|platform|php|"
    r"product|integrations?|tools?|systems?)\s+engineer(?:ing)?)\b",
    re.I,
)
# Title words a SQL prefilter looks for when a brief has no stack rule.
_ENGINEERING_WORDS = ("engineer", "developer", "programmer")
# Where the stack is named, as rank points: the title beats a requirements line beats a mention.
_STACK_POINTS = {StackPlace.TITLE: 12, StackPlace.REQUIRED: 8, StackPlace.MENTIONED: 4}

_STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA",
    "colorado": "CO", "connecticut": "CT", "delaware": "DE", "district of columbia": "DC",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID", "illinois": "IL",
    "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA",
    "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT",
    "virginia": "VA", "washington": "WA", "west virginia": "WV", "wisconsin": "WI",
    "wyoming": "WY",
}  # fmt: skip
# States that share a border, for "in or next to a state the Location names".
_NEIGHBORS = {
    "AL": "FL GA MS TN", "AK": "", "AZ": "CA CO NM NV UT", "AR": "LA MO MS OK TN TX",
    "CA": "AZ NV OR", "CO": "AZ KS NE NM OK UT WY", "CT": "MA NY RI", "DC": "MD VA",
    "DE": "MD NJ PA", "FL": "AL GA", "GA": "AL FL NC SC TN", "HI": "", "ID": "MT NV OR UT WA WY",
    "IL": "IN IA KY MO WI", "IN": "IL KY MI OH", "IA": "IL MN MO NE SD WI",
    "KS": "CO MO NE OK", "KY": "IL IN MO OH TN VA WV", "LA": "AR MS TX",
    "ME": "NH", "MD": "DC DE PA VA WV", "MA": "CT NH NY RI VT", "MI": "IN OH WI",
    "MN": "IA ND SD WI", "MS": "AL AR LA TN", "MO": "AR IL IA KS KY NE OK TN",
    "MT": "ID ND SD WY", "NE": "CO IA KS MO SD WY", "NV": "AZ CA ID OR UT", "NH": "MA ME VT",
    "NJ": "DE NY PA", "NM": "AZ CO OK TX UT", "NY": "CT MA NJ PA VT", "NC": "GA SC TN VA",
    "ND": "MN MT SD", "OH": "IN KY MI PA WV", "OK": "AR CO KS MO NM TX", "OR": "CA ID NV WA",
    "PA": "DE MD NJ NY OH WV", "RI": "CT MA", "SC": "GA NC", "SD": "IA MN MT ND NE WY",
    "TN": "AL AR GA KY MO MS NC VA", "TX": "AR LA NM OK", "UT": "AZ CO ID NM NV WY",
    "VT": "MA NH NY", "VA": "DC KY MD NC TN WV", "WA": "ID OR", "WV": "KY MD OH PA VA",
    "WI": "IA IL MI MN", "WY": "CO ID MT NE SD UT",
}  # fmt: skip
_CODES = set(_NEIGHBORS)
# Regions a Location may name instead of a state.
_EASTERN = "CT DE DC FL GA IN KY ME MD MA MI NH NJ NY NC OH PA RI SC TN VT VA WV"
_REGIONS = (
    (re.compile(r"\b(?:east coast|eastern|e[sd]?t)\b", re.I), set(_EASTERN.split())),
    (re.compile(r"\b(?:west coast|pacific|p[sd]?t)\b", re.I), {"CA", "OR", "WA", "NV"}),
)
_STATE_NAME = re.compile(r"\b(" + "|".join(sorted(_STATES, key=len, reverse=True)) + r")\b", re.I)
_STATE_CODE = re.compile(r"(?:^|[\s,(])([A-Z]{2})(?![\w.])")

# Why the code gate left jobs out, in the order the run's notes name them.
_LEFT_OUT = (
    ("known", "already known or turned down"),
    ("company", "at companies you've hidden or turned down"),
    ("agency", "posted by agencies"),
    ("snippet", "with too little text to judge"),
    ("stack", "not using {stack}"),
    ("nice_only", "naming {stack} only as a nice-to-have"),
    ("rule_out", "requiring something you ruled out"),
    ("title", "not matching the work"),
    ("level", "at a level you don't want"),
    ("old", "posted too long ago"),
    ("mode", "in a work mode not wanted"),
    ("abroad", "only outside the US"),
    ("not_us", "not open to people in the US"),
    ("far", "onsite or hybrid too far away"),
)


@dataclass(frozen=True)
class CompanyStages:
    # tailor's /pipeline/judge as a screen: one posting, one small Haiku call.
    judge: Stage
    # tailor's /companies/same_company: unclear company names, up to 20 a call.
    same_company: Stage
    # tailor's /companies/discover and /companies/careers, and a company board reader, for
    # finding new companies. Without all three, a run reads only what the catalog has.
    discover: Stage | None = None
    careers: Stage | None = None
    read_feed: ReadFeed | None = None
    # tailor's /pipeline/judge as a verifier: Sonnet's second look at each posting the screen
    # keeps. Without it, the screen's verdict is final.
    verify: Stage | None = None
    # Callback's job boards, searched by code: {"queries", "location"} gives {"pages",
    # "failed"}, with everything found already recorded in the catalog.
    boards: Stage | None = None


def states_named(location: str | None) -> set[str]:
    """US states a place names, by name or two-letter code: "Philadelphia, PA" is {"PA"}."""
    text = location or ""
    named = {_STATES[match.lower()] for match in _STATE_NAME.findall(text)}
    named |= {code for code in _STATE_CODE.findall(text) if code in _CODES}
    for pattern, states in _REGIONS:
        if pattern.search(text):
            named |= states
    return named


def _would_move(relocation: str, location: str | None) -> bool:
    """Whether the person would relocate for an onsite or hybrid job placed here."""
    if relocation == "anywhere":
        return True
    return relocation == "metro" and in_major_metro(location)


def _near(location: str | None, wanted: set[str]) -> bool:
    """A job in or next to one of the wanted states. A job naming no state isn't ruled out."""
    states = states_named(location)
    if not states:
        return True
    reach = set(wanted)
    for state in wanted:
        reach.update(_NEIGHBORS.get(state, "").split())
    return bool(states & reach)


@dataclass
class _Candidate:
    job: CatalogJob
    company: Company
    # The profile's row for the company, or None for a company found in the wider catalog.
    listed: ProfileCompany | None
    strength: int
    level: PostingLevel | None

    @property
    def past_leads(self) -> int:
        return (self.listed.leads or 0) if self.listed is not None else 0


def _spread(candidates: list[_Candidate]) -> list[_Candidate]:
    """Best first, with companies taking turns within each level of match, so one company with
    many openings can't fill a run. Within a company, newest first, then companies that gave
    leads before go first in each turn."""
    ordered = sorted(
        candidates,
        key=lambda item: (
            -item.strength,
            -(item.job.posted_on.toordinal() if item.job.posted_on else 0),
            -item.past_leads,
        ),
    )
    spread: list[_Candidate] = []
    for strength in sorted({item.strength for item in ordered}, reverse=True):
        by_company: dict[uuid.UUID, list[_Candidate]] = {}
        for item in ordered:
            if item.strength == strength:
                by_company.setdefault(item.company.id, []).append(item)
        queues = sorted(by_company.values(), key=lambda queue: -queue[0].past_leads)
        for turn in range(max(len(queue) for queue in queues)):
            spread.extend(queue[turn] for queue in queues if turn < len(queue))
    return spread


def _page(job: CatalogJob, company: Company) -> dict[str, Any]:
    """A catalog job as a posting the judge reads, from its main link."""
    primary = next((source for source in job.sources if source.is_primary), job.sources[0])
    return {
        "url": primary.url,
        "ok": True,
        "source": primary.source,
        "title": job.title,
        "company": company.name,
        "location": job.location or "",
        "work_mode": job.work_mode or "",
        "employment_type": "",
        "salary": "",
        "posted_at": job.posted_on.isoformat() if job.posted_on else "",
        "text": job.text[:_MAX_POSTING_TEXT],
        # Decided in code from a fixed list, so the judge doesn't guess which cities count.
        "major_metro": in_major_metro(job.location),
    }


def _lead(page: dict[str, Any], job: CatalogJob, judgment: dict[str, Any]) -> dict[str, Any]:
    """A kept posting as a lead. The judgment stands in for the brief, which is written later."""
    others = [source.url for source in job.sources if source.url != page["url"]]
    level = level_from_value(judgment.get("posting_level"))
    return {
        "company": page["company"],
        "title": page["title"],
        "url": page["url"],
        "location": page["location"],
        "work_mode": page["work_mode"],
        "posted": page["posted_at"],
        "level": level.value if level else "",
        "fit": judgment["fit"],
        "fit_summary": judgment.get("work_match") or "",
        "agency": judgment.get("agency") or "",
        "posting_text": page["text"],
        "sources": [page["url"], *others][:10],
        # Written when the user asks for it, not during the run.
        "brief_status": "none",
    }


def decide(
    spec: MatchSpec, judgment: dict[str, Any], learned_levels: frozenset[PostingLevel]
) -> tuple[bool, str]:
    """The final call on a judgment, with code holding the stack and level rules. The judge
    reads the posting; code, not the judge, decides whether its level is wanted."""
    reason = str(judgment.get("reason") or "")
    if not judgment.get("location_ok"):
        return False, reason or "Not open to where you are."
    if spec.strict and judgment.get("stack_ok") is False:
        return False, f"Its own work doesn't use {', '.join(spec.must_have)}. {reason}".strip()
    level = level_from_value(judgment.get("posting_level"))
    if level_fit(spec, level, learned_levels) is LevelFit.OUT:
        return False, f"A {level.value if level else 'different'}-level role. {reason}".strip()
    if not judgment.get("keep") or judgment.get("fit") == "none":
        return False, reason
    return True, reason


def run_companies(
    db: Session,
    user_id: uuid.UUID,
    profile_id: uuid.UUID | None,
    payload: dict[str, Any],
    stages: CompanyStages,
    today: date,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    wait_seconds: float = WAIT_FOR_READS_SECONDS,
    run_id: uuid.UUID | None = None,
) -> ScoutServiceResult:
    """Run Scout for one profile. When tailor can't be reached, TailorUnavailable carries
    what the run spent before that, so it's recorded even though nothing is charged."""
    tally = _Tally()
    try:
        return _Run(
            db, user_id, profile_id, payload, stages, today, clock, sleep, tally, run_id
        ).go(wait_seconds)
    except TailorUnavailable as exc:
        raise TailorUnavailable(str(exc), tally.spent_fields()) from exc


def _listed(
    db: Session, profile_id: uuid.UUID | None, hidden: set[str]
) -> tuple[dict[uuid.UUID, tuple[Company, ProfileCompany]], set[uuid.UUID], int, int]:
    """The profile's companies to read, the ones left out, how many were hidden and how many
    skipped for leads that were all turned down."""
    if profile_id is None:
        return {}, set(), 0, 0
    rows = db.execute(
        select(ProfileCompany, Company)
        .join(Company, Company.id == ProfileCompany.company_id)
        .where(ProfileCompany.profile_id == profile_id)
    ).all()
    companies: dict[uuid.UUID, tuple[Company, ProfileCompany]] = {}
    left_out: set[uuid.UUID] = set()
    hidden_count = dismissed_count = 0
    for listed, company in rows:
        if company.key in hidden:
            hidden_count += 1
            left_out.add(company.id)
        elif listed.dismissed >= _DISMISSED_TO_SKIP and listed.dismissed >= listed.leads:
            dismissed_count += 1
            left_out.add(company.id)
        else:
            companies[company.id] = (company, listed)
    return companies, left_out, hidden_count, dismissed_count


class _Run:
    """One run's state, so each step reads as one method."""

    def __init__(
        self,
        db: Session,
        user_id: uuid.UUID,
        profile_id: uuid.UUID | None,
        payload: dict[str, Any],
        stages: CompanyStages,
        today: date,
        clock: Callable[[], float],
        sleep: Callable[[float], None],
        tally: _Tally,
        run_id: uuid.UUID | None,
    ) -> None:
        self.db = db
        self.user_id = user_id
        self.profile_id = profile_id
        self.payload = payload
        self.stages = stages
        self.today = today
        self.clock = clock
        self.sleep = sleep
        self.tally = tally
        self.run_id = run_id
        self.started = clock()
        self.limits = payload["limits"]
        self.budget = float(payload["budget_usd"])
        self.target = int(self.limits["target_leads"])
        self.max_checks = int(self.limits["max_pages"])
        self.time_limit = int(self.limits["max_minutes"]) * 60
        self.spec = spec_from_payload(payload)
        # What the judge is told: the brief with the stack and levels code holds it to.
        self.brief = {**payload, **brief_lines(self.spec)}
        self.hidden = {company_key(name) for name in payload.get("hidden_companies") or []}
        self.location = payload.get("location") or ""
        self.us_only = board_location(self.location) == "us"
        self.wanted_states = states_named(self.location) if self.us_only else set()
        self.families = [
            parse_query(_TITLE_FAMILY_QUERIES[family])
            for family in payload.get("title_families") or []
            if family in _TITLE_FAMILY_QUERIES
        ]
        known_urls, known_pairs = _known(payload)
        self.known_urls = known_urls | {link for url in known_urls if (link := catalog_url(url))}
        self.known_pairs = known_pairs | {
            (company_key(job.get("company") or ""), (job.get("title") or "").strip().lower())
            for job in payload.get("declined") or []
        }
        self.leads: list[dict[str, Any]] = []
        self.checked = 0
        self.web_searches = 0
        self.memory: Memory | None = None
        self.counts: Counter[str] = Counter()

    # Steps

    def go(self, wait_seconds: float) -> ScoutServiceResult:
        self.tally.step("note", self._spec_note())
        self._harvest()
        self._catalog_companies()
        companies, left_out = self._list(wait_seconds)
        key = brief_key(self.payload)
        protected = {
            word
            for term in self.spec.stack_terms
            for word in re.findall(r"[a-z0-9]+", term.lower())
        }
        if self.profile_id is not None:
            self.memory = load_memory(self.db, self.profile_id, key, protected)
        candidates = self._scan(companies, left_out)
        if len(candidates) < SHORT_FACTOR * self.target:
            if self._discover(len(candidates)):
                companies, left_out = self._list(wait_seconds, quiet=True)
                candidates = self._scan(companies, left_out)
        else:
            self.tally.step(
                "note",
                f"Didn't search the web for more companies: the catalog gave {len(candidates)} "
                "jobs to check.",
            )
        self._scan_note(candidates)
        if not candidates:
            remembered = self._memory_notes()
            self._settle_pairs(set(companies))
            if remembered:
                return self._finish(
                    "finished",
                    "Every job in the catalog that matches this brief was checked before. Each "
                    "run adds to the catalog, from the job boards and from companies found on "
                    "the web.",
                )
            return self._finish(
                "finished",
                "Nothing open in the catalog matches this brief yet. Each run adds to it, from "
                "the job boards and from companies found on the web.",
            )
        candidates = self._refresh(candidates)
        stopped = self._judge_all(candidates, key)
        self._memory_notes()
        self._settle_pairs(set(companies) | {item.company.id for item in candidates})
        if stopped is not None:
            return self._finish(*stopped)
        if len(self.leads) >= self.target:
            return self._finish(
                "target_reached", f"Found {len(self.leads)} leads, the number you asked for."
            )
        return self._finish(
            "finished", f"Checked every job that matched the brief: {self.checked} postings."
        )

    def _spec_note(self) -> str:
        stack = (
            f"must use {' or '.join(self.spec.must_have)}" if self.spec.strict else "no stack rule"
        )
        ruled = f"; rules out {', '.join(self.spec.rule_out)}" if self.spec.rule_out else ""
        return f"Matching: {stack}{ruled}; levels: {self.spec.describe_levels()}."

    def _harvest(self) -> None:
        """Search the job boards for the brief's stack. What they return is in the catalog
        before the scan, so it's judged and remembered like any other catalog job."""
        queries = board_queries(self.payload)
        if self.stages.boards is None or not queries:
            return
        try:
            answer = self.stages.boards(
                {"queries": queries, "location": "us" if self.us_only else ""}
            )
        except (httpx.HTTPError, SQLAlchemyError, ValueError) as exc:
            self.db.rollback()
            logger.warning("Couldn't search the job boards for a Scout run: %s", exc)
            self.tally.step("error", "Couldn't search the job boards this time.")
            return
        pages = answer.get("pages") or []
        where = " open to the US" if self.us_only else ""
        self.tally.step(
            "note",
            f"Job boards: {len(pages)} listings{where} for {', '.join(queries)}, added to the "
            "catalog.",
        )
        failed = answer.get("failed") or []
        if failed:
            self.tally.step("error", f"Couldn't search {', '.join(failed)} this time.")

    def _catalog_companies(self) -> None:
        if self.stages.read_feed is None or self.profile_id is None:
            return
        terms = self.spec.stack_terms or board_queries(self.payload)
        finds = discover_from_catalog(
            self.db,
            self.user_id,
            self.profile_id,
            terms,
            self.hidden,
            self.stages.read_feed,
            self.location,
        )
        for note in finds.notes():
            self.tally.step("note", note)

    def _list(
        self, wait_seconds: float, quiet: bool = False
    ) -> tuple[dict[uuid.UUID, tuple[Company, ProfileCompany]], set[uuid.UUID]]:
        """The profile's list, with its never-read boards read first."""
        companies, left_out, hidden_count, dismissed_count = _listed(
            self.db, self.profile_id, self.hidden
        )
        if quiet:
            return companies, left_out
        skipped = [
            f"{hidden_count} hidden" if hidden_count else "",
            f"{dismissed_count} whose leads you turned down" if dismissed_count else "",
        ]
        reasons = "; ".join(item for item in skipped if item)
        self.tally.step(
            "note",
            f"{len(companies)} companies on your list"
            f"{f' ({reasons} left out)' if reasons else ''}; the rest of the catalog is "
            "searched too.",
        )
        unread = request_reads(self.db, companies, utcnow())
        if unread:
            self.tally.step("note", f"Waiting for {len(unread)} companies' job boards to be read.")
            left = wait_for_reads(self.db, unread, wait_seconds, self.clock, self.sleep)
            if left:
                self.tally.step(
                    "note", f"{left} job boards weren't read in time; the next run uses them."
                )
        return companies, left_out

    def _open_jobs(self, listed: set[uuid.UUID]) -> list[tuple[CatalogJob, Company]]:
        """Fresh, open catalog jobs that could match, from the whole catalog. SQL only narrows
        by words; the code gate decides."""
        fresh_after = utcnow() - timedelta(days=FRESH_DAYS)
        if self.spec.strict:
            words = self.spec.stack_terms
            matches = or_(
                *(CatalogJob.title.ilike(f"%{word}%") for word in words),
                *(CatalogJob.text.ilike(f"%{word}%") for word in words),
            )
        else:
            words = [
                phrase.strip('"')
                for family in self.payload.get("title_families") or []
                if (phrase := _TITLE_FAMILY_QUERIES.get(family))
            ] or list(_ENGINEERING_WORDS)
            matches = or_(
                CatalogJob.company_id.in_(list(listed)),
                *(CatalogJob.title.ilike(f"%{word}%") for word in words),
            )
        rows = self.db.execute(
            select(CatalogJob, Company)
            .join(Company, Company.id == CatalogJob.company_id)
            .where(
                CatalogJob.closed_at.is_(None),
                CatalogJob.last_seen >= fresh_after,
                matches,
            )
            .options(selectinload(CatalogJob.sources))
        ).all()
        return [(job, company) for job, company in rows]

    def _scan(
        self,
        companies: dict[uuid.UUID, tuple[Company, ProfileCompany]],
        left_out: set[uuid.UUID],
    ) -> list[_Candidate]:
        """Every job the code gate lets through, best first. Counts why the rest were left out."""
        self.counts = Counter()
        if self.memory is not None:
            self.memory.reset_counts()
        learned = self.memory.levels if self.memory is not None else frozenset()
        relocation = self.payload.get("relocation") or "none"
        wanted_modes = self.payload.get("work_modes") or []
        skip_agencies = self.payload.get("agency_mode") == "skip"
        candidates: list[_Candidate] = []
        jobs = self._open_jobs(set(companies))
        self.counts["scanned"] = len(jobs)
        for job, company in jobs:
            if not job.sources:
                continue
            reason = self._left_out(
                job, company, left_out, learned, relocation, wanted_modes, skip_agencies
            )
            if reason is not None:
                self.counts[reason] += 1
                continue
            if self.memory is not None and self.memory.skip(job):
                # Counted by the memory, and named in the run's notes.
                continue
            listed = companies.get(company.id)
            candidates.append(self._candidate(job, company, listed[1] if listed else None))
        return _spread(candidates)

    def _left_out(
        self,
        job: CatalogJob,
        company: Company,
        left_out: set[uuid.UUID],
        learned: frozenset[PostingLevel],
        relocation: str,
        wanted_modes: list[str],
        skip_agencies: bool,
    ) -> str | None:
        """Why the code gate leaves this job out, or None to keep it."""
        urls = {catalog_url(source.url) for source in job.sources}
        if urls & self.known_urls or (company.key, job.title.strip().lower()) in self.known_pairs:
            return "known"
        if company.id in left_out or company.key in self.hidden:
            return "company"
        if skip_agencies and company.kind == CompanyKind.AGENCY.value:
            return "agency"
        if len(job.text.strip()) < MIN_TEXT:
            return "snippet"
        if self.spec.strict:
            place = stack_place(self.spec, job.title, job.text)
            if not stack_passes(self.spec, place):
                return "nice_only" if place is StackPlace.NICE_ONLY else "stack"
        elif not self._title_fits(job.title):
            return "title"
        if ruled_out(self.spec, job.title, job.text):
            return "rule_out"
        if level_fit(self.spec, job_level(job), learned) is LevelFit.OUT:
            return "level"
        if job.posted_on and _too_old(
            job.posted_on.isoformat(), self.payload.get("posted_within_days"), self.today
        ):
            return "old"
        if _wrong_mode(job.work_mode or "", wanted_modes):
            return "mode"
        if _abroad(job.location or "", self.us_only):
            return "abroad"
        if self.us_only and job.us_hireable == "no" and _facts_current(job):
            return "not_us"
        if (
            self.wanted_states
            and job.work_mode in _ON_SITE
            and not _near(job.location, self.wanted_states)
            and not _would_move(relocation, job.location)
        ):
            return "far"
        return None

    def _title_fits(self, title: str) -> bool:
        """With no stack rule: a title family in the title, or with none picked, a plain
        engineering title the judge will decide on."""
        if self.families:
            return any(search.matches_title(title, "") for search in self.families)
        return bool(_ENGINEERING_TITLE.search(title))

    def _candidate(
        self, job: CatalogJob, company: Company, listed: ProfileCompany | None
    ) -> _Candidate:
        level = job_level(job)
        strength = 0
        if self.spec.strict:
            strength += _STACK_POINTS.get(stack_place(self.spec, job.title, job.text), 0)
        elif _ENGINEERING_TITLE.search(job.title):
            strength += 1
        if any(search.matches_title(job.title, "") for search in self.families):
            strength += 2 if self.spec.strict else 4
        if self.spec.levels and level_fit(self.spec, level) is LevelFit.EXACT:
            strength += 2
        if listed is not None:
            strength += 1
        return _Candidate(job, company, listed, strength, level)

    def _memory_notes(self) -> int:
        """Say what the profile's memory left out without asking the judge. How many notes."""
        notes = self.memory.notes() if self.memory is not None else []
        for note in notes:
            self.tally.step("note", note)
        return len(notes)

    def _scan_note(self, candidates: list[_Candidate]) -> None:
        stack = " or ".join(self.spec.must_have)
        left_out = [
            f"{self.counts[key]} {label.format(stack=stack)}"
            for key, label in _LEFT_OUT
            if self.counts[key]
        ]
        reasons = "; ".join(left_out)
        self.tally.step(
            "note",
            f"{self.counts['scanned']} open jobs in the catalog could match, "
            f"{len(candidates)} to check{f' ({reasons})' if reasons else ''}.",
        )

    def _discover(self, found: int) -> bool:
        """Search the web for more companies. True when it ran."""
        stages = self.stages
        if not (stages.discover and stages.careers and stages.read_feed and self.profile_id):
            return False
        if int(self.limits.get("max_searches") or 0) < 1:
            return False
        self.tally.step(
            "note",
            f"Only {found} jobs to check in the catalog, so searching the web for more companies.",
        )

        def counted(stage: str, answer: dict[str, Any]) -> None:
            self.tally.add(stage, answer)
            self.tally.steps.extend(answer.get("steps") or [])

        def failed(stage: str, error: TailorUnavailable) -> None:
            self.tally.add_failed(stage, error)
            self.tally.step("error", f"Couldn't look for new companies: {error}")

        outcome = discover(
            self.db,
            self.user_id,
            self.profile_id,
            self.brief,
            stages.discover,
            stages.careers,
            stages.read_feed,
            counted,
            failed,
        )
        self.web_searches = outcome.searches
        for note in describe(outcome):
            self.tally.step("note", note)
        return True

    def _refresh(self, candidates: list[_Candidate]) -> list[_Candidate]:
        """Read again the stale boards of the companies behind the first candidates, and drop
        the jobs they no longer list."""
        read_feed = self.stages.read_feed
        if read_feed is None:
            return candidates
        company_ids: list[uuid.UUID] = []
        for item in candidates[:REFRESH_JOBS]:
            if item.company.id not in company_ids:
                company_ids.append(item.company.id)
        company_ids = company_ids[:REFRESH_COMPANIES]
        stale_before = utcnow() - READ_EVERY
        sources = [
            source
            for source in self.db.scalars(
                select(CompanySource).where(
                    CompanySource.company_id.in_(company_ids),
                    CompanySource.is_own.is_(True),
                    CompanySource.status != "dead",
                    CompanySource.failures < MAX_FAILURES,
                )
            )
            if source.last_checked is None
            or source.last_checked.replace(tzinfo=None) < stale_before.replace(tzinfo=None)
        ]
        if not sources:
            return candidates

        def read(source: CompanySource) -> list[FeedJob] | None:
            try:
                return read_feed(source.source, source.identifier)
            except _READ_ERRORS as exc:
                logger.info("Couldn't read %s:%s: %s", source.source, source.identifier, exc)
                return None

        with ThreadPoolExecutor(max_workers=_READS_AT_ONCE) as pool:
            results = list(pool.map(read, sources))
        for source, jobs in zip(sources, results, strict=True):
            apply_read(self.db, source.id, jobs, utcnow())
        still_open = [item for item in candidates if item.job.closed_at is None]
        closed = len(candidates) - len(still_open)
        self.tally.step(
            "note",
            f"Read {len(sources)} companies' job boards again before judging"
            f"{f'; {closed} of their jobs had closed' if closed else ''}.",
        )
        return still_open

    def _judge_all(self, candidates: list[_Candidate], key: str) -> tuple[str, str] | None:
        """Screen, then verify, each candidate in turn until a limit stops the run."""
        failures = 0
        learned = self.memory.levels if self.memory is not None else frozenset()
        for index, candidate in enumerate(candidates):
            # What the last posting taught (its facts on the shared catalog job, the verdict) is
            # committed before the next model call. Held to the end, those catalog rows stayed
            # locked for the whole run, and a board search recording the same jobs waited
            # minutes, then deadlocked with this run recording its leads.
            self.db.commit()
            if len(self.leads) >= self.target:
                found = f"Found {len(self.leads)} leads, the number you asked for."
                return ("target_reached", found)
            if self.checked >= self.max_checks:
                return (
                    "max_pages",
                    f"Checked {self.checked} postings, the limit, with "
                    f"{len(candidates) - index} more left.",
                )
            if self.clock() - self.started >= self.time_limit:
                minutes = self.limits["max_minutes"]
                return ("time_limit", f"Reached the {minutes}-minute time limit.")
            if self.tally.spent + self._next_cost() > self.budget:
                return ("budget", f"Reached this run's spending ceiling (${self.budget:.2f}).")
            twin = (candidate.job.company_id, candidate.job.title_key)
            if self.memory is not None and twin in self.memory.twin_rejections:
                # The same job, posted elsewhere, was turned down earlier in this run.
                self.memory.counts["twin"] += 1
                continue
            name = f"{candidate.company.name}, {candidate.job.title}"
            page = _page(candidate.job, candidate.company)
            judgment = self._ask("judge", self.stages.judge, page, name)
            if judgment is None:
                failures += 1
                if failures >= _MAX_FAILURES_IN_A_ROW:
                    return ("model_error", "The judge failed several times in a row.")
                continue
            failures = 0
            self.checked += 1
            store_facts(candidate.job, judgment)
            keep, reason = decide(self.spec, judgment, learned)
            if keep and self.stages.verify is not None:
                verified = self._ask("verify", self.stages.verify, page, name)
                if verified is None:
                    # Unverified, so not kept; the screen's verdict isn't remembered either.
                    self.tally.step("skipped", f"{name}: couldn't get a second look this time.")
                    continue
                judgment = {**verified, "verified": True}
                store_facts(candidate.job, judgment)
                keep, reason = decide(self.spec, judgment, learned)
            judgment = {**judgment, "keep": keep}
            if self.memory is not None and self.profile_id is not None:
                remember(
                    self.db,
                    self.user_id,
                    self.profile_id,
                    self.run_id,
                    candidate.job,
                    key,
                    judgment,
                )
            if not keep:
                self.tally.step("skipped", f"{name}: {reason}")
                if self.memory is not None:
                    self.memory.note_rejection(candidate.job, bool(judgment.get("location_ok")))
                continue
            if judgment.get("agency") and self.payload.get("agency_mode") == "skip":
                self.tally.step("skipped", f"{name}: posted by an agency, {judgment['agency']}")
                continue
            self.leads.append(_lead(page, candidate.job, judgment))
            fit = f"a {judgment['fit']} fit. {judgment.get('work_match') or ''}".strip()
            self.tally.step("lead", f"{name}: {fit}")
        self.db.commit()
        return None

    def _next_cost(self) -> float:
        """What the next posting could cost: a screen, and a verify if it's kept."""
        cost = self.tally.last.get("judge", _FIRST_SCREEN_USD)
        if self.stages.verify is not None:
            cost += self.tally.last.get("verify", _FIRST_VERIFY_USD)
        return cost

    def _ask(
        self, stage: str, call: Stage, page: dict[str, Any], name: str
    ) -> dict[str, Any] | None:
        """One judge call, counted against the run. None when it failed."""
        try:
            answer = call({"brief": self.brief, "posting": page})
        except TailorUnavailable as exc:
            self.tally.add_failed(stage, exc)
            what = "judge" if stage == "judge" else "double-check"
            self.tally.step("error", f"Couldn't {what} {name}: {exc}")
            return None
        self.tally.add(stage, answer)
        return dict(answer["judgment"])

    def _settle_pairs(self, company_ids: set[uuid.UUID]) -> None:
        """Check queued company pairs that involve this run's companies, charged to this run."""
        try:
            spent = check_pending_pairs(self.db, self.stages.same_company, company_ids=company_ids)
        except TailorUnavailable as exc:
            self.tally.add_failed("same_company", exc)
            self.tally.step("error", f"Couldn't check similar company names: {exc}")
            self.db.rollback()
            return
        if spent is not None:
            self.tally.add("same_company", spent)
            self.tally.step(
                "note", "Checked whether some similar company names are the same company."
            )
        self.db.commit()

    def _finish(self, code: str, message: str) -> ScoutServiceResult:
        self.tally.step("stop", message)
        logger.info(
            "Scout run stopped (%s): %d leads, %d postings checked, $%.3f",
            code,
            len(self.leads),
            self.checked,
            self.tally.spent,
        )
        return ScoutServiceResult(
            leads=self.leads,
            stop_code=code,
            stop_message=message,
            searches=self.web_searches,
            pages=self.checked,
            requests=len(self.tally.request_costs),
            usage=self.tally.usage,
            cost_usd=round(self.tally.spent, 4),
            model="companies",
            steps=self.tally.steps,
            cost_breakdown={part: round(value, 4) for part, value in self.tally.costs.items()},
            request_costs=self.tally.request_costs,
        )


def _facts_current(job: CatalogJob) -> bool:
    """Whether the facts stored on the job were read from its current text."""
    return bool(job.facts_key) and job.facts_key == text_key(job)
