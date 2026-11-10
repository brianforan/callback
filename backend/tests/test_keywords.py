from datetime import date, timedelta

from fastapi.testclient import TestClient

from app.cli import refresh_resume_text
from app.main import app
from app.models import Resume

TEX_OLD = rb"\begin{document}Python Kafka platform. Machine learning\end{document}"
TEX_NEW = rb"\begin{document}Python React frontend. Machine learning\end{document}"


def _upload(client: TestClient, content: bytes):
    response = client.post("/api/resumes", files={"file": ("r.tex", content, "text/x-tex")})
    assert response.status_code in (200, 201), response.text
    return response.json()


def _apply(client: TestClient, resume, days_ago: int = 0):
    applied_on = (date.today() - timedelta(days=days_ago)).isoformat()
    response = client.post(
        "/api/applications",
        json={
            "company": "Acme",
            "role_title": "Engineer",
            "resume_id": resume["id"],
            "applied_on": applied_on,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _trends(client: TestClient):
    response = client.get("/api/analytics/keywords")
    assert response.status_code == 200, response.text
    return response.json()


def _by_term(rows):
    return {row["term"]: row for row in rows}


def _shift_resumes(client: TestClient):
    """Six applications with the old resume, then six with the new one, a week apart."""
    old = _upload(client, TEX_OLD)
    new = _upload(client, TEX_NEW)
    for index in range(12):
        _apply(client, old if index < 6 else new, days_ago=(12 - index) * 7)


def test_no_applications_gives_empty_trends(client):
    _upload(client, TEX_OLD)
    trends = _trends(client)
    assert trends["applications"] == 0
    assert trends["most_used"] == []
    assert trends["weekly"] == []


def test_counts_each_send_and_skips_unsent_resumes(client):
    sent = _upload(client, TEX_OLD)
    _upload(client, TEX_NEW)
    _apply(client, sent, days_ago=7)
    _apply(client, sent)
    trends = _trends(client)
    assert trends["applications"] == 2
    words = _by_term(trends["most_used"])
    assert words["Kafka"]["applications"] == 2
    assert words["Kafka"]["share"] == 1
    assert words["Kafka"]["mentions"] == 2
    assert "React" not in words
    # Only known skills count, so ordinary words never show up as keywords.
    assert set(words) == {"Python", "Kafka"}


def test_faded_and_rising_wait_for_enough_applications(client):
    resume = _upload(client, TEX_OLD)
    for _ in range(9):
        _apply(client, resume)
    trends = _trends(client)
    assert trends["min_applications"] == 10
    assert trends["faded"] == []
    assert trends["rising"] == []
    assert trends["most_used"][0]["early_share"] is None


def test_faded_and_rising_compare_earliest_and_latest_thirds(client):
    _shift_resumes(client)
    trends = _trends(client)
    faded = _by_term(trends["faded"])
    rising = _by_term(trends["rising"])
    assert faded["Kafka"]["early_share"] == 1
    assert faded["Kafka"]["recent_share"] == 0
    assert faded["Kafka"]["last_used"] < rising["React"]["first_used"]
    assert rising["React"]["early_share"] == 0
    assert rising["React"]["recent_share"] == 1
    assert "Python" not in faded
    assert "Python" not in rising
    assert _by_term(trends["most_used"])["Python"]["share"] == 1


def test_chart_follows_the_words_that_moved_and_leaves_gaps(client):
    old = _upload(client, TEX_OLD)
    new = _upload(client, TEX_NEW)
    for days_ago in (35, 28):
        _apply(client, old, days_ago=days_ago)
    for days_ago in (14, 7):
        _apply(client, new, days_ago=days_ago)
    trends = _trends(client)
    assert {"Kafka", "React"} <= set(trends["chart_terms"])
    assert "Python" not in trends["chart_terms"][:2]
    assert len(trends["weekly"]) >= 5
    assert trends["weekly"][0]["shares"]["Kafka"] == 1
    assert trends["weekly"][2]["shares"] == {}


def test_ignoring_a_skill_hides_it_from_trends(client):
    resume = _upload(client, TEX_OLD)
    _apply(client, resume)

    response = client.post("/api/keywords/ignored", json={"term": "  Machine "})
    assert response.status_code == 204
    assert client.post("/api/keywords/ignored", json={"term": "Kafka"}).status_code == 204
    assert client.post("/api/keywords/ignored", json={"term": "kafka"}).status_code == 204

    trends = _trends(client)
    assert trends["ignored"] == ["kafka", "machine"]
    assert "Kafka" not in _by_term(trends["most_used"])
    assert "Python" in _by_term(trends["most_used"])

    restored = client.delete("/api/keywords/ignored", params={"term": "kafka"})
    assert restored.status_code == 204
    assert "Kafka" in _by_term(_trends(client)["most_used"])


def test_terms_with_url_characters_can_be_restored(client):
    client.post("/api/keywords/ignored", json={"term": "c#"})
    assert _trends(client)["ignored"] == ["c#"]
    client.delete("/api/keywords/ignored", params={"term": "c#"})
    assert _trends(client)["ignored"] == []


def test_ignored_terms_are_isolated_between_users(client, other_client):
    client.post("/api/keywords/ignored", json={"term": "kafka"})
    assert _trends(other_client)["ignored"] == []
    other_client.delete("/api/keywords/ignored", params={"term": "kafka"})
    assert _trends(client)["ignored"] == ["kafka"]


def test_keyword_endpoints_require_login(session_factory):
    anonymous = TestClient(app)
    assert anonymous.get("/api/analytics/keywords").status_code == 401
    assert anonymous.post("/api/keywords/ignored", json={"term": "x"}).status_code == 401


def test_refresh_resumes_rewrites_stored_text(session_factory, client, monkeypatch):
    monkeypatch.setattr("app.cli.SessionLocal", session_factory)
    _upload(client, rb"\begin{document}Jane\\[0.12em] Engineer\end{document}")
    with session_factory() as db:
        resume = db.query(Resume).one()
        resume.plain_text = "Jane 0.12em Engineer"
        resume.word_count = 3
        db.commit()

    assert refresh_resume_text("owner@example.com") == 0
    with session_factory() as db:
        resume = db.query(Resume).one()
        assert resume.plain_text == "Jane Engineer"
        assert resume.word_count == 2
    assert refresh_resume_text("nobody@example.com") == 1
