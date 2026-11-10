"""A Scout run read back: what it used, what it cost, what it did, and what to change.

Everything here is worked out by plain rules from what the run stored (its limits, usage, cost
breakdown, step log and leads). Nothing calls a model. The findings are short, specific notes on
why the run stopped and what would make the next one better.
"""

import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlsplit

from app.models import ScoutRun, TailorStatus

FindingLevel = Literal["good", "info", "warning"]

_RESULTS = re.compile(r"^(\d+) results, (\d+) new$")

# Hosts of applicant tracking systems, where a posting comes from the company itself.
ATS_HOSTS = (
    "greenhouse.io",
    "lever.co",
    "ashbyhq.com",
    "myworkdayjobs.com",
    "workable.com",
    "smartrecruiters.com",
    "bamboohr.com",
    "jobvite.com",
    "icims.com",
    "recruitee.com",
    "breezy.hr",
)
# Hosts that repost other companies' jobs, so a lead from one may be stale or second-hand.
AGGREGATOR_HOSTS = (
    "jobgether.com",
    "linkedin.com",
    "indeed.com",
    "glassdoor.com",
    "ziprecruiter.com",
    "builtin.com",
    "wellfound.com",
    "simplyhired.com",
    "monster.com",
    "remoterocketship.com",
    "remotive.com",
    "weworkremotely.com",
    "dice.com",
    "talent.com",
    "jooble.org",
    "adzuna.com",
)

COST_LABELS = {
    "cache_write": "Adding pages and results to the run",
    "cache_read": "Re-reading the run so far, each step",
    "output": "The agent's writing and reasoning",
    "web_search": "Web search fees",
    "input": "Other input",
}
_COST_TIPS = {
    "cache_write": (
        "Each page and search result is written into the run once. Fewer pages per lead, or "
        "shorter page reads, cut this most."
    ),
    "cache_read": (
        "Every step re-reads everything gathered so far, so long runs grow costly. A smaller lead "
        "target or fewer steps cut this."
    ),
    "output": "The agent's own notes and briefs.",
    "web_search": "Each web search is billed on its own. Fewer, sharper searches cut this.",
    "input": "Text sent outside the cache.",
}
_REFUSAL_KINDS = (
    ("didn't appear", "its link never appeared in a search or page"),
    ("already has", "you already have the job"),
    ("hidden", "the company is hidden"),
    ("already recorded", "it was a repeat in the same run"),
    ("invalid", "the lead's fields were invalid"),
)
_STOP_LIMIT = {
    "target_reached": "leads",
    "max_searches": "searches",
    "max_pages": "pages",
    "time_limit": "minutes",
    "budget": "spend",
}


@dataclass(frozen=True)
class Limit:
    key: str
    label: str
    used: float
    limit: float
    unit: str
    stopped_run: bool


@dataclass(frozen=True)
class CostPart:
    key: str
    label: str
    usd: float
    share: float


@dataclass(frozen=True)
class Query:
    request: int
    query: str
    results: int | None
    new: int | None
    repeated: bool


@dataclass(frozen=True)
class Page:
    request: int
    url: str
    host: str
    became_lead: bool


@dataclass(frozen=True)
class Finding:
    level: FindingLevel
    title: str
    detail: str


@dataclass(frozen=True)
class Report:
    minutes_used: float | None
    requests: int
    nudges: int
    limits: list[Limit]
    cost_usd: float
    cost_per_lead_usd: float | None
    costs: list[CostPart]
    queries: list[Query]
    pages: list[Page]
    refused: list[str]
    errors: list[tuple[str, int]]
    lead_hosts: list[tuple[str, int]]
    findings: list[Finding]


def _host(url: str) -> str:
    try:
        host = urlsplit(url.strip()).netloc.lower()
    except ValueError:
        return url
    return host.removeprefix("www.")


def _same_url(url: str) -> str:
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip()
    return f"{parts.netloc.lower().removeprefix('www.')}{parts.path.rstrip('/')}?{parts.query}"


def _is_host(host: str, hosts: tuple[str, ...]) -> bool:
    return any(host == known or host.endswith(f".{known}") for known in hosts)


def _plural(count: float, one: str, many: str | None = None) -> str:
    return f"{count:g} {one if count == 1 else (many or one + 's')}"


def _minutes(run: ScoutRun) -> float | None:
    if run.started_at is None or run.finished_at is None:
        return None
    started: datetime = run.started_at
    finished: datetime = run.finished_at
    if started.tzinfo is None and finished.tzinfo is not None:
        started = started.replace(tzinfo=finished.tzinfo)
    if finished.tzinfo is None and started.tzinfo is not None:
        finished = finished.replace(tzinfo=started.tzinfo)
    return round(max(0.0, (finished - started).total_seconds()) / 60, 1)


def _queries(steps: list[dict[str, Any]]) -> list[Query]:
    """Each search with its results, paired in order within the request they were made in."""
    by_request: dict[int, tuple[list[str], list[tuple[int, int] | None]]] = {}
    for step in steps:
        request = int(step.get("request") or 0)
        searches, results = by_request.setdefault(request, ([], []))
        if step.get("kind") == "search":
            searches.append(str(step.get("detail", "")))
        elif step.get("kind") == "results":
            match = _RESULTS.match(str(step.get("detail", "")))
            results.append((int(match[1]), int(match[2])) if match else None)
    seen: set[str] = set()
    queries = []
    for request in sorted(by_request):
        searches, results = by_request[request]
        for index, text in enumerate(searches):
            counts = results[index] if index < len(results) else None
            key = " ".join(text.lower().split())
            queries.append(
                Query(
                    request=request,
                    query=text,
                    results=counts[0] if counts else None,
                    new=counts[1] if counts else None,
                    repeated=key in seen,
                )
            )
            seen.add(key)
    return queries


def _pages(steps: list[dict[str, Any]], leads: list[dict[str, Any]]) -> list[Page]:
    lead_urls = set()
    for lead in leads:
        lead_urls.add(_same_url(str(lead.get("url", ""))))
        lead_urls.update(_same_url(str(source)) for source in lead.get("sources") or [])
    return [
        Page(
            request=int(step.get("request") or 0),
            url=str(step.get("detail", "")),
            host=_host(str(step.get("detail", ""))),
            became_lead=_same_url(str(step.get("detail", ""))) in lead_urls,
        )
        for step in steps
        if step.get("kind") == "fetch"
    ]


def _refusal_reason(detail: str) -> str:
    text = detail.lower()
    for marker, reason in _REFUSAL_KINDS:
        if marker in text:
            return reason
    return "another reason"


def _limits(run: ScoutRun, minutes_used: float | None) -> list[Limit]:
    limits = (run.settings or {}).get("limits", {})
    stopped = _STOP_LIMIT.get(run.stop_code or "")
    rows = [
        ("leads", "Leads", len(run.leads or []), limits.get("target_leads"), "leads"),
        ("searches", "Web searches", run.searches, limits.get("max_searches"), "searches"),
        (
            "pages",
            "Postings checked"
            if (run.settings or {}).get("engine") == "companies"
            else "Pages read",
            run.pages,
            limits.get("max_pages"),
            "pages",
        ),
        ("minutes", "Time", minutes_used, limits.get("max_minutes"), "minutes"),
        # Runs from before the dollar ceiling have none, and show no row.
        ("spend", "Spending", run.cost_usd, _budget(run), "USD"),
    ]
    return [
        Limit(key=key, label=label, used=used, limit=limit, unit=unit, stopped_run=key == stopped)
        for key, label, used, limit, unit in rows
        if used is not None and limit
    ]


def _budget(run: ScoutRun) -> float | None:
    """The most the run could spend, fixed when it was queued, or None for older runs."""
    budget = (run.settings or {}).get("budget_usd")
    return float(budget) if isinstance(budget, int | float) and budget > 0 else None


def _costs(run: ScoutRun) -> list[CostPart]:
    breakdown = run.cost_breakdown or {}
    total = sum(breakdown.values())
    if total <= 0:
        return []
    parts = [
        CostPart(
            key=key,
            label=COST_LABELS.get(key, key),
            usd=round(usd, 4),
            share=round(usd / total, 3),
        )
        for key, usd in breakdown.items()
        if usd > 0
    ]
    return sorted(parts, key=lambda part: part.usd, reverse=True)


def _stop_finding(run: ScoutRun, limits: list[Limit], nudges: int) -> Finding | None:
    code = run.stop_code or ""
    leads = len(run.leads or [])
    by_key = {limit.key: limit for limit in limits}
    target = by_key["leads"].limit if "leads" in by_key else None
    if run.status == TailorStatus.FAILED.value:
        return Finding("warning", "The run failed", run.stop_message or "No reason was recorded.")
    if code == "target_reached":
        return Finding("good", "It found every lead you asked for", run.stop_message or "")
    if code in ("finished", "max_requests") and (target is None or leads < target):
        left = [
            _plural(by_key[key].limit - by_key[key].used, one, many)
            for key, one, many in (
                ("searches", "search", "searches"),
                ("pages", "page read", "page reads"),
            )
            if key in by_key and by_key[key].limit - by_key[key].used > 0
        ]
        asked = (
            f" It had already been asked to keep going {_plural(nudges, 'time')}." if nudges else ""
        )
        return Finding(
            "warning",
            "The agent ended the run itself, with budget left",
            f"It still had {', '.join(left) or 'budget'} when it stopped.{asked} None of your "
            "limits stopped it, so raising them wouldn't have helped this run.",
        )
    if code == "budget":
        return Finding(
            "info",
            "It reached the most one run may spend",
            f"{run.stop_message or ''} The ceiling is a safety stop an admin sets. Fewer "
            "postings to check makes a run cheaper.",
        )
    if code in _STOP_LIMIT:
        limit = by_key.get(_STOP_LIMIT[code])
        name = limit.label.lower() if limit else code
        more = (
            f" To get more than {_plural(leads, 'lead')}, raise the {name} limit."
            if target and leads < target
            else ""
        )
        return Finding("info", f"It stopped at your {name} limit", f"{run.stop_message}{more}")
    if code == "dry_streak":
        return Finding(
            "info",
            "Its searches kept coming back with results it had already seen",
            "A broader description, more title families or another location may give it new "
            "ground to cover.",
        )
    if code in ("model_error", "refusal"):
        return Finding("warning", "The model stopped the run", run.stop_message or "")
    return None


def build_report(run: ScoutRun) -> Report:
    steps = run.steps or []
    leads = run.leads or []
    minutes_used = _minutes(run)
    limits = _limits(run, minutes_used)
    costs = _costs(run)
    queries = _queries(steps)
    pages = _pages(steps, leads)
    nudges = sum(
        1
        for step in steps
        if step.get("kind") == "note" and str(step.get("detail", "")).startswith("Asked to keep")
    )
    refused = [str(step.get("detail", "")) for step in steps if step.get("kind") == "skipped"]
    error_counts = Counter(
        str(step.get("detail", "")) for step in steps if step.get("kind") == "error"
    )
    hosts = Counter(_host(str(lead.get("url", ""))) for lead in leads)
    requests = max((int(step.get("request") or 0) for step in steps), default=0)
    per_lead = round(run.cost_usd / len(leads), 4) if leads and run.cost_usd else None

    findings: list[Finding] = []
    stop = _stop_finding(run, limits, nudges)
    if stop:
        findings.append(stop)

    target = next((limit.limit for limit in limits if limit.key == "leads"), None)
    if per_lead is not None:
        detail = f"{_plural(len(leads), 'lead')} for ${run.cost_usd:.2f}."
        level: FindingLevel = "info"
        budget = _budget(run)
        if target and len(leads) < target:
            needed = run.cost_usd / len(leads) * target
            detail += f" At this rate, {target:g} leads would cost about ${needed:.2f}"
            if budget is None:
                detail += "."
            elif needed > budget:
                level = "warning"
                detail += f", more than this run's ${budget:.2f} spending ceiling."
            else:
                detail += ", inside this run's spending ceiling."
        findings.append(Finding(level, f"About ${per_lead:.2f} a lead", detail))

    if costs and costs[0].share >= 0.4:
        top = costs[0]
        findings.append(
            Finding(
                "info",
                f"{round(top.share * 100)}% of the cost was {top.label.lower()}",
                _COST_TIPS.get(top.key, ""),
            )
        )

    if pages:
        kept = sum(page.became_lead for page in pages)
        detail = f"{_plural(kept, 'page')} of {len(pages)} opened became leads or backed one."
        level = "info"
        if len(pages) >= 4 and kept / len(pages) < 0.3:
            level = "warning"
            detail += (
                " Most postings it opened didn't fit. A more specific description, or fewer "
                "title families, may point it at better ones."
            )
        findings.append(Finding(level, "How often an opened posting became a lead", detail))

    repeated = sum(query.repeated for query in queries)
    stale = sum(1 for query in queries if query.new == 0 and not query.repeated)
    if repeated or stale:
        parts = []
        if repeated:
            parts.append(f"repeated {_plural(repeated, 'query', 'queries')} word for word")
        if stale:
            parts.append(f"ran {_plural(stale, 'search', 'searches')} that found nothing new")
        findings.append(
            Finding(
                "warning" if repeated else "info",
                "Some searches were wasted",
                f"It {' and '.join(parts)}, out of {len(queries)}.",
            )
        )

    aggregated = [host for host in hosts if _is_host(host, AGGREGATOR_HOSTS)]
    if aggregated:
        count = sum(hosts[host] for host in aggregated)
        findings.append(
            Finding(
                "warning",
                f"{_plural(count, 'lead')} came from job aggregators",
                f"From {', '.join(sorted(aggregated))}. Check each one is still open on the "
                "company's own site before applying.",
            )
        )
    direct = sum(hosts[host] for host in hosts if _is_host(host, ATS_HOSTS))
    if direct:
        findings.append(
            Finding(
                "good",
                f"{_plural(direct, 'lead')} came straight from a company's hiring system",
                "Greenhouse, Lever, Ashby and similar pages are the company's own posting.",
            )
        )

    if refused:
        reasons = Counter(_refusal_reason(detail) for detail in refused)
        summary = "; ".join(f"{count} because {reason}" for reason, count in reasons.most_common())
        findings.append(
            Finding(
                "info",
                f"It tried to keep {_plural(len(refused), 'job')} that were refused",
                summary,
            )
        )

    caps = sum(count for detail, count in error_counts.items() if "max_uses_exceeded" in detail)
    others = {
        detail: count for detail, count in error_counts.items() if "max_uses_exceeded" not in detail
    }
    if caps:
        findings.append(
            Finding(
                "info",
                f"It reached a step's search or page cap {_plural(caps, 'time')}",
                "That only ends the step, not the run, but the agent has read it as the end "
                "before.",
            )
        )
    if others:
        failed = _plural(sum(others.values()), "search or page read", "searches or page reads")
        findings.append(
            Finding(
                "warning",
                f"{failed} failed",
                "; ".join(f"{detail} ({count})" for detail, count in others.items()),
            )
        )

    return Report(
        minutes_used=minutes_used,
        requests=requests,
        nudges=nudges,
        limits=limits,
        cost_usd=run.cost_usd,
        cost_per_lead_usd=per_lead,
        costs=costs,
        queries=queries,
        pages=pages,
        refused=refused,
        errors=sorted(error_counts.items(), key=lambda item: -item[1]),
        lead_hosts=hosts.most_common(),
        findings=findings,
    )
