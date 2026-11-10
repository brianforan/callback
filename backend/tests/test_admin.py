"""The Admin panel's Users tab: roles and plans, and who may change them."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.cli import set_role
from app.config import get_settings
from app.main import app
from app.models import Role, User


def _user_id(session_factory, email):
    with session_factory() as db:
        return db.scalar(select(User.id).where(User.email == email))


def _set_role(session_factory, email, role):
    with session_factory() as db:
        user = db.scalar(select(User).where(User.email == email))
        user.role = role
        db.commit()


@pytest.fixture
def admin(client, session_factory):
    """The first test user, owner@example.com, holds the stored admin role."""
    _set_role(session_factory, "owner@example.com", Role.ADMIN)
    return client


def test_new_users_are_free_members(client):
    me = client.get("/api/auth/me").json()
    assert (me["role"], me["plan"], me["is_admin"]) == ("member", "free", False)


def test_admin_routes_are_hidden_from_members(client, other_client):
    assert other_client.get("/api/admin/users").status_code == 404
    other_id = other_client.get("/api/auth/me").json()["id"]
    assert (
        other_client.patch(f"/api/admin/users/{other_id}", json={"plan": "premium"}).status_code
        == 404
    )
    assert client.get("/api/admin/spend").status_code == 404


def test_no_setting_makes_anyone_an_admin(session_factory, monkeypatch):
    """OWNER_EMAIL used to make whoever registered with that address an admin, unverified."""
    monkeypatch.setenv("OWNER_EMAIL", "boss@example.com")
    get_settings.cache_clear()
    try:
        visitor = TestClient(app)
        registered = visitor.post(
            "/api/auth/register",
            json={"email": "boss@example.com", "password": "correct-horse-battery"},
        )
        assert registered.status_code == 201, registered.text
        me = visitor.get("/api/auth/me").json()
        assert me["is_admin"] is False
        assert "is_owner" not in me
        assert visitor.get("/api/admin/users").status_code == 404
    finally:
        monkeypatch.delenv("OWNER_EMAIL")
        get_settings.cache_clear()


def test_admins_list_users_and_set_role_and_plan(admin, other_client, session_factory):
    users = admin.get("/api/admin/users").json()
    assert [user["email"] for user in users] == ["owner@example.com", "other@example.com"]
    assert "password_hash" not in users[0]

    other_id = _user_id(session_factory, "other@example.com")
    changed = admin.patch(f"/api/admin/users/{other_id}", json={"role": "admin", "plan": "premium"})
    assert changed.status_code == 200
    assert (changed.json()["role"], changed.json()["plan"]) == ("admin", "premium")
    # The new admin sees the panel straight away.
    assert other_client.get("/api/auth/me").json()["is_admin"] is True
    assert other_client.get("/api/admin/users").status_code == 200


def test_admins_cannot_change_their_own_role(other_client, session_factory):
    _set_role(session_factory, "other@example.com", Role.ADMIN)
    other_id = _user_id(session_factory, "other@example.com")
    response = other_client.patch(f"/api/admin/users/{other_id}", json={"role": "member"})
    assert response.status_code == 409
    # Their own plan is fine.
    response = other_client.patch(f"/api/admin/users/{other_id}", json={"plan": "premium"})
    assert response.status_code == 200


def test_updates_need_a_known_user_and_a_change(admin):
    missing = "00000000-0000-0000-0000-000000000000"
    assert admin.patch(f"/api/admin/users/{missing}", json={"plan": "free"}).status_code == 404
    admin_id = admin.get("/api/auth/me").json()["id"]
    assert admin.patch(f"/api/admin/users/{admin_id}", json={}).status_code == 422
    assert admin.patch(f"/api/admin/users/{admin_id}", json={"role": "boss"}).status_code == 422


def test_admins_can_promote_but_only_the_cli_demotes(
    admin, other_client, session_factory, monkeypatch
):
    third_id = _add_third_user(session_factory)
    _set_role(session_factory, "other@example.com", Role.ADMIN)
    promoted = other_client.patch(f"/api/admin/users/{third_id}", json={"role": "admin"})
    assert promoted.status_code == 200
    # No admin can take it back in the panel, so one admin can't remove the rest.
    for someone in (admin, other_client):
        demoted = someone.patch(f"/api/admin/users/{third_id}", json={"role": "member"})
        assert demoted.status_code == 403
        assert "set-role" in demoted.json()["detail"]
    # Admins can still change an admin's plan.
    plan = other_client.patch(f"/api/admin/users/{third_id}", json={"plan": "premium"})
    assert plan.status_code == 200
    # Someone with shell access on the server can.
    monkeypatch.setattr("app.cli.SessionLocal", session_factory)
    assert set_role("third@example.com", Role.MEMBER.value) == 0
    with session_factory() as db:
        assert db.get(User, third_id).role == Role.MEMBER.value


def _add_third_user(session_factory):
    with session_factory() as db:
        user = User(email="third@example.com", password_hash="unused")
        db.add(user)
        db.commit()
        return user.id
