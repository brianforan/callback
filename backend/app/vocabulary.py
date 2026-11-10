"""A user's skill vocabulary: the built-in software list when any of their profiles uses it, and
every profile's own terms (docs/skills_spec.md). Postings belong to the user, so they're read with
all of it."""

import uuid

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import Profile, ProfileTerm, User
from app.postings.parser import Vocabulary, build_vocabulary


def user_vocabulary(db: Session, user_id: uuid.UUID) -> Vocabulary:
    version = db.scalar(select(User.vocabulary_version).where(User.id == user_id)) or 0
    switches = db.scalars(select(Profile.software_skills).where(Profile.user_id == user_id)).all()
    # A user with no profile yet gets the first one when it's needed, with the software list on.
    software = any(switches) if switches else True
    terms = db.execute(
        select(ProfileTerm.name, ProfileTerm.aliases)
        .join(Profile, ProfileTerm.profile_id == Profile.id)
        .where(Profile.user_id == user_id)
        .order_by(ProfileTerm.created_at, ProfileTerm.id)
    ).all()
    return build_vocabulary(software, [(name, aliases or []) for name, aliases in terms], version)


def bump_vocabulary(db: Session, user_id: uuid.UUID) -> None:
    """Mark the user's postings as read with an old vocabulary, so the worker reads them again."""
    db.execute(
        update(User)
        .where(User.id == user_id)
        .values(vocabulary_version=User.vocabulary_version + 1)
    )
