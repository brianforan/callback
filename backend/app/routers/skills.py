"""The active profile's skill vocabulary: the built-in software list switch and the user's own
terms (docs/skills_spec.md). Every change marks the user's postings to be read again."""

import logging
import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.deps import CurrentProfile, Db
from app.models import JobPosting, PostingTechnology, Profile, ProfileTerm
from app.postings.parser import TECHNOLOGY_NAMES
from app.schemas import SkillsOut, SkillsUpdate, SkillTermIn, SkillTermOut
from app.vocabulary import bump_vocabulary

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/skills", tags=["skills"])

# Enough for any one field's skills, few enough that parsing every posting stays quick.
MAX_TERMS = 300


def _out(db: Session, profile: Profile) -> SkillsOut:
    terms = db.scalars(
        select(ProfileTerm)
        .where(ProfileTerm.profile_id == profile.id)
        .order_by(func.lower(ProfileTerm.name))
    ).all()
    named_in = dict(
        db.execute(
            select(
                func.lower(PostingTechnology.name),
                func.count(func.distinct(PostingTechnology.posting_id)),
            )
            .join(JobPosting, PostingTechnology.posting_id == JobPosting.id)
            .where(JobPosting.user_id == profile.user_id)
            .group_by(func.lower(PostingTechnology.name))
        ).all()
    )
    return SkillsOut(
        profile_id=profile.id,
        software_skills=profile.software_skills,
        software_count=len(TECHNOLOGY_NAMES),
        terms=[
            SkillTermOut(
                id=term.id,
                name=term.name,
                aliases=list(term.aliases or []),
                postings=named_in.get(term.name.lower(), 0),
            )
            for term in terms
        ],
        max_terms=MAX_TERMS,
    )


def _get_term(db: Session, profile: Profile, term_id: uuid.UUID) -> ProfileTerm:
    term = db.scalar(
        select(ProfileTerm).where(ProfileTerm.id == term_id, ProfileTerm.profile_id == profile.id)
    )
    if term is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Skill not found")
    return term


def _name_taken(db: Session, profile: Profile, name: str, other_than: uuid.UUID | None) -> bool:
    query = select(ProfileTerm.id).where(
        ProfileTerm.profile_id == profile.id, func.lower(ProfileTerm.name) == name.lower()
    )
    if other_than is not None:
        query = query.where(ProfileTerm.id != other_than)
    return db.scalar(query) is not None


def _taken() -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, "This profile already has that skill")


def _commit_change(db: Session, profile: Profile) -> None:
    """Save, and mark the user's postings to be read with the new vocabulary."""
    bump_vocabulary(db, profile.user_id)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        # The unique index caught a name added at the same moment by another request.
        raise _taken() from exc


@router.get("", response_model=SkillsOut)
def get_skills(profile: CurrentProfile, db: Db) -> SkillsOut:
    return _out(db, profile)


@router.patch("", response_model=SkillsOut)
def update_skills(payload: SkillsUpdate, profile: CurrentProfile, db: Db) -> SkillsOut:
    if profile.software_skills != payload.software_skills:
        profile.software_skills = payload.software_skills
        _commit_change(db, profile)
        logger.info(
            "Profile %s turned the software skills %s",
            profile.id,
            "on" if payload.software_skills else "off",
        )
    return _out(db, profile)


@router.post("/terms", response_model=SkillsOut, status_code=status.HTTP_201_CREATED)
def add_term(payload: SkillTermIn, profile: CurrentProfile, db: Db) -> SkillsOut:
    count = db.scalar(
        select(func.count(ProfileTerm.id)).where(ProfileTerm.profile_id == profile.id)
    )
    if (count or 0) >= MAX_TERMS:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"This profile already has {MAX_TERMS} skills. Remove one first.",
        )
    if _name_taken(db, profile, payload.name, None):
        raise _taken()
    db.add(ProfileTerm(profile_id=profile.id, name=payload.name, aliases=payload.aliases))
    _commit_change(db, profile)
    return _out(db, profile)


@router.patch("/terms/{term_id}", response_model=SkillsOut)
def edit_term(
    term_id: uuid.UUID, payload: SkillTermIn, profile: CurrentProfile, db: Db
) -> SkillsOut:
    term = _get_term(db, profile, term_id)
    if _name_taken(db, profile, payload.name, term.id):
        raise _taken()
    if term.name != payload.name or list(term.aliases or []) != payload.aliases:
        term.name = payload.name
        term.aliases = payload.aliases
        _commit_change(db, profile)
    return _out(db, profile)


@router.delete("/terms/{term_id}", response_model=SkillsOut)
def delete_term(term_id: uuid.UUID, profile: CurrentProfile, db: Db) -> SkillsOut:
    db.delete(_get_term(db, profile, term_id))
    _commit_change(db, profile)
    return _out(db, profile)
