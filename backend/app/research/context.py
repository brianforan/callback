"""This company around a rejection (docs/rejection_research_spec.md, chunk 4): what its newest
research snapshot and the catalog's hiring history say, as plain lines for the report and as
the company section the analysis model reads.

Read only: nothing here adds a company or calls a model. The report's company lines are worked
out each time it's opened, so what a job did after the rejection fills in as time passes.
"""

import re
import statistics
import uuid
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.companies.identity import find_company
from app.models import (
    Application,
    ApplicationStatus,
    CatalogJob,
    CatalogJobSource,
    Company,
    CompanyResearch,
    CompanySource,
    PostingLevel,
    Requirement,
)
from app.postings.parser import company_key, parse_posting
from app.research.history import (
    CANT_SEE,
    BoardRow,
    ListingRow,
    Opening,
    after_rejection,
    is_engineering,
    median_days_open,
    openings,
    readable,
    reposts,
)
from app.research.snapshots import hiring_source, newest_snapshot
from app.schemas import CompanyContextOut, ContextLine, RepeatCompany

# How far back a hiring trend is compared, and which events count as "shortly before".
ROLES_WINDOW = timedelta(days=60)
EVENTS_BEFORE = timedelta(days=90)
RECENT_EVENTS = timedelta(days=365)
_MAX_EVENTS = 6
# What a company's own engineering postings ask for: those seen in the last year, at most the
# newest 200, and only said with at least this many to go on.
POSTINGS_WINDOW = timedelta(days=365)
MIN_POSTINGS = 3
_MAX_POSTINGS = 200
_TOP_TECHNOLOGIES = 6
_LEVEL_ORDER = ("junior", "mid", "senior", "staff", "principal", "manager")
_DATE = re.compile(r"^(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?$")

EVENT_LABELS = {
    "layoff": "Layoff",
    "hiring_freeze": "Hiring freeze",
    "reorg": "Reorganization",
    "acquisition": "Acquired a company",
    "acquired": "Was acquired",
    "ipo": "IPO",
    "leadership_change": "Leadership change",
    "office_opened": "Office opened",
    "office_closed": "Office closed",
}
_SENIORITY = {
    "mostly_junior": "mostly junior",
    "mixed": "a mix of levels",
    "mostly_senior": "mostly senior",
}
_EMPLOYERS = {
    "big_tech": "big tech",
    "startup": "startups",
    "consulting": "consulting firms",
    "agency": "agencies",
    "enterprise": "large enterprises",
}


def _aware(moment: datetime) -> datetime:
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


def _link(application: Application) -> str | None:
    return application.job_url or (application.posting.url if application.posting else None)


def application_company(db: Session, application: Application) -> Company | None:
    return find_company(db, application.company, hiring_source(_link(application)))


def rejected_at(application: Application) -> datetime:
    """When the application was marked rejected, the latest time if more than once."""
    times = [
        _aware(event.occurred_at)
        for event in application.events
        if event.status == ApplicationStatus.REJECTED.value
    ]
    return max(times) if times else _aware(application.updated_at)


def load_history(db: Session, company_id: uuid.UUID) -> tuple[list[BoardRow], list[ListingRow]]:
    """The company's sources and every listing of its catalog jobs, for app.research.history."""
    boards = [
        BoardRow(source=row.source, is_own=row.is_own, status=row.status)
        for row in db.scalars(select(CompanySource).where(CompanySource.company_id == company_id))
    ]
    rows = db.execute(
        select(CatalogJob, CatalogJobSource)
        .join(CatalogJobSource, CatalogJobSource.job_id == CatalogJob.id)
        .where(CatalogJob.company_id == company_id)
    ).all()
    listings = [
        ListingRow(
            job_id=job.id,
            source=listing.source,
            url=listing.url,
            title=job.title,
            title_key=job.title_key,
            fingerprint=job.fingerprint,
            first_seen=listing.first_seen,
            last_seen=listing.last_seen,
            job_closed_at=job.closed_at,
        )
        for job, listing in rows
    ]
    return boards, listings


def event_day(text: str) -> date | None:
    """The first day of an event's YYYY, YYYY-MM or YYYY-MM-DD."""
    found = _DATE.match(text or "")
    if found is None:
        return None
    try:
        return date(int(found[1]), int(found[2] or 1), int(found[3] or 1))
    except ValueError:
        return None


def engineering_open(found: Sequence[Opening], moment: datetime) -> int:
    return sum(opening.engineering and opening.open_at(moment) for opening in found)


def roles_before(found: Sequence[Opening], rejected: datetime) -> tuple[int, int] | None:
    """Open engineering roles 60 days before the rejection and at it, or None when Callback
    hadn't read the board that long."""
    if not found or min(opening.opened for opening in found) > rejected - ROLES_WINDOW:
        return None
    return engineering_open(found, rejected - ROLES_WINDOW), engineering_open(found, rejected)


def _source(snapshot: CompanyResearch, index: Any) -> dict[str, str]:
    sources = snapshot.sources or []
    if isinstance(index, int) and 0 <= index < len(sources):
        return sources[index]
    return {}


def event_text(event: dict[str, Any]) -> str:
    parts = [EVENT_LABELS.get(event.get("kind", ""), "Event")]
    if event.get("detail"):
        parts.append(event["detail"])
    if event.get("count"):
        parts.append(f"{event['count']:,} people")
    if event.get("percent"):
        parts.append(f"{event['percent']}%")
    return ": ".join(parts[:2]) + "".join(f", {part}" for part in parts[2:])


# --- What its own postings ask for ---------------------------------------------------------------


@dataclass(frozen=True)
class PostingProfile:
    """What a company's own engineering postings ask for, read by the posting parser. Its
    postings, not its people: what it hires for, which stands in when research can't say who
    works there."""

    postings: int
    with_years: int
    years_low: int | None
    years_high: int | None
    years_median: float | None
    # (level, percent of postings with a level), in ladder order.
    levels: list[tuple[str, int]]
    # Technologies required by at least two postings, most often first.
    technologies: list[str]


def _percentile(ordered: list[int], share: float) -> int:
    return ordered[min(len(ordered) - 1, int(share * len(ordered)))]


def posting_profile(
    db: Session, boards: Sequence[BoardRow], listings: Sequence[ListingRow], now: datetime
) -> PostingProfile | None:
    """What the company's engineering postings on its own board asked for in the last year,
    or None with fewer than MIN_POSTINGS of them."""
    own = {board.source for board in boards if board.is_own}
    since = now - POSTINGS_WINDOW
    job_ids = {
        listing.job_id
        for listing in listings
        if listing.source in own and _aware(listing.last_seen) >= since
    }
    if not job_ids:
        return None
    jobs = db.execute(
        select(CatalogJob.title, CatalogJob.text, CatalogJob.location)
        .where(CatalogJob.id.in_(job_ids))
        .order_by(CatalogJob.last_seen.desc())
        .limit(_MAX_POSTINGS)
    ).all()
    parsed = [
        item
        for title, text, location in jobs
        if is_engineering(title)
        # Internships say nothing about who an experienced engineer is compared with.
        and (item := parse_posting(title, text or "", None, location, None)).level
        != PostingLevel.INTERN
    ]
    if len(parsed) < MIN_POSTINGS:
        return None
    years = sorted(item.years_required for item in parsed if item.years_required)
    levels = Counter(
        item.level.value
        for item in parsed
        if item.level is not None and item.level.value in _LEVEL_ORDER
    )
    leveled = sum(levels.values())
    required = Counter(
        technology.name
        for item in parsed
        for technology in item.technologies
        if technology.requirement == Requirement.REQUIRED
    )
    enough_years = len(years) >= MIN_POSTINGS
    return PostingProfile(
        postings=len(parsed),
        with_years=len(years),
        years_low=_percentile(years, 0.25) if enough_years else None,
        years_high=_percentile(years, 0.75) if enough_years else None,
        years_median=statistics.median(years) if enough_years else None,
        levels=[
            (level, round(100 * levels[level] / leveled)) for level in _LEVEL_ORDER if levels[level]
        ]
        if leveled >= MIN_POSTINGS
        else [],
        technologies=[
            name for name, count in required.most_common(_TOP_TECHNOLOGIES) if count >= 2
        ],
    )


def _years_phrase(profile: PostingProfile) -> str | None:
    if profile.years_low is None or profile.years_high is None:
        return None
    span = (
        f"{profile.years_low}"
        if profile.years_low == profile.years_high
        else f"{profile.years_low} to {profile.years_high}"
    )
    return f"{span} years of experience (median {profile.years_median:g})"


def posting_lines(profile: PostingProfile | None) -> list[str]:
    """The profile as lines for the model, each saying it's from the company's postings."""
    if profile is None:
        return []
    lines = []
    if years := _years_phrase(profile):
        lines.append(
            f"Its own engineering postings ask for {years}, in the {profile.with_years} of "
            f"{profile.postings} that state years"
        )
    if profile.levels:
        mix = ", ".join(f"{level} {percent}%" for level, percent in profile.levels)
        lines.append(f"Levels its own engineering postings hire at: {mix}")
    if profile.technologies:
        lines.append(
            "Technologies its own engineering postings most often require: "
            + ", ".join(profile.technologies)
        )
    return lines


# --- For the model ------------------------------------------------------------------------------


def workforce_lines(snapshot: CompanyResearch | None) -> list[str]:
    """The snapshot's workforce profile as plain lines. Empty when unknown."""
    workforce = snapshot.workforce if snapshot is not None else {}
    if not workforce or (snapshot.confidence or {}).get("workforce") == "unknown":
        return []
    lines = []
    low, high = workforce.get("typical_years_low"), workforce.get("typical_years_high")
    if low and high:
        lines.append(f"Engineers typically have {low} to {high} years of experience")
    elif low:
        lines.append(f"Engineers typically have {low}+ years of experience")
    if seniority := _SENIORITY.get(workforce.get("seniority_mix", "")):
        lines.append(f"Engineering seniority: {seniority}")
    if industries := workforce.get("prior_industries"):
        lines.append(f"Engineers often come from: {', '.join(industries)}")
    if kinds := [
        _EMPLOYERS[kind]
        for kind in workforce.get("prior_employer_kinds") or []
        if kind in _EMPLOYERS
    ]:
        lines.append(f"Engineers often come from {', '.join(kinds)}")
    if percent := workforce.get("cs_degree_percent"):
        lines.append(f"{percent}% of engineers have a computer science degree")
    return lines


def signal_lines(
    snapshot: CompanyResearch | None,
    found: Sequence[Opening],
    can_see: bool,
    rejected: datetime,
) -> list[str]:
    """What the company was doing around the rejection: layoffs and freezes in the 90 days
    before, and how its open engineering roles moved over the 60 days before."""
    lines = []
    for event in (snapshot.events if snapshot is not None else []) or []:
        day = event_day(event.get("date", ""))
        if event.get("kind") not in ("layoff", "hiring_freeze") or day is None:
            continue
        before = (rejected.date() - day).days
        if 0 <= before <= EVENTS_BEFORE.days:
            lines.append(
                f"{event_text(event)} ({event['date']}, about {before} days before the rejection)"
            )
    if not can_see:
        lines.append(CANT_SEE)
        return lines
    trend = roles_before(found, rejected)
    if trend is None:
        lines.append(
            "Callback hasn't read this company's job board long enough to compare openings."
        )
        return lines
    then, now = trend
    fell = then > 0 and now <= then / 2
    lines.append(
        f"Open engineering roles: {then} sixty days before the rejection, {now} at it"
        + (", down by half or more" if fell else "")
    )
    return lines


def model_context(
    db: Session, application: Application, company: Company | None, snapshot: CompanyResearch | None
) -> dict[str, Any] | None:
    """The company section of the analysis request: who works there (from research) and what its
    own postings ask for (from the catalog). None when neither is known."""
    if company is None:
        return None
    boards, listings = load_history(db, company.id)
    found = openings(boards, listings)
    workforce = workforce_lines(snapshot) + posting_lines(
        posting_profile(db, boards, listings, datetime.now(UTC))
    )
    if snapshot is None and not workforce:
        return None
    return {
        "name": company.name[:300],
        "workforce": [line[:300] for line in workforce][:12],
        "signals": [
            line[:300]
            for line in signal_lines(snapshot, found, readable(boards), rejected_at(application))
        ][:12],
    }


# --- For the report ---------------------------------------------------------------------------


def _research_line(
    snapshot: CompanyResearch, label: str, text: str, index: Any, as_of: str = ""
) -> ContextLine:
    source = _source(snapshot, index)
    return ContextLine(
        label=label,
        text=text,
        source_url=source.get("url") or None,
        source_title=source.get("title") or None,
        as_of=as_of or source.get("published_on") or None,
    )


def _snapshot_lines(snapshot: CompanyResearch, now: datetime) -> list[ContextLine]:
    lines = []
    profile = snapshot.profile or {}
    if profile.get("what_it_does"):
        text = profile["what_it_does"]
        if profile.get("industry"):
            text = f"{text} ({profile['industry']})"
        lines.append(_research_line(snapshot, "What it is", text, profile.get("source")))
    size = snapshot.size or {}
    if size.get("headcount") or size.get("trend") not in (None, "", "unknown"):
        parts = []
        if size.get("headcount"):
            parts.append(f"About {size['headcount']:,} people")
        if size.get("trend") not in (None, "", "unknown"):
            parts.append(size["trend"])
        lines.append(
            _research_line(
                snapshot, "Size", ", ".join(parts), size.get("source"), size.get("as_of", "")
            )
        )
    else:
        lines.append(ContextLine(label="Size", text="Unknown"))
    recent = [
        event
        for event in snapshot.events or []
        if (day := event_day(event.get("date", ""))) is not None
        and (now.date() - day).days <= RECENT_EVENTS.days
    ]
    for event in recent[:_MAX_EVENTS]:
        lines.append(
            _research_line(
                snapshot, "Event", event_text(event), event.get("source"), event.get("date", "")
            )
        )
    if not recent:
        lines.append(ContextLine(label="Events", text="None found in the last 12 months"))
    return lines


def _catalog_lines(
    db: Session,
    company: Company,
    boards: list[BoardRow],
    listings: list[ListingRow],
    application: Application,
    now: datetime,
) -> list[ContextLine]:
    if not readable(boards):
        return [ContextLine(label="Hiring", text=CANT_SEE)]
    found = openings(boards, listings)
    catalog = f"Callback's catalog of {company.name}'s own job board"
    today = now.date().isoformat()

    def line(label: str, text: str) -> ContextLine:
        return ContextLine(label=label, text=text, source_title=catalog, as_of=today)

    rejected = rejected_at(application)
    lines = []
    open_now = engineering_open(found, now)
    trend = roles_before(found, rejected)
    roles = f"{open_now} now"
    if trend is not None:
        roles += f"; {trend[0]} sixty days before the rejection and {trend[1]} at it"
    lines.append(line("Open engineering roles", roles))
    profile = posting_profile(db, boards, listings, now)
    if profile is not None:
        parts = []
        if years := _years_phrase(profile):
            parts.append(years)
        if profile.levels:
            ranked = sorted(
                profile.levels, key=lambda item: (-item[1], _LEVEL_ORDER.index(item[0]))
            )
            parts.append(", ".join(f"{level} {percent}%" for level, percent in ranked[:2]))
        if profile.technologies:
            parts.append(f"often {', '.join(profile.technologies[:4])}")
        if parts:
            lines.append(
                line(
                    "What its postings ask",
                    f"{'; '.join(parts)}, from {profile.postings} engineering postings",
                )
            )
    if (median := median_days_open(found)) is not None:
        lines.append(line("Time listed", f"Roles stay listed a median of {median:g} days"))
    year_ago = now - RECENT_EVENTS
    reposted = [item for item in reposts(found) if item.repost.opened >= year_ago]
    if reposted:
        lines.append(line("Reposts", f"{len(reposted)} roles reposted in the last year"))
    posting = application.posting
    outcome = after_rejection(
        found,
        listings,
        url=_link(application),
        title=posting.title if posting else application.role_title,
        text=posting.raw_text if posting else "",
        rejected=rejected,
        now=now,
    )
    so_far = "" if outcome.complete else " so far"
    text = {
        "still_open": f"Still listed {outcome.days_open} days after the rejection{so_far}",
        "closed": f"Closed {outcome.closed_on}, {outcome.days_open} days after the rejection",
        "reposted": f"Reposted on {outcome.reposted_on}",
        "not_found": "Not found on the company's own board",
    }[outcome.outcome]
    lines.append(line("This job afterwards", text))
    return lines


def company_view(db: Session, application: Application, now: datetime) -> CompanyContextOut:
    """This company now, for the report: its newest snapshot's facts and its hiring history."""
    company = application_company(db, application)
    if company is None:
        return CompanyContextOut(
            company=None, lines=[ContextLine(label="Research", text="Not researched yet")]
        )
    snapshot = newest_snapshot(db, company.id)
    lines = (
        _snapshot_lines(snapshot, now)
        if snapshot is not None
        else [ContextLine(label="Research", text="Not researched yet")]
    )
    boards, listings = load_history(db, company.id)
    lines += _catalog_lines(db, company, boards, listings, application, now)
    return CompanyContextOut(
        company=company.name,
        researched_at=snapshot.finished_at if snapshot else None,
        lines=lines,
    )


def repeat_companies(
    db: Session, rejected: Sequence[Application], now: datetime
) -> list[RepeatCompany]:
    """Companies that rejected these applications more than once, with what's known."""
    groups: dict[str, list[Application]] = defaultdict(list)
    companies: dict[str, Company | None] = {}
    for application in rejected:
        company = application_company(db, application)
        key = str(company.id) if company else f"name:{company_key(application.company)}"
        groups[key].append(application)
        companies[key] = company
    repeats = []
    for key, applications in groups.items():
        if len(applications) < 2:
            continue
        company = companies[key]
        snapshot = newest_snapshot(db, company.id) if company else None
        row = RepeatCompany(
            company=company.name if company else applications[0].company,
            rejections=len(applications),
        )
        if snapshot is not None:
            size = snapshot.size or {}
            row.what_it_does = (snapshot.profile or {}).get("what_it_does") or None
            if size.get("headcount"):
                row.size = f"About {size['headcount']:,} people"
            row.recent_events = [
                f"{event['date']}: {event_text(event)}"
                for event in snapshot.events or []
                if (day := event_day(event.get("date", ""))) is not None
                and (now.date() - day).days <= RECENT_EVENTS.days
            ][:_MAX_EVENTS]
            row.researched_at = snapshot.finished_at
        repeats.append(row)
    return sorted(repeats, key=lambda row: (-row.rejections, row.company.lower()))
