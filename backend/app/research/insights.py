"""Insights -> Companies and the company exports (docs/rejection_research_spec.md, chunk 5).

A user sees the companies they applied to that have a research snapshot; the facts are shared,
the list isn't, since research runs on rejections and a list of every researched company would
show where other users were turned down. The full exports are for the admin. Exports hold
company facts only: never who asked for a snapshot, what it cost, or anyone's applications.
"""

import csv
import io
import json
import uuid
from collections.abc import Iterator, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Application, Company, CompanyResearch, TailorStatus, User
from app.research.context import (
    RECENT_EVENTS,
    application_company,
    engineering_open,
    event_day,
    event_text,
    load_history,
)
from app.research.history import Opening, median_days_open, openings, readable, reposts, weekly
from app.research.snapshots import newest_snapshot, to_out
from app.roles import is_admin
from app.schemas import CompanyCard, CompanyDetail, HiringWeekOut

_MAX_EVENTS = 6
WEEK_COLUMNS = ("week_start", "open", "open_engineering", "opened", "closed")
EVENT_COLUMNS = (
    "company",
    "domain",
    "ticker",
    "exchange",
    "date",
    "kind",
    "detail",
    "count",
    "percent",
    "source_url",
    "source_title",
    "researched_at",
)


def own_companies(db: Session, user: User) -> list[Company]:
    """The companies this user applied to that the catalog knows, each once."""
    applications = db.scalars(select(Application).where(Application.user_id == user.id)).all()
    found: dict[uuid.UUID, Company] = {}
    for application in applications:
        company = application_company(db, application)
        if company is not None:
            found.setdefault(company.id, company)
    return list(found.values())


def _done(db: Session, company_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, int]:
    rows = db.execute(
        select(CompanyResearch.company_id, func.count(CompanyResearch.id))
        .where(
            CompanyResearch.company_id.in_(company_ids),
            CompanyResearch.status == TailorStatus.DONE.value,
        )
        .group_by(CompanyResearch.company_id)
    ).all()
    return {company_id: count for company_id, count in rows}


def _recent_events(snapshot: CompanyResearch, now: datetime) -> list[str]:
    events = [
        f"{event['date']}: {event_text(event)}"
        for event in snapshot.events or []
        if (day := event_day(event.get("date", ""))) is not None
        and (now.date() - day).days <= RECENT_EVENTS.days
    ]
    return events[:_MAX_EVENTS]


def _card_fields(
    company: Company,
    snapshot: CompanyResearch,
    count: int,
    found: list[Opening],
    can_see: bool,
    now: datetime,
) -> dict[str, Any]:
    ownership = snapshot.ownership or {}
    funding = snapshot.funding or {}
    size = snapshot.size or {}
    return {
        "id": company.id,
        "name": company.name,
        "domain": company.domain,
        "researched_at": snapshot.finished_at,
        "snapshots": count,
        "ownership": ownership.get("kind")
        if ownership.get("kind") not in (None, "unknown")
        else None,
        "stage": funding.get("stage") if funding.get("stage") not in (None, "unknown") else None,
        "headcount": size.get("headcount") or None,
        "trend": size.get("trend") if size.get("trend") not in (None, "unknown") else None,
        "recent_events": _recent_events(snapshot, now),
        "can_see_hiring": can_see,
        "open_roles": sum(opening.open_at(now) for opening in found) if can_see else None,
        "open_engineering": engineering_open(found, now) if can_see else None,
    }


def company_cards(db: Session, user: User, now: datetime) -> list[CompanyCard]:
    """The user's companies that have a snapshot, most recently researched first."""
    companies = own_companies(db, user)
    counts = _done(db, [company.id for company in companies])
    cards = []
    for company in companies:
        if not counts.get(company.id):
            continue
        snapshot = newest_snapshot(db, company.id)
        if snapshot is None:
            continue
        boards, listings = load_history(db, company.id)
        found = openings(boards, listings)
        cards.append(
            CompanyCard(
                **_card_fields(company, snapshot, counts[company.id], found, readable(boards), now)
            )
        )
    return sorted(cards, key=lambda card: card.researched_at or now, reverse=True)


def can_view(db: Session, user: User, company: Company) -> bool:
    """Admins see any company; others only one they applied to."""
    return is_admin(user) or any(item.id == company.id for item in own_companies(db, user))


def _weeks(found: list[Opening], now: datetime) -> list[HiringWeekOut]:
    return [
        HiringWeekOut(
            week_start=week.start,
            open=week.open,
            open_engineering=week.open_engineering,
            opened=week.opened,
            closed=week.closed,
        )
        for week in weekly(found, now)
    ]


def company_detail(db: Session, company: Company, now: datetime) -> CompanyDetail | None:
    """One company's page, or None when it has no snapshot."""
    history = db.scalars(
        select(CompanyResearch)
        .where(
            CompanyResearch.company_id == company.id,
            CompanyResearch.status == TailorStatus.DONE.value,
        )
        .order_by(CompanyResearch.finished_at.desc(), CompanyResearch.id)
    ).all()
    if not history:
        return None
    boards, listings = load_history(db, company.id)
    found = openings(boards, listings)
    can_see = readable(boards)
    year_ago = now - RECENT_EVENTS
    return CompanyDetail(
        **_card_fields(company, history[0], len(history), found, can_see, now),
        history=[to_out(snapshot) for snapshot in history],
        weeks=_weeks(found, now) if can_see else [],
        median_days_open=median_days_open(found) if can_see else None,
        reposts_last_year=(
            sum(item.repost.opened >= year_ago for item in reposts(found)) if can_see else 0
        ),
    )


def hiring_csv(db: Session, company: Company, now: datetime) -> str:
    """The weekly series as CSV. Only a header when Callback doesn't read the company's board."""
    boards, listings = load_history(db, company.id)
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(WEEK_COLUMNS)
    if readable(boards):
        for week in _weeks(openings(boards, listings), now):
            writer.writerow(
                [
                    week.week_start.isoformat(),
                    week.open,
                    week.open_engineering,
                    week.opened,
                    week.closed,
                ]
            )
    return out.getvalue()


def _snapshots(db: Session) -> Iterator[tuple[Company, CompanyResearch]]:
    rows = db.execute(
        select(Company, CompanyResearch)
        .join(CompanyResearch, CompanyResearch.company_id == Company.id)
        .where(CompanyResearch.status == TailorStatus.DONE.value)
        .order_by(Company.name, CompanyResearch.finished_at)
    ).all()
    yield from rows


def export_jsonl(db: Session) -> str:
    """One line per finished snapshot, every fact with its sources. Never who asked for it or
    what it cost."""
    lines = []
    for company, snapshot in _snapshots(db):
        lines.append(
            json.dumps(
                {
                    "snapshot_id": str(snapshot.id),
                    "company_id": str(company.id),
                    "company": company.name,
                    "domain": company.domain,
                    "researched_at": snapshot.finished_at.isoformat()
                    if snapshot.finished_at
                    else None,
                    "model": snapshot.model,
                    "searches": snapshot.searches,
                    "sources": snapshot.sources or [],
                    "profile": snapshot.profile or {},
                    "ownership": snapshot.ownership or {},
                    "funding": snapshot.funding or {},
                    "size": snapshot.size or {},
                    "events": snapshot.events or [],
                    "engineering": snapshot.engineering or {},
                    "workforce": snapshot.workforce or {},
                    "interviewing": snapshot.interviewing or {},
                    "confidence": snapshot.confidence or {},
                    "dropped": snapshot.dropped or {},
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    return "".join(f"{line}\n" for line in lines)


def events_csv(db: Session) -> str:
    """One row per dated event per company. An event found again in a later snapshot is one
    row, from the newest snapshot that has it."""
    rows: dict[tuple[uuid.UUID, str, str, str], list[Any]] = {}
    for company, snapshot in _snapshots(db):
        ownership = snapshot.ownership or {}
        sources = snapshot.sources or []
        for event in snapshot.events or []:
            index = event.get("source")
            source = sources[index] if isinstance(index, int) and 0 <= index < len(sources) else {}
            key = (
                company.id,
                event.get("date", ""),
                event.get("kind", ""),
                event.get("detail", ""),
            )
            rows[key] = [
                company.name,
                company.domain or "",
                ownership.get("ticker", ""),
                ownership.get("exchange", ""),
                event.get("date", ""),
                event.get("kind", ""),
                event.get("detail", ""),
                event.get("count") or "",
                event.get("percent") or "",
                source.get("url", ""),
                source.get("title", ""),
                snapshot.finished_at.isoformat() if snapshot.finished_at else "",
            ]
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(EVENT_COLUMNS)
    for row in sorted(rows.values(), key=lambda row: (row[0].lower(), row[4])):
        writer.writerow(row)
    return out.getvalue()
