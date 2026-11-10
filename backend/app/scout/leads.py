"""Scout leads: turning one into a posting ready to apply to, and where each one stands.

A lead becomes a tracked posting built from the posting's own text, so Callback's parser reads
its salary, level and skills and its matcher scores it like any other job. It's then marked
ready to apply with the user's best-fitting baseline, with the agent's brief as its notes. A
posting the user already had keeps whatever they set on it.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, load_only, selectinload

from app.companies.lists import note_dismissed
from app.models import (
    Application,
    HiddenKind,
    HiddenListing,
    JobPosting,
    Profile,
    Resume,
    ScoutRun,
    TailorStatus,
    User,
    utcnow,
)
from app.postings.parser import canonical_url, company_key
from app.postings.store import create_posting, find_by_url
from app.resumes.matching import facts_from_posting
from app.resumes.variants import load_baselines, pick_baseline
from app.scout.limits import SCOUT_SOURCE, lead_posting_text

logger = logging.getLogger(__name__)

_MAX_NOTES = 20_000


def _section(title: str, items: list[str] | None) -> str | None:
    items = [item for item in items or [] if item.strip()]
    if not items:
        return None
    return f"{title}:\n" + "\n".join(f"- {item}" for item in items)


def agency_note(lead: dict[str, Any]) -> str | None:
    """What the user should know about an agency behind a lead, or None when there's none."""
    agency = str(lead.get("agency") or "").strip()
    if not agency:
        return None
    if lead.get("agency_url"):
        return (
            f"Found through a listing by the agency {agency} ({lead['agency_url']}). This is the "
            "employer's own posting, so apply here directly rather than through the agency."
        )
    return (
        f"Posted by an agency: {agency}. Agency jobs often pay less than hiring directly, and "
        "applying through the agency can stop the company hiring you directly later. Check "
        "who the employer is and whether it posts the job itself."
    )


def lead_notes(lead: dict[str, Any]) -> str:
    """The agent's brief as the posting's notes, in the order an application needs it."""
    parts = [
        "Found by Scout.",
        f"Fit: {lead['fit']}" if lead.get("fit") else None,
        agency_note(lead),
        f"Why it fits: {lead['fit_summary']}" if lead.get("fit_summary") else None,
        f"Pay: {lead['salary']}" if lead.get("salary") else None,
        f"The offer: {lead['offer_summary']}" if lead.get("offer_summary") else None,
        _section("Applying takes", lead.get("how_to_apply")),
        _section("Lead with", lead.get("application_tips")),
        _section("They want", lead.get("what_they_want")),
        _section("You match", lead.get("you_match")),
        _section("You lack", lead.get("you_lack")),
    ]
    return "\n\n".join(part for part in parts if part)[:_MAX_NOTES]


def _stage(db: Session, profile: Profile, posting: JobPosting, notes: str, today: date) -> bool:
    """Mark a new posting ready to apply with the profile's best baseline. False for none."""
    resumes = db.scalars(select(Resume).where(Resume.profile_id == profile.id)).all()
    baselines = load_baselines(resumes, today)
    pick = pick_baseline(baselines, facts_from_posting(posting)) if baselines else None
    if pick is None:
        return False
    posting.staged_at = utcnow()
    posting.staged_resume_id = pick.resume_id
    posting.staged_notes = notes
    return True


def track_lead(
    db: Session, user: User, profile: Profile, lead: dict[str, Any], today: date
) -> JobPosting:
    """The posting for a lead: the one that already has its URL, or a new one, staged.

    A new one is saved under the run's profile and staged with that profile's baseline.
    """
    posting = find_by_url(db, user, lead.get("url"))
    if posting is not None:
        return posting
    posting = create_posting(
        db,
        user,
        profile_id=profile.id,
        company=str(lead.get("company", ""))[:200],
        title=str(lead.get("title", ""))[:200],
        seen_on=today,
        url=lead.get("url"),
        location=(str(lead.get("location") or "")[:200] or None),
        source=SCOUT_SOURCE,
        raw_text=lead_posting_text(lead),
    )
    db.flush()
    if not _stage(db, profile, posting, lead_notes(lead), today):
        logger.info("Scout lead %s tracked without a baseline to stage", lead.get("id"))
    return posting


class LeadState(StrEnum):
    """Where a lead stands in Scout. Applied isn't stored: it's a lead with an application."""

    NEW = "new"
    LATER = "later"
    DISMISSED = "dismissed"
    # The posting has closed. Put away like a dismissed lead, but it isn't a judgment on the job,
    # so later runs aren't told to skip jobs like it.
    EXPIRED = "expired"


_PUT_AWAY = (LeadState.DISMISSED.value, LeadState.EXPIRED.value)


@dataclass(frozen=True)
class LeadItem:
    """One lead across every run, with the run it came from and how far the user has taken it."""

    lead: dict[str, Any]
    run_id: uuid.UUID
    found_at: datetime
    # The label of the run it's listed under, or None.
    label: str | None
    state: str
    application: Application | None


def _hide(db: Session, user: User, lead: dict[str, Any]) -> None:
    """A hidden-job rule for the lead, so future runs and Job listings both skip it."""
    url = canonical_url(lead.get("url"))
    title = str(lead.get("title") or "")[:200]
    if url is None or not title:
        return
    exists = db.scalar(
        select(HiddenListing.id).where(
            HiddenListing.user_id == user.id,
            HiddenListing.kind == HiddenKind.LISTING.value,
            HiddenListing.key == url,
        )
    )
    if exists is None:
        company = str(lead.get("company") or "")[:200]
        db.add(
            HiddenListing(
                user_id=user.id,
                kind=HiddenKind.LISTING.value,
                key=url,
                company=company,
                company_key=company_key(company),
                title=title,
                url=url,
            )
        )


def _unhide(db: Session, user: User, lead: dict[str, Any]) -> None:
    url = canonical_url(lead.get("url"))
    if url is None:
        return
    db.execute(
        delete(HiddenListing).where(
            HiddenListing.user_id == user.id,
            HiddenListing.kind == HiddenKind.LISTING.value,
            HiddenListing.key == url,
        )
    )


def set_lead_state(
    db: Session,
    user: User,
    profile: Profile,
    lead: dict[str, Any],
    state: LeadState,
    reason: str,
    today: date,
) -> None:
    """Move a lead between new, later, dismissed and expired. The caller saves the run's leads.

    Dismissing or marking expired hides the job and removes the posting Scout made for it,
    unless it has an application or came from somewhere else. Restoring undoes both. Only a
    dismissed lead keeps a reason, since only that one tells later runs what to skip.
    """
    posting = db.get(JobPosting, uuid.UUID(lead["posting_id"])) if lead.get("posting_id") else None
    if posting is not None and posting.user_id != user.id:
        posting = None
    was_dismissed = lead.get("state") == LeadState.DISMISSED.value
    if state.value in _PUT_AWAY:
        if posting is not None and posting.applications:
            what = "dismissed" if state == LeadState.DISMISSED else "marked expired"
            raise ValueError(f"You've applied to this job, so it can't be {what}")
        _hide(db, user, lead)
        if posting is not None and posting.source == SCOUT_SOURCE:
            db.delete(posting)
            lead["posting_id"] = None
        lead["decline_reason"] = reason.strip()[:300] if state == LeadState.DISMISSED else ""
    else:
        if lead.get("state") in _PUT_AWAY:
            _unhide(db, user, lead)
            db.flush()
            lead["posting_id"] = str(track_lead(db, user, profile, lead, today).id)
        lead["decline_reason"] = ""
    lead["state"] = state.value
    now_dismissed = state == LeadState.DISMISSED
    if now_dismissed != was_dismissed:
        # The company's count on the profile's list, which Companies runs use to skip it.
        note_dismissed(db, profile.id, lead.get("url"), 1 if now_dismissed else -1)


def list_leads(db: Session, user: User, profile_id: uuid.UUID | None) -> list[LeadItem]:
    """Every lead one profile's runs found, newest first, once per posting or link.

    None lists the runs whose profile was deleted.
    """
    runs = db.scalars(
        select(ScoutRun)
        .where(
            ScoutRun.user_id == user.id,
            ScoutRun.profile_id == profile_id,
            ScoutRun.status == TailorStatus.DONE.value,
        )
        .order_by(ScoutRun.created_at.desc(), ScoutRun.id.desc())
        # Every run the profile made, so only its leads, not its logs and costs.
        .options(
            load_only(
                ScoutRun.id,
                ScoutRun.created_at,
                ScoutRun.label,
                ScoutRun.leads,
            )
        )
    ).all()
    posting_ids = {
        uuid.UUID(lead["posting_id"])
        for run in runs
        for lead in run.leads or []
        if lead.get("posting_id")
    }
    # A lead's posting can be gone: another run's copy of the lead was dismissed, or the user
    # deleted it in Job listings. Such a lead is listed without it, so nothing asks for it.
    existing = (
        set(
            db.scalars(
                select(JobPosting.id).where(
                    JobPosting.user_id == user.id, JobPosting.id.in_(posting_ids)
                )
            ).all()
        )
        if posting_ids
        else set()
    )
    applications: dict[uuid.UUID, Application] = {}
    if posting_ids:
        for application in db.scalars(
            select(Application)
            .where(Application.user_id == user.id, Application.posting_id.in_(posting_ids))
            .options(selectinload(Application.resume), selectinload(Application.communications))
            .order_by(Application.applied_on.desc(), Application.created_at.desc())
        ).all():
            applications.setdefault(application.posting_id, application)
    seen: set[str] = set()
    items = []
    for run in runs:
        for lead in run.leads or []:
            posting_id = uuid.UUID(lead["posting_id"]) if lead.get("posting_id") else None
            if posting_id is not None and posting_id not in existing:
                lead = {**lead, "posting_id": None}
                posting_id = None
            # The same job by either its posting or its link, since a copy whose posting was
            # removed keeps only the link.
            keys = {
                key for key in (lead.get("posting_id"), canonical_url(lead.get("url"))) if key
            } or {lead.get("id")}
            if keys & seen:
                continue
            seen |= keys
            items.append(
                LeadItem(
                    lead=lead,
                    run_id=run.id,
                    found_at=run.created_at,
                    label=run.label,
                    state=lead.get("state") or LeadState.NEW.value,
                    application=applications.get(posting_id) if posting_id else None,
                )
            )
    return items


def declined_jobs(db: Session, user_id: uuid.UUID, limit: int) -> list[dict[str, str]]:
    """Jobs the user turned down with a reason, newest first, so future runs skip their like."""
    runs = db.scalars(
        select(ScoutRun)
        .where(ScoutRun.user_id == user_id)
        .order_by(ScoutRun.created_at.desc())
        .options(load_only(ScoutRun.id, ScoutRun.created_at, ScoutRun.leads))
    ).all()
    declined = [
        {
            "company": str(lead.get("company", ""))[:200],
            "title": str(lead.get("title", ""))[:200],
            "reason": str(lead.get("decline_reason", ""))[:300],
        }
        for run in runs
        for lead in run.leads or []
        if lead.get("state") == LeadState.DISMISSED.value and lead.get("decline_reason")
    ]
    return declined[:limit]


# The settings that decide what counts as a fit. Pages passed on under other settings may fit now.
_JUDGED_BY = ("description", "fit_mode", "title_families", "work_modes", "location")


def passed_pages(db: Session, run: ScoutRun, limit: int) -> list[str]:
    """Pages earlier runs opened and didn't keep, under the same idea of a fit, newest first.

    A page that became a lead or backed one isn't included, and neither is one from a run with
    a different description, fit setting, titles, work modes or location, since what didn't fit
    then may fit now.
    """
    settings = run.settings or {}
    judged = {key: settings.get(key) for key in _JUDGED_BY}
    earlier = db.scalars(
        select(ScoutRun)
        .where(
            ScoutRun.user_id == run.user_id,
            ScoutRun.id != run.id,
            ScoutRun.status == TailorStatus.DONE.value,
        )
        .order_by(ScoutRun.created_at.desc())
        .options(
            load_only(
                ScoutRun.id,
                ScoutRun.created_at,
                ScoutRun.settings,
                ScoutRun.leads,
                ScoutRun.steps,
            )
        )
    ).all()
    pages: dict[str, str] = {}
    for other in earlier:
        other_settings = other.settings or {}
        if any(other_settings.get(key) != value for key, value in judged.items()):
            continue
        kept = set()
        for lead in other.leads or []:
            kept.add(canonical_url(lead.get("url")))
            kept.update(canonical_url(source) for source in lead.get("sources") or [])
        for step in other.steps or []:
            url = (
                canonical_url(str(step.get("detail", ""))) if step.get("kind") == "fetch" else None
            )
            if url and url not in kept and url not in pages:
                pages[url] = str(step["detail"])
    return list(pages.values())[:limit]
