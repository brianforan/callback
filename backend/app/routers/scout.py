import logging
import uuid
from dataclasses import asdict
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, load_only

from app.app_config import daily_limit, get_value
from app.deps import CurrentProfile, CurrentUser, Db
from app.models import (
    Application,
    Profile,
    ScoutRun,
    ScoutSettings,
    TailorStatus,
    User,
)
from app.postings.parser import TECHNOLOGY_NAMES
from app.profiles import profile_for_work
from app.schemas import (
    LeadStateUpdate,
    ScoutAllowanceOut,
    ScoutEngineHistory,
    ScoutLeadApplication,
    ScoutLeadItem,
    ScoutLeadOut,
    ScoutLimitOut,
    ScoutLimitRangeOut,
    ScoutOut,
    ScoutReportOut,
    ScoutRunOut,
    ScoutRunStart,
    ScoutSettingsIn,
    ScoutSettingsOut,
    ScoutStep,
)
from app.scout.briefs import BriefLimitReached, BriefRefused, briefs_today, queue_brief
from app.scout.leads import (
    LeadItem,
    LeadState,
    list_leads,
    set_lead_state,
)
from app.scout.leads import track_lead as track_lead_posting
from app.scout.limits import (
    ENGINE,
    LIMITS,
    MAX_DESCRIPTION_LENGTH,
    PAST_ENGINES,
    POSTED_WITHIN_DAYS,
    AgencyMode,
    FitMode,
    Plan,
    Relocation,
    default_limits,
    fit_limits,
    limit_errors,
)
from app.scout.match import clean_levels, effective_must_have, effective_rule_out
from app.scout.report import build_report

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/scout", tags=["scout"])


_ACTIVE = (TailorStatus.QUEUED.value, TailorStatus.RUNNING.value)
_RUNS_SHOWN = 20


def _midnight(now: datetime) -> datetime:
    return datetime.combine(now.astimezone(UTC).date(), time.min, tzinfo=UTC)


def runs_today(db: Session, user: User, now: datetime) -> int:
    """Runs started since midnight UTC, on any profile. Failed ones don't count: a run that
    couldn't finish can be started again."""
    return (
        db.scalar(
            select(func.count(ScoutRun.id)).where(
                ScoutRun.user_id == user.id,
                ScoutRun.created_at >= _midnight(now),
                ScoutRun.status != TailorStatus.FAILED.value,
            )
        )
        or 0
    )


def _history(db: Session, user: User) -> dict[str, ScoutEngineHistory]:
    """Runs, leads and measured cost of the user's finished runs, by engine."""
    runs = db.scalars(
        select(ScoutRun)
        .where(
            ScoutRun.user_id == user.id,
            ScoutRun.status == TailorStatus.DONE.value,
        )
        .options(
            load_only(
                ScoutRun.settings,
                ScoutRun.leads,
                ScoutRun.pages,
                ScoutRun.cost_usd,
            )
        )
    ).all()
    totals: dict[str, ScoutEngineHistory] = {}
    for run in runs:
        engine = (run.settings or {}).get("engine") or "agent"
        seen = totals.get(engine) or ScoutEngineHistory(runs=0, leads=0, pages=0, cost_usd=0.0)
        totals[engine] = ScoutEngineHistory(
            runs=seen.runs + 1,
            leads=seen.leads + len(run.leads or []),
            pages=seen.pages + (run.pages or 0),
            cost_usd=round(seen.cost_usd + (run.cost_usd or 0.0), 4),
        )
    return totals


def _settings_out(saved: ScoutSettings | None) -> ScoutSettingsOut:
    """The saved settings, or the defaults for someone who hasn't saved any."""
    if saved is None:
        return ScoutSettingsOut(must_have=[], rule_out=[], limits=default_limits(), updated_at=None)
    return ScoutSettingsOut(
        description=saved.description,
        title_families=saved.title_families,
        work_modes=saved.work_modes,
        location=saved.location,
        posted_within_days=saved.posted_within_days,
        fit_mode=FitMode(saved.fit_mode),
        engine=ENGINE,
        agency_mode=AgencyMode(saved.agency_mode),
        relocation=Relocation(saved.relocation),
        # What a run will use: the saved lists, or the description's when never saved.
        must_have=effective_must_have(saved.must_have, saved.description),
        rule_out=effective_rule_out(saved.rule_out, saved.description),
        levels=clean_levels(saved.levels or []),
        # Settings saved under credits hold limits worked out from the cap; they carry on.
        limits=fit_limits(saved.limits or {}),
        updated_at=saved.updated_at,
    )


def _load(db: Session, profile: Profile) -> ScoutSettings | None:
    return db.scalar(select(ScoutSettings).where(ScoutSettings.profile_id == profile.id))


@router.get("", response_model=ScoutOut)
def get_scout(user: CurrentUser, profile: CurrentProfile, db: Db) -> ScoutOut:
    """The plan and daily caps are the account's. The settings are the active profile's."""
    now = datetime.now(UTC)
    return ScoutOut(
        plan=Plan(user.plan),
        allowance=ScoutAllowanceOut(
            runs_today=runs_today(db, user, now),
            daily_runs=daily_limit(db, user, "smartsearch_daily_runs"),
            briefs_today=briefs_today(db, user.id, now),
            daily_briefs=daily_limit(db, user, "smartsearch_daily_briefs"),
            run_max_usd=float(get_value(db, "smartsearch_run_max_usd")),
            resets_at=_midnight(now) + timedelta(days=1),
        ),
        limits=[
            ScoutLimitOut(
                key=spec.key,
                label=spec.label,
                help=spec.help,
                unit=spec.unit,
                minimum=spec.minimum,
                maximum=spec.maximum,
                default=spec.default,
                by_engine={ENGINE: ScoutLimitRangeOut(**asdict(spec.range_for(ENGINE)))},
            )
            for spec in LIMITS
        ],
        posted_within_options=list(POSTED_WITHIN_DAYS),
        description_max_length=MAX_DESCRIPTION_LENGTH,
        technology_names=list(TECHNOLOGY_NAMES),
        settings=_settings_out(_load(db, profile)),
        history=_history(db, user),
    )


@router.put("/settings", response_model=ScoutSettingsOut)
def save_settings(
    payload: ScoutSettingsIn, user: CurrentUser, profile: CurrentProfile, db: Db
) -> ScoutSettingsOut:
    if user.plan != Plan.PREMIUM:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Scout is a premium feature")
    if payload.limits is not None and (errors := limit_errors(payload.limits)):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "; ".join(errors))
    saved = _load(db, profile)
    if saved is None:
        saved = ScoutSettings(user_id=user.id, profile_id=profile.id)
        db.add(saved)
    saved.description = payload.description
    saved.title_families = payload.title_families
    saved.work_modes = [mode.value for mode in payload.work_modes]
    saved.location = payload.location
    saved.posted_within_days = payload.posted_within_days
    saved.fit_mode = payload.fit_mode.value
    # One engine: whatever an older page sends, runs use it.
    saved.engine = ENGINE
    saved.agency_mode = payload.agency_mode.value
    saved.relocation = payload.relocation.value
    # Saving makes the lists explicit, so a later edit to the description doesn't change them.
    # An older page that doesn't send them keeps what was saved.
    if payload.must_have is not None:
        saved.must_have = payload.must_have
    if payload.rule_out is not None:
        saved.rule_out = payload.rule_out
    saved.levels = [level.value for level in payload.levels]
    if payload.limits is not None:
        saved.limits = payload.limits
    elif not saved.limits:
        saved.limits = default_limits()
    db.commit()
    logger.info("Saved Scout settings for profile %s", profile.id)
    return _settings_out(saved)


def _run_out(run: ScoutRun) -> ScoutRunOut:
    settings = run.settings or {}
    target = (settings.get("limits") or {}).get("target_leads")
    return ScoutRunOut(
        id=run.id,
        status=run.status,
        label=run.label,
        description=str(settings.get("description", "")),
        # Runs from before the engine choice all used the agent.
        engine=engine if (engine := settings.get("engine")) in PAST_ENGINES else "agent",
        target_leads=target if isinstance(target, int) else None,
        budget_usd=budget
        if isinstance(budget := settings.get("budget_usd"), int | float)
        else None,
        cost_usd=run.cost_usd,
        searches=run.searches,
        pages=run.pages,
        stop_code=run.stop_code,
        stop_message=run.stop_message,
        leads=[ScoutLeadOut.model_validate(lead) for lead in run.leads or []],
        steps=[ScoutStep.model_validate(step) for step in run.steps or []],
        created_at=run.created_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
    )


def run_query(user: User, run_id: uuid.UUID, for_update: bool = False) -> Select:
    """One of the user's runs. for_update locks its row until the request commits.

    The lead endpoints copy a run's whole leads list, change one lead and write the list back,
    so two changes at once would otherwise lose one of them.
    """
    query = select(ScoutRun).where(ScoutRun.id == run_id, ScoutRun.user_id == user.id)
    return query.with_for_update() if for_update else query


def _get_run(db: Session, user: User, run_id: uuid.UUID, for_update: bool = False) -> ScoutRun:
    run = db.scalar(run_query(user, run_id, for_update))
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")
    return run


@router.post("/runs", response_model=ScoutRunOut, status_code=status.HTTP_201_CREATED)
def start_run(
    user: CurrentUser,
    profile: CurrentProfile,
    db: Db,
    payload: ScoutRunStart | None = None,
) -> ScoutRunOut:
    """Queue a run with the profile's saved settings and today's dollar ceiling.

    The body is optional: it can name the run, and its leads are filed under that label.

    The daily cap and the one-run-at-a-time rule are the account's, across every profile.
    """
    if user.plan != Plan.PREMIUM:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Scout is a premium feature")
    saved = _load(db, profile)
    if saved is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Save your settings before running")
    settings = _settings_out(saved)
    if not settings.description:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Describe the work you're looking for, then save"
        )
    active = db.scalar(
        select(ScoutRun.id).where(ScoutRun.user_id == user.id, ScoutRun.status.in_(_ACTIVE))
    )
    if active is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "A run is already going. Wait for it to end")
    limit = daily_limit(db, user, "smartsearch_daily_runs")
    if limit is not None and runs_today(db, user, datetime.now(UTC)) >= limit:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"You've used all {limit} Scout runs for today. They reset at midnight UTC.",
        )
    run = ScoutRun(
        user_id=user.id,
        profile_id=profile.id,
        status=TailorStatus.QUEUED.value,
        label=payload.label if payload else None,
        settings={
            **settings.model_dump(
                mode="json",
                include={
                    "description",
                    "title_families",
                    "work_modes",
                    "location",
                    "posted_within_days",
                    "fit_mode",
                    "engine",
                    "agency_mode",
                    "relocation",
                    "must_have",
                    "rule_out",
                    "levels",
                    "limits",
                },
            ),
            # The most this run may spend, fixed now so a later change doesn't alter it.
            "budget_usd": float(get_value(db, "smartsearch_run_max_usd")),
        },
        usage={},
        leads=[],
        steps=[],
    )
    db.add(run)
    try:
        db.commit()
    except IntegrityError as exc:
        # Another request queued a run between the check above and this insert.
        db.rollback()
        logger.info("Refused a second active Scout run for user %s: %s", user.id, exc.orig)
        raise HTTPException(
            status.HTTP_409_CONFLICT, "A run is already going. Wait for it to end"
        ) from exc
    logger.info("Queued Scout run %s for user %s", run.id, user.id)
    return _run_out(run)


@router.get("/runs", response_model=list[ScoutRunOut])
def list_runs(user: CurrentUser, profile: CurrentProfile, db: Db) -> list[ScoutRunOut]:
    runs = db.scalars(
        select(ScoutRun)
        .where(ScoutRun.user_id == user.id, ScoutRun.profile_id == profile.id)
        .order_by(ScoutRun.created_at.desc(), ScoutRun.id.desc())
        .limit(_RUNS_SHOWN)
    ).all()
    return [_run_out(run) for run in runs]


@router.get("/runs/{run_id}", response_model=ScoutRunOut)
def get_run(run_id: uuid.UUID, user: CurrentUser, db: Db) -> ScoutRunOut:
    return _run_out(_get_run(db, user, run_id))


@router.get("/runs/{run_id}/report", response_model=ScoutReportOut)
def get_report(run_id: uuid.UUID, user: CurrentUser, db: Db) -> ScoutReportOut:
    run = _get_run(db, user, run_id)
    report = asdict(build_report(run))
    report["errors"] = [{"name": name, "count": count} for name, count in report["errors"]]
    report["lead_hosts"] = [{"name": name, "count": count} for name, count in report["lead_hosts"]]
    return ScoutReportOut(run=_run_out(run), **report)


@router.post("/runs/{run_id}/leads/{lead_id}/track", response_model=ScoutRunOut)
def track_lead(run_id: uuid.UUID, lead_id: str, user: CurrentUser, db: Db) -> ScoutRunOut:
    """Track a lead from a run made before leads tracked themselves. Runs now do it on their own."""
    run = _get_run(db, user, run_id, for_update=True)
    leads = [dict(lead) for lead in run.leads or []]
    lead = next((item for item in leads if item.get("id") == lead_id), None)
    if lead is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Lead not found")
    if lead.get("posting_id") is None:
        run_profile = profile_for_work(db, user, run.profile_id)
        posting = track_lead_posting(db, user, run_profile, lead, date.today())
        lead["posting_id"] = str(posting.id)
        # A new list, so the JSON column sees the change.
        run.leads = leads
        db.commit()
        logger.info("Tracked Scout lead %s from run %s", lead_id, run.id)
    return _run_out(run)


def _contact_summary(application: Application) -> dict[str, Any]:
    """How many contacts were logged, the latest one, and the soonest open next step."""
    logged = application.communications
    steps = sorted(
        (c for c in logged if c.next_step and not c.next_step_done),
        key=lambda c: (c.next_step_due is None, c.next_step_due or date.max),
    )
    return {
        "communications": len(logged),
        "last_contact_on": max((c.occurred_on for c in logged), default=None),
        "next_step": steps[0].next_step if steps else "",
        "next_step_due": steps[0].next_step_due if steps else None,
    }


def _lead_item(item: LeadItem) -> ScoutLeadItem:
    application = item.application
    return ScoutLeadItem(
        **ScoutLeadOut.model_validate(item.lead).model_dump(exclude={"state", "decline_reason"}),
        run_id=item.run_id,
        found_at=item.found_at,
        label=item.label,
        state=item.state,
        decline_reason=str(item.lead.get("decline_reason") or ""),
        application=(
            ScoutLeadApplication(
                id=application.id,
                status=application.status,
                applied_on=application.applied_on,
                resume_label=application.resume.label if application.resume else None,
                **_contact_summary(application),
            )
            if application
            else None
        ),
    )


@router.get("/leads", response_model=list[ScoutLeadItem])
def get_leads(user: CurrentUser, profile: CurrentProfile, db: Db) -> list[ScoutLeadItem]:
    """Every lead across the profile's runs, newest first. The page splits them in two."""
    return [_lead_item(item) for item in list_leads(db, user, profile.id)]


@router.post("/runs/{run_id}/leads/{lead_id}/brief", response_model=ScoutLeadItem)
def request_brief(run_id: uuid.UUID, lead_id: str, user: CurrentUser, db: Db) -> ScoutLeadItem:
    """Queue the brief for a Companies lead. Asking again while one is on the way, or after it's
    written, changes nothing and doesn't count against today's briefs."""
    if user.plan != Plan.PREMIUM:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Scout is a premium feature")
    run = _get_run(db, user, run_id, for_update=True)
    limit = daily_limit(db, user, "smartsearch_daily_briefs")
    try:
        lead = queue_brief(db, run, lead_id, limit, datetime.now(UTC))
    except BriefLimitReached as exc:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(exc)) from exc
    except BriefRefused as exc:
        found = any(item.get("id") == lead_id for item in run.leads or [])
        code = status.HTTP_409_CONFLICT if found else status.HTTP_404_NOT_FOUND
        raise HTTPException(code, str(exc)) from exc
    db.commit()
    item = next(
        (
            entry
            for entry in list_leads(db, user, run.profile_id)
            if entry.run_id == run.id and entry.lead.get("id") == lead_id
        ),
        None,
    )
    return _lead_item(
        item
        or LeadItem(
            lead=lead,
            run_id=run.id,
            found_at=run.created_at,
            label=run.label,
            state=lead.get("state") or LeadState.NEW.value,
            application=None,
        )
    )


@router.post("/runs/{run_id}/leads/{lead_id}/state", response_model=ScoutLeadItem)
def update_lead_state(
    run_id: uuid.UUID, lead_id: str, payload: LeadStateUpdate, user: CurrentUser, db: Db
) -> ScoutLeadItem:
    run = _get_run(db, user, run_id, for_update=True)
    leads = [dict(lead) for lead in run.leads or []]
    lead = next((item for item in leads if item.get("id") == lead_id), None)
    if lead is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Lead not found")
    run_profile = profile_for_work(db, user, run.profile_id)
    try:
        set_lead_state(
            db, user, run_profile, lead, LeadState(payload.state), payload.reason, date.today()
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    # A new list, so the JSON column sees the change.
    run.leads = leads
    db.commit()
    logger.info("Scout lead %s from run %s is now %s", lead_id, run.id, payload.state)
    item = next(
        (
            entry
            # The run's own profile, which is None for a run whose profile was deleted.
            for entry in list_leads(db, user, run.profile_id)
            if entry.run_id == run.id and entry.lead.get("id") == lead_id
        ),
        None,
    )
    if item is None:
        # The list shows a job once, from its newest run, so an older run's copy isn't in it.
        item = LeadItem(
            lead=lead,
            run_id=run.id,
            found_at=run.created_at,
            label=run.label,
            state=lead.get("state") or LeadState.NEW.value,
            application=None,
        )
    return _lead_item(item)
