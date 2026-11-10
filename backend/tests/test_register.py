from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app

GOOD = {"email": "New.User@Example.com", "password": "a-long-enough-password"}


def test_register_signs_in_and_normalizes_email(session_factory):
    client = TestClient(app)
    response = client.post("/api/auth/register", json=GOOD)
    assert response.status_code == 201
    assert response.json()["email"] == "new.user@example.com"
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert client.get("/api/auth/me").json()["email"] == "new.user@example.com"
    assert "password" not in response.text


def test_registered_user_can_log_in_again(session_factory):
    TestClient(app).post("/api/auth/register", json=GOOD)
    response = TestClient(app).post(
        "/api/auth/login", json={"email": "new.user@example.com", "password": GOOD["password"]}
    )
    assert response.status_code == 200


def test_duplicate_email_is_rejected_case_insensitively(session_factory):
    client = TestClient(app)
    assert client.post("/api/auth/register", json=GOOD).status_code == 201
    duplicate = {**GOOD, "email": "new.user@EXAMPLE.COM"}
    assert TestClient(app).post("/api/auth/register", json=duplicate).status_code == 409


def test_rejects_short_password_and_bad_email(session_factory):
    client = TestClient(app)
    short = {"email": "a@example.com", "password": "short"}
    assert client.post("/api/auth/register", json=short).status_code == 422
    for email in ("nope", "@example.com", "a@acme", "a b@example.com"):
        payload = {"email": email, "password": GOOD["password"]}
        assert client.post("/api/auth/register", json=payload).status_code == 422


def test_new_user_cannot_see_existing_data(client):
    client.post("/api/applications", json={"company": "Acme", "role_title": "Engineer"})
    newcomer = TestClient(app)
    assert newcomer.post("/api/auth/register", json=GOOD).status_code == 201
    assert newcomer.get("/api/applications").json() == []


def test_registration_can_be_turned_off(session_factory, monkeypatch):
    closed = get_settings().model_copy(update={"registration_enabled": False})
    monkeypatch.setattr("app.routers.auth.get_settings", lambda: closed)
    client = TestClient(app)
    assert client.get("/api/auth/config").json() == {"registration_enabled": False}
    assert client.post("/api/auth/register", json=GOOD).status_code == 403
    assert client.get("/api/auth/me").status_code == 401
