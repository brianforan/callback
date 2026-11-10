"""Resume source format: "tex", or "pdf" for an uploaded PDF that is analysed but not tailored.

Every existing resume was uploaded as LaTeX, so they all start as "tex".

Revision ID: 0025
Revises: 0024
"""

import sqlalchemy as sa

from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "resumes",
        sa.Column("source_format", sa.String(8), nullable=False, server_default="tex"),
    )


def downgrade() -> None:
    op.drop_column("resumes", "source_format")
