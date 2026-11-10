"""How a company hires over time, worked out in code from the catalog
(docs/rejection_research_spec.md, chunk 3). Pure functions over rows, no model calls, no table of
their own.

History is read from listings on the company's own hiring system (Greenhouse, Lever, Ashby), not
from catalog jobs. The catalog folds a job that comes back after closing into its old row and
clears its close date, so a job's row can't say when it closed or that it was posted again. Each
listing keeps its own first and last sighting, though, and a job relisted under a new link is a
second listing on the same job, with a gap between them.

An opening is one stretch of a job being listed: its own-board listings that overlap or follow
within a read or two of each other. It opens at its first sighting and closes at its last, once
the catalog has marked the job closed or the job was listed again later. Close dates are within
one read of the real one (every 6 hours for companies on someone's list, daily for the rest).

Shown only for companies whose own board Callback reads: job-board listings come and go for
reasons that aren't the company's.
"""

import statistics
import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from app.companies.catalog import SAME_TEXT_BITS, catalog_url, fingerprint, text_distance, title_key
from app.models import RoleFamily
from app.postings.parser import parse_role_family

CANT_SEE = "Callback can't see this company's hiring directly."
# Listings of one job this close together are one opening, since boards are read at most daily
# and a failed read can skip one.
SAME_OPENING_GAP = timedelta(days=2)
# Openings first seen this soon after the company's board was first read were already open
# then: when they really opened is unknown.
ALREADY_OPEN_GAP = timedelta(days=1)
# A closed job counts as reposted when a near-identical one opens within this long after.
REPOST_WINDOW = timedelta(days=60)
# How long after a rejection the job applied to is followed.
AFTER_REJECTION = timedelta(days=30)
_SECONDS_PER_DAY = 86_400


@dataclass(frozen=True)
class BoardRow:
    """One of the company's sources, from company_sources."""

    source: str
    is_own: bool
    # ok, empty or dead.
    status: str


@dataclass(frozen=True)
class ListingRow:
    """One listing of one of the company's catalog jobs, with what its job row says."""

    job_id: uuid.UUID
    # greenhouse, lever, ashby, a job board's id, or web.
    source: str
    url: str
    title: str
    title_key: str
    fingerprint: int | None
    first_seen: datetime
    last_seen: datetime
    job_closed_at: datetime | None


@dataclass(frozen=True)
class Opening:
    """One stretch of a job being listed on the company's own board."""

    job_id: uuid.UUID
    title: str
    title_key: str
    fingerprint: int | None
    engineering: bool
    opened: datetime
    # None while it's still listed.
    closed: datetime | None
    # Listed when Callback first read the board, so its real opening date is unknown.
    already_open: bool
    urls: frozenset[str]

    def open_at(self, moment: datetime) -> bool:
        return self.opened <= moment and (self.closed is None or self.closed >= moment)


@dataclass(frozen=True)
class Week:
    """One week, Monday to Sunday UTC. open counts what was listed at its end (or now, for the
    current week); opened leaves out openings already listed when the board was first read."""

    start: date
    open: int
    open_engineering: int
    opened: int
    closed: int


@dataclass(frozen=True)
class Repost:
    original: Opening
    repost: Opening
    gap_days: int


@dataclass(frozen=True)
class AfterRejection:
    """What the job applied to did in the 30 days after the rejection.

    reposted wins over still_open, which wins over closed. days_open is how many of those days
    it stayed listed, up to now while the 30 days aren't over. complete is False until then.
    """

    outcome: Literal["still_open", "closed", "reposted", "not_found"]
    matched_by: Literal["link", "title_and_text"] | None = None
    days_open: int | None = None
    closed_on: date | None = None
    reposted_on: date | None = None
    complete: bool = False


def _utc(moment: datetime) -> datetime:
    """SQLite gives naive times, Postgres aware ones; both are UTC."""
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


def _days(span: timedelta) -> int:
    return max(0, int(span.total_seconds() // _SECONDS_PER_DAY))


def readable(boards: Iterable[BoardRow]) -> bool:
    """Whether Callback reads the company's own board, so its hiring history means something."""
    return any(board.is_own and board.status != "dead" for board in boards)


def is_engineering(title: str) -> bool:
    """A software role by the posting parser's role families. Managers count only when the
    title says engineering."""
    family = parse_role_family(title)
    if family == RoleFamily.MANAGEMENT:
        return "engineer" in title.lower()
    return family != RoleFamily.OTHER


def openings(boards: Iterable[BoardRow], listings: Iterable[ListingRow]) -> list[Opening]:
    """The company's openings from its own-board listings, oldest first."""
    own = {board.source for board in boards if board.is_own}
    by_job: dict[uuid.UUID, list[ListingRow]] = defaultdict(list)
    for listing in listings:
        if listing.source in own:
            by_job[listing.job_id].append(listing)
    if not by_job:
        return []
    board_start = min(_utc(item.first_seen) for rows in by_job.values() for item in rows)

    found: list[Opening] = []
    for job_rows in by_job.values():
        job_rows.sort(key=lambda item: _utc(item.first_seen))
        runs: list[list[ListingRow]] = []
        for listing in job_rows:
            last = max(_utc(item.last_seen) for item in runs[-1]) if runs else None
            if last is not None and _utc(listing.first_seen) <= last + SAME_OPENING_GAP:
                runs[-1].append(listing)
            else:
                runs.append([listing])
        newest = job_rows[-1]
        for index, run in enumerate(runs):
            opened = _utc(run[0].first_seen)
            last_seen = max(_utc(item.last_seen) for item in run)
            # An earlier stretch ended when the job was listed again; the last one ends only
            # when the catalog marked the job closed.
            ended = index < len(runs) - 1 or newest.job_closed_at is not None
            found.append(
                Opening(
                    job_id=newest.job_id,
                    title=newest.title,
                    title_key=newest.title_key,
                    fingerprint=newest.fingerprint,
                    engineering=is_engineering(newest.title),
                    opened=opened,
                    closed=last_seen if ended else None,
                    already_open=opened <= board_start + ALREADY_OPEN_GAP,
                    urls=frozenset(item.url for item in run),
                )
            )
    found.sort(key=lambda opening: (opening.opened, str(opening.job_id)))
    return found


def _monday(moment: datetime) -> date:
    day = moment.date()
    return day - timedelta(days=day.weekday())


def weekly(found: Sequence[Opening], now: datetime) -> list[Week]:
    """Open, opened and closed counts per week, from the first opening's week to this one."""
    if not found:
        return []
    now = _utc(now)
    weeks: list[Week] = []
    start = _monday(min(opening.opened for opening in found))
    while start <= now.date():
        begin = datetime(start.year, start.month, start.day, tzinfo=UTC)
        end = min(begin + timedelta(days=7), now)
        listed = [
            opening
            for opening in found
            if opening.opened < end and (opening.closed is None or opening.closed >= end)
        ]
        weeks.append(
            Week(
                start=start,
                open=len(listed),
                open_engineering=sum(opening.engineering for opening in listed),
                opened=sum(
                    not opening.already_open and begin <= opening.opened < end for opening in found
                ),
                closed=sum(
                    opening.closed is not None and begin <= opening.closed < end
                    for opening in found
                ),
            )
        )
        start += timedelta(days=7)
    return weeks


def median_days_open(found: Iterable[Opening]) -> float | None:
    """Median days a role stays listed, over closed openings whose start Callback saw."""
    spans = [
        (opening.closed - opening.opened).total_seconds() / _SECONDS_PER_DAY
        for opening in found
        if opening.closed is not None and not opening.already_open
    ]
    return round(statistics.median(spans), 1) if spans else None


def _near_identical(first: Opening, second: Opening) -> bool:
    if first.job_id == second.job_id:
        return True
    if first.title_key != second.title_key:
        return False
    if first.fingerprint is None or second.fingerprint is None:
        return False
    return text_distance(first.fingerprint, second.fingerprint) <= SAME_TEXT_BITS


def reposts(found: Sequence[Opening]) -> list[Repost]:
    """Openings that came within 60 days of a near-identical one closing: the same job listed
    again, or another job with the same title key and text within the catalog's distance.
    Each repost is paired with the most recent closing before it."""
    paired: list[Repost] = []
    for later in found:
        before = [
            earlier
            for earlier in found
            if earlier is not later
            and earlier.closed is not None
            and earlier.closed <= later.opened <= earlier.closed + REPOST_WINDOW
            and _near_identical(earlier, later)
        ]
        if before:
            original = max(before, key=lambda opening: opening.closed or opening.opened)
            gap = later.opened - (original.closed or original.opened)
            paired.append(Repost(original=original, repost=later, gap_days=_days(gap)))
    return paired


def _listed_at(opening: Opening, moment: datetime) -> bool:
    """Listed then, as far as Callback can tell. One already listed when the board was first
    read has an unknown start, so it counts as listed at any earlier moment too."""
    if opening.already_open and moment < opening.opened:
        return opening.closed is None or opening.closed >= moment
    return opening.open_at(moment)


def _applied_opening(candidates: list[Opening], rejected: datetime) -> Opening | None:
    """The opening listed at the rejection, else the latest one that closed before it."""
    listed = [opening for opening in candidates if _listed_at(opening, rejected)]
    if listed:
        return max(listed, key=lambda opening: opening.opened)
    earlier = [opening for opening in candidates if opening.opened <= rejected]
    return max(earlier, key=lambda opening: opening.opened) if earlier else None


def after_rejection(
    found: Sequence[Opening],
    listings: Iterable[ListingRow],
    *,
    url: str | None,
    title: str,
    text: str,
    rejected: datetime,
    now: datetime,
) -> AfterRejection:
    """Whether the job applied to was still listed, closed or reposted in the 30 days after
    the rejection. Found by its link on any board, else by its title and text."""
    rejected, now = _utc(rejected), _utc(now)
    link = catalog_url(url)
    job_ids = {listing.job_id for listing in listings if link is not None and listing.url == link}
    matched_by: Literal["link", "title_and_text"] = "link"
    candidates = [opening for opening in found if opening.job_id in job_ids]
    if not candidates:
        matched_by = "title_and_text"
        key, print_ = title_key(title), fingerprint(text)
        candidates = [
            opening
            for opening in found
            if print_ is not None
            and opening.title_key == key
            and opening.fingerprint is not None
            and text_distance(print_, opening.fingerprint) <= SAME_TEXT_BITS
        ]
    applied = _applied_opening(candidates, rejected)
    if applied is None:
        return AfterRejection(outcome="not_found")

    window_end = rejected + AFTER_REJECTION
    seen_until = min(window_end, now)
    complete = now >= window_end
    for item in reposts(found):
        if item.original == applied and rejected < item.repost.opened <= seen_until:
            return AfterRejection(
                outcome="reposted",
                matched_by=matched_by,
                days_open=_days((applied.closed or rejected) - rejected),
                closed_on=applied.closed.date() if applied.closed else None,
                reposted_on=item.repost.opened.date(),
                complete=complete,
            )
    if applied.closed is None or applied.closed > seen_until:
        return AfterRejection(
            outcome="still_open",
            matched_by=matched_by,
            days_open=_days(seen_until - rejected),
            complete=complete,
        )
    return AfterRejection(
        outcome="closed",
        matched_by=matched_by,
        days_open=_days(applied.closed - rejected),
        closed_on=applied.closed.date(),
        complete=complete,
    )
