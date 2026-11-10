"""Keeps the Companies catalog's job lists fresh: reads every open job at the companies on any
profile's list, straight from each company's own hiring system, every 6 hours.

Runs as its own container from the API image: python -m app.companies.catalog_worker. It holds no
API key and makes no model calls. Its egress network exists only to reach Greenhouse, Lever and
Ashby. A Companies run reads what this stores, so a run never waits on hundreds of requests.
"""

import logging
import time
import uuid
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx
from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.companies.catalog import (
    SOURCE_MAX_FAILURES,
    CatalogListing,
    catalog_url,
    hiring_ref,
    record_all,
)
from app.companies.feeds import FeedGone, FeedJob, new_feed_client, read_feed
from app.db import SessionLocal
from app.logs import configure_logging
from app.models import (
    CatalogJob,
    CatalogJobSource,
    Company,
    CompanySource,
    ProfileCompany,
    utcnow,
)
from app.workers import poll_forever

logger = logging.getLogger(__name__)

READ_EVERY = timedelta(hours=6)
# Boards on no one's list are read too, less often, so Scout's catalog-wide scan sees
# their jobs close.
READ_UNLISTED_EVERY = timedelta(hours=24)
# Reads in a row that failed before a source is left alone, until it turns up in a new link.
MAX_FAILURES = SOURCE_MAX_FAILURES
BATCH = 8
POLL_SECONDS = 20.0
_READ_ERRORS = (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError)

ReadFeed = Callable[[str, str], list[FeedJob]]


@dataclass(frozen=True)
class _Read:
    source_id: uuid.UUID
    jobs: list[FeedJob] | None
    gone: bool = False
    error: str | None = None


def due_sources(db: Session, now: datetime, limit: int = BATCH) -> list[CompanySource]:
    """Own hiring systems a run is waiting on, never read, on any profile's list and not read in
    6 hours, or on no list and not read in 24 hours. Waited-on and never-read ones first, then
    listed ones."""
    on_a_list = exists().where(ProfileCompany.company_id == CompanySource.company_id)
    return list(
        db.scalars(
            select(CompanySource)
            .where(
                CompanySource.is_own.is_(True),
                CompanySource.status != "dead",
                CompanySource.failures < MAX_FAILURES,
                (CompanySource.wanted_by.is_not(None))
                | (CompanySource.last_checked.is_(None))
                | (on_a_list & (CompanySource.last_checked < now - READ_EVERY))
                | (CompanySource.last_checked < now - READ_UNLISTED_EVERY),
            )
            .order_by(
                CompanySource.wanted_by.is_(None),
                CompanySource.last_checked.is_not(None),
                ~on_a_list,
                CompanySource.last_checked,
            )
            .limit(limit)
        ).all()
    )


def _fetch(read: ReadFeed, source_id: uuid.UUID, system: str, token: str) -> _Read:
    try:
        return _Read(source_id, read(system, token))
    except FeedGone as exc:
        return _Read(source_id, None, gone=True, error=str(exc))
    except _READ_ERRORS as exc:
        return _Read(source_id, None, error=type(exc).__name__)


def _close_missing(db: Session, source: CompanySource, open_urls: set[str], now: datetime) -> int:
    """Mark the company's jobs from this board that its feed no longer lists as closed."""
    closed = 0
    rows = db.execute(
        select(CatalogJob, CatalogJobSource.url)
        .join(CatalogJobSource, CatalogJobSource.job_id == CatalogJob.id)
        .where(
            CatalogJob.company_id == source.company_id,
            CatalogJob.closed_at.is_(None),
            CatalogJobSource.source == source.source,
        )
    ).all()
    still_open = {job.id for job, url in rows if url in open_urls}
    for job, url in rows:
        ref = hiring_ref(url)
        if ref is None or ref.token != source.identifier or job.id in still_open:
            continue
        job.closed_at = now
        closed += 1
    return closed


def _apply(db: Session, result: _Read, now: datetime) -> None:
    source = db.get(CompanySource, result.source_id)
    if source is None:
        return
    source.last_checked = now
    source.wanted_by = None
    if result.jobs is None:
        source.failures += 1
        if result.gone:
            source.status = "dead"
        logger.warning(
            "Couldn't read %s:%s (%s), %d failures in a row",
            source.source,
            source.identifier,
            result.error,
            source.failures,
        )
        db.commit()
        return
    company = db.get(Company, source.company_id)
    listings = [
        CatalogListing(
            source=source.source,
            url=job.url,
            company=job.company or (company.name if company else source.identifier),
            title=job.title,
            text=job.text,
            location=job.location,
            work_mode=job.work_mode,
            posted_on=job.posted_on,
        )
        for job in result.jobs
    ]
    recorded = record_all(db, listings)
    open_urls = {url for job in result.jobs if (url := catalog_url(job.url))}
    db.flush()
    closed = _close_missing(db, source, open_urls, now)
    source.failures = 0
    source.status = "ok" if result.jobs else "empty"
    source.open_jobs = len(result.jobs)
    db.commit()
    logger.info(
        "Read %s:%s: %d open jobs recorded, %d closed",
        source.source,
        source.identifier,
        recorded,
        closed,
    )


def apply_read(
    db: Session, source_id: uuid.UUID, jobs: list[FeedJob] | None, now: datetime
) -> None:
    """Record one board's read, as the worker does: its jobs, and the board's state. Commits."""
    _apply(db, _Read(source_id, jobs, error=None if jobs is not None else "no jobs"), now)


def read_due(db: Session, read: ReadFeed, now: datetime | None = None, limit: int = BATCH) -> int:
    """Read one batch of due sources, the downloads at once and the recording one by one.
    Returns how many were read."""
    moment = now or utcnow()
    sources = due_sources(db, moment, limit)
    if not sources:
        return 0
    targets = [(source.id, source.source, source.identifier) for source in sources]
    db.commit()
    with ThreadPoolExecutor(max_workers=len(targets)) as pool:
        results = list(pool.map(lambda target: _fetch(read, *target), targets))
    for result in results:
        _apply(db, result, utcnow())
    return len(results)


def request_reads(db: Session, company_ids: Iterable[uuid.UUID], now: datetime) -> list[uuid.UUID]:
    """Ask the worker to read these companies' never-read sources first. Returns their ids."""
    sources = db.scalars(
        select(CompanySource).where(
            CompanySource.company_id.in_(list(company_ids)),
            CompanySource.is_own.is_(True),
            CompanySource.status != "dead",
            CompanySource.failures < MAX_FAILURES,
            CompanySource.last_checked.is_(None),
        )
    ).all()
    for source in sources:
        source.wanted_by = now
    db.commit()
    return [source.id for source in sources]


def wait_for_reads(
    db: Session,
    source_ids: list[uuid.UUID],
    timeout: float,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Wait up to timeout seconds for these sources to be read. Returns how many still aren't."""
    deadline = clock() + timeout
    while True:
        db.expire_all()
        unread = db.scalar(
            select(CompanySource.id)
            .where(CompanySource.id.in_(source_ids), CompanySource.last_checked.is_(None))
            .limit(1)
        )
        if unread is None or clock() >= deadline:
            break
        sleep(2.0)
    left = db.scalars(
        select(CompanySource.id).where(
            CompanySource.id.in_(source_ids), CompanySource.last_checked.is_(None)
        )
    ).all()
    db.commit()
    return len(left)


def main() -> None:
    configure_logging()
    logger.info("Catalog worker started")
    with new_feed_client() as client:

        def read(system: str, token: str) -> list[FeedJob]:
            return read_feed(client, system, token)

        def step() -> bool:
            with SessionLocal() as db:
                return read_due(db, read) > 0

        poll_forever("catalog worker", POLL_SECONDS, step)


if __name__ == "__main__":
    main()
