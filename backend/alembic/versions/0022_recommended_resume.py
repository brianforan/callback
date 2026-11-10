"""The resume the matcher recommended for each application, kept beside the one sent.

Revision ID: 0022
Revises: 0021
"""

import sqlalchemy as sa

from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None

_FK = "fk_applications_recommended_resume_id_resumes"


def upgrade() -> None:
    op.add_column(
        "applications",
        sa.Column(
            "recommended_resume_id",
            sa.Uuid(),
            sa.ForeignKey("resumes.id", ondelete="SET NULL", name=_FK),
        ),
    )


def downgrade() -> None:
    op.drop_constraint(_FK, "applications", type_="foreignkey")
    op.drop_column("applications", "recommended_resume_id")
