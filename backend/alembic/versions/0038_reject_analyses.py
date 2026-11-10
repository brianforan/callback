"""Rejection analysis: one report per rejected application, comparing its posting with the
resume sent and the fact sheet.

Revision ID: 0038
Revises: 0037
"""

import sqlalchemy as sa

from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reject_analyses",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey(
                "users.id",
                name="fk_reject_analyses_user_id_users",
                ondelete="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column(
            "profile_id",
            sa.Uuid(),
            sa.ForeignKey(
                "profiles.id",
                name="fk_reject_analyses_profile_id_profiles",
                ondelete="SET NULL",
            ),
            nullable=True,
        ),
        sa.Column(
            "application_id",
            sa.Uuid(),
            sa.ForeignKey(
                "applications.id",
                name="fk_reject_analyses_application_id_applications",
                ondelete="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column(
            "resume_id",
            sa.Uuid(),
            sa.ForeignKey(
                "resumes.id",
                name="fk_reject_analyses_resume_id_resumes",
                ondelete="SET NULL",
            ),
            nullable=True,
        ),
        sa.Column("resume_label", sa.String(120), nullable=True),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("stage", sa.String(20), nullable=True),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("checklist", sa.JSON(), nullable=False),
        sa.Column("technology_gaps", sa.JSON(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("strengths", sa.JSON(), nullable=False),
        sa.Column("experience_gaps", sa.JSON(), nullable=False),
        sa.Column("focus_areas", sa.JSON(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("model", sa.String(40), nullable=True),
        sa.Column("usage", sa.JSON(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("application_id", name="uq_reject_analyses_application_id"),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'done', 'failed')",
            name="ck_reject_analyses_status",
        ),
    )
    op.create_index("ix_reject_analyses_user_id", "reject_analyses", ["user_id"])
    op.create_index("ix_reject_analyses_profile_id", "reject_analyses", ["profile_id"])
    op.create_index("ix_reject_analyses_status", "reject_analyses", ["status"])


def downgrade() -> None:
    op.drop_index("ix_reject_analyses_status", table_name="reject_analyses")
    op.drop_index("ix_reject_analyses_profile_id", table_name="reject_analyses")
    op.drop_index("ix_reject_analyses_user_id", table_name="reject_analyses")
    op.drop_table("reject_analyses")
