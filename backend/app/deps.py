import logging
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import ApiToken, Profile, User, utcnow
from app.profiles import PROFILE_COOKIE, default_profile, owned_profile, read_profile_id
from app.roles import is_admin
from app.security import SESSION_COOKIE, hash_api_token, read_session_token

logger = logging.getLogger(__name__)

Db = Annotated[Session, Depends(get_db)]


def _user_from_api_token(db: Session, token: str) -> User | None:
    api_token = db.scalar(select(ApiToken).where(ApiToken.token_hash == hash_api_token(token)))
    if api_token is None:
        return None
    api_token.last_used_at = utcnow()
    db.commit()
    return api_token.user


def _user_from_session(db: Session, token: str) -> User | None:
    user_id = read_session_token(token)
    return db.get(User, user_id) if user_id else None


def get_current_user(request: Request, db: Db) -> User:
    authorization = request.headers.get("authorization", "")
    scheme, _, credentials = authorization.partition(" ")
    user = None
    if scheme.lower() == "bearer" and credentials:
        user = _user_from_api_token(db, credentials.strip())
    elif session := request.cookies.get(SESSION_COOKIE):
        user = _user_from_session(db, session)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    return user


def get_intake_user(request: Request, db: Db) -> User:
    """Credentials win when sent, so a bad token fails instead of landing on the default user."""
    if request.headers.get("authorization") or request.cookies.get(SESSION_COOKIE):
        return get_current_user(request, db)
    default_user_id = get_settings().default_user_id
    if default_user_id is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    user = db.get(User, default_user_id)
    if user is None:
        logger.error("DEFAULT_USER_ID %s matches no user", default_user_id)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "DEFAULT_USER_ID does not match a user"
        )
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
IntakeUser = Annotated[User, Depends(get_intake_user)]


def get_admin_user(user: CurrentUser) -> User:
    if not is_admin(user):
        # Not found rather than forbidden, so the Admin panel's existence isn't advertised.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return user


AdminUser = Annotated[User, Depends(get_admin_user)]


def _active_profile(request: Request, db: Session, user: User) -> Profile:
    """The profile this browser picked, or the user's first when it picked none or a stale one.

    Kept in a cookie rather than on the user, so two people sharing an account can each stay
    in their own profile.
    """
    picked = owned_profile(db, user, read_profile_id(request.cookies.get(PROFILE_COOKIE)))
    if picked is not None:
        return picked
    profile = default_profile(db, user)
    # Keeps a profile made just now, so the next request finds the same one.
    db.commit()
    return profile


def get_current_profile(request: Request, db: Db, user: CurrentUser) -> Profile:
    return _active_profile(request, db, user)


def get_intake_profile(request: Request, db: Db, user: IntakeUser) -> Profile:
    return _active_profile(request, db, user)


CurrentProfile = Annotated[Profile, Depends(get_current_profile)]
IntakeProfile = Annotated[Profile, Depends(get_intake_profile)]
