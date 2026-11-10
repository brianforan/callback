"""Works through queued company research snapshots, one at a time, from the tailor worker's loop.

For each: gather what the catalog knows about the company and the job its requester applied
to, send it to the tailor service (Haiku with web search), check every fact that comes back
(app.research.snapshots) and store what passes. A failed call is recorded with what it cost and
never blocks anything else.
"""

import logging
from collections import Counter
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    CatalogJob,
    Company,
    CompanyResearch,
    CompanySource,
    JobPosting,
    TailorStatus,
    utcnow,
)
from app.research.snapshots import clean_research
from app.schemas import CompanyResearchServiceResult
from app.workers import TailorUnavailable, claim_oldest, post_json, run_safely

logger = logging.getLogger(__name__)

# The tailor service refuses anything longer, so the limits are checked before sending.
_MAX_SHORT = 300
_MAX_CATALOG_SOURCES = 20
# Extra minutes a running snapshot gets beyond its timeout before it counts as abandoned.
_STALE_MARGIN = timedelta(minutes=5)

Research = Callable[[dict[str, Any]], CompanyResearchServiceResult]


def http_researcher(client: httpx.Client) -> Research:
    def research(payload: dict[str, Any]) -> CompanyResearchServiceResult:
        return post_json(client, "/companies/research", payload, CompanyResearchServiceResult)

    return research


def claim_next_research(db: Session) -> CompanyResearch | None:
    """Take the oldest queued snapshot and mark it running."""
    return claim_oldest(db, CompanyResearch)


def _finish(research: CompanyResearch, status: TailorStatus, note: str | None) -> None:
    research.status = status.value
    research.note = note
    research.finished_at = utcnow()


def fail_stale_research(db: Session, now: datetime, timeout_seconds: float) -> int:
    """Fail snapshots left running by a worker that stopped, so they don't hang forever."""
    cutoff = now - timedelta(seconds=timeout_seconds) - _STALE_MARGIN
    stale = db.scalars(
        select(CompanyResearch).where(
            CompanyResearch.status == TailorStatus.RUNNING.value,
            CompanyResearch.started_at < cutoff,
        )
    ).all()
    for research in stale:
        logger.warning("Company research %s was abandoned while running", research.id)
        _finish(research, TailorStatus.FAILED, "The research stopped before it finished.")
    db.commit()
    return len(stale)


def _short(value: str | None) -> str:
    return (value or "").strip()[:_MAX_SHORT]


def research_payload(db: Session, research: CompanyResearch, today: date) -> dict[str, Any] | None:
    """What the model is told about the company, or None when the company is gone.

    The job's title and location come from the requester's newest posting at this company and
    are sent, never stored on the snapshot.
    """
    company = db.get(Company, research.company_id)
    if company is None:
        return None
    posting = None
    if research.requested_by is not None:
        posting = db.scalar(
            select(JobPosting)
            .where(
                JobPosting.user_id == research.requested_by,
                JobPosting.company_key == company.key,
            )
            .order_by(JobPosting.seen_on.desc(), JobPosting.created_at.desc())
            .limit(1)
        )
    sources = db.scalars(
        select(CompanySource)
        .where(CompanySource.company_id == company.id, CompanySource.status != "dead")
        .order_by(CompanySource.is_own.desc(), CompanySource.source)
        .limit(_MAX_CATALOG_SOURCES)
    ).all()
    open_jobs = db.scalar(
        select(func.count(CatalogJob.id)).where(
            CatalogJob.company_id == company.id, CatalogJob.closed_at.is_(None)
        )
    )
    return {
        "company": _short(company.name),
        "domain": _short(company.domain),
        "title": _short(posting.title if posting else None),
        "location": _short(posting.location if posting else None),
        "catalog_sources": [
            _short(f"{source.source}:{source.identifier}{' (own)' if source.is_own else ''}")
            for source in sources
        ],
        "open_jobs": open_jobs or 0,
        "today": today.isoformat(),
    }


def _add_cost(research: CompanyResearch, usage: dict[str, Any], cost_usd: float) -> None:
    total: Counter[str] = Counter(research.usage or {})
    total.update({name: int(count or 0) for name, count in usage.items()})
    research.usage = dict(total)
    research.cost_usd = round((research.cost_usd or 0.0) + cost_usd, 6)


def run_research(db: Session, research: CompanyResearch, call: Research, today: date) -> None:
    payload = research_payload(db, research, today)
    if payload is None:
        _finish(research, TailorStatus.FAILED, "The company is no longer in the catalog.")
        db.commit()
        return
    # End the read transaction, so nothing is held open during the slow call.
    db.commit()
    try:
        result = call(payload)
    except TailorUnavailable as exc:
        if exc.spent:
            # Model calls and searches are billed even when their answer can't be used.
            _add_cost(research, exc.spent.get("usage") or {}, float(exc.spent.get("cost_usd") or 0))
        _finish(
            research, TailorStatus.FAILED, f"The research couldn't be done: {str(exc).rstrip('.')}."
        )
        db.commit()
        return
    clean = clean_research(result.research, today)
    research.sources = clean.sources
    for name, value in clean.sections.items():
        setattr(research, name, value)
    research.confidence = clean.confidence
    research.dropped = clean.dropped
    research.searches = result.searches
    research.model = result.model[:40] or None
    _add_cost(research, result.usage.model_dump(), result.cost_usd)
    _finish(research, TailorStatus.DONE, None)
    db.commit()
    logger.info(
        "Company research %s finished: %d sources kept, dropped %s",
        research.id,
        len(clean.sources),
        clean.dropped or "nothing",
    )


def run_research_safely(db: Session, research: CompanyResearch, call: Research) -> None:
    run_safely(
        db,
        f"Company research {research.id}",
        lambda: run_research(db, research, call, date.today()),
        lambda: _finish(
            research, TailorStatus.FAILED, "Something went wrong while researching. Try again."
        ),
    )
