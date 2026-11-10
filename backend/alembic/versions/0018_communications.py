"""Communications on applications: emails, calls, interviews, messages and notes, with next steps.

Revision ID: 0018
Revises: 0017
"""

import sqlalchemy as sa

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "communications",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "application_id",
            sa.Uuid(),
            sa.ForeignKey(
                "applications.id",
                ondelete="CASCADE",
                name="fk_communications_application_id_applications",
            ),
            nullable=False,
        ),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("direction", sa.String(10), nullable=False),
        sa.Column("occurred_on", sa.Date(), nullable=False),
        sa.Column("contact", sa.String(200), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("next_step", sa.String(300), nullable=False),
        sa.Column("next_step_due", sa.Date()),
        sa.Column("next_step_done", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('email', 'call', 'interview', 'message', 'note')",
            name="ck_communications_kind",
        ),
        sa.CheckConstraint(
            "direction IN ('inbound', 'outbound', 'none')", name="ck_communications_direction"
        ),
    )
    op.create_index("ix_communications_application_id", "communications", ["application_id"])


def downgrade() -> None:
    op.drop_index("ix_communications_application_id", table_name="communications")
    op.drop_table("communications")
