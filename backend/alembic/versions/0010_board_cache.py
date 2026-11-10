"""Job board results kept in the database, so searches stay fast across restarts.

Revision ID: 0010
Revises: 0009
"""

import sqlalchemy as sa

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "board_cache",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("board", sa.String(40), nullable=False),
        sa.Column("listings", sa.JSON(), nullable=False),
        sa.Column("warning", sa.Text()),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fresh_until", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_board_cache_fetched_at", "board_cache", ["fetched_at"])


def downgrade() -> None:
    op.drop_index("ix_board_cache_fetched_at", table_name="board_cache")
    op.drop_table("board_cache")
