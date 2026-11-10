"""A plan on each user, and SmartSearch settings for premium users.

Revision ID: 0011
Revises: 0010
"""

import sqlalchemy as sa

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("plan", sa.String(20), nullable=False, server_default="free"),
    )
    op.create_table(
        "smartsearch_settings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey(
                "users.id", ondelete="CASCADE", name="fk_smartsearch_settings_user_id_users"
            ),
            nullable=False,
        ),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("title_families", sa.JSON(), nullable=False),
        sa.Column("work_modes", sa.JSON(), nullable=False),
        sa.Column("posted_within_days", sa.Integer()),
        sa.Column("research_depth", sa.String(20), nullable=False),
        sa.Column("limits", sa.JSON(), nullable=False),
        sa.Column("credit_cap", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", name="uq_smartsearch_settings_user_id"),
    )


def downgrade() -> None:
    op.drop_table("smartsearch_settings")
    op.drop_column("users", "plan")
