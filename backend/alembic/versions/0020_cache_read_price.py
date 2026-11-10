"""Reprice past runs and tailorings: Sonnet 5.5 cache reads are $0.10 per million, not $0.20.

Every stored cost is worked out again from its own token and search counts, at Anthropic's
prices for Sonnet 5.5: $2 input, $10 output, $2.50 cache write and $0.10 cache read per million
tokens, and $0.01 a web search. That covers each run's total, its breakdown by part and each
request in it, and each tailoring. A finished run is then charged its corrected cost in credits,
never more than its cap, at SMARTSEARCH_USD_PER_CREDIT ($0.05 by default). Runs still waiting or
running hold their cap and are left alone. Downgrading leaves the corrected figures in place,
since the wrong ones aren't worth restoring.

Revision ID: 0020
Revises: 0019
"""

import json
import os
from typing import Any

import sqlalchemy as sa

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

_MILLION = 1_000_000
_PRICES = {
    "input": ("input_tokens", 2.0 / _MILLION),
    "output": ("output_tokens", 10.0 / _MILLION),
    "cache_write": ("cache_creation_input_tokens", 2.5 / _MILLION),
    "cache_read": ("cache_read_input_tokens", 0.1 / _MILLION),
    "web_search": ("web_search_requests", 0.01),
}


def _json(value: Any, default: Any) -> Any:
    if value is None:
        return default
    return value if isinstance(value, dict | list) else json.loads(value)


def _parts(counts: dict[str, Any]) -> dict[str, float]:
    return {part: (counts.get(field) or 0) * price for part, (field, price) in _PRICES.items()}


def upgrade() -> None:
    usd_per_credit = float(os.environ.get("SMARTSEARCH_USD_PER_CREDIT") or 0.05)
    connection = op.get_bind()

    runs = sa.table(
        "smartsearch_runs",
        sa.column("id", sa.Uuid()),
        sa.column("status", sa.String()),
        sa.column("usage", sa.JSON()),
        sa.column("credit_cap", sa.Integer()),
        sa.column("cost_usd", sa.Float()),
        sa.column("credits_used", sa.Float()),
        sa.column("cost_breakdown", sa.JSON()),
        sa.column("request_costs", sa.JSON()),
    )
    rows = connection.execute(
        sa.select(runs.c.id, runs.c.status, runs.c.usage, runs.c.credit_cap, runs.c.request_costs)
    ).all()
    for run_id, run_status, usage, cap, request_costs in rows:
        counts = _json(usage, {})
        if not counts:
            continue
        parts = _parts(counts)
        cost = sum(parts.values())
        steps = [
            {**step, "cost_usd": round(sum(_parts(step).values()), 6)}
            for step in _json(request_costs, [])
        ]
        values: dict[str, Any] = {
            "cost_usd": round(cost, 4),
            "cost_breakdown": {part: round(dollars, 4) for part, dollars in parts.items()},
            "request_costs": steps,
        }
        # A failed run was charged nothing, and one still going holds its cap.
        if run_status == "done":
            values["credits_used"] = min(round(cost / usd_per_credit, 1), float(cap))
        connection.execute(runs.update().where(runs.c.id == run_id).values(**values))

    jobs = sa.table(
        "tailor_jobs",
        sa.column("id", sa.Uuid()),
        sa.column("usage", sa.JSON()),
        sa.column("cost_usd", sa.Float()),
    )
    for job_id, usage in connection.execute(sa.select(jobs.c.id, jobs.c.usage)).all():
        counts = _json(usage, {})
        if counts:
            cost = sum(_parts(counts).values())
            connection.execute(
                jobs.update().where(jobs.c.id == job_id).values(cost_usd=round(cost, 6))
            )


def downgrade() -> None:
    pass
