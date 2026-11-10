"""Initial schema: users, API tokens, resumes, applications, status events.

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

STATUS_CHECK = (
    "status IN ('applied', 'screening', 'interview', 'offer', 'rejected', 'ghosted', 'withdrawn')"
)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_table(
        "api_tokens",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE", name="fk_api_tokens_user_id_users"),
            nullable=False,
        ),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("token_hash", name="uq_api_tokens_token_hash"),
    )
    op.create_table(
        "resumes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE", name="fk_resumes_user_id_users"),
            nullable=False,
        ),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("source_tex", sa.Text(), nullable=False),
        sa.Column("plain_text", sa.Text(), nullable=False),
        sa.Column("word_count", sa.Integer(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "content_sha256", name="uq_resumes_user_content"),
    )
    op.create_index("ix_resumes_user_id", "resumes", ["user_id"])
    op.create_table(
        "applications",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE", name="fk_applications_user_id_users"),
            nullable=False,
        ),
        sa.Column(
            "resume_id",
            sa.Uuid(),
            sa.ForeignKey(
                "resumes.id", ondelete="RESTRICT", name="fk_applications_resume_id_resumes"
            ),
            nullable=True,
        ),
        sa.Column("company", sa.String(200), nullable=False),
        sa.Column("role_title", sa.String(200), nullable=False),
        sa.Column("job_url", sa.String(2048), nullable=True),
        sa.Column("location", sa.String(200), nullable=True),
        sa.Column("source", sa.String(80), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("applied_on", sa.Date(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(STATUS_CHECK, name="ck_applications_status"),
    )
    op.create_index("ix_applications_user_id", "applications", ["user_id"])
    op.create_index("ix_applications_resume_id", "applications", ["resume_id"])
    op.create_table(
        "status_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "application_id",
            sa.Uuid(),
            sa.ForeignKey(
                "applications.id",
                ondelete="CASCADE",
                name="fk_status_events_application_id_applications",
            ),
            nullable=False,
        ),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(STATUS_CHECK, name="ck_status_events_status"),
    )
    op.create_index("ix_status_events_application_id", "status_events", ["application_id"])


def downgrade() -> None:
    op.drop_table("status_events")
    op.drop_table("applications")
    op.drop_table("resumes")
    op.drop_table("api_tokens")
    op.drop_table("users")
