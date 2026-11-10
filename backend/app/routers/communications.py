import logging
import uuid
from datetime import date

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.deps import CurrentUser, Db
from app.models import Application, Communication, Direction, User
from app.schemas import CommunicationIn, CommunicationOut, CommunicationUpdate, NextStepOut

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["communications"])

_MAX_NEXT_STEPS = 50


def _application(db: Session, user: User, application_id: uuid.UUID) -> Application:
    application = db.scalar(
        select(Application).where(Application.id == application_id, Application.user_id == user.id)
    )
    if application is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Application not found")
    return application


def _communication(db: Session, user: User, communication_id: uuid.UUID) -> Communication:
    communication = db.scalar(
        select(Communication)
        .join(Application)
        .where(Communication.id == communication_id, Application.user_id == user.id)
    )
    if communication is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Communication not found")
    return communication


@router.get("/applications/{application_id}/communications", response_model=list[CommunicationOut])
def list_communications(
    application_id: uuid.UUID, user: CurrentUser, db: Db
) -> list[Communication]:
    _application(db, user, application_id)
    return list(
        db.scalars(
            select(Communication)
            .where(Communication.application_id == application_id)
            .order_by(Communication.occurred_on.desc(), Communication.created_at.desc())
        ).all()
    )


@router.post(
    "/applications/{application_id}/communications",
    response_model=CommunicationOut,
    status_code=status.HTTP_201_CREATED,
)
def add_communication(
    application_id: uuid.UUID, payload: CommunicationIn, user: CurrentUser, db: Db
) -> Communication:
    _application(db, user, application_id)
    communication = Communication(application_id=application_id, **payload.model_dump())
    db.add(communication)
    db.commit()
    logger.info("Logged a %s on application %s", payload.kind.value, application_id)
    return communication


@router.patch("/communications/{communication_id}", response_model=CommunicationOut)
def update_communication(
    communication_id: uuid.UUID, payload: CommunicationUpdate, user: CurrentUser, db: Db
) -> Communication:
    communication = _communication(db, user, communication_id)
    changes = payload.model_dump(exclude_unset=True)
    for name in ("contact", "summary", "next_step"):
        if isinstance(changes.get(name), str):
            changes[name] = changes[name].strip()
    for name, value in changes.items():
        if value is None and name not in ("next_step_due",):
            continue
        setattr(communication, name, value)
    if communication.next_step_due and not communication.next_step:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "A due date needs a next step")
    if communication.kind == "note":
        communication.direction = Direction.NONE.value
    db.commit()
    return communication


@router.delete("/communications/{communication_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_communication(communication_id: uuid.UUID, user: CurrentUser, db: Db) -> None:
    db.delete(_communication(db, user, communication_id))
    db.commit()


@router.get("/communications/next-steps", response_model=list[NextStepOut])
def next_steps(user: CurrentUser, db: Db) -> list[NextStepOut]:
    """Open next steps across applications: overdue and soonest first, undated last."""
    today = date.today()
    rows = db.scalars(
        select(Communication)
        .join(Application)
        .where(
            Application.user_id == user.id,
            Communication.next_step != "",
            Communication.next_step_done.is_(False),
        )
        .options(joinedload(Communication.application))
    ).all()
    rows = sorted(
        rows,
        key=lambda row: (row.next_step_due is None, row.next_step_due or today, row.created_at),
    )
    return [
        NextStepOut(
            communication_id=row.id,
            application_id=row.application_id,
            company=row.application.company,
            role_title=row.application.role_title,
            next_step=row.next_step,
            next_step_due=row.next_step_due,
            overdue=row.next_step_due is not None and row.next_step_due < today,
        )
        for row in rows[:_MAX_NEXT_STEPS]
    ]
