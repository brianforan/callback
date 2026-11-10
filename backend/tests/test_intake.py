import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.config import get_settings
from app.main import app
from app.models import ApiToken, Application, JobPosting, Resume, User
from app.security import generate_api_token, hash_api_token

PATH = "/api/intake/applications"
TEX = "\\documentclass{article}\n\\begin{document}Python Kafka engineer\\end{document}\n"
BODY = {"company": "Acme", "role_title": "Engineer"}


def _add_user(factory, email: str) -> User:
    with factory() as session:
        user = User(email=email, password_hash="unused")
        session.add(user)
        session.commit()
        return user


def _count(factory, model) -> int:
    with factory() as session:
        return session.scalar(select(func.count()).select_from(model))


@pytest.fixture
def clear_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def default_user(session_factory, monkeypatch, clear_settings) -> User:
    user = _add_user(session_factory, "default@example.com")
    monkeypatch.setenv("DEFAULT_USER_ID", str(user.id))
    return user


@pytest.fixture
def anonymous() -> TestClient:
    return TestClient(app)


def test_guide_lists_what_the_post_needs(session_factory, anonymous, clear_settings):
    guide = anonymous.get(PATH).json()
    assert guide["method"] == "POST"
    assert guide["path"] == PATH
    assert guide["bearer_token_required"] is True
    assert set(guide["json_schema"]["required"]) == {"company", "role_title"}
    assert "resume_tex" in guide["json_schema"]["properties"]
    assert "posting_text" in guide["json_schema"]["properties"]


def test_guide_says_no_token_is_needed_with_a_default_user(default_user, anonymous):
    assert anonymous.get(PATH).json()["bearer_token_required"] is False


def test_guide_example_is_accepted(default_user, anonymous):
    example = anonymous.get(PATH).json()["example"]
    assert anonymous.post(PATH, json=example).status_code == 201


def test_post_without_default_user_requires_authentication(
    session_factory, anonymous, clear_settings
):
    assert anonymous.post(PATH, json=BODY).status_code == 401


def test_post_stores_everything_for_the_default_user(session_factory, default_user, anonymous):
    response = anonymous.post(
        PATH,
        json={
            **BODY,
            "resume_tex": TEX,
            "resume_filename": "out/acme.tex",
            "job_url": "https://example.com/jobs/1",
            "source": "LinkedIn",
            "location": "Remote",
            "applied_on": "2026-10-30",
            "notes": "Applied through the site",
            "posting_text": "Engineer\n\nRequired\n- Python",
            "referrer": "Sam",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["applied_on"] == "2026-10-30"
    assert body["status"] == "applied"
    assert body["resume_label"] == "Acme Engineer"

    with session_factory() as session:
        application = session.scalar(select(Application))
        assert application.user_id == default_user.id
        assert application.notes == "Applied through the site"
        resume = session.scalar(select(Resume))
        assert resume.user_id == default_user.id
        assert resume.source_tex == TEX
        assert resume.filename == "acme.tex"
        posting = session.scalar(select(JobPosting))
        assert posting.raw_text == "Engineer\n\nRequired\n- Python"
        assert posting.referrer == "Sam"


def test_identical_resume_is_reused(session_factory, default_user, anonymous):
    first = anonymous.post(PATH, json={**BODY, "resume_tex": TEX}).json()
    second = anonymous.post(PATH, json={**BODY, "company": "Beta", "resume_tex": TEX}).json()
    assert first["resume_id"] == second["resume_id"]
    assert _count(session_factory, Resume) == 1
    assert _count(session_factory, Application) == 2


def test_resume_is_optional(session_factory, default_user, anonymous):
    response = anonymous.post(PATH, json=BODY)
    assert response.status_code == 201
    assert response.json()["resume_id"] is None
    assert _count(session_factory, Resume) == 0


def test_valid_token_overrides_the_default_user(session_factory, default_user, anonymous):
    other = _add_user(session_factory, "other@example.com")
    token = generate_api_token()
    with session_factory() as session:
        session.add(ApiToken(user_id=other.id, name="test", token_hash=hash_api_token(token)))
        session.commit()
    response = anonymous.post(PATH, json=BODY, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 201
    with session_factory() as session:
        assert session.scalar(select(Application)).user_id == other.id


def test_bad_token_does_not_fall_back_to_the_default_user(
    session_factory, default_user, anonymous
):
    response = anonymous.post(PATH, json=BODY, headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 401
    assert _count(session_factory, Application) == 0


def test_unknown_default_user_is_reported(session_factory, monkeypatch, clear_settings, anonymous):
    monkeypatch.setenv("DEFAULT_USER_ID", "c048281b-c614-450d-a0eb-b90f40c46b8f")
    assert anonymous.post(PATH, json=BODY).status_code == 503


def test_blank_default_user_setting_means_none(monkeypatch, clear_settings):
    monkeypatch.setenv("DEFAULT_USER_ID", "")
    assert get_settings().default_user_id is None


def test_unescaped_latex_is_refused(session_factory, default_user, anonymous):
    response = anonymous.post(PATH, json={**BODY, "resume_tex": "\begin{document}"})
    assert response.status_code == 422
    assert _count(session_factory, Application) == 0
    assert _count(session_factory, Resume) == 0


@pytest.mark.parametrize(
    "overrides",
    [
        {"role_title": ""},
        {"role": "Engineer"},
        {"status": "maybe"},
        {"applied_on": "yesterday"},
        {"job_url": "javascript:alert(1)"},
        {"resume_tex": TEX, "resume_filename": "acme.pdf"},
    ],
)
def test_invalid_body_stores_nothing(session_factory, default_user, anonymous, overrides):
    response = anonymous.post(PATH, json={**BODY, **overrides})
    assert response.status_code == 422
    assert _count(session_factory, Application) == 0
    assert _count(session_factory, Resume) == 0


def test_resume_over_the_limit_is_refused(session_factory, default_user, anonymous, monkeypatch):
    monkeypatch.setenv("MAX_RESUME_BYTES", "10")
    assert anonymous.post(PATH, json={**BODY, "resume_tex": TEX}).status_code == 413
    assert _count(session_factory, Resume) == 0


@pytest.mark.parametrize("headers", [{"Content-Type": "text/plain"}, {}])
def test_other_sites_cannot_post_without_a_json_content_type(
    session_factory, default_user, anonymous, headers
):
    # A browser sends these across origins with no preflight, so they must not be parsed.
    response = anonymous.post(
        PATH, content='{"company": "Acme", "role_title": "Engineer"}', headers=headers
    )
    assert response.status_code == 415
    assert _count(session_factory, Application) == 0


def test_other_routes_still_require_authentication(default_user, anonymous):
    assert anonymous.get("/api/applications").status_code == 401
    assert anonymous.post("/api/applications", json=BODY).status_code == 401
