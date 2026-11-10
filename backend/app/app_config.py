"""Settings that tune what Callback spends on the API platform, kept in the database so the
admin can change them in Admin -> Settings as costs come in, without a deploy.

Each setting's range and default live here; the `app_config` table holds only the ones the admin
changed. Values are read from the database every time they're used, so the api and every worker
see a change at once. Secrets and deploy wiring stay in the environment (app.config).
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AppConfig, User

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ConfigSpec:
    key: str
    label: str
    help: str
    unit: str
    # int or float. A float setting accepts whole numbers too.
    kind: type
    minimum: float
    maximum: float
    default: float


# The order here is the order Admin -> Settings shows them in. Each default for a setting that
# was in the environment is what docker-compose.yml passed before it moved, so moving it changed
# nothing on a server.
SPECS: tuple[ConfigSpec, ...] = (
    ConfigSpec(
        key="smartsearch_daily_runs",
        label="Scout runs per user a day",
        help="A typical run costs $0.40 to $0.90. A failed run doesn't count.",
        unit="runs",
        kind=int,
        minimum=0,
        maximum=100,
        default=2,
    ),
    ConfigSpec(
        key="smartsearch_daily_briefs",
        label="Scout briefs per user a day",
        help="One Sonnet call each, about $0.08. A failed brief doesn't count.",
        unit="briefs",
        kind=int,
        minimum=0,
        maximum=500,
        default=10,
    ),
    ConfigSpec(
        key="smartsearch_run_max_usd",
        label="Most one Scout run may spend",
        help="A safety stop, checked before every model call. It holds for every user, "
        "daily limits or not. A run at every maximum costs about $1.50.",
        unit="USD",
        kind=float,
        minimum=0.1,
        maximum=20,
        default=2.0,
    ),
    ConfigSpec(
        key="tailor_daily_limit",
        label="Tailorings per user a day",
        help="Each makes at most two Sonnet calls. Counted from midnight UTC, failed ones too.",
        unit="tailorings",
        kind=int,
        minimum=0,
        maximum=1000,
        default=5,
    ),
    ConfigSpec(
        key="reject_analysis_daily_limit",
        label="Rejection analyses per user a day",
        help="One Haiku call each. More wait in the queue for the next UTC day.",
        unit="analyses",
        kind=int,
        minimum=0,
        maximum=1000,
        default=20,
    ),
    ConfigSpec(
        key="company_research_daily_limit",
        label="Company research snapshots per user a day",
        help="One web-searching Haiku call each, about $0.05 to $0.08. Over it, nothing starts.",
        unit="snapshots",
        kind=int,
        minimum=0,
        maximum=1000,
        default=10,
    ),
    ConfigSpec(
        key="company_research_max_age_days",
        label="Reuse a company's research for",
        help="Everyone reuses a company's newest snapshot this long before a new one can start.",
        unit="days",
        kind=int,
        minimum=1,
        maximum=365,
        default=30,
    ),
)

SPECS_BY_KEY = {spec.key: spec for spec in SPECS}


class ConfigError(ValueError):
    """A value that can't be saved. The message is safe to show the admin."""


def _valid(spec: ConfigSpec, value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return False
    if spec.kind is int and not float(value).is_integer():
        return False
    return spec.minimum <= value <= spec.maximum


def _typed(spec: ConfigSpec, value: float) -> int | float:
    return int(value) if spec.kind is int else float(value)


def get_value(db: Session, key: str) -> int | float:
    """The value in force: the admin's, when it's valid for today's range, else the default."""
    spec = SPECS_BY_KEY[key]
    row = db.get(AppConfig, key)
    if row is not None:
        if _valid(spec, row.value):
            return _typed(spec, row.value)
        logger.warning("Ignoring app_config %s=%r, outside today's range", key, row.value)
    return _typed(spec, spec.default)


def saved_rows(db: Session) -> dict[str, AppConfig]:
    return {row.key: row for row in db.scalars(select(AppConfig)).all()}


def set_value(db: Session, key: str, value: float, admin_id: uuid.UUID) -> AppConfig:
    """Save the admin's value. Raises ConfigError for an unknown key or a value out of range.
    The caller commits."""
    spec = SPECS_BY_KEY.get(key)
    if spec is None:
        raise ConfigError(f"{key} isn't a setting")
    if not _valid(spec, value):
        whole = "a whole number " if spec.kind is int else ""
        raise ConfigError(
            f"{spec.label} must be {whole}from {_typed(spec, spec.minimum):g} to "
            f"{_typed(spec, spec.maximum):g} {spec.unit}"
        )
    row = db.get(AppConfig, key)
    if row is None:
        row = AppConfig(key=key)
        db.add(row)
    row.value = _typed(spec, value)
    row.updated_by = admin_id
    row.updated_at = datetime.now(UTC)
    logger.info("Admin %s set %s to %s", admin_id, key, row.value)
    return row


def reset_value(db: Session, key: str, admin_id: uuid.UUID) -> None:
    """Go back to the default. Raises ConfigError for an unknown key. The caller commits."""
    if key not in SPECS_BY_KEY:
        raise ConfigError(f"{key} isn't a setting")
    row = db.get(AppConfig, key)
    if row is not None:
        db.delete(row)
        logger.info("Admin %s reset %s to its default", admin_id, key)


def daily_limit(db: Session, user: User | uuid.UUID, key: str) -> int | None:
    """The user's daily cap for one kind of model call, or None when the admin lifted their caps
    (users.override_limits)."""
    found = db.get(User, user) if isinstance(user, uuid.UUID) else user
    if found is not None and found.override_limits:
        return None
    return int(get_value(db, key))
