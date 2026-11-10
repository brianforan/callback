"""What the queue workers share: the client for the tailor and compiler services, claiming the
oldest queued row, running one without letting a bug stop the worker, and the poll loop.
"""

import logging
import signal
import time
from collections.abc import Callable
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models import CompanyResearch, ScoutRun, TailorJob, TailorStatus, utcnow

logger = logging.getLogger(__name__)

# Characters of a service's error message kept for the user.
_MAX_DETAIL = 300


class TailorUnavailable(Exception):
    """The tailor service couldn't give an answer. The message is safe to show the user.

    spent is what the failed attempt still cost, when tailor says: model calls are billed even
    when their answer can't be used. It has the fields of a stage's answer (model, usage,
    cost_usd, and for the pipeline cost_breakdown and request_costs).
    """

    def __init__(self, message: str, spent: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.spent = spent


def _detail(response: httpx.Response) -> tuple[str | None, dict[str, Any] | None]:
    """An error's message, and what the failed attempt cost when tailor sent that too."""
    try:
        detail = response.json().get("detail")
    except (ValueError, AttributeError):
        return None, None
    if isinstance(detail, dict):
        message = detail.get("message")
        spent = detail.get("spent")
        return (
            message[:_MAX_DETAIL] if isinstance(message, str) else None,
            spent if isinstance(spent, dict) else None,
        )
    return (detail[:_MAX_DETAIL] if isinstance(detail, str) else None), None


def post_json[T: BaseModel](
    client: httpx.Client,
    path: str,
    payload: dict[str, Any],
    model: type[T],
    service: str = "tailoring service",
) -> T:
    """Call the tailor or compiler service and read its answer, or raise TailorUnavailable."""
    try:
        response = client.post(path, json=payload)
    except httpx.TimeoutException as exc:
        logger.warning("The %s timed out on %s", service, path)
        raise TailorUnavailable(f"The {service} took too long to answer.") from exc
    except httpx.HTTPError as exc:
        logger.error("Could not reach the %s: %s", service, exc)
        raise TailorUnavailable(f"The {service} couldn't be reached.") from exc
    if response.status_code != httpx.codes.OK:
        logger.error("The %s returned %s on %s", service, response.status_code, path)
        message, spent = _detail(response)
        raise TailorUnavailable(
            message or f"The {service} returned an error ({response.status_code}).", spent
        )
    try:
        return model.model_validate_json(response.content)
    except ValidationError as exc:
        logger.error("The %s sent an unreadable answer on %s", service, path)
        raise TailorUnavailable(f"The {service}'s answer couldn't be read.") from exc


def claim_oldest[R: (TailorJob, ScoutRun, CompanyResearch)](
    db: Session, model: type[R]
) -> R | None:
    """Take the oldest queued row and mark it running. Skip-locked, so two workers would never
    take the same one."""
    row = db.scalar(
        select(model)
        .where(model.status == TailorStatus.QUEUED.value)
        .order_by(model.created_at, model.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if row is None:
        db.rollback()
        return None
    row.status = TailorStatus.RUNNING.value
    row.started_at = utcnow()
    db.commit()
    return row


def run_safely(db: Session, what: str, work: Callable[[], None], fail: Callable[[], None]) -> None:
    """Run one job. A bug in it marks the job failed, and never stops the worker or leaves the
    job running, which would hold its daily slot or block the queue for good."""
    try:
        work()
    except Exception:
        logger.exception("%s failed unexpectedly", what)
        db.rollback()
        try:
            fail()
            db.commit()
        except SQLAlchemyError:
            logger.exception("Could not mark %s as failed", what)
            db.rollback()


def poll_forever(name: str, poll_seconds: float, step: Callable[[], bool]) -> None:
    """Call step until SIGTERM or SIGINT. It returns True when it did some work, so the next
    one starts at once, and False to wait poll_seconds."""
    stopping = False

    def stop(signum: int, frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while not stopping:
        try:
            if step():
                continue
        except SQLAlchemyError:
            logger.exception("The %s couldn't reach the database", name)
        time.sleep(poll_seconds)
    logger.info("The %s stopped", name)
