"""Billing: per-request run costs, tailoring costs, Anthropic's billed usage and cost, top-ups.

Tailorings made before this have no cost, so one is worked out from their stored token counts at
the prices tailor used then: $2 input, $10 output, $2.50 cache write and $0.20 cache read per
million tokens.

Revision ID: 0019
Revises: 0018
"""

import json

import sqlalchemy as sa

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

_PRICES = {
    "input_tokens": 2.0 / 1_000_000,
    "output_tokens": 10.0 / 1_000_000,
    "cache_creation_input_tokens": 2.5 / 1_000_000,
    "cache_read_input_tokens": 0.2 / 1_000_000,
}


def upgrade() -> None:
    op.add_column(
        "smartsearch_runs",
        sa.Column("request_costs", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column("smartsearch_runs", sa.Column("billed", sa.JSON()))
    op.add_column(
        "tailor_jobs", sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0")
    )
    op.add_column("tailor_jobs", sa.Column("billed", sa.JSON()))
    op.create_table(
        "billing_costs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("workspace_id", sa.String(80)),
        sa.Column("description", sa.String(300), nullable=False),
        sa.Column("cost_type", sa.String(40)),
        sa.Column("model", sa.String(80)),
        sa.Column("token_type", sa.String(80)),
        sa.Column("amount_usd", sa.Float(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_billing_costs_day", "billing_costs", ["day"])
    op.create_table(
        "credit_topups",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("amount_usd", sa.Float(), nullable=False),
        sa.Column("added_on", sa.Date(), nullable=False),
        sa.Column("note", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "billing_state",
        sa.Column("key", sa.String(40), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    jobs = sa.table(
        "tailor_jobs",
        sa.column("id", sa.Uuid()),
        sa.column("usage", sa.JSON()),
        sa.column("cost_usd", sa.Float()),
    )
    connection = op.get_bind()
    for job_id, usage in connection.execute(sa.select(jobs.c.id, jobs.c.usage)).all():
        counts = usage if isinstance(usage, dict) else json.loads(usage or "{}")
        cost = sum((counts.get(field) or 0) * price for field, price in _PRICES.items())
        if cost:
            connection.execute(
                jobs.update().where(jobs.c.id == job_id).values(cost_usd=round(cost, 6))
            )


def downgrade() -> None:
    op.drop_table("billing_state")
    op.drop_table("credit_topups")
    op.drop_index("ix_billing_costs_day", table_name="billing_costs")
    op.drop_table("billing_costs")
    op.drop_column("tailor_jobs", "billed")
    op.drop_column("tailor_jobs", "cost_usd")
    op.drop_column("smartsearch_runs", "billed")
    op.drop_column("smartsearch_runs", "request_costs")
