from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.deps import CurrentProfile, CurrentUser, Db
from app.models import FactSheet, Profile
from app.profiles import owned_profile
from app.schemas import FactSheetOut, FactSheetUpdate

router = APIRouter(prefix="/api/fact-sheet", tags=["fact sheet"])


def _out(profile: Profile, sheet: FactSheet | None) -> FactSheetOut:
    if sheet is None:
        return FactSheetOut(profile_id=profile.id, content="", updated_at=None)
    return FactSheetOut(profile_id=profile.id, content=sheet.content, updated_at=sheet.updated_at)


@router.get("", response_model=FactSheetOut)
def get_fact_sheet(profile: CurrentProfile, db: Db) -> FactSheetOut:
    sheet = db.scalar(select(FactSheet).where(FactSheet.profile_id == profile.id))
    return _out(profile, sheet)


@router.put("", response_model=FactSheetOut)
def save_fact_sheet(
    payload: FactSheetUpdate, user: CurrentUser, profile: CurrentProfile, db: Db
) -> FactSheetOut:
    """Save to the profile the draft was written for, which may no longer be the active one."""
    if payload.profile_id is not None and payload.profile_id != profile.id:
        target = owned_profile(db, user, payload.profile_id)
        if target is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Profile not found")
        profile = target
    sheet = db.scalar(select(FactSheet).where(FactSheet.profile_id == profile.id))
    if sheet is None:
        sheet = FactSheet(user_id=profile.user_id, profile_id=profile.id)
        db.add(sheet)
    sheet.content = payload.content
    db.commit()
    return _out(profile, sheet)
