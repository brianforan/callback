"""Scout's titles to search under become phrases each profile types, so the seven fixed title
families saved so far become the phrases they searched for (docs/skills_spec.md, chunk 9). Saved
settings and the settings each run kept both change.

Revision ID: 0045
Revises: 0044
"""

from collections.abc import Callable
from typing import Any

import sqlalchemy as sa

from alembic import op

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None

# Each old value and its phrase, as Scout searched for it. Kept here rather than imported, so the
# migration doesn't change if the app's copy (app.scout.limits.OLD_TITLE_PHRASES) does.
PHRASES = {
    "forward_deployed": "forward deployed",
    "solutions": "solutions engineer",
    "implementation": "implementation engineer",
    "internal_tools": "internal tools",
    "business_systems": "business systems",
    "automation": "automation engineer",
    "integration": "integration engineer",
}
_VALUES = {phrase: value for value, phrase in PHRASES.items()}

_settings = sa.table("smartsearch_settings", sa.column("id"), sa.column("title_families", sa.JSON))
_runs = sa.table("smartsearch_runs", sa.column("id"), sa.column("settings", sa.JSON))


def to_phrases(values: list[str]) -> list[str]:
    return [PHRASES.get(value, value) for value in values]


def to_values(phrases: list[str]) -> list[str]:
    """Back to the old values. A phrase that wasn't one of the seven has none, and is dropped."""
    values = (_VALUES.get(phrase.strip().lower()) for phrase in phrases)
    return list(dict.fromkeys(value for value in values if value))


def _convert(change: Callable[[list[str]], list[str]]) -> None:
    connection = op.get_bind()
    for row_id, families in connection.execute(
        sa.select(_settings.c.id, _settings.c.title_families)
    ).all():
        changed = change(families or [])
        if changed != families:
            connection.execute(
                _settings.update().where(_settings.c.id == row_id).values(title_families=changed)
            )
    for row_id, settings in connection.execute(sa.select(_runs.c.id, _runs.c.settings)).all():
        kept: Any = (settings or {}).get("title_families")
        if not isinstance(kept, list):
            continue
        changed = change(kept)
        if changed != kept:
            connection.execute(
                _runs.update()
                .where(_runs.c.id == row_id)
                .values(settings={**settings, "title_families": changed})
            )


def upgrade() -> None:
    _convert(to_phrases)


def downgrade() -> None:
    _convert(to_values)
