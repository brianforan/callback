"""Each profile's list of companies for the Companies engine, and a flag for sources a run is
waiting on.

Revision ID: 0031
Revises: 0030
"""

import sqlalchemy as sa

from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "company_sources", sa.Column("wanted_by", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_table(
        "profile_companies",
        sa.Column(
            "profile_id",
            sa.Uuid(),
            sa.ForeignKey(
                "profiles.id", name="fk_profile_companies_profile_id_profiles", ondelete="CASCADE"
            ),
            primary_key=True,
        ),
        sa.Column(
            "company_id",
            sa.Uuid(),
            sa.ForeignKey(
                "companies.id",
                name="fk_profile_companies_company_id_companies",
                ondelete="CASCADE",
            ),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey(
                "users.id", name="fk_profile_companies_user_id_users", ondelete="CASCADE"
            ),
            nullable=False,
        ),
        sa.Column("origin", sa.String(20), nullable=False),
        sa.Column("leads", sa.Integer(), nullable=False),
        sa.Column("dismissed", sa.Integer(), nullable=False),
        sa.Column("last_lead_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_profile_companies_company_id", "profile_companies", ["company_id"])
    op.create_index("ix_profile_companies_user_id", "profile_companies", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_profile_companies_user_id", table_name="profile_companies")
    op.drop_index("ix_profile_companies_company_id", table_name="profile_companies")
    op.drop_table("profile_companies")
    op.drop_column("company_sources", "wanted_by")
