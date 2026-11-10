"""A location for SmartSearch: the city, region or country the person wants to work in.

Revision ID: 0013
Revises: 0012
"""

import sqlalchemy as sa

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "smartsearch_settings",
        sa.Column("location", sa.String(120), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("smartsearch_settings", "location")
