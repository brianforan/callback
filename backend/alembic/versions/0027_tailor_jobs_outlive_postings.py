"""A tailoring job outlives its posting: deleting the posting clears the link instead.

The daily limit counts tailor_jobs rows and the Spend page reads them, so deleting a posting
used to hand a tailoring back and erase what it cost. No rows change on upgrade. Downgrade
deletes the jobs whose posting is gone, which is what the old cascade would have done.

Revision ID: 0027
Revises: 0026
"""

import sqlalchemy as sa

from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None

_FK = "fk_tailor_jobs_posting_id_job_postings"


def _replace_foreign_key(ondelete: str) -> None:
    op.drop_constraint(_FK, "tailor_jobs", type_="foreignkey")
    op.create_foreign_key(
        _FK, "tailor_jobs", "job_postings", ["posting_id"], ["id"], ondelete=ondelete
    )


def upgrade() -> None:
    op.alter_column("tailor_jobs", "posting_id", existing_type=sa.Uuid(), nullable=True)
    _replace_foreign_key("SET NULL")


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM tailor_jobs WHERE posting_id IS NULL"))
    op.alter_column("tailor_jobs", "posting_id", existing_type=sa.Uuid(), nullable=False)
    _replace_foreign_key("CASCADE")
