"""User roles: "member" or "admin", beside each user's plan.

Everyone starts as a member. The user named by OWNER_EMAIL, when it's set in the environment the
migration runs in, is stored as an admin too, so the role survives OWNER_EMAIL being cleared.

Revision ID: 0024
Revises: 0023
"""

import logging
import os

import sqlalchemy as sa

from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("role", sa.String(20), nullable=False, server_default="member"),
    )
    owner = os.environ.get("OWNER_EMAIL", "").strip().lower()
    if not owner:
        logger.info("OWNER_EMAIL is empty, so no user was made an admin")
        return
    result = op.get_bind().execute(
        sa.text("UPDATE users SET role = 'admin' WHERE lower(email) = :owner"), {"owner": owner}
    )
    logger.info("Made %d user(s) matching OWNER_EMAIL an admin", result.rowcount)


def downgrade() -> None:
    op.drop_column("users", "role")
