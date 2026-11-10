"""Rejection analysis, API side: queueing a report when an application is rejected, grading the
posting's named requirements against the resume sent and the fact sheet, and adding the reports
up across rejections.

Pure functions apart from the queueing helpers, and no model calls here. The tailor worker runs
each queued report (app.research.analysis_worker), and the one model call happens in the tailor
service.
"""

import logging
import uuid
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime, time

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    TAILOR_ACTIVE,
    Application,
    ApplicationStatus,
    RejectAnalysis,
    Resume,
    TailorStatus,
)
from app.resumes.matching import MatchProfile
from app.resumes.structure import Evidence
from app.schemas import (
    ComparisonOut,
    EstimateOut,
    ExperienceExample,
    ExperienceGapOut,
    FocusAreaOut,
    GapAction,
    RejectAnalysisOut,
    RejectedExperience,
    RejectedTechnology,
    RejectionRow,
    RejectionSummary,
    RepeatCompany,
    RequirementCheck,
    TechnologyGap,
)
from app.tailoring.rules import FactSheetSkills, SkillStatus

logger = logging.getLogger(__name__)

# Funnel stages in order, so the furthest one reached before a rejection can be told.
_STAGES = (
    ApplicationStatus.APPLIED.value,
    ApplicationStatus.SCREENING.value,
    ApplicationStatus.INTERVIEW.value,
    ApplicationStatus.OFFER.value,
)
_STAGE_RANK = {stage: rank for rank, stage in enumerate(_STAGES)}
_GAP_REQUIREMENTS = ("required", "nice_to_have", "mentioned")
_REQUIREMENT_ORDER = {name: rank for rank, name in enumerate(_GAP_REQUIREMENTS)}
_VERDICT_ORDER = {"missing": 0, "unknown": 1, "weak": 2}
_MAX_TECHNOLOGIES = 25
_MAX_EXPERIENCE_EXAMPLES = 3
_MAX_RECENT = 30
# The note on a report written without the posting's text. When the text arrives later, a
# report with this note is queued again (app.postings.text).
NO_TEXT_NOTE = (
    "The posting has no text saved, so only its named technologies were compared. Add its text "
    "to the posting for a full report."
)


def furthest_stage(application: Application) -> str:
    """The furthest funnel stage reached before the rejection. Applied when none was logged."""
    reached = [event.status for event in application.events if event.status in _STAGE_RANK]
    return max(reached, key=_STAGE_RANK.__getitem__, default=ApplicationStatus.APPLIED.value)


def midnight_utc(now: datetime) -> datetime:
    return datetime.combine(now.astimezone(UTC).date(), time.min, tzinfo=UTC)


def used_today(db: Session, user_id: uuid.UUID, now: datetime) -> int:
    """Analyses the worker started for this user since midnight UTC, finished or not."""
    query = select(func.count(RejectAnalysis.id)).where(
        RejectAnalysis.user_id == user_id, RejectAnalysis.started_at >= midnight_utc(now)
    )
    return db.scalar(query) or 0


def requeue(analysis: RejectAnalysis) -> None:
    """Back to queued, behind what's already waiting. started_at stays, so a run earlier today
    still counts toward the daily limit until the worker starts this one."""
    analysis.status = TailorStatus.QUEUED.value
    analysis.note = None
    analysis.finished_at = None
    analysis.created_at = datetime.now(UTC)


def queue_analysis(db: Session, application: Application) -> RejectAnalysis | None:
    """Queue a report for a rejected application that has none. Adds to the session only.

    An application already analyzed keeps its report: moving it off rejected and back doesn't
    pay for a second one. A re-run is asked for on its own (requeue).
    """
    if application.status != ApplicationStatus.REJECTED.value:
        return None
    existing = db.scalar(
        select(RejectAnalysis).where(RejectAnalysis.application_id == application.id)
    )
    if existing is not None:
        return None
    analysis = RejectAnalysis(
        user_id=application.user_id,
        profile_id=application.profile_id,
        application_id=application.id,
        status=TailorStatus.QUEUED.value,
        checklist=[],
        technology_gaps=[],
        summary="",
        strengths=[],
        experience_gaps=[],
        focus_areas=[],
        usage={},
        cost_usd=0.0,
    )
    db.add(analysis)
    logger.info("Queued a rejection analysis for application %s", application.id)
    return analysis


def rerun_blocked(analysis: RejectAnalysis | None, now: datetime) -> str | None:
    """Why the report can't be run again now, or None when it can.

    A run that failed can be tried again at once. A finished one can be run again from the
    next UTC day, after the resume or fact sheet has had a chance to change.
    """
    if analysis is None:
        return None
    if analysis.status in TAILOR_ACTIVE:
        return "This analysis is already waiting or running."
    if analysis.status == TailorStatus.FAILED.value:
        return None
    started = analysis.started_at
    if started is not None and started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    if started is not None and started >= midnight_utc(now):
        return "This was analyzed today. Run it again tomorrow, after updating your resume."
    return None


# --- Grading the named requirements ---------------------------------------------------------


def other_backers(profile: MatchProfile, sent: Resume, names: Iterable[str]) -> dict[str, str]:
    """For each name, the label of another resume in the profile with a role or project for it."""
    backers: dict[str, str] = {}
    for name in names:
        for resume in profile.resumes:
            if resume.id == sent.id:
                continue
            if profile.evidence(resume, name).evidence in (Evidence.EXPERIENCE, Evidence.PROJECT):
                backers[name] = resume.label
                break
    return backers


def _action(
    check: RequirementCheck, skills: FactSheetSkills | None, backers: Mapping[str, str]
) -> tuple[GapAction, str]:
    status = skills.technologies.get(check.name) if skills is not None else None
    if status is SkillStatus.NEVER:
        return "learn", "Your fact sheet says not to claim it, so this is a real gap to build."
    if status is SkillStatus.RESTRICTED:
        return (
            "limited",
            "Your fact sheet allows it only in certain places, so it can't lead on the resume.",
        )
    if status is SkillStatus.CLAIMABLE:
        if check.verdict == "missing":
            return (
                "add_to_resume",
                "Your fact sheet says you can claim it, but this resume doesn't name it.",
            )
        return (
            "show_in_a_role",
            "Your fact sheet backs it. Show it in a role's bullet, with what you built.",
        )
    if check.name in backers:
        return "borrow", f"Your {backers[check.name]} resume shows it."
    if check.verdict == "weak":
        return (
            "show_in_a_role",
            "It's on the resume, but only listed or with fewer years than asked. A bullet "
            "showing what you built with it would make it count.",
        )
    return "learn", "Not on this resume, another of your resumes, or your fact sheet."


def technology_gaps(
    checklist: Sequence[RequirementCheck],
    skills: FactSheetSkills | None,
    backers: Mapping[str, str],
) -> list[TechnologyGap]:
    """The named technologies not fully met, required first, then missing before weak."""
    gaps = []
    for check in checklist:
        if check.kind != "skill" or check.verdict == "met":
            continue
        if check.requirement not in _REQUIREMENT_ORDER:
            continue
        action, note = _action(check, skills, backers)
        gaps.append(
            TechnologyGap(
                name=check.name,
                requirement=check.requirement,  # type: ignore[arg-type]
                verdict=check.verdict,
                detail=check.detail,
                action=action,
                note=note,
            )
        )
    gaps.sort(
        key=lambda gap: (
            _REQUIREMENT_ORDER[gap.requirement],
            _VERDICT_ORDER.get(gap.verdict, len(_VERDICT_ORDER)),
            gap.name.lower(),
        )
    )
    return gaps


# --- Reading reports back -------------------------------------------------------------------


def to_out(analysis: RejectAnalysis) -> RejectAnalysisOut:
    return RejectAnalysisOut(
        id=analysis.id,
        application_id=analysis.application_id,
        status=analysis.status,  # type: ignore[arg-type]
        stage=analysis.stage,  # type: ignore[arg-type]
        score=analysis.score,
        resume_id=analysis.resume_id,
        resume_label=analysis.resume_label,
        checklist=[RequirementCheck.model_validate(item) for item in analysis.checklist or []],
        technology_gaps=[
            TechnologyGap.model_validate(item) for item in analysis.technology_gaps or []
        ],
        summary=analysis.summary or "",
        strengths=list(analysis.strengths or []),
        experience_gaps=[
            ExperienceGapOut.model_validate(item) for item in analysis.experience_gaps or []
        ],
        focus_areas=[FocusAreaOut.model_validate(item) for item in analysis.focus_areas or []],
        comparison=[ComparisonOut.model_validate(item) for item in analysis.comparison or []],
        estimate=EstimateOut.model_validate(analysis.estimate) if analysis.estimate else None,
        note=analysis.note,
        created_at=analysis.created_at,
        started_at=analysis.started_at,
        finished_at=analysis.finished_at,
    )


def _newest_first(analysis: RejectAnalysis) -> tuple[datetime, str]:
    finished = analysis.finished_at or analysis.created_at
    if finished.tzinfo is None:
        finished = finished.replace(tzinfo=UTC)
    return finished, str(analysis.id)


def summarize(
    rejected: Sequence[Application],
    analyses: Mapping[uuid.UUID, RejectAnalysis],
    used: int,
    daily_limit: int,
    repeats: Sequence[RepeatCompany] = (),
) -> RejectionSummary:
    """Add up the reports for these rejected applications, keyed by application id."""
    stages = Counter(furthest_stage(application) for application in rejected)
    mine = [analyses[app.id] for app in rejected if app.id in analyses]
    done = sorted(
        (item for item in mine if item.status == TailorStatus.DONE.value),
        key=_newest_first,
        reverse=True,
    )
    by_id = {application.id: application for application in rejected}

    requirement_counts: dict[str, Counter[str]] = defaultdict(Counter)
    verdict_counts: dict[str, Counter[str]] = defaultdict(Counter)
    latest: dict[str, TechnologyGap] = {}
    areas: Counter[str] = Counter()
    examples: dict[str, list[ExperienceExample]] = defaultdict(list)
    for analysis in done:
        application = by_id[analysis.application_id]
        for raw in analysis.technology_gaps or []:
            gap = TechnologyGap.model_validate(raw)
            requirement_counts[gap.name][gap.requirement] += 1
            verdict_counts[gap.name][gap.verdict] += 1
            # Newest first, so the first seen reflects the current fact sheet.
            latest.setdefault(gap.name, gap)
        seen_areas: set[str] = set()
        for raw in analysis.experience_gaps or []:
            gap_out = ExperienceGapOut.model_validate(raw)
            if gap_out.area not in seen_areas:
                seen_areas.add(gap_out.area)
                areas[gap_out.area] += 1
            if len(examples[gap_out.area]) < _MAX_EXPERIENCE_EXAMPLES:
                examples[gap_out.area].append(
                    ExperienceExample(
                        application_id=application.id,
                        company=application.company,
                        role_title=application.role_title,
                        need=gap_out.need,
                    )
                )

    technologies = [
        RejectedTechnology(
            name=name,
            required=counts["required"],
            nice_to_have=counts["nice_to_have"],
            mentioned=counts["mentioned"],
            total=sum(counts.values()),
            missing=verdict_counts[name]["missing"],
            weak=verdict_counts[name]["weak"],
            action=latest[name].action,
            note=latest[name].note,
        )
        for name, counts in requirement_counts.items()
    ]
    technologies.sort(key=lambda row: (-row.required, -row.total, row.name.lower()))
    experience = [
        RejectedExperience(area=area, count=count, examples=examples[area])
        for area, count in sorted(areas.items(), key=lambda pair: (-pair[1], pair[0]))
    ]
    scores = [analysis.score for analysis in done if analysis.score is not None]
    recent = sorted(
        mine,
        key=lambda item: (by_id[item.application_id].applied_on, str(item.id)),
        reverse=True,
    )[:_MAX_RECENT]
    return RejectionSummary(
        rejections=len(rejected),
        analyzed=len(done),
        waiting=sum(item.status in TAILOR_ACTIVE for item in mine),
        not_analyzed=len(rejected) - len(mine),
        stages={stage: stages.get(stage, 0) for stage in _STAGES},
        average_score=round(sum(scores) / len(scores), 1) if scores else None,
        technologies=technologies[:_MAX_TECHNOLOGIES],
        experience=experience,
        recent=[
            RejectionRow(
                application_id=item.application_id,
                company=by_id[item.application_id].company,
                role_title=by_id[item.application_id].role_title,
                applied_on=by_id[item.application_id].applied_on,
                status=item.status,  # type: ignore[arg-type]
                stage=item.stage,  # type: ignore[arg-type]
                score=item.score,
                top_focus=(item.focus_areas or [{}])[0].get("title") or None,
                finished_at=item.finished_at,
            )
            for item in recent
        ],
        used_today=used,
        daily_limit=daily_limit,
        estimates=dict(
            Counter((item.estimate or {}).get("band") for item in done if item.estimate)
        ),
        repeat_companies=list(repeats),
    )
