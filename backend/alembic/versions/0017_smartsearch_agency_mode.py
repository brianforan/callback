"""What SmartSearch does with agency listings: keep them, find the employer's own, or skip them.

Revision ID: 0017
Revises: 0016
"""

import sqlalchemy as sa

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "smartsearch_settings",
        sa.Column("agency_mode", sa.String(20), nullable=False, server_default="include"),
    )


def downgrade() -> None:
    op.drop_column("smartsearch_settings", "agency_mode")
