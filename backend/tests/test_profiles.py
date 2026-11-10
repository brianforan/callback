import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app import profiles as profiles_module
from app.cli import create_user
from app.main import app
from app.models import Application, FactSheet, JobPosting, Profile, Resume, User
from app.profiles import default_profile

TEX_ENGINEER = rb"\begin{document}Python Kafka Postgres platform engineer\end{document}"
TEX_ACCOUNTANT = rb"\begin{document}GAAP month-end close reconciliations accountant\end{document}"


def upload(client, content: bytes) -> str:
    response = client.post("/api/resumes", files={"file": ("resume.tex", content, "text/x-tex")})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def add_profile(client, name: str) -> str:
    """Creates the profile and makes it this client's active one."""
    response = client.post("/api/profiles", json={"name": name})
    assert response.status_code == 201, response.text
    profile_id = response.json()["id"]
    assert client.post(f"/api/profiles/{profile_id}/activate").status_code == 200
    return profile_id


def activate(client, profile_id: str) -> None:
    assert client.post(f"/api/profiles/{profile_id}/activate").status_code == 200


def test_a_new_user_starts_with_one_default_profile(client):
    body = client.get("/api/profiles").json()
    assert [profile["name"] for profile in body["profiles"]] == ["Default"]
    assert body["active_id"] == body["profiles"][0]["id"]


def test_resumes_and_fact_sheets_belong_to_the_active_profile(client):
    default_id = client.get("/api/profiles").json()["active_id"]
    upload(client, TEX_ENGINEER)
    client.put("/api/fact-sheet", json={"content": "engineer facts"})

    add_profile(client, "Accountant")
    assert client.get("/api/resumes").json() == []
    assert client.get("/api/fact-sheet").json()["content"] == ""
    upload(client, TEX_ACCOUNTANT)
    client.put("/api/fact-sheet", json={"content": "accountant facts"})

    activate(client, default_id)
    assert len(client.get("/api/resumes").json()) == 1
    assert client.get("/api/fact-sheet").json()["content"] == "engineer facts"


def test_the_same_resume_can_sit_in_two_profiles(client):
    upload(client, TEX_ENGINEER)
    add_profile(client, "Copy")
    upload(client, TEX_ENGINEER)


def test_an_application_and_its_new_posting_take_the_resume_s_profile(client, session_factory):
    default_id = client.get("/api/profiles").json()["active_id"]
    engineer_resume = upload(client, TEX_ENGINEER)
    accountant_id = add_profile(client, "Accountant")

    sent = client.post(
        "/api/applications",
        json={"company": "Globex", "role_title": "Engineer", "resume_id": engineer_resume},
    ).json()
    unsent = client.post(
        "/api/applications", json={"company": "Initech", "role_title": "Accountant"}
    ).json()

    assert sent["profile_id"] == default_id
    assert unsent["profile_id"] == accountant_id
    with session_factory() as session:
        postings = {
            posting.company: str(posting.profile_id)
            for posting in session.scalars(select(JobPosting)).all()
        }
    assert postings == {"Globex": default_id, "Initech": accountant_id}


def test_applications_are_shared_across_profiles(client):
    client.post("/api/applications", json={"company": "Acme", "role_title": "Engineer"})
    add_profile(client, "Accountant")
    assert [app["company"] for app in client.get("/api/applications").json()] == ["Acme"]


def test_profile_names_are_unique_and_the_only_profile_stays(client):
    only = client.get("/api/profiles").json()["active_id"]
    assert client.delete(f"/api/profiles/{only}").status_code == 409
    assert client.post("/api/profiles", json={"name": "Default"}).status_code == 409


def test_a_profile_whose_resume_was_sent_cannot_be_deleted(client):
    default_id = client.get("/api/profiles").json()["active_id"]
    accountant_id = add_profile(client, "Accountant")
    resume = upload(client, TEX_ACCOUNTANT)
    client.post(
        "/api/applications",
        json={"company": "Initech", "role_title": "Accountant", "resume_id": resume},
    )
    activate(client, default_id)
    assert client.delete(f"/api/profiles/{accountant_id}").status_code == 409


def test_deleting_the_active_profile_falls_back_to_the_first(client):
    default_id = client.get("/api/profiles").json()["active_id"]
    spare_id = add_profile(client, "Spare")
    upload(client, TEX_ACCOUNTANT)
    assert client.delete(f"/api/profiles/{spare_id}").status_code == 204
    assert client.get("/api/profiles").json()["active_id"] == default_id
    assert client.get("/api/resumes").json() == []


def test_another_user_s_profile_is_not_found(client, other_client):
    theirs = other_client.get("/api/profiles").json()["active_id"]
    assert client.post(f"/api/profiles/{theirs}/activate").status_code == 404
    assert client.delete(f"/api/profiles/{theirs}").status_code == 404


def test_a_job_staged_under_one_profile_opens_under_another(client):
    """Saved jobs are shared, so the staged resume can belong to a profile that isn't active."""
    engineer_resume = upload(client, TEX_ENGINEER)
    posting = client.post(
        "/api/postings",
        json={
            "company": "Acme",
            "title": "Platform Engineer",
            "url": "https://a.test/platform",
            "raw_text": "Requirements\n- Python\n- Kafka\n",
        },
    ).json()
    staged = client.post(
        f"/api/postings/{posting['id']}/stage", json={"resume_id": engineer_resume}
    )
    assert staged.status_code == 200, staged.text
    in_own_profile = staged.json()["staged_match"]

    add_profile(client, "Accountant")
    upload(client, TEX_ACCOUNTANT)
    detail = client.get(f"/api/postings/{posting['id']}")

    assert detail.status_code == 200, detail.text
    assert detail.json()["staged_resume_id"] == engineer_resume
    assert detail.json()["staged_match"] == in_own_profile


def test_a_fact_sheet_draft_saves_to_the_profile_it_was_written_for(client):
    """Another tab can switch the active profile while an editor holds a draft."""
    default_id = client.get("/api/profiles").json()["active_id"]
    assert client.get("/api/fact-sheet").json()["profile_id"] == default_id
    add_profile(client, "Accountant")
    client.put("/api/fact-sheet", json={"content": "accountant facts"})

    saved = client.put(
        "/api/fact-sheet", json={"content": "engineer facts", "profile_id": default_id}
    )

    assert saved.status_code == 200, saved.text
    assert saved.json()["profile_id"] == default_id
    assert client.get("/api/fact-sheet").json()["content"] == "accountant facts"
    activate(client, default_id)
    assert client.get("/api/fact-sheet").json()["content"] == "engineer facts"


def test_a_resume_version_saves_to_the_profile_given(client):
    default_id = client.get("/api/profiles").json()["active_id"]
    add_profile(client, "Accountant")

    saved = client.post(
        "/api/resumes",
        files={"file": ("resume.tex", TEX_ENGINEER, "text/x-tex")},
        data={"profile_id": default_id},
    )

    assert saved.status_code == 201, saved.text
    assert saved.json()["profile_id"] == default_id
    assert client.get("/api/resumes").json() == []
    activate(client, default_id)
    assert [resume["id"] for resume in client.get("/api/resumes").json()] == [saved.json()["id"]]


def test_saving_to_another_user_s_profile_is_not_found(client, other_client):
    theirs = other_client.get("/api/profiles").json()["active_id"]
    sheet = client.put("/api/fact-sheet", json={"content": "x", "profile_id": theirs})
    resume = client.post(
        "/api/resumes",
        files={"file": ("resume.tex", TEX_ENGINEER, "text/x-tex")},
        data={"profile_id": theirs},
    )
    assert sheet.status_code == 404
    assert resume.status_code == 404
    assert other_client.get("/api/fact-sheet").json()["content"] == ""
    assert other_client.get("/api/resumes").json() == []


def test_foreign_keys_are_enforced_in_tests(session_factory):
    """The ON DELETE rules below live in the database, so SQLite has to be told to apply them."""
    with session_factory() as db:
        assert db.execute(text("PRAGMA foreign_keys")).scalar() == 1


def test_deleting_a_profile_removes_what_it_owns_and_keeps_its_applications(
    client, session_factory
):
    default_id = client.get("/api/profiles").json()["active_id"]
    spare_id = add_profile(client, "Spare")
    resume_id = upload(client, TEX_ACCOUNTANT)
    client.put("/api/fact-sheet", json={"content": "spare facts"})
    application = client.post(
        "/api/applications", json={"company": "Initech", "role_title": "Accountant"}
    ).json()
    assert application["profile_id"] == spare_id

    activate(client, default_id)
    assert client.delete(f"/api/profiles/{spare_id}").status_code == 204

    with session_factory() as db:
        assert db.get(Resume, uuid.UUID(resume_id)) is None
        assert db.scalars(select(FactSheet)).all() == []
        kept = db.get(Application, uuid.UUID(application["id"]))
        assert kept is not None and kept.profile_id is None
        posting = db.get(JobPosting, kept.posting_id)
        assert posting is not None and posting.profile_id is None


def _profile_names(session_factory, email):
    with session_factory() as db:
        user = db.scalar(select(User).where(User.email == email))
        return [p.name for p in db.scalars(select(Profile).where(Profile.user_id == user.id))]


def test_a_new_account_starts_with_its_profile_already_made(session_factory, monkeypatch):
    """Made with the account, so a first page's parallel requests have nothing to race over."""
    response = TestClient(app).post(
        "/api/auth/register", json={"email": "new@example.com", "password": "correct-horse-battery"}
    )
    assert response.status_code == 201, response.text
    assert _profile_names(session_factory, "new@example.com") == ["Default"]

    monkeypatch.setattr("app.cli.SessionLocal", session_factory)
    monkeypatch.setattr("app.cli.getpass.getpass", lambda prompt: "correct-horse-battery")
    assert create_user("cli@example.com") == 0
    assert _profile_names(session_factory, "cli@example.com") == ["Default"]


def test_a_default_profile_made_by_another_request_meanwhile_is_used(
    client, session_factory, monkeypatch
):
    """The other request's insert lands between this one's check and its own insert."""
    with session_factory() as db:
        user = db.scalar(select(User).where(User.email == "owner@example.com"))
        theirs = profiles_module.first_profile(user.id)
        db.add(theirs)
        db.commit()
        real_user_profiles = profiles_module.user_profiles
        calls = []

        def not_seen_the_first_time(session, user_id):
            calls.append(user_id)
            return [] if len(calls) == 1 else real_user_profiles(session, user_id)

        monkeypatch.setattr(profiles_module, "user_profiles", not_seen_the_first_time)
        picked = default_profile(db, user)
        db.commit()

        assert picked.id == theirs.id
        assert len(db.scalars(select(Profile).where(Profile.user_id == user.id)).all()) == 1
