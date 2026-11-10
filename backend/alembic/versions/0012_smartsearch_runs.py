"""SmartSearch runs, with their reserved and spent credits and the leads they found.

Revision ID: 0012
Revises: 0011
"""

import sqlalchemy as sa

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "smartsearch_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE", name="fk_smartsearch_runs_user_id_users"),
            nullable=False,
        ),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("settings", sa.JSON(), nullable=False),
        sa.Column("credit_cap", sa.Integer(), nullable=False),
        sa.Column("credits_used", sa.Float(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.Column("usage", sa.JSON(), nullable=False),
        sa.Column("searches", sa.Integer(), nullable=False),
        sa.Column("pages", sa.Integer(), nullable=False),
        sa.Column("stop_code", sa.String(20)),
        sa.Column("stop_message", sa.Text()),
        sa.Column("leads", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'done', 'failed')", name="ck_smartsearch_runs_status"
        ),
    )
    op.create_index("ix_smartsearch_runs_user_id", "smartsearch_runs", ["user_id"])
    op.create_index("ix_smartsearch_runs_status", "smartsearch_runs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_smartsearch_runs_status", table_name="smartsearch_runs")
    op.drop_index("ix_smartsearch_runs_user_id", table_name="smartsearch_runs")
    op.drop_table("smartsearch_runs")
