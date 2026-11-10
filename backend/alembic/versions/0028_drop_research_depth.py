"""SmartSearch no longer researches companies, so its research depth setting is dropped.

Downgrade adds the column back with every row at "standard", the old default.

Revision ID: 0028
Revises: 0027
"""

import sqlalchemy as sa

from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("smartsearch_settings", "research_depth")


def downgrade() -> None:
    op.add_column(
        "smartsearch_settings",
        sa.Column("research_depth", sa.String(20), nullable=False, server_default="standard"),
    )
    op.alter_column("smartsearch_settings", "research_depth", server_default=None)
