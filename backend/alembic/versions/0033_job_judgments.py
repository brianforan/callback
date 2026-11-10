"""The judge's answers about catalog jobs, per profile and brief, for Companies runs.

Revision ID: 0033
Revises: 0032
"""

import sqlalchemy as sa

from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "job_judgments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", name="fk_job_judgments_user_id_users", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "profile_id",
            sa.Uuid(),
            sa.ForeignKey(
                "profiles.id", name="fk_job_judgments_profile_id_profiles", ondelete="CASCADE"
            ),
            nullable=False,
        ),
        sa.Column(
            "catalog_job_id",
            sa.Uuid(),
            sa.ForeignKey(
                "catalog_jobs.id",
                name="fk_job_judgments_catalog_job_id_catalog_jobs",
                ondelete="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column("brief_key", sa.String(64), nullable=False),
        sa.Column("text_key", sa.String(64), nullable=False),
        sa.Column("keep", sa.Boolean(), nullable=False),
        sa.Column("fit", sa.String(20), nullable=False),
        sa.Column("location_ok", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "run_id",
            sa.Uuid(),
            sa.ForeignKey(
                "smartsearch_runs.id",
                name="fk_job_judgments_run_id_smartsearch_runs",
                ondelete="SET NULL",
            ),
            nullable=True,
        ),
        sa.Column("judged_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "profile_id", "catalog_job_id", "brief_key", name="uq_job_judgments_profile_job_brief"
        ),
    )
    op.create_index("ix_job_judgments_user_id", "job_judgments", ["user_id"])
    op.create_index("ix_job_judgments_profile_id", "job_judgments", ["profile_id"])
    op.create_index("ix_job_judgments_catalog_job_id", "job_judgments", ["catalog_job_id"])


def downgrade() -> None:
    op.drop_index("ix_job_judgments_catalog_job_id", table_name="job_judgments")
    op.drop_index("ix_job_judgments_profile_id", table_name="job_judgments")
    op.drop_index("ix_job_judgments_user_id", table_name="job_judgments")
    op.drop_table("job_judgments")
