"""Briefs written on demand for Companies leads.

Revision ID: 0032
Revises: 0031
"""

import sqlalchemy as sa

from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None

_STATUSES = "status IN ('queued', 'running', 'done', 'failed')"


def upgrade() -> None:
    op.create_table(
        "smartsearch_briefs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey(
                "users.id", name="fk_smartsearch_briefs_user_id_users", ondelete="CASCADE"
            ),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            sa.Uuid(),
            sa.ForeignKey(
                "smartsearch_runs.id",
                name="fk_smartsearch_briefs_run_id_smartsearch_runs",
                ondelete="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column("lead_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("credit_cap", sa.Integer(), nullable=False),
        sa.Column("credits_used", sa.Float(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.Column("usage", sa.JSON(), nullable=False),
        sa.Column("request_costs", sa.JSON(), nullable=False),
        sa.Column("billed", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(_STATUSES, name="ck_smartsearch_briefs_status"),
    )
    op.create_index("ix_smartsearch_briefs_user_id", "smartsearch_briefs", ["user_id"])
    op.create_index("ix_smartsearch_briefs_run_id", "smartsearch_briefs", ["run_id"])
    op.create_index("ix_smartsearch_briefs_status", "smartsearch_briefs", ["status"])
    _mark_briefless_leads()


def _mark_briefless_leads() -> None:
    """Leads from Companies runs made before this were saved without a brief and without the
    flag that offers one, so they get it now."""
    runs = sa.table(
        "smartsearch_runs",
        sa.column("id"),
        sa.column("settings", sa.JSON),
        sa.column("leads", sa.JSON),
    )
    connection = op.get_bind()
    for run_id, settings, leads in connection.execute(
        sa.select(runs.c.id, runs.c.settings, runs.c.leads)
    ):
        if (settings or {}).get("engine") != "companies" or not leads:
            continue
        changed = [
            {**lead, "brief_status": "none"}
            if not lead.get("brief_status") and not lead.get("what_they_want")
            else lead
            for lead in leads
        ]
        if changed != leads:
            connection.execute(runs.update().where(runs.c.id == run_id).values(leads=changed))


def downgrade() -> None:
    op.drop_index("ix_smartsearch_briefs_status", table_name="smartsearch_briefs")
    op.drop_index("ix_smartsearch_briefs_run_id", table_name="smartsearch_briefs")
    op.drop_index("ix_smartsearch_briefs_user_id", table_name="smartsearch_briefs")
    op.drop_table("smartsearch_briefs")
