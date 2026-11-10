"""Posting text read from the posting's link when it was saved without any: whether a read is
waiting, done or failed, why it failed, and when it was read.

Revision ID: 0039
Revises: 0038
"""

import sqlalchemy as sa

from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("job_postings", sa.Column("text_status", sa.String(10)))
    op.add_column("job_postings", sa.Column("text_error", sa.String(300)))
    op.add_column("job_postings", sa.Column("text_fetched_at", sa.DateTime(timezone=True)))
    op.create_check_constraint(
        "ck_job_postings_text_status",
        "job_postings",
        "text_status IN ('pending', 'fetched', 'failed')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_job_postings_text_status", "job_postings", type_="check")
    op.drop_column("job_postings", "text_fetched_at")
    op.drop_column("job_postings", "text_error")
    op.drop_column("job_postings", "text_status")
