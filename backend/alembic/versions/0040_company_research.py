"""Company research snapshots: one row per snapshot of a company, shared by every user and never
written over once finished.

Revision ID: 0040
Revises: 0039
"""

import sqlalchemy as sa

from alembic import op

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "company_research",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "company_id",
            sa.Uuid(),
            sa.ForeignKey(
                "companies.id", name="fk_company_research_company_id_companies", ondelete="CASCADE"
            ),
            nullable=False,
        ),
        sa.Column(
            "requested_by",
            sa.Uuid(),
            sa.ForeignKey(
                "users.id", name="fk_company_research_requested_by_users", ondelete="SET NULL"
            ),
        ),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("note", sa.Text()),
        sa.Column("sources", sa.JSON(), nullable=False),
        sa.Column("profile", sa.JSON(), nullable=False),
        sa.Column("ownership", sa.JSON(), nullable=False),
        sa.Column("funding", sa.JSON(), nullable=False),
        sa.Column("size", sa.JSON(), nullable=False),
        sa.Column("events", sa.JSON(), nullable=False),
        sa.Column("engineering", sa.JSON(), nullable=False),
        sa.Column("workforce", sa.JSON(), nullable=False),
        sa.Column("interviewing", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.JSON(), nullable=False),
        sa.Column("dropped", sa.JSON(), nullable=False),
        sa.Column("searches", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("model", sa.String(40)),
        sa.Column("usage", sa.JSON(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'done', 'failed')", name="ck_company_research_status"
        ),
    )
    op.create_index("ix_company_research_status", "company_research", ["status"])
    op.create_index("ix_company_research_requested_by", "company_research", ["requested_by"])
    op.create_index(
        "ix_company_research_company_id_finished_at",
        "company_research",
        ["company_id", "finished_at"],
    )


def downgrade() -> None:
    op.drop_table("company_research")
