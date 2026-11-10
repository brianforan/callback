"""Whether a profile would relocate for an onsite or hybrid job: none, metro or anywhere.

Revision ID: 0035
Revises: 0034
"""

import sqlalchemy as sa

from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "smartsearch_settings",
        sa.Column("relocation", sa.String(10), nullable=False, server_default="none"),
    )


def downgrade() -> None:
    op.drop_column("smartsearch_settings", "relocation")
