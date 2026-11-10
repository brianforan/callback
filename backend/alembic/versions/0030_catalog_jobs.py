"""The Companies catalog's sources and jobs: where each company lists jobs, one record per job
with every place it's listed, and how useful each source has been.

Shared by every user, and built only from public listings. No existing rows change.

Revision ID: 0030
Revises: 0029
"""

import sqlalchemy as sa

from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "company_sources",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "company_id",
            sa.Uuid(),
            sa.ForeignKey(
                "companies.id", name="fk_company_sources_company_id_companies", ondelete="CASCADE"
            ),
            nullable=False,
        ),
        sa.Column("source", sa.String(40), nullable=False),
        sa.Column("identifier", sa.String(200), nullable=False),
        sa.Column("is_own", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("open_jobs", sa.Integer(), nullable=False),
        sa.Column("failures", sa.Integer(), nullable=False),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_checked", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("source", "identifier", name="uq_company_sources_source_identifier"),
    )
    op.create_index("ix_company_sources_company_id", "company_sources", ["company_id"])
    op.create_table(
        "catalog_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "company_id",
            sa.Uuid(),
            sa.ForeignKey(
                "companies.id", name="fk_catalog_jobs_company_id_companies", ondelete="CASCADE"
            ),
            nullable=False,
        ),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("title_key", sa.String(200), nullable=False),
        sa.Column("location", sa.String(300), nullable=True),
        sa.Column("work_mode", sa.String(20), nullable=True),
        sa.Column("posted_on", sa.Date(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("fingerprint", sa.BigInteger(), nullable=True),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_catalog_jobs_company_id_title_key", "catalog_jobs", ["company_id", "title_key"]
    )
    op.create_table(
        "catalog_job_sources",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "job_id",
            sa.Uuid(),
            sa.ForeignKey(
                "catalog_jobs.id",
                name="fk_catalog_job_sources_job_id_catalog_jobs",
                ondelete="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column("source", sa.String(40), nullable=False),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("external_id", sa.String(100), nullable=True),
        sa.Column(
            "agency_id",
            sa.Uuid(),
            sa.ForeignKey(
                "companies.id",
                name="fk_catalog_job_sources_agency_id_companies",
                ondelete="SET NULL",
            ),
            nullable=True,
        ),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("url", name="uq_catalog_job_sources_url"),
    )
    op.create_index("ix_catalog_job_sources_job_id", "catalog_job_sources", ["job_id"])
    op.create_index("ix_catalog_job_sources_agency_id", "catalog_job_sources", ["agency_id"])
    op.create_index(
        "ix_catalog_job_sources_source_external_id",
        "catalog_job_sources",
        ["source", "external_id"],
    )
    op.create_table(
        "board_stats",
        sa.Column("board", sa.String(40), primary_key=True),
        sa.Column("listings_recorded", sa.Integer(), nullable=False),
        sa.Column("kept", sa.Integer(), nullable=False),
        sa.Column("last_kept_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("board_stats")
    op.drop_index("ix_catalog_job_sources_source_external_id", table_name="catalog_job_sources")
    op.drop_index("ix_catalog_job_sources_agency_id", table_name="catalog_job_sources")
    op.drop_index("ix_catalog_job_sources_job_id", table_name="catalog_job_sources")
    op.drop_table("catalog_job_sources")
    op.drop_index("ix_catalog_jobs_company_id_title_key", table_name="catalog_jobs")
    op.drop_table("catalog_jobs")
    op.drop_index("ix_company_sources_company_id", table_name="company_sources")
    op.drop_table("company_sources")
