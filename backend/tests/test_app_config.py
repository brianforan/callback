"""Admin -> Settings: the settings that tune API spend, kept in app_config, and the per-user
override of the daily caps."""

import pytest
from sqlalchemy import select

from app.app_config import SPECS, SPECS_BY_KEY, daily_limit, get_value
from app.models import AppConfig, Role, User


@pytest.fixture
def admin(client, session_factory):
    with session_factory() as db:
        db.scalar(select(User).where(User.email == "owner@example.com")).role = Role.ADMIN
        db.commit()
    return client


def _setting(items, key):
    return next(item for item in items if item["key"] == key)


def test_every_setting_starts_at_its_default(admin):
    response = admin.get("/api/admin/config")
    assert response.status_code == 200, response.text
    items = response.json()
    assert [item["key"] for item in items] == [spec.key for spec in SPECS]
    tailor = _setting(items, "tailor_daily_limit")
    assert (tailor["value"], tailor["default"], tailor["saved"]) == (5, 5, False)


def test_defaults_match_what_compose_passed_before_the_move():
    """A server whose .env holds only secrets ran on these, so moving them changed nothing.
    The Scout ones are new."""
    assert {key: SPECS_BY_KEY[key].default for key in SPECS_BY_KEY} == {
        "smartsearch_daily_runs": 2,
        "smartsearch_daily_briefs": 10,
        "smartsearch_run_max_usd": 2.0,
        "tailor_daily_limit": 5,
        "reject_analysis_daily_limit": 20,
        "company_research_daily_limit": 10,
        "company_research_max_age_days": 30,
    }


def test_admin_changes_and_resets_a_setting(admin, session_factory):
    changed = admin.put("/api/admin/config/tailor_daily_limit", json={"value": 8})
    assert changed.status_code == 200, changed.text
    item = _setting(changed.json(), "tailor_daily_limit")
    assert (item["value"], item["saved"], item["updated_by"]) == (8, True, "owner@example.com")
    with session_factory() as db:
        assert get_value(db, "tailor_daily_limit") == 8

    reset = admin.delete("/api/admin/config/tailor_daily_limit")
    assert reset.status_code == 200
    assert _setting(reset.json(), "tailor_daily_limit")["value"] == 5
    with session_factory() as db:
        assert db.get(AppConfig, "tailor_daily_limit") is None


def test_a_dollar_setting_takes_cents(admin):
    response = admin.put("/api/admin/config/smartsearch_run_max_usd", json={"value": 1.25})
    assert response.status_code == 200, response.text
    assert _setting(response.json(), "smartsearch_run_max_usd")["value"] == 1.25


@pytest.mark.parametrize("value", [-1, 1001, 2.5])
def test_a_value_out_of_range_is_refused(admin, value):
    response = admin.put("/api/admin/config/tailor_daily_limit", json={"value": value})
    assert response.status_code == 422
    assert "Tailorings per user a day must be" in response.json()["detail"]


def test_an_unknown_setting_is_not_found(admin):
    assert admin.put("/api/admin/config/nope", json={"value": 1}).status_code == 404
    assert admin.delete("/api/admin/config/nope").status_code == 404


def test_members_cant_see_or_change_settings(client):
    assert client.get("/api/admin/config").status_code == 404
    assert client.put("/api/admin/config/tailor_daily_limit", json={"value": 9}).status_code == 404


def test_a_stored_value_outside_todays_range_falls_back_to_the_default(session_factory):
    with session_factory() as db:
        db.add(AppConfig(key="tailor_daily_limit", value=5000))
        db.commit()
        assert get_value(db, "tailor_daily_limit") == 5


def test_admin_overrides_a_users_limits_including_their_own(admin, session_factory):
    me = admin.get("/api/auth/me").json()
    response = admin.patch(f"/api/admin/users/{me['id']}", json={"override_limits": True})
    assert response.status_code == 200, response.text
    assert response.json()["override_limits"] is True
    with session_factory() as db:
        user = db.scalar(select(User).where(User.email == "owner@example.com"))
        assert daily_limit(db, user, "tailor_daily_limit") is None
        assert daily_limit(db, user.id, "company_research_daily_limit") is None


def test_users_have_daily_limits_unless_overridden(client, session_factory, set_config):
    set_config("tailor_daily_limit", 3)
    with session_factory() as db:
        user = db.scalar(select(User))
        assert user.override_limits is False
        assert daily_limit(db, user, "tailor_daily_limit") == 3
