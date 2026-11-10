"""The company catalog for the Companies engine: companies, their aliases, and checked pairs.

Shared by every user, and built only from public listings. No existing rows change.

Revision ID: 0029
Revises: 0028
"""

import sqlalchemy as sa

from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "companies",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("key", sa.String(200), nullable=False),
        sa.Column("compact_key", sa.String(200), nullable=False),
        sa.Column("domain", sa.String(255), nullable=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("domain", name="uq_companies_domain"),
    )
    op.create_index("ix_companies_key", "companies", ["key"])
    op.create_index("ix_companies_compact_key", "companies", ["compact_key"])
    op.create_table(
        "company_aliases",
        sa.Column("alias_key", sa.String(200), primary_key=True),
        sa.Column(
            "company_id",
            sa.Uuid(),
            sa.ForeignKey(
                "companies.id", name="fk_company_aliases_company_id_companies", ondelete="CASCADE"
            ),
            nullable=False,
        ),
        sa.Column("decided_by", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_company_aliases_company_id", "company_aliases", ["company_id"])
    op.create_table(
        "company_match_checks",
        sa.Column("key_a", sa.String(200), primary_key=True),
        sa.Column("key_b", sa.String(200), primary_key=True),
        sa.Column("same", sa.Boolean(), nullable=True),
        sa.Column("decided_by", sa.String(20), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("company_match_checks")
    op.drop_index("ix_company_aliases_company_id", table_name="company_aliases")
    op.drop_table("company_aliases")
    op.drop_index("ix_companies_compact_key", table_name="companies")
    op.drop_index("ix_companies_key", table_name="companies")
    op.drop_table("companies")
