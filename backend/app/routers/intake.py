"""Logs an application and its resume in one request, for clients with no access to local files."""

import logging
from http import HTTPStatus
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.config import get_settings
from app.deps import Db, IntakeProfile, IntakeUser
from app.routers.applications import save_application
from app.routers.resumes import store_resume
from app.schemas import ApplicationCreate, ApplicationIntake, ApplicationOut, IntakeGuide

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/intake", tags=["intake"])

INTAKE_PATH = "/api/intake/applications"
DEFAULT_RESUME_FILENAME = "resume.tex"
MAX_LABEL_LENGTH = 120
RESUME_FIELDS = {"resume_tex", "resume_filename", "resume_label"}

INSTRUCTIONS = [
    "Send one POST per application. Leave out anything you don't know, and never guess.",
    "Put the whole resume in resume_tex and escape it as JSON: every LaTeX backslash becomes \\\\.",
    "Identical resume content reuses the stored version, so always send the resume that was sent.",
    "Nothing is stored unless the whole request succeeds, so a 4xx or 5xx can be fixed and resent.",
    "After a timeout or dropped connection, ask the user before resending. It may have been "
    "logged already, and a second POST would log it twice.",
]
EXAMPLE: dict[str, Any] = {
    "company": "Acme",
    "role_title": "Staff Engineer",
    "resume_tex": "\\documentclass{article}\n\\begin{document}\nResume\n\\end{document}\n",
    "resume_filename": "acme-staff-engineer.tex",
    "resume_label": "Acme tailored",
    "job_url": "https://example.com/jobs/123",
    "source": "LinkedIn",
    "location": "Remote",
    "applied_on": "2026-11-01",
    "posting_text": "Staff Engineer\n\nRequired\n- Python\n\nNice to have\n- Kafka",
    "referrer": "Sam",
}


@router.get("/applications", response_model=IntakeGuide)
def describe_intake() -> IntakeGuide:
    settings = get_settings()
    return IntakeGuide(
        method="POST",
        path=INTAKE_PATH,
        content_type="application/json",
        bearer_token_required=settings.default_user_id is None,
        max_resume_bytes=settings.max_resume_bytes,
        instructions=INSTRUCTIONS,
        json_schema=ApplicationIntake.model_json_schema(),
        example=EXAMPLE,
    )


def _require_json(request: Request) -> None:
    # A page on another site can post text/plain or no type at all without a preflight. With a
    # default user there is no credential to stop it, so only a type that needs one is accepted.
    content_type = request.headers.get("content-type", "").partition(";")[0].strip().lower()
    if content_type != "application/json":
        raise HTTPException(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Send application/json")


@router.post(
    "/applications",
    response_model=ApplicationOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(_require_json)],
)
def log_application(
    payload: ApplicationIntake, user: IntakeUser, profile: IntakeProfile, db: Db
) -> ApplicationOut:
    resume_id = None
    if payload.resume_tex:
        if len(payload.resume_tex.encode("utf-8")) > get_settings().max_resume_bytes:
            raise HTTPException(HTTPStatus.CONTENT_TOO_LARGE, "Resume file is too large")
        label = payload.resume_label or f"{payload.company} {payload.role_title}"
        resume, _ = store_resume(
            db,
            profile,
            filename=payload.resume_filename or DEFAULT_RESUME_FILENAME,
            source=payload.resume_tex,
            label=label[:MAX_LABEL_LENGTH],
        )
        resume_id = resume.id

    application = ApplicationCreate(
        **payload.model_dump(exclude=RESUME_FIELDS), resume_id=resume_id
    )
    saved = save_application(db, user, profile, application)
    logger.info("Logged application %s through intake", saved.id)
    return saved
