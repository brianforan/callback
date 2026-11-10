import uuid
from datetime import date
from http import HTTPStatus

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session, load_only, selectinload

from app.analytics import related_postings, stack_coverage
from app.companies.lists import note_saved_posting
from app.deps import CurrentProfile, CurrentUser, Db
from app.models import Application, FactSheet, JobPosting, Profile, Resume, User, utcnow
from app.postings.parser import Vocabulary
from app.postings.store import (
    apply_parse,
    clear_stage,
    create_posting,
    find_by_url,
    is_placeholder,
    load_match_profile,
    update_posting,
)
from app.postings.text import find_text, link_or_text_edited
from app.resumes.matching import JobFacts, MatchProfile, facts_from_posting, score_job
from app.resumes.variants import load_baselines, pick_baseline
from app.schemas import (
    MatchEstimate,
    PostingCreate,
    PostingDetail,
    PostingOut,
    PostingUpdate,
    StageRequest,
)
from app.scout.limits import SCOUT_SOURCE
from app.tailoring.rules import fact_sheet_skills, tailor_advice
from app.vocabulary import user_vocabulary

router = APIRouter(prefix="/api/postings", tags=["postings"])


def _base_query(user: User):
    return (
        select(JobPosting)
        .where(JobPosting.user_id == user.id)
        .options(
            selectinload(JobPosting.technologies),
            selectinload(JobPosting.applications).selectinload(Application.resume),
            selectinload(JobPosting.staged_resume),
        )
    )


def _get_owned(db: Session, user: User, posting_id: uuid.UUID) -> JobPosting:
    posting = db.scalar(_base_query(user).where(JobPosting.id == posting_id))
    if posting is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Posting not found")
    return posting


def _to_out(posting: JobPosting, vocabulary: Vocabulary) -> PostingOut:
    summary = PostingOut.model_validate(posting)
    summary.coverage = stack_coverage(posting, vocabulary)
    summary.skills_updating = posting.skills_version < vocabulary.version
    summary.placeholder = is_placeholder(posting)
    summary.staged_resume_label = posting.staged_resume.label if posting.staged_resume else None
    summary.from_scout = posting.source == SCOUT_SOURCE
    return summary


def _staged_match(
    db: Session, profile: MatchProfile | None, job: JobFacts, posting: JobPosting
) -> MatchEstimate | None:
    """The staged resume's score, worked out in the profile it belongs to.

    Saved jobs are shared by every profile, so the staged resume may be another profile's.
    It's then scored against that profile's resumes and applications, as it was when staged.
    """
    staged = posting.staged_resume
    if staged is None:
        return None
    if profile is not None and staged.id in profile.structures:
        return score_job(profile, job, only=staged)
    own = load_match_profile(db, staged.profile_id, exclude_posting_id=posting.id)
    if own is None or staged.id not in own.structures:
        return None
    return score_job(own, job, only=staged)


def _related_candidates(user: User):
    """The account's postings with only what related_postings compares: stack and text."""
    return (
        select(JobPosting)
        .where(JobPosting.user_id == user.id)
        .options(
            load_only(
                JobPosting.id,
                JobPosting.company,
                JobPosting.title,
                JobPosting.level,
                JobPosting.raw_text,
            ),
            selectinload(JobPosting.technologies),
        )
    )


def _to_detail(db: Session, user: User, active: Profile, posting: JobPosting) -> PostingDetail:
    """Matched against the active profile's resumes, baselines and fact sheet."""
    others = db.scalars(_related_candidates(user)).all()
    vocabulary = user_vocabulary(db, user.id)
    summary = _to_out(posting, vocabulary)
    profile = load_match_profile(
        db, active.id, exclude_posting_id=posting.id, vocabulary=vocabulary
    )
    job = facts_from_posting(posting, vocabulary)
    # The match profile already holds every resume in the active profile.
    baselines = load_baselines(profile.resumes if profile else [], date.today(), vocabulary)
    pick = pick_baseline(baselines, job) if baselines else None
    fact_sheet = db.scalar(select(FactSheet.content).where(FactSheet.profile_id == active.id))
    return PostingDetail(
        **summary.model_dump(),
        raw_text=posting.raw_text,
        related=related_postings(posting, others),
        match=score_job(profile, job) if profile else None,
        baseline=pick,
        staged_match=_staged_match(db, profile, job, posting),
        tailor_advice=tailor_advice(baselines, pick, job, fact_sheet_skills(fact_sheet)),
    )


def _reload_detail(
    db: Session, user: User, active: Profile, posting_id: uuid.UUID
) -> PostingDetail:
    db.expire_all()
    return _to_detail(db, user, active, _get_owned(db, user, posting_id))


@router.get("", response_model=list[PostingOut])
def list_postings(user: CurrentUser, db: Db) -> list[PostingOut]:
    query = _base_query(user).order_by(JobPosting.seen_on.desc(), JobPosting.created_at.desc())
    vocabulary = user_vocabulary(db, user.id)
    return [_to_out(posting, vocabulary) for posting in db.scalars(query).all()]


@router.post("", response_model=PostingDetail, status_code=status.HTTP_201_CREATED)
def add_posting(
    payload: PostingCreate,
    response: Response,
    user: CurrentUser,
    profile: CurrentProfile,
    db: Db,
) -> PostingDetail:
    # A URL seen before returns the existing posting, so one job stays one row.
    existing = find_by_url(db, user, payload.url)
    if existing:
        response.status_code = status.HTTP_200_OK
        return _reload_detail(db, user, profile, existing.id)
    posting = create_posting(
        db,
        user,
        profile_id=profile.id,
        company=payload.company,
        title=payload.title,
        seen_on=payload.seen_on or date.today(),
        url=payload.url,
        location=payload.location,
        source=payload.source,
        raw_text=payload.raw_text,
        referrer=payload.referrer,
    )
    db.flush()
    note_saved_posting(db, posting)
    find_text(db, posting)
    db.commit()
    return _reload_detail(db, user, profile, posting.id)


@router.get("/{posting_id}", response_model=PostingDetail)
def get_posting(
    posting_id: uuid.UUID, user: CurrentUser, profile: CurrentProfile, db: Db
) -> PostingDetail:
    return _to_detail(db, user, profile, _get_owned(db, user, posting_id))


@router.patch("/{posting_id}", response_model=PostingDetail)
def edit_posting(
    posting_id: uuid.UUID,
    payload: PostingUpdate,
    user: CurrentUser,
    profile: CurrentProfile,
    db: Db,
) -> PostingDetail:
    posting = _get_owned(db, user, posting_id)
    changes = payload.model_dump(exclude_unset=True)
    salary_min = changes.get("salary_min", posting.salary_min)
    salary_max = changes.get("salary_max", posting.salary_max)
    if salary_min is not None and salary_max is not None and salary_min > salary_max:
        raise HTTPException(
            HTTPStatus.UNPROCESSABLE_CONTENT, "Minimum salary can't be above the maximum"
        )
    if changes.get("url"):
        duplicate = find_by_url(db, user, changes["url"])
        if duplicate and duplicate.id != posting.id:
            raise HTTPException(status.HTTP_409_CONFLICT, "Another posting already has this URL")
    old_url, old_text = posting.url, posting.raw_text
    update_posting(posting, changes, user_vocabulary(db, user.id))
    if changes.get("url"):
        note_saved_posting(db, posting)
    # The form sends the link with every save, so only a real change counts.
    if posting.url != old_url or posting.raw_text != old_text:
        link_or_text_edited(db, posting)
    db.commit()
    return _reload_detail(db, user, profile, posting_id)


@router.post("/{posting_id}/fetch-text", response_model=PostingDetail)
def fetch_posting_text(
    posting_id: uuid.UUID, user: CurrentUser, profile: CurrentProfile, db: Db
) -> PostingDetail:
    """Try again to get the text of a posting saved with a link and none: the catalog, else a
    read of the link by the worker."""
    posting = _get_owned(db, user, posting_id)
    if not posting.url:
        raise HTTPException(HTTPStatus.UNPROCESSABLE_CONTENT, "This posting has no link to read")
    if posting.raw_text.strip():
        raise HTTPException(status.HTTP_409_CONFLICT, "This posting already has its text")
    find_text(db, posting)
    db.commit()
    return _reload_detail(db, user, profile, posting_id)


@router.post("/{posting_id}/reparse", response_model=PostingDetail)
def reparse_posting(
    posting_id: uuid.UUID, user: CurrentUser, profile: CurrentProfile, db: Db
) -> PostingDetail:
    apply_parse(_get_owned(db, user, posting_id), user_vocabulary(db, user.id))
    db.commit()
    return _reload_detail(db, user, profile, posting_id)


@router.post("/{posting_id}/stage", response_model=PostingDetail)
def stage_posting(
    posting_id: uuid.UUID,
    payload: StageRequest,
    user: CurrentUser,
    profile: CurrentProfile,
    db: Db,
) -> PostingDetail:
    """Mark a posting ready to apply, with the resume to send. Staging again replaces both."""
    posting = _get_owned(db, user, posting_id)
    if posting.applications:
        raise HTTPException(status.HTTP_409_CONFLICT, "You've already applied to this posting")
    resume = db.scalar(
        select(Resume).where(Resume.id == payload.resume_id, Resume.user_id == user.id)
    )
    if resume is None:
        raise HTTPException(HTTPStatus.UNPROCESSABLE_CONTENT, "Unknown resume")
    posting.staged_at = utcnow()
    posting.staged_resume_id = resume.id
    posting.staged_notes = (payload.notes or "").strip() or None
    db.commit()
    return _reload_detail(db, user, profile, posting_id)


@router.delete("/{posting_id}/stage", response_model=PostingDetail)
def unstage_posting(
    posting_id: uuid.UUID, user: CurrentUser, profile: CurrentProfile, db: Db
) -> PostingDetail:
    clear_stage(_get_owned(db, user, posting_id))
    db.commit()
    return _reload_detail(db, user, profile, posting_id)


@router.delete("/{posting_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_posting(posting_id: uuid.UUID, user: CurrentUser, db: Db) -> None:
    db.delete(_get_owned(db, user, posting_id))
    db.commit()
