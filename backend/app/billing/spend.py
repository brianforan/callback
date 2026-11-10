"""What Callback's model use costs, measured by Callback and billed by Anthropic, for the owner.

Plain sums over stored rows, no model calls. Measured costs come from each run's and tailoring's
own usage, priced by tailor at the configured list prices. Billed figures come from Anthropic's
usage and cost reports, read by the billing worker. Together they say what a run really costs,
and so what budget to give a friend.
"""

import math
from collections import defaultdict
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from statistics import mean, median
from typing import Any

from app.models import (
    BillingCost,
    CompanyResearch,
    CreditTopUp,
    RejectAnalysis,
    ScoutBrief,
    ScoutRun,
    TailorJob,
    User,
)
from app.postings.parser import canonical_url, company_key

BUDGETS_USD = (5.0, 10.0, 20.0)
_JOBS_SHOWN = 100
_TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "web_search_requests",
)


def _day(moment: datetime | None) -> date | None:
    return moment.date() if moment else None


def _tokens(usage: dict[str, Any] | None) -> dict[str, int]:
    usage = usage or {}
    return {name: int(usage.get(name) or 0) for name in _TOKEN_FIELDS}


def _percentile(values: Sequence[float], share: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(share * len(ordered)) - 1)]


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 4)


def balance(topups: Sequence[CreditTopUp], costs: Sequence[BillingCost]) -> dict[str, Any]:
    """Top-ups minus what Anthropic billed the organization since the first one."""
    since = min((topup.added_on for topup in topups), default=None)
    added = sum(topup.amount_usd for topup in topups)
    billed = sum(cost.amount_usd for cost in costs if since is not None and cost.day >= since)
    return {
        "topups_usd": round(added, 2),
        "billed_since_usd": round(billed, 4),
        "balance_usd": round(added - billed, 4),
        "since": since,
    }


def daily(
    runs: Sequence[ScoutRun],
    tailorings: Sequence[TailorJob],
    costs: Sequence[BillingCost],
    workspace_id: str | None,
    today: date,
    days: int,
    briefs: Sequence[ScoutBrief] = (),
    others: Sequence[RejectAnalysis | CompanyResearch] = (),
) -> list[dict[str, Any]]:
    """Each day: what Callback measured, and what Anthropic billed for its workspace. others are
    rejection reports and company research, which count by the day they finished."""
    measured: dict[date, float] = defaultdict(float)
    for item in others:
        if (day := _day(item.finished_at)) is not None:
            measured[day] += item.cost_usd or 0.0
    for brief in briefs:
        if (day := _day(brief.finished_at)) is not None:
            measured[day] += brief.cost_usd or 0.0
    for run in runs:
        if (day := _day(run.finished_at)) is not None:
            measured[day] += run.cost_usd or 0.0
    for job in tailorings:
        if (day := _day(job.finished_at)) is not None:
            measured[day] += job.cost_usd or 0.0
    billed: dict[date, float] = defaultdict(float)
    reported = {cost.day for cost in costs}
    for cost in costs:
        if workspace_id is None or cost.workspace_id == workspace_id:
            billed[cost.day] += cost.amount_usd
    rows = []
    for offset in range(days):
        day = today - timedelta(days=offset)
        rows.append(
            {
                "day": day,
                "measured_usd": round(measured.get(day, 0.0), 4),
                "billed_usd": round(billed.get(day, 0.0), 4) if day in reported else None,
            }
        )
    return rows


def lead_key(lead: dict[str, Any]) -> str:
    """One job however many runs found it: its posting link, else its company and title."""
    url = canonical_url(lead.get("url"))
    if url:
        return url
    company = company_key(str(lead.get("company") or ""))
    title = str(lead.get("title") or "").strip().lower()
    return f"{company}|{title}" if company or title else str(lead.get("id"))


def per_user(
    users: dict[Any, User],
    runs: Sequence[ScoutRun],
    tailorings: Sequence[TailorJob],
    analyses: Sequence[RejectAnalysis] = (),
    research: Sequence[CompanyResearch] = (),
) -> list[dict[str, Any]]:
    by_user: dict[Any, dict[str, Any]] = defaultdict(
        lambda: {
            "runs": [],
            "tailorings": 0,
            "tailoring_usd": 0.0,
            "leads": set(),
            "analyses": 0,
            "research": 0,
            "other_usd": 0.0,
        }
    )
    for analysis in analyses:
        entry = by_user[analysis.user_id]
        entry["analyses"] += 1
        entry["other_usd"] += analysis.cost_usd or 0.0
    for snapshot in research:
        # The person whose rejection or click started it; None once they're deleted.
        entry = by_user[snapshot.requested_by]
        entry["research"] += 1
        entry["other_usd"] += snapshot.cost_usd or 0.0
    for run in runs:
        entry = by_user[run.user_id]
        entry["runs"].append(run.cost_usd or 0.0)
        # A job two runs both found is one lead.
        entry["leads"].update(lead_key(lead) for lead in run.leads or [])
    for job in tailorings:
        entry = by_user[job.user_id]
        entry["tailorings"] += 1
        entry["tailoring_usd"] += job.cost_usd or 0.0
    rows = []
    for user_id, entry in by_user.items():
        total = sum(entry["runs"]) + entry["tailoring_usd"] + entry["other_usd"]
        run_total = sum(entry["runs"])
        leads = len(entry["leads"])
        rows.append(
            {
                "email": users[user_id].email if user_id in users else "Deleted user",
                "runs": len(entry["runs"]),
                "tailorings": entry["tailorings"],
                "analyses": entry["analyses"],
                "research": entry["research"],
                "leads": leads,
                "measured_usd": round(total, 4),
                "median_run_usd": _round(median(entry["runs"])) if entry["runs"] else None,
                "per_lead_usd": _round(run_total / leads) if leads else None,
            }
        )
    return sorted(rows, key=lambda row: row["measured_usd"], reverse=True)


def quote(runs: Sequence[ScoutRun], tailorings: Sequence[TailorJob]) -> dict[str, Any]:
    """What a typical run, lead and tailoring cost, and what a few budgets would buy."""
    costs = [run.cost_usd for run in runs if run.cost_usd]
    per_lead = [run.cost_usd / len(run.leads) for run in runs if run.cost_usd and run.leads]
    tailoring = [job.cost_usd for job in tailorings if job.cost_usd]
    typical_run = median(costs) if costs else None
    typical_lead = median(per_lead) if per_lead else None
    typical_tailoring = median(tailoring) if tailoring else None
    budgets = [
        {
            "budget_usd": budget,
            "runs": int(budget // typical_run) if typical_run else 0,
            "leads": int(budget // typical_lead) if typical_lead else 0,
            "tailorings": int(budget // typical_tailoring) if typical_tailoring else 0,
        }
        for budget in BUDGETS_USD
    ]
    return {
        "runs_measured": len(costs),
        "median_run_usd": _round(typical_run),
        "mean_run_usd": _round(mean(costs)) if costs else None,
        "p90_run_usd": _round(_percentile(costs, 0.9)),
        "max_run_usd": _round(max(costs)) if costs else None,
        "median_per_lead_usd": _round(typical_lead),
        "median_tailoring_usd": _round(typical_tailoring),
        "budgets": budgets,
    }


def jobs(
    users: dict[Any, User],
    runs: Sequence[ScoutRun],
    tailorings: Sequence[TailorJob],
    analyses: Sequence[RejectAnalysis] = (),
    research: Sequence[CompanyResearch] = (),
) -> list[dict[str, Any]]:
    """The latest model calls, each with its measured usage, newest first. Runs and tailorings
    also carry what was billed; rejection reports and research aren't matched to the bill."""

    def email(user_id: Any) -> str:
        return users[user_id].email if user_id in users else "Deleted user"

    rows: list[dict[str, Any]] = [
        {
            "id": item.id,
            "kind": kind,
            "email": email(user_id),
            "created_at": item.created_at,
            "status": item.status,
            "stop": item.note,
            "leads": 0,
            "measured_usd": round(item.cost_usd or 0.0, 4),
            "measured": _tokens(item.usage),
            "billed": None,
            "requests": [],
        }
        for kind, items in (("rejection_analysis", analyses), ("company_research", research))
        for item in items
        for user_id in [item.user_id if kind == "rejection_analysis" else item.requested_by]
    ]
    for run in runs:
        rows.append(
            {
                "id": run.id,
                "kind": "scout",
                "email": users[run.user_id].email if run.user_id in users else "Deleted user",
                "created_at": run.created_at,
                "status": run.status,
                "stop": run.stop_message,
                "leads": len(run.leads or []),
                "measured_usd": round(run.cost_usd or 0.0, 4),
                "measured": _tokens(run.usage),
                "billed": run.billed,
                "requests": run.request_costs or [],
            }
        )
    for job in tailorings:
        rows.append(
            {
                "id": job.id,
                "kind": "tailoring",
                "email": users[job.user_id].email if job.user_id in users else "Deleted user",
                "created_at": job.created_at,
                "status": job.status,
                "stop": None,
                "leads": 0,
                "measured_usd": round(job.cost_usd or 0.0, 4),
                "measured": _tokens(job.usage),
                "billed": job.billed,
                "requests": [],
            }
        )
    rows.sort(key=lambda row: row["created_at"], reverse=True)
    return rows[:_JOBS_SHOWN]
