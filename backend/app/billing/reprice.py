"""Pricing past Scout runs again after a fix to how tailor prices model use.

Each run stores every model request's token counts, so its cost can be worked out again at
today's rules without any model call. Prices live in tailor, so tailor does the pricing; this
gathers the requests, sends them, and writes the new costs back. Searches a run logged as failed
(which Anthropic doesn't bill) come off the request they belonged to.
"""

import logging
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ScoutRun, TailorStatus

logger = logging.getLogger(__name__)

# The agent engine's requests don't name their model; it's always the Scout model.
AGENT_MODEL = "claude-sonnet-5-5"
_TOKENS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "web_fetch_requests",
)

Price = Callable[[list[dict[str, Any]]], list[dict[str, Any]]]


@dataclass(frozen=True)
class RunChange:
    run_id: uuid.UUID
    engine: str
    old_cost: float
    new_cost: float


def _failed_searches(run: ScoutRun) -> Counter[int]:
    return Counter(
        int(step.get("request") or 0)
        for step in run.steps or []
        if step.get("kind") == "error" and str(step.get("detail") or "").startswith("Search failed")
    )


def reprice_runs(db: Session, price: Price, apply: bool) -> list[RunChange]:
    """Every finished or failed run priced again. With apply, the new costs are saved. Commits
    only when applying."""
    changes: list[RunChange] = []
    runs = db.scalars(
        select(ScoutRun).where(
            ScoutRun.status.in_((TailorStatus.DONE.value, TailorStatus.FAILED.value))
        )
    ).all()
    for run in runs:
        stored = [dict(item) for item in run.request_costs or []]
        if not stored:
            continue
        failed = _failed_searches(run)
        requests = []
        for item in stored:
            searches = int(item.get("web_search_requests") or 0)
            searches = max(0, searches - failed.get(int(item.get("request") or 0), 0))
            item["web_search_requests"] = searches
            requests.append(
                {
                    "model": item.get("model") or AGENT_MODEL,
                    "web_search_requests": searches,
                    **{name: int(item.get(name) or 0) for name in _TOKENS},
                }
            )
        priced = price(requests)
        breakdown: dict[str, float] = {}
        for item, cost in zip(stored, priced, strict=True):
            item["cost_usd"] = cost["cost_usd"]
            for part, dollars in cost["cost_breakdown"].items():
                breakdown[part] = breakdown.get(part, 0.0) + dollars
        new_cost = round(sum(cost["cost_usd"] for cost in priced), 4)
        changes.append(
            RunChange(
                run_id=run.id,
                engine=(run.settings or {}).get("engine") or "agent",
                old_cost=run.cost_usd or 0.0,
                new_cost=new_cost,
            )
        )
        if apply:
            run.request_costs = stored
            run.cost_usd = new_cost
            run.cost_breakdown = {part: round(value, 4) for part, value in breakdown.items()}
            run.usage = {
                **(run.usage or {}),
                "web_search_requests": sum(item["web_search_requests"] for item in stored),
            }
    if apply:
        db.commit()
        logger.info("Priced %d Scout runs again and saved them", len(changes))
    return changes
