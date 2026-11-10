"""Scout's work boundary: the work a profile does, read by one model call from its own
baselines, fact sheet and description, so a run with little to go on still searches inside it
(docs/skills_spec.md, Scout's work boundary). The engine makes the call at the start of a run
(app.scout.engine); this module says what goes in and what's kept.
"""

import hashlib
import json
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import FactSheet, Resume, utcnow
from app.scout.limits import MAX_WORK_LENGTH, MAX_WORK_TITLES, usable_title_phrases

# The baselines a run reads, newest first, and how much of each.
MAX_BASELINES = 10
MAX_BASELINE_TEXT = 20_000


def scout_baselines(db: Session, profile_id: uuid.UUID | None) -> list[Resume]:
    return list(
        db.scalars(
            select(Resume)
            .where(Resume.profile_id == profile_id, Resume.is_variant.is_(True))
            .order_by(Resume.created_at.desc())
            .limit(MAX_BASELINES)
        ).all()
    )


def inputs_key(description: str, fact_sheet: str, baseline_texts: list[str]) -> str:
    """A fingerprint of what the boundary was read from. A new one means it's read again."""
    material = [description.strip(), fact_sheet.strip(), *baseline_texts]
    return hashlib.sha256(json.dumps(material).encode()).hexdigest()


def payload_inputs_key(payload: dict[str, Any]) -> str:
    """The fingerprint of a run's material, as the worker gathered it."""
    return inputs_key(
        payload.get("description") or "",
        payload.get("fact_sheet") or "",
        [baseline["text"] for baseline in payload.get("baselines") or []],
    )


def profile_inputs_key(db: Session, profile_id: uuid.UUID, description: str) -> str:
    """The fingerprint a run would read now, for the profile's saved description."""
    sheet = db.scalar(select(FactSheet.content).where(FactSheet.profile_id == profile_id))
    texts = [resume.plain_text[:MAX_BASELINE_TEXT] for resume in scout_baselines(db, profile_id)]
    return inputs_key(description, sheet or "", texts)


def boundary_from_answer(answer: dict[str, Any], key: str) -> dict[str, Any] | None:
    """What's stored from tailor's /pipeline/work answer. None when it found no work: no line
    or no usable title."""
    work = " ".join(str(answer.get("work") or "").split())[:MAX_WORK_LENGTH]
    titles = usable_title_phrases(list(answer.get("titles") or []))[:MAX_WORK_TITLES]
    if not work or not titles:
        return None
    return {
        "work": work,
        "titles": titles,
        "inputs_key": key,
        "made_at": utcnow().isoformat(),
        "edited": False,
    }
