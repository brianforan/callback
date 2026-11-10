"""A label a SmartSearch run can be given, so its leads can be filtered by it.

Nullable: runs made before labels, and runs started without one, have none.

Revision ID: 0036
Revises: 0035
"""

import sqlalchemy as sa

from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("smartsearch_runs", sa.Column("label", sa.String(60), nullable=True))


def downgrade() -> None:
    op.drop_column("smartsearch_runs", "label")
