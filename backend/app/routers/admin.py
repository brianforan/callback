"""The Admin panel's Users, Boards and Settings tabs: every user with their role, plan and
limits override, the Companies catalog's sources, and the settings that tune API spend.
Admins only."""

import logging
import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.app_config import (
    SPECS,
    SPECS_BY_KEY,
    ConfigError,
    get_value,
    reset_value,
    saved_rows,
    set_value,
)
from app.companies.catalog import WEB
from app.deps import AdminUser, Db
from app.listings.boards import BOARDS
from app.models import (
    BoardStat,
    CatalogJob,
    Company,
    CompanyKind,
    CompanySource,
    Role,
    User,
)
from app.schemas import (
    AdminUserOut,
    AdminUserUpdate,
    AppConfigOut,
    AppConfigUpdate,
    CatalogSourceStatOut,
    CatalogStatsOut,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/users", response_model=list[AdminUserOut])
def list_users(admin: AdminUser, db: Db) -> list[User]:
    """Every user, oldest first."""
    return list(db.scalars(select(User).order_by(User.created_at, User.email)).all())


_SOURCE_NAMES = {
    **{board.id: board.name for board in BOARDS},
    "greenhouse": "Greenhouse",
    "lever": "Lever",
    "ashby": "Ashby",
    WEB: "Other web pages",
}


@router.get("/catalog", response_model=CatalogStatsOut)
def catalog_stats(admin: AdminUser, db: Db) -> CatalogStatsOut:
    """Where the catalog's listings came from, most kept first."""
    companies = dict(
        db.execute(
            select(
                CompanySource.source, func.count(func.distinct(CompanySource.company_id))
            ).group_by(CompanySource.source)
        ).all()
    )
    stats = db.scalars(select(BoardStat)).all()
    sources = [
        CatalogSourceStatOut(
            source=stat.board,
            name=_SOURCE_NAMES.get(stat.board, stat.board),
            listings_recorded=stat.listings_recorded,
            kept=stat.kept,
            last_kept_at=stat.last_kept_at,
            companies=companies.get(stat.board, 0),
        )
        for stat in stats
    ]
    sources.sort(key=lambda item: (-item.kept, -item.listings_recorded, item.name))
    return CatalogStatsOut(
        companies=db.scalar(select(func.count()).select_from(Company)) or 0,
        agencies=db.scalar(
            select(func.count())
            .select_from(Company)
            .where(Company.kind == CompanyKind.AGENCY.value)
        )
        or 0,
        jobs=db.scalar(select(func.count()).select_from(CatalogJob)) or 0,
        sources=sources,
    )


@router.patch("/users/{user_id}", response_model=AdminUserOut)
def update_user(user_id: uuid.UUID, payload: AdminUserUpdate, admin: AdminUser, db: Db) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if payload.role is not None and payload.role != user.role:
        if user.id == admin.id:
            raise HTTPException(status.HTTP_409_CONFLICT, "You can't change your own role")
        if user.role == Role.ADMIN:
            # Admins can make admins but not unmake them, so one admin can't remove the rest.
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "An admin's role can't be taken away here. On the server, run "
                "python -m app.cli set-role --email ... --role member.",
            )
        logger.info("Admin %s set user %s to role %s", admin.id, user.id, payload.role.value)
        user.role = payload.role.value
    if payload.plan is not None and payload.plan != user.plan:
        logger.info("Admin %s set user %s to plan %s", admin.id, user.id, payload.plan.value)
        user.plan = payload.plan.value
    if payload.override_limits is not None and payload.override_limits != user.override_limits:
        logger.info(
            "Admin %s set user %s override_limits to %s",
            admin.id,
            user.id,
            payload.override_limits,
        )
        user.override_limits = payload.override_limits
    db.commit()
    return user


def _config_out(db: Session) -> list[AppConfigOut]:
    rows = saved_rows(db)
    emails = dict(
        db.execute(
            select(User.id, User.email).where(
                User.id.in_([row.updated_by for row in rows.values() if row.updated_by])
            )
        ).all()
    )
    items = []
    for spec in SPECS:
        row = rows.get(spec.key)
        items.append(
            AppConfigOut(
                key=spec.key,
                label=spec.label,
                help=spec.help,
                unit=spec.unit,
                kind="int" if spec.kind is int else "float",
                minimum=spec.minimum,
                maximum=spec.maximum,
                default=spec.default,
                value=get_value(db, spec.key),
                saved=row is not None,
                updated_at=row.updated_at if row else None,
                updated_by=emails.get(row.updated_by) if row and row.updated_by else None,
            )
        )
    return items


@router.get("/config", response_model=list[AppConfigOut])
def list_config(admin: AdminUser, db: Db) -> list[AppConfigOut]:
    """Every setting that tunes API spend, in the order the Settings tab shows them."""
    return _config_out(db)


@router.put("/config/{key}", response_model=list[AppConfigOut])
def update_config(
    key: str, payload: AppConfigUpdate, admin: AdminUser, db: Db
) -> list[AppConfigOut]:
    if key not in SPECS_BY_KEY:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{key} isn't a setting")
    try:
        set_value(db, key, payload.value, admin.id)
    except ConfigError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    db.commit()
    return _config_out(db)


@router.delete("/config/{key}", response_model=list[AppConfigOut])
def reset_config(key: str, admin: AdminUser, db: Db) -> list[AppConfigOut]:
    """Back to the default."""
    if key not in SPECS_BY_KEY:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{key} isn't a setting")
    reset_value(db, key, admin.id)
    db.commit()
    return _config_out(db)
