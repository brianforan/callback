"""A compiled PDF for each resume, made by the tailor worker.

Revision ID: 0009
Revises: 0008
"""

import sqlalchemy as sa

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("resumes", sa.Column("pdf", sa.LargeBinary()))
    op.add_column("resumes", sa.Column("pdf_pages", sa.Integer()))
    op.add_column("resumes", sa.Column("pdf_error", sa.Text()))


def downgrade() -> None:
    op.drop_column("resumes", "pdf_error")
    op.drop_column("resumes", "pdf_pages")
    op.drop_column("resumes", "pdf")
