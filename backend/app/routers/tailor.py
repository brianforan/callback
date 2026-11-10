"""Start a tailoring for a posting, follow it, and download the PDF it made.

Starting only queues a job. The tailor worker does the work, so these routes never call the
tailor service or a model themselves.
"""

import logging
import re
import uuid
from datetime import UTC, date, datetime, time
from http import HTTPStatus

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.app_config import daily_limit
from app.deps import CurrentProfile, CurrentUser, Db
from app.models import (
    TAILOR_ACTIVE,
    JobPosting,
    Resume,
    TailorJob,
    TailorStatus,
    User,
)
from app.resumes.matching import facts_from_posting
from app.resumes.variants import load_baselines, pick_baseline
from app.schemas import TailorJobOut, TailorStatusOut

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["tailoring"])

_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


def _used_today(db: Session, user: User) -> int:
    """Tailorings started since midnight UTC, finished or not. Failed ones count too."""
    midnight = datetime.combine(datetime.now(UTC).date(), time.min, tzinfo=UTC)
    query = select(func.count(TailorJob.id)).where(
        TailorJob.user_id == user.id, TailorJob.created_at >= midnight
    )
    return db.scalar(query) or 0


def _to_out(job: TailorJob) -> TailorJobOut:
    return TailorJobOut(
        id=job.id,
        posting_id=job.posting_id,
        status=job.status,
        attempts=job.attempts,
        baseline_id=job.baseline_id,
        baseline_label=job.baseline.label if job.baseline else None,
        result_resume_id=job.result_resume_id,
        result_resume_label=job.result_resume.label if job.result_resume else None,
        has_pdf=job.has_pdf,
        reasons=job.reasons or [],
        changelog=job.changelog or [],
        gaps=job.gaps or [],
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


def _status(db: Session, user: User, posting_id: uuid.UUID) -> TailorStatusOut:
    job = db.scalar(
        select(TailorJob)
        .where(TailorJob.user_id == user.id, TailorJob.posting_id == posting_id)
        .options(selectinload(TailorJob.baseline), selectinload(TailorJob.result_resume))
        .order_by(TailorJob.created_at.desc(), TailorJob.id.desc())
        .limit(1)
    )
    return TailorStatusOut(
        job=_to_out(job) if job else None,
        used_today=_used_today(db, user),
        daily_limit=daily_limit(db, user, "tailor_daily_limit"),
    )


def _get_posting(db: Session, user: User, posting_id: uuid.UUID) -> JobPosting:
    posting = db.scalar(
        select(JobPosting)
        .where(JobPosting.id == posting_id, JobPosting.user_id == user.id)
        .options(selectinload(JobPosting.technologies), selectinload(JobPosting.applications))
    )
    if posting is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Posting not found")
    return posting


@router.get("/postings/{posting_id}/tailor", response_model=TailorStatusOut)
def tailor_status(posting_id: uuid.UUID, user: CurrentUser, db: Db) -> TailorStatusOut:
    _get_posting(db, user, posting_id)
    return _status(db, user, posting_id)


@router.post(
    "/postings/{posting_id}/tailor",
    response_model=TailorStatusOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def start_tailoring(
    posting_id: uuid.UUID, user: CurrentUser, profile: CurrentProfile, db: Db
) -> TailorStatusOut:
    """Queue a tailoring of the posting's suggested baseline. The result is staged when done.

    The baseline and fact sheet (if any) come from the active profile, and the job keeps that
    profile.
    """
    posting = _get_posting(db, user, posting_id)
    if posting.applications:
        raise HTTPException(status.HTTP_409_CONFLICT, "You've already applied to this posting")
    active = db.scalar(
        select(TailorJob.id).where(
            TailorJob.posting_id == posting.id, TailorJob.status.in_(TAILOR_ACTIVE)
        )
    )
    if active is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This posting is already being tailored")
    if not posting.raw_text.strip():
        raise HTTPException(
            HTTPStatus.UNPROCESSABLE_CONTENT,
            "Add the posting's text first, so there's something to tailor to",
        )
    baselines = load_baselines(
        db.scalars(select(Resume).where(Resume.profile_id == profile.id)).all(), date.today()
    )
    pick = pick_baseline(baselines, facts_from_posting(posting)) if baselines else None
    if pick is None:
        raise HTTPException(
            HTTPStatus.UNPROCESSABLE_CONTENT,
            "Add a baseline first, so there's a resume to start from",
        )
    picked = db.get(Resume, pick.resume_id)
    if picked is not None and picked.source_format == "pdf":
        # A PDF's outline is read from its text, so there's no LaTeX to rewrite or compile.
        raise HTTPException(
            HTTPStatus.UNPROCESSABLE_CONTENT,
            f"The best baseline for this job, {picked.label}, is a PDF. Tailoring rewrites "
            "LaTeX, so upload a .tex version of it to tailor from.",
        )
    limit = daily_limit(db, user, "tailor_daily_limit")
    if limit is not None and _used_today(db, user) >= limit:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"You've used all {limit} tailorings for today. They reset at midnight UTC.",
        )

    job = TailorJob(
        user_id=user.id,
        profile_id=profile.id,
        posting_id=posting.id,
        baseline_id=pick.resume_id,
        status=TailorStatus.QUEUED.value,
        attempts=0,
        reasons=[],
        changelog=[],
        gaps=[],
        usage={},
    )
    db.add(job)
    db.commit()
    logger.info("Queued tailoring job %s", job.id)
    return _status(db, user, posting.id)


@router.get("/tailor-jobs/{job_id}/pdf")
def tailored_pdf(job_id: uuid.UUID, user: CurrentUser, db: Db) -> Response:
    job = db.scalar(
        select(TailorJob)
        .where(TailorJob.id == job_id, TailorJob.user_id == user.id)
        .options(selectinload(TailorJob.result_resume))
    )
    if job is None or job.result_pdf is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No PDF for this tailoring")
    stem = job.result_resume.filename.removesuffix(".tex") if job.result_resume else "tailored"
    filename = _FILENAME.sub("-", stem)[:100] or "tailored"
    return Response(
        content=job.result_pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}.pdf"'},
    )
