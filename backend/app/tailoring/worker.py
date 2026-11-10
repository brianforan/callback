"""Works through tailoring jobs one at a time.

For each job: gather the baseline, posting, checklist gaps and fact sheet, send them to the tailor
service, check what comes back with plain rules, and on success save the resume and stage the
posting with it. A result that fails the checks is retried once with the reasons, then the job fails
with them. Nothing is ever submitted. With no tailoring waiting, it researches one queued company
(app.research.snapshot_worker), so a rejection's company is researched before its report; then it
runs one queued rejection analysis (app.research.analysis_worker); then it reads the text of one
posting saved with a link and no text (app.postings.text); then it reads again the skills of a batch
of postings parsed before their owner's skill terms changed (app.postings.store.reread_skills); and
with none of those, it compiles one resume that has no PDF yet, so every resume can be downloaded
as a PDF.

Runs as its own container from the API image: python -m app.tailoring.worker
"""

import base64
import binascii
import logging
import re
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select, update
from sqlalchemy.orm import Session, selectinload

from app.app_config import get_value
from app.config import get_settings
from app.db import SessionLocal
from app.logs import configure_logging
from app.models import FactSheet, JobPosting, Resume, TailorJob, TailorStatus, User, utcnow
from app.postings.store import reread_skills
from app.postings.text import fetch_next_text, http_text_fetcher
from app.profiles import profile_for_work
from app.research.analysis_worker import (
    claim_next_analysis,
    fail_stale_analyses,
    http_analyzer,
    run_analysis_safely,
)
from app.research.snapshot_worker import (
    claim_next_research,
    fail_stale_research,
    http_researcher,
    run_research_safely,
)
from app.resumes.matching import JobFacts, build_profile, facts_from_posting, score_job
from app.resumes.variants import load_baselines, pick_baseline
from app.routers.resumes import store_resume
from app.schemas import CompileServiceResult, TailorServiceResult
from app.tailoring.rules import (
    TAILORING_RULES,
    FactSheetSkills,
    check_job_lines,
    check_score,
    check_technologies,
    compare_scores,
    gap_lines,
    mix_lines,
    parse_skills_status,
    staged_notes,
    transient_resume,
)
from app.workers import (
    TailorUnavailable,
    claim_oldest,
    poll_forever,
    post_json,
    run_safely,
)

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 2
# The tailor and compiler services refuse anything larger, so the limits are checked before sending.
_MAX_POSTING_TEXT = 60_000
_MAX_BASELINE_TEX = 200_000
_MAX_GAPS = 100
_MAX_MIX = 50
_MAX_DETAIL = 300
# A retry's reasons, sent back as feedback: at most this many lines, each this long.
_MAX_FEEDBACK_LINES = 20
_MAX_FEEDBACK_CHARS = 2_000
# Extra minutes a running job gets beyond its attempts' timeouts before it counts as abandoned.
_STALE_MARGIN = timedelta(minutes=5)
_SLUG = re.compile(r"[^A-Za-z0-9]+")
_COMPILE_BACKOFF_SECONDS = 60.0
_FETCH_BACKOFF_SECONDS = 60.0


SendRequest = Callable[[dict[str, Any]], TailorServiceResult]
CompileTex = Callable[[str], CompileServiceResult]


def http_sender(client: httpx.Client) -> SendRequest:
    def send(payload: dict[str, Any]) -> TailorServiceResult:
        return post_json(client, "/tailor", payload, TailorServiceResult)

    return send


def http_compiler(client: httpx.Client) -> CompileTex:
    """Compile through the compiler service. Its client is separate from the tailor's."""

    def compile_tex(tex: str) -> CompileServiceResult:
        return post_json(client, "/compile", {"tex": tex}, CompileServiceResult, "compiler service")

    return compile_tex


def compile_next_pdf(db: Session, compile_tex: CompileTex) -> bool:
    """Compile one resume that has no PDF yet, baselines first. False when none is waiting.

    A resume whose LaTeX doesn't compile keeps the reason and isn't tried again until its
    source is edited. An outage raises TailorUnavailable and leaves it for later. A baseline
    edited while it compiled keeps no PDF, so the next call compiles the new source.
    """
    resume = db.scalar(
        select(Resume)
        .where(Resume.pdf_pages.is_(None), Resume.pdf_error.is_(None))
        .order_by(Resume.is_variant.desc(), Resume.created_at.desc(), Resume.id)
        .limit(1)
        # Read afresh, since an edit to a resume this session loaded before changes its source.
        .execution_options(populate_existing=True)
    )
    if resume is None:
        db.rollback()
        return False
    resume_id, source, digest = resume.id, resume.source_tex, resume.content_sha256
    if len(source) > _MAX_BASELINE_TEX:
        resume.pdf_error = "The .tex is too large to compile."
        db.commit()
        return True
    # End the read transaction, so nothing is held open during the compile.
    db.commit()
    result = compile_tex(source)
    pdf = _decode_pdf(result.pdf_base64) if result.ok else None
    if pdf is not None and result.pages:
        values: dict[str, Any] = {"pdf": pdf, "pdf_pages": result.pages}
    else:
        values = {"pdf_error": " ".join(result.reasons)[:_MAX_DETAIL] or "It didn't compile."}
    # Saved only if the source is still the one compiled, since a baseline can be edited meanwhile.
    saved = db.execute(
        update(Resume)
        .where(Resume.id == resume_id, Resume.content_sha256 == digest)
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    db.commit()
    if not saved.rowcount:
        logger.info("Resume %s changed while it compiled, so it will be compiled again", resume_id)
        return True
    logger.info("Compiled resume %s: %s", resume_id, values.get("pdf_error", "ok"))
    return True


def _decode_pdf(encoded: str | None) -> bytes | None:
    try:
        return base64.b64decode(encoded or "", validate=True) or None
    except (binascii.Error, ValueError):
        logger.error("The tailor service sent a PDF that isn't valid base64")
        return None


def claim_next(db: Session) -> TailorJob | None:
    """Take the oldest queued job."""
    return claim_oldest(db, TailorJob)


def _finish(job: TailorJob, status: TailorStatus, reasons: list[str]) -> None:
    job.status = status.value
    job.reasons = reasons
    job.finished_at = utcnow()


def fail_stale(db: Session, now: datetime, attempt_timeout: float) -> int:
    """Fail jobs left running by a worker that stopped, so they don't hang forever."""
    cutoff = now - timedelta(seconds=attempt_timeout * MAX_ATTEMPTS) - _STALE_MARGIN
    stale = db.scalars(
        select(TailorJob).where(
            TailorJob.status == TailorStatus.RUNNING.value, TailorJob.started_at < cutoff
        )
    ).all()
    for job in stale:
        logger.warning("Tailoring job %s was abandoned while running", job.id)
        _finish(job, TailorStatus.FAILED, ["Tailoring stopped before it finished. Try again."])
    db.commit()
    return len(stale)


@dataclass(frozen=True)
class _Inputs:
    payload: dict[str, Any]
    posting: JobPosting
    source: Resume
    baselines: list[Resume]
    facts: JobFacts
    skills: FactSheetSkills


def _gather(db: Session, job: TailorJob, today: date) -> _Inputs | str:
    """Everything one job needs, read now, or the reason it can't run."""
    if job.posting_id is None:
        return "The posting was deleted."
    posting = db.scalar(
        select(JobPosting)
        .where(JobPosting.id == job.posting_id, JobPosting.user_id == job.user_id)
        .options(selectinload(JobPosting.technologies), selectinload(JobPosting.applications))
    )
    if posting is None:
        return "The posting was deleted."
    if posting.applications:
        return "You've already applied to this posting."
    if not posting.raw_text.strip():
        return "The posting has no text to tailor to."
    source = db.get(Resume, job.baseline_id) if job.baseline_id else None
    if source is None or source.user_id != job.user_id:
        return "The baseline was deleted. Try again to use another."
    if source.source_format == "pdf":
        return "The baseline is a PDF. Tailoring needs a .tex baseline."
    if len(source.source_tex) > _MAX_BASELINE_TEX:
        return "The baseline's .tex is too large to tailor."
    user = db.get(User, job.user_id)
    if user is None:
        return "The account was deleted."
    # The profile the job was started under, so it uses that profile's claims and baselines.
    owner = profile_for_work(db, user, job.profile_id)
    # The fact sheet is optional. Without one, only the baselines back a claim.
    sheet = db.scalar(select(FactSheet).where(FactSheet.profile_id == owner.id))
    fact_sheet = sheet.content if sheet else ""

    resumes = db.scalars(select(Resume).where(Resume.profile_id == owner.id)).all()
    loaded = load_baselines(resumes, today)
    baselines = loaded.variants if loaded else []
    facts = facts_from_posting(posting)
    # Scored on its own against a profile of the baselines, as the reverse ATS report does.
    pool = list({resume.id: resume for resume in [*baselines, source]}.values())
    profile = build_profile(pool, [], today)
    match = score_job(profile, facts, only=source) if profile else None
    pick = pick_baseline(loaded, facts) if loaded else None
    mix = mix_lines(pick, baselines) if pick and pick.resume_id == source.id else []
    payload = {
        "baseline_tex": source.source_tex,
        "posting": {
            "company": posting.company,
            "title": posting.title,
            "text": posting.raw_text[:_MAX_POSTING_TEXT],
        },
        "gaps": gap_lines(match.checklist)[:_MAX_GAPS] if match else [],
        "mix": mix[:_MAX_MIX],
        "fact_sheet": fact_sheet,
        "rules": TAILORING_RULES,
        "feedback": [],
    }
    return _Inputs(
        payload=payload,
        posting=posting,
        source=source,
        baselines=baselines,
        facts=facts,
        skills=parse_skills_status(fact_sheet),
    )


def _check(inputs: _Inputs, result: TailorServiceResult, today: date) -> list[str]:
    """Plain-rule checks on a result. An empty list means it can be kept."""
    if not result.tex or not result.pdf_base64:
        return ["The tailoring service sent no resume."]
    others = [resume for resume in inputs.baselines if resume.id != inputs.source.id]
    reasons = check_job_lines(inputs.source.source_tex, result.tex, today)
    reasons += check_technologies(result.tex, inputs.source, others, inputs.skills)
    tailored = transient_resume(result.tex, inputs.source)
    reasons += check_score(
        compare_scores(inputs.baselines, inputs.source, tailored, inputs.facts, today)
    )
    return reasons


def _slug(text: str) -> str:
    return _SLUG.sub("-", text).strip("-")[:80] or "resume"


def _save(db: Session, job: TailorJob, inputs: _Inputs, result: TailorServiceResult) -> None:
    """Keep the result as a new resume and stage the posting with it."""
    pdf = _decode_pdf(result.pdf_base64)
    if pdf is None:
        _finish(job, TailorStatus.FAILED, ["The tailoring service sent a damaged PDF."])
        db.commit()
        return
    # Read the posting again: it may have been applied to or deleted during the call.
    posting = db.scalar(
        select(JobPosting)
        .where(JobPosting.id == inputs.posting.id)
        .options(selectinload(JobPosting.applications))
        .execution_options(populate_existing=True)
    )
    user = db.get(User, job.user_id)
    if user is None:
        # Deleting the user deletes the job too, so there's nothing left to record.
        logger.info("Tailoring job %s finished after its user was deleted", job.id)
        db.rollback()
        return
    if posting is None:
        # The job stays, with what it cost, but there's no posting to keep the result for.
        logger.info("Tailoring job %s finished after its posting was deleted", job.id)
        _finish(job, TailorStatus.FAILED, ["The posting was deleted while it was being tailored."])
        db.commit()
        return
    resume, _ = store_resume(
        db,
        profile_for_work(db, user, job.profile_id),
        filename=f"{_slug(posting.company)}-{_slug(posting.title)}.tex",
        source=result.tex or "",
        label=f"Tailored: {posting.company} - {posting.title}"[:120],
    )
    job.result_resume_id = resume.id
    job.result_pdf = pdf
    resume.pdf = pdf
    resume.pdf_pages = result.pages or 1
    resume.pdf_error = None
    if posting.applications:
        _finish(
            job,
            TailorStatus.DONE,
            ["You applied before tailoring finished, so the posting wasn't staged."],
        )
    else:
        posting.staged_at = utcnow()
        posting.staged_resume_id = resume.id
        posting.staged_notes = staged_notes(inputs.source.label, result.changelog, result.gaps)
        _finish(job, TailorStatus.DONE, [])
    db.commit()
    logger.info("Tailoring job %s finished as resume %s", job.id, resume.id)


def run_job(db: Session, job: TailorJob, send: SendRequest, today: date) -> None:
    inputs = _gather(db, job, today)
    if isinstance(inputs, str):
        _finish(job, TailorStatus.FAILED, [inputs])
        db.commit()
        return
    # End the read transaction, so nothing is held open during the slow call.
    db.commit()

    usage: Counter[str] = Counter(job.usage or {})
    feedback: list[str] = []
    reasons: list[str] = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        job.attempts = attempt
        db.commit()
        try:
            result = send({**inputs.payload, "feedback": feedback})
        except TailorUnavailable as exc:
            reasons = [str(exc)]
            if exc.spent:
                # A draft paid for before the error, such as when the compiler was down.
                usage.update(
                    {
                        name: int(count or 0)
                        for name, count in (exc.spent.get("usage") or {}).items()
                    }
                )
                job.usage = dict(usage)
                job.cost_usd = round(
                    (job.cost_usd or 0.0) + float(exc.spent.get("cost_usd") or 0.0), 6
                )
            continue
        usage.update(result.usage.model_dump())
        job.usage = dict(usage)
        job.cost_usd = round((job.cost_usd or 0.0) + result.cost_usd, 6)
        job.changelog = result.changelog
        job.gaps = result.gaps
        reasons = _check(inputs, result, today) if result.ok else result.reasons
        if not reasons:
            _save(db, job, inputs, result)
            return
        logger.info("Tailoring job %s attempt %d turned down: %s", job.id, attempt, reasons)
        feedback = [reason[:_MAX_FEEDBACK_CHARS] for reason in reasons[:_MAX_FEEDBACK_LINES]]
    _finish(job, TailorStatus.FAILED, reasons)
    db.commit()


def _run_safely(db: Session, job: TailorJob, send: SendRequest) -> None:
    run_safely(
        db,
        f"Tailoring job {job.id}",
        lambda: run_job(db, job, send, date.today()),
        lambda: _finish(
            job, TailorStatus.FAILED, ["Something went wrong while tailoring. Try again."]
        ),
    )


def main() -> None:
    settings = get_settings()
    configure_logging()
    logger.info(
        "Tailor worker started, sending jobs to %s and compiling at %s",
        settings.tailor_url,
        settings.compiler_url,
    )
    with (
        httpx.Client(
            base_url=settings.tailor_url, timeout=settings.tailor_timeout_seconds
        ) as tailor_client,
        httpx.Client(
            base_url=settings.compiler_url, timeout=settings.compiler_timeout_seconds
        ) as compiler_client,
    ):
        send = http_sender(tailor_client)
        analyze = http_analyzer(tailor_client)
        research = http_researcher(tailor_client)
        fetch_text = http_text_fetcher(tailor_client)
        compile_tex = http_compiler(compiler_client)
        # After an outage, reading links and compiling wait a while instead of retrying every
        # poll. Each has its own wait, since they use different services.
        fetch_after = 0.0
        compile_after = 0.0

        def step() -> bool:
            nonlocal compile_after, fetch_after
            try:
                with SessionLocal() as db:
                    fail_stale(db, utcnow(), settings.tailor_timeout_seconds)
                    fail_stale_analyses(db, utcnow(), settings.tailor_timeout_seconds)
                    fail_stale_research(db, utcnow(), settings.tailor_timeout_seconds)
                    job = claim_next(db)
                    if job is not None:
                        _run_safely(db, job, send)
                        return True
                    snapshot = claim_next_research(db)
                    if snapshot is not None:
                        run_research_safely(db, snapshot, research)
                        return True
                    analysis = claim_next_analysis(
                        db, utcnow(), int(get_value(db, "reject_analysis_daily_limit"))
                    )
                    if analysis is not None:
                        run_analysis_safely(db, analysis, analyze)
                        return True
                    if time.monotonic() >= fetch_after:
                        try:
                            if fetch_next_text(db, fetch_text):
                                return True
                        except TailorUnavailable:
                            db.rollback()
                            fetch_after = time.monotonic() + _FETCH_BACKOFF_SECONDS
                    if reread_skills(db):
                        return True
                    return time.monotonic() >= compile_after and compile_next_pdf(db, compile_tex)
            except TailorUnavailable:
                compile_after = time.monotonic() + _COMPILE_BACKOFF_SECONDS
                return False

        poll_forever("tailor worker", settings.tailor_poll_seconds, step)


if __name__ == "__main__":
    main()
