import logging
import uuid
from datetime import date

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import load_only

from app.billing import spend
from app.config import get_settings
from app.deps import AdminUser, Db
from app.models import (
    BillingCost,
    BillingState,
    CompanyResearch,
    CreditTopUp,
    RejectAnalysis,
    ScoutBrief,
    ScoutRun,
    TailorJob,
    User,
)
from app.schemas import SpendOut, TopUpIn, TopUpOut

logger = logging.getLogger(__name__)
# The Admin panel's Spend tab. Admins only; anyone else gets not found.
router = APIRouter(prefix="/api/admin/spend", tags=["admin"])

_DAYS = 31


@router.get("", response_model=SpendOut)
def get_spend(admin: AdminUser, db: Db) -> SpendOut:
    settings = get_settings()
    today = date.today()
    # Every finished run and tailoring ever, so only the columns the page reports on: no step
    # logs, settings or PDFs.
    runs = db.scalars(
        select(ScoutRun)
        .where(ScoutRun.status.in_(("done", "failed")))
        .options(
            load_only(
                ScoutRun.id,
                ScoutRun.user_id,
                ScoutRun.status,
                ScoutRun.stop_message,
                ScoutRun.leads,
                ScoutRun.cost_usd,
                ScoutRun.usage,
                ScoutRun.billed,
                ScoutRun.request_costs,
                ScoutRun.created_at,
                ScoutRun.finished_at,
            )
        )
    ).all()
    # Briefs written on request for Companies leads, so measured cost per day matches the bill.
    briefs = db.scalars(
        select(ScoutBrief)
        .where(ScoutBrief.status.in_(("done", "failed")))
        .options(load_only(ScoutBrief.cost_usd, ScoutBrief.finished_at))
    ).all()
    tailorings = db.scalars(
        select(TailorJob)
        .where(TailorJob.status.in_(("done", "failed")))
        .options(
            load_only(
                TailorJob.id,
                TailorJob.user_id,
                TailorJob.status,
                TailorJob.cost_usd,
                TailorJob.usage,
                TailorJob.billed,
                TailorJob.created_at,
                TailorJob.finished_at,
            )
        )
    ).all()
    # Rejection reports and company research: each one Haiku call.
    analyses = db.scalars(
        select(RejectAnalysis)
        .where(RejectAnalysis.status.in_(("done", "failed")), RejectAnalysis.cost_usd > 0)
        .options(
            load_only(
                RejectAnalysis.id,
                RejectAnalysis.user_id,
                RejectAnalysis.status,
                RejectAnalysis.note,
                RejectAnalysis.cost_usd,
                RejectAnalysis.usage,
                RejectAnalysis.created_at,
                RejectAnalysis.finished_at,
            )
        )
    ).all()
    research = db.scalars(
        select(CompanyResearch)
        .where(CompanyResearch.status.in_(("done", "failed")))
        .options(
            load_only(
                CompanyResearch.id,
                CompanyResearch.requested_by,
                CompanyResearch.status,
                CompanyResearch.note,
                CompanyResearch.cost_usd,
                CompanyResearch.usage,
                CompanyResearch.created_at,
                CompanyResearch.finished_at,
            )
        )
    ).all()
    users = {user.id: user for user in db.scalars(select(User)).all()}
    costs = db.scalars(select(BillingCost)).all()
    topups = db.scalars(
        select(CreditTopUp).order_by(CreditTopUp.added_on.desc(), CreditTopUp.created_at.desc())
    ).all()
    state = {row.key: row.value for row in db.scalars(select(BillingState)).all()}
    return SpendOut(
        setup={
            "usage_synced_at": state.get("usage_synced_at") or None,
            "cost_synced_at": state.get("cost_synced_at") or None,
            "last_error": state.get("last_error", ""),
            "api_key_filter": bool(settings.billing_api_key_id),
            "workspace_filter": bool(settings.billing_workspace_id),
        },
        balance={
            **spend.balance(topups, costs),
            "topups": [TopUpOut.model_validate(topup) for topup in topups],
        },
        quote=spend.quote(runs, tailorings),
        days=spend.daily(
            runs,
            tailorings,
            costs,
            settings.billing_workspace_id,
            today,
            _DAYS,
            briefs,
            [*analyses, *research],
        ),
        users=spend.per_user(users, runs, tailorings, analyses, research),
        jobs=spend.jobs(users, runs, tailorings, analyses, research),
    )


@router.post("/topups", response_model=TopUpOut, status_code=status.HTTP_201_CREATED)
def add_topup(payload: TopUpIn, admin: AdminUser, db: Db) -> CreditTopUp:
    topup = CreditTopUp(**payload.model_dump())
    db.add(topup)
    db.commit()
    logger.info("Recorded a $%.2f top-up", payload.amount_usd)
    return topup


@router.delete("/topups/{topup_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_topup(topup_id: uuid.UUID, admin: AdminUser, db: Db) -> None:
    topup = db.get(CreditTopUp, topup_id)
    if topup is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Top-up not found")
    db.delete(topup)
    db.commit()
