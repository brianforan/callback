from datetime import date, timedelta

from fastapi.testclient import TestClient

from app.cli import create_token
from app.main import app

TEX_A = rb"\begin{document}Python Kafka Postgres platform engineer\end{document}"
TEX_B = rb"\begin{document}Python React Typescript frontend engineer\end{document}"


def upload(client: TestClient, content: bytes = TEX_A, name: str = "resume.tex"):
    return client.post("/api/resumes", files={"file": (name, content, "text/x-tex")})


def create_application(client: TestClient, **overrides):
    payload = {"company": "Acme", "role_title": "Engineer", **overrides}
    response = client.post("/api/applications", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_requires_authentication(session_factory):
    anonymous = TestClient(app)
    assert anonymous.get("/api/applications").status_code == 401
    assert anonymous.get("/api/analytics/overview").status_code == 401


def test_login_rejects_bad_password_and_unknown_email(client):
    fresh = TestClient(app)
    bad = {"email": "owner@example.com", "password": "wrong"}
    unknown = {"email": "nobody@example.com", "password": "wrong"}
    assert fresh.post("/api/auth/login", json=bad).status_code == 401
    assert fresh.post("/api/auth/login", json=unknown).status_code == 401


def test_session_cookie_is_http_only_and_strict(session_factory, client):
    response = client.post(
        "/api/auth/login", json={"email": "owner@example.com", "password": "correct-horse-battery"}
    )
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=strict" in cookie


def test_upload_is_idempotent_on_content(client):
    first = upload(client)
    assert first.status_code == 201
    again = upload(client, name="renamed.tex")
    assert again.status_code == 200
    assert again.json()["id"] == first.json()["id"]
    assert upload(client, TEX_B).json()["id"] != first.json()["id"]


def test_upload_rejects_wrong_type_and_binary(client):
    assert upload(client, name="resume.docx").status_code == 422
    # A .pdf name is accepted only with PDF content.
    assert upload(client, name="resume.pdf").status_code == 422
    assert upload(client, b"\xff\xfe\x00bad").status_code == 422
    assert upload(client, b"   ").status_code == 422


def test_status_change_records_event(client):
    application = create_application(client)
    assert [event["status"] for event in application["events"]] == ["applied"]
    updated = client.patch(f"/api/applications/{application['id']}", json={"status": "interview"})
    assert updated.status_code == 200
    assert [event["status"] for event in updated.json()["events"]] == ["applied", "interview"]
    same = client.patch(f"/api/applications/{application['id']}", json={"status": "interview"})
    assert len(same.json()["events"]) == 2


def test_rejects_unsafe_url_and_null_required_fields(client):
    bad_url = {"company": "A", "role_title": "B", "job_url": "javascript:alert(1)"}
    assert client.post("/api/applications", json=bad_url).status_code == 422
    application = create_application(client)
    url = f"/api/applications/{application['id']}"
    assert client.patch(url, json={"company": None}).status_code == 422
    assert client.patch(url, json={"notes": None}).status_code == 200


def test_data_is_isolated_between_users(client, other_client):
    resume = upload(client).json()
    application = create_application(client, resume_id=resume["id"])
    assert other_client.get("/api/applications").json() == []
    assert other_client.get(f"/api/applications/{application['id']}").status_code == 404
    assert other_client.delete(f"/api/applications/{application['id']}").status_code == 404
    assert other_client.get(f"/api/resumes/{resume['id']}").status_code == 404
    linked_to_foreign = {"company": "X", "role_title": "Y", "resume_id": resume["id"]}
    assert other_client.post("/api/applications", json=linked_to_foreign).status_code == 422


def test_resume_in_use_cannot_be_deleted(client):
    resume = upload(client).json()
    application = create_application(client, resume_id=resume["id"])
    assert client.delete(f"/api/resumes/{resume['id']}").status_code == 409
    client.delete(f"/api/applications/{application['id']}")
    assert client.delete(f"/api/resumes/{resume['id']}").status_code == 204


def test_overview_counts_callbacks_even_after_rejection(client):
    resume = upload(client).json()
    rejected_later = create_application(client, resume_id=resume["id"], source="LinkedIn")
    url = f"/api/applications/{rejected_later['id']}"
    client.patch(url, json={"status": "interview"})
    client.patch(url, json={"status": "rejected"})
    old = (date.today() - timedelta(days=30)).isoformat()
    create_application(client, resume_id=resume["id"], source="LinkedIn", applied_on=old)

    overview = client.get("/api/analytics/overview").json()
    assert overview["totals"]["applications"] == 2
    assert overview["totals"]["callbacks"] == 1
    assert overview["totals"]["callback_rate"] == 0.5
    assert {stage["stage"]: stage["count"] for stage in overview["funnel"]} == {
        "applied": 2,
        "screening": 1,
        "interview": 1,
        "offer": 0,
    }
    assert overview["sources"][0]["source"] == "LinkedIn"
    assert overview["resumes"][0]["callbacks"] == 1
    assert [item["days_waiting"] for item in overview["stale"]] == [30]
    assert sum(point["applications"] for point in overview["weekly"]) == 2


def test_hiring_paused_is_not_active_and_keeps_callback(client):
    resume = upload(client).json()
    application = create_application(client, resume_id=resume["id"])
    url = f"/api/applications/{application['id']}"
    client.patch(url, json={"status": "screening"})
    response = client.patch(url, json={"status": "hiring_paused"})
    assert response.status_code == 200
    assert response.json()["status"] == "hiring_paused"

    totals = client.get("/api/analytics/overview").json()["totals"]
    assert totals["active"] == 0
    assert totals["callbacks"] == 1


TEX_STRUCTURED = rb"""\begin{document}
\section*{Skills}
Python, Docker, Kafka
\section*{Experience}
\job{Backend Engineer}{Acme}
{Jan 2020 -- Present}
\begin{itemize}
  \item Python services on Kafka.
\end{itemize}
\section*{Education}
Bachelor's in Computer Science
\end{document}"""


def test_resume_insights_grade_posting_demand_against_the_latest_resume(client):
    resume = upload(client, TEX_STRUCTURED).json()
    create_application(
        client,
        resume_id=resume["id"],
        posting_text="Requirements\n- Python\n- Docker\n- Rust\n",
    )
    create_application(client, company="Beta", posting_text="Requirements\n- Python\n")

    insights = client.get("/api/analytics/resumes").json()
    assert insights["latest_resume"] == resume["label"]
    assert insights["postings"] == 2
    demand = {row["name"]: row for row in insights["demand"]}
    assert demand["Python"]["required_in"] == 2
    assert demand["Python"]["verdict"] == "met"
    assert demand["Python"]["evidence"] == "experience"
    assert demand["Docker"]["verdict"] == "weak"
    assert demand["Rust"]["verdict"] == "missing"
    assert insights["demand"][0]["name"] == "Python"

    [evidence] = insights["resumes"]
    assert evidence["sections"] == ["Skills", "Experience", "Education"]
    assert evidence["roles"] == 1
    assert evidence["degree"] == "bachelor"
    assert evidence["backed"] == ["Kafka", "Python"]
    assert evidence["listed_only"] == ["Docker"]


def test_api_token_authenticates(session_factory, client, capsys, monkeypatch):
    monkeypatch.setattr("app.cli.SessionLocal", session_factory)
    assert create_token("owner@example.com", "claude") == 0
    token = capsys.readouterr().out.strip()
    assert token.startswith("cb_")
    anonymous = TestClient(app)
    headers = {"Authorization": f"Bearer {token}"}
    assert anonymous.get("/api/auth/me", headers=headers).json()["email"] == "owner@example.com"
    assert (
        anonymous.get("/api/auth/me", headers={"Authorization": "Bearer nope"}).status_code == 401
    )
