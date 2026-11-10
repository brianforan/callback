"""Works through queued rejection analyses, one at a time, from the tailor worker's loop.

For each: read the rejected application's posting, the resume sent (or the profile's best
baseline when none was) and the fact sheet; grade the posting's named requirements in code; then
send it all to the tailor service for the written report. The graded part is kept even when the
model call fails, so a report always has something to show.
"""

import logging
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, aliased, selectinload

from app.models import (
    Application,
    FactSheet,
    JobPosting,
    RejectAnalysis,
    Resume,
    TailorStatus,
    User,
    utcnow,
)
from app.postings.parser import Vocabulary
from app.profiles import profile_for_work
from app.research.analysis import (
    NO_TEXT_NOTE,
    furthest_stage,
    midnight_utc,
    other_backers,
    technology_gaps,
)
from app.research.context import application_company, model_context
from app.research.snapshots import newest_snapshot
from app.resumes.matching import build_profile, facts_from_posting, score_job
from app.resumes.structure import latest_resume
from app.resumes.variants import load_baselines, pick_baseline
from app.schemas import RejectAnalysisServiceResult
from app.similarity import tex_to_text
from app.tailoring.rules import fact_sheet_skills
from app.vocabulary import user_vocabulary
from app.workers import TailorUnavailable, post_json, run_safely

logger = logging.getLogger(__name__)

# The tailor service refuses anything larger, so the limits are checked before sending.
_MAX_POSTING_TEXT = 60_000
_MAX_RESUME_TEXT = 20_000
_MAX_FACT_SHEET = 100_000
_MAX_CHECKLIST = 100
_MAX_DETAIL = 500
_MAX_LABEL = 120
_MAX_TITLE = 200
# Extra minutes a running analysis gets beyond its timeout before it counts as abandoned.
_STALE_MARGIN = timedelta(minutes=5)

AnalyzeRequest = Callable[[dict[str, Any]], RejectAnalysisServiceResult]


def http_analyzer(client: httpx.Client) -> AnalyzeRequest:
    def analyze(payload: dict[str, Any]) -> RejectAnalysisServiceResult:
        return post_json(client, "/reject-analysis", payload, RejectAnalysisServiceResult)

    return analyze


def claim_next_analysis(db: Session, now: datetime, daily_limit: int) -> RejectAnalysis | None:
    """Take the oldest queued analysis whose user is under today's limit, or has no limit
    (users.override_limits), and mark it running.

    Skip-locked, so two workers never take the same one. A user over the limit keeps theirs
    queued until midnight UTC, and other users' analyses go ahead meanwhile.
    """
    started = aliased(RejectAnalysis)
    used = (
        select(func.count(started.id))
        .where(started.user_id == RejectAnalysis.user_id, started.started_at >= midnight_utc(now))
        .correlate(RejectAnalysis)
        .scalar_subquery()
    )
    unlimited = (
        select(User.override_limits)
        .where(User.id == RejectAnalysis.user_id)
        .correlate(RejectAnalysis)
        .scalar_subquery()
    )
    analysis = db.scalar(
        select(RejectAnalysis)
        .where(
            RejectAnalysis.status == TailorStatus.QUEUED.value,
            or_(used < daily_limit, unlimited),
        )
        .order_by(RejectAnalysis.created_at, RejectAnalysis.id)
        .limit(1)
        .with_for_update(skip_locked=True, of=RejectAnalysis)
    )
    if analysis is None:
        db.rollback()
        return None
    analysis.status = TailorStatus.RUNNING.value
    analysis.started_at = now
    db.commit()
    return analysis


def _finish(analysis: RejectAnalysis, status: TailorStatus, note: str | None) -> None:
    analysis.status = status.value
    analysis.note = note
    analysis.finished_at = utcnow()


def fail_stale_analyses(db: Session, now: datetime, timeout_seconds: float) -> int:
    """Fail analyses left running by a worker that stopped, so they don't hang forever."""
    cutoff = now - timedelta(seconds=timeout_seconds) - _STALE_MARGIN
    stale = db.scalars(
        select(RejectAnalysis).where(
            RejectAnalysis.status == TailorStatus.RUNNING.value,
            RejectAnalysis.started_at < cutoff,
        )
    ).all()
    for analysis in stale:
        logger.warning("Rejection analysis %s was abandoned while running", analysis.id)
        _finish(analysis, TailorStatus.FAILED, "The analysis stopped before it finished.")
    db.commit()
    return len(stale)


@dataclass(frozen=True)
class _Prepared:
    """The graded part of a report, and the request for the written part (None to skip it)."""

    fields: dict[str, Any]
    payload: dict[str, Any] | None
    skip_note: str | None


def _resume_to_compare(
    application: Application,
    resumes: list[Resume],
    posting: JobPosting,
    today: date,
    vocabulary: Vocabulary,
) -> Resume | None:
    """The resume sent, else the baseline that fits the job best, else the newest resume."""
    sent = application.resume
    if sent is not None and sent.user_id == application.user_id:
        return sent
    baselines = load_baselines(resumes, today, vocabulary)
    pick = pick_baseline(baselines, facts_from_posting(posting, vocabulary)) if baselines else None
    if pick is not None:
        picked = next((resume for resume in resumes if resume.id == pick.resume_id), None)
        if picked is not None:
            return picked
    return latest_resume(resumes, [])


def _gather(db: Session, analysis: RejectAnalysis, today: date) -> _Prepared | str:
    """Everything one analysis needs, read now, or the reason it can't run."""
    application = db.scalar(
        select(Application)
        .where(
            Application.id == analysis.application_id,
            Application.user_id == analysis.user_id,
        )
        .options(
            selectinload(Application.events),
            selectinload(Application.resume),
            selectinload(Application.posting).selectinload(JobPosting.technologies),
        )
    )
    if application is None:
        return "The application was deleted."
    posting = application.posting
    if posting is None:
        return "This application has no saved posting to compare with."
    user = db.get(User, analysis.user_id)
    if user is None:
        return "The account was deleted."
    owner = profile_for_work(db, user, analysis.profile_id)
    resumes = list(db.scalars(select(Resume).where(Resume.profile_id == owner.id)).all())
    vocabulary = user_vocabulary(db, user.id)
    resume = _resume_to_compare(application, resumes, posting, today, vocabulary)
    if resume is None:
        return "Add a resume to this profile first, so there's something to compare with."

    facts = facts_from_posting(posting, vocabulary)
    # Scored on its own, as an ATS would see it, against a profile with no application history.
    pool = list({item.id: item for item in [*resumes, resume]}.values())
    profile = build_profile(pool, [], today, vocabulary)
    match = score_job(profile, facts, only=resume) if profile else None
    checklist = match.checklist if match else []
    sheet = db.scalar(select(FactSheet).where(FactSheet.profile_id == owner.id))
    fact_sheet = sheet.content if sheet else ""
    skills = fact_sheet_skills(fact_sheet)
    unmet = [check.name for check in checklist if check.kind == "skill" and check.verdict != "met"]
    backers = other_backers(profile, resume, unmet) if profile else {}
    gaps = technology_gaps(checklist, skills, backers)
    stage = furthest_stage(application)
    # The company's newest snapshot when the report is written; research runs first in the
    # worker, so a rejection's company is usually researched by now.
    company = application_company(db, application)
    snapshot = newest_snapshot(db, company.id) if company is not None else None
    fields = {
        "company_research_id": snapshot.id if snapshot is not None else None,
        "resume_id": resume.id,
        "resume_label": resume.label[:_MAX_LABEL],
        "stage": stage,
        "score": match.score if match else None,
        "checklist": [check.model_dump() for check in checklist],
        "technology_gaps": [gap.model_dump() for gap in gaps],
    }

    resume_text = (resume.plain_text or tex_to_text(resume.source_tex)).strip()
    if not posting.raw_text.strip():
        return _Prepared(
            fields=fields,
            payload=None,
            skip_note=NO_TEXT_NOTE,
        )
    if not resume_text:
        return _Prepared(
            fields=fields,
            payload=None,
            skip_note="The resume has no readable text, so only its named technologies were "
            "compared.",
        )
    payload = {
        "posting": {
            "company": posting.company[:_MAX_TITLE],
            "title": posting.title[:_MAX_TITLE],
            "text": posting.raw_text[:_MAX_POSTING_TEXT],
        },
        "stage": stage,
        "resume_label": resume.label[:_MAX_LABEL],
        "resume_text": resume_text[:_MAX_RESUME_TEXT],
        "fact_sheet": fact_sheet[:_MAX_FACT_SHEET],
        "checklist": [
            {
                "kind": check.kind,
                "name": check.name[:_MAX_TITLE],
                "requirement": check.requirement,
                "verdict": check.verdict,
                "detail": check.detail[:_MAX_DETAIL],
            }
            for check in checklist[:_MAX_CHECKLIST]
        ],
        "score": match.score if match else None,
        "resume_years": profile.years(resume) if profile else None,
        "company": model_context(db, application, company, snapshot),
    }
    return _Prepared(fields=fields, payload=payload, skip_note=None)


def _add_cost(analysis: RejectAnalysis, usage: dict[str, Any], cost_usd: float) -> None:
    total: Counter[str] = Counter(analysis.usage or {})
    total.update({name: int(count or 0) for name, count in usage.items()})
    analysis.usage = dict(total)
    analysis.cost_usd = round((analysis.cost_usd or 0.0) + cost_usd, 6)


def run_analysis(
    db: Session, analysis: RejectAnalysis, analyze: AnalyzeRequest, today: date
) -> None:
    prepared = _gather(db, analysis, today)
    if isinstance(prepared, str):
        _finish(analysis, TailorStatus.FAILED, prepared)
        db.commit()
        return
    for name, value in prepared.fields.items():
        setattr(analysis, name, value)
    # A re-run starts the written part afresh.
    analysis.summary = ""
    analysis.strengths = []
    analysis.experience_gaps = []
    analysis.focus_areas = []
    analysis.comparison = []
    analysis.estimate = {}
    if prepared.payload is None:
        _finish(analysis, TailorStatus.DONE, prepared.skip_note)
        db.commit()
        logger.info("Rejection analysis %s finished without a model call", analysis.id)
        return
    analysis_id = analysis.id
    # Keep the graded part, and end the transaction so nothing is held open during the call.
    db.commit()

    try:
        result = analyze(prepared.payload)
    except TailorUnavailable as exc:
        if not _still_there(db, analysis_id):
            return
        if exc.spent:
            # Model calls are billed even when their answer can't be used.
            _add_cost(analysis, exc.spent.get("usage") or {}, float(exc.spent.get("cost_usd") or 0))
        reason = str(exc).rstrip(".")
        _finish(
            analysis,
            TailorStatus.FAILED,
            f"The written report couldn't be made: {reason}. The graded checklist is still "
            "current, and you can try again.",
        )
        db.commit()
        return
    if not _still_there(db, analysis_id):
        return
    analysis.summary = result.analysis.summary
    analysis.strengths = list(result.analysis.strengths)
    analysis.experience_gaps = [gap.model_dump() for gap in result.analysis.experience_gaps]
    analysis.focus_areas = [area.model_dump() for area in result.analysis.focus_areas]
    analysis.comparison = [line.model_dump() for line in result.analysis.comparison]
    analysis.estimate = result.analysis.estimate.model_dump()
    analysis.model = result.model[:40] or None
    _add_cost(analysis, result.usage.model_dump(), result.cost_usd)
    _finish(analysis, TailorStatus.DONE, None)
    db.commit()
    logger.info("Rejection analysis %s finished", analysis_id)


def _still_there(db: Session, analysis_id: uuid.UUID) -> bool:
    """False when the application, and so its analysis, was deleted during the model call."""
    if db.scalar(select(RejectAnalysis.id).where(RejectAnalysis.id == analysis_id)) is None:
        logger.info("Rejection analysis %s was deleted while it ran", analysis_id)
        db.rollback()
        return False
    return True


def run_analysis_safely(db: Session, analysis: RejectAnalysis, analyze: AnalyzeRequest) -> None:
    run_safely(
        db,
        f"Rejection analysis {analysis.id}",
        lambda: run_analysis(db, analysis, analyze, date.today()),
        lambda: _finish(
            analysis, TailorStatus.FAILED, "Something went wrong while analyzing. Try again."
        ),
    )
