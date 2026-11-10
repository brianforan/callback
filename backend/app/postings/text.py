"""Posting text for postings saved with a link and no text (docs/rejection_research_spec.md,
chunk 1). No model calls.

When a posting is saved, the shared catalog is tried first, in the same request: a job listed
at the same link already has its text. Otherwise the posting waits as pending, and the tailor
worker reads the link through the tailor service's fetcher, one posting at a time. Text that
arrives this way, or is pasted, sends a rejection report written without it back to the queue.
"""

import logging
import uuid
from collections.abc import Callable

import httpx
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.companies.catalog import catalog_url
from app.models import (
    Application,
    CatalogJob,
    CatalogJobSource,
    JobPosting,
    PostingTextStatus,
    RejectAnalysis,
    TailorStatus,
    utcnow,
)
from app.postings.store import apply_parse
from app.research.analysis import NO_TEXT_NOTE, requeue
from app.vocabulary import user_vocabulary
from app.workers import post_json

logger = logging.getLogger(__name__)

# Shorter text is a snippet or a page that needs a browser, not a posting.
MIN_TEXT_CHARS = 500
# The most a posting keeps, as PostingText allows when it's pasted.
MAX_TEXT_CHARS = 50_000
MAX_ERROR_CHARS = 300
SHORT_TEXT_ERROR = (
    "The page showed too little text to be the job. It may need JavaScript to show it."
)


class FetchedText(BaseModel):
    """What the tailor service read from a link. Its other fields are ignored."""

    ok: bool
    text: str = ""
    error: str = ""


FetchText = Callable[[str], FetchedText]


def http_text_fetcher(client: httpx.Client) -> FetchText:
    def fetch(url: str) -> FetchedText:
        return post_json(client, "/postings/fetch", {"url": url}, FetchedText)

    return fetch


def requeue_unread_analyses(db: Session, posting_id: uuid.UUID) -> int:
    """Queue again each rejection report that was written without this posting's text.

    Free of the once-a-day rule for re-runs, since the report was missing its main input.
    """
    analyses = db.scalars(
        select(RejectAnalysis)
        .join(Application, Application.id == RejectAnalysis.application_id)
        .where(
            Application.posting_id == posting_id,
            RejectAnalysis.status == TailorStatus.DONE.value,
            RejectAnalysis.note == NO_TEXT_NOTE,
        )
    ).all()
    for analysis in analyses:
        requeue(analysis)
        logger.info("Queued rejection analysis %s again, now its posting has text", analysis.id)
    return len(analyses)


def link_or_text_edited(db: Session, posting: JobPosting) -> None:
    """After the user changed the posting's link or text. Pasted text ends any read and lets
    a report written without text be written again; a new link with no text is looked for."""
    posting.text_status = None
    posting.text_error = None
    if posting.raw_text.strip():
        requeue_unread_analyses(db, posting.id)
    else:
        find_text(db, posting)


def _keep_text(db: Session, posting: JobPosting, text: str, fetched: bool) -> None:
    posting.raw_text = text[:MAX_TEXT_CHARS]
    apply_parse(posting, user_vocabulary(db, posting.user_id))
    posting.text_status = PostingTextStatus.FETCHED.value if fetched else None
    posting.text_error = None
    if fetched:
        posting.text_fetched_at = utcnow()
    requeue_unread_analyses(db, posting.id)


def catalog_text(db: Session, url: str | None) -> str | None:
    """The catalog's text for the job listed at this link, when it's long enough to keep."""
    link = catalog_url(url)
    if link is None:
        return None
    text = db.scalar(
        select(CatalogJob.text)
        .join(CatalogJobSource, CatalogJobSource.job_id == CatalogJob.id)
        .where(CatalogJobSource.url == link)
    )
    text = (text or "").strip()
    return text if len(text) >= MIN_TEXT_CHARS else None


def find_text(db: Session, posting: JobPosting) -> None:
    """For a posting with a link and no text: the catalog's copy now, else wait for the worker
    to read the link. Changes the session only."""
    if not posting.url or posting.raw_text.strip():
        return
    text = catalog_text(db, posting.url)
    if text is not None:
        # A posting just made has no id until it's flushed, and requeueing needs one.
        db.flush()
        _keep_text(db, posting, text, fetched=False)
        logger.info("Copied posting %s's text from the catalog", posting.id)
        return
    posting.text_status = PostingTextStatus.PENDING.value
    posting.text_error = None


def fetch_next_text(db: Session, fetch: FetchText) -> bool:
    """Read one pending posting's link. False when none is waiting.

    Nothing is held open during the read. The result is kept only if the posting still waits
    for it at the same link, so text pasted meanwhile wins. A tailor outage raises
    TailorUnavailable and leaves the posting pending for later.
    """
    row = db.execute(
        select(JobPosting.id, JobPosting.url)
        .where(JobPosting.text_status == PostingTextStatus.PENDING.value)
        .order_by(JobPosting.updated_at, JobPosting.id)
        .limit(1)
    ).first()
    db.commit()
    if row is None:
        return False
    posting_id, url = row
    result = fetch(url) if url else FetchedText(ok=False, error="The posting has no link.")
    posting = db.scalar(
        select(JobPosting)
        .where(JobPosting.id == posting_id)
        .options(selectinload(JobPosting.technologies))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        posting is None
        or posting.text_status != PostingTextStatus.PENDING.value
        or posting.url != url
        or posting.raw_text.strip()
    ):
        logger.info(
            "Posting %s changed while its link was read, so the result is dropped", posting_id
        )
        db.rollback()
        return True
    text = result.text.strip() if result.ok else ""
    if len(text) >= MIN_TEXT_CHARS:
        _keep_text(db, posting, text, fetched=True)
        logger.info("Read posting %s's text from its link", posting_id)
    else:
        posting.text_status = PostingTextStatus.FAILED.value
        error = SHORT_TEXT_ERROR if result.ok else result.error or "The page couldn't be read."
        posting.text_error = error[:MAX_ERROR_CHARS]
        logger.info("Couldn't read posting %s's text: %s", posting_id, posting.text_error)
    db.commit()
    return True


def queue_missing_texts(db: Session, user_id: uuid.UUID) -> tuple[int, int]:
    """For an account's postings saved with a link and no text before this existed: copy each
    one's text from the catalog, or mark it to be read. Failed reads are tried again. Returns
    how many were copied and how many now wait. Commits."""
    postings = db.scalars(
        select(JobPosting)
        .where(
            JobPosting.user_id == user_id,
            JobPosting.url.is_not(None),
            func.trim(JobPosting.raw_text) == "",
            JobPosting.text_status.is_distinct_from(PostingTextStatus.PENDING.value),
        )
        .options(selectinload(JobPosting.technologies))
    ).all()
    copied = 0
    for posting in postings:
        find_text(db, posting)
        copied += posting.text_status is None
    db.commit()
    return copied, len(postings) - copied
