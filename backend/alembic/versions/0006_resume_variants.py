"""Resume variants: baselines kept for one kind of job, with rules for which jobs they suit.

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "resumes",
        sa.Column("is_variant", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "resumes",
        sa.Column("variant_rules", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )


def downgrade() -> None:
    op.drop_column("resumes", "variant_rules")
    op.drop_column("resumes", "is_variant")
