"""Finding a user's profiles: the one picked in this browser, or the first one they made."""

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Profile, User

logger = logging.getLogger(__name__)

DEFAULT_PROFILE_NAME = "Default"
PROFILE_COOKIE = "callback_profile"
# Enough for a few job searches side by side, few enough that the switcher stays short.
MAX_PROFILES = 10


def user_profiles(db: Session, user_id: uuid.UUID) -> list[Profile]:
    """Oldest first, so the first is the default and the order never shifts."""
    return list(
        db.scalars(
            select(Profile)
            .where(Profile.user_id == user_id)
            .order_by(Profile.created_at, Profile.id)
        ).all()
    )


def first_profile(user_id: uuid.UUID) -> Profile:
    """The profile a new account starts with, added in the same commit as the user.

    It uses the built-in software skills, as every profile did before profiles had their own.
    """
    return Profile(user_id=user_id, name=DEFAULT_PROFILE_NAME, software_skills=True)


def default_profile(db: Session, user: User) -> Profile:
    """The user's first profile, made on the spot for a user who has none yet. Flushes only.

    New accounts get theirs when they're created. This covers any made before that, and two
    requests making it at once: the second one's insert fails and it uses the first one's.
    """
    profiles = user_profiles(db, user.id)
    if profiles:
        return profiles[0]
    profile = first_profile(user.id)
    try:
        # A savepoint, so a failed insert undoes only itself and not the caller's work.
        with db.begin_nested():
            db.add(profile)
    except IntegrityError:
        logger.info("Another request made the default profile for user %s first", user.id)
        return user_profiles(db, user.id)[0]
    logger.info("Created the default profile for user %s", user.id)
    return profile


def owned_profile(db: Session, user: User, profile_id: uuid.UUID | None) -> Profile | None:
    if profile_id is None:
        return None
    return db.scalar(select(Profile).where(Profile.id == profile_id, Profile.user_id == user.id))


def profile_for_work(db: Session, user: User, profile_id: uuid.UUID | None) -> Profile:
    """The profile a queued job was made under, or the default when it was deleted or unset."""
    return owned_profile(db, user, profile_id) or default_profile(db, user)


def read_profile_id(raw: str | None) -> uuid.UUID | None:
    try:
        return uuid.UUID(raw) if raw else None
    except ValueError:
        return None
