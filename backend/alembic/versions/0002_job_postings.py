"""Normalized job postings and their technologies, linked from applications.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "job_postings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE", name="fk_job_postings_user_id_users"),
            nullable=False,
        ),
        sa.Column("company", sa.String(200), nullable=False),
        sa.Column("company_key", sa.String(200), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("url", sa.String(2048), nullable=True),
        sa.Column("location", sa.String(200), nullable=True),
        sa.Column("source", sa.String(80), nullable=True),
        sa.Column("source_kind", sa.String(20), nullable=True),
        sa.Column("referred", sa.Boolean(), nullable=False),
        sa.Column("referrer", sa.String(200), nullable=True),
        sa.Column("level", sa.String(20), nullable=True),
        sa.Column("role_family", sa.String(20), nullable=True),
        sa.Column("years_required", sa.Integer(), nullable=True),
        sa.Column("salary_min", sa.Integer(), nullable=True),
        sa.Column("salary_max", sa.Integer(), nullable=True),
        sa.Column("work_mode", sa.String(20), nullable=True),
        sa.Column("employment_type", sa.String(20), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("corrected_fields", sa.JSON(), nullable=False),
        sa.Column("seen_on", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "url", name="uq_job_postings_user_url"),
    )
    op.create_index("ix_job_postings_user_id", "job_postings", ["user_id"])
    op.create_index("ix_job_postings_company_key", "job_postings", ["company_key"])
    op.create_table(
        "posting_technologies",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "posting_id",
            sa.Uuid(),
            sa.ForeignKey(
                "job_postings.id",
                ondelete="CASCADE",
                name="fk_posting_technologies_posting_id_job_postings",
            ),
            nullable=False,
        ),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("category", sa.String(20), nullable=False),
        sa.Column("requirement", sa.String(20), nullable=False),
        sa.UniqueConstraint("posting_id", "name", name="uq_posting_technologies_posting_name"),
    )
    op.create_index(
        "ix_posting_technologies_posting_id", "posting_technologies", ["posting_id"]
    )
    op.add_column("applications", sa.Column("posting_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_applications_posting_id_job_postings",
        "applications",
        "job_postings",
        ["posting_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_applications_posting_id", "applications", ["posting_id"])


def downgrade() -> None:
    op.drop_index("ix_applications_posting_id", table_name="applications")
    op.drop_constraint(
        "fk_applications_posting_id_job_postings", "applications", type_="foreignkey"
    )
    op.drop_column("applications", "posting_id")
    op.drop_table("posting_technologies")
    op.drop_table("job_postings")
