"""Terms a user has chosen to leave out of resume keyword analysis.

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ignored_terms",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE", name="fk_ignored_terms_user_id_users"),
            nullable=False,
        ),
        sa.Column("term", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "term", name="uq_ignored_terms_user_term"),
    )
    op.create_index("ix_ignored_terms_user_id", "ignored_terms", ["user_id"])


def downgrade() -> None:
    op.drop_table("ignored_terms")
