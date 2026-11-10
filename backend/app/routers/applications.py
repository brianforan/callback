import uuid
from datetime import UTC, date, datetime
from http import HTTPStatus

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.companies.lists import note_saved_posting
from app.deps import CurrentProfile, CurrentUser, Db
from app.models import (
    Application,
    JobPosting,
    Profile,
    Resume,
    StatusEvent,
    User,
)
from app.postings.store import (
    clear_stage,
    create_posting,
    find_by_url,
    is_placeholder,
    recommend_resume,
    set_referrer,
)
from app.postings.text import find_text
from app.research.snapshots import start_report
from app.schemas import ApplicationCreate, ApplicationOut, ApplicationUpdate, StatusEventOut
from app.scout.limits import SCOUT_SOURCE

router = APIRouter(prefix="/api/applications", tags=["applications"])


def _base_query(user: User):
    return (
        select(Application)
        .where(Application.user_id == user.id)
        .options(
            joinedload(Application.resume),
            joinedload(Application.recommended_resume),
            joinedload(Application.posting),
            selectinload(Application.events),
        )
    )


def _get_owned(db: Session, user: User, application_id: uuid.UUID) -> Application:
    application = db.scalar(_base_query(user).where(Application.id == application_id))
    if application is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Application not found")
    return application


def _resume_profile(db: Session, user: User, resume_id: uuid.UUID | None) -> uuid.UUID | None:
    """The profile of a resume the user owns, None for no resume. Refuses anyone else's."""
    if resume_id is None:
        return None
    profile_id = db.scalar(
        select(Resume.profile_id).where(Resume.id == resume_id, Resume.user_id == user.id)
    )
    if profile_id is None:
        raise HTTPException(HTTPStatus.UNPROCESSABLE_CONTENT, "Unknown resume")
    return profile_id


def _get_owned_posting(db: Session, user: User, posting_id: uuid.UUID) -> JobPosting:
    posting = db.scalar(
        select(JobPosting).where(JobPosting.id == posting_id, JobPosting.user_id == user.id)
    )
    if posting is None:
        raise HTTPException(HTTPStatus.UNPROCESSABLE_CONTENT, "Unknown posting")
    return posting


def _posting_for(
    db: Session, user: User, profile_id: uuid.UUID, payload: ApplicationCreate, applied_on: date
) -> JobPosting:
    """Every application gets a posting: the one named, one with the same URL, or a new one.

    A new one is made under the given profile.
    """
    if payload.posting_id:
        posting = _get_owned_posting(db, user, payload.posting_id)
    else:
        posting = find_by_url(db, user, payload.job_url)
    if posting is None:
        posting = create_posting(
            db,
            user,
            profile_id=profile_id,
            company=payload.company,
            title=payload.role_title,
            seen_on=applied_on,
            url=payload.job_url,
            location=payload.location,
            source=payload.source,
            raw_text=payload.posting_text,
            referrer=payload.referrer,
        )
        db.flush()
        note_saved_posting(db, posting)
        find_text(db, posting)
        return posting
    if payload.referrer:
        set_referrer(posting, payload.referrer)
    return posting


def _retire_placeholder(db: Session, posting_id: uuid.UUID, replacement: JobPosting | None) -> None:
    """An application moved off a placeholder posting leaves nothing worth keeping there.

    The placeholder is deleted once no application uses it, so it doesn't count in posting
    trends. A referrer on it moves to the replacement, unless that already has one.
    """
    db.flush()
    posting = db.get(JobPosting, posting_id)
    if posting is None or not is_placeholder(posting):
        return
    remaining = db.scalar(
        select(func.count()).select_from(Application).where(Application.posting_id == posting_id)
    )
    if remaining:
        return
    if replacement is not None and posting.referrer and not replacement.referred:
        set_referrer(replacement, posting.referrer)
    # Loaded before the flush, its application list may still name the moved application,
    # and deleting would clear that application's new link.
    db.expire(posting, ["applications"])
    db.delete(posting)


def _to_out(application: Application) -> ApplicationOut:
    return ApplicationOut(
        id=application.id,
        company=application.company,
        role_title=application.role_title,
        job_url=application.job_url,
        location=application.location,
        source=application.source,
        status=application.status,
        applied_on=application.applied_on,
        notes=application.notes,
        resume_id=application.resume_id,
        resume_label=application.resume.label if application.resume else None,
        recommended_resume_id=application.recommended_resume_id,
        recommended_resume_label=(
            application.recommended_resume.label if application.recommended_resume else None
        ),
        posting_id=application.posting_id,
        profile_id=application.profile_id,
        created_at=application.created_at,
        updated_at=application.updated_at,
        events=[StatusEventOut.model_validate(event) for event in application.events],
        from_scout=SCOUT_SOURCE
        in (application.source, application.posting.source if application.posting else None),
    )


def _rejected(db: Session, application: Application) -> None:
    """A rejection gets a report on what the posting asked for that the resume lacked, written
    after its company is researched when it has no recent snapshot. Both only queue."""
    start_report(db, application, datetime.now(UTC))


def save_application(
    db: Session, user: User, profile: Profile, payload: ApplicationCreate
) -> ApplicationOut:
    """Commits the session, so anything added before this call is stored with the application.

    It's logged under the profile of the resume sent, or the given profile when none was.
    """
    named_profile_id = _resume_profile(db, user, payload.resume_id) or profile.id
    applied_on = payload.applied_on or date.today()
    application = Application(
        **payload.model_dump(exclude={"applied_on", "posting_id", "posting_text", "referrer"}),
        user_id=user.id,
        applied_on=applied_on,
    )
    # A new posting goes under the same profile as the application, so the filters agree.
    application.posting = _posting_for(db, user, named_profile_id, payload, applied_on)
    # Applying to a staged posting sends the staged resume unless another is named.
    if application.posting.staged_at is not None:
        if application.resume_id is None:
            application.resume_id = application.posting.staged_resume_id
        clear_stage(application.posting)
    # The posting link already put the half-built application in the session; don't flush it yet.
    with db.no_autoflush:
        resume_profile_id = _resume_profile(db, user, application.resume_id)
    application.profile_id = resume_profile_id or profile.id
    application.events.append(StatusEvent(status=application.status))
    db.add(application)
    # The id is needed to leave this application out of the "used elsewhere" check.
    db.flush()
    application.recommended_resume_id = recommend_resume(db, application)
    # Logged as already rejected, such as an old application entered late: analyze it too.
    _rejected(db, application)
    db.commit()
    return _to_out(_get_owned(db, user, application.id))


@router.get("", response_model=list[ApplicationOut])
def list_applications(user: CurrentUser, db: Db) -> list[ApplicationOut]:
    query = _base_query(user).order_by(Application.applied_on.desc(), Application.created_at.desc())
    return [_to_out(application) for application in db.scalars(query).all()]


@router.post("", response_model=ApplicationOut, status_code=status.HTTP_201_CREATED)
def create_application(
    payload: ApplicationCreate, user: CurrentUser, profile: CurrentProfile, db: Db
) -> ApplicationOut:
    return save_application(db, user, profile, payload)


@router.get("/{application_id}", response_model=ApplicationOut)
def get_application(application_id: uuid.UUID, user: CurrentUser, db: Db) -> ApplicationOut:
    return _to_out(_get_owned(db, user, application_id))


@router.patch("/{application_id}", response_model=ApplicationOut)
def update_application(
    application_id: uuid.UUID, payload: ApplicationUpdate, user: CurrentUser, db: Db
) -> ApplicationOut:
    application = _get_owned(db, user, application_id)
    changes = payload.model_dump(exclude_unset=True)
    if "resume_id" in changes:
        resume_profile_id = _resume_profile(db, user, changes["resume_id"])
        # Sending another profile's resume moves the application to that profile.
        if resume_profile_id is not None:
            application.profile_id = resume_profile_id
    new_posting = None
    if changes.get("posting_id"):
        new_posting = _get_owned_posting(db, user, changes["posting_id"])
    old_posting_id = application.posting_id
    old_status = application.status
    new_status = changes.get("status")
    if new_status and new_status != old_status:
        application.events.append(StatusEvent(status=new_status))
    for field, value in changes.items():
        setattr(application, field, value)
    if old_posting_id and old_posting_id != application.posting_id:
        _retire_placeholder(db, old_posting_id, new_posting)
    if new_status and new_status != old_status:
        _rejected(db, application)
    db.commit()
    db.expire_all()
    return _to_out(_get_owned(db, user, application_id))


@router.delete("/{application_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_application(application_id: uuid.UUID, user: CurrentUser, db: Db) -> None:
    application = _get_owned(db, user, application_id)
    posting_id = application.posting_id
    db.delete(application)
    # A placeholder made for this application alone goes with it, as when it's moved away.
    if posting_id is not None:
        _retire_placeholder(db, posting_id, None)
    db.commit()
