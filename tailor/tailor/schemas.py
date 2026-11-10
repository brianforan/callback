from datetime import date
from typing import Annotated, Any, Literal, get_args

from pydantic import BaseModel, Field, StringConstraints

Line = Annotated[str, StringConstraints(max_length=2_000)]


class Gap(BaseModel):
    """One checklist line the baseline doesn't fully meet, as the API's matcher graded it."""

    kind: str = Field(max_length=40)
    name: str = Field(max_length=200)
    requirement: str = Field(max_length=40)
    verdict: str = Field(max_length=40)
    detail: str = Field(max_length=500)


class MixSkill(BaseModel):
    """A required skill another of the user's baselines backs, with that baseline's lines for it."""

    name: str = Field(max_length=200)
    baseline: str = Field(max_length=120)
    evidence: list[Line] = Field(default_factory=list, max_length=5)


class Posting(BaseModel):
    company: str = Field(max_length=200)
    title: str = Field(max_length=200)
    text: str = Field(max_length=60_000)


class TailorRequest(BaseModel):
    """Everything one tailoring needs. The service keeps nothing between requests."""

    baseline_tex: str = Field(min_length=1, max_length=200_000)
    posting: Posting
    gaps: list[Gap] = Field(default_factory=list, max_length=100)
    mix: list[MixSkill] = Field(default_factory=list, max_length=50)
    fact_sheet: str = Field(min_length=1, max_length=100_000)
    rules: str = Field(min_length=1, max_length=20_000)
    # Why the previous attempt was turned down, when this is a retry.
    feedback: list[Line] = Field(default_factory=list, max_length=20)


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


class TailorResponse(BaseModel):
    """A finished attempt. ok is false when the result was unusable, with the reasons why."""

    ok: bool
    tex: str | None = None
    pdf_base64: str | None = None
    pages: int | None = None
    changelog: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    model: str
    usage: Usage
    # What the attempt cost in US dollars, at the configured prices.
    cost_usd: float = 0.0


class CompileResponse(BaseModel):
    """The compiler service's answer: the PDF, or why there isn't one."""

    ok: bool
    pdf_base64: str | None = None
    pages: int | None = None
    reasons: list[str] = Field(default_factory=list)


SHORT_TEXT_MAX = 300
# Characters of a fetched posting kept and sent back.
POSTING_TEXT_MAX = 60_000
ShortText = Annotated[str, StringConstraints(max_length=SHORT_TEXT_MAX)]
LongText = Annotated[str, StringConstraints(max_length=2_000)]


class ScoutLimits(BaseModel):
    """Where a run must stop. The API checks the user's ranges; these are sanity bounds."""

    target_leads: int = Field(ge=1, le=60)
    dry_streak: int = Field(ge=1, le=50)
    max_searches: int = Field(ge=1, le=100)
    max_pages: int = Field(ge=1, le=500)
    max_minutes: int = Field(ge=1, le=60)


class KnownJob(BaseModel):
    """A job the user already has, so the agent doesn't bring it back."""

    company: str = Field(max_length=200)
    title: str | None = Field(default=None, max_length=200)
    url: str | None = Field(default=None, max_length=2_048)


class DeclinedJob(BaseModel):
    """A job the person turned down, and why, so the agent skips jobs like it."""

    company: str = Field(max_length=200)
    title: str = Field(max_length=200)
    reason: str = Field(max_length=300)


class BaselineText(BaseModel):
    label: str = Field(max_length=120)
    text: str = Field(max_length=20_000)


class ScoutRequest(BaseModel):
    """Everything one run needs. The service keeps nothing between requests."""

    description: str = Field(min_length=1, max_length=600)
    title_families: list[ShortText] = Field(default_factory=list, max_length=20)
    work_modes: list[ShortText] = Field(default_factory=list, max_length=5)
    location: str = Field(default="", max_length=120)
    posted_within_days: int | None = Field(default=None, ge=1, le=365)
    fit_mode: Literal["wide", "balanced", "close"] = "balanced"
    # include keeps agency listings, find_employer looks for the employer's own posting first,
    # and skip records none.
    agency_mode: Literal["include", "find_employer", "skip"] = "include"
    # Where they'd move for an onsite or hybrid job: nowhere, a major US metro area, or anywhere
    # in the US.
    relocation: Literal["none", "metro", "anywhere"] = "none"
    limits: ScoutLimits
    # The most this run may spend, in US dollars. Checked before every model request.
    budget_usd: float = Field(gt=0, le=20)
    fact_sheet: str = Field(default="", max_length=100_000)
    baselines: list[BaselineText] = Field(default_factory=list, max_length=10)
    known_jobs: list[KnownJob] = Field(default_factory=list, max_length=300)
    hidden_companies: list[ShortText] = Field(default_factory=list, max_length=300)
    declined: list[DeclinedJob] = Field(default_factory=list, max_length=50)
    # Pages earlier runs with the same brief opened and passed on, so they aren't paid for again.
    passed_pages: list[Annotated[str, StringConstraints(max_length=2_048)]] = Field(
        default_factory=list, max_length=200
    )
    # Technologies the job's own work must use, any one of them; empty for no stack rule. Code
    # has already checked each posting names one; the judge checks the work really uses it.
    must_have: list[ShortText] = Field(default_factory=list, max_length=40)
    # Technologies that rule a job out when it requires them.
    rule_out: list[ShortText] = Field(default_factory=list, max_length=20)
    # Levels wanted (intern, junior, mid, senior, staff, principal, manager). Empty for any.
    levels: list[ShortText] = Field(default_factory=list, max_length=10)
    today: date


StopCode = Literal[
    "target_reached",
    "dry_streak",
    "max_searches",
    "max_pages",
    "time_limit",
    "budget",
    "finished",
    "max_requests",
    "refusal",
    "model_error",
]


StepKind = Literal["note", "search", "results", "fetch", "lead", "skipped", "error", "stop"]


class SearchStep(BaseModel):
    """One thing the agent did, for the run's log. request is the model request it was part of."""

    kind: StepKind
    detail: str = Field(max_length=500)
    request: int


class SearchUsage(Usage):
    web_search_requests: int = 0
    web_fetch_requests: int = 0


class RequestCost(SearchUsage):
    """One model request in a run: what it used and what it cost."""

    request: int
    cost_usd: float
    seconds: float


class StageCost(BaseModel):
    """What one stage call used and cost. Requests are numbered within the call, from 1."""

    model: str
    usage: SearchUsage = Field(default_factory=SearchUsage)
    cost_usd: float = 0.0
    cost_breakdown: dict[str, float] = Field(default_factory=dict)
    request_costs: list[RequestCost] = Field(default_factory=list)


class CompanyResearchRequest(BaseModel):
    """One company to research. title and location are of the job applied to; catalog_sources
    are where Callback sees the company's jobs, such as "greenhouse:acme (own)"."""

    company: ShortText = Field(min_length=1)
    domain: ShortText = ""
    title: ShortText = ""
    location: ShortText = ""
    catalog_sources: list[ShortText] = Field(default_factory=list, max_length=20)
    open_jobs: int = Field(default=0, ge=0)
    today: date


class CompanyResearchResponse(StageCost):
    """The snapshot as the model saved it, unchecked: Callback checks every fact before it's
    stored. searches is how many web searches it used."""

    research: dict[str, Any]
    searches: int = 0


class PostingFetchRequest(BaseModel):
    """A link a user saved a posting with, to read its text from."""

    url: str = Field(min_length=1, max_length=2_048)


class PostingText(BaseModel):
    """A posting's text and facts, as code fetched them."""

    url: str = Field(max_length=2_048)
    ok: bool = True
    source: ShortText = "page"
    title: ShortText = ""
    company: ShortText = ""
    location: ShortText = ""
    work_mode: ShortText = ""
    employment_type: ShortText = ""
    salary: ShortText = ""
    posted_at: ShortText = ""
    text: str = Field(default="", max_length=POSTING_TEXT_MAX)
    error: ShortText = ""
    # Callback's own check, from its fixed list of the largest US metro areas: the posting's
    # location is in one. Set by code, never read from the posting.
    major_metro: bool = False


PostingLevel = Literal[
    "intern", "junior", "mid", "senior", "staff", "principal", "manager", "unclear"
]


class Judgment(BaseModel):
    keep: bool
    fit: Literal["strong", "good", "stretch", "none"]
    work_match: LongText
    location_ok: bool
    # Read from the posting alone, whoever the brief is for, so Callback can store them on the
    # shared job: whether someone living in the US can be hired, and the level it hires at.
    us_hireable: Literal["yes", "no", "unclear"] = "unclear"
    posting_level: PostingLevel = "unclear"
    # Whether the job's own work uses one of the brief's Must use technologies. True when the
    # brief has none.
    stack_ok: bool = True
    agency: ShortText = ""
    reason: LongText


class JudgeRequest(BaseModel):
    brief: ScoutRequest
    posting: PostingText
    # screen: Haiku, quick, for every posting code lets through. verify: Sonnet's second look at
    # a posting the screen kept, before it becomes a lead.
    tier: Literal["screen", "verify"] = "screen"


class JudgeResponse(StageCost):
    judgment: Judgment


class BriefRequest(BaseModel):
    brief: ScoutRequest
    posting: PostingText
    judgment: Judgment


class BriefFields(BaseModel):
    """The lead fields the brief writer fills. Links and the posting text come from code."""

    company: ShortText
    title: ShortText
    location: ShortText = ""
    work_mode: ShortText = ""
    posted: ShortText = ""
    fit_summary: LongText
    what_they_want: list[LongText] = Field(default_factory=list, max_length=10)
    you_match: list[LongText] = Field(default_factory=list, max_length=10)
    you_lack: list[LongText] = Field(default_factory=list, max_length=10)
    requirements: list[ShortText] = Field(default_factory=list, max_length=30)
    company_notes: LongText = ""
    salary: ShortText = ""
    level: ShortText = ""
    employment_type: ShortText = ""
    offer_summary: LongText = ""
    how_to_apply: list[LongText] = Field(default_factory=list, max_length=10)
    application_tips: list[LongText] = Field(default_factory=list, max_length=10)


class BriefResponse(StageCost):
    lead: BriefFields


class CompanySide(BaseModel):
    """One name in a pair the Companies engine couldn't settle by code."""

    name: ShortText = Field(min_length=1)
    domain: ShortText = ""


class CompanyPair(BaseModel):
    a: CompanySide
    b: CompanySide


class SameCompanyRequest(BaseModel):
    pairs: list[CompanyPair] = Field(min_length=1, max_length=20)


class SameCompany(BaseModel):
    same: bool


class DiscoverRequest(ScoutRequest):
    """A Companies run's search for companies that fit its brief."""

    technologies: list[ShortText] = Field(default_factory=list, max_length=20)
    listed_count: int = Field(default=0, ge=0)
    companies_wanted: int = Field(ge=1, le=60)


class FoundCompany(BaseModel):
    name: ShortText = Field(min_length=1)
    website: Annotated[str, StringConstraints(max_length=2_048)] = ""
    careers_url: Annotated[str, StringConstraints(max_length=2_048)] = ""
    why: LongText = ""


class DiscoverResponse(StageCost):
    companies: list[FoundCompany]
    stop_code: StopCode
    stop_message: str
    searches: int
    steps: list[SearchStep] = Field(default_factory=list)


class CareersRequest(BaseModel):
    urls: list[Annotated[str, StringConstraints(max_length=2_048)]] = Field(
        min_length=1, max_length=20
    )


class CareersPage(BaseModel):
    """Greenhouse, Lever or Ashby links found on one careers page."""

    url: str = Field(max_length=2_048)
    ok: bool
    links: list[Annotated[str, StringConstraints(max_length=2_048)]] = Field(default_factory=list)
    error: ShortText = ""


class CareersResponse(BaseModel):
    pages: list[CareersPage]


class SameCompanyResponse(StageCost):
    """One answer per pair, in the pairs' order."""

    answers: list[SameCompany]


class PricedUsage(SearchUsage):
    """One stored request's usage, to be priced again."""

    model: ShortText


class RepriceRequest(BaseModel):
    requests: list[PricedUsage] = Field(min_length=1, max_length=1_000)


class RepricedCost(BaseModel):
    cost_usd: float
    cost_breakdown: dict[str, float]


class RepriceResponse(BaseModel):
    costs: list[RepricedCost]


# --- Rejection analysis ---------------------------------------------------------------------

# Kinds of experience a posting can ask for beyond named technologies. Fixed, so Callback can
# count them across rejections without reading free text.
ExperienceArea = Literal[
    "domain",
    "scale_and_performance",
    "system_design",
    "leadership_and_mentoring",
    "ownership_and_impact",
    "cloud_and_infrastructure",
    "data",
    "security_and_compliance",
    "testing_and_quality",
    "product_and_customers",
    "collaboration_and_communication",
    "other",
]
EXPERIENCE_AREAS: tuple[str, ...] = get_args(ExperienceArea)


class RejectCompanyContext(BaseModel):
    """What Callback knows about the company, as plain lines written by its code: the workforce
    profile from the company's research snapshot, and what the company was doing around the
    rejection (layoffs, freezes, engineering openings)."""

    name: ShortText
    workforce: list[ShortText] = Field(default_factory=list, max_length=12)
    signals: list[ShortText] = Field(default_factory=list, max_length=12)


class RejectAnalysisRequest(BaseModel):
    """One rejected application: the posting, the resume sent, the fact sheet and the checklist
    Callback's code already graded. The service keeps nothing between requests."""

    posting: Posting
    # The furthest stage reached before the rejection: applied, screening, interview or offer.
    stage: Literal["applied", "screening", "interview", "offer"] = "applied"
    resume_label: str = Field(max_length=120)
    resume_text: str = Field(min_length=1, max_length=20_000)
    fact_sheet: str = Field(default="", max_length=100_000)
    # Every checklist line, met or not, so the model sees strengths as well as gaps.
    checklist: list[Gap] = Field(default_factory=list, max_length=100)
    score: int | None = Field(default=None, ge=0, le=100)
    # Years of experience the resume shows, as Callback's checklist counts them.
    resume_years: float | None = Field(default=None, ge=0, le=80)
    # None when the company hasn't been researched.
    company: RejectCompanyContext | None = None


class ExperienceGap(BaseModel):
    """Something the posting asks for that isn't a named technology, and how the person stands."""

    area: ExperienceArea
    need: ShortText
    evidence: LongText
    suggestion: LongText


class FocusArea(BaseModel):
    """One thing worth working on, ranked by how much it would close the distance to this job."""

    kind: Literal["technology", "experience"]
    title: ShortText
    why: LongText
    action: LongText


ComparisonMeasure = Literal["years", "seniority", "industries", "employer_kinds"]
COMPARISON_MEASURES: tuple[str, ...] = get_args(ComparisonMeasure)
EstimateBand = Literal["likely", "possibly", "unlikely", "cant_tell"]
ESTIMATE_BANDS: tuple[str, ...] = get_args(EstimateBand)


class Comparison(BaseModel):
    """One measure of the company's typical engineer beside what the resume shows."""

    measure: ComparisonMeasure
    typical: ShortText
    yours: ShortText
    note: ShortText = ""


class Estimate(BaseModel):
    """A rough estimate of how much the rejection may have been about the person's profile."""

    band: EstimateBand = "cant_tell"
    reasons: list[ShortText] = Field(default_factory=list, max_length=3)


class RejectAnalysisFields(BaseModel):
    summary: LongText
    strengths: list[ShortText] = Field(default_factory=list, max_length=8)
    experience_gaps: list[ExperienceGap] = Field(default_factory=list, max_length=8)
    focus_areas: list[FocusArea] = Field(default_factory=list, max_length=5)
    comparison: list[Comparison] = Field(default_factory=list, max_length=4)
    estimate: Estimate = Field(default_factory=Estimate)


class RejectAnalysisResponse(StageCost):
    analysis: RejectAnalysisFields
