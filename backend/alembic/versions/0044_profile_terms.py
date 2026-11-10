"""Each profile gets its own skill terms and a switch for the built-in software list, so
matching works outside software (docs/skills_spec.md, chunk 1). A user's vocabulary version and
each posting's skills version say when a posting's skills need reading again.

Revision ID: 0044
Revises: 0043
"""

import sqlalchemy as sa

from alembic import op

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "profiles",
        sa.Column("software_skills", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    # Every profile so far was scored with the software list, so it keeps it.
    op.execute(sa.text("UPDATE profiles SET software_skills = true"))
    op.create_table(
        "profile_terms",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "profile_id",
            sa.Uuid(),
            sa.ForeignKey(
                "profiles.id", name="fk_profile_terms_profile_id_profiles", ondelete="CASCADE"
            ),
            nullable=False,
        ),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("aliases", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "uq_profile_terms_profile_name",
        "profile_terms",
        ["profile_id", sa.text("lower(name)")],
        unique=True,
    )
    op.add_column(
        "users",
        sa.Column("vocabulary_version", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "job_postings",
        sa.Column("skills_version", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("job_postings", "skills_version")
    op.drop_column("users", "vocabulary_version")
    op.drop_index("uq_profile_terms_profile_name", table_name="profile_terms")
    op.drop_table("profile_terms")
    op.drop_column("profiles", "software_skills")
