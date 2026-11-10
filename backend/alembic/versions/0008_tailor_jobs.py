"""Tailoring jobs, worked through one at a time by the tailor worker.

Revision ID: 0008
Revises: 0007
"""

import sqlalchemy as sa

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tailor_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE", name="fk_tailor_jobs_user_id_users"),
            nullable=False,
        ),
        sa.Column(
            "posting_id",
            sa.Uuid(),
            sa.ForeignKey(
                "job_postings.id", ondelete="CASCADE", name="fk_tailor_jobs_posting_id_job_postings"
            ),
            nullable=False,
        ),
        sa.Column(
            "baseline_id",
            sa.Uuid(),
            sa.ForeignKey(
                "resumes.id", ondelete="SET NULL", name="fk_tailor_jobs_baseline_id_resumes"
            ),
        ),
        sa.Column(
            "result_resume_id",
            sa.Uuid(),
            sa.ForeignKey(
                "resumes.id", ondelete="SET NULL", name="fk_tailor_jobs_result_resume_id_resumes"
            ),
        ),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("reasons", sa.JSON(), nullable=False),
        sa.Column("changelog", sa.JSON(), nullable=False),
        sa.Column("gaps", sa.JSON(), nullable=False),
        sa.Column("usage", sa.JSON(), nullable=False),
        sa.Column("result_pdf", sa.LargeBinary()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'done', 'failed')", name="ck_tailor_jobs_status"
        ),
    )
    op.create_index("ix_tailor_jobs_user_id", "tailor_jobs", ["user_id"])
    op.create_index("ix_tailor_jobs_posting_id", "tailor_jobs", ["posting_id"])
    op.create_index("ix_tailor_jobs_status", "tailor_jobs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_tailor_jobs_status", table_name="tailor_jobs")
    op.drop_index("ix_tailor_jobs_posting_id", table_name="tailor_jobs")
    op.drop_index("ix_tailor_jobs_user_id", table_name="tailor_jobs")
    op.drop_table("tailor_jobs")
