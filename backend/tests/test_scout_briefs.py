"""Briefs on demand for Companies leads: queueing, the daily cap, writing, failures and
crashes."""

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.billing.spend import daily
from app.models import JobPosting, ScoutBrief, ScoutRun, User, utcnow
from app.profiles import default_profile
from app.scout.briefs import claim_next_brief, fail_stale_briefs
from app.scout.worker import write_brief_safely
from app.workers import TailorUnavailable

TODAY = date(2026, 11, 5)


def _setup(session_factory, *, brief_status="none"):
    """A premium owner with one finished Companies run and its lead, staged as a posting."""
    with session_factory() as db:
        user = db.scalar(select(User).where(User.email == "owner@example.com"))
        user.plan = "premium"
        profile = default_profile(db, user)
        db.flush()
        posting = JobPosting(
            user_id=user.id,
            profile_id=profile.id,
            company="Acme",
            company_key="acme",
            title="Laravel Engineer",
            url="https://jobs.lever.co/acme/1",
            seen_on=TODAY,
            staged_at=utcnow(),
            staged_notes="Found by Scout.",
        )
        db.add(posting)
        db.flush()
        run = ScoutRun(
            user_id=user.id,
            profile_id=profile.id,
            status="done",
            settings={
                "engine": "companies",
                "description": "Laravel internal tools",
                "limits": {
                    "target_leads": 20,
                    "dry_streak": 4,
                    "max_searches": 5,
                    "max_pages": 60,
                    "max_minutes": 10,
                },
            },
            leads=[
                {
                    "id": "lead-1",
                    "company": "Acme",
                    "title": "Laravel Engineer",
                    "url": "https://jobs.lever.co/acme/1",
                    "fit": "good",
                    "fit_summary": "Laravel internal tools.",
                    "posting_text": "Build internal tools in Laravel.",
                    "posting_id": str(posting.id),
                    "state": "new",
                    "brief_status": brief_status,
                }
            ],
            steps=[],
            started_at=utcnow(),
            finished_at=utcnow(),
        )
        db.add(run)
        db.commit()
        return run.id, posting.id


def _brief_answer(payload, cost=0.06):
    assert payload["posting"]["text"] == "Build internal tools in Laravel."
    assert payload["judgment"]["fit"] == "good"
    return {
        "model": "claude-sonnet-5-5",
        "cost_usd": cost,
        "usage": {"output_tokens": 900},
        "request_costs": [{"request": 1, "cost_usd": cost}],
        "lead": {
            "company": "Acme",
            "title": "Laravel Engineer",
            "fit_summary": "Builds the internal tools you've built before.",
            "what_they_want": ["Laravel", "Queues"],
            "you_match": ["Laravel"],
            "you_lack": ["Kafka"],
            "application_tips": ["Lead with the billing tool"],
            "salary": "$140K - $170K",
        },
    }


def _lead(client):
    [lead] = client.get("/api/scout/leads").json()
    return lead


def _used(client):
    return client.get("/api/scout").json()["allowance"]["briefs_today"]


def test_a_brief_is_queued_once_and_counts_once(client, session_factory):
    run_id, _ = _setup(session_factory)
    assert _lead(client)["brief_status"] == "none"
    path = f"/api/scout/runs/{run_id}/leads/lead-1/brief"
    first = client.post(path)
    assert first.status_code == 200, first.text
    assert first.json()["brief_status"] == "queued"
    # A second click while it's on the way changes nothing.
    assert client.post(path).json()["brief_status"] == "queued"
    with session_factory() as db:
        assert len(db.scalars(select(ScoutBrief)).all()) == 1
    assert _used(client) == 1


def test_writing_a_brief_fills_the_lead_and_records_its_cost(client, session_factory):
    run_id, posting_id = _setup(session_factory)
    client.post(f"/api/scout/runs/{run_id}/leads/lead-1/brief")
    with session_factory() as db:
        brief = claim_next_brief(db)
        assert brief is not None and brief.status == "running"
        write_brief_safely(db, brief, _brief_answer)
        db.refresh(brief)
        assert (brief.status, brief.cost_usd) == ("done", 0.06)
        posting = db.get(JobPosting, posting_id)
        assert "Lead with the billing tool" in posting.staged_notes
    lead = _lead(client)
    assert lead["brief_status"] == "done"
    assert lead["what_they_want"] == ["Laravel", "Queues"]
    assert lead["fit_summary"] == "Builds the internal tools you've built before."
    assert (lead["company"], lead["url"]) == ("Acme", "https://jobs.lever.co/acme/1")
    # Counted once, and asking again changes nothing.
    assert _used(client) == 1
    again = client.post(f"/api/scout/runs/{run_id}/leads/lead-1/brief")
    assert again.json()["brief_status"] == "done"
    with session_factory() as db:
        assert len(db.scalars(select(ScoutBrief)).all()) == 1


def test_a_brief_is_refused_once_todays_briefs_are_used(client, session_factory, set_config):
    set_config("smartsearch_daily_briefs", 0)
    run_id, _ = _setup(session_factory)
    refused = client.post(f"/api/scout/runs/{run_id}/leads/lead-1/brief")
    assert refused.status_code == 429
    assert refused.json()["detail"] == (
        "You've used all 0 Scout briefs for today. They reset at midnight UTC."
    )
    assert client.post(f"/api/scout/runs/{run_id}/leads/nope/brief").status_code == 404
    allowance = client.get("/api/scout").json()["allowance"]
    assert (allowance["briefs_today"], allowance["daily_briefs"]) == (0, 0)


def test_a_user_with_limits_overridden_gets_briefs_past_the_cap(
    client, session_factory, set_config
):
    set_config("smartsearch_daily_briefs", 0)
    run_id, _ = _setup(session_factory)
    with session_factory() as db:
        db.scalar(select(User)).override_limits = True
        db.commit()
    answer = client.post(f"/api/scout/runs/{run_id}/leads/lead-1/brief")
    assert answer.status_code == 200, answer.text
    assert client.get("/api/scout").json()["allowance"]["daily_briefs"] is None


def test_a_failed_brief_doesnt_count_and_can_be_asked_for_again(client, session_factory):
    run_id, _ = _setup(session_factory)
    path = f"/api/scout/runs/{run_id}/leads/lead-1/brief"
    client.post(path)

    def outage(payload):
        raise TailorUnavailable("The model is rate limited right now.", {"cost_usd": 0.01})

    with session_factory() as db:
        brief = claim_next_brief(db)
        write_brief_safely(db, brief, outage)
        db.refresh(brief)
        assert (brief.status, brief.cost_usd) == ("failed", 0.01)
    lead = _lead(client)
    assert lead["brief_status"] == "failed" and "rate limited" in lead["brief_error"]
    assert _used(client) == 0
    assert client.post(path).json()["brief_status"] == "queued"
    with session_factory() as db:
        assert len(db.scalars(select(ScoutBrief)).all()) == 2


def test_a_crash_while_writing_fails_the_brief(client, session_factory):
    run_id, _ = _setup(session_factory)
    client.post(f"/api/scout/runs/{run_id}/leads/lead-1/brief")

    def crashes(payload):
        raise RuntimeError("a bug in the worker")

    with session_factory() as db:
        brief = claim_next_brief(db)
        write_brief_safely(db, brief, crashes)
        db.refresh(brief)
        assert brief.status == "failed"
    assert _lead(client)["brief_status"] == "failed"
    assert _used(client) == 0


def test_a_brief_left_running_is_failed_and_doesnt_count(client, session_factory):
    run_id, _ = _setup(session_factory)
    client.post(f"/api/scout/runs/{run_id}/leads/lead-1/brief")
    with session_factory() as db:
        brief = claim_next_brief(db)
        brief.started_at = utcnow() - timedelta(minutes=11)
        db.commit()
        assert fail_stale_briefs(db, utcnow()) == 1
    assert _lead(client)["brief_status"] == "failed"
    assert _used(client) == 0


def test_leads_whose_run_wrote_the_brief_offer_none(client, session_factory):
    run_id, _ = _setup(session_factory, brief_status="")
    answer = client.post(f"/api/scout/runs/{run_id}/leads/lead-1/brief")
    assert answer.status_code == 200 and answer.json()["brief_status"] == ""
    with session_factory() as db:
        assert db.scalars(select(ScoutBrief)).all() == []


def test_briefs_count_in_the_spend_pages_measured_cost():
    brief = ScoutBrief(cost_usd=0.06, finished_at=utcnow())
    [today] = daily([], [], [], None, utcnow().date(), 1, [brief])
    assert today["measured_usd"] == pytest.approx(0.06)
