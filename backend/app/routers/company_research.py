"""Company research: an application's company snapshot, and asking for a new one.

Snapshots are also queued when an application is marked rejected (app.routers.applications).
These routes only read and queue: the tailor worker does the research, so they never call the
tailor service or a model themselves.
"""

import uuid
from datetime import UTC, datetime
from http import HTTPStatus

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.deps import CurrentUser, Db
from app.models import Application, User
from app.research.snapshots import queue_research, research_view
from app.schemas import CompanyResearchView

router = APIRouter(prefix="/api", tags=["company research"])


def _application(db: Session, user: User, application_id: uuid.UUID) -> Application:
    application = db.scalar(
        select(Application)
        .where(Application.id == application_id, Application.user_id == user.id)
        .options(selectinload(Application.posting))
    )
    if application is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Application not found")
    return application


@router.get("/applications/{application_id}/company-research", response_model=CompanyResearchView)
def get_company_research(
    application_id: uuid.UUID, user: CurrentUser, db: Db
) -> CompanyResearchView:
    return research_view(db, _application(db, user, application_id), datetime.now(UTC))


@router.post(
    "/applications/{application_id}/company-research",
    response_model=CompanyResearchView,
    status_code=status.HTTP_202_ACCEPTED,
)
def start_company_research(
    application_id: uuid.UUID, user: CurrentUser, db: Db
) -> CompanyResearchView:
    """Research this application's company, under the same rules as a rejection: not an agency,
    no snapshot in the last 30 days, and within today's limit. One already running is reused."""
    application = _application(db, user, application_id)
    now = datetime.now(UTC)
    url = application.job_url or (application.posting.url if application.posting else None)
    queued = queue_research(db, user.id, application.company, url, now)
    if queued.research is None:
        db.rollback()
        code = {
            "agency": HTTPStatus.UNPROCESSABLE_CONTENT,
            "no_name": HTTPStatus.UNPROCESSABLE_CONTENT,
            "over_limit": HTTPStatus.TOO_MANY_REQUESTS,
        }.get(queued.code or "", HTTPStatus.CONFLICT)
        raise HTTPException(code, queued.reason)
    db.commit()
    return research_view(db, application, now)
