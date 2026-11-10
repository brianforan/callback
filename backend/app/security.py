import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.config import get_settings

MIN_PASSWORD_LENGTH = 12
SESSION_COOKIE = "callback_session"
API_TOKEN_PREFIX = "cb_"  # noqa: S105
_JWT_ALGORITHM = "HS256"

_hasher = PasswordHasher()
# Verified against when the email is unknown, so login timing doesn't reveal which emails exist.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    try:
        _hasher.verify(password_hash or _DUMMY_HASH, password)
    except (VerificationError, InvalidHashError):
        return False
    return password_hash is not None


def create_session_token(user_id: uuid.UUID) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    claims = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(hours=settings.session_hours),
    }
    return jwt.encode(claims, settings.secret_key, algorithm=_JWT_ALGORITHM)


def read_session_token(token: str) -> uuid.UUID | None:
    try:
        claims = jwt.decode(token, get_settings().secret_key, algorithms=[_JWT_ALGORITHM])
        return uuid.UUID(claims["sub"])
    except (jwt.InvalidTokenError, KeyError, ValueError):
        return None


def generate_api_token() -> str:
    return API_TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_api_token(token: str) -> str:
    # Tokens are 256 bits of randomness, so a fast hash is enough; only the hash is stored.
    return hashlib.sha256(token.encode()).hexdigest()
