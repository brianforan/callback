"""SmartSearch credits are replaced by daily caps on runs and briefs (app_config), so the
credit columns go. Every run and brief keeps its recorded cost_usd.

Revision ID: 0043
Revises: 0042
"""

import sqlalchemy as sa

from alembic import op

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None

# What a credit was worth, and the old default cap, to fill the columns back on a downgrade.
_USD_PER_CREDIT = 0.05
_RUN_CAP = 40
_BRIEF_CAP = 3


def upgrade() -> None:
    op.drop_column("smartsearch_runs", "credits_used")
    op.drop_column("smartsearch_runs", "credit_cap")
    op.drop_column("smartsearch_briefs", "credits_used")
    op.drop_column("smartsearch_briefs", "credit_cap")
    op.drop_column("smartsearch_settings", "credit_cap")


def downgrade() -> None:
    for table, cap in (
        ("smartsearch_runs", _RUN_CAP),
        ("smartsearch_briefs", _BRIEF_CAP),
    ):
        op.add_column(
            table, sa.Column("credit_cap", sa.Integer(), nullable=False, server_default=str(cap))
        )
        op.add_column(
            table,
            sa.Column("credits_used", sa.Float(), nullable=False, server_default="0"),
        )
        # Credits were the real cost at $0.05 each, never over the cap.
        op.execute(
            sa.text(
                f"UPDATE {table} SET credits_used = "  # noqa: S608 - fixed table names
                f"LEAST(ROUND(CAST(cost_usd / {_USD_PER_CREDIT} AS numeric), 1), credit_cap)"
            )
        )
    op.add_column(
        "smartsearch_settings",
        sa.Column("credit_cap", sa.Integer(), nullable=False, server_default=str(_RUN_CAP)),
    )
