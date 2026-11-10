"""Add a status for a company that has paused hiring.

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

OLD_STATUS_CHECK = (
    "status IN ('applied', 'screening', 'interview', 'offer', 'rejected', 'ghosted', 'withdrawn')"
)
NEW_STATUS_CHECK = (
    "status IN ('applied', 'screening', 'interview', 'offer', 'rejected', 'ghosted', "
    "'hiring_paused', 'withdrawn')"
)
TABLE_CONSTRAINTS = (
    ("applications", "ck_applications_status"),
    ("status_events", "ck_status_events_status"),
)


def _replace_check(check: str) -> None:
    for table, constraint in TABLE_CONSTRAINTS:
        op.drop_constraint(constraint, table, type_="check")
        op.create_check_constraint(constraint, table, check)


def upgrade() -> None:
    _replace_check(NEW_STATUS_CHECK)


def downgrade() -> None:
    # The old constraint has no paused status, so those rows become the closest outcome it has.
    for table, _ in TABLE_CONSTRAINTS:
        op.execute(
            sa.text(f"UPDATE {table} SET status = 'rejected' WHERE status = 'hiring_paused'")
        )
    _replace_check(OLD_STATUS_CHECK)
