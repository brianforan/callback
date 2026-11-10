"""The Companies catalog's jobs: one record per job, with every place it's listed.

Listings arrive from Job listings' boards, from pages Scout reads and from its leads. Each is
matched to a job by, in order: its link; its hiring system's job id (Greenhouse, Lever, Ashby);
the same company, title and place; and, for long postings, near-identical text. The employer's
own posting is a job's main link, and the others are where it's also listed.

Recording runs after a search has its results, on its own thread, so it can't slow or fail one.
"""

import hashlib
import logging
import re
import uuid
from collections.abc import Callable, Iterable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.companies.identity import ResolvedCompany, matching_key, resolve_company
from app.db import commit_retrying_deadlock
from app.listings.boards import RawListing
from app.models import (
    BoardStat,
    CatalogJob,
    CatalogJobSource,
    Company,
    CompanyKind,
    CompanyResearch,
    CompanySource,
    ProfileCompany,
    utcnow,
)
from app.postings.parser import canonical_url

logger = logging.getLogger(__name__)

HIRING_SYSTEMS = ("greenhouse", "lever", "ashby")
# Reads in a row that failed before the catalog worker leaves a source alone.
SOURCE_MAX_FAILURES = 3
# Any page that isn't a hiring system or a job board.
WEB = "web"

_TOKEN = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
_GREENHOUSE_HOSTS = {
    "boards.greenhouse.io",
    "job-boards.greenhouse.io",
    "boards.eu.greenhouse.io",
    "job-boards.eu.greenhouse.io",
}
_LEVER_HOSTS = {"jobs.lever.co", "jobs.eu.lever.co"}
_ASHBY_HOSTS = {"jobs.ashbyhq.com"}

# Postings shorter than this are often a board's snippet, too little to compare by text.
_MIN_FINGERPRINT_TEXT = 600
# Bits two fingerprints may differ by and still be the same posting.
SAME_TEXT_BITS = 3
_MAX_TITLE = 200
_MAX_LOCATION = 300


# Query parameters that only track how someone got to a listing, so one job's links differ.
_TRACKING = re.compile(
    r"^(?:utm_\w+|gh_src|lever-source|ref|refid|referrer|source|src|trk|trackingid)$",
    re.I,
)


# Boards whose links name the job in the path, so their whole query is tracking.
_PATH_ID_HOSTS = ("jooble.org",)


def catalog_url(url: str | None) -> str | None:
    """A listing's link without its tracking parameters, so one job has one link per place."""
    link = canonical_url(url)
    if link is None:
        return None
    parts = urlsplit(link)
    host = (parts.hostname or "").lower()
    if any(host == name or host.endswith(f".{name}") for name in _PATH_ID_HOSTS):
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    kept = [(name, value) for name, value in parse_qsl(parts.query) if not _TRACKING.match(name)]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept), ""))


@dataclass(frozen=True)
class HiringRef:
    """A hiring system named in a link: its company token and job id, when the link has them."""

    system: str
    token: str | None
    job_id: str | None


@dataclass(frozen=True)
class CatalogListing:
    """One listing from anywhere, as the catalog records it.

    agency is the agency a judge named, when company is the employer it posted for.
    direct_url is a board's link to the employer's own posting, such as JobSpy's.
    """

    source: str
    url: str
    company: str
    title: str
    text: str = ""
    location: str | None = None
    work_mode: str | None = None
    posted_on: date | None = None
    direct_url: str | None = None
    agency: str | None = None


def _valid(value: str | None) -> str | None:
    return value if value and _TOKEN.match(value) else None


def hiring_ref(url: str | None) -> HiringRef | None:
    """The Greenhouse, Lever or Ashby company and job a link points at, or None.

    Tokens and ids are checked before they're kept, since chunk 3 builds requests from them.
    """
    if not url:
        return None
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    segments = [segment for segment in parts.path.split("/") if segment]
    query = parse_qs(parts.query)
    if host in _GREENHOUSE_HOSTS:
        if segments[:1] == ["embed"]:
            # job_app links name a job; job_board ones, and their /js script, the whole board.
            token = _valid((query.get("for") or [None])[0])
            job_id = (
                _valid((query.get("token") or [None])[0]) if segments[1:2] == ["job_app"] else None
            )
            return HiringRef("greenhouse", token and token.lower(), job_id) if token else None
        token = _valid(segments[0]) if segments else None
        if token is None:
            return None
        job_id = _valid(segments[2]) if len(segments) >= 3 and segments[1] == "jobs" else None
        return HiringRef("greenhouse", token.lower(), job_id)
    if host == "boards-api.greenhouse.io" and segments[:2] == ["v1", "boards"]:
        token = _valid(segments[2]) if len(segments) >= 3 else None
        job_id = _valid(segments[4]) if len(segments) >= 5 and segments[3] == "jobs" else None
        return HiringRef("greenhouse", token.lower(), job_id) if token else None
    if host in _LEVER_HOSTS or host in _ASHBY_HOSTS:
        system = "lever" if host in _LEVER_HOSTS else "ashby"
        token = _valid(segments[0]) if segments else None
        job_id = (
            _valid(segments[1])
            if len(segments) >= 2 and segments[1] not in ("apply", "embed", "jobs")
            else None
        )
        return HiringRef(system, token.lower(), job_id) if token else None
    if host == "api.lever.co" and segments[:2] == ["v0", "postings"]:
        token = _valid(segments[2]) if len(segments) >= 3 else None
        job_id = _valid(segments[3]) if len(segments) >= 4 else None
        return HiringRef("lever", token.lower(), job_id) if token else None
    # A company's own careers page with an embedded Greenhouse board.
    job_id = _valid((query.get("gh_jid") or [None])[0])
    return HiringRef("greenhouse", None, job_id) if job_id else None


# Trailing places and work modes dropped from a title, such as "(Remote)" or "- New York, NY".
_MODES = r"remote|hybrid|on-?site|in-?office|anywhere|worldwide|global|distributed"
_REGIONS = (
    r"us|usa|u\.s\.a?\.?|united states|north america|americas|canada|uk|united kingdom|europe|"
    r"emea|latam|apac|nyc|sf|bay area|east coast|west coast"
)
_PLACE = re.compile(
    rf"^(?:(?:{_MODES}|{_REGIONS})(?:[\s/,&-]+(?:{_MODES}|{_REGIONS}|only))*"
    rf"|.*\b(?:{_MODES})\b.*"
    r"|[a-z][a-z .'-]{1,40},\s*[a-z]{2}(?:\s*\d{5})?)$"
)
_SUFFIX = re.compile(
    r"^(?P<head>.+?)\s*(?:[(\[](?P<bracket>[^)\]]*)[)\]]|\s[-–|,]\s*(?P<tail>.+))$"
)
_ABBREVIATIONS = {"sr": "senior", "jr": "junior", "eng": "engineer", "mgr": "manager"}
_MAX_SUFFIX_WORDS = 5


def title_key(title: str) -> str:
    """A title with its spelling settled, so "Sr. Engineer - Remote" and "Senior Engineer" match.

    Levels stay ("Engineer II" isn't "Engineer III"), and so do teams ("Engineer - Platform").
    """
    text = title.lower().strip()
    while (found := _SUFFIX.match(text)) is not None:
        suffix = (found.group("bracket") or found.group("tail") or "").strip()
        if len(suffix.split()) > _MAX_SUFFIX_WORDS or not _PLACE.match(suffix):
            break
        text = found.group("head").strip()
    words = re.findall(r"[a-z0-9]+", text.replace("&", " and "))
    return " ".join(_ABBREVIATIONS.get(word, word) for word in words)[:_MAX_TITLE]


def fingerprint(text: str) -> int | None:
    """A 64-bit SimHash of the text's three-word runs, as a signed number for Postgres's bigint.

    None for text too short to compare, such as a board's snippet.
    """
    if len(text) < _MIN_FINGERPRINT_TEXT:
        return None
    words = re.findall(r"[a-z0-9]+", text.lower())
    shingles = {" ".join(words[index : index + 3]) for index in range(len(words) - 2)}
    if not shingles:
        return None
    weights = [0] * 64
    for shingle in shingles:
        value = int.from_bytes(hashlib.blake2b(shingle.encode(), digest_size=8).digest(), "big")
        for bit in range(64):
            weights[bit] += 1 if value >> bit & 1 else -1
    value = sum(1 << bit for bit in range(64) if weights[bit] > 0)
    return value - (1 << 64) if value >= 1 << 63 else value


def text_distance(first: int, second: int) -> int:
    """Bits two fingerprints differ by."""
    return ((first ^ second) & ((1 << 64) - 1)).bit_count()


_US_NAMES = re.compile(r"\b(?:united states(?: of america)?|u\.s\.a?\.?|usa)(?![\w.])")
_MODE_WORDS = {"remote", "hybrid", "onsite", "on", "site", "in", "office", "only", "or", "and"}


def _place_words(location: str | None) -> set[str]:
    text = _US_NAMES.sub(" us ", (location or "").lower())
    return {word for word in re.findall(r"[a-z0-9]+", text) if word not in _MODE_WORDS}


def _same_place(first: str | None, second: str | None) -> bool:
    """Both blank, one blank, or one names the other, such as "New York" and "New York, NY", or
    "Remote, US" and "United States". Work modes don't count as places."""
    a, b = _place_words(first), _place_words(second)
    return not a or not b or a <= b or b <= a


def _source_of(url: str, ref: HiringRef | None, fallback: str) -> str:
    return ref.system if ref is not None and ref.token is not None else fallback


def _bump(db: Session, board: str, *, recorded: int = 0, kept: int = 0) -> None:
    """Add to a source's counts in SQL, so two processes counting at once both count."""
    stat = db.get(BoardStat, board)
    if stat is None:
        try:
            with db.begin_nested():
                db.add(BoardStat(board=board, listings_recorded=0, kept=0))
        except IntegrityError:
            logger.debug("Another process added the stats row for %s first", board)
    values: dict[str, object] = {
        "listings_recorded": BoardStat.listings_recorded + recorded,
        "kept": BoardStat.kept + kept,
    }
    if kept:
        values["last_kept_at"] = utcnow()
    db.execute(
        update(BoardStat)
        .where(BoardStat.board == board)
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if stat is not None:
        db.expire(stat)


def company_source(
    db: Session, company: Company, source: str, identifier: str, is_own: bool
) -> None:
    """Note that this company's jobs are listed here, or that it was seen there again."""
    identifier = identifier[:200]
    row = db.scalar(
        select(CompanySource).where(
            CompanySource.source == source, CompanySource.identifier == identifier
        )
    )
    if row is not None:
        row.last_seen = utcnow()
        if is_own and (row.status == "dead" or row.failures >= SOURCE_MAX_FAILURES):
            # Seen in a new link, so it's worth reading again.
            logger.info("%s:%s turned up again, so it will be read again", source, identifier)
            row.status, row.failures = "ok", 0
        return
    try:
        with db.begin_nested():
            db.add(
                CompanySource(
                    company_id=company.id, source=source, identifier=identifier, is_own=is_own
                )
            )
    except IntegrityError:
        logger.debug("Another process added %s:%s first", source, identifier)


def _resolve(db: Session, listing: CatalogListing, own: HiringRef | None) -> ResolvedCompany:
    source = (own.system, own.token) if own is not None and own.token is not None else None
    if listing.agency and matching_key(listing.agency) != matching_key(listing.company):
        # A judge named both: the company is the employer.
        employer = resolve_company(db, listing.company, source=source).company
        agency = resolve_company(db, listing.agency).company
        if agency.kind != CompanyKind.AGENCY.value:
            agency.kind = CompanyKind.AGENCY.value
        return ResolvedCompany(company=employer, agency=agency)
    resolved = resolve_company(db, listing.company, source=source)
    if listing.agency and resolved.company.kind != CompanyKind.AGENCY.value:
        # The judge named the company as the agency, with no employer.
        resolved.company.kind = CompanyKind.AGENCY.value
    return resolved


def _companies_posted_for(db: Session, agency: Company) -> list[uuid.UUID]:
    return list(
        db.scalars(
            select(CatalogJob.company_id)
            .join(CatalogJobSource, CatalogJobSource.job_id == CatalogJob.id)
            .where(CatalogJobSource.agency_id == agency.id)
            .distinct()
        ).all()
    )


def _match(
    db: Session,
    urls: list[str],
    refs: list[HiringRef],
    company: Company,
    key: str,
    location: str | None,
    print_: int | None,
    source: str,
) -> tuple[CatalogJob | None, Company | None]:
    """The job a listing is, by the rules in order, and the employer when an agency's listing
    matched a job at a company it posts for.

    Text alone links listings from different places only: one place listing two near-identical
    postings under different titles, such as one per language, is listing two jobs.
    """
    for url in urls:
        found = db.scalar(select(CatalogJobSource).where(CatalogJobSource.url == url))
        if found is not None:
            return found.job, None
    for ref in refs:
        if ref.job_id is None:
            continue
        found = db.scalar(
            select(CatalogJobSource)
            .where(
                CatalogJobSource.source == ref.system, CatalogJobSource.external_id == ref.job_id
            )
            .limit(1)
        )
        if found is not None:
            return found.job, None
    same_title = db.scalars(
        select(CatalogJob)
        .where(CatalogJob.company_id == company.id, CatalogJob.title_key == key)
        .order_by(CatalogJob.first_seen)
    ).all()
    for job in same_title:
        if _same_place(job.location, location):
            return job, None
    if print_ is None:
        return None, None
    agency_only = company.kind == CompanyKind.AGENCY.value
    company_ids = [company.id, *(_companies_posted_for(db, company) if agency_only else [])]
    rows = db.execute(
        select(CatalogJob.id, CatalogJob.fingerprint, CatalogJob.company_id).where(
            CatalogJob.company_id.in_(company_ids), CatalogJob.fingerprint.is_not(None)
        )
    ).all()
    for job_id, other, company_id in rows:
        if text_distance(print_, other) > SAME_TEXT_BITS:
            continue
        listed_here = db.scalar(
            select(CatalogJobSource.id)
            .where(CatalogJobSource.job_id == job_id, CatalogJobSource.source == source)
            .limit(1)
        )
        if listed_here is None:
            employer = db.get(Company, company_id) if company_id != company.id else None
            return db.get(CatalogJob, job_id), employer
    return None, None


def _primary(job: CatalogJob) -> CatalogJobSource | None:
    """The employer's own posting when the job has one, else its earliest listing."""
    own = [
        source
        for source in job.sources
        if source.source in HIRING_SYSTEMS and source.agency_id is None
    ]
    pool = own or job.sources
    if not pool:
        return None
    return min(pool, key=lambda source: (source.first_seen.replace(tzinfo=None), str(source.id)))


def _set_primary(job: CatalogJob) -> CatalogJobSource | None:
    pick = _primary(job)
    for source in job.sources:
        source.is_primary = source is pick
    return pick


def _add_source(
    db: Session,
    job: CatalogJob,
    source: str,
    url: str,
    ref: HiringRef | None,
    agency: Company | None,
) -> CatalogJobSource:
    existing = db.scalar(select(CatalogJobSource).where(CatalogJobSource.url == url))
    now = utcnow()
    if existing is not None:
        existing.last_seen = now
        if existing.source == WEB and source != WEB:
            # A page first seen through Scout turned out to be this board's.
            existing.source = source
        if existing.external_id is None and ref is not None and ref.job_id is not None:
            existing.external_id = ref.job_id
        return existing
    added = CatalogJobSource(
        job=job,
        source=source,
        url=url,
        external_id=ref.job_id if ref is not None else None,
        agency_id=agency.id if agency is not None else None,
        is_primary=False,
        first_seen=now,
        last_seen=now,
    )
    db.add(added)
    _bump(db, source, recorded=1)
    return added


def record_listing(db: Session, listing: CatalogListing) -> CatalogJob | None:
    """Record one listing in the catalog, as a new job or another place an existing one is
    listed. None for a listing with no link, title or company. Flushes; the caller commits."""
    url = catalog_url(listing.url)
    title = " ".join(listing.title.split())[:_MAX_TITLE]
    key = title_key(title)
    if not url or not key or not listing.company.strip():
        return None
    direct = catalog_url(listing.direct_url)
    if direct == url:
        direct = None
    ref, direct_ref = hiring_ref(url), hiring_ref(direct)
    own = next((item for item in (ref, direct_ref) if item and item.token), None)
    resolved = _resolve(db, listing, own)
    company, agency = resolved.company, resolved.agency
    location = (listing.location or "").strip()[:_MAX_LOCATION] or None
    print_ = fingerprint(listing.text)
    board_source = _source_of(url, ref, listing.source)
    job, employer = _match(
        db,
        [item for item in (url, direct) if item],
        [item for item in (ref, direct_ref) if item],
        company,
        key,
        location,
        print_,
        board_source,
    )
    if employer is not None:
        # An agency's own listing, matched by its text to a job at a company it posts for.
        agency, company = company, employer
    now = utcnow()
    if job is None:
        job = CatalogJob(
            company_id=company.id,
            title=title,
            title_key=key,
            location=location,
            work_mode=listing.work_mode,
            posted_on=listing.posted_on,
            text=listing.text,
            fingerprint=print_,
            first_seen=now,
            last_seen=now,
        )
        db.add(job)
        db.flush()
    else:
        job.last_seen = now
        job.closed_at = None
        job.location = job.location or location
        job.work_mode = job.work_mode or listing.work_mode
        job.posted_on = job.posted_on or listing.posted_on
        if len(listing.text) > len(job.text):
            job.text, job.fingerprint = listing.text, print_
    added = _add_source(db, job, board_source, url, ref, agency)
    if direct is not None:
        _add_source(db, job, _source_of(direct, direct_ref, WEB), direct, direct_ref, None)
    db.flush()
    db.refresh(job, ["sources"])
    if _set_primary(job) is added:
        # The listing that's now the main link gives the job its title.
        job.title, job.title_key = title, key
    if own is not None and own.token is not None:
        company_source(db, company, own.system, own.token, is_own=True)
    if board_source not in (*HIRING_SYSTEMS, WEB):
        # The board's name for the listing, which with agency wording names the employer.
        company_source(db, company, board_source, listing.company, is_own=False)
    db.flush()
    return job


def record_kept(db: Session, url: str | None) -> None:
    """Count a judge keeping the job at this link for each place the job is listed."""
    link = catalog_url(url)
    if link is None:
        return
    found = db.scalar(select(CatalogJobSource).where(CatalogJobSource.url == link))
    if found is None:
        return
    for source in {item.source for item in found.job.sources}:
        _bump(db, source, kept=1)


def merge_jobs(db: Session, keep: CatalogJob, drop: CatalogJob) -> None:
    """Make two records of one job one: drop's listings move to keep, and drop is deleted."""
    if keep.id == drop.id:
        return
    db.execute(
        update(CatalogJobSource)
        .where(CatalogJobSource.job_id == drop.id)
        .values(job_id=keep.id)
        .execution_options(synchronize_session=False)
    )
    keep.first_seen = min(
        keep.first_seen, drop.first_seen, key=lambda moment: moment.replace(tzinfo=None)
    )
    keep.last_seen = max(
        keep.last_seen, drop.last_seen, key=lambda moment: moment.replace(tzinfo=None)
    )
    if keep.closed_at is None or drop.closed_at is None:
        keep.closed_at = None
    db.flush()
    db.expire(drop)
    db.delete(drop)
    db.flush()
    db.refresh(keep, ["sources"])
    _set_primary(keep)


def move_company_rows(db: Session, keep: Company, drop: Company) -> None:
    """Before a company merge: drop's sources and jobs move to keep, and jobs that turn out to be
    the same are merged."""
    for listed in db.scalars(select(ProfileCompany).where(ProfileCompany.company_id == drop.id)):
        twin = db.get(ProfileCompany, (listed.profile_id, keep.id))
        if twin is None:
            continue
        # On one profile's list twice: one entry keeps both counts.
        twin.leads += listed.leads
        twin.dismissed += listed.dismissed
        db.delete(listed)
    db.flush()
    for model, column in (
        (CompanySource, CompanySource.company_id),
        (CompanyResearch, CompanyResearch.company_id),
        (CatalogJob, CatalogJob.company_id),
        (CatalogJobSource, CatalogJobSource.agency_id),
        (ProfileCompany, ProfileCompany.company_id),
    ):
        db.execute(
            update(model)
            .where(column == drop.id)
            .values({column.key: keep.id})
            .execution_options(synchronize_session=False)
        )
    db.expire_all()
    jobs = db.scalars(
        select(CatalogJob)
        .where(CatalogJob.company_id == keep.id)
        .order_by(CatalogJob.title_key, CatalogJob.first_seen)
    ).all()
    kept: list[CatalogJob] = []
    for job in jobs:
        twin = next(
            (
                other
                for other in kept
                if other.title_key == job.title_key and _same_place(other.location, job.location)
            ),
            None,
        )
        if twin is None:
            kept.append(job)
        else:
            merge_jobs(db, twin, job)


def from_raw(board: str, raw: RawListing) -> CatalogListing:
    """A job board listing, as the catalog records it."""
    return CatalogListing(
        source=board,
        url=raw.url,
        company=raw.company,
        title=raw.title,
        text=raw.description,
        location=raw.location,
        posted_on=raw.published_on,
        direct_url=raw.direct_url,
    )


def _posted(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def _mode(value: Any) -> str | None:
    text = str(value or "").strip().lower()[:20]
    return text or None


def page_listing(page: dict[str, Any]) -> CatalogListing:
    """A page the pipeline read, as the catalog records it."""
    source = str(page.get("source") or "")
    return CatalogListing(
        source=source if source in HIRING_SYSTEMS else WEB,
        url=str(page.get("url") or ""),
        company=str(page.get("company") or ""),
        title=str(page.get("title") or ""),
        text=str(page.get("text") or ""),
        location=str(page.get("location") or "") or None,
        work_mode=_mode(page.get("work_mode")),
        posted_on=_posted(page.get("posted_at")),
    )


def lead_listing(lead: dict[str, Any]) -> CatalogListing:
    """A lead either engine kept, as the catalog records it, with the agency its judge named."""
    url = str(lead.get("url") or "")
    ref = hiring_ref(url)
    return CatalogListing(
        source=ref.system if ref is not None and ref.token is not None else WEB,
        url=url,
        company=str(lead.get("company") or ""),
        title=str(lead.get("title") or ""),
        text=str(lead.get("posting_text") or ""),
        location=str(lead.get("location") or "") or None,
        work_mode=_mode(lead.get("work_mode")),
        posted_on=_posted(lead.get("posted")),
        agency=str(lead.get("agency") or "") or None,
    )


def record_all(db: Session, listings: Iterable[CatalogListing]) -> int:
    """Record listings one by one, skipping any whose company can't be named. Returns how many
    were recorded. Flushes; the caller commits."""
    recorded = 0
    for listing in listings:
        try:
            if record_listing(db, listing) is not None:
                recorded += 1
        except ValueError as exc:
            logger.info("Skipped a %s listing for the catalog: %s", listing.source, exc)
    return recorded


class CatalogRecorder:
    """Records job board results in the catalog on one background thread, in the order they
    came, so searches never wait on it and two batches never write at once in one process."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self.session_factory = session_factory
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="catalog")

    def submit(self, board: str, raws: list[RawListing]) -> Future[int]:
        return self._executor.submit(self._record, board, list(raws))

    def _record(self, board: str, raws: list[RawListing]) -> int:
        started = datetime.now()
        with self.session_factory() as db:
            try:
                recorded = commit_retrying_deadlock(
                    db, lambda: record_all(db, (from_raw(board, raw) for raw in raws))
                )
            except SQLAlchemyError:
                db.rollback()
                logger.exception("Couldn't record %d %s listings in the catalog", len(raws), board)
                return 0
        seconds = (datetime.now() - started).total_seconds()
        logger.info("Recorded %d %s listings in the catalog in %.1fs", recorded, board, seconds)
        return recorded
