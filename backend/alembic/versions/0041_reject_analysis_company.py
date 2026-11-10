"""Rejection reports set the company's typical engineer beside the resume, give a rough
estimate of how much the rejection may have been about the person, and name the company
research snapshot they were written with.

Revision ID: 0041
Revises: 0040
"""

import sqlalchemy as sa

from alembic import op

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "reject_analyses",
        sa.Column("comparison", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "reject_analyses",
        sa.Column("estimate", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.add_column(
        "reject_analyses",
        sa.Column(
            "company_research_id",
            sa.Uuid(),
            sa.ForeignKey(
                "company_research.id",
                name="fk_reject_analyses_company_research_id_company_research",
                ondelete="SET NULL",
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column("reject_analyses", "company_research_id")
    op.drop_column("reject_analyses", "estimate")
    op.drop_column("reject_analyses", "comparison")
