import logging
from collections.abc import Callable, Iterator

from psycopg.errors import DeadlockDetected
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

logger = logging.getLogger(__name__)

engine = create_engine(get_settings().database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session


def is_deadlock(exc: SQLAlchemyError) -> bool:
    """Postgres cancelled this transaction to break a deadlock with another one."""
    return isinstance(getattr(exc, "orig", None), DeadlockDetected)


def commit_retrying_deadlock[T](db: Session, work: Callable[[], T]) -> T:
    """Run work and commit it. If Postgres cancels it to break a deadlock, roll back and run it
    once more, as Postgres advises: the other transaction has usually finished by then. Any
    other error, or a second deadlock, is raised for the caller to handle."""
    try:
        result = work()
        db.commit()
        return result
    except SQLAlchemyError as exc:
        if not is_deadlock(exc):
            raise
        db.rollback()
        logger.info("A transaction was cancelled to break a deadlock; running it once more")
    result = work()
    db.commit()
    return result
