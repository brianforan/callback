"""Settings that tune API spend move from the environment to an app_config table, and a user
can be let past the daily caps.

Revision ID: 0042
Revises: 0041
"""

import sqlalchemy as sa

from alembic import op

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # No rows: a setting without one uses its default, which matches the old compose default.
    op.create_table(
        "app_config",
        sa.Column("key", sa.String(80), primary_key=True),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "updated_by",
            sa.Uuid(),
            sa.ForeignKey("users.id", name="fk_app_config_updated_by_users", ondelete="SET NULL"),
        ),
    )
    op.add_column(
        "users",
        sa.Column("override_limits", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("users", "override_limits")
    op.drop_table("app_config")
