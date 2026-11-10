"""Briefs on demand for Companies leads.

A Companies run saves and stages its leads with the judge's one-line verdict and no brief, so a
run can find dozens of leads for a few cents. The user asks for a brief on a lead they care about:
it's queued here, written by the Scout worker with the same Sonnet call Pipeline uses, and
its measured cost is recorded. Each user may ask for `smartsearch_daily_briefs` a UTC day
(app.app_config); a failed brief doesn't count.
"""

import logging
import uuid
from datetime import UTC, datetime, time, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    JobPosting,
    ScoutBrief,
    ScoutRun,
    TailorStatus,
    utcnow,
)
from app.scout.leads import lead_notes
from app.workers import TailorUnavailable

logger = logging.getLogger(__name__)

# A brief still running after this was left by a worker that stopped.
STALE_AFTER = timedelta(minutes=10)

# Lead fields a brief fills in. The lead keeps its own link, company and title.
_BRIEF_FIELDS = (
    "fit_summary",
    "what_they_want",
    "you_match",
    "you_lack",
    "requirements",
    "company_notes",
    "salary",
    "level",
    "employment_type",
    "offer_summary",
    "how_to_apply",
    "application_tips",
)


class BriefRefused(Exception):
    """A brief that can't be queued. The message is safe to show the user."""


class BriefLimitReached(BriefRefused):
    """The user has asked for every brief today allows."""


def briefs_today(db: Session, user_id: uuid.UUID, now: datetime) -> int:
    """Briefs asked for since midnight UTC, waiting, running or written. Failed ones don't
    count, so a brief that couldn't be written can be asked for again."""
    midnight = datetime.combine(now.astimezone(UTC).date(), time.min, tzinfo=UTC)
    return (
        db.scalar(
            select(func.count(ScoutBrief.id)).where(
                ScoutBrief.user_id == user_id,
                ScoutBrief.created_at >= midnight,
                ScoutBrief.status != TailorStatus.FAILED.value,
            )
        )
        or 0
    )


def queue_brief(
    db: Session, run: ScoutRun, lead_id: str, daily_briefs: int | None, now: datetime
) -> dict[str, Any]:
    """Queue a brief for one lead of a run, locked by the caller. Returns the run's lead as it
    is now. A lead with a brief, or one on the way, is returned as it is, so a second click
    can't charge twice. The caller commits."""
    leads = [dict(lead) for lead in run.leads or []]
    lead = next((item for item in leads if item.get("id") == lead_id), None)
    if lead is None:
        raise BriefRefused("Lead not found")
    if lead.get("brief_status") not in ("none", "failed"):
        return lead
    if daily_briefs is not None and briefs_today(db, run.user_id, now) >= daily_briefs:
        raise BriefLimitReached(
            f"You've used all {daily_briefs} Scout briefs for today. They reset at midnight UTC."
        )
    db.add(
        ScoutBrief(
            user_id=run.user_id,
            run_id=run.id,
            lead_id=lead_id,
            status=TailorStatus.QUEUED.value,
        )
    )
    lead["brief_status"] = TailorStatus.QUEUED.value
    lead["brief_error"] = ""
    # A new list, so the JSON column sees the change.
    run.leads = leads
    logger.info("Queued a brief for lead %s of Scout run %s", lead_id, run.id)
    return lead


def claim_next_brief(db: Session) -> ScoutBrief | None:
    """Take the oldest queued brief and mark it running."""
    brief = db.scalar(
        select(ScoutBrief)
        .where(ScoutBrief.status == TailorStatus.QUEUED.value)
        .order_by(ScoutBrief.created_at, ScoutBrief.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if brief is None:
        db.rollback()
        return None
    brief.status = TailorStatus.RUNNING.value
    brief.started_at = utcnow()
    _set_lead(db, brief, {"brief_status": TailorStatus.RUNNING.value})
    db.commit()
    return brief


def _set_lead(db: Session, brief: ScoutBrief, changes: dict[str, Any]) -> dict[str, Any] | None:
    """Change the brief's lead in its run, with the run locked so a state change at the same
    moment isn't lost. Returns the lead as changed, or None when it's gone."""
    run = db.scalar(select(ScoutRun).where(ScoutRun.id == brief.run_id).with_for_update())
    if run is None:
        return None
    leads = [dict(lead) for lead in run.leads or []]
    lead = next((item for item in leads if item.get("id") == brief.lead_id), None)
    if lead is None:
        return None
    lead.update(changes)
    run.leads = leads
    return lead


def _page(lead: dict[str, Any]) -> dict[str, Any]:
    """The lead's posting, in the shape tailor reads pages."""
    return {
        "url": lead.get("url") or "",
        "ok": True,
        "source": "companies",
        "title": lead.get("title") or "",
        "company": lead.get("company") or "",
        "location": lead.get("location") or "",
        "work_mode": lead.get("work_mode") or "",
        "employment_type": lead.get("employment_type") or "",
        "salary": lead.get("salary") or "",
        "posted_at": lead.get("posted") or "",
        "text": lead.get("posting_text") or "",
    }


def _judgment(lead: dict[str, Any]) -> dict[str, Any]:
    fit = lead.get("fit") if lead.get("fit") in ("strong", "good", "stretch") else "stretch"
    return {
        "keep": True,
        "fit": fit,
        "work_match": lead.get("fit_summary") or "",
        "location_ok": True,
        "agency": lead.get("agency") or "",
        "reason": "",
    }


def _fail(db: Session, brief: ScoutBrief, message: str) -> None:
    brief.status = TailorStatus.FAILED.value
    brief.error = message[:500]
    brief.finished_at = utcnow()
    _set_lead(db, brief, {"brief_status": TailorStatus.FAILED.value, "brief_error": message[:300]})


def write_one_brief(
    db: Session,
    brief: ScoutBrief,
    ask: Any,
    payload: dict[str, Any],
) -> None:
    """Write one claimed brief with tailor's /pipeline/brief, store it on the lead, refresh the
    staged posting's notes, and record what it cost. Commits."""
    run = db.get(ScoutRun, brief.run_id)
    lead = next(
        (item for item in (run.leads if run else []) or [] if item.get("id") == brief.lead_id),
        None,
    )
    if run is None or lead is None:
        _fail(db, brief, "The lead is gone.")
        db.commit()
        return
    db.commit()
    try:
        answer = ask({"brief": payload, "posting": _page(lead), "judgment": _judgment(lead)})
    except TailorUnavailable as exc:
        if exc.spent:
            # A failure doesn't count against the day, but what it measured stays on record.
            brief.cost_usd = float(exc.spent.get("cost_usd") or 0.0)
            brief.usage = dict(exc.spent.get("usage") or {})
        _fail(db, brief, f"{exc} It doesn't count against today's briefs.")
        db.commit()
        return
    written = answer.get("lead") or {}
    changes: dict[str, Any] = {
        name: written[name] for name in _BRIEF_FIELDS if written.get(name) not in (None, "", [])
    }
    changes.update(brief_status=TailorStatus.DONE.value, brief_error="")
    updated = _set_lead(db, brief, changes)
    brief.cost_usd = float(answer.get("cost_usd") or 0.0)
    brief.usage = dict(answer.get("usage") or {})
    brief.request_costs = list(answer.get("request_costs") or [])
    brief.status = TailorStatus.DONE.value
    brief.finished_at = utcnow()
    if updated is not None and updated.get("posting_id"):
        posting = db.get(JobPosting, uuid.UUID(updated["posting_id"]))
        if posting is not None and posting.staged_at is not None:
            # The posting's notes are what Apply shows, so they carry the brief now.
            posting.staged_notes = lead_notes(updated)
    db.commit()
    logger.info(
        "Wrote the brief for lead %s of run %s: $%.4f",
        brief.lead_id,
        brief.run_id,
        brief.cost_usd,
    )


def fail_brief(db: Session, brief: ScoutBrief) -> None:
    """Mark a brief failed after a bug, so it doesn't count against the day."""
    _fail(
        db,
        brief,
        "Something went wrong while writing the brief. It doesn't count against today's briefs.",
    )


def fail_stale_briefs(db: Session, now: datetime) -> int:
    """Fail briefs left running by a worker that stopped, so they don't count against the day."""
    running = db.scalars(
        select(ScoutBrief).where(ScoutBrief.status == TailorStatus.RUNNING.value)
    ).all()
    failed = 0
    for brief in running:
        started = brief.started_at
        if started is not None and started.tzinfo is None:
            started = started.replace(tzinfo=now.tzinfo)
        if started is not None and started < now - STALE_AFTER:
            logger.warning("Scout brief %s was abandoned while running", brief.id)
            _fail(db, brief, "The brief stopped before it was written. It doesn't count.")
            failed += 1
    db.commit()
    return failed
