import uuid
from collections import Counter
from datetime import date
from functools import lru_cache
from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from app.companies.catalog import CatalogRecorder
from app.config import get_settings
from app.db import SessionLocal
from app.deps import CurrentProfile, CurrentUser, Db
from app.listings.cache import KEEP_RESULTS, ListingStore, SqlListingStore
from app.listings.query import parse_query
from app.listings.search import (
    HiddenRule,
    HiddenRules,
    Listing,
    ListingFilters,
    filter_listings,
    listing_facts,
    search_listings,
)
from app.models import (
    Application,
    EmploymentType,
    FactSheet,
    HiddenKind,
    HiddenListing,
    JobPosting,
    PostingLevel,
    Requirement,
    RoleFamily,
    SourceKind,
    User,
    WorkMode,
)
from app.postings.parser import (
    ParsedPosting,
    ParsedTechnology,
    Vocabulary,
    canonical_url,
    company_key,
    parse_requirements,
)
from app.postings.store import load_match_profile
from app.resumes.matching import score_job
from app.resumes.variants import load_baselines, pick_baseline
from app.schemas import (
    BaselinePick,
    HiddenListingOut,
    HideListingRequest,
    LabelCount,
    ListingHidden,
    ListingOut,
    ListingSearch,
    ListingSort,
    ListingSourceOut,
    ListingTracking,
    MatchEstimate,
    TailorAdvice,
    TechnologyOut,
)
from app.scout.limits import SCOUT_SOURCE
from app.tailoring.rules import fact_sheet_skills, tailor_advice
from app.vocabulary import user_vocabulary

router = APIRouter(prefix="/api/listings", tags=["listings"])

PAGE_SIZE = 50
_MAX_TECHNOLOGY_OPTIONS = 60
_SAVED_SOURCE = "Saved"


def _latest_statuses(db: Session, user: User) -> dict[uuid.UUID, str]:
    """The status of the newest application to each posting, so a listing can show it."""
    rows = db.execute(
        select(Application.posting_id, Application.status)
        .where(Application.user_id == user.id, Application.posting_id.is_not(None))
        .order_by(Application.applied_on, Application.created_at)
    ).all()
    return {posting_id: status for posting_id, status in rows}


def _hidden_rules(db: Session, user: User) -> HiddenRules:
    rows = db.scalars(select(HiddenListing).where(HiddenListing.user_id == user.id)).all()
    return HiddenRules(
        [
            HiddenRule(
                id=row.id,
                kind=row.kind,
                key=row.key,
                company_key=row.company_key,
                title=row.title,
            )
            for row in rows
        ]
    )


def _saved_listing(posting: JobPosting, vocabulary: Vocabulary) -> Listing:
    """A tracked posting shown among the results, using its stored fields with the user's edits."""
    return Listing(
        id=f"posting-{posting.id}",
        source=posting.source or _SAVED_SOURCE,
        company=posting.company,
        title=posting.title,
        # A hand-entered posting may have no URL. Blank never matches a board result or a rule.
        url=posting.url or "",
        location=posting.location,
        description=posting.raw_text,
        published_on=posting.seen_on,
        given_parsed=ParsedPosting(
            level=PostingLevel(posting.level) if posting.level else None,
            role_family=(
                RoleFamily(posting.role_family) if posting.role_family else RoleFamily.OTHER
            ),
            years_required=posting.years_required,
            salary_min=posting.salary_min,
            salary_max=posting.salary_max,
            work_mode=WorkMode(posting.work_mode) if posting.work_mode else None,
            employment_type=(
                EmploymentType(posting.employment_type) if posting.employment_type else None
            ),
            source_kind=SourceKind(posting.source_kind) if posting.source_kind else None,
            referred=posting.referred,
            referrer=posting.referrer,
            technologies=[
                ParsedTechnology(
                    name=technology.name,
                    category=technology.category,
                    requirement=Requirement(technology.requirement),
                )
                for technology in posting.technologies
            ],
        ),
        given_requirements=parse_requirements(posting.raw_text, vocabulary),
    )


def _to_out(
    listing: Listing,
    posting: JobPosting | None,
    statuses: dict[uuid.UUID, str],
    match: MatchEstimate | None,
    hidden: HiddenRule | None,
    baseline: BaselinePick | None,
    advice: TailorAdvice | None,
    vocabulary: Vocabulary,
) -> ListingOut:
    posting_id = posting.id if posting else None
    parsed = listing.parsed_with(vocabulary)
    return ListingOut(
        id=listing.id,
        source=listing.source,
        company=listing.company,
        title=listing.title,
        url=listing.url or None,
        location=listing.location,
        description=listing.description,
        published_on=listing.published_on,
        level=parsed.level,
        role_family=parsed.role_family,
        years_required=parsed.years_required,
        salary_min=parsed.salary_min,
        salary_max=parsed.salary_max,
        work_mode=parsed.work_mode,
        employment_type=parsed.employment_type,
        technologies=[TechnologyOut.model_validate(item) for item in parsed.technologies],
        posting_id=posting_id,
        application_status=statuses.get(posting_id) if posting_id else None,
        match=match,
        hidden=ListingHidden(id=hidden.id, kind=hidden.kind) if hidden else None,
        baseline=baseline,
        staged=bool(posting and posting.staged_at),
        staged_resume_id=posting.staged_resume_id if posting else None,
        tailor_advice=advice,
        from_scout=bool(posting and posting.source == SCOUT_SOURCE),
    )


def _sorted(
    listings: list[Listing], sort: ListingSort, matches: dict[str, MatchEstimate | None]
) -> list[Listing]:
    """Results arrive in relevance order, and each sort is stable, so ties keep that order."""

    def by_match(listing: Listing) -> int:
        match = matches.get(listing.id)
        return -match.score if match else 1

    def by_date(listing: Listing) -> int:
        return -listing.published_on.toordinal() if listing.published_on else 0

    def by_salary(listing: Listing) -> int:
        parsed = listing.parsed
        top = parsed.salary_max if parsed.salary_max is not None else parsed.salary_min
        return -top if top is not None else 1

    if sort is ListingSort.MATCH:
        return sorted(listings, key=by_match)
    if sort is ListingSort.NEWEST:
        return sorted(listings, key=by_date)
    if sort is ListingSort.SALARY:
        return sorted(listings, key=by_salary)
    return listings


@lru_cache
def get_listing_store() -> ListingStore:
    """Where board results are kept between searches. Swap this to move the cache elsewhere."""
    return SqlListingStore(SessionLocal, KEEP_RESULTS)


@lru_cache
def get_catalog_recorder() -> CatalogRecorder | None:
    """Records board results in the Companies catalog. None records nothing."""
    return CatalogRecorder(SessionLocal)


@router.get("", response_model=ListingSearch)
def search(
    user: CurrentUser,
    active: CurrentProfile,
    db: Db,
    store: Annotated[ListingStore, Depends(get_listing_store)],
    recorder: Annotated[CatalogRecorder | None, Depends(get_catalog_recorder)],
    q: Annotated[str, Query(max_length=200)],
    location: Annotated[str, Query(max_length=200)] = "",
    level: Annotated[list[PostingLevel] | None, Query()] = None,
    role_family: Annotated[list[RoleFamily] | None, Query()] = None,
    work_mode: Annotated[list[WorkMode] | None, Query()] = None,
    employment_type: Annotated[list[EmploymentType] | None, Query()] = None,
    source: Annotated[list[str] | None, Query()] = None,
    stack: Annotated[list[str] | None, Query()] = None,
    exclude_stack: Annotated[list[str] | None, Query()] = None,
    salary_min: Annotated[int | None, Query(ge=0, le=10_000_000)] = None,
    has_salary: bool = False,
    years_max: Annotated[int | None, Query(ge=0, le=60)] = None,
    posted_within_days: Annotated[int | None, Query(ge=1, le=3650)] = None,
    min_match: Annotated[int | None, Query(ge=0, le=100)] = None,
    fits_baseline: bool = False,
    hide_tracked: bool = False,
    tracking: Annotated[list[ListingTracking] | None, Query()] = None,
    show_hidden: bool = False,
    sort: ListingSort | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    profile_id: uuid.UUID | None = None,
) -> ListingSearch:
    keywords = q.strip()
    if not parse_query(keywords).board_keywords:
        raise HTTPException(HTTPStatus.UNPROCESSABLE_CONTENT, "Enter something to search for")
    results = search_listings(get_settings(), keywords, location.strip(), store, recorder)
    today = date.today()
    filters = ListingFilters(
        levels=frozenset(item.value for item in level or []),
        role_families=frozenset(item.value for item in role_family or []),
        work_modes=frozenset(item.value for item in work_mode or []),
        employment_types=frozenset(item.value for item in employment_type or []),
        sources=frozenset(source or []),
        stack_all=frozenset(stack or []),
        stack_none=frozenset(exclude_stack or []),
        salary_min=salary_min,
        has_salary=has_salary,
        years_max=years_max,
        posted_within_days=posted_within_days,
    )
    vocabulary = user_vocabulary(db, user.id)
    kept = filter_listings(results.listings, filters, today, vocabulary)

    # Listings are cached across users, so what this user has tracked is added per request.
    # Tracked jobs are shared by every profile unless one profile's are asked for.
    tracked_query = (
        select(JobPosting)
        .where(JobPosting.user_id == user.id)
        .options(selectinload(JobPosting.technologies))
    )
    if profile_id is not None:
        tracked_query = tracked_query.where(JobPosting.profile_id == profile_id)
    postings = db.scalars(tracked_query).all()
    by_url = {posting.url: posting.id for posting in postings if posting.url}
    posting_by_id = {posting.id: posting for posting in postings}
    if hide_tracked:
        kept = [listing for listing in kept if listing.url not in by_url]
    # A tracked job no longer on the boards still shows when a tracked state is picked.
    live_urls = {listing.url for listing in results.listings}
    unlisted = [posting for posting in postings if posting.url not in live_urls]
    saved_all = [_saved_listing(posting, vocabulary) for posting in unlisted]
    saved = filter_listings(saved_all, filters, today, vocabulary)
    posting_of = {listing.id: by_url[listing.url] for listing in kept if listing.url in by_url}
    posting_of.update(
        (listing.id, posting.id) for listing, posting in zip(saved_all, unlisted, strict=True)
    )
    saved_ids = {listing.id for listing in saved}

    statuses = _latest_statuses(db, user)

    def state(listing: Listing) -> ListingTracking:
        posting_id = posting_of.get(listing.id)
        if posting_id is None:
            return ListingTracking.UNTRACKED
        if posting_id in statuses:
            return ListingTracking(statuses[posting_id])
        if posting_by_id[posting_id].staged_at is not None:
            return ListingTracking.STAGED
        return ListingTracking.TRACKED

    wanted = frozenset(tracking or [])

    def chosen(listing: Listing) -> bool:
        return state(listing) in wanted if wanted else listing.id not in saved_ids

    pool = kept + saved
    rules = _hidden_rules(db, user)
    hidden = {listing.id: rule for listing in pool if (rule := rules.find(listing))}
    hidden_total = sum(1 for listing in pool if listing.id in hidden and chosen(listing))
    if not show_hidden:
        pool = [listing for listing in pool if listing.id not in hidden]

    profile = load_match_profile(db, active.id, vocabulary=vocabulary)
    matches = (
        {
            # A tracked listing is scored without its own applications, as its posting is.
            listing.id: score_job(
                profile.without_posting(posting_of.get(listing.id)),
                listing_facts(listing, posting_of.get(listing.id), vocabulary),
            )
            for listing in pool
        }
        if profile
        else {}
    )
    if min_match is not None and profile:
        pool = [
            listing
            for listing in pool
            if (match := matches.get(listing.id)) is not None and match.score >= min_match
        ]
    # The match profile already holds every resume in the active profile.
    baselines = load_baselines(profile.resumes if profile else [], today, vocabulary)
    picks = (
        {
            listing.id: pick_baseline(baselines, listing_facts(listing, vocabulary=vocabulary))
            for listing in pool
        }
        if baselines
        else {}
    )
    if fits_baseline:
        pool = [
            listing
            for listing in pool
            if (pick := picks.get(listing.id)) is not None and pick.covered
        ]
    tracking_counts = Counter(state(listing) for listing in pool)
    kept = [listing for listing in pool if chosen(listing)]

    # Advice is worked out for the page shown only, since it scores the chosen baseline again.
    skills = fact_sheet_skills(
        db.scalar(select(FactSheet.content).where(FactSheet.profile_id == active.id))
    )
    chosen_sort = sort or (ListingSort.MATCH if profile else ListingSort.NEWEST)
    ordered = _sorted(kept, chosen_sort, matches)
    start = (page - 1) * PAGE_SIZE
    technologies = Counter(
        technology.name
        for listing in results.listings + saved_all
        for technology in listing.parsed_with(vocabulary).technologies
    )
    return ListingSearch(
        listings=[
            _to_out(
                listing,
                posting_by_id.get(posting_of[listing.id]) if listing.id in posting_of else None,
                statuses,
                matches.get(listing.id),
                hidden.get(listing.id),
                picks.get(listing.id),
                tailor_advice(
                    baselines,
                    picks.get(listing.id),
                    listing_facts(listing, vocabulary=vocabulary),
                    skills,
                ),
                vocabulary,
            )
            for listing in ordered[start : start + PAGE_SIZE]
        ],
        total=len(ordered),
        unfiltered_total=len(results.listings)
        + (len(saved_all) if wanted - {ListingTracking.UNTRACKED} else 0),
        page=page,
        page_size=PAGE_SIZE,
        sort=chosen_sort,
        technologies=[
            LabelCount(label=name, count=count)
            for name, count in sorted(technologies.items(), key=lambda row: (-row[1], row[0]))[
                :_MAX_TECHNOLOGY_OPTIONS
            ]
        ],
        has_profile=profile is not None,
        sources=[ListingSourceOut.model_validate(item) for item in results.sources],
        hidden_total=hidden_total,
        tracking_counts=dict(tracking_counts),
        refreshing=results.refreshing,
    )


@router.get("/hidden", response_model=list[HiddenListingOut])
def list_hidden(user: CurrentUser, db: Db) -> list[HiddenListing]:
    return list(
        db.scalars(
            select(HiddenListing)
            .where(HiddenListing.user_id == user.id)
            .order_by(HiddenListing.created_at.desc())
        ).all()
    )


@router.post("/hidden", response_model=HiddenListingOut, status_code=status.HTTP_201_CREATED)
def hide(payload: HideListingRequest, user: CurrentUser, db: Db) -> HiddenListing:
    """Hide one job or a whole company. Hiding something already hidden returns the same rule."""
    key = company_key(payload.company)
    is_company = payload.kind == HiddenKind.COMPANY
    url = None if is_company else canonical_url(payload.url)
    rule_key = key if is_company else url
    existing = db.scalar(
        select(HiddenListing).where(
            HiddenListing.user_id == user.id,
            HiddenListing.kind == payload.kind,
            HiddenListing.key == rule_key,
        )
    )
    if existing is not None:
        return existing
    row = HiddenListing(
        user_id=user.id,
        kind=payload.kind,
        key=rule_key,
        company=payload.company,
        company_key=key,
        title=None if is_company else payload.title,
        url=url,
    )
    db.add(row)
    db.commit()
    return row


@router.delete("/hidden/{hidden_id}", status_code=status.HTTP_204_NO_CONTENT)
def unhide(hidden_id: uuid.UUID, user: CurrentUser, db: Db) -> None:
    db.execute(
        delete(HiddenListing).where(HiddenListing.id == hidden_id, HiddenListing.user_id == user.id)
    )
    db.commit()
