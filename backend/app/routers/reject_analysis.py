"""Rejection analysis: read an application's report, run it again, and see the patterns across
the active profile's rejections.

Reports are queued automatically when an application is marked rejected (app.routers.
applications). These routes only read and queue: the tailor worker does the work, so they never
call the tailor service or a model themselves.
"""

import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.app_config import daily_limit
from app.deps import CurrentProfile, CurrentUser, Db
from app.models import Application, ApplicationStatus, RejectAnalysis, User
from app.research.analysis import (
    requeue,
    rerun_blocked,
    summarize,
    to_out,
    used_today,
)
from app.research.context import application_company, company_view, repeat_companies
from app.research.snapshots import active_research, research_at_rejection, start_report
from app.schemas import QueuedCount, RejectAnalysisStatusOut, RejectionSummary

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["rejection analysis"])


def _application(db: Session, user: User, application_id: uuid.UUID) -> Application:
    application = db.scalar(
        select(Application).where(Application.id == application_id, Application.user_id == user.id)
    )
    if application is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Application not found")
    return application


def _analysis(db: Session, application: Application) -> RejectAnalysis | None:
    return db.scalar(select(RejectAnalysis).where(RejectAnalysis.application_id == application.id))


def _status(db: Session, user: User, application: Application) -> RejectAnalysisStatusOut:
    analysis = _analysis(db, application)
    now = datetime.now(UTC)
    blocked = rerun_blocked(analysis, now)
    if blocked is None and application.status != ApplicationStatus.REJECTED.value:
        blocked = "Only a rejected application is analyzed."
    rejected = application.status == ApplicationStatus.REJECTED.value
    company = application_company(db, application) if rejected else None
    return RejectAnalysisStatusOut(
        analysis=to_out(analysis) if analysis else None,
        researching=company is not None and active_research(db, company.id) is not None,
        company=company_view(db, application, now) if rejected else None,
        used_today=used_today(db, user.id, now),
        daily_limit=daily_limit(db, user, "reject_analysis_daily_limit"),
        rerun_blocked=blocked,
    )


@router.get(
    "/applications/{application_id}/reject-analysis", response_model=RejectAnalysisStatusOut
)
def get_reject_analysis(
    application_id: uuid.UUID, user: CurrentUser, db: Db
) -> RejectAnalysisStatusOut:
    return _status(db, user, _application(db, user, application_id))


@router.post(
    "/applications/{application_id}/reject-analysis",
    response_model=RejectAnalysisStatusOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def run_reject_analysis(
    application_id: uuid.UUID, user: CurrentUser, db: Db
) -> RejectAnalysisStatusOut:
    """Queue the report, or queue it again with the current resume and fact sheet.

    Over the daily limit it still queues, and runs after midnight UTC.
    """
    application = _application(db, user, application_id)
    if application.status != ApplicationStatus.REJECTED.value:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a rejected application is analyzed")
    analysis = _analysis(db, application)
    now = datetime.now(UTC)
    if analysis is None:
        start_report(db, application, now)
    else:
        blocked = rerun_blocked(analysis, now)
        if blocked is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, blocked)
        # A snapshot older than the reuse window is renewed first, as for a new report.
        research_at_rejection(db, application, now)
        # It may have been made under a profile since deleted; use the application's now.
        analysis.profile_id = application.profile_id
        requeue(analysis)
        logger.info("Queued rejection analysis %s again", analysis.id)
    db.commit()
    return _status(db, user, application)


def _rejected(db: Session, user: User, profile_id: uuid.UUID) -> list[Application]:
    return list(
        db.scalars(
            select(Application)
            .where(
                Application.user_id == user.id,
                Application.profile_id == profile_id,
                Application.status == ApplicationStatus.REJECTED.value,
            )
            .options(selectinload(Application.events), selectinload(Application.posting))
            .order_by(Application.applied_on.desc(), Application.id)
        ).all()
    )


@router.get("/reject-analyses/summary", response_model=RejectionSummary)
def rejection_summary(user: CurrentUser, profile: CurrentProfile, db: Db) -> RejectionSummary:
    """The active profile's rejections added up: what they asked for that its resumes lacked."""
    rejected = _rejected(db, user, profile.id)
    analyses = {
        analysis.application_id: analysis
        for analysis in db.scalars(
            select(RejectAnalysis).where(
                RejectAnalysis.user_id == user.id,
                RejectAnalysis.application_id.in_([application.id for application in rejected]),
            )
        ).all()
    }
    now = datetime.now(UTC)
    return summarize(
        rejected,
        analyses,
        used_today(db, user.id, now),
        daily_limit(db, user, "reject_analysis_daily_limit"),
        repeat_companies(db, rejected, now),
    )


@router.post(
    "/reject-analyses/backfill",
    response_model=QueuedCount,
    status_code=status.HTTP_202_ACCEPTED,
)
def backfill_reject_analyses(user: CurrentUser, profile: CurrentProfile, db: Db) -> QueuedCount:
    """Queue a report for every rejected application in the active profile that has none, such
    as ones rejected before this feature. They run within the daily limit, oldest first."""
    now = datetime.now(UTC)
    queued = sum(
        start_report(db, application, now) is not None
        for application in reversed(_rejected(db, user, profile.id))
    )
    db.commit()
    logger.info("Queued %d earlier rejections for analysis for user %s", queued, user.id)
    return QueuedCount(queued=queued)
