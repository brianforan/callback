"""How close a fit SmartSearch keeps: a wide net with stretches, balanced, or close fits only.

Revision ID: 0016
Revises: 0015
"""

import sqlalchemy as sa

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "smartsearch_settings",
        sa.Column("fit_mode", sa.String(20), nullable=False, server_default="balanced"),
    )


def downgrade() -> None:
    op.drop_column("smartsearch_settings", "fit_mode")
