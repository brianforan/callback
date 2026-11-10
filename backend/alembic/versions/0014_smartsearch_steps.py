"""The steps each SmartSearch run took, so a run can be read back and the search refined.

Revision ID: 0014
Revises: 0013
"""

import sqlalchemy as sa

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "smartsearch_runs",
        sa.Column("steps", sa.JSON(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("smartsearch_runs", "steps")
