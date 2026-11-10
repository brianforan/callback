"""SmartSearch's strict matching: a brief's Must use, Rule out and levels; what the judge read
from a posting, stored on the shared catalog job; and more of each verdict.

docs/smartsearch_spec.md. must_have and rule_out are nullable on purpose: None means never saved,
and the description's technologies stand in for them.

Revision ID: 0037
Revises: 0036
"""

import sqlalchemy as sa

from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("smartsearch_settings", sa.Column("must_have", sa.JSON(), nullable=True))
    op.add_column("smartsearch_settings", sa.Column("rule_out", sa.JSON(), nullable=True))
    op.add_column(
        "smartsearch_settings",
        sa.Column("levels", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column("catalog_jobs", sa.Column("level", sa.String(20), nullable=True))
    op.add_column("catalog_jobs", sa.Column("us_hireable", sa.String(10), nullable=True))
    op.add_column("catalog_jobs", sa.Column("facts_key", sa.String(64), nullable=True))
    op.add_column("job_judgments", sa.Column("stack_ok", sa.Boolean(), nullable=True))
    op.add_column("job_judgments", sa.Column("posting_level", sa.String(20), nullable=True))
    op.add_column(
        "job_judgments",
        sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("job_judgments", "verified")
    op.drop_column("job_judgments", "posting_level")
    op.drop_column("job_judgments", "stack_ok")
    op.drop_column("catalog_jobs", "facts_key")
    op.drop_column("catalog_jobs", "us_hireable")
    op.drop_column("catalog_jobs", "level")
    op.drop_column("smartsearch_settings", "levels")
    op.drop_column("smartsearch_settings", "rule_out")
    op.drop_column("smartsearch_settings", "must_have")
