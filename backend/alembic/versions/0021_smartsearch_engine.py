"""SmartSearch's engine: the agent, or the cheaper pipeline of fixed stages.

Revision ID: 0021
Revises: 0020
"""

import sqlalchemy as sa

from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "smartsearch_settings",
        sa.Column("engine", sa.String(20), nullable=False, server_default="agent"),
    )


def downgrade() -> None:
    op.drop_column("smartsearch_settings", "engine")
