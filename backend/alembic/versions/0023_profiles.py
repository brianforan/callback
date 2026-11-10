"""Profiles: one user, several job searches, each with its own resumes and fact sheet.

Every user gets a "Default" profile holding everything they have now. Resumes, fact sheets and
SmartSearch settings belong to a profile. Postings, applications, tailorings and SmartSearch
runs note the profile they were made under.

Revision ID: 0023
Revises: 0022
"""

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None

DEFAULT_NAME = "Default"

# Tables whose rows must belong to a profile.
_OWNED = ("resumes", "fact_sheets", "smartsearch_settings")
# Tables whose rows only note a profile, and keep the row when it's deleted.
_NOTED = ("job_postings", "applications", "tailor_jobs", "smartsearch_runs")
_INDEXED = ("resumes", *_NOTED)


def _fk(table: str) -> str:
    return f"fk_{table}_profile_id_profiles"


def upgrade() -> None:
    profiles = op.create_table(
        "profiles",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE", name="fk_profiles_user_id_users"),
            nullable=False,
        ),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "name", name="uq_profiles_user_name"),
    )
    op.create_index("ix_profiles_user_id", "profiles", ["user_id"])

    connection = op.get_bind()
    user_ids = connection.execute(sa.text("SELECT id FROM users")).scalars().all()
    now = datetime.now(UTC)
    if user_ids:
        op.bulk_insert(
            profiles,
            [
                {"id": uuid.uuid4(), "user_id": user_id, "name": DEFAULT_NAME, "created_at": now}
                for user_id in user_ids
            ],
        )

    for table in (*_OWNED, *_NOTED):
        ondelete = "CASCADE" if table in _OWNED else "SET NULL"
        op.add_column(
            table,
            sa.Column(
                "profile_id",
                sa.Uuid(),
                sa.ForeignKey("profiles.id", ondelete=ondelete, name=_fk(table)),
            ),
        )
        # Each user has exactly one profile at this point, so this picks it.
        op.execute(
            f"UPDATE {table} SET profile_id = "  # noqa: S608 - table names are constants
            f"(SELECT profiles.id FROM profiles WHERE profiles.user_id = {table}.user_id)"
        )

    for table in _OWNED:
        op.alter_column(table, "profile_id", existing_type=sa.Uuid(), nullable=False)
    for table in _INDEXED:
        op.create_index(f"ix_{table}_profile_id", table, ["profile_id"])

    op.drop_constraint("uq_resumes_user_content", "resumes", type_="unique")
    op.create_unique_constraint(
        "uq_resumes_profile_content", "resumes", ["profile_id", "content_sha256"]
    )
    op.drop_constraint("uq_fact_sheets_user_id", "fact_sheets", type_="unique")
    op.create_unique_constraint("uq_fact_sheets_profile_id", "fact_sheets", ["profile_id"])
    op.drop_constraint("uq_smartsearch_settings_user_id", "smartsearch_settings", type_="unique")
    op.create_unique_constraint(
        "uq_smartsearch_settings_profile_id", "smartsearch_settings", ["profile_id"]
    )


def downgrade() -> None:
    connection = op.get_bind()
    several = connection.execute(
        sa.text("SELECT user_id FROM profiles GROUP BY user_id HAVING COUNT(*) > 1 LIMIT 1")
    ).first()
    if several is not None:
        # Folding profiles back together would drop fact sheets and settings without asking.
        raise RuntimeError(
            "A user has more than one profile. Delete the extra profiles before downgrading."
        )

    op.drop_constraint("uq_smartsearch_settings_profile_id", "smartsearch_settings", type_="unique")
    op.create_unique_constraint(
        "uq_smartsearch_settings_user_id", "smartsearch_settings", ["user_id"]
    )
    op.drop_constraint("uq_fact_sheets_profile_id", "fact_sheets", type_="unique")
    op.create_unique_constraint("uq_fact_sheets_user_id", "fact_sheets", ["user_id"])
    op.drop_constraint("uq_resumes_profile_content", "resumes", type_="unique")
    op.create_unique_constraint("uq_resumes_user_content", "resumes", ["user_id", "content_sha256"])

    for table in _INDEXED:
        op.drop_index(f"ix_{table}_profile_id", table_name=table)
    for table in (*_OWNED, *_NOTED):
        op.drop_constraint(_fk(table), table, type_="foreignkey")
        op.drop_column(table, "profile_id")
    op.drop_index("ix_profiles_user_id", table_name="profiles")
    op.drop_table("profiles")
