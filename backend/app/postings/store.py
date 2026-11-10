"""Creates and updates job postings, keeping the parser away from fields the user corrected."""

import logging
import uuid
from collections.abc import Iterable
from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    Application,
    JobPosting,
    PostingTechnology,
    Profile,
    Requirement,
    Resume,
    User,
)
from app.postings.parser import (
    ParsedTechnology,
    Vocabulary,
    canonical_url,
    company_key,
    parse_posting,
)
from app.resumes.matching import MatchProfile, build_profile, facts_from_posting, score_job
from app.vocabulary import user_vocabulary

logger = logging.getLogger(__name__)

TECHNOLOGIES_FIELD = "technologies"
PARSED_FIELDS = (
    "level",
    "role_family",
    "years_required",
    "salary_min",
    "salary_max",
    "work_mode",
    "employment_type",
    "source_kind",
    "referred",
    "referrer",
)
_REPARSE_TRIGGERS = {"title", "raw_text", "source", "location", "url"}
# Postings whose skills are read again in one go, so one worker step stays short.
REREAD_BATCH = 50


def _sync_technologies(posting: JobPosting, wanted: Iterable[ParsedTechnology]) -> None:
    """Update rows in place. Replacing them would insert before deleting and break uniqueness."""
    by_name = {technology.name: technology for technology in wanted}
    for row in list(posting.technologies):
        match = by_name.pop(row.name, None)
        if match is None:
            posting.technologies.remove(row)
        else:
            row.category = match.category
            row.requirement = match.requirement.value
    for technology in by_name.values():
        posting.technologies.append(
            PostingTechnology(
                name=technology.name,
                category=technology.category,
                requirement=technology.requirement.value,
            )
        )


def apply_parse(posting: JobPosting, vocabulary: Vocabulary) -> None:
    """Run the parser over a posting, leaving every field the user corrected untouched.

    vocabulary is the owner's (app.vocabulary.user_vocabulary), and its version is stamped on
    the posting so a later change to it reads the skills again.
    """
    parsed = parse_posting(
        posting.title,
        posting.raw_text or "",
        posting.source,
        posting.location,
        posting.url,
        vocabulary,
    )
    posting.skills_version = vocabulary.version
    corrected = set(posting.corrected_fields or [])
    for name in PARSED_FIELDS:
        if name not in corrected:
            setattr(posting, name, getattr(parsed, name))
    if TECHNOLOGIES_FIELD not in corrected:
        _sync_technologies(posting, parsed.technologies)


def find_by_url(db: Session, user: User, url: str | None) -> JobPosting | None:
    canonical = canonical_url(url)
    if canonical is None:
        return None
    return db.scalar(
        select(JobPosting).where(JobPosting.user_id == user.id, JobPosting.url == canonical)
    )


def clear_stage(posting: JobPosting) -> None:
    posting.staged_at = None
    posting.staged_resume_id = None
    posting.staged_notes = None


def is_placeholder(posting: JobPosting) -> bool:
    """A posting made only because an application needed one: no link, no text, no edits."""
    return (
        not posting.url
        and not posting.raw_text.strip()
        and set(posting.corrected_fields or []) <= {"referred", "referrer"}
    )


def set_referrer(posting: JobPosting, referrer: str) -> None:
    posting.referred = True
    posting.referrer = referrer
    posting.corrected_fields = sorted({*(posting.corrected_fields or []), "referred", "referrer"})


def create_posting(
    db: Session,
    user: User,
    *,
    profile_id: uuid.UUID | None,
    company: str,
    title: str,
    seen_on: date,
    url: str | None = None,
    location: str | None = None,
    source: str | None = None,
    raw_text: str | None = None,
    referrer: str | None = None,
) -> JobPosting:
    posting = JobPosting(
        user_id=user.id,
        profile_id=profile_id,
        company=company,
        company_key=company_key(company),
        title=title,
        url=canonical_url(url),
        location=location,
        source=source,
        raw_text=raw_text or "",
        corrected_fields=[],
        seen_on=seen_on,
    )
    apply_parse(posting, user_vocabulary(db, user.id))
    if referrer:
        set_referrer(posting, referrer)
    db.add(posting)
    return posting


def update_posting(posting: JobPosting, changes: dict[str, Any], vocabulary: Vocabulary) -> None:
    """Apply edits. A changed parsed field becomes a correction that later parses keep."""
    corrected = set(posting.corrected_fields or [])
    reparse = False

    names = changes.pop(TECHNOLOGIES_FIELD, None)
    if names is not None:
        current = {row.name: row.requirement for row in posting.technologies}
        wanted = {vocabulary.canonical(name) for name in names if name.strip()}
        if wanted != set(current):
            _sync_technologies(
                posting,
                [
                    ParsedTechnology(
                        name,
                        vocabulary.category(name),
                        Requirement(current.get(name, Requirement.MENTIONED)),
                    )
                    for name in wanted
                ],
            )
            corrected.add(TECHNOLOGIES_FIELD)

    for name, value in changes.items():
        if name == "url":
            value = canonical_url(value)
        if getattr(posting, name) == value:
            continue
        setattr(posting, name, value)
        if name in PARSED_FIELDS:
            corrected.add(name)
        elif name == "company":
            posting.company_key = company_key(value)
        elif name in _REPARSE_TRIGGERS:
            reparse = True

    posting.corrected_fields = sorted(corrected)
    if reparse:
        apply_parse(posting, vocabulary)


def load_match_profile(
    db: Session,
    profile_id: uuid.UUID,
    exclude_posting_id: uuid.UUID | None = None,
    exclude_resume_id: uuid.UUID | None = None,
    vocabulary: Vocabulary | None = None,
) -> MatchProfile | None:
    """One profile's resumes and the applications logged under it, reduced for matching.

    Leave out a posting's own applications when scoring it, or it would match itself. A resume
    can be left out too, so a recommendation doesn't pick one made for the job itself.
    Without a vocabulary, the profile owner's is loaded.
    """
    resume_query = select(Resume).where(Resume.profile_id == profile_id)
    if exclude_resume_id is not None:
        resume_query = resume_query.where(Resume.id != exclude_resume_id)
    resumes = db.scalars(resume_query).all()
    application_query = select(Application).where(Application.profile_id == profile_id)
    if exclude_posting_id is not None:
        # IS DISTINCT FROM, so applications with no posting are kept.
        application_query = application_query.where(
            Application.posting_id.is_distinct_from(exclude_posting_id)
        )
    applications = db.scalars(
        application_query.options(
            selectinload(Application.events),
            selectinload(Application.posting).selectinload(JobPosting.technologies),
        )
    ).all()
    if vocabulary is None:
        owner = db.scalar(select(Profile.user_id).where(Profile.id == profile_id))
        if owner is None:
            return None
        vocabulary = user_vocabulary(db, owner)
    return build_profile(resumes, applications, date.today(), vocabulary)


def recommend_resume(db: Session, application: Application) -> uuid.UUID | None:
    """The resume the matcher would have sent for this application's posting, or None.

    Chosen from the resumes of the profile it was logged under. The resume actually sent is
    left out of the choice when no other application uses it: a resume uploaded or tailored
    for this one job would otherwise always win its own comparison.
    """
    posting = application.posting
    if posting is None or application.profile_id is None:
        return None
    exclude = None
    if application.resume_id is not None:
        used_elsewhere = db.scalar(
            select(func.count())
            .select_from(Application)
            .where(
                Application.resume_id == application.resume_id,
                Application.id != application.id,
            )
        )
        if not used_elsewhere:
            exclude = application.resume_id
    profile = load_match_profile(
        db, application.profile_id, exclude_posting_id=posting.id, exclude_resume_id=exclude
    )
    if profile is None:
        return None
    match = score_job(profile, facts_from_posting(posting, profile.vocabulary))
    return match.best_resume.id if match else None


def sync_postings(db: Session, user: User) -> tuple[int, int]:
    """Create a posting for each application lacking one and re-parse all. Safe to repeat."""
    applications = db.scalars(
        select(Application)
        .where(Application.user_id == user.id, Application.posting_id.is_(None))
        .order_by(Application.applied_on)
    ).all()
    created = 0
    for application in applications:
        posting = find_by_url(db, user, application.job_url)
        if posting is None:
            posting = create_posting(
                db,
                user,
                profile_id=application.profile_id,
                company=application.company,
                title=application.role_title,
                seen_on=application.applied_on,
                url=application.job_url,
                location=application.location,
                source=application.source,
                raw_text=application.notes,
            )
            created += 1
        application.posting = posting
        db.flush()

    postings = db.scalars(
        select(JobPosting)
        .where(JobPosting.user_id == user.id)
        .options(selectinload(JobPosting.technologies))
    ).all()
    vocabulary = user_vocabulary(db, user.id)
    for posting in postings:
        apply_parse(posting, vocabulary)
    db.commit()
    logger.info("Created %d postings and re-parsed %d", created, len(postings))
    return created, len(postings)


def reread_skills(db: Session) -> int:
    """Read again a batch of postings parsed before their owner's skill terms changed.

    Returns how many were read, so 0 means none are behind. Corrected skills stay as they are.
    """
    postings = db.scalars(
        select(JobPosting)
        .join(User, JobPosting.user_id == User.id)
        .where(JobPosting.skills_version < User.vocabulary_version)
        .order_by(JobPosting.user_id, JobPosting.id)
        .limit(REREAD_BATCH)
        .options(selectinload(JobPosting.technologies))
    ).all()
    vocabularies: dict[uuid.UUID, Vocabulary] = {}
    for posting in postings:
        if posting.user_id not in vocabularies:
            vocabularies[posting.user_id] = user_vocabulary(db, posting.user_id)
        apply_parse(posting, vocabularies[posting.user_id])
    db.commit()
    if postings:
        logger.info("Read the skills of %d postings again", len(postings))
    return len(postings)
