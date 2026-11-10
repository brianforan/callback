"""Searches the job boards at once, sharing cached results so no board gets polled too often.

Results are kept in memory and in a store (the database), so a restart doesn't start every search
cold. Results past their fresh window are still shown at once while newer ones are fetched in the
background, and the search says it's refreshing so the page can offer the update.
"""

import hashlib
import html
import logging
import re
import threading
import time
import uuid
from collections.abc import Iterable
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from functools import cached_property

import httpx

from app.companies.catalog import CatalogRecorder
from app.config import Settings
from app.listings.boards import (
    BOARDS,
    FETCH_ERRORS,
    REQUEST_TIMEOUT_SECONDS,
    US_NAMES,
    USER_AGENT,
    Board,
    BoardQuery,
    RawListing,
    describe_error,
    missing_settings,
)
from app.listings.cache import KEEP_RESULTS, ListingStore, StoredResults, cache_key
from app.listings.query import ListingQuery, parse_query
from app.models import utcnow
from app.postings.parser import (
    BUILTIN,
    ParsedPosting,
    PostingRequirements,
    Vocabulary,
    canonical_url,
    company_key,
    parse_posting,
    parse_requirements,
)
from app.resumes.matching import JobFacts

logger = logging.getLogger(__name__)

# At INFO, httpx logs each request's full URL, and some boards take their key in the URL.
logging.getLogger("httpx").setLevel(logging.WARNING)

MAX_TEXT = 50_000
MAX_NAME = 200
MAX_URL = 2048
# A failure is remembered briefly, so a board that is rate limiting isn't asked again right away.
FAILURE_CACHE_SECONDS = 300
MAX_CACHE_ENTRIES = 256
# Below nginx's 30s proxy timeout. A slower board keeps fetching and fills the cache.
SEARCH_DEADLINE_SECONDS = 25.0
_DEEP = "deep"
# Boards write the US several ways, and often name only a city and state. A remote job with no
# region, or a worldwide one, is open to someone in the US too.
_OPEN_TO_US = (
    re.compile(r"\b(?:USA?|U\.S\.(?:A\.)?|United States)(?![\w.])", re.I),
    re.compile(
        r",\s*(?:AL|AK|AZ|AR|CA|CO|CT|DC|DE|FL|GA|HI|IA|ID|IL|IN|KS|KY|LA|MA|MD|ME|MI|MN|MO|MS|MT"
        r"|NC|ND|NE|NH|NJ|NM|NV|NY|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VA|VT|WA|WI|WV|WY)\b"
    ),
    re.compile(r"^remote$|worldwide|anywhere|\bglobal\b", re.I),
)


@dataclass(frozen=True)
class Listing:
    id: str
    source: str
    company: str
    title: str
    url: str
    location: str | None
    description: str
    published_on: date | None
    # Given for a saved posting, with the user's corrections. A board listing is parsed the first
    # time it's needed instead, since most of a board's listings never match the search.
    given_parsed: ParsedPosting | None = None
    given_requirements: PostingRequirements | None = None
    # Parses with a user's own vocabulary, by its key. Listings are shared by every user, so each
    # vocabulary's parse is kept apart from the built-in one.
    _parsed_by: dict[str, ParsedPosting] = field(default_factory=dict, compare=False, repr=False)
    _requirements_by: dict[str, PostingRequirements] = field(
        default_factory=dict, compare=False, repr=False
    )

    @cached_property
    def parsed(self) -> ParsedPosting:
        if self.given_parsed is not None:
            return self.given_parsed
        return parse_posting(self.title, self.description, self.source, self.location, self.url)

    @cached_property
    def requirements(self) -> PostingRequirements:
        if self.given_requirements is not None:
            return self.given_requirements
        return parse_requirements(self.description)

    def parsed_with(self, vocabulary: Vocabulary) -> ParsedPosting:
        if self.given_parsed is not None or vocabulary.key == BUILTIN.key:
            return self.parsed
        if vocabulary.key not in self._parsed_by:
            self._parsed_by[vocabulary.key] = parse_posting(
                self.title, self.description, self.source, self.location, self.url, vocabulary
            )
        return self._parsed_by[vocabulary.key]

    def requirements_with(self, vocabulary: Vocabulary) -> PostingRequirements:
        if self.given_requirements is not None or vocabulary.key == BUILTIN.key:
            return self.requirements
        if vocabulary.key not in self._requirements_by:
            self._requirements_by[vocabulary.key] = parse_requirements(self.description, vocabulary)
        return self._requirements_by[vocabulary.key]


@dataclass(frozen=True)
class SourceStatus:
    id: str
    name: str
    site: str
    state: str
    listings: int = 0
    fetched_at: datetime | None = None
    message: str | None = None
    missing_settings: tuple[str, ...] = ()
    setup_url: str | None = None
    # Older results are shown while newer ones are fetched.
    refreshing: bool = False


@dataclass(frozen=True)
class SearchResults:
    listings: list[Listing]
    sources: list[SourceStatus]

    @property
    def refreshing(self) -> bool:
        return any(source.refreshing for source in self.sources)


@dataclass(frozen=True)
class ListingFilters:
    levels: frozenset[str] = frozenset()
    role_families: frozenset[str] = frozenset()
    work_modes: frozenset[str] = frozenset()
    employment_types: frozenset[str] = frozenset()
    sources: frozenset[str] = frozenset()
    stack_all: frozenset[str] = frozenset()
    stack_none: frozenset[str] = frozenset()
    salary_min: int | None = None
    has_salary: bool = False
    years_max: int | None = None
    posted_within_days: int | None = None


@dataclass(frozen=True)
class HiddenRule:
    id: uuid.UUID
    kind: str
    key: str
    company_key: str
    title: str | None


class HiddenRules:
    """Finds the rule hiding a listing. A company rule wins over a rule for one of its jobs."""

    def __init__(self, rules: list[HiddenRule]) -> None:
        self._companies = {rule.key: rule for rule in rules if rule.kind == "company"}
        jobs = [rule for rule in rules if rule.kind == "listing"]
        self._urls = {rule.key: rule for rule in jobs}
        # The same job on another board has another URL, so company and title match it too.
        self._jobs = {(rule.company_key, (rule.title or "").casefold()): rule for rule in jobs}

    def find(self, listing: Listing) -> HiddenRule | None:
        key = company_key(listing.company)
        return (
            self._companies.get(key)
            or self._urls.get(listing.url)
            or self._jobs.get((key, listing.title.casefold()))
        )


@dataclass(frozen=True)
class _CacheEntry:
    listings: list[Listing]
    fetched_at: datetime
    expires_at: float
    error: str | None = None
    warning: str | None = None


_executor = ThreadPoolExecutor(max_workers=len(BOARDS), thread_name_prefix="job-board")
# Scraped boards get their own few threads. A run of quick searches can start a slow LinkedIn
# scrape for each one, and in the shared pool those would hold up every feed board's refresh.
_SCRAPE_THREADS = 2
_scrape_executor = ThreadPoolExecutor(
    max_workers=_SCRAPE_THREADS, thread_name_prefix="job-board-scrape"
)


def executor_for(board: Board) -> ThreadPoolExecutor:
    return _scrape_executor if board.scrapes else _executor


_lock = threading.Lock()
_CacheKey = tuple[str, str, str, str]
_cache: dict[_CacheKey, _CacheEntry] = {}
_in_flight: dict[_CacheKey, Future[_CacheEntry]] = {}


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def new_client() -> httpx.Client:
    return httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS, headers={"User-Agent": USER_AGENT})


def _clip(value: str, limit: int) -> str:
    # Postgres refuses NUL characters, and the limits match what a tracked posting can hold.
    return value.replace("\x00", "")[:limit]


def _normalize(board: Board, raw: RawListing) -> Listing | None:
    """Turn a raw listing into one that can be shown as a link and saved as a posting."""
    url = canonical_url(raw.url)
    if not url or len(url) > MAX_URL or not url.lower().startswith(("http://", "https://")):
        return None
    # Jobicy sends names such as "hims &#038; hers". URLs stay as they are, since unescaping would
    # turn a query such as "&region=" into "®ion=".
    company = _clip(html.unescape(raw.company), MAX_NAME).strip()
    title = _clip(html.unescape(raw.title), MAX_NAME).strip()
    if not company or not title:
        return None
    location = _clip(html.unescape(raw.location or ""), MAX_NAME).strip() or None
    description = _clip(raw.description, MAX_TEXT)
    return Listing(
        id=hashlib.sha256(f"{board.id}|{url}".encode()).hexdigest()[:16],
        source=board.name,
        company=company,
        title=title,
        url=url,
        location=location,
        description=description,
        published_on=raw.published_on,
    )


def _fetch_board(
    board: Board, settings: Settings, query: BoardQuery
) -> tuple[_CacheEntry, list[RawListing] | None]:
    """The board's listings, with the raw ones to store. A failure has no raw listings."""
    now = time.monotonic()
    try:
        with new_client() as client:
            fetched = board.fetch(client, settings, query)
    except FETCH_ERRORS as error:
        reason = describe_error(error)
        logger.warning("Job board %s failed: %s", board.id, reason)
        return _CacheEntry([], utcnow(), now + FAILURE_CACHE_SECONDS, error=reason), None
    listings = [listing for raw in fetched.listings if (listing := _normalize(board, raw))]
    logger.info("Job board %s returned %d listings", board.id, len(listings))
    entry = _CacheEntry(
        listings,
        utcnow(),
        now + settings.listings_cache_minutes * 60,
        warning=fetched.warning,
    )
    return entry, fetched.listings


@dataclass(frozen=True)
class _Loaded:
    """A board's result for one search, and whether newer results are being fetched."""

    future: Future[_CacheEntry]
    refreshing: bool = False


def _key(board: Board, query: BoardQuery) -> _CacheKey:
    return (
        board.id,
        query.keywords.lower() if board.searches_keywords else "",
        query.location.lower() if board.searches_location else "",
        _DEEP if query.deep and board.deepens else "",
    )


def _resolved(entry: _CacheEntry) -> Future[_CacheEntry]:
    done: Future[_CacheEntry] = Future()
    done.set_result(entry)
    return done


def _from_store(store: ListingStore, board: Board, key: _CacheKey) -> _CacheEntry | None:
    stored = store.load(cache_key(*key))
    if stored is None:
        return None
    listings = [listing for raw in stored.listings if (listing := _normalize(board, raw))]
    remaining = (stored.fresh_until - utcnow()).total_seconds()
    return _CacheEntry(
        listings, stored.fetched_at, time.monotonic() + remaining, warning=stored.warning
    )


def _usable(entry: _CacheEntry, settings: Settings) -> bool:
    """Results past their fresh window are still shown while they're younger than the keep limit."""
    return not entry.error and entry.fetched_at >= utcnow() - KEEP_RESULTS


def _load(
    board: Board,
    settings: Settings,
    query: BoardQuery,
    store: ListingStore | None,
    recorder: CatalogRecorder | None = None,
) -> _Loaded:
    """Return fresh results, or older ones while newer are fetched, or join or start a fetch."""
    key = _key(board, query)

    def run(previous: _CacheEntry | None) -> _CacheEntry:
        try:
            entry, raw = _fetch_board(board, settings, query)
            if entry.error and previous is not None:
                # Keep showing the older results, and try again after a short wait.
                entry = replace(
                    previous,
                    expires_at=time.monotonic() + FAILURE_CACHE_SECONDS,
                    warning=f"Couldn't refresh: {entry.error}",
                )
            elif raw is not None and store is not None:
                store.save(
                    cache_key(*key),
                    StoredResults(
                        board=board.id,
                        listings=raw,
                        fetched_at=entry.fetched_at,
                        fresh_until=entry.fetched_at
                        + timedelta(minutes=settings.listings_cache_minutes),
                        warning=entry.warning,
                    ),
                )
            if raw and recorder is not None:
                # Queued, so the search has its results without waiting for the catalog.
                recorder.submit(board.id, raw)
            with _lock:
                _cache[key] = entry
                while len(_cache) > MAX_CACHE_ENTRIES:
                    del _cache[next(iter(_cache))]
            return entry
        finally:
            with _lock:
                _in_flight.pop(key, None)

    with _lock:
        cached = _cache.get(key)
    if cached is None and store is not None:
        # Read outside the lock, since it's a database call.
        stored = _from_store(store, board, key)
        if stored is not None:
            with _lock:
                _cache.setdefault(key, stored)

    with _lock:
        cached = _cache.get(key)
        if cached and cached.expires_at > time.monotonic():
            return _Loaded(_resolved(cached))
        running = _in_flight.get(key)
        if cached is not None and _usable(cached, settings):
            if running is None:
                _in_flight[key] = executor_for(board).submit(run, cached)
            return _Loaded(_resolved(cached), refreshing=True)
        if running:
            return _Loaded(running)
        future = executor_for(board).submit(run, None)
        _in_flight[key] = future
        return _Loaded(future)


def _keep(listing: Listing, board: Board, query: ListingQuery, location: str) -> bool:
    if not query.matches(
        listing.title, listing.company, listing.description, trust_text=board.searches_keywords
    ):
        return False
    if location and not board.searches_location:
        text = listing.location or ""
        if location in US_NAMES:
            return any(pattern.search(text) for pattern in _OPEN_TO_US)
        return location in text.lower()
    return True


def _ranked(listings: list[Listing], query: ListingQuery) -> list[Listing]:
    """Put title matches first, then the newest, and show a job on several boards only once."""

    def rank(listing: Listing) -> tuple[bool, int]:
        in_title = query.matches_title(listing.title, listing.company)
        published = listing.published_on.toordinal() if listing.published_on else 0
        return (not in_title, -published)

    seen_jobs: set[tuple[str, str]] = set()
    seen_urls: set[str] = set()
    unique = []
    for listing in sorted(listings, key=rank):
        job = (company_key(listing.company), listing.title.casefold())
        if job in seen_jobs or listing.url in seen_urls:
            continue
        seen_jobs.add(job)
        seen_urls.add(listing.url)
        unique.append(listing)
    return unique


def filter_listings(
    listings: Iterable[Listing],
    filters: ListingFilters,
    today: date,
    vocabulary: Vocabulary = BUILTIN,
) -> list[Listing]:
    """Apply the filter panel. A job with no stated salary or years isn't assumed to fail."""

    def stack(listing: Listing) -> set[str]:
        return {technology.name for technology in listing.parsed_with(vocabulary).technologies}

    def keep(listing: Listing) -> bool:
        parsed = listing.parsed
        if filters.levels and parsed.level not in filters.levels:
            return False
        if filters.role_families and parsed.role_family not in filters.role_families:
            return False
        if filters.work_modes and parsed.work_mode not in filters.work_modes:
            return False
        if filters.employment_types and parsed.employment_type not in filters.employment_types:
            return False
        if filters.sources and listing.source not in filters.sources:
            return False
        names = stack(listing)
        if not filters.stack_all <= names or filters.stack_none & names:
            return False
        top_salary = parsed.salary_max if parsed.salary_max is not None else parsed.salary_min
        if filters.has_salary and top_salary is None:
            return False
        if filters.salary_min is not None and top_salary is not None:
            if top_salary < filters.salary_min:
                return False
        if filters.years_max is not None and parsed.years_required is not None:
            if parsed.years_required > filters.years_max:
                return False
        if filters.posted_within_days is not None:
            oldest = today - timedelta(days=filters.posted_within_days)
            if listing.published_on is None or listing.published_on < oldest:
                return False
        return True

    return [listing for listing in listings if keep(listing)]


def listing_facts(
    listing: Listing, posting_id: uuid.UUID | None = None, vocabulary: Vocabulary = BUILTIN
) -> JobFacts:
    """posting_id is the saved posting for this listing, so its own applications are left out."""
    parsed = listing.parsed_with(vocabulary)
    return JobFacts(
        title=listing.title,
        level=parsed.level,
        role_family=parsed.role_family,
        years_required=parsed.years_required,
        technologies=tuple((tech.name, tech.requirement.value) for tech in parsed.technologies),
        requirements=listing.requirements_with(vocabulary),
        posting_id=posting_id,
    )


def search_listings(
    settings: Settings,
    keywords: str,
    location: str = "",
    store: ListingStore | None = None,
    recorder: CatalogRecorder | None = None,
    deep: bool = False,
    deadline_seconds: float = SEARCH_DEADLINE_SECONDS,
) -> SearchResults:
    """Search every usable board. One board failing only marks that board in the result.

    Without a store, results are kept in memory only. With a recorder, freshly fetched results
    are also recorded in the Companies catalog, in the background. A deep search, for a
    Scout run, asks the boards that can for more, and is given longer to answer.
    """
    parsed_query = parse_query(keywords)
    query = BoardQuery(parsed_query.board_keywords, location, deep)
    wanted_location = location.lower()

    loads: dict[str, _Loaded] = {}
    for board in BOARDS:
        if not missing_settings(board, settings):
            loads[board.id] = _load(board, settings, query, store, recorder)
    wait([loaded.future for loaded in loads.values()], timeout=deadline_seconds)

    matched: list[Listing] = []
    sources: list[SourceStatus] = []
    for board in BOARDS:
        loaded = loads.get(board.id)
        if loaded is None:
            missing = tuple(missing_settings(board, settings))
            sources.append(
                SourceStatus(
                    board.id,
                    board.name,
                    board.site,
                    "needs_setup",
                    message=f"Set {' and '.join(missing)} in .env, then restart the API.",
                    missing_settings=missing,
                    setup_url=board.setup_url,
                )
            )
            continue
        future = loaded.future
        if not future.done():
            sources.append(
                SourceStatus(
                    board.id,
                    board.name,
                    board.site,
                    "pending",
                    message="Still fetching. Results appear when it finishes.",
                )
            )
            continue
        failure = future.exception()
        if failure is not None:
            logger.error("Job board %s crashed", board.id, exc_info=failure)
            sources.append(
                SourceStatus(
                    board.id, board.name, board.site, "failed", message="Unexpected response"
                )
            )
            continue
        entry = future.result()
        if entry.error:
            sources.append(
                SourceStatus(
                    board.id,
                    board.name,
                    board.site,
                    "failed",
                    fetched_at=entry.fetched_at,
                    message=entry.error,
                )
            )
            continue
        kept = [
            listing
            for listing in entry.listings
            if _keep(listing, board, parsed_query, wanted_location)
        ]
        matched.extend(kept)
        sources.append(
            SourceStatus(
                board.id,
                board.name,
                board.site,
                "ok",
                listings=len(kept),
                fetched_at=entry.fetched_at,
                message=entry.warning,
                refreshing=loaded.refreshing,
            )
        )

    return SearchResults(_ranked(matched, parsed_query), sources)
