"""HTTP front of the tailoring service, reachable only from Callback's worker.

One /tailor request is one attempt: draft with the model, have the compiler service compile it,
and require exactly one page. A result that isn't usable comes back as ok=false with reasons, so
the caller can retry with them. Scout's stages (/pipeline/judge, /pipeline/brief and
/companies/*) each make one short model call or read a few pages; Callback's worker runs the
search itself. /reject-analysis makes one short call comparing a rejected posting with the resume
sent and the fact sheet. /companies/research searches the web for one company's snapshot.
/postings/fetch reads one posting's link with code, no model. Nothing is
stored here between requests, and no LaTeX runs here.
"""

import logging
from dataclasses import asdict
from functools import lru_cache
from typing import Any

import anthropic
import httpx
from fastapi import FastAPI, HTTPException, status

from tailor.companies import discover_companies, same_company
from tailor.company_research import research_company
from tailor.compiler import (
    TAILORED_MAX_PAGES,
    Compiler,
    CompilerUnavailable,
    http_compiler,
)
from tailor.config import get_settings
from tailor.fetch import FetchedPage, Fetcher, FetchRefused
from tailor.logs import configure_logging
from tailor.metering import cost_parts, message_cost
from tailor.model import (
    CreateMessage,
    DraftRejected,
    ModelUnavailable,
    draft_resume,
    streaming_create,
)
from tailor.pipeline import StageFailed, judge_posting, read_work, write_brief
from tailor.reject_analysis import analyze_rejection
from tailor.schemas import (
    POSTING_TEXT_MAX,
    SHORT_TEXT_MAX,
    BriefRequest,
    BriefResponse,
    CareersPage,
    CareersRequest,
    CareersResponse,
    CompanyResearchRequest,
    CompanyResearchResponse,
    DiscoverRequest,
    DiscoverResponse,
    JudgeRequest,
    JudgeResponse,
    PostingFetchRequest,
    PostingText,
    RejectAnalysisRequest,
    RejectAnalysisResponse,
    RepricedCost,
    RepriceRequest,
    RepriceResponse,
    SameCompanyRequest,
    SameCompanyResponse,
    SearchUsage,
    TailorRequest,
    TailorResponse,
    WorkRequest,
    WorkResponse,
)

configure_logging()
logger = logging.getLogger(__name__)

app = FastAPI(title="Callback tailor", docs_url=None, redoc_url=None, openapi_url=None)


@lru_cache
def get_create_message() -> CreateMessage:
    settings = get_settings()
    client = anthropic.Anthropic(
        api_key=settings.anthropic_api_key, timeout=settings.tailor_request_timeout
    )
    return streaming_create(client)


@lru_cache
def get_search_message() -> CreateMessage:
    settings = get_settings()
    client = anthropic.Anthropic(
        api_key=settings.anthropic_api_key, timeout=settings.scout_request_timeout
    )
    return streaming_create(client)


@lru_cache
def get_compiler() -> Compiler:
    settings = get_settings()
    client = httpx.Client(base_url=settings.compiler_url, timeout=settings.compiler_timeout_seconds)
    return http_compiler(client)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/tailor", response_model=TailorResponse)
def tailor(request: TailorRequest) -> TailorResponse:
    result = _tailor(request)
    usage = SearchUsage(**result.usage.model_dump())
    result.cost_usd = round(message_cost(usage, get_settings(), get_settings().tailor_model), 6)
    return result


def _tailor(request: TailorRequest) -> TailorResponse:
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Tailoring isn't set up: no Anthropic API key"
        )
    try:
        draft = draft_resume(get_create_message(), request, settings)
    except ModelUnavailable as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    except DraftRejected as exc:
        return TailorResponse(
            ok=False, reasons=[str(exc)], model=settings.tailor_model, usage=exc.usage
        )

    rejected = TailorResponse(
        ok=False,
        changelog=draft.changelog,
        gaps=draft.gaps,
        model=settings.tailor_model,
        usage=draft.usage,
    )
    try:
        compiled = get_compiler()(draft.tex, TAILORED_MAX_PAGES)
    except CompilerUnavailable as exc:
        # The draft was paid for, so its cost goes back with the error.
        usage = SearchUsage(**draft.usage.model_dump())
        spent = {
            "model": settings.tailor_model,
            "usage": draft.usage.model_dump(),
            "cost_usd": round(message_cost(usage, settings, settings.tailor_model), 6),
        }
        raise _failed(str(exc), spent) from exc
    if not compiled.ok or compiled.pdf_base64 is None or compiled.pages is None:
        logger.info("Draft was turned down: %s", compiled.reasons)
        return rejected.model_copy(
            update={"reasons": compiled.reasons or ["The compiler turned the draft down."]}
        )
    return TailorResponse(
        ok=True,
        tex=draft.tex,
        pdf_base64=compiled.pdf_base64,
        pages=compiled.pages,
        changelog=draft.changelog,
        gaps=draft.gaps,
        model=settings.tailor_model,
        usage=draft.usage,
    )


def _key_or_503() -> None:
    if not get_settings().anthropic_api_key:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Scout isn't set up: no Anthropic API key"
        )


@lru_cache
def get_fetcher() -> Fetcher:
    settings = get_settings()
    client = httpx.Client(timeout=settings.pipeline_fetch_timeout)
    return Fetcher(
        client,
        settings.pipeline_fetch_max_bytes,
        POSTING_TEXT_MAX,
        deadline_seconds=settings.pipeline_fetch_deadline,
    )


def _failed(message: str, spent: dict[str, Any]) -> HTTPException:
    """A 502 that also says what the failed attempt cost, since its model calls were billed."""
    return HTTPException(status.HTTP_502_BAD_GATEWAY, {"message": message, "spent": spent})


@app.post("/pipeline/judge", response_model=JudgeResponse)
def pipeline_judge(request: JudgeRequest) -> JudgeResponse:
    _key_or_503()
    try:
        return judge_posting(get_search_message(), request, get_settings())
    except StageFailed as exc:
        raise _failed(str(exc), exc.cost.spent_json()) from exc


@app.post("/pipeline/work", response_model=WorkResponse)
def pipeline_work(request: WorkRequest) -> WorkResponse:
    _key_or_503()
    try:
        return read_work(get_search_message(), request, get_settings())
    except StageFailed as exc:
        raise _failed(str(exc), exc.cost.spent_json()) from exc


@app.post("/pricing/reprice", response_model=RepriceResponse)
def pricing_reprice(request: RepriceRequest) -> RepriceResponse:
    """Stored requests priced again at today's rules, so a pricing fix can correct past runs.
    No model call."""
    settings = get_settings()
    costs = []
    for item in request.requests:
        try:
            parts = cost_parts(
                SearchUsage(**item.model_dump(exclude={"model"})), settings, item.model
            )
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
        costs.append(
            RepricedCost(
                cost_usd=round(sum(parts.values()), 6),
                cost_breakdown={part: round(value, 6) for part, value in parts.items()},
            )
        )
    return RepriceResponse(costs=costs)


@app.post("/companies/discover", response_model=DiscoverResponse)
def companies_discover(request: DiscoverRequest) -> DiscoverResponse:
    """Haiku with web search lists companies that fit a brief. Code checks each one after."""
    _key_or_503()
    return discover_companies(get_search_message(), request, get_settings())


@app.post("/companies/careers", response_model=CareersResponse)
def companies_careers(request: CareersRequest) -> CareersResponse:
    """Greenhouse, Lever and Ashby links on careers pages. No model, no tokens."""
    fetcher = get_fetcher()
    pages = []
    for url in request.urls:
        try:
            pages.append(CareersPage(url=url, ok=True, links=fetcher.hiring_links(url)))
        except (FetchRefused, httpx.HTTPError) as exc:
            reason = str(exc) if isinstance(exc, FetchRefused) else "The page couldn't be read."
            pages.append(CareersPage(url=url, ok=False, error=reason[:SHORT_TEXT_MAX]))
    return CareersResponse(pages=pages)


@app.post("/postings/fetch", response_model=PostingText)
def postings_fetch(request: PostingFetchRequest) -> PostingText:
    """One posting a user saved with a link and no text, read by code from its job feed or its
    page. No model, no tokens, no API key needed. A link that can't be read comes back with
    ok=false and the reason in plain words."""
    return _posting_text(get_fetcher().fetch(request.url))


def _posting_text(page: FetchedPage) -> PostingText:
    """A fetched posting clipped to the response's limits, since feeds and pages can have a
    title or location of any length."""
    fields = {
        name: value[:SHORT_TEXT_MAX] if isinstance(value, str) else value
        for name, value in asdict(page).items()
        if name not in ("url", "text")
    }
    return PostingText(url=page.url, text=page.text[:POSTING_TEXT_MAX], **fields)


@app.post("/companies/research", response_model=CompanyResearchResponse)
def companies_research(request: CompanyResearchRequest) -> CompanyResearchResponse:
    """One company's snapshot from Haiku with web search. Unchecked here: Callback checks
    every fact before storing it."""
    if not get_settings().anthropic_api_key:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Company research isn't set up: no Anthropic API key",
        )
    try:
        return research_company(get_search_message(), request, get_settings())
    except StageFailed as exc:
        raise _failed(str(exc), exc.cost.spent_json()) from exc


@app.post("/companies/same_company", response_model=SameCompanyResponse)
def companies_same_company(request: SameCompanyRequest) -> SameCompanyResponse:
    _key_or_503()
    try:
        return same_company(get_search_message(), request, get_settings())
    except StageFailed as exc:
        raise _failed(str(exc), exc.cost.spent_json()) from exc


@app.post("/pipeline/brief", response_model=BriefResponse)
def pipeline_brief(request: BriefRequest) -> BriefResponse:
    _key_or_503()
    try:
        return write_brief(get_search_message(), request, get_settings())
    except StageFailed as exc:
        raise _failed(str(exc), exc.cost.spent_json()) from exc


@app.post("/reject-analysis", response_model=RejectAnalysisResponse)
def reject_analysis(request: RejectAnalysisRequest) -> RejectAnalysisResponse:
    """What a rejected application's posting asked for beyond the resume sent. One model call."""
    if not get_settings().anthropic_api_key:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Rejection analysis isn't set up: no Anthropic API key",
        )
    try:
        return analyze_rejection(get_search_message(), request, get_settings())
    except StageFailed as exc:
        raise _failed(str(exc), exc.cost.spent_json()) from exc
