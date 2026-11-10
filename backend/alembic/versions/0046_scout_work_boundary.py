"""Each profile's Scout settings keep a work boundary: the work it does, read by a model from its
baselines, fact sheet and description (docs/skills_spec.md, chunk 10). None until a run reads it.

Revision ID: 0046
Revises: 0045
"""

import sqlalchemy as sa

from alembic import op

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("smartsearch_settings", sa.Column("work_boundary", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("smartsearch_settings", "work_boundary")
