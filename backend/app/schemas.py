import logging
import uuid
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    computed_field,
    field_validator,
    model_validator,
)

from app.models import (
    ApplicationStatus,
    CommunicationKind,
    Direction,
    EmploymentType,
    HiddenKind,
    PostingLevel,
    PostingTextStatus,
    Requirement,
    Role,
    RoleFamily,
    SourceKind,
    WorkMode,
)
from app.postings.parser import canonical_technology
from app.roles import is_admin_role
from app.scout.limits import (
    MAX_DESCRIPTION_LENGTH,
    MAX_LABEL_LENGTH,
    MAX_LOCATION_LENGTH,
    MAX_TITLE_PHRASES,
    POSTED_WITHIN_DAYS,
    AgencyMode,
    FitMode,
    Plan,
    Relocation,
    clean_title_phrases,
)
from app.scout.match import MAX_TECHNOLOGIES, clean_technologies
from app.security import MIN_PASSWORD_LENGTH

_logger = logging.getLogger(__name__)

RequiredName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]
OptionalName = Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)]
OptionalSource = Annotated[str, StringConstraints(strip_whitespace=True, max_length=80)]
OptionalUrl = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2048)]
TechnologyName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)
]
PostingText = Annotated[str, StringConstraints(max_length=50_000)]
Salary = Annotated[int, Field(ge=0, le=10_000_000)]
Years = Annotated[int, Field(ge=0, le=60)]


def _validate_http_url(value: str | None) -> str | None:
    # The URL is rendered as a link, so anything but http(s) (e.g. javascript:) is refused.
    if value and not value.lower().startswith(("http://", "https://")):
        raise ValueError("must start with http:// or https://")
    return value


def _is_web_link(value: Any) -> bool:
    return isinstance(value, str) and value.strip().lower().startswith(("http://", "https://"))


def _blank_to_none(value: str | None) -> str | None:
    return value.strip() or None if isinstance(value, str) else value


class LoginRequest(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=1024)


class RegisterRequest(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=1024)

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: str) -> str:
        email = value.strip().lower()
        local, _, domain = email.partition("@")
        if not local or "." not in domain or " " in email:
            raise ValueError("must be a valid email address")
        return email


class AuthConfig(BaseModel):
    registration_enabled: bool


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    role: Role
    plan: Plan

    @computed_field
    @property
    def is_admin(self) -> bool:
        """Admins see the Admin panel: users, roles, plans and spend."""
        return is_admin_role(self.role)


ProfileName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]


class ProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    software_skills: bool
    created_at: datetime


class ProfilesOut(BaseModel):
    """Every profile, oldest first, and the one this browser is using."""

    profiles: list[ProfileOut]
    active_id: uuid.UUID


class ProfileCreate(BaseModel):
    name: ProfileName
    software_skills: bool = False


class ProfileUpdate(BaseModel):
    name: ProfileName


SkillName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
# Enough for the usual spellings and abbreviations of one skill.
MAX_SKILL_ALIASES = 10


class SkillTermIn(BaseModel):
    """A skill for the active profile, and other ways postings write it ("AP" for "Accounts
    Payable")."""

    name: SkillName
    aliases: list[SkillName] = Field(default_factory=list, max_length=MAX_SKILL_ALIASES)

    @model_validator(mode="after")
    def _distinct(self) -> "SkillTermIn":
        seen = {self.name.lower()}
        for alias in self.aliases:
            if alias.lower() in seen:
                raise ValueError(f"{alias!r} is listed twice, or is the skill's own name")
            seen.add(alias.lower())
        return self


class SkillTermOut(BaseModel):
    id: uuid.UUID
    name: str
    aliases: list[str]
    # How many of the user's saved postings name it.
    postings: int


class SkillsOut(BaseModel):
    """The active profile's vocabulary, as the Skills tab shows it."""

    profile_id: uuid.UUID
    software_skills: bool
    # How many skills the built-in software list holds.
    software_count: int
    terms: list[SkillTermOut]
    max_terms: int


class SkillsUpdate(BaseModel):
    software_skills: bool


class VariantRules(BaseModel):
    """Which jobs a resume variant suits. An empty list means no limit on that field."""

    role_families: list[RoleFamily] = Field(default_factory=list, max_length=len(RoleFamily))
    levels: list[PostingLevel] = Field(default_factory=list, max_length=len(PostingLevel))
    # The job must name at least one of these, as required, nice to have or mentioned.
    focus_skills: list[TechnologyName] = Field(default_factory=list, max_length=30)
    # The job must not require any of these.
    deal_breakers: list[TechnologyName] = Field(default_factory=list, max_length=30)
    allow_clearance: bool = False
    max_years: Years | None = None
    min_score: int = Field(default=60, ge=0, le=100)

    @field_validator("focus_skills", "deal_breakers")
    @classmethod
    def _canonical(cls, names: list[str]) -> list[str]:
        """Use the parser's spelling, so "postgres" matches "PostgreSQL", and drop repeats."""
        return list(dict.fromkeys(canonical_technology(name) for name in names))


class ResumeOut(BaseModel):
    id: uuid.UUID
    profile_id: uuid.UUID
    label: str
    filename: str
    word_count: int
    created_at: datetime
    application_count: int
    is_variant: bool = False
    variant_rules: VariantRules = Field(default_factory=VariantRules)
    # "pdf" when an uploaded PDF: analysed, but not edited or tailored.
    source_format: Literal["tex", "pdf"] = "tex"
    # pdf_pages is set once the worker has compiled it; pdf_error when compiling failed.
    pdf_pages: int | None = None
    pdf_error: str | None = None


class VariantUpdate(BaseModel):
    is_variant: bool
    variant_rules: VariantRules = Field(default_factory=VariantRules)


class ResumeDetail(ResumeOut):
    source_tex: str


class ResumeSourceUpdate(BaseModel):
    """Edited LaTeX for a baseline. Its size and content are checked as an upload's are."""

    source_tex: str


class StatusEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    status: ApplicationStatus
    occurred_at: datetime


class ApplicationCreate(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    company: RequiredName
    role_title: RequiredName
    job_url: OptionalUrl | None = None
    location: OptionalName | None = None
    source: OptionalSource | None = None
    status: ApplicationStatus = ApplicationStatus.APPLIED.value
    applied_on: date | None = None
    resume_id: uuid.UUID | None = None
    posting_id: uuid.UUID | None = None
    notes: str | None = Field(default=None, max_length=20_000)
    posting_text: PostingText | None = None
    referrer: OptionalName | None = None

    _blanks = field_validator(
        "job_url", "location", "source", "notes", "posting_text", "referrer", mode="before"
    )(_blank_to_none)
    _url = field_validator("job_url")(_validate_http_url)


class ApplicationIntake(BaseModel):
    # An unknown field is an error, so a misnamed one can't be dropped without anyone noticing.
    model_config = ConfigDict(use_enum_values=True, extra="forbid")

    company: RequiredName = Field(description="Company name.")
    role_title: RequiredName = Field(description="Job title as posted.")
    resume_tex: str | None = Field(
        default=None,
        description="Full LaTeX source of the resume that was sent, unchanged. "
        "Leave out if no resume is known.",
    )
    resume_filename: Annotated[str, StringConstraints(max_length=255)] | None = Field(
        default=None, description="Filename of the resume. Must end in .tex."
    )
    resume_label: Annotated[str, StringConstraints(max_length=120)] | None = Field(
        default=None, description="Display name for the resume if it is new."
    )
    job_url: OptionalUrl | None = Field(default=None, description="Job posting URL.")
    source: OptionalSource | None = Field(
        default=None, description="Where the job was found, such as LinkedIn."
    )
    location: OptionalName | None = Field(default=None, description="Location, such as Remote.")
    status: ApplicationStatus = Field(
        default=ApplicationStatus.APPLIED.value,
        description="Only set this if the application is not new.",
    )
    applied_on: date | None = Field(
        default=None,
        description="The user's local date, YYYY-MM-DD. Defaults to the server's date, "
        "which may be a day off, so always send it.",
    )
    notes: str | None = Field(default=None, max_length=20_000, description="Free-form notes.")
    posting_text: PostingText | None = Field(
        default=None,
        description="Full posting exactly as written, with its line breaks and headings. "
        "The app parses it, so never summarize it.",
    )
    referrer: OptionalName | None = Field(default=None, description="Who referred the user.")

    _blanks = field_validator(
        "resume_filename",
        "resume_label",
        "job_url",
        "location",
        "source",
        "notes",
        "posting_text",
        "referrer",
        mode="before",
    )(_blank_to_none)
    _url = field_validator("job_url")(_validate_http_url)

    @field_validator("resume_tex")
    @classmethod
    def _check_tex(cls, value: str | None) -> str | None:
        # Not stripped, so the same file sent here and uploaded directly stays one resume.
        if value is None or not value.strip():
            return None
        # In JSON an unescaped \begin decodes to a backspace plus "egin" and is otherwise valid.
        if any(ord(char) < 32 and char not in "\t\n\r" for char in value):
            raise ValueError(
                "contains control characters. Escape every LaTeX backslash as \\\\ in the JSON"
            )
        return value

    @field_validator("resume_filename")
    @classmethod
    def _check_filename(cls, value: str | None) -> str | None:
        if value is None:
            return None
        filename = Path(value).name
        if not filename.lower().endswith(".tex"):
            raise ValueError("must end in .tex")
        return filename


class IntakeGuide(BaseModel):
    method: str
    path: str
    content_type: str
    bearer_token_required: bool
    max_resume_bytes: int
    instructions: list[str]
    json_schema: dict[str, Any]
    example: dict[str, Any]


class ApplicationUpdate(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    company: RequiredName | None = None
    role_title: RequiredName | None = None
    job_url: OptionalUrl | None = None
    location: OptionalName | None = None
    source: OptionalSource | None = None
    status: ApplicationStatus | None = None
    applied_on: date | None = None
    resume_id: uuid.UUID | None = None
    posting_id: uuid.UUID | None = None
    notes: str | None = Field(default=None, max_length=20_000)

    _blanks = field_validator("job_url", "location", "source", "notes", mode="before")(
        _blank_to_none
    )
    _url = field_validator("job_url")(_validate_http_url)

    @field_validator("company", "role_title", "status", "applied_on")
    @classmethod
    def _not_null(cls, value: object) -> object:
        if value is None:
            raise ValueError("cannot be null")
        return value


class ApplicationOut(BaseModel):
    id: uuid.UUID
    company: str
    role_title: str
    job_url: str | None
    location: str | None
    source: str | None
    status: ApplicationStatus
    applied_on: date
    notes: str | None
    resume_id: uuid.UUID | None
    resume_label: str | None
    # What the matcher suggested when this was logged, to compare with the resume sent.
    recommended_resume_id: uuid.UUID | None = None
    recommended_resume_label: str | None = None
    posting_id: uuid.UUID | None
    # The profile it was logged under, for filtering. None once that profile is deleted.
    profile_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime
    events: list[StatusEventOut]
    # The application or its posting came from Scout.
    from_scout: bool = False


class Totals(BaseModel):
    applications: int
    active: int
    this_week: int
    callbacks: int
    callback_rate: float
    interviews: int
    offers: int
    median_days_to_callback: float | None


class FunnelStage(BaseModel):
    stage: str
    count: int


class WeeklyPoint(BaseModel):
    week_start: date
    applications: int
    callbacks: int


class SourcePerformance(BaseModel):
    source: str
    applications: int
    callbacks: int
    callback_rate: float


class ResumePerformance(BaseModel):
    resume_id: uuid.UUID
    label: str
    uploaded_at: datetime
    applications: int
    callbacks: int
    interviews: int
    callback_rate: float


class StaleApplication(BaseModel):
    id: uuid.UUID
    company: str
    role_title: str
    applied_on: date
    # Days since the application, or since the last logged contact when there is one.
    days_waiting: int
    last_contact_on: date | None = None


class Overview(BaseModel):
    totals: Totals
    funnel: list[FunnelStage]
    weekly: list[WeeklyPoint]
    sources: list[SourcePerformance]
    resumes: list[ResumePerformance]
    stale: list[StaleApplication]


class KeywordRow(BaseModel):
    term: str
    applications: int
    share: float
    mentions: int
    first_used: date
    last_used: date
    early_share: float | None
    recent_share: float | None


Verdict = Literal["met", "weak", "missing", "unknown"]
EvidenceLevel = Literal["experience", "project", "listed", "none"]


class SkillDemand(BaseModel):
    """A skill postings ask for, and how your latest resume backs it."""

    name: str
    category: str
    required_in: int
    postings: int
    evidence: EvidenceLevel
    months: int
    verdict: Verdict


class ResumeEvidence(BaseModel):
    """What the parser read from one resume, so you can see what an ATS would see."""

    resume_id: uuid.UUID
    label: str
    uploaded_at: datetime
    sections: list[str]
    roles: int
    years: float | None
    degree: str | None
    backed: list[str]
    listed_only: list[str]


class MixSkill(BaseModel):
    """A required skill the chosen baseline misses but another baseline backs."""

    name: str
    baseline: str


class BaselinePick(BaseModel):
    """Which baseline to start a job from. Covered is false when every baseline's rules skip it."""

    resume_id: uuid.UUID
    label: str
    score: int
    covered: bool
    reasons: list[str]
    mix: list[MixSkill]


class VariantFit(BaseModel):
    """How one saved posting looks to one variant, by the variant's rules and the checklist."""

    posting_id: uuid.UUID
    company: str
    title: str
    score: int
    passes: bool
    # Why the variant's rules turn the job down. Empty when it passes.
    reasons: list[str]
    # True when this variant scores highest among the variants that pass the job.
    best: bool
    knocked_out: bool
    missing_required: list[str]
    weak_required: list[str]


class VariantSummary(BaseModel):
    resume_id: uuid.UUID
    label: str
    rules: VariantRules
    evidence: ResumeEvidence
    passes: int
    best_for: int
    fits: list[VariantFit]


class VariantOverlap(BaseModel):
    first: str
    second: str
    postings: int


class UncoveredPosting(BaseModel):
    posting_id: uuid.UUID
    company: str
    title: str
    closest_variant: str | None
    closest_score: int | None
    reasons: list[str]


class VariantReport(BaseModel):
    postings: int
    variants: list[VariantSummary]
    overlaps: list[VariantOverlap]
    uncovered: list[UncoveredPosting]


class ResumeInsights(BaseModel):
    latest_resume: str | None
    postings: int
    demand: list[SkillDemand]
    resumes: list[ResumeEvidence]


class PostingCreate(BaseModel):
    company: RequiredName
    title: RequiredName
    url: OptionalUrl | None = None
    location: OptionalName | None = None
    source: OptionalSource | None = None
    raw_text: PostingText | None = None
    referrer: OptionalName | None = None
    seen_on: date | None = None

    _blanks = field_validator("url", "location", "source", "raw_text", "referrer", mode="before")(
        _blank_to_none
    )
    _url = field_validator("url")(_validate_http_url)


class PostingUpdate(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    company: RequiredName | None = None
    title: RequiredName | None = None
    url: OptionalUrl | None = None
    location: OptionalName | None = None
    source: OptionalSource | None = None
    raw_text: PostingText | None = None
    seen_on: date | None = None
    level: PostingLevel | None = None
    role_family: RoleFamily | None = None
    years_required: Years | None = None
    salary_min: Salary | None = None
    salary_max: Salary | None = None
    work_mode: WorkMode | None = None
    employment_type: EmploymentType | None = None
    source_kind: SourceKind | None = None
    referred: bool | None = None
    referrer: OptionalName | None = None
    technologies: list[TechnologyName] | None = Field(default=None, max_length=100)

    _blanks = field_validator("url", "location", "source", "referrer", mode="before")(
        _blank_to_none
    )
    _url = field_validator("url")(_validate_http_url)

    @field_validator("company", "title", "seen_on", "referred", "raw_text")
    @classmethod
    def _not_null(cls, value: object) -> object:
        if value is None:
            raise ValueError("cannot be null")
        return value


class TechnologyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    category: str
    requirement: Requirement


class PostingApplication(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: ApplicationStatus
    applied_on: date


class StackCoverage(BaseModel):
    resume_label: str
    required_total: int
    required_matched: int
    missing_required: list[str]
    nice_total: int
    nice_matched: int
    missing_nice: list[str]
    coverage: float | None


class PostingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    company: str
    title: str
    url: str | None
    location: str | None
    source: str | None
    source_kind: SourceKind | None
    referred: bool
    referrer: str | None
    level: PostingLevel | None
    role_family: RoleFamily | None
    years_required: int | None
    salary_min: int | None
    salary_max: int | None
    work_mode: WorkMode | None
    employment_type: EmploymentType | None
    corrected_fields: list[str]
    seen_on: date
    updated_at: datetime
    technologies: list[TechnologyOut]
    applications: list[PostingApplication]
    # Reading the text from the link: pending, fetched or failed, with failed's reason in plain
    # words. None when nothing was read that way.
    text_status: PostingTextStatus | None = None
    text_error: str | None = None
    coverage: StackCoverage | None = None
    # Its skills were read before the user's skill terms last changed; the worker reads them again.
    skills_updating: bool = False
    # Made only because an application needed a posting: no link, no text and no edits.
    placeholder: bool = False
    staged_at: datetime | None = None
    staged_resume_id: uuid.UUID | None = None
    staged_resume_label: str | None = None
    staged_notes: str | None = None
    # Scout found it and tracked it.
    from_scout: bool = False
    # The profile it was saved under, for filtering. None once that profile is deleted.
    profile_id: uuid.UUID | None = None


class RelatedPosting(BaseModel):
    id: uuid.UUID
    company: str
    title: str
    level: PostingLevel | None
    shared_stack: list[str]
    stack_overlap: float
    likely_repeat: bool


class MatchPart(BaseModel):
    label: str
    score: int
    weight: int
    detail: str


class RequirementCheck(BaseModel):
    """One line of the checklist an ATS or recruiter would run against a resume."""

    kind: Literal["skill", "years", "degree", "clearance", "citizenship"]
    name: str
    requirement: Literal["required", "nice_to_have", "mentioned", "knockout"]
    verdict: Verdict
    detail: str


class MatchResume(BaseModel):
    id: uuid.UUID
    label: str
    required_matched: int
    required_weak: int
    required_total: int
    nice_matched: int
    nice_total: int


class SimilarApplication(BaseModel):
    company: str
    title: str
    status: ApplicationStatus
    similarity: float


class CallbackEstimate(BaseModel):
    rate: float
    similar_applications: int
    similar_callbacks: int
    applications: int
    callbacks: int
    closest: list[SimilarApplication]


class MatchEstimate(BaseModel):
    score: int
    confidence: Literal["low", "medium", "high"]
    applications: int
    resumes: int
    parts: list[MatchPart]
    checklist: list[RequirementCheck]
    knocked_out: bool
    resume_years: float | None
    matched_required: list[str]
    weak_required: list[str]
    missing_required: list[str]
    missing_nice: list[str]
    faded_required: list[str]
    latest_resume: str
    best_resume: MatchResume
    callback: CallbackEstimate | None


class TailorGap(BaseModel):
    """A checklist gap a rewrite could close, and what backs closing it."""

    name: str
    requirement: Literal["required", "nice_to_have"]
    verdict: Verdict
    # fact_sheet: the baseline leaves it out, but the fact sheet allows it.
    # baseline: another baseline backs it in a role or project.
    source: Literal["fact_sheet", "baseline"]
    baseline: str | None = None


class TailorAdvice(BaseModel):
    """Whether tailoring could improve on the suggested baseline, worked out by plain rules.

    ready is false when there's no baseline, with what's missing. An empty
    gaps list means a rewrite could only reword the baseline. unfixable names the required
    skills the baseline leaves out that no rewrite could add, since nothing backs them.
    """

    ready: bool
    missing: str | None = None
    gaps: list[TailorGap] = Field(default_factory=list)
    unfixable: list[str] = Field(default_factory=list)


class PostingDetail(PostingOut):
    raw_text: str
    related: list[RelatedPosting]
    match: MatchEstimate | None = None
    baseline: BaselinePick | None = None
    # The checklist for the staged resume itself, which may not be the best-scoring one.
    staged_match: MatchEstimate | None = None
    tailor_advice: TailorAdvice | None = None


class StageRequest(BaseModel):
    resume_id: uuid.UUID
    notes: str | None = Field(default=None, max_length=20_000)


class FactSheetOut(BaseModel):
    # The profile this sheet belongs to, so an editor can save its draft back to that profile.
    profile_id: uuid.UUID
    content: str
    updated_at: datetime | None


class FactSheetUpdate(BaseModel):
    content: str = Field(max_length=100_000)
    # The profile the draft was written for. The active profile when left out, but another
    # tab can change that while an editor is open, so editors send it.
    profile_id: uuid.UUID | None = None


class LabelCount(BaseModel):
    label: str
    count: int


class PostingWeek(BaseModel):
    week_start: date
    postings: int
    referred: int
    levels: dict[str, int]
    median_years: float | None
    median_salary: float | None
    median_coverage: float | None


class StackWeek(BaseModel):
    week_start: date
    shares: dict[str, float]


class StackRow(BaseModel):
    name: str
    category: str
    postings: int
    share: float
    required: int
    first_seen: date
    last_seen: date


class StackGap(BaseModel):
    name: str
    category: str
    required_in: int
    missing_in: int


class CompanyRow(BaseModel):
    company: str
    postings: int
    applications: int
    last_applied: date | None


class PostingTrends(BaseModel):
    total_postings: int
    applied_postings: int
    levels: list[LabelCount]
    role_families: list[LabelCount]
    source_kinds: list[LabelCount]
    referrals: list[SourcePerformance]
    weekly: list[PostingWeek]
    stack: list[StackRow]
    stack_weekly: list[StackWeek]
    companies: list[CompanyRow]
    scored_postings: int
    median_coverage: float | None
    gaps: list[StackGap]
    coverage_callbacks: list[SourcePerformance]
    recommendation_callbacks: list[SourcePerformance]


class KeywordTrends(BaseModel):
    applications: int
    min_applications: int
    most_used: list[KeywordRow]
    faded: list[KeywordRow]
    rising: list[KeywordRow]
    chart_terms: list[str]
    weekly: list[StackWeek]
    ignored: list[str]


class HideListingRequest(BaseModel):
    kind: HiddenKind
    company: RequiredName
    title: OptionalName | None = None
    url: OptionalUrl | None = None

    _blanks = field_validator("title", "url", mode="before")(_blank_to_none)
    _url = field_validator("url")(_validate_http_url)

    @model_validator(mode="after")
    def _listing_needs_url_and_title(self) -> "HideListingRequest":
        if self.kind == HiddenKind.LISTING and not (self.url and self.title):
            raise ValueError("hiding one job needs its URL and title")
        return self


class HiddenListingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: HiddenKind
    company: str
    title: str | None
    url: str | None
    created_at: datetime


class ListingHidden(BaseModel):
    """Why a listing is hidden, and the rule to remove to show it again."""

    id: uuid.UUID
    kind: HiddenKind


class ListingOut(BaseModel):
    id: str
    source: str
    company: str
    title: str
    # Only a saved posting entered by hand can lack one.
    url: str | None
    location: str | None
    description: str
    published_on: date | None
    level: PostingLevel | None
    role_family: RoleFamily | None
    years_required: int | None
    salary_min: int | None
    salary_max: int | None
    work_mode: WorkMode | None
    employment_type: EmploymentType | None
    technologies: list[TechnologyOut]
    posting_id: uuid.UUID | None
    application_status: ApplicationStatus | None
    match: MatchEstimate | None
    hidden: ListingHidden | None = None
    baseline: BaselinePick | None = None
    staged: bool = False
    staged_resume_id: uuid.UUID | None = None
    tailor_advice: TailorAdvice | None = None
    # The tracked posting behind it came from Scout.
    from_scout: bool = False


class ListingSourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    site: str
    state: Literal["ok", "failed", "needs_setup", "pending"]
    listings: int
    fetched_at: datetime | None
    message: str | None
    missing_settings: list[str]
    setup_url: str | None
    refreshing: bool = False


class ListingSort(StrEnum):
    MATCH = "match"
    NEWEST = "newest"
    SALARY = "salary"
    RELEVANCE = "relevance"


class ListingTracking(StrEnum):
    """Where a listing stands for the user. The status values mirror ApplicationStatus."""

    UNTRACKED = "untracked"
    TRACKED = "tracked"
    STAGED = "staged"
    APPLIED = "applied"
    SCREENING = "screening"
    INTERVIEW = "interview"
    OFFER = "offer"
    REJECTED = "rejected"
    GHOSTED = "ghosted"
    HIRING_PAUSED = "hiring_paused"
    WITHDRAWN = "withdrawn"


class ListingSearch(BaseModel):
    listings: list[ListingOut]
    total: int
    unfiltered_total: int
    page: int
    page_size: int
    sort: ListingSort
    technologies: list[LabelCount]
    has_profile: bool
    sources: list[ListingSourceOut]
    # Listings in these results that the user hid, whether or not they're shown.
    hidden_total: int = 0
    # How many results each tracking state would show on its own, after every other filter.
    tracking_counts: dict[ListingTracking, int] = {}
    # Some boards' results are older ones shown while newer are fetched. Search again shortly.
    refreshing: bool = False


class IgnoreTermRequest(BaseModel):
    term: Annotated[
        str, StringConstraints(strip_whitespace=True, to_lower=True, min_length=1, max_length=120)
    ]


class TailorJobOut(BaseModel):
    """A tailoring request and, once finished, what came of it."""

    id: uuid.UUID
    # None once the posting is deleted.
    posting_id: uuid.UUID | None
    status: Literal["queued", "running", "done", "failed"]
    attempts: int
    baseline_id: uuid.UUID | None
    baseline_label: str | None
    result_resume_id: uuid.UUID | None
    result_resume_label: str | None
    has_pdf: bool
    reasons: list[str]
    changelog: list[str]
    gaps: list[str]
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class TailorStatusOut(BaseModel):
    """The latest tailoring for a posting, and how many of today's the user has left."""

    job: TailorJobOut | None
    used_today: int
    # None when the admin lifted this user's daily caps (users.override_limits).
    daily_limit: int | None


class TailorUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


class CompileServiceResult(BaseModel):
    """What the tailor service returns for compiling a resume as it is."""

    ok: bool
    pdf_base64: str | None = None
    pages: int | None = None
    reasons: list[str] = Field(default_factory=list)


class TailorServiceResult(BaseModel):
    """What the tailor service returns for one attempt."""

    ok: bool
    tex: str | None = None
    pdf_base64: str | None = None
    pages: int | None = None
    changelog: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    model: str = ""
    usage: TailorUsage = Field(default_factory=TailorUsage)
    cost_usd: float = Field(default=0.0, ge=0)


# --- Rejection analysis ---------------------------------------------------------------------

# What to do about a named requirement the resume sent didn't fully meet. add_to_resume: the
# fact sheet allows it and the resume doesn't name it. show_in_a_role: it's only listed, or its
# years are short, and the fact sheet allows more. borrow: another of the profile's resumes shows
# it. limited: the fact sheet allows it only in certain places. learn: nothing backs it yet.
GapAction = Literal["add_to_resume", "show_in_a_role", "borrow", "limited", "learn"]
RejectStage = Literal["applied", "screening", "interview", "offer"]


class TechnologyGap(BaseModel):
    """A named requirement the resume sent didn't fully meet, and what backs closing it."""

    name: str
    requirement: Literal["required", "nice_to_have", "mentioned"]
    verdict: Verdict
    detail: str
    action: GapAction
    note: str


class ExperienceGapOut(BaseModel):
    area: str
    need: str
    evidence: str
    suggestion: str


class FocusAreaOut(BaseModel):
    kind: Literal["technology", "experience"]
    title: str
    why: str
    action: str


EstimateBand = Literal["likely", "possibly", "unlikely", "cant_tell"]


class ComparisonOut(BaseModel):
    """One measure of the company's typical engineer beside what the resume shows."""

    measure: Literal["years", "seniority", "industries", "employer_kinds"]
    typical: str
    yours: str
    note: str = ""


class EstimateOut(BaseModel):
    """A rough estimate of how much the rejection may have been about the person's profile."""

    band: EstimateBand = "cant_tell"
    reasons: list[str] = Field(default_factory=list, max_length=3)


class RejectAnalysisFieldsIn(BaseModel):
    """The written part of an analysis, as the tailor service returns it."""

    summary: str = Field(max_length=2_000)
    strengths: list[Annotated[str, StringConstraints(max_length=300)]] = Field(
        default_factory=list, max_length=8
    )
    experience_gaps: list[ExperienceGapOut] = Field(default_factory=list, max_length=8)
    focus_areas: list[FocusAreaOut] = Field(default_factory=list, max_length=5)
    comparison: list[ComparisonOut] = Field(default_factory=list, max_length=4)
    estimate: EstimateOut = Field(default_factory=EstimateOut)


# --- Company research ---------------------------------------------------------------------


class ResearchUsage(TailorUsage):
    web_search_requests: int = 0


class CompanyResearchServiceResult(BaseModel):
    """What the tailor service returns for one company: the snapshot as the model saved it,
    which app.research.snapshots checks before anything is stored."""

    model: str = ""
    usage: ResearchUsage = Field(default_factory=ResearchUsage)
    cost_usd: float = Field(default=0.0, ge=0)
    research: dict[str, Any]
    searches: int = Field(default=0, ge=0)


class CompanyResearchOut(BaseModel):
    """One snapshot's checked facts. Each fact's source is an index into sources."""

    id: uuid.UUID
    status: str
    note: str | None
    sources: list[dict[str, Any]]
    profile: dict[str, Any]
    ownership: dict[str, Any]
    funding: dict[str, Any]
    size: dict[str, Any]
    events: list[dict[str, Any]]
    engineering: dict[str, Any]
    workforce: dict[str, Any]
    interviewing: dict[str, Any]
    confidence: dict[str, str]
    searches: int
    created_at: datetime
    finished_at: datetime | None


class CompanyResearchView(BaseModel):
    """An application's company, as the drawer shows it. company is None when the catalog
    doesn't know it yet; it's added when research starts."""

    company: str | None
    # For a link to the company's page; None with company.
    company_id: uuid.UUID | None = None
    agency: bool = False
    latest: CompanyResearchOut | None = None
    # queued or running, while a snapshot of this company is under way.
    active: str | None = None
    # Why the newest attempt failed, when it did after the newest snapshot.
    failure: str | None = None
    used_today: int
    # None when the admin lifted this user's daily caps (users.override_limits).
    daily_limit: int | None
    # Why research can't start now, or None when it can.
    blocked: str | None = None


class RejectAnalysisServiceResult(BaseModel):
    """What the tailor service returns for one rejection analysis."""

    model: str = ""
    usage: TailorUsage = Field(default_factory=TailorUsage)
    cost_usd: float = Field(default=0.0, ge=0)
    analysis: RejectAnalysisFieldsIn


class RejectAnalysisOut(BaseModel):
    """One rejected application's report."""

    id: uuid.UUID
    application_id: uuid.UUID
    status: Literal["queued", "running", "done", "failed"]
    stage: RejectStage | None
    score: int | None
    resume_id: uuid.UUID | None
    resume_label: str | None
    checklist: list[RequirementCheck]
    technology_gaps: list[TechnologyGap]
    summary: str
    strengths: list[str]
    experience_gaps: list[ExperienceGapOut]
    focus_areas: list[FocusAreaOut]
    comparison: list[ComparisonOut] = Field(default_factory=list)
    # None for a report written before estimates, or one whose written part failed.
    estimate: EstimateOut | None = None
    note: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class ContextLine(BaseModel):
    """One line about the company, with where it came from: a research source, or Callback's
    catalog of the company's own job board (no link)."""

    label: str
    text: str
    source_url: str | None = None
    source_title: str | None = None
    # When the fact was published or seen, as YYYY-MM-DD, YYYY-MM or YYYY.
    as_of: str | None = None


class CompanyContextOut(BaseModel):
    """This company now, worked out each time the report is opened."""

    company: str | None
    researched_at: datetime | None = None
    lines: list[ContextLine] = Field(default_factory=list)


class RejectAnalysisStatusOut(BaseModel):
    """An application's analysis, if any, and whether another run can start now."""

    analysis: RejectAnalysisOut | None
    company: CompanyContextOut | None = None
    # The company is being researched; a waiting report runs once that's done.
    researching: bool = False
    used_today: int
    # None when the admin lifted this user's daily caps (users.override_limits).
    daily_limit: int | None
    # Why another run can't start now, or None when it can.
    rerun_blocked: str | None


class RejectedTechnology(BaseModel):
    """A named requirement rejected postings asked for that the resume sent didn't fully meet."""

    name: str
    # Rejections whose posting asked for it, by how strongly.
    required: int
    nice_to_have: int
    mentioned: int
    total: int
    missing: int
    weak: int
    # From the newest analysis that named it, so it reflects the current fact sheet.
    action: GapAction
    note: str


class ExperienceExample(BaseModel):
    application_id: uuid.UUID
    company: str
    role_title: str
    need: str


class RejectedExperience(BaseModel):
    """A kind of experience rejected postings asked for that the resume showed weakly."""

    area: str
    count: int
    examples: list[ExperienceExample]


class RejectionRow(BaseModel):
    application_id: uuid.UUID
    company: str
    role_title: str
    applied_on: date
    status: Literal["queued", "running", "done", "failed"]
    stage: RejectStage | None
    score: int | None
    top_focus: str | None
    finished_at: datetime | None


class RepeatCompany(BaseModel):
    """A company that rejected the profile more than once, with what's known about it."""

    company: str
    rejections: int
    what_it_does: str | None = None
    size: str | None = None
    # Layoffs, freezes and the like in the last 12 months, newest first, as short lines.
    recent_events: list[str] = Field(default_factory=list)
    researched_at: datetime | None = None


class CompanyCard(BaseModel):
    """One company on the user's Insights -> Companies list: its newest snapshot, in brief."""

    id: uuid.UUID
    name: str
    domain: str | None
    researched_at: datetime | None
    snapshots: int
    ownership: str | None = None
    stage: str | None = None
    headcount: int | None = None
    trend: str | None = None
    # Layoffs, freezes and the like in the last 12 months, newest first.
    recent_events: list[str] = Field(default_factory=list)
    # Whether Callback reads the company's own job board, and its open roles there now.
    can_see_hiring: bool = False
    open_roles: int | None = None
    open_engineering: int | None = None


class HiringWeekOut(BaseModel):
    week_start: date
    open: int
    open_engineering: int
    opened: int
    closed: int


class CompanyDetail(CompanyCard):
    """One company's page: every snapshot, newest first, and its weekly hiring history."""

    history: list[CompanyResearchOut] = Field(default_factory=list)
    weeks: list[HiringWeekOut] = Field(default_factory=list)
    median_days_open: float | None = None
    reposts_last_year: int = 0


class RejectionSummary(BaseModel):
    """What the profile's rejections asked for, added up, so the patterns show."""

    rejections: int
    analyzed: int
    waiting: int
    # Rejected applications with no analysis yet, such as ones from before this feature.
    not_analyzed: int
    stages: dict[str, int]
    average_score: float | None
    technologies: list[RejectedTechnology]
    experience: list[RejectedExperience]
    recent: list[RejectionRow]
    used_today: int
    # None when the admin lifted this user's daily caps (users.override_limits).
    daily_limit: int | None
    # Finished reports in each rough-estimate band.
    estimates: dict[str, int] = Field(default_factory=dict)
    repeat_companies: list[RepeatCompany] = Field(default_factory=list)


class QueuedCount(BaseModel):
    queued: int


class ScoutSettingsIn(BaseModel):
    """What to look for and where a run must stop."""

    description: str = Field(default="", max_length=MAX_DESCRIPTION_LENGTH)
    # Titles to search under: phrases the profile typed, each searched as an exact phrase.
    title_families: list[str] = Field(default_factory=list, max_length=MAX_TITLE_PHRASES)
    work_modes: list[WorkMode] = Field(default_factory=list, max_length=len(WorkMode))
    # A city, region or country, such as "Philadelphia, PA" or "US East Coast". Empty for anywhere.
    location: str = Field(default="", max_length=MAX_LOCATION_LENGTH)
    posted_within_days: int | None = None
    fit_mode: FitMode = FitMode.BALANCED
    # Scout has one engine now. Accepted from older pages, and ignored.
    engine: Literal["agent", "pipeline", "companies"] = "companies"
    agency_mode: AgencyMode = AgencyMode.INCLUDE
    relocation: Relocation = Relocation.NONE
    # Technologies a job must use, any one of them. None leaves it to the description.
    must_have: list[str] | None = Field(default=None, max_length=MAX_TECHNOLOGIES)
    # Technologies that rule a job out when it requires them. None leaves it to the description.
    rule_out: list[str] | None = Field(default=None, max_length=MAX_TECHNOLOGIES)
    # Levels wanted. Empty means any level except intern and manager.
    levels: list[PostingLevel] = Field(default_factory=list, max_length=len(PostingLevel))
    # Where a run stops (app.scout.limits.LIMITS). None keeps what was saved, or the
    # defaults. An older page's credit_cap is ignored.
    limits: dict[str, int] | None = None

    @field_validator("description", "location")
    @classmethod
    def _strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("title_families")
    @classmethod
    def _clean_title_phrases(cls, values: list[str]) -> list[str]:
        return clean_title_phrases(values)

    @field_validator("work_modes", "levels")
    @classmethod
    def _dedupe(cls, values: list[StrEnum]) -> list[StrEnum]:
        return list(dict.fromkeys(values))

    @field_validator("must_have", "rule_out")
    @classmethod
    def _clean_technologies(cls, values: list[str] | None) -> list[str] | None:
        return None if values is None else clean_technologies(values)

    @field_validator("posted_within_days")
    @classmethod
    def _known_window(cls, value: int | None) -> int | None:
        if value is not None and value not in POSTED_WITHIN_DAYS:
            raise ValueError(f"must be one of {', '.join(map(str, POSTED_WITHIN_DAYS))} or empty")
        return value


class ScoutRunStart(BaseModel):
    """What a person adds when starting a run. Everything else comes from the saved settings."""

    # A name for the run. Its leads are filed under it. Blank counts as none.
    label: str | None = Field(default=None, max_length=MAX_LABEL_LENGTH)

    @field_validator("label")
    @classmethod
    def _tidy_label(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return " ".join(value.split()) or None


class ScoutSettingsOut(ScoutSettingsIn):
    # What the run will use: the saved limits brought into today's ranges.
    limits: dict[str, int]
    updated_at: datetime | None


class ScoutLimitRangeOut(BaseModel):
    """One engine's range and wording for a limit."""

    label: str
    help: str
    minimum: int
    maximum: int
    default: int


class ScoutLimitOut(BaseModel):
    key: str
    label: str
    help: str
    unit: str
    minimum: int
    maximum: int
    default: int
    # Every engine's range, the same as above for engines without their own.
    by_engine: dict[str, ScoutLimitRangeOut] = Field(default_factory=dict)


class ScoutAllowanceOut(BaseModel):
    """What the user may still start today. Daily caps are None when the admin lifted them."""

    # Runs and briefs started since midnight UTC, not counting failed ones.
    runs_today: int
    daily_runs: int | None
    briefs_today: int
    daily_briefs: int | None
    # The most one run may spend, a safety stop for everyone.
    run_max_usd: float
    resets_at: datetime


class ScoutOut(BaseModel):
    """What the Scout page needs: the plan, today's allowance, the rules and settings."""

    plan: Plan
    allowance: ScoutAllowanceOut
    limits: list[ScoutLimitOut]
    posted_within_options: list[int]
    description_max_length: int
    # The posting parser's technology names, offered for Must use and Rule out.
    technology_names: list[str] = Field(default_factory=list)
    settings: ScoutSettingsOut
    # What this user's finished runs cost, by engine, measured.
    history: dict[str, "ScoutEngineHistory"] = Field(default_factory=dict)


class ScoutEngineHistory(BaseModel):
    runs: int
    leads: int
    # Pages read, or for Companies postings checked, so leads per page can be worked out.
    pages: int = 0
    cost_usd: float


class ScoutLeadOut(BaseModel):
    """One job a run found. posting_id is set once the user tracks it."""

    id: str
    company: str
    title: str
    url: str
    location: str = ""
    work_mode: str = ""
    posted: str = ""
    fit_summary: str = ""
    what_they_want: list[str] = Field(default_factory=list)
    you_match: list[str] = Field(default_factory=list)
    you_lack: list[str] = Field(default_factory=list)
    requirements: list[str] = Field(default_factory=list)
    company_notes: str = ""
    fit: str = ""
    agency: str = ""
    # The agency's listing, when the agent found the employer's own posting and kept that.
    agency_url: str = ""
    company_website: str = ""
    company_linkedin: str = ""
    salary: str = ""
    level: str = ""
    employment_type: str = ""
    offer_summary: str = ""
    how_to_apply: list[str] = Field(default_factory=list)
    application_tips: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    posting_id: uuid.UUID | None = None
    state: str = "new"
    decline_reason: str = ""
    # Companies leads get their brief on request: none, queued, running, done or failed. Empty
    # for leads whose run wrote the brief.
    brief_status: str = ""
    brief_error: str = ""


class ScoutStep(BaseModel):
    """One thing the agent did. request is the model request it was part of, from 1."""

    kind: Literal["note", "search", "results", "fetch", "lead", "skipped", "error", "stop"]
    detail: str = Field(max_length=500)
    request: int = 0


class ScoutRunOut(BaseModel):
    id: uuid.UUID
    status: str
    # None for a run started without one, and for every run made before labels.
    label: str | None = None
    description: str
    # The engine and lead target the run was started with, copied from its saved settings.
    engine: Literal["agent", "pipeline", "companies"]
    target_leads: int | None
    # The most the run could spend, or None for runs from before the dollar ceiling.
    budget_usd: float | None = None
    cost_usd: float
    searches: int
    pages: int
    stop_code: str | None
    stop_message: str | None
    leads: list[ScoutLeadOut]
    steps: list[ScoutStep]
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class ScoutServiceUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    web_search_requests: int = 0
    web_fetch_requests: int = 0


class ScoutServiceLead(BaseModel):
    """A lead as the tailor service returns it, checked again before it's stored."""

    company: str = Field(max_length=300)
    title: str = Field(max_length=300)
    url: str = Field(max_length=2_048)
    location: str = Field(default="", max_length=300)
    work_mode: str = Field(default="", max_length=300)
    posted: str = Field(default="", max_length=300)
    fit_summary: str = Field(default="", max_length=2_000)
    what_they_want: list[str] = Field(default_factory=list, max_length=10)
    you_match: list[str] = Field(default_factory=list, max_length=10)
    you_lack: list[str] = Field(default_factory=list, max_length=10)
    requirements: list[str] = Field(default_factory=list, max_length=30)
    company_notes: str = Field(default="", max_length=2_000)
    fit: str = Field(default="", max_length=300)
    agency: str = Field(default="", max_length=300)
    agency_url: str = Field(default="", max_length=2_048)
    company_website: str = Field(default="", max_length=2_048)
    company_linkedin: str = Field(default="", max_length=2_048)
    salary: str = Field(default="", max_length=300)
    level: str = Field(default="", max_length=300)
    employment_type: str = Field(default="", max_length=300)
    offer_summary: str = Field(default="", max_length=2_000)
    how_to_apply: list[str] = Field(default_factory=list, max_length=10)
    application_tips: list[str] = Field(default_factory=list, max_length=10)
    posting_text: str = Field(default="", max_length=12_000)
    sources: list[str] = Field(default_factory=list, max_length=10)
    # "none" for a Companies lead, whose brief is written when the user asks for it.
    brief_status: Literal["", "none"] = ""

    # Every link here becomes an href in the UI. tailor already checks them, but a lead is
    # untrusted web content, so a link that isn't http(s) is dropped again here.
    _link = field_validator("url")(_validate_http_url)

    @field_validator("agency_url", "company_website", "company_linkedin", mode="before")
    @classmethod
    def _web_link_or_blank(cls, value: Any) -> Any:
        return value if not value or _is_web_link(value) else ""

    @field_validator("sources", mode="before")
    @classmethod
    def _web_links_only(cls, value: Any) -> Any:
        return [item for item in value if _is_web_link(item)] if isinstance(value, list) else value


class ScoutServiceResult(BaseModel):
    """What the tailor service returns for one whole Scout run."""

    leads: list[ScoutServiceLead] = Field(default_factory=list, max_length=60)

    @field_validator("leads", mode="before")
    @classmethod
    def _leads_with_web_links(cls, value: Any) -> Any:
        """A lead whose own link isn't http(s) is dropped, not the whole run with it."""
        if not isinstance(value, list):
            return value
        kept = [
            lead for lead in value if not isinstance(lead, dict) or _is_web_link(lead.get("url"))
        ]
        if len(kept) < len(value):
            _logger.warning("Dropped %d Scout leads without a web link", len(value) - len(kept))
        return kept

    stop_code: str = Field(max_length=20)
    stop_message: str = Field(max_length=500)
    searches: int = 0
    pages: int = 0
    requests: int = 0
    usage: ScoutServiceUsage = Field(default_factory=ScoutServiceUsage)
    cost_usd: float = Field(default=0.0, ge=0)
    model: str = ""
    steps: list[ScoutStep] = Field(default_factory=list, max_length=500)
    cost_breakdown: dict[str, float] = Field(default_factory=dict)
    request_costs: list[dict[str, Any]] = Field(default_factory=list, max_length=300)


class ReportLimitOut(BaseModel):
    key: str
    label: str
    used: float
    limit: float
    unit: str
    stopped_run: bool


class ReportCostOut(BaseModel):
    key: str
    label: str
    usd: float
    share: float


class ReportQueryOut(BaseModel):
    request: int
    query: str
    results: int | None
    new: int | None
    repeated: bool


class ReportPageOut(BaseModel):
    request: int
    url: str
    host: str
    became_lead: bool


class ReportFindingOut(BaseModel):
    level: Literal["good", "info", "warning"]
    title: str
    detail: str


class ReportCountOut(BaseModel):
    name: str
    count: int


class ScoutReportOut(BaseModel):
    """A run read back by plain rules: limits used, cost, what it did, and findings."""

    run: ScoutRunOut
    minutes_used: float | None
    requests: int
    nudges: int
    limits: list[ReportLimitOut]
    cost_usd: float
    cost_per_lead_usd: float | None
    costs: list[ReportCostOut]
    queries: list[ReportQueryOut]
    pages: list[ReportPageOut]
    refused: list[str]
    errors: list[ReportCountOut]
    lead_hosts: list[ReportCountOut]
    findings: list[ReportFindingOut]


class ScoutLeadApplication(BaseModel):
    id: uuid.UUID
    status: ApplicationStatus
    applied_on: date
    resume_label: str | None
    communications: int = 0
    last_contact_on: date | None = None
    next_step: str = ""
    next_step_due: date | None = None


class ScoutLeadItem(ScoutLeadOut):
    """A lead in the Scout module, across runs: new, later, dismissed, or applied to."""

    run_id: uuid.UUID
    found_at: datetime
    # The label of the run this lead is listed under, or None when that run has none.
    label: str | None = None
    state: Literal["new", "later", "dismissed", "expired"]
    decline_reason: str = ""
    application: ScoutLeadApplication | None = None


class LeadStateUpdate(BaseModel):
    state: Literal["new", "later", "dismissed", "expired"]
    reason: str = Field(default="", max_length=300)


class CommunicationIn(BaseModel):
    """One contact about an application. A next step's due date needs the step itself."""

    kind: CommunicationKind
    direction: Direction = Direction.NONE
    occurred_on: date
    contact: str = Field(default="", max_length=200)
    summary: str = Field(default="", max_length=5_000)
    next_step: str = Field(default="", max_length=300)
    next_step_due: date | None = None
    next_step_done: bool = False

    @field_validator("contact", "summary", "next_step")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def _due_needs_a_step(self) -> "CommunicationIn":
        if self.next_step_due and not self.next_step:
            raise ValueError("a due date needs a next step")
        if self.kind == CommunicationKind.NOTE:
            self.direction = Direction.NONE
        return self


class CommunicationUpdate(BaseModel):
    """A partial change. Only the fields sent are changed."""

    kind: CommunicationKind | None = None
    direction: Direction | None = None
    occurred_on: date | None = None
    contact: str | None = Field(default=None, max_length=200)
    summary: str | None = Field(default=None, max_length=5_000)
    next_step: str | None = Field(default=None, max_length=300)
    next_step_due: date | None = None
    next_step_done: bool | None = None


class CommunicationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    application_id: uuid.UUID
    kind: CommunicationKind
    direction: Direction
    occurred_on: date
    contact: str
    summary: str
    next_step: str
    next_step_due: date | None
    next_step_done: bool
    created_at: datetime
    updated_at: datetime


class NextStepOut(BaseModel):
    """An open next step from a communication, with the application it's about."""

    communication_id: uuid.UUID
    application_id: uuid.UUID
    company: str
    role_title: str
    next_step: str
    next_step_due: date | None
    overdue: bool


class TopUpIn(BaseModel):
    amount_usd: float = Field(gt=0, le=100_000)
    added_on: date
    note: str = Field(default="", max_length=200)


class TopUpOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    amount_usd: float
    added_on: date
    note: str


class SpendSetup(BaseModel):
    usage_synced_at: str | None
    cost_synced_at: str | None
    last_error: str
    api_key_filter: bool
    workspace_filter: bool


class SpendBalance(BaseModel):
    topups_usd: float
    # What Anthropic billed the whole organization from the first top-up on.
    billed_since_usd: float
    balance_usd: float
    since: date | None
    topups: list[TopUpOut]


class SpendDay(BaseModel):
    day: date
    measured_usd: float
    billed_usd: float | None


class SpendUser(BaseModel):
    email: str
    runs: int
    tailorings: int
    # Rejection reports and company research snapshots each person's rejections started.
    analyses: int = 0
    research: int = 0
    leads: int
    measured_usd: float
    median_run_usd: float | None
    per_lead_usd: float | None


class SpendBudget(BaseModel):
    budget_usd: float
    runs: int
    leads: int
    tailorings: int


class SpendQuote(BaseModel):
    runs_measured: int
    median_run_usd: float | None
    mean_run_usd: float | None
    p90_run_usd: float | None
    max_run_usd: float | None
    median_per_lead_usd: float | None
    median_tailoring_usd: float | None
    budgets: list[SpendBudget]


class SpendTokens(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    web_search_requests: int = 0


class SpendBilled(SpendTokens):
    window_start: str
    window_end: str
    shared_with: int
    checked_at: str


class SpendRequest(BaseModel):
    request: int
    # The pipeline engine's stage (find, judge or brief) and its model. Empty for the agent.
    stage: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    web_search_requests: int = 0
    web_fetch_requests: int = 0
    seconds: float = 0
    cost_usd: float = 0


class SpendJob(BaseModel):
    """One model call Callback measured: a Scout run, tailoring, rejection report or company
    research. Only runs and tailorings are matched to what was billed."""

    id: uuid.UUID
    kind: Literal["scout", "tailoring", "rejection_analysis", "company_research"]
    email: str
    created_at: datetime
    status: str
    stop: str | None
    leads: int
    measured_usd: float
    measured: SpendTokens
    billed: SpendBilled | None
    requests: list[SpendRequest]


class SpendOut(BaseModel):
    setup: SpendSetup
    balance: SpendBalance
    quote: SpendQuote
    days: list[SpendDay]
    users: list[SpendUser]
    jobs: list[SpendJob]


class AdminUserOut(UserOut):
    """One user as the Admin panel lists them."""

    override_limits: bool
    created_at: datetime


class AppConfigOut(BaseModel):
    """One setting in Admin -> Settings: its range, default and the value in force."""

    key: str
    label: str
    help: str
    unit: str
    kind: Literal["int", "float"]
    minimum: float
    maximum: float
    default: float
    value: float
    # Whether the admin changed it; False means it's at its default.
    saved: bool
    updated_at: datetime | None
    updated_by: str | None


class AppConfigUpdate(BaseModel):
    value: float


class CatalogSourceStatOut(BaseModel):
    """One place listings come from: a job board, a hiring system, or other web pages."""

    source: str
    name: str
    # Listings recorded from it, and how many of their jobs a Scout judge kept.
    listings_recorded: int
    kept: int
    last_kept_at: datetime | None
    # Companies seen listing jobs there.
    companies: int


class CatalogStatsOut(BaseModel):
    """The Companies catalog as the Admin panel's Boards tab shows it."""

    companies: int
    agencies: int
    jobs: int
    sources: list[CatalogSourceStatOut]


class AdminUserUpdate(BaseModel):
    """A change to one user's role, plan or limits override. Fields left out stay as they are."""

    role: Role | None = None
    plan: Plan | None = None
    override_limits: bool | None = None

    @model_validator(mode="after")
    def _something_to_change(self) -> "AdminUserUpdate":
        if self.role is None and self.plan is None and self.override_limits is None:
            raise ValueError("give a role, a plan or override_limits")
        return self
