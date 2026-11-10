"""What each SmartSearch run's cost was made of, for the run report.

Runs made before this have no breakdown, so one is worked out from their stored token and search
counts at the prices tailor used when they ran: $2 input, $10 output, $2.50 cache write and
$0.20 cache read per million tokens, and $0.01 a web search. Later runs get theirs from tailor.

Revision ID: 0015
Revises: 0014
"""

import json

import sqlalchemy as sa

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

# Prices in force for runs before this revision. Fixed here, since the past doesn't change.
_PRICES = {
    "input": ("input_tokens", 2.0 / 1_000_000),
    "output": ("output_tokens", 10.0 / 1_000_000),
    "cache_write": ("cache_creation_input_tokens", 2.5 / 1_000_000),
    "cache_read": ("cache_read_input_tokens", 0.2 / 1_000_000),
    "web_search": ("web_search_requests", 0.01),
}


def upgrade() -> None:
    op.add_column(
        "smartsearch_runs",
        sa.Column("cost_breakdown", sa.JSON(), nullable=False, server_default="{}"),
    )
    runs = sa.table(
        "smartsearch_runs",
        sa.column("id", sa.Uuid()),
        sa.column("usage", sa.JSON()),
        sa.column("cost_breakdown", sa.JSON()),
    )
    connection = op.get_bind()
    for run_id, usage in connection.execute(sa.select(runs.c.id, runs.c.usage)).all():
        counts = usage if isinstance(usage, dict) else json.loads(usage or "{}")
        if not counts:
            continue
        breakdown = {
            part: round((counts.get(field) or 0) * price, 4)
            for part, (field, price) in _PRICES.items()
        }
        connection.execute(
            runs.update().where(runs.c.id == run_id).values(cost_breakdown=breakdown)
        )


def downgrade() -> None:
    op.drop_column("smartsearch_runs", "cost_breakdown")
