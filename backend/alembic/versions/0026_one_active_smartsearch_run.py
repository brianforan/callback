"""One SmartSearch run waiting or running per account, enforced by a partial unique index.

start_run checks for an active run before adding one, but two requests at once could both pass
that check. The index makes the second insert fail instead. It is created without any change to
existing rows: if an account already has two active runs, the upgrade fails and names the index,
and the extra run has to be ended first.

Revision ID: 0026
Revises: 0025
"""

import sqlalchemy as sa

from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None

_INDEX = "uq_smartsearch_runs_one_active"
_ACTIVE = "status IN ('queued', 'running')"


def upgrade() -> None:
    op.create_index(
        _INDEX,
        "smartsearch_runs",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text(_ACTIVE),
    )


def downgrade() -> None:
    op.drop_index(_INDEX, table_name="smartsearch_runs")
