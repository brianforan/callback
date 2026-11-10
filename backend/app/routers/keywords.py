from typing import Annotated

from fastapi import APIRouter, Query, status
from sqlalchemy import delete, select

from app.deps import CurrentUser, Db
from app.models import IgnoredTerm
from app.schemas import IgnoreTermRequest

router = APIRouter(prefix="/api/keywords", tags=["keywords"])


@router.post("/ignored", status_code=status.HTTP_204_NO_CONTENT)
def ignore_term(payload: IgnoreTermRequest, user: CurrentUser, db: Db) -> None:
    existing = db.scalar(
        select(IgnoredTerm).where(IgnoredTerm.user_id == user.id, IgnoredTerm.term == payload.term)
    )
    if existing is None:
        db.add(IgnoredTerm(user_id=user.id, term=payload.term))
        db.commit()


# The term is a query parameter because terms such as c# and ci/cd don't fit in a URL path.
@router.delete("/ignored", status_code=status.HTTP_204_NO_CONTENT)
def restore_term(term: Annotated[str, Query(max_length=120)], user: CurrentUser, db: Db) -> None:
    db.execute(
        delete(IgnoredTerm).where(
            IgnoredTerm.user_id == user.id, IgnoredTerm.term == term.strip().lower()
        )
    )
    db.commit()
