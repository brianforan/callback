"""Works through Scout runs one at a time.

For each run: gather the user's fact sheet, baselines, and the jobs they already have or have
hidden, run Scout's one engine (app.scout.engine) with the run's settings, and
store the leads it returns with what the run cost. The engine's model calls go to the tailor
service. Each lead becomes a tracked posting, ready to apply with the user's best baseline. A run
records its real cost and stops before spending past its dollar ceiling. If the tailor service
can't be reached, the run fails and doesn't count against the user's runs for the day.

Runs as its own container from the API image: python -m app.scout.worker. It's separate
from the tailor worker so a run, which takes minutes, never holds up a tailoring.
"""

import logging
import uuid
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import date, datetime, timedelta
from typing import Any

import httpx
from pydantic import RootModel
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.app_config import get_value
from app.companies.catalog import (
    CatalogRecorder,
    lead_listing,
    record_kept,
    record_listing,
)
from app.companies.feeds import FeedJob, new_feed_client, read_feed
from app.companies.lists import note_lead
from app.config import Settings, get_settings
from app.db import SessionLocal, commit_retrying_deadlock
from app.listings.boards import RawListing
from app.listings.cache import KEEP_RESULTS, ListingStore, SqlListingStore
from app.listings.search import Listing, SearchResults, search_listings
from app.logs import configure_logging
from app.models import (
    FactSheet,
    HiddenListing,
    JobPosting,
    ScoutBrief,
    ScoutRun,
    TailorStatus,
    User,
    utcnow,
)
from app.profiles import profile_for_work
from app.schemas import ScoutServiceResult
from app.scout.briefs import (
    claim_next_brief,
    fail_brief,
    fail_stale_briefs,
    write_one_brief,
)
from app.scout.engine import CompanyStages, run_companies
from app.scout.leads import declined_jobs, passed_pages, track_lead
from app.scout.limits import ENGINE, LIMITS_BY_KEY
from app.scout.match import clean_levels
from app.scout.work import MAX_BASELINE_TEXT, scout_baselines
from app.workers import TailorUnavailable, claim_oldest, poll_forever, post_json, run_safely

logger = logging.getLogger(__name__)

Stage = Callable[[dict[str, Any]], dict[str, Any]]

# The tailor service refuses anything larger, so these are cut before sending. Baselines are
# cut in app.scout.work, which reads them the same way for the work boundary.
_MAX_KNOWN = 300
_MAX_HIDDEN = 300
_MAX_DECLINED = 50
_MAX_PASSED = 200
# Extra minutes a running run gets beyond its time limit before it counts as abandoned.
_STALE_MARGIN = timedelta(minutes=10)
# Seconds one model call or job board search may take before it counts as failed.
STAGE_TIMEOUT_SECONDS = 300
# Seconds a run waits for the job boards' listings to be recorded in the catalog.
RECORD_WAIT_SECONDS = 120.0
# Seconds a run's deep job board searches get to answer. A deep LinkedIn search reads each job's
# page a few seconds apart. A board still searching after this is named as unavailable, and its
# listings reach the catalog for the next run.
BOARD_DEADLINE_SECONDS = 180.0


def default_minutes() -> int:
    """A run's time limit when its settings don't say: the longest allowed."""
    return LIMITS_BY_KEY["max_minutes"].maximum


_NOT_COUNTED = "It doesn't count against today's runs."


def claim_next(db: Session) -> ScoutRun | None:
    """Take the oldest queued run."""
    return claim_oldest(db, ScoutRun)


def _fail(run: ScoutRun, message: str) -> None:
    run.status = TailorStatus.FAILED.value
    run.stop_code = "failed"
    run.stop_message = message
    run.finished_at = utcnow()


def fail_stale(db: Session, now: datetime) -> int:
    """Fail runs left running by a worker that stopped, so they don't count against the day."""
    running = db.scalars(
        select(ScoutRun).where(ScoutRun.status == TailorStatus.RUNNING.value)
    ).all()
    failed = 0
    for run in running:
        minutes = (run.settings or {}).get("limits", {}).get("max_minutes", default_minutes())
        started = run.started_at
        if started is not None and started.tzinfo is None:
            started = started.replace(tzinfo=now.tzinfo)
        if started is not None and started < now - timedelta(minutes=minutes) - _STALE_MARGIN:
            logger.warning("Scout run %s was abandoned while running", run.id)
            _fail(run, f"The run stopped before it finished. {_NOT_COUNTED}")
            failed += 1
    db.commit()
    return failed


def _run_profile(db: Session, run: ScoutRun) -> uuid.UUID | None:
    """The profile a run searches for: its own, or the user's first when that one is gone."""
    user = db.get(User, run.user_id)
    return profile_for_work(db, user, run.profile_id).id if user else run.profile_id


def build_payload(db: Session, run: ScoutRun, today: date) -> dict[str, Any]:
    """Everything the agent needs, read now, so it uses the current fact sheet and baselines."""
    settings = run.settings or {}
    profile_id = _run_profile(db, run)
    sheet = db.scalar(select(FactSheet).where(FactSheet.profile_id == profile_id))
    baselines = scout_baselines(db, profile_id)
    postings = db.scalars(
        select(JobPosting)
        .where(JobPosting.user_id == run.user_id)
        .order_by(JobPosting.seen_on.desc(), JobPosting.id)
        .limit(_MAX_KNOWN)
    ).all()
    hidden = db.scalars(select(HiddenListing).where(HiddenListing.user_id == run.user_id)).all()
    known = [{"company": p.company, "title": p.title, "url": p.url} for p in postings]
    known += [
        {"company": h.company, "title": h.title, "url": h.url}
        for h in hidden
        if h.kind == "listing"
    ]
    stack: dict[str, Any] = {}
    # Runs started before Must use and Rule out existed have neither, and read them from the
    # description, as their settings did.
    for key in ("must_have", "rule_out"):
        if isinstance(settings.get(key), list):
            stack[key] = settings[key]
    return {
        **stack,
        "levels": clean_levels(settings.get("levels") or []),
        "description": settings.get("description", ""),
        "title_families": settings.get("title_families", []),
        "work_modes": settings.get("work_modes", []),
        "location": settings.get("location", ""),
        "posted_within_days": settings.get("posted_within_days"),
        "fit_mode": settings.get("fit_mode", "balanced"),
        "agency_mode": settings.get("agency_mode", "include"),
        "relocation": settings.get("relocation", "none"),
        "limits": settings.get("limits", {}),
        # Fixed when the run was queued; a run queued before that uses today's ceiling.
        "budget_usd": float(settings.get("budget_usd") or get_value(db, "smartsearch_run_max_usd")),
        "fact_sheet": sheet.content if sheet else "",
        "baselines": [
            {"label": resume.label[:120], "text": resume.plain_text[:MAX_BASELINE_TEXT]}
            for resume in baselines
        ],
        "known_jobs": known[:_MAX_KNOWN],
        "hidden_companies": [h.company for h in hidden if h.kind == "company"][:_MAX_HIDDEN],
        "declined": declined_jobs(db, run.user_id, _MAX_DECLINED),
        "passed_pages": passed_pages(db, run, _MAX_PASSED),
        "today": today.isoformat(),
    }


def _track_leads(db: Session, run: ScoutRun, leads: list[dict[str, Any]], today: date) -> None:
    """Make each lead a posting, ready to apply with the best baseline. One failure skips one."""
    user = db.get(User, run.user_id)
    if user is None:
        return
    profile = profile_for_work(db, user, run.profile_id)
    for lead in leads:
        try:
            with db.begin_nested():
                lead["posting_id"] = str(track_lead(db, user, profile, lead, today).id)
        except SQLAlchemyError:
            logger.exception("Couldn't track lead %s from Scout run %s", lead["id"], run.id)


def save_result(db: Session, run: ScoutRun, result: ScoutServiceResult, today: date) -> None:
    leads = [
        {**lead.model_dump(), "id": uuid.uuid4().hex, "posting_id": None} for lead in result.leads
    ]
    _track_leads(db, run, leads, today)
    run.leads = leads
    run.steps = [step.model_dump() for step in result.steps]
    run.cost_breakdown = dict(result.cost_breakdown)
    run.request_costs = list(result.request_costs)
    run.searches = result.searches
    run.pages = result.pages
    run.usage = result.usage.model_dump()
    run.cost_usd = result.cost_usd
    run.stop_code = result.stop_code
    run.stop_message = result.stop_message
    run.status = TailorStatus.DONE.value
    run.finished_at = utcnow()


class _Answer(RootModel[dict[str, Any]]):
    """A stage's answer, passed on as it came. The pipeline reads the fields it needs."""


def _listing_page(listing: Listing) -> dict[str, Any]:
    """A job board listing as a posting the pipeline can judge, in the shape tailor reads pages."""
    parsed = listing.parsed
    salary = (
        f"${parsed.salary_min:,} - ${parsed.salary_max:,}"
        if parsed.salary_min is not None and parsed.salary_max is not None
        else ""
    )
    return {
        "url": listing.url,
        "ok": True,
        "source": listing.source,
        "title": listing.title,
        "company": listing.company,
        "location": listing.location or "",
        "work_mode": parsed.work_mode.value if parsed.work_mode else "",
        "employment_type": parsed.employment_type.value if parsed.employment_type else "",
        "salary": salary,
        "posted_at": listing.published_on.isoformat() if listing.published_on else "",
        "text": listing.description,
    }


class _WaitingRecorder(CatalogRecorder):
    """Records job board results in the catalog as Job listings does, and keeps each batch's
    future, so a run can wait until they're in the catalog before it scans it."""

    def __init__(self, recorder: CatalogRecorder) -> None:
        # The same session factory and the same one thread, so batches stay in order.
        self.session_factory = recorder.session_factory
        self._executor = recorder._executor
        self.pending: list[Future[int]] = []

    def submit(self, board: str, raws: list[RawListing]) -> Future[int]:
        future = super().submit(board, raws)
        self.pending.append(future)
        return future

    def wait(self, timeout: float) -> None:
        for future in self.pending:
            try:
                future.result(timeout=timeout)
            except FutureTimeout:
                logger.warning("Job board listings were still being recorded after %ss", timeout)
                return
        self.pending.clear()


def board_stage(
    settings: Settings, store: ListingStore, recorder: CatalogRecorder | None = None
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Searches the same job boards as Job listings, through its cache, deeply and for every
    query at once.

    The location is what Job listings understands ("us", or none), worked out by the engine
    from the run's Location. A board that fails or is still loading is named in failed. With a
    recorder, it returns once what the boards sent is in the catalog.
    """

    def search(payload: dict[str, Any]) -> dict[str, Any]:
        pages: list[dict[str, Any]] = []
        failed: list[str] = []
        seen: set[str] = set()
        location = str(payload.get("location") or "")
        queries = [str(query) for query in payload.get("queries") or []]
        waiting = _WaitingRecorder(recorder) if recorder is not None else None

        def one(query: str) -> SearchResults:
            return search_listings(
                settings,
                query,
                location,
                store,
                waiting,
                deep=True,
                deadline_seconds=BOARD_DEADLINE_SECONDS,
            )

        with ThreadPoolExecutor(
            max_workers=max(len(queries), 1), thread_name_prefix="scout-boards"
        ) as pool:
            answers = list(pool.map(one, queries))
        for results in answers:
            for source in results.sources:
                if source.state in ("failed", "pending") and source.name not in failed:
                    failed.append(source.name)
            for listing in results.listings:
                if listing.url not in seen:
                    seen.add(listing.url)
                    pages.append(_listing_page(listing))
        if waiting is not None:
            waiting.wait(RECORD_WAIT_SECONDS)
        logger.info(
            "Scout board search: %d listings for %d queries, %d boards unavailable",
            len(pages),
            len(queries),
            len(failed),
        )
        return {"pages": pages, "failed": failed}

    return search


def _stage(client: httpx.Client, path: str, extra: dict[str, Any] | None = None) -> Stage:
    def call(payload: dict[str, Any]) -> dict[str, Any]:
        return post_json(client, path, {**payload, **(extra or {})}, _Answer, "search service").root

    return call


def http_company_stages(
    client: httpx.Client, feeds: httpx.Client, boards: Stage | None = None
) -> CompanyStages:
    def board(system: str, token: str) -> list[FeedJob]:
        return read_feed(feeds, system, token)

    return CompanyStages(
        judge=_stage(client, "/pipeline/judge", {"tier": "screen"}),
        verify=_stage(client, "/pipeline/judge", {"tier": "verify"}),
        same_company=_stage(client, "/companies/same_company"),
        work=_stage(client, "/pipeline/work"),
        discover=_stage(client, "/companies/discover"),
        careers=_stage(client, "/companies/careers"),
        read_feed=board,
        boards=boards,
    )


def record_run(db: Session, run: ScoutRun) -> None:
    """Record what a finished run kept in the catalog. A failure here is logged and leaves the
    run as it was saved."""
    leads = list(run.leads or [])
    user_id, profile_id = run.user_id, run.profile_id

    def record_leads() -> int:
        recorded = 0
        for lead in leads:
            try:
                job = record_listing(db, lead_listing(lead))
            except ValueError as exc:
                logger.info("Skipped a lead for the catalog: %s", exc)
                continue
            if job is None:
                continue
            recorded += 1
            record_kept(db, lead.get("url"))
            # The lead's company joins the profile's list, so Companies runs read it.
            note_lead(db, user_id, profile_id, job.company_id)
        return recorded

    try:
        recorded = commit_retrying_deadlock(db, record_leads)
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Couldn't record Scout run %s in the catalog", run.id)
        return
    logger.info("Recorded %d listings from Scout run %s in the catalog", recorded, run.id)


def run_one(db: Session, run: ScoutRun, today: date, stages: CompanyStages) -> None:
    """One run, whatever engine its settings name: Scout has one engine now, and a run
    queued before that still runs on it."""
    payload = build_payload(db, run, today)
    profile_id = _run_profile(db, run)
    if (run.settings or {}).get("engine") != ENGINE:
        run.settings = {**(run.settings or {}), "engine": ENGINE}
    # End the read transaction, so nothing is held open during the run.
    db.commit()
    try:
        result = run_companies(db, run.user_id, profile_id, payload, stages, today, run_id=run.id)
    except TailorUnavailable as exc:
        _fail(run, f"{exc} {_NOT_COUNTED}")
        if exc.spent:
            # An outage doesn't count against the day, but what the run measured stays on Spend.
            run.cost_usd = float(exc.spent.get("cost_usd") or 0.0)
            run.usage = dict(exc.spent.get("usage") or {})
            run.cost_breakdown = dict(exc.spent.get("cost_breakdown") or {})
            run.request_costs = list(exc.spent.get("request_costs") or [])
        db.commit()
        return
    save_result(db, run, result, today)
    db.commit()
    logger.info(
        "Scout run %s finished (%s): %d leads, $%.3f",
        run.id,
        run.stop_code,
        len(run.leads),
        run.cost_usd,
    )
    # After the run is saved, so recording can't lose or delay it.
    record_run(db, run)


def _run_safely(db: Session, run: ScoutRun, stages: CompanyStages) -> None:
    run_safely(
        db,
        f"Scout run {run.id}",
        lambda: run_one(db, run, date.today(), stages),
        lambda: _fail(run, f"Something went wrong during the run. {_NOT_COUNTED}"),
    )


def write_brief_safely(db: Session, brief: ScoutBrief, ask: Stage) -> None:
    """Write one claimed brief. A bug marks it failed, with nothing charged."""

    def work() -> None:
        run = db.get(ScoutRun, brief.run_id)
        if run is None:
            fail_brief(db, brief)
            db.commit()
            return
        payload = build_payload(db, run, date.today())
        write_one_brief(db, brief, ask, payload)

    run_safely(db, f"Scout brief {brief.id}", work, lambda: fail_brief(db, brief))


def main() -> None:
    settings = get_settings()
    configure_logging()
    logger.info("Scout worker started, sending model calls to %s", settings.tailor_url)
    with httpx.Client(base_url=settings.tailor_url, timeout=STAGE_TIMEOUT_SECONDS) as client:
        store = SqlListingStore(SessionLocal, KEEP_RESULTS)
        recorder = CatalogRecorder(SessionLocal)
        feeds = new_feed_client()
        stages = http_company_stages(client, feeds, board_stage(settings, store, recorder))
        brief = _stage(client, "/pipeline/brief")

        def step() -> bool:
            with SessionLocal() as db:
                fail_stale(db, utcnow())
                fail_stale_briefs(db, utcnow())
                # Briefs take seconds and runs take minutes, so a brief never waits for a run.
                claimed = claim_next_brief(db)
                if claimed is not None:
                    write_brief_safely(db, claimed, brief)
                    return True
                run = claim_next(db)
                if run is None:
                    return False
                _run_safely(db, run, stages)
                return True

        poll_forever("Scout worker", settings.tailor_poll_seconds, step)


if __name__ == "__main__":
    main()
