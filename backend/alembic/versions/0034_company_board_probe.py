"""When code last tried a company's name on Greenhouse, Lever and Ashby.

Revision ID: 0034
Revises: 0033
"""

import sqlalchemy as sa

from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "companies", sa.Column("board_probed_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("companies", "board_probed_at")
