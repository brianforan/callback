"""Staged postings, whose application materials are ready, and a fact sheet per user.

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fact_sheets",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE", name="fk_fact_sheets_user_id_users"),
            nullable=False,
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", name="uq_fact_sheets_user_id"),
    )
    op.add_column("job_postings", sa.Column("staged_at", sa.DateTime(timezone=True)))
    op.add_column(
        "job_postings",
        sa.Column(
            "staged_resume_id",
            sa.Uuid(),
            sa.ForeignKey(
                "resumes.id", ondelete="SET NULL", name="fk_job_postings_staged_resume_id_resumes"
            ),
        ),
    )
    op.add_column("job_postings", sa.Column("staged_notes", sa.Text()))


def downgrade() -> None:
    op.drop_column("job_postings", "staged_notes")
    op.drop_constraint(
        "fk_job_postings_staged_resume_id_resumes", "job_postings", type_="foreignkey"
    )
    op.drop_column("job_postings", "staged_resume_id")
    op.drop_column("job_postings", "staged_at")
    op.drop_table("fact_sheets")
