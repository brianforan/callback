"""Finding new companies for a Companies run: Haiku with web search lists companies that fit the
brief, and code checks each one is real and hiring before it joins the profile's list.

A company passes when its own Greenhouse, Lever or Ashby board answers with at least one open
job: from a link the model gave, or from a careers page that embeds or links to one. The jobs read
then are recorded at once, so the run can judge them without waiting for the catalog worker.
Companies whose jobs live anywhere else are recorded in the catalog but not read.

A run asks for this only when the catalog gave it too few matching jobs to check
(app.scout.engine). A brief with a Must use list searches for those technologies only,
not the fact sheet's, so the companies found use the stack the brief is strict about.
"""

import logging
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.companies.catalog import HiringRef, company_source, hiring_ref
from app.companies.catalog_worker import apply_read
from app.companies.feeds import FeedGone, FeedJob
from app.companies.identity import looks_like_agency, matching_key, resolve_company
from app.companies.lists import add_to_profile
from app.models import (
    CompanyAlias,
    CompanyKind,
    CompanySource,
    ProfileCompany,
    ProfileCompanyOrigin,
    utcnow,
)
from app.postings.parser import company_key, technology_mentions
from app.scout.match import spec_from_payload
from app.workers import TailorUnavailable

logger = logging.getLogger(__name__)

# Discovery's share of the run's budget and time, so judging has the rest.
BUDGET_SHARE = 0.15
TIME_SHARE = 0.4
COMPANIES_WANTED = 40
_MAX_TECHNOLOGIES = 8
_CAREERS_PER_CALL = 20
_FEED_READS_AT_ONCE = 8
_READ_ERRORS = (FeedGone, httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError)
_MAX_NAMED = 8

Stage = Callable[[dict[str, Any]], dict[str, Any]]
ReadFeed = Callable[[str, str], list[FeedJob]]


@dataclass
class Discovery:
    """What discovery found, for the run's notes."""

    searches: int = 0
    found: int = 0
    added: int = 0
    known: int = 0
    empty: int = 0
    agencies: int = 0
    unreadable: list[str] = field(default_factory=list)


def listed_count(db: Session, profile_id: uuid.UUID | None) -> int:
    if profile_id is None:
        return 0
    return (
        db.scalar(
            select(func.count())
            .select_from(ProfileCompany)
            .where(ProfileCompany.profile_id == profile_id)
        )
        or 0
    )


def _technologies(payload: dict[str, Any]) -> list[str]:
    """The brief's Must use list when it has one, else what the fact sheet and description name."""
    spec = spec_from_payload(payload)
    if spec.must_have:
        return spec.stack_terms[:_MAX_TECHNOLOGIES]
    text = f"{payload.get('fact_sheet') or ''}\n{payload.get('description') or ''}"
    return [name for name, _ in technology_mentions(text).most_common(_MAX_TECHNOLOGIES)]


def _own_ref(*links: str) -> HiringRef | None:
    for link in links:
        ref = hiring_ref(link)
        if ref is not None and ref.token is not None:
            return ref
    return None


def _on_list(db: Session, profile_id: uuid.UUID, name: str) -> bool:
    alias = db.get(CompanyAlias, matching_key(name))
    return alias is not None and db.get(ProfileCompany, (profile_id, alias.company_id)) is not None


def discover(
    db: Session,
    user_id: uuid.UUID,
    profile_id: uuid.UUID,
    payload: dict[str, Any],
    find: Stage,
    careers: Stage,
    read_feed: ReadFeed,
    on_cost: Callable[[str, dict[str, Any]], None],
    on_failed: Callable[[str, TailorUnavailable], None],
) -> Discovery:
    """Find companies for the brief, check them, and put the ones hiring on the profile's list.

    on_cost and on_failed count each model call's cost against the run. A failed search raises
    nothing: the run goes on with the list it has.
    """
    outcome = Discovery()
    limits = payload["limits"]
    request = {
        **payload,
        "limits": {**limits, "max_minutes": max(1, int(int(limits["max_minutes"]) * TIME_SHARE))},
        "budget_usd": round(float(payload["budget_usd"]) * BUDGET_SHARE, 4),
        "technologies": _technologies(payload),
        "listed_count": listed_count(db, profile_id),
        "companies_wanted": COMPANIES_WANTED,
    }
    try:
        answer = find(request)
    except TailorUnavailable as exc:
        on_failed("discover", exc)
        return outcome
    on_cost("discover", answer)
    outcome.searches = int(answer.get("searches") or 0)
    hidden = {company_key(name) for name in payload.get("hidden_companies") or []}
    fresh = []
    for item in answer.get("companies") or []:
        name = " ".join(str(item.get("name") or "").split())
        if not matching_key(name) or company_key(name) in hidden:
            continue
        if looks_like_agency(db, name):
            outcome.agencies += 1
        elif _on_list(db, profile_id, name):
            outcome.known += 1
        else:
            fresh.append(item)
    outcome.found = len(fresh)

    # A board link from the search, or one found on the careers page.
    refs: dict[int, HiringRef] = {}
    pages: dict[int, str] = {}
    for index, item in enumerate(fresh):
        ref = _own_ref(str(item.get("careers_url") or ""), str(item.get("website") or ""))
        if ref is not None:
            refs[index] = ref
        elif page := str(item.get("careers_url") or item.get("website") or "").strip():
            pages[index] = page
    order = list(pages)
    for start in range(0, len(order), _CAREERS_PER_CALL):
        batch = order[start : start + _CAREERS_PER_CALL]
        try:
            read = careers({"urls": [pages[index] for index in batch]})
        except TailorUnavailable as exc:
            logger.warning("Couldn't read careers pages: %s", exc)
            break
        for index, page in zip(batch, read.get("pages") or [], strict=False):
            ref = _own_ref(*(page.get("links") or []))
            if ref is not None:
                refs[index] = ref

    def read(ref: HiringRef) -> list[FeedJob] | None:
        try:
            return read_feed(ref.system, ref.token or "")
        except _READ_ERRORS as exc:
            logger.info("Couldn't read %s:%s while checking it: %s", ref.system, ref.token, exc)
            return None

    checked = list(refs.items())
    with ThreadPoolExecutor(max_workers=_FEED_READS_AT_ONCE) as pool:
        boards = dict(
            zip(
                (index for index, _ in checked),
                pool.map(read, (r for _, r in checked)),
                strict=True,
            )
        )

    for index, item in enumerate(fresh):
        name = str(item.get("name"))
        website = str(item.get("website") or "") or None
        ref = refs.get(index)
        jobs = boards.get(index)
        if ref is None or jobs is None:
            resolve_company(db, name, domain=website)
            outcome.unreadable.append(name)
            continue
        company = resolve_company(
            db, name, domain=website, source=(ref.system, ref.token or "")
        ).company
        if company.kind == CompanyKind.AGENCY.value:
            outcome.agencies += 1
            continue
        company_source(db, company, ref.system, ref.token or "", is_own=True)
        db.flush()
        source = db.scalar(
            select(CompanySource).where(
                CompanySource.source == ref.system, CompanySource.identifier == ref.token
            )
        )
        was_listed = db.get(ProfileCompany, (profile_id, company.id)) is not None
        if source is not None:
            # Recorded now, so this run can judge them without waiting for the catalog worker.
            apply_read(db, source.id, jobs, utcnow())
        if not jobs:
            outcome.empty += 1
        elif was_listed:
            outcome.known += 1
        else:
            add_to_profile(db, user_id, profile_id, company.id, ProfileCompanyOrigin.DISCOVERED)
            outcome.added += 1
        db.commit()
    db.commit()
    return outcome


def describe(outcome: Discovery) -> list[str]:
    """The run's notes about discovery."""
    parts = [
        f"{outcome.added} added to your list" if outcome.added else "none new to add",
        f"{outcome.known} already on it" if outcome.known else "",
        f"{outcome.empty} with no open jobs" if outcome.empty else "",
        f"{outcome.agencies} agencies left out" if outcome.agencies else "",
        f"{len(outcome.unreadable)} without a job board Callback can read"
        if outcome.unreadable
        else "",
    ]
    notes = [
        f"Looked for new companies with {outcome.searches} web searches: "
        + "; ".join(part for part in parts if part)
        + "."
    ]
    if outcome.unreadable:
        named = ", ".join(outcome.unreadable[:_MAX_NAMED])
        more = len(outcome.unreadable) - _MAX_NAMED
        notes.append(
            f"Found, but their jobs aren't on a system Callback can read: {named}"
            f"{f' and {more} more' if more > 0 else ''}."
        )
    return notes
