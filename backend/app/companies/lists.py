"""Each profile's list of companies: the ones its Companies runs read.

A list is private to its user, though it points into the shared catalog. Companies join it from
the user's own saved postings and Scout leads, and (chunk 4) from discovery for its brief.
"""

import logging
import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.companies.catalog import (
    catalog_url,
    company_source,
    from_raw,
    hiring_ref,
    lead_listing,
    record_listing,
)
from app.companies.identity import provisional_name, resolve_company
from app.listings.cache import decode_listing
from app.models import (
    BoardCache,
    CatalogJobSource,
    JobPosting,
    Profile,
    ProfileCompany,
    ProfileCompanyOrigin,
    ScoutRun,
    utcnow,
)

logger = logging.getLogger(__name__)


def _first_profile(db: Session, user_id: uuid.UUID) -> uuid.UUID | None:
    return db.scalar(
        select(Profile.id)
        .where(Profile.user_id == user_id)
        .order_by(Profile.created_at, Profile.id)
        .limit(1)
    )


def add_to_profile(
    db: Session,
    user_id: uuid.UUID,
    profile_id: uuid.UUID | None,
    company_id: uuid.UUID,
    origin: ProfileCompanyOrigin,
) -> ProfileCompany | None:
    """Put a company on a profile's list, if it isn't there yet. A run whose profile was deleted
    adds to the user's first profile. None when the user has no profile."""
    target = profile_id or _first_profile(db, user_id)
    if target is None:
        return None
    listed = db.get(ProfileCompany, (target, company_id))
    if listed is not None:
        return listed
    listed = ProfileCompany(
        profile_id=target, company_id=company_id, user_id=user_id, origin=origin.value
    )
    try:
        with db.begin_nested():
            db.add(listed)
    except IntegrityError:
        logger.info("Another process put company %s on profile %s first", company_id, target)
        return db.get(ProfileCompany, (target, company_id))
    return listed


def note_lead(
    db: Session, user_id: uuid.UUID, profile_id: uuid.UUID | None, company_id: uuid.UUID
) -> None:
    """A lead came from this company: it joins the profile's list, and the lead is counted."""
    listed = add_to_profile(db, user_id, profile_id, company_id, ProfileCompanyOrigin.LEAD)
    if listed is not None:
        listed.leads = (listed.leads or 0) + 1
        listed.last_lead_at = utcnow()


def note_dismissed(db: Session, profile_id: uuid.UUID | None, url: str | None, change: int) -> None:
    """Count a lead turned down (1) or restored (-1) against its company on the profile's list."""
    link = catalog_url(url)
    if profile_id is None or link is None:
        return
    found = db.scalar(select(CatalogJobSource).where(CatalogJobSource.url == link))
    if found is None:
        return
    listed = db.get(ProfileCompany, (profile_id, found.job.company_id))
    if listed is not None:
        listed.dismissed = max(0, (listed.dismissed or 0) + change)


def note_saved_posting(db: Session, posting: JobPosting) -> None:
    """A posting saved from a company's own board puts that company on the posting's profile's
    list. Only the board's token is used, never what the user typed. A failure here is logged
    and never fails the save."""
    try:
        with db.begin_nested():
            company_id = _own_company(db, posting.url)
            if company_id is not None:
                add_to_profile(
                    db,
                    posting.user_id,
                    posting.profile_id,
                    company_id,
                    ProfileCompanyOrigin.POSTINGS,
                )
    except SQLAlchemyError:
        logger.exception("Couldn't add the company of posting %s to its profile's list", posting.id)


@dataclass
class SeedSummary:
    postings: int = 0
    leads: int = 0
    candidates: int = 0
    board_listings: int = 0
    profiles: set[uuid.UUID] = field(default_factory=set)


def _own_company(db: Session, url: str | None) -> uuid.UUID | None:
    """The company whose own hiring system a link is on, added from its token if it's new. The
    name comes from the token, never from what a user typed."""
    ref = hiring_ref(url)
    if ref is None or ref.token is None:
        return None
    company = resolve_company(
        db, provisional_name(ref.token), source=(ref.system, ref.token)
    ).company
    company_source(db, company, ref.system, ref.token, is_own=True)
    return company.id


def seed_catalog(db: Session) -> SeedSummary:
    """Fill the catalog from what Callback already has, with no requests and no model calls:
    hiring-system links in saved postings, Scout leads and the links runs looked at, and
    cached job board results. Users' own links also put their companies on that profile's list.
    Commits as it goes."""
    summary = SeedSummary()
    for posting in db.scalars(select(JobPosting).where(JobPosting.url.is_not(None))).all():
        company_id = _own_company(db, posting.url)
        if company_id is None:
            continue
        listed = add_to_profile(
            db, posting.user_id, posting.profile_id, company_id, ProfileCompanyOrigin.POSTINGS
        )
        summary.postings += 1
        if listed is not None:
            summary.profiles.add(listed.profile_id)
    db.commit()

    for run in db.scalars(select(ScoutRun)).all():
        for lead in run.leads or []:
            try:
                job = record_listing(db, lead_listing(lead))
            except ValueError as exc:
                logger.info("Skipped a lead while seeding: %s", exc)
                continue
            if job is None:
                continue
            listed = add_to_profile(
                db, run.user_id, run.profile_id, job.company_id, ProfileCompanyOrigin.LEAD
            )
            summary.leads += 1
            if listed is not None:
                summary.profiles.add(listed.profile_id)
        for step in run.steps or []:
            if step.get("kind") != "fetch":
                continue
            company_id = _own_company(db, step.get("detail"))
            if company_id is None:
                continue
            listed = add_to_profile(
                db, run.user_id, run.profile_id, company_id, ProfileCompanyOrigin.DISCOVERED
            )
            summary.candidates += 1
            if listed is not None:
                summary.profiles.add(listed.profile_id)
        db.commit()

    for row in db.scalars(select(BoardCache)).all():
        for item in row.listings or []:
            raw = decode_listing(item)
            if hiring_ref(raw.url) is None and hiring_ref(raw.direct_url) is None:
                continue
            try:
                if record_listing(db, from_raw(row.board, raw)) is not None:
                    summary.board_listings += 1
            except ValueError as exc:
                logger.info("Skipped a cached listing while seeding: %s", exc)
        db.commit()
    return summary
