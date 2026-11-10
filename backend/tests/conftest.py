import os

os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"
os.environ["SECRET_KEY"] = "test-secret-key-that-is-long-enough-for-hs256"
os.environ["COOKIE_SECURE"] = "false"
os.environ["REGISTRATION_ENABLED"] = "true"


import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.db import get_db
from app.listings.cache import KEEP_RESULTS, SqlListingStore
from app.main import app
from app.models import AppConfig, Base, User
from app.routers.listings import get_catalog_recorder, get_listing_store
from app.security import hash_password

PASSWORD = "correct-horse-battery"


@pytest.fixture
def session_factory(tmp_path):
    # A file, not :memory:, so each thread gets its own connection from the pool, as with
    # Postgres. Job listings fetch boards on background threads and save what they find while
    # the request reads, and one connection shared by all of them failed now and then.
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'callback.db'}",
        # Seconds a connection waits for another's write to finish before giving up.
        connect_args={"check_same_thread": False, "timeout": 30},
    )

    @event.listens_for(engine, "connect")
    def _configure(connection, _record):
        # SQLite ignores foreign keys unless asked, and the app relies on Postgres's ON DELETE
        # rules, so tests check them too.
        connection.execute("PRAGMA foreign_keys=ON")
        # Readers don't wait for a writer, as in Postgres.
        connection.execute("PRAGMA journal_mode=WAL")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def override_get_db():
        with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_listing_store] = lambda: SqlListingStore(factory, KEEP_RESULTS)
    # Tests that check the catalog record into it themselves.
    app.dependency_overrides[get_catalog_recorder] = lambda: None
    yield factory
    app.dependency_overrides.clear()
    engine.dispose()


@pytest.fixture
def queries(session_factory):
    """The SELECT statements run while a test watches, to check what a request loads."""
    statements: list[str] = []
    engine = session_factory.kw["bind"]

    def record(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    yield statements
    event.remove(engine, "before_cursor_execute", record)


@pytest.fixture
def set_config(session_factory):
    """Save an admin's value for one app_config setting, as Admin -> Settings would."""

    def save(key: str, value: float) -> None:
        with session_factory() as session:
            session.merge(AppConfig(key=key, value=value))
            session.commit()

    return save


def _add_user(factory, email: str) -> None:
    with factory() as session:
        session.add(User(email=email, password_hash=hash_password(PASSWORD)))
        session.commit()


def _logged_in_client(email: str) -> TestClient:
    test_client = TestClient(app)
    response = test_client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200
    return test_client


@pytest.fixture
def client(session_factory) -> TestClient:
    _add_user(session_factory, "owner@example.com")
    return _logged_in_client("owner@example.com")


@pytest.fixture
def other_client(session_factory) -> TestClient:
    _add_user(session_factory, "other@example.com")
    return _logged_in_client("other@example.com")
