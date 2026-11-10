import hashlib
import logging
import re
import uuid
from http import HTTPStatus
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Response, UploadFile, status
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.deps import CurrentProfile, CurrentUser, Db
from app.models import Application, Profile, Resume, User
from app.profiles import owned_profile
from app.resumes.pdf_text import PdfReadError, read_pdf
from app.resumes.variants import variant_rules
from app.schemas import (
    ResumeDetail,
    ResumeOut,
    ResumeSourceUpdate,
    VariantRules,
    VariantUpdate,
)
from app.similarity import tex_to_text

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/resumes", tags=["resumes"])

# Baselines are the few resumes a profile keeps for different kinds of job, so a handful is enough.
MAX_BASELINES = 3


def _application_count(db: Session, resume_id: uuid.UUID) -> int:
    query = select(func.count(Application.id)).where(Application.resume_id == resume_id)
    return db.scalar(query) or 0


def _get_owned(db: Session, user: User, resume_id: uuid.UUID) -> Resume:
    resume = db.scalar(select(Resume).where(Resume.id == resume_id, Resume.user_id == user.id))
    if resume is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Resume not found")
    return resume


def _to_out(resume: Resume, application_count: int) -> ResumeOut:
    return ResumeOut(
        id=resume.id,
        profile_id=resume.profile_id,
        label=resume.label,
        filename=resume.filename,
        word_count=resume.word_count,
        created_at=resume.created_at,
        application_count=application_count,
        is_variant=resume.is_variant,
        variant_rules=variant_rules(resume),
        source_format=resume.source_format,
        pdf_pages=resume.pdf_pages,
        pdf_error=resume.pdf_error,
    )


def _read_upload(file: UploadFile) -> tuple[str, bytes]:
    """Validate an upload's name and size and return its (filename, bytes)."""
    filename = Path(file.filename or "").name[:255]
    lowered = filename.lower()
    if lowered.endswith(".tex"):
        limit = get_settings().max_resume_bytes
    elif lowered.endswith(".pdf"):
        limit = get_settings().max_resume_pdf_bytes
    else:
        raise HTTPException(
            HTTPStatus.UNPROCESSABLE_CONTENT, "Only .tex and .pdf files are accepted"
        )
    raw = file.file.read(limit + 1)
    if len(raw) > limit:
        raise HTTPException(HTTPStatus.CONTENT_TOO_LARGE, "Resume file is too large")
    return filename, raw


def _tex_source(raw: bytes) -> str:
    try:
        source = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(HTTPStatus.UNPROCESSABLE_CONTENT, "Resume must be UTF-8 text") from exc
    if not source.strip() or "\x00" in source:
        raise HTTPException(HTTPStatus.UNPROCESSABLE_CONTENT, "Resume file has no usable text")
    return source


def _existing(db: Session, profile_id: uuid.UUID, digest: str) -> Resume | None:
    # Re-uploading identical content returns the existing version, so one resume
    # sent to many companies stays one row and the similarity history stays clean.
    return db.scalar(
        select(Resume).where(Resume.profile_id == profile_id, Resume.content_sha256 == digest)
    )


def _add(db: Session, profile: Profile, resume: Resume, label: str | None) -> Resume:
    count = select(func.count(Resume.id)).where(Resume.profile_id == profile.id)
    version = (db.scalar(count) or 0) + 1
    resume.user_id = profile.user_id
    resume.profile_id = profile.id
    resume.label = (label or "").strip() or f"{Path(resume.filename).stem[:100]} v{version}"
    resume.word_count = len(resume.plain_text.split())
    db.add(resume)
    db.flush()
    return resume


def store_resume(
    db: Session, profile: Profile, *, filename: str, source: str, label: str | None
) -> tuple[Resume, bool]:
    """Return the resume and whether it is new. Flushes but leaves the commit to the caller."""
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    existing = _existing(db, profile.id, digest)
    if existing:
        return existing, False
    resume = Resume(
        filename=filename,
        source_format="tex",
        source_tex=source,
        plain_text=tex_to_text(source),
        content_sha256=digest,
    )
    return _add(db, profile, resume, label), True


def store_pdf_resume(
    db: Session, profile: Profile, *, filename: str, raw: bytes, label: str | None
) -> tuple[Resume, bool]:
    """Store an uploaded PDF for analysis, as store_resume does. The PDF is kept to download.

    Its source_tex is an outline read from the PDF's text, so it's never compiled or tailored.
    """
    digest = hashlib.sha256(raw).hexdigest()
    existing = _existing(db, profile.id, digest)
    if existing:
        return existing, False
    # The header may sit after a little junk, but within the first 1 KB.
    if b"%PDF-" not in raw[:1024]:
        raise HTTPException(HTTPStatus.UNPROCESSABLE_CONTENT, "This file isn't a PDF")
    try:
        parsed = read_pdf(raw)
    except PdfReadError as exc:
        raise HTTPException(HTTPStatus.UNPROCESSABLE_CONTENT, str(exc)) from exc
    resume = Resume(
        filename=filename,
        source_format="pdf",
        source_tex=parsed.outline,
        plain_text=tex_to_text(parsed.outline),
        content_sha256=digest,
        # The upload is its own PDF, which also keeps the compile worker away from it.
        pdf=raw,
        pdf_pages=parsed.pages,
    )
    return _add(db, profile, resume, label), True


def _store_upload(
    db: Session, profile: Profile, file: UploadFile, label: str | None
) -> tuple[Resume, bool]:
    filename, raw = _read_upload(file)
    if filename.lower().endswith(".pdf"):
        return store_pdf_resume(db, profile, filename=filename, raw=raw, label=label)
    return store_resume(db, profile, filename=filename, source=_tex_source(raw), label=label)


@router.get("", response_model=list[ResumeOut])
def list_resumes(profile: CurrentProfile, db: Db) -> list[ResumeOut]:
    rows = db.execute(
        select(Resume, func.count(Application.id))
        .outerjoin(Application, Application.resume_id == Resume.id)
        .where(Resume.profile_id == profile.id)
        .group_by(Resume.id)
        .order_by(Resume.created_at.desc())
    ).all()
    return [_to_out(resume, count) for resume, count in rows]


@router.post("", response_model=ResumeOut, status_code=status.HTTP_201_CREATED)
def upload_resume(
    file: UploadFile,
    response: Response,
    user: CurrentUser,
    profile: CurrentProfile,
    db: Db,
    label: Annotated[str | None, Form(max_length=120)] = None,
    profile_id: Annotated[uuid.UUID | None, Form()] = None,
) -> ResumeOut:
    """Store in the active profile, or in the one given, such as the edited resume's own."""
    if profile_id is not None and profile_id != profile.id:
        target = owned_profile(db, user, profile_id)
        if target is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Profile not found")
        profile = target
    resume, created = _store_upload(db, profile, file, label)
    if not created:
        response.status_code = status.HTTP_200_OK
        return _to_out(resume, _application_count(db, resume.id))

    db.commit()
    logger.info("Stored %s resume %s", resume.source_format, resume.id)
    return _to_out(resume, 0)


@router.get("/{resume_id}", response_model=ResumeDetail)
def get_resume(resume_id: uuid.UUID, user: CurrentUser, db: Db) -> ResumeDetail:
    resume = _get_owned(db, user, resume_id)
    summary = _to_out(resume, _application_count(db, resume.id))
    return ResumeDetail(**summary.model_dump(), source_tex=resume.source_tex)


def _check_baseline_room(db: Session, profile_id: uuid.UUID, *keep_out: uuid.UUID) -> None:
    """Refuse a new baseline when the profile's limit is reached, not counting the ones given."""
    query = select(func.count(Resume.id)).where(
        Resume.profile_id == profile_id, Resume.is_variant.is_(True), Resume.id.not_in(keep_out)
    )
    if (db.scalar(query) or 0) >= MAX_BASELINES:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"You already have {MAX_BASELINES} baselines. Replace or remove one first.",
        )


@router.post("/baselines", response_model=ResumeOut, status_code=status.HTTP_201_CREATED)
def upload_baseline(
    file: UploadFile,
    user: CurrentUser,
    profile: CurrentProfile,
    db: Db,
    label: Annotated[str | None, Form(max_length=120)] = None,
    rules: Annotated[str | None, Form(max_length=20_000)] = None,
    replaces: Annotated[uuid.UUID | None, Form()] = None,
) -> ResumeOut:
    """Upload a resume as a baseline in one step, optionally taking over another's place.

    The replaced baseline stays as an ordinary resume, and its rules carry over unless new
    ones are given. Identical content reuses the existing resume, as a normal upload does.
    """
    try:
        parsed_rules = VariantRules.model_validate_json(rules) if rules else None
    except ValidationError as exc:
        raise HTTPException(HTTPStatus.UNPROCESSABLE_CONTENT, "Baseline rules are invalid") from exc
    replaced = _get_owned(db, user, replaces) if replaces else None
    if replaced is not None and replaced.profile_id != profile.id:
        # Baselines are counted per profile, so one can only take the place of its own.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Resume not found")
    resume, _ = _store_upload(db, profile, file, label)
    _check_baseline_room(db, profile.id, resume.id, *([replaced.id] if replaced else []))

    if label and label.strip():
        resume.label = label.strip()
    if parsed_rules is not None:
        resume.variant_rules = parsed_rules.model_dump(mode="json")
    elif replaced is not None and replaced.id != resume.id:
        resume.variant_rules = dict(replaced.variant_rules or {})
    resume.is_variant = True
    if replaced is not None and replaced.id != resume.id:
        replaced.is_variant = False
    db.commit()
    logger.info("Stored baseline %s", resume.id)
    return _to_out(resume, _application_count(db, resume.id))


@router.put("/{resume_id}/variant", response_model=ResumeOut)
def set_variant(
    resume_id: uuid.UUID, payload: VariantUpdate, user: CurrentUser, db: Db
) -> ResumeOut:
    """Mark a resume as a baseline, or not, and save its rules. Rules are kept when unmarked."""
    resume = _get_owned(db, user, resume_id)
    if payload.is_variant and not resume.is_variant:
        _check_baseline_room(db, resume.profile_id, resume.id)
    resume.is_variant = payload.is_variant
    resume.variant_rules = payload.variant_rules.model_dump(mode="json")
    db.commit()
    return _to_out(resume, _application_count(db, resume.id))


@router.put("/{resume_id}/source", response_model=ResumeOut)
def update_source(
    resume_id: uuid.UUID, payload: ResumeSourceUpdate, user: CurrentUser, db: Db
) -> ResumeOut:
    """Save edited LaTeX over a baseline in place, keeping its id.

    Postings' baseline picks, staged postings and applications that point at it follow the
    edit. Its PDF is cleared, so the compile worker makes it again from the new source.
    """
    resume = _get_owned(db, user, resume_id)
    if resume.source_format != "tex":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This baseline was uploaded as a PDF, so there's no LaTeX to edit. Replace its file.",
        )
    if not resume.is_variant:
        # Other resumes are versions kept as they were sent, so edits become a new version.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Only baselines can be edited in place. Save it as a new version instead.",
        )
    raw = payload.source_tex.encode("utf-8")
    if len(raw) > get_settings().max_resume_bytes:
        raise HTTPException(HTTPStatus.CONTENT_TOO_LARGE, "Resume file is too large")
    source = _tex_source(raw)
    digest = hashlib.sha256(raw).hexdigest()
    if digest == resume.content_sha256:
        return _to_out(resume, _application_count(db, resume.id))
    if _existing(db, resume.profile_id, digest) is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Another resume in this profile already has exactly this content.",
        )

    resume.source_tex = source
    resume.plain_text = tex_to_text(source)
    resume.word_count = len(resume.plain_text.split())
    resume.content_sha256 = digest
    resume.pdf = None
    resume.pdf_pages = None
    resume.pdf_error = None
    db.commit()
    logger.info("Saved edited source of baseline %s; its PDF will be compiled again", resume.id)
    return _to_out(resume, _application_count(db, resume.id))


@router.get("/{resume_id}/pdf")
def download_pdf(resume_id: uuid.UUID, user: CurrentUser, db: Db) -> Response:
    resume = _get_owned(db, user, resume_id)
    if resume.pdf is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This resume has no PDF yet")
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(resume.filename).stem)[:100] or "resume"
    return Response(
        content=resume.pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{stem}.pdf"'},
    )


@router.delete("/{resume_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_resume(resume_id: uuid.UUID, user: CurrentUser, db: Db) -> None:
    resume = _get_owned(db, user, resume_id)
    if _application_count(db, resume.id):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "This resume is linked to applications and can't be deleted"
        )
    db.delete(resume)
    db.commit()
