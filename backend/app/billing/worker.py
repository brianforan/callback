"""Reads what Anthropic actually billed, so Callback's own cost figures can be checked.

Two reports from Anthropic's Admin API, read with an Admin API key that only this worker holds:
- The usage report, minute by minute. For each finished Scout run and tailoring, the tokens
  and web searches billed in the minutes it ran are stored beside what Callback measured. Other
  Callback calls in the same minutes are counted too, since the report can't tell them apart.
- The cost report, in dollars, one day at a time. The last month is stored line by line.

Runs as its own container from the API image: python -m app.billing.worker. It reaches the
internet only to call api.anthropic.com, and has no tools a model could call.
"""

import logging
import signal
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, load_only

from app.config import billing_key, get_settings
from app.db import SessionLocal
from app.logs import configure_logging
from app.models import (
    BillingCost,
    BillingState,
    ScoutBrief,
    ScoutRun,
    TailorJob,
    TailorStatus,
    utcnow,
)

logger = logging.getLogger(__name__)

API_BASE = "https://api.anthropic.com"
_API_VERSION = "2023-06-01"
_USER_AGENT = "Callback/0.1 (self-hosted job search tracker)"
# The usage report holds at most a day of minutes per page.
_MAX_MINUTE_BUCKETS = 1_440
# Runs and tailorings older than this aren't checked, since nobody needs them reconciled.
_RECONCILE_DAYS = 30
_FINISHED = (TailorStatus.DONE.value, TailorStatus.FAILED.value)

FetchReport = Callable[[str, list[tuple[str, str]]], list[dict[str, Any]]]


class BillingUnavailable(Exception):
    """Anthropic's reports couldn't be read. The message is safe to show the owner."""


def http_fetcher(client: httpx.Client) -> FetchReport:
    """Read every page of one report and return its buckets, oldest first."""

    def fetch(path: str, params: list[tuple[str, str]]) -> list[dict[str, Any]]:
        buckets: list[dict[str, Any]] = []
        page: str | None = None
        while True:
            query = [*params, ("page", page)] if page else params
            try:
                response = client.get(path, params=query)
            except httpx.HTTPError as exc:
                logger.error("Could not reach Anthropic's %s: %s", path, exc)
                raise BillingUnavailable(
                    "Anthropic's billing reports couldn't be reached."
                ) from exc
            if response.status_code != httpx.codes.OK:
                logger.error("Anthropic's %s returned %s", path, response.status_code)
                hint = (
                    " The key was refused: these reports need an Admin API key, or an API key "
                    "that isn't tied to a workspace. Set ANTHROPIC_ADMIN_API_KEY. Individual "
                    "Console accounts can't make Admin keys; set up an organization in the "
                    "Console's settings first."
                    if response.status_code in (401, 403)
                    else ""
                )
                raise BillingUnavailable(
                    f"Anthropic's billing reports returned an error ({response.status_code}).{hint}"
                )
            try:
                body = response.json()
            except ValueError as exc:
                raise BillingUnavailable("Anthropic's billing report couldn't be read.") from exc
            buckets.extend(body.get("data") or [])
            page = body.get("next_page")
            if not body.get("has_more") or not page:
                return buckets

    return fetch


def _utc(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _minute(moment: datetime) -> datetime:
    return _utc(moment).replace(second=0, microsecond=0)


def _stamp(moment: datetime) -> str:
    return _utc(moment).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class _Job:
    """A finished run or tailoring: when it ran, and the row to store its billed usage on."""

    row: ScoutRun | TailorJob | ScoutBrief
    started: datetime
    finished: datetime


def _window(job: _Job) -> tuple[datetime, datetime]:
    """The whole minutes a job ran in, plus the next one, where its last request may land."""
    start = _minute(job.started)
    end = _minute(job.finished) + timedelta(minutes=2)
    return start, min(end, start + timedelta(minutes=_MAX_MINUTE_BUCKETS))


def _finished_jobs(db: Session, since: datetime) -> list[_Job]:
    jobs: list[_Job] = []
    for model in (ScoutRun, TailorJob, ScoutBrief):
        # Only the times and the billed figures: every finished job in the window is read on
        # each poll, so its PDF and logs stay in the database.
        rows = db.scalars(
            select(model)
            .where(
                model.status.in_(_FINISHED),
                model.started_at.is_not(None),
                model.finished_at.is_not(None),
                model.finished_at >= since,
            )
            .options(load_only(model.id, model.started_at, model.finished_at, model.billed))
        ).all()
        jobs.extend(_Job(row, _utc(row.started_at), _utc(row.finished_at)) for row in rows)
    return jobs


def _usage_params(start: datetime, end: datetime) -> list[tuple[str, str]]:
    settings = get_settings()
    params = [
        ("starting_at", _stamp(start)),
        ("ending_at", _stamp(end)),
        ("bucket_width", "1m"),
        ("limit", str(_MAX_MINUTE_BUCKETS)),
    ]
    if settings.billing_api_key_id:
        params.append(("api_key_ids[]", settings.billing_api_key_id))
    if settings.billing_workspace_id:
        params.append(("workspace_ids[]", settings.billing_workspace_id))
    return params


def sum_usage(buckets: Iterable[dict[str, Any]]) -> dict[str, int]:
    """Billed tokens and searches added up across minute buckets, in Callback's own field names."""
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "web_search_requests": 0,
    }
    for bucket in buckets:
        for result in bucket.get("results") or []:
            creation = result.get("cache_creation") or {}
            totals["input_tokens"] += int(result.get("uncached_input_tokens") or 0)
            totals["output_tokens"] += int(result.get("output_tokens") or 0)
            totals["cache_creation_input_tokens"] += int(
                creation.get("ephemeral_5m_input_tokens") or 0
            ) + int(creation.get("ephemeral_1h_input_tokens") or 0)
            totals["cache_read_input_tokens"] += int(result.get("cache_read_input_tokens") or 0)
            totals["web_search_requests"] += int(
                (result.get("server_tool_use") or {}).get("web_search_requests") or 0
            )
    return totals


def reconcile(db: Session, fetch: FetchReport, now: datetime) -> int:
    """Store billed usage on runs and tailorings that ended long enough ago to be reported."""
    settings = get_settings()
    ready_before = now - timedelta(minutes=settings.billing_report_delay_minutes)
    jobs = _finished_jobs(db, now - timedelta(days=_RECONCILE_DAYS))
    checked = 0
    for job in jobs:
        if job.row.billed is not None or job.finished > ready_before:
            continue
        start, end = _window(job)
        totals = sum_usage(
            fetch("/v1/organizations/usage_report/messages", _usage_params(start, end))
        )
        shared = sum(
            1
            for other in jobs
            if other.row is not job.row and other.started < end and other.finished >= start
        )
        job.row.billed = {
            **totals,
            "window_start": _stamp(start),
            "window_end": _stamp(end),
            # Other Callback model calls in the same minutes, which the report adds in too.
            "shared_with": shared,
            "checked_at": _stamp(now),
        }
        db.commit()
        checked += 1
    return checked


def sync_costs(db: Session, fetch: FetchReport, today: date) -> int:
    """Replace the stored cost report lines for the last days with what Anthropic reports now."""
    days = get_settings().billing_cost_days
    first = today - timedelta(days=days - 1)
    buckets = fetch(
        "/v1/organizations/cost_report",
        [
            ("starting_at", f"{first.isoformat()}T00:00:00Z"),
            ("ending_at", f"{(today + timedelta(days=1)).isoformat()}T00:00:00Z"),
            ("bucket_width", "1d"),
            ("group_by[]", "workspace_id"),
            ("group_by[]", "description"),
            ("limit", str(days)),
        ],
    )
    db.execute(delete(BillingCost).where(BillingCost.day >= first))
    lines = 0
    for bucket in buckets:
        day = datetime.fromisoformat(str(bucket["starting_at"]).replace("Z", "+00:00")).date()
        for result in bucket.get("results") or []:
            db.add(
                BillingCost(
                    day=day,
                    workspace_id=result.get("workspace_id"),
                    description=str(result.get("description") or "")[:300],
                    cost_type=result.get("cost_type"),
                    model=result.get("model"),
                    token_type=result.get("token_type"),
                    # The report gives cents as a decimal string.
                    amount_usd=float(result.get("amount") or 0) / 100,
                )
            )
            lines += 1
    db.commit()
    return lines


def set_state(db: Session, key: str, value: str) -> None:
    row = db.get(BillingState, key)
    if row is None:
        db.add(BillingState(key=key, value=value))
    else:
        row.value = value
    db.commit()


def run_once(db: Session, fetch: FetchReport, now: datetime, sync_cost: bool) -> None:
    """One pass: reconcile what's ready, and the cost report when it's due. Errors are kept."""
    try:
        checked = reconcile(db, fetch, now)
        set_state(db, "usage_synced_at", _stamp(now))
        if checked:
            logger.info("Checked billed usage for %d runs and tailorings", checked)
        if sync_cost:
            lines = sync_costs(db, fetch, now.date())
            set_state(db, "cost_synced_at", _stamp(now))
            logger.info("Stored %d cost report lines", lines)
        set_state(db, "last_error", "")
    except BillingUnavailable as exc:
        db.rollback()
        set_state(db, "last_error", str(exc))


def main() -> None:
    settings = get_settings()
    configure_logging()
    stopping = False

    def stop(signum: int, frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    key = billing_key(settings)
    if not key:
        logger.warning("No ANTHROPIC_ADMIN_API_KEY or ANTHROPIC_API_KEY, so billing stays off")
    elif not settings.anthropic_admin_api_key:
        logger.info("Reading billing reports with ANTHROPIC_API_KEY")
    headers = {
        "x-api-key": key or "",
        "anthropic-version": _API_VERSION,
        "User-Agent": _USER_AGENT,
    }
    next_cost_sync = 0.0
    with httpx.Client(base_url=API_BASE, headers=headers, timeout=60.0) as client:
        fetch = http_fetcher(client)
        while not stopping:
            if key:
                due = time.monotonic() >= next_cost_sync
                try:
                    with SessionLocal() as db:
                        run_once(db, fetch, utcnow(), sync_cost=due)
                    if due:
                        next_cost_sync = time.monotonic() + settings.billing_cost_sync_seconds
                except SQLAlchemyError:
                    logger.exception("The billing worker couldn't reach the database")
            for _ in range(int(settings.billing_poll_seconds)):
                if stopping:
                    break
                time.sleep(1)
    logger.info("Billing worker stopped")


if __name__ == "__main__":
    main()
