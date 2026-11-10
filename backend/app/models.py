import uuid
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, column_property, mapped_column, relationship


class ApplicationStatus(StrEnum):
    APPLIED = "applied"
    SCREENING = "screening"
    INTERVIEW = "interview"
    OFFER = "offer"
    REJECTED = "rejected"
    GHOSTED = "ghosted"
    HIRING_PAUSED = "hiring_paused"
    WITHDRAWN = "withdrawn"


class PostingLevel(StrEnum):
    INTERN = "intern"
    JUNIOR = "junior"
    MID = "mid"
    SENIOR = "senior"
    STAFF = "staff"
    PRINCIPAL = "principal"
    MANAGER = "manager"


class RoleFamily(StrEnum):
    FULL_STACK = "full_stack"
    FRONTEND = "frontend"
    BACKEND = "backend"
    MOBILE = "mobile"
    DEVOPS = "devops"
    DATA = "data"
    MACHINE_LEARNING = "machine_learning"
    SECURITY = "security"
    QA = "qa"
    MANAGEMENT = "management"
    GENERAL_SOFTWARE = "general_software"
    OTHER = "other"


class WorkMode(StrEnum):
    REMOTE = "remote"
    HYBRID = "hybrid"
    ONSITE = "onsite"


class EmploymentType(StrEnum):
    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    CONTRACT = "contract"
    INTERNSHIP = "internship"


class SourceKind(StrEnum):
    JOB_BOARD = "job_board"
    COMPANY_SITE = "company_site"
    REFERRAL = "referral"
    RECRUITER = "recruiter"


class Requirement(StrEnum):
    REQUIRED = "required"
    NICE_TO_HAVE = "nice_to_have"
    MENTIONED = "mentioned"


class PostingTextStatus(StrEnum):
    """Reading a posting's text from its link, for one saved without text (app.postings.text)."""

    PENDING = "pending"
    FETCHED = "fetched"
    FAILED = "failed"


POSTING_TEXT_STATUS_CHECK = "text_status IN ({})".format(
    ", ".join(f"'{status.value}'" for status in PostingTextStatus)
)


STATUS_CHECK = "status IN ({})".format(
    ", ".join(f"'{status.value}'" for status in ApplicationStatus)
)


class Role(StrEnum):
    """What a user may administer. Separate from their plan, so an admin can be free or premium."""

    MEMBER = "member"
    ADMIN = "admin"


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("email", name="uq_users_email"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320))
    password_hash: Mapped[str] = mapped_column(String(255))
    # "free" or "premium". Premium unlocks Scout. Set in Admin, or `app.cli set-plan`.
    plan: Mapped[str] = mapped_column(String(20), default="free", server_default="free")
    # "member" or "admin". Admins see the Admin panel. Set in Admin, or `app.cli set-role`.
    role: Mapped[str] = mapped_column(
        String(20), default=lambda: Role.MEMBER.value, server_default="member"
    )
    # No daily caps for this user (app.app_config). A run's dollar ceiling still holds. Set in
    # Admin -> Users.
    override_limits: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    # Goes up whenever a profile's skill terms change, so postings parsed before are read again.
    vocabulary_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AppConfig(Base):
    """A setting the admin changed from its default (app.app_config holds the list)."""

    __tablename__ = "app_config"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )


class ApiToken(Base):
    __tablename__ = "api_tokens"
    __table_args__ = (UniqueConstraint("token_hash", name="uq_api_tokens_token_hash"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(80))
    token_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship()


class Profile(Base):
    """One side of a user's job search, such as software engineering or accounting.

    A profile owns its resumes, skill terms, fact sheet and Scout settings. Applications and
    postings stay the user's and only note the profile they were made under, so they can be
    filtered.
    """

    __tablename__ = "profiles"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_profiles_user_name"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(60))
    # Whether the built-in software skills (app.postings.parser) are part of this profile's.
    software_skills: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProfileTerm(Base):
    """A skill the user added to a profile's vocabulary, with other ways postings write it."""

    __tablename__ = "profile_terms"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(80))
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# One name per profile, whatever its case.
Index(
    "uq_profile_terms_profile_name",
    ProfileTerm.profile_id,
    func.lower(ProfileTerm.name),
    unique=True,
)


class IgnoredTerm(Base):
    __tablename__ = "ignored_terms"
    __table_args__ = (UniqueConstraint("user_id", "term", name="uq_ignored_terms_user_term"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    term: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class HiddenKind(StrEnum):
    LISTING = "listing"
    COMPANY = "company"


class HiddenListing(Base):
    """A job, or every job at a company, the user doesn't want in job listing results.

    The key is the listing's URL for one job and the company key for a company. A hidden job
    also matches the same company and title, so it stays hidden on other boards.
    """

    __tablename__ = "hidden_listings"
    __table_args__ = (
        UniqueConstraint("user_id", "kind", "key", name="uq_hidden_listings_user_kind_key"),
        CheckConstraint("kind IN ('listing', 'company')", name="ck_hidden_listings_kind"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(10))
    key: Mapped[str] = mapped_column(String(2048))
    company: Mapped[str] = mapped_column(String(200))
    company_key: Mapped[str] = mapped_column(String(200))
    title: Mapped[str | None] = mapped_column(String(200))
    url: Mapped[str | None] = mapped_column(String(2048))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Resume(Base):
    __tablename__ = "resumes"
    __table_args__ = (
        UniqueConstraint("profile_id", "content_sha256", name="uq_resumes_profile_content"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("profiles.id", ondelete="CASCADE"), index=True
    )
    label: Mapped[str] = mapped_column(String(120))
    filename: Mapped[str] = mapped_column(String(255))
    # "tex" for an uploaded .tex. "pdf" for an uploaded PDF, whose source_tex is an outline read
    # from its text: analysed like LaTeX, but never compiled or tailored.
    source_format: Mapped[str] = mapped_column(String(8), default="tex", server_default="tex")
    source_tex: Mapped[str] = mapped_column(Text)
    plain_text: Mapped[str] = mapped_column(Text)
    word_count: Mapped[int]
    content_sha256: Mapped[str] = mapped_column(String(64))
    # A variant is a baseline you keep for one kind of job, with rules for which jobs it suits.
    is_variant: Mapped[bool] = mapped_column(Boolean, default=False)
    variant_rules: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # The compiled PDF, made by the tailor worker. Loaded only when downloaded.
    pdf: Mapped[bytes | None] = mapped_column(LargeBinary, deferred=True)
    pdf_pages: Mapped[int | None]
    # Why compiling failed. A failure isn't retried, since the source only changes when a
    # baseline is edited, which clears the PDF, its pages and this for the worker to try again.
    pdf_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FactSheet(Base):
    """What the user can truthfully claim, in their own words. Tailoring reads it as the rules.

    One per profile, since an accountant's claims aren't an engineer's.
    """

    __tablename__ = "fact_sheets"
    __table_args__ = (UniqueConstraint("profile_id", name="uq_fact_sheets_profile_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"))
    content: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ScoutSettings(Base):
    """What a profile wants Scout to look for, and where each run must stop."""

    __tablename__ = "smartsearch_settings"
    __table_args__ = (UniqueConstraint("profile_id", name="uq_smartsearch_settings_profile_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"))
    description: Mapped[str] = mapped_column(Text, default="")
    title_families: Mapped[list[str]] = mapped_column(JSON, default=list)
    work_modes: Mapped[list[str]] = mapped_column(JSON, default=list)
    # Where the person wants to work, in their own words: a city, a region or a country.
    location: Mapped[str] = mapped_column(String(120), default="", server_default="")
    posted_within_days: Mapped[int | None] = mapped_column()
    # wide keeps stretches, balanced keeps jobs the person meets most of, close keeps near fits.
    fit_mode: Mapped[str] = mapped_column(String(20), default="balanced", server_default="balanced")
    engine: Mapped[str] = mapped_column(String(20), default="agent", server_default="agent")
    # include keeps agency listings, find_employer looks for the employer's own posting, skip
    # records none.
    agency_mode: Mapped[str] = mapped_column(
        String(20), default="include", server_default="include"
    )
    # none, metro (a major US metro area) or anywhere (in the US): where they'd move for an
    # onsite or hybrid job.
    relocation: Mapped[str] = mapped_column(String(10), default="none", server_default="none")
    # Technologies a job must use (any one of them), and ones that rule a job out. None means
    # never saved: the description's technologies are used instead (app.scout.match).
    must_have: Mapped[list[str] | None] = mapped_column(JSON)
    rule_out: Mapped[list[str] | None] = mapped_column(JSON)
    # PostingLevel values wanted. Empty means any level except intern and manager.
    levels: Mapped[list[str]] = mapped_column(JSON, default=list, server_default="[]")
    limits: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    # The work the profile does, read by a model from its baselines, fact sheet and description
    # (app.scout.work): work, titles, inputs_key, made_at and edited. None until a run reads it.
    work_boundary: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class JobPosting(Base):
    __tablename__ = "job_postings"
    __table_args__ = (
        UniqueConstraint("user_id", "url", name="uq_job_postings_user_url"),
        CheckConstraint(POSTING_TEXT_STATUS_CHECK, name="ck_job_postings_text_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # The profile it was made under, for filtering. Deleting the profile keeps the row.
    profile_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("profiles.id", ondelete="SET NULL"), index=True
    )
    company: Mapped[str] = mapped_column(String(200))
    company_key: Mapped[str] = mapped_column(String(200), index=True)
    title: Mapped[str] = mapped_column(String(200))
    url: Mapped[str | None] = mapped_column(String(2048))
    location: Mapped[str | None] = mapped_column(String(200))
    source: Mapped[str | None] = mapped_column(String(80))
    source_kind: Mapped[str | None] = mapped_column(String(20))
    referred: Mapped[bool] = mapped_column(Boolean, default=False)
    referrer: Mapped[str | None] = mapped_column(String(200))
    level: Mapped[str | None] = mapped_column(String(20))
    role_family: Mapped[str | None] = mapped_column(String(20))
    years_required: Mapped[int | None]
    salary_min: Mapped[int | None]
    salary_max: Mapped[int | None]
    work_mode: Mapped[str | None] = mapped_column(String(20))
    employment_type: Mapped[str | None] = mapped_column(String(20))
    raw_text: Mapped[str] = mapped_column(Text, default="")
    # Reading the text from the link, for a posting saved with a link and no text: a
    # PostingTextStatus value, or None when nothing was read that way. Pasting text clears it.
    text_status: Mapped[str | None] = mapped_column(String(10))
    # Why the read failed, in plain words for the user.
    text_error: Mapped[str | None] = mapped_column(String(300))
    text_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Names of parsed fields the user has edited. The parser never overwrites these.
    corrected_fields: Mapped[list[str]] = mapped_column(JSON, default=list)
    # The owner's vocabulary_version its skills were read with. Behind means the worker reads
    # them again (app.postings.store.reread_skills).
    skills_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    seen_on: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    # A staged posting has its application materials ready and waits for the user to submit.
    # Logging an application to it clears these, since it's no longer waiting.
    staged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    staged_resume_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("resumes.id", ondelete="SET NULL")
    )
    staged_notes: Mapped[str | None] = mapped_column(Text)

    technologies: Mapped[list["PostingTechnology"]] = relationship(
        cascade="all, delete-orphan", order_by="PostingTechnology.name"
    )
    staged_resume: Mapped[Resume | None] = relationship()
    applications: Mapped[list["Application"]] = relationship(back_populates="posting")


class PostingTechnology(Base):
    __tablename__ = "posting_technologies"
    __table_args__ = (
        UniqueConstraint("posting_id", "name", name="uq_posting_technologies_posting_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    posting_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("job_postings.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(80))
    category: Mapped[str] = mapped_column(String(20))
    requirement: Mapped[str] = mapped_column(String(20))


class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (CheckConstraint(STATUS_CHECK, name="ck_applications_status"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # The profile it was made under, for filtering. Deleting the profile keeps the row.
    profile_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("profiles.id", ondelete="SET NULL"), index=True
    )
    resume_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("resumes.id", ondelete="RESTRICT"), index=True
    )
    posting_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("job_postings.id", ondelete="SET NULL"), index=True
    )
    # The resume the matcher would have sent when this was logged. Kept apart from resume_id so
    # what you sent can be compared with what was suggested.
    recommended_resume_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("resumes.id", ondelete="SET NULL")
    )
    company: Mapped[str] = mapped_column(String(200))
    role_title: Mapped[str] = mapped_column(String(200))
    job_url: Mapped[str | None] = mapped_column(String(2048))
    location: Mapped[str | None] = mapped_column(String(200))
    source: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(20))
    applied_on: Mapped[date] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    resume: Mapped[Resume | None] = relationship(foreign_keys=[resume_id])
    recommended_resume: Mapped[Resume | None] = relationship(foreign_keys=[recommended_resume_id])
    posting: Mapped[JobPosting | None] = relationship(back_populates="applications")
    events: Mapped[list["StatusEvent"]] = relationship(
        cascade="all, delete-orphan", order_by="StatusEvent.occurred_at"
    )
    communications: Mapped[list["Communication"]] = relationship(
        back_populates="application",
        cascade="all, delete-orphan",
        order_by="Communication.occurred_on.desc()",
    )


class BoardCache(Base):
    """One job board's results for one search, kept so a restart doesn't start every search cold.

    Shared by every user, since the same search on the same board returns the same jobs. The
    key is a hash of the board and the search, so long searches fit.
    """

    __tablename__ = "board_cache"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    board: Mapped[str] = mapped_column(String(40))
    # The board's listings as fetched, before parsing, so parser changes apply to them too.
    listings: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    warning: Mapped[str | None] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    fresh_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TailorStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


TAILOR_ACTIVE = (TailorStatus.QUEUED.value, TailorStatus.RUNNING.value)
TAILOR_STATUS_CHECK = "status IN ({})".format(
    ", ".join(f"'{status.value}'" for status in TailorStatus)
)
TAILOR_ACTIVE_CHECK = "status IN ({})".format(", ".join(f"'{value}'" for value in TAILOR_ACTIVE))


class TailorJob(Base):
    """One request to tailor a baseline to a posting, worked through by the tailor worker.

    The inputs are read when the job runs, not stored, so the job always uses the current
    baseline, posting and fact sheet. A finished job keeps what came back.
    """

    __tablename__ = "tailor_jobs"
    __table_args__ = (CheckConstraint(TAILOR_STATUS_CHECK, name="ck_tailor_jobs_status"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # The profile whose fact sheet and baselines it uses. None falls back to the user's first.
    profile_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("profiles.id", ondelete="SET NULL"), index=True
    )
    # None once the posting is deleted. The job stays, since the daily limit counts it and the
    # Spend page reports what it cost.
    posting_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("job_postings.id", ondelete="SET NULL"), index=True
    )
    baseline_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("resumes.id", ondelete="SET NULL")
    )
    result_resume_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("resumes.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(10), index=True)
    attempts: Mapped[int] = mapped_column(default=0)
    # Why the last attempt was turned down, in plain words for the user.
    reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    changelog: Mapped[list[str]] = mapped_column(JSON, default=list)
    gaps: Mapped[list[str]] = mapped_column(JSON, default=list)
    # Token counts summed over every attempt, for keeping an eye on cost.
    usage: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    # What its attempts cost in US dollars, as tailor priced them.
    cost_usd: Mapped[float] = mapped_column(default=0.0, server_default="0")
    # Tokens Anthropic's usage report shows for the job's minutes, once checked.
    billed: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    # The compiled PDF. Loaded only when downloaded; has_pdf answers whether it's there.
    result_pdf: Mapped[bytes | None] = mapped_column(LargeBinary, deferred=True)
    has_pdf: Mapped[bool] = column_property(result_pdf.column.is_not(None))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    baseline: Mapped[Resume | None] = relationship(foreign_keys=[baseline_id])
    result_resume: Mapped[Resume | None] = relationship(foreign_keys=[result_resume_id])


class RejectAnalysis(Base):
    """What a rejected application's posting asked for that the resume sent didn't show.

    One per application, queued when it's marked rejected and worked through by the tailor
    worker. Code grades the named requirements against the resume and the fact sheet; one model
    call in the tailor service reads the experience the posting asks for and writes the report.
    The inputs are read when it runs, so a re-run uses the current resume and fact sheet.
    """

    __tablename__ = "reject_analyses"
    __table_args__ = (
        UniqueConstraint("application_id", name="uq_reject_analyses_application_id"),
        CheckConstraint(TAILOR_STATUS_CHECK, name="ck_reject_analyses_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # The profile whose resumes and fact sheet it compares with. None falls back to the user's
    # first profile.
    profile_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("profiles.id", ondelete="SET NULL"), index=True
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE")
    )
    # The resume it was compared with: the one sent, else the profile's best baseline.
    resume_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("resumes.id", ondelete="SET NULL")
    )
    resume_label: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(10), index=True)
    # The furthest stage reached before the rejection: applied, screening, interview or offer.
    stage: Mapped[str | None] = mapped_column(String(20))
    score: Mapped[int | None]
    # Every checklist line, as matching graded it against the resume.
    checklist: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    # The named requirements not fully met, each with what the fact sheet and other resumes say.
    technology_gaps: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    # The model's report. Empty when the posting had no text to read or the call failed.
    summary: Mapped[str] = mapped_column(Text, default="")
    strengths: Mapped[list[str]] = mapped_column(JSON, default=list)
    experience_gaps: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    focus_areas: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    # The company's typical engineer beside the resume, and a rough estimate of how much the
    # rejection may have been about the person's profile (docs/rejection_research_spec.md,
    # chunk 4). Empty for reports from before it, or with no company research.
    comparison: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list, server_default="[]"
    )
    estimate: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, server_default="{}")
    # The company research snapshot the report was written with.
    company_research_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("company_research.id", ondelete="SET NULL")
    )
    # Why the written part is missing, in plain words for the user.
    note: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(String(40))
    usage: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    cost_usd: Mapped[float] = mapped_column(default=0.0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StatusEvent(Base):
    __tablename__ = "status_events"
    __table_args__ = (CheckConstraint(STATUS_CHECK, name="ck_status_events_status"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    application_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(20))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CommunicationKind(StrEnum):
    EMAIL = "email"
    CALL = "call"
    INTERVIEW = "interview"
    MESSAGE = "message"
    NOTE = "note"


class Direction(StrEnum):
    """Who it came from. A note to self has neither."""

    INBOUND = "inbound"
    OUTBOUND = "outbound"
    NONE = "none"


class Communication(Base):
    """One contact about an application, such as a recruiter's email or an interview, with an
    optional next step and when it's due. Logged contact also restarts the follow-up clock."""

    __tablename__ = "communications"
    __table_args__ = (
        CheckConstraint(
            "kind IN ({})".format(", ".join(f"'{kind.value}'" for kind in CommunicationKind)),
            name="ck_communications_kind",
        ),
        CheckConstraint(
            "direction IN ({})".format(", ".join(f"'{d.value}'" for d in Direction)),
            name="ck_communications_direction",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    application_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(20))
    direction: Mapped[str] = mapped_column(String(10))
    occurred_on: Mapped[date] = mapped_column(Date)
    contact: Mapped[str] = mapped_column(String(200), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    next_step: Mapped[str] = mapped_column(String(300), default="")
    next_step_due: Mapped[date | None] = mapped_column(Date)
    next_step_done: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    application: Mapped["Application"] = relationship(back_populates="communications")


class ScoutRun(Base):
    """One Scout run, worked through by the Scout worker.

    The settings are copied in when the run starts, so a later change doesn't alter it. A run
    records what it really cost, and counts against its user's daily runs unless it failed.
    Its leads are kept here, each with the posting it became once the user tracks it.
    """

    __tablename__ = "smartsearch_runs"
    __table_args__ = (
        CheckConstraint(TAILOR_STATUS_CHECK, name="ck_smartsearch_runs_status"),
        # One run waiting or running per account, held by the database so two requests at once
        # can't both get past the check in start_run.
        Index(
            "uq_smartsearch_runs_one_active",
            "user_id",
            unique=True,
            postgresql_where=text(TAILOR_ACTIVE_CHECK),
            sqlite_where=text(TAILOR_ACTIVE_CHECK),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # The profile whose fact sheet and baselines it uses. None falls back to the user's first.
    profile_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("profiles.id", ondelete="SET NULL"), index=True
    )
    status: Mapped[str] = mapped_column(String(10), index=True)
    # A name the user gave the run when starting it. Its leads are filed under it on the Leads
    # tab. None for runs started without one, which includes every run made before labels.
    label: Mapped[str | None] = mapped_column(String(60))
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    cost_usd: Mapped[float] = mapped_column(default=0.0)
    # Token and server tool counts, for keeping an eye on cost.
    usage: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    searches: Mapped[int] = mapped_column(default=0)
    pages: Mapped[int] = mapped_column(default=0)
    stop_code: Mapped[str | None] = mapped_column(String(20))
    stop_message: Mapped[str | None] = mapped_column(Text)
    leads: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    # What the agent did, in order: searches, results, pages, leads kept or refused, the stop.
    steps: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, server_default="[]")
    # US dollars by part (input, output, cache_write, cache_read, web_search), as tailor priced it.
    cost_breakdown: Mapped[dict[str, float]] = mapped_column(
        JSON, default=dict, server_default="{}"
    )
    # Every model request in order: its tokens, searches, page reads, seconds and cost.
    request_costs: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list, server_default="[]"
    )
    # Tokens Anthropic's usage report shows for the run's minutes, once checked.
    billed: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BillingCost(Base):
    """One line of Anthropic's daily cost report: what the organization was billed, in USD."""

    __tablename__ = "billing_costs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    day: Mapped[date] = mapped_column(Date, index=True)
    workspace_id: Mapped[str | None] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(String(300), default="")
    cost_type: Mapped[str | None] = mapped_column(String(40))
    model: Mapped[str | None] = mapped_column(String(80))
    token_type: Mapped[str | None] = mapped_column(String(80))
    amount_usd: Mapped[float] = mapped_column()
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CreditTopUp(Base):
    """Credit the owner added to the Anthropic account, entered by hand to work out a balance."""

    __tablename__ = "credit_topups"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    amount_usd: Mapped[float] = mapped_column()
    added_on: Mapped[date] = mapped_column(Date)
    note: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BillingState(Base):
    """The billing worker's last sync, and its last error, for the Spend page."""

    __tablename__ = "billing_state"

    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class CompanyKind(StrEnum):
    EMPLOYER = "employer"
    # A staffing or recruiting agency, which posts jobs for other companies.
    AGENCY = "agency"


class Company(Base):
    """One company in the catalog Companies runs read. Shared by every user, since it's built
    only from public listings.

    Its identity is its website domain (and, later, its hiring-system ID). Its names are aliases,
    so one company can be written many ways.
    """

    __tablename__ = "companies"
    __table_args__ = (UniqueConstraint("domain", name="uq_companies_domain"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200))
    # company_key(name), as on job_postings.
    key: Mapped[str] = mapped_column(String(200), index=True)
    # The matching key: key with "the" and "&" settled and spaces removed.
    compact_key: Mapped[str] = mapped_column(String(200), index=True)
    domain: Mapped[str | None] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(20), default=CompanyKind.EMPLOYER.value)
    # When code last tried its name on Greenhouse, Lever and Ashby, found or not.
    board_probed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    aliases: Mapped[list["CompanyAlias"]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )


class CompanyAlias(Base):
    """A matching key known to mean one company."""

    __tablename__ = "company_aliases"

    alias_key: Mapped[str] = mapped_column(String(200), primary_key=True)
    company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    # exact (its own name), source (same domain or hiring-system ID) or model.
    decided_by: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    company: Mapped[Company] = relationship(back_populates="aliases")


class CompanyMatchCheck(Base):
    """Two similar names, and whether they're one company. same is None while the pair waits for
    a model check, so no pair is ever asked twice."""

    __tablename__ = "company_match_checks"

    # Matching keys, ordered so key_a < key_b.
    key_a: Mapped[str] = mapped_column(String(200), primary_key=True)
    key_b: Mapped[str] = mapped_column(String(200), primary_key=True)
    same: Mapped[bool | None] = mapped_column(Boolean)
    # source or model, once decided.
    decided_by: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CompanyResearch(Base):
    """One research snapshot of a company: what it is, how it's funded, how it's hiring, what it
    builds with, the kind of people it employs and how it interviews, each fact with its source.

    Shared by every user, like the catalog. Append-only: a new snapshot is added, never written
    over, so changes over time show. The row is also the queue entry while it waits and runs;
    once finished it's never edited. requested_by is only for the cost record and the daily
    limit, never shown with the facts. See docs/rejection_research_spec.md, chunk 2.
    """

    __tablename__ = "company_research"
    __table_args__ = (
        CheckConstraint(TAILOR_STATUS_CHECK, name="ck_company_research_status"),
        Index("ix_company_research_company_id_finished_at", "company_id", "finished_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # A merge moves snapshots to the company kept (app.companies.catalog.move_company_rows) before
    # the other is deleted, so the cascade only matters if a company is deleted outright.
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    status: Mapped[str] = mapped_column(String(10), index=True)
    # Why it failed, in plain words.
    note: Mapped[str | None] = mapped_column(Text)
    # [{url, title, published_on}], at most 15, after the checks.
    sources: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    # One object per section, as cleaned by app.research.snapshots. Empty when nothing passed.
    profile: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    ownership: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    funding: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    size: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    events: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    engineering: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    workforce: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    interviewing: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # Per section: stated, estimated or unknown.
    confidence: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    # What the checks dropped, by reason, so their effect can be seen.
    dropped: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    searches: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    model: Mapped[str | None] = mapped_column(String(40))
    usage: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    cost_usd: Mapped[float] = mapped_column(default=0.0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CompanySource(Base):
    """One place a company's jobs are listed: its own hiring system, or a job board.

    Shared by every user. is_own sources are the ones the catalog worker reads (chunk 3).
    """

    __tablename__ = "company_sources"
    __table_args__ = (
        UniqueConstraint("source", "identifier", name="uq_company_sources_source_identifier"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    # greenhouse, lever, ashby, or a job board's id.
    source: Mapped[str] = mapped_column(String(40))
    # The hiring system's token, or the company's name as the board writes it.
    identifier: Mapped[str] = mapped_column(String(200))
    is_own: Mapped[bool] = mapped_column(Boolean, default=False)
    # ok, empty (answered with no jobs) or dead (gone or invalid).
    status: Mapped[str] = mapped_column(String(10), default="ok")
    open_jobs: Mapped[int] = mapped_column(Integer, default=0)
    failures: Mapped[int] = mapped_column(Integer, default=0)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_checked: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # When a run asked for this source to be read before it starts. The worker reads these first.
    wanted_by: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CatalogJob(Base):
    """One job, however many boards list it. Shared by every user."""

    __tablename__ = "catalog_jobs"
    __table_args__ = (Index("ix_catalog_jobs_company_id_title_key", "company_id", "title_key"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(200))
    title_key: Mapped[str] = mapped_column(String(200))
    location: Mapped[str | None] = mapped_column(String(300))
    work_mode: Mapped[str | None] = mapped_column(String(20))
    posted_on: Mapped[date | None] = mapped_column(Date)
    text: Mapped[str] = mapped_column(Text, default="")
    # SimHash of the text, as a signed 64-bit number. None when the text is too short to compare.
    fingerprint: Mapped[int | None] = mapped_column(BigInteger)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # When its own hiring system stopped listing it.
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Facts a Scout judge read from the posting alone, never from anyone's brief, so every
    # later run can use them without asking again: the level it hires at (a PostingLevel value)
    # and whether someone in the US can be hired (yes, no or unclear). facts_key is the posting's
    # text fingerprint when they were read, so a changed posting is read again.
    level: Mapped[str | None] = mapped_column(String(20))
    us_hireable: Mapped[str | None] = mapped_column(String(10))
    facts_key: Mapped[str | None] = mapped_column(String(64))

    sources: Mapped[list["CatalogJobSource"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="CatalogJobSource.first_seen"
    )


class CatalogJobSource(Base):
    """One listing of a catalog job: where it's listed, and its link there."""

    __tablename__ = "catalog_job_sources"
    __table_args__ = (
        UniqueConstraint("url", name="uq_catalog_job_sources_url"),
        Index("ix_catalog_job_sources_source_external_id", "source", "external_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("catalog_jobs.id", ondelete="CASCADE"), index=True
    )
    # greenhouse, lever, ashby, a job board's id, or web for any other page.
    source: Mapped[str] = mapped_column(String(40))
    url: Mapped[str] = mapped_column(String(2048))
    # The hiring system's id for the job, when the link or feed gives one.
    external_id: Mapped[str | None] = mapped_column(String(100))
    # The agency that posted this listing, when one did.
    agency_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("companies.id", ondelete="SET NULL"), index=True
    )
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    job: Mapped[CatalogJob] = relationship(back_populates="sources")


class BoardStat(Base):
    """How useful each source has been: listings recorded from it, and how many a judge kept."""

    __tablename__ = "board_stats"

    board: Mapped[str] = mapped_column(String(40), primary_key=True)
    listings_recorded: Mapped[int] = mapped_column(Integer, default=0)
    kept: Mapped[int] = mapped_column(Integer, default=0)
    last_kept_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProfileCompanyOrigin(StrEnum):
    # A hiring-system link in the user's own saved postings.
    POSTINGS = "postings"
    # Found for the profile's brief by a Companies run (chunk 4).
    DISCOVERED = "discovered"
    # A Scout lead for the profile was there.
    LEAD = "lead"
    BOARD = "board"


class ProfileCompany(Base):
    """A company on one profile's list: the companies its Companies runs read. Private to the user,
    unlike the catalog it points into."""

    __tablename__ = "profile_companies"

    profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("profiles.id", ondelete="CASCADE"), primary_key=True
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    origin: Mapped[str] = mapped_column(String(20))
    # Leads it has given this profile, and how many of them were turned down.
    leads: Mapped[int] = mapped_column(Integer, default=0)
    dismissed: Mapped[int] = mapped_column(Integer, default=0)
    last_lead_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ScoutBrief(Base):
    """A brief written for one Companies lead when the user asks for it, after the run.

    Queued by the Leads page, written by the Scout worker, which records its measured cost.
    It counts against its user's daily briefs unless it failed or crashed; then it can be asked for
    again, as a new row.
    """

    __tablename__ = "smartsearch_briefs"
    __table_args__ = (CheckConstraint(TAILOR_STATUS_CHECK, name="ck_smartsearch_briefs_status"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("smartsearch_runs.id", ondelete="CASCADE"), index=True
    )
    lead_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(10), index=True)
    cost_usd: Mapped[float] = mapped_column(default=0.0)
    usage: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    request_costs: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    # What Anthropic billed in the minutes it ran, filled in later by the billing worker.
    billed: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class JobJudgment(Base):
    """What the judge said about one catalog job for one profile's brief, so it's never asked
    twice and its answers can teach the profile's filters. Private to the user."""

    __tablename__ = "job_judgments"
    __table_args__ = (
        UniqueConstraint(
            "profile_id", "catalog_job_id", "brief_key", name="uq_job_judgments_profile_job_brief"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("profiles.id", ondelete="CASCADE"), index=True
    )
    catalog_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("catalog_jobs.id", ondelete="CASCADE"), index=True
    )
    # A hash of what the judge was told about the person: the brief and the fact sheet.
    brief_key: Mapped[str] = mapped_column(String(64))
    # A hash of the posting as judged, so a changed posting is judged again.
    text_key: Mapped[str] = mapped_column(String(64))
    keep: Mapped[bool] = mapped_column(Boolean)
    fit: Mapped[str] = mapped_column(String(20))
    location_ok: Mapped[bool] = mapped_column(Boolean)
    # Whether the job's own work uses the brief's Must use technologies. None from before.
    stack_ok: Mapped[bool | None] = mapped_column(Boolean)
    # The level the judge read from the posting, or None when it was unclear.
    posting_level: Mapped[str | None] = mapped_column(String(20))
    # True when Sonnet's second look made the final call, not just Haiku's screen.
    verified: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    reason: Mapped[str] = mapped_column(Text, default="")
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("smartsearch_runs.id", ondelete="SET NULL")
    )
    judged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
