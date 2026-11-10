"""Insights -> Companies: the user's researched companies, one company's page, its hiring history
as CSV, and the admin's exports of every snapshot and event (docs/rejection_research_spec.md,
chunk 5). Read only: nothing here queues research or calls a model.
"""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.deps import AdminUser, CurrentUser, Db
from app.models import Company, User
from app.research.insights import (
    can_view,
    company_cards,
    company_detail,
    events_csv,
    export_jsonl,
    hiring_csv,
)
from app.schemas import CompanyCard, CompanyDetail

router = APIRouter(prefix="/api", tags=["company insights"])


def _viewable(db: Session, user: User, company_id: uuid.UUID) -> Company:
    """The company, if this user may see it. Not found otherwise, so whether another user's
    company exists isn't given away."""
    company = db.get(Company, company_id)
    if company is None or not can_view(db, user, company):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Company not found")
    return company


def _download(content: str, media_type: str, filename: str) -> Response:
    return Response(
        content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/companies/mine", response_model=list[CompanyCard])
def my_companies(user: CurrentUser, db: Db) -> list[CompanyCard]:
    """Companies this user applied to that have a research snapshot."""
    return company_cards(db, user, datetime.now(UTC))


@router.get("/companies/{company_id}", response_model=CompanyDetail)
def get_company(company_id: uuid.UUID, user: CurrentUser, db: Db) -> CompanyDetail:
    detail = company_detail(db, _viewable(db, user, company_id), datetime.now(UTC))
    if detail is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This company hasn't been researched")
    return detail


@router.get("/companies/{company_id}/hiring-history.csv")
def hiring_history(company_id: uuid.UUID, user: CurrentUser, db: Db) -> Response:
    company = _viewable(db, user, company_id)
    return _download(
        hiring_csv(db, company, datetime.now(UTC)), "text/csv", f"hiring-history-{company.id}.csv"
    )


@router.get("/company-research/export.jsonl")
def export_research(admin: AdminUser, db: Db) -> Response:
    """Every finished snapshot, one JSON object per line. Company facts only."""
    return _download(export_jsonl(db), "application/x-ndjson", "company-research.jsonl")


@router.get("/company-research/events.csv")
def export_events(admin: AdminUser, db: Db) -> Response:
    """Every dated event found about any company, one row each."""
    return _download(events_csv(db), "text/csv", "company-events.csv")
