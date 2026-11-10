from collections.abc import Sequence
from datetime import date

from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.analytics import (
    build_keyword_trends,
    build_overview,
    build_posting_trends,
    build_resume_insights,
)
from app.config import get_settings
from app.deps import CurrentProfile, CurrentUser, Db
from app.models import Application, IgnoredTerm, JobPosting, Profile, Resume, User
from app.resumes.variants import build_variant_report
from app.schemas import KeywordTrends, Overview, PostingTrends, ResumeInsights, VariantReport
from app.vocabulary import user_vocabulary

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


def _applications(db: Session, user: User) -> Sequence[Application]:
    query = (
        select(Application)
        .where(Application.user_id == user.id)
        .options(selectinload(Application.events), selectinload(Application.communications))
    )
    return db.scalars(query).all()


def _resumes(db: Session, user: User) -> Sequence[Resume]:
    return db.scalars(select(Resume).where(Resume.user_id == user.id)).all()


def _profile_applications(db: Session, profile: Profile) -> Sequence[Application]:
    query = (
        select(Application)
        .where(Application.profile_id == profile.id)
        .options(selectinload(Application.events), selectinload(Application.communications))
    )
    return db.scalars(query).all()


def _profile_resumes(db: Session, profile: Profile) -> Sequence[Resume]:
    return db.scalars(select(Resume).where(Resume.profile_id == profile.id)).all()


def _profile_postings(db: Session, profile: Profile) -> Sequence[JobPosting]:
    return db.scalars(
        select(JobPosting)
        .where(JobPosting.profile_id == profile.id)
        .options(selectinload(JobPosting.technologies))
    ).all()


def _ignored_terms(db: Session, user: User) -> set[str]:
    return set(db.scalars(select(IgnoredTerm.term).where(IgnoredTerm.user_id == user.id)).all())


@router.get("/overview", response_model=Overview)
def overview(user: CurrentUser, db: Db) -> Overview:
    return build_overview(
        _applications(db, user),
        _resumes(db, user),
        today=date.today(),
        stale_after_days=get_settings().stale_after_days,
    )


# The resume and baseline reports sit on the Resumes page, which shows the active profile, so
# they read only what was made under it. The other reports cover the whole account.
@router.get("/resumes", response_model=ResumeInsights)
def resume_insights(profile: CurrentProfile, db: Db) -> ResumeInsights:
    return build_resume_insights(
        _profile_resumes(db, profile),
        _profile_applications(db, profile),
        _profile_postings(db, profile),
        today=date.today(),
        vocabulary=user_vocabulary(db, profile.user_id),
    )


@router.get("/variants", response_model=VariantReport)
def variant_report(profile: CurrentProfile, db: Db) -> VariantReport:
    return build_variant_report(
        _profile_resumes(db, profile),
        _profile_postings(db, profile),
        today=date.today(),
        vocabulary=user_vocabulary(db, profile.user_id),
    )


@router.get("/keywords", response_model=KeywordTrends)
def keyword_trends(user: CurrentUser, db: Db) -> KeywordTrends:
    return build_keyword_trends(
        _resumes(db, user),
        _applications(db, user),
        _ignored_terms(db, user),
        today=date.today(),
        vocabulary=user_vocabulary(db, user.id),
    )


@router.get("/postings", response_model=PostingTrends)
def posting_trends(user: CurrentUser, db: Db) -> PostingTrends:
    query = (
        select(JobPosting)
        .where(JobPosting.user_id == user.id)
        .options(
            selectinload(JobPosting.technologies),
            selectinload(JobPosting.applications).selectinload(Application.events),
            selectinload(JobPosting.applications).selectinload(Application.resume),
        )
    )
    return build_posting_trends(
        db.scalars(query).all(), today=date.today(), vocabulary=user_vocabulary(db, user.id)
    )
