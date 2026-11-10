import logging

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.deps import CurrentUser, Db
from app.models import User
from app.profiles import PROFILE_COOKIE, first_profile
from app.schemas import AuthConfig, LoginRequest, RegisterRequest, UserOut
from app.security import SESSION_COOKIE, create_session_token, hash_password, verify_password

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])


def _start_session(response: Response, user: User) -> None:
    settings = get_settings()
    response.set_cookie(
        SESSION_COOKIE,
        create_session_token(user.id),
        max_age=settings.session_hours * 3600,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
    )


@router.get("/config", response_model=AuthConfig)
def config() -> AuthConfig:
    return AuthConfig(registration_enabled=get_settings().registration_enabled)


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, response: Response, db: Db) -> User:
    if not get_settings().registration_enabled:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Sign-up is turned off")
    user = User(email=payload.email, password_hash=hash_password(payload.password))
    db.add(user)
    try:
        db.flush()
        db.add(first_profile(user.id))
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "An account with this email already exists"
        ) from exc
    logger.info("Registered user %s", user.id)
    _start_session(response, user)
    return user


@router.post("/login", response_model=UserOut)
def login(payload: LoginRequest, response: Response, db: Db) -> User:
    user = db.scalar(select(User).where(User.email == payload.email.strip().lower()))
    if not verify_password(payload.password, user.password_hash if user else None):
        logger.warning("Failed login attempt")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password")
    _start_session(response, user)
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")
    # The next person to sign in on this browser starts in their own first profile.
    response.delete_cookie(PROFILE_COOKIE, path="/")


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> User:
    return user
