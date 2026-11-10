"""Jobs and companies a user has hidden from job listing results.

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hidden_listings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey(
                "users.id", ondelete="CASCADE", name="fk_hidden_listings_user_id_users"
            ),
            nullable=False,
        ),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("key", sa.String(2048), nullable=False),
        sa.Column("company", sa.String(200), nullable=False),
        sa.Column("company_key", sa.String(200), nullable=False),
        sa.Column("title", sa.String(200), nullable=True),
        sa.Column("url", sa.String(2048), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "kind", "key", name="uq_hidden_listings_user_kind_key"),
        sa.CheckConstraint("kind IN ('listing', 'company')", name="ck_hidden_listings_kind"),
    )
    op.create_index("ix_hidden_listings_user_id", "hidden_listings", ["user_id"])


def downgrade() -> None:
    op.drop_table("hidden_listings")
