"""A user's profiles: list, add, rename, remove, and pick the one this browser uses."""

import logging
import uuid

from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.deps import CurrentProfile, CurrentUser, Db
from app.models import Application, Profile, Resume, User
from app.profiles import MAX_PROFILES, PROFILE_COOKIE, owned_profile, user_profiles
from app.schemas import ProfileCreate, ProfileOut, ProfilesOut, ProfileUpdate
from app.vocabulary import bump_vocabulary

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/profiles", tags=["profiles"])

# The pick outlives a login session, so signing in again lands in the same profile.
_COOKIE_DAYS = 365


def _get_owned(db: Session, user: User, profile_id: uuid.UUID) -> Profile:
    profile = owned_profile(db, user, profile_id)
    if profile is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Profile not found")
    return profile


def _listing(db: Session, user: User, active_id: uuid.UUID) -> ProfilesOut:
    profiles = user_profiles(db, user.id)
    return ProfilesOut(
        profiles=[ProfileOut.model_validate(profile) for profile in profiles],
        active_id=active_id,
    )


def _name_taken() -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, "You already have a profile with this name")


def set_profile_cookie(response: Response, profile_id: uuid.UUID) -> None:
    response.set_cookie(
        PROFILE_COOKIE,
        str(profile_id),
        max_age=_COOKIE_DAYS * 24 * 3600,
        httponly=True,
        secure=get_settings().cookie_secure,
        samesite="strict",
        path="/",
    )


@router.get("", response_model=ProfilesOut)
def list_profiles(user: CurrentUser, profile: CurrentProfile, db: Db) -> ProfilesOut:
    return _listing(db, user, profile.id)


@router.post("", response_model=ProfileOut, status_code=status.HTTP_201_CREATED)
def create_profile(payload: ProfileCreate, user: CurrentUser, db: Db) -> Profile:
    count = db.scalar(select(func.count(Profile.id)).where(Profile.user_id == user.id)) or 0
    if count >= MAX_PROFILES:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"You already have {MAX_PROFILES} profiles. Remove one first.",
        )
    profile = Profile(user_id=user.id, name=payload.name, software_skills=payload.software_skills)
    db.add(profile)
    if payload.software_skills:
        bump_vocabulary(db, user.id)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise _name_taken() from exc
    logger.info("Created profile %s for user %s", profile.id, user.id)
    return profile


@router.patch("/{profile_id}", response_model=ProfileOut)
def rename_profile(
    profile_id: uuid.UUID, payload: ProfileUpdate, user: CurrentUser, db: Db
) -> Profile:
    profile = _get_owned(db, user, profile_id)
    profile.name = payload.name
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise _name_taken() from exc
    return profile


@router.post("/{profile_id}/activate", response_model=ProfilesOut)
def activate_profile(
    profile_id: uuid.UUID, response: Response, user: CurrentUser, db: Db
) -> ProfilesOut:
    profile = _get_owned(db, user, profile_id)
    set_profile_cookie(response, profile.id)
    return _listing(db, user, profile.id)


@router.delete("/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_profile(
    profile_id: uuid.UUID, request: Request, response: Response, user: CurrentUser, db: Db
) -> None:
    """Delete a profile with its resumes, fact sheet and Scout settings.

    Applications, postings and past runs stay, without a profile. A profile whose resumes were
    sent with applications can't go, since those applications would lose their resume.
    """
    profile = _get_owned(db, user, profile_id)
    if len(user_profiles(db, user.id)) == 1:
        raise HTTPException(status.HTTP_409_CONFLICT, "You can't remove your only profile")
    linked = db.scalar(
        select(func.count(Application.id))
        .select_from(Application)
        .join(Resume, Application.resume_id == Resume.id)
        .where(Resume.profile_id == profile.id)
    )
    if linked:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This profile's resumes are linked to applications, so it can't be removed",
        )
    db.delete(profile)
    # Its skill terms and software switch go with it, so the user's vocabulary changes.
    bump_vocabulary(db, user.id)
    db.commit()
    if request.cookies.get(PROFILE_COOKIE) == str(profile_id):
        response.delete_cookie(PROFILE_COOKIE, path="/")
    logger.info("Deleted profile %s for user %s", profile_id, user.id)
