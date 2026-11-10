import uuid
from datetime import UTC, date, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError

from app.models import ScoutRun, ScoutSettings, TailorStatus, User
from app.routers import scout as scout_router
from app.schemas import ScoutServiceResult
from app.scout import worker as scout_worker
from app.scout.engine import CompanyStages
from app.scout.leads import lead_notes
from app.scout.limits import (
    LIMITS,
    default_limits,
    fit_limits,
    lead_posting_text,
    limit_errors,
)
from app.scout.worker import build_payload, claim_next, fail_stale, run_one
from app.tailoring.worker import TailorUnavailable


def _make_premium(session_factory, email: str = "owner@example.com") -> None:
    with session_factory() as session:
        user = session.scalar(select(User).where(User.email == email))
        user.plan = "premium"
        session.commit()


def _payload(**changes):
    payload = {
        "description": "  Internal tools work with operations teams  ",
        "title_families": [" internal  tools ", 'automation "engineer"'],
        "work_modes": ["remote"],
        "location": "  Philadelphia, PA  ",
        "posted_within_days": 14,
        "fit_mode": "wide",
        "agency_mode": "find_employer",
        "limits": default_limits(),
    }
    payload.update(changes)
    return payload


def test_limit_errors_name_the_range_and_unknown_keys():
    assert limit_errors(default_limits()) == []
    errors = limit_errors({**default_limits(), "max_searches": 99, "surprise": 1})
    assert "surprise isn't a Scout limit" in errors
    assert any("from 1 to 10 searches" in error for error in errors)
    missing = {key: value for key, value in default_limits().items() if key != "max_pages"}
    assert limit_errors(missing) == ["Most postings to check is missing"]


def test_fit_limits_clamps_and_fills_in():
    fitted = fit_limits({"max_pages": 500, "target_leads": 0})
    assert fitted["max_pages"] == 300
    assert fitted["target_leads"] == 1
    assert set(fitted) == {spec.key for spec in LIMITS}


def test_new_user_sees_free_plan_and_defaults(client):
    body = client.get("/api/scout").json()
    assert body["plan"] == "free"
    allowance = body["allowance"]
    assert (allowance["runs_today"], allowance["daily_runs"]) == (0, 2)
    assert (allowance["briefs_today"], allowance["daily_briefs"]) == (0, 10)
    assert allowance["run_max_usd"] == 2.0
    assert "credit_rates" not in body
    assert [limit["key"] for limit in body["limits"]] == [spec.key for spec in LIMITS]
    settings = body["settings"]
    assert settings["updated_at"] is None
    assert settings["limits"] == default_limits()
    assert "credit_cap" not in settings
    assert settings["agency_mode"] == "include"


def test_free_user_cannot_save(client):
    response = client.put("/api/scout/settings", json=_payload())
    assert response.status_code == 403


def test_premium_user_saves_and_others_dont_see_it(client, other_client, session_factory):
    _make_premium(session_factory)
    response = client.put("/api/scout/settings", json=_payload())
    assert response.status_code == 200
    saved = response.json()
    assert saved["description"] == "Internal tools work with operations teams"
    assert saved["title_families"] == ["internal tools", "automation engineer"]
    assert saved["location"] == "Philadelphia, PA"
    assert saved["fit_mode"] == "wide"
    assert saved["agency_mode"] == "find_employer"
    assert saved["relocation"] == "none"
    assert saved["limits"] == default_limits()
    moved = client.put("/api/scout/settings", json=_payload(relocation="metro"))
    assert moved.json()["relocation"] == "metro"
    assert client.put("/api/scout/settings", json=_payload(relocation="mars")).status_code == 422
    assert client.get("/api/scout").json()["settings"]["posted_within_days"] == 14
    assert other_client.get("/api/scout").json()["settings"]["updated_at"] is None


def test_save_rejects_out_of_range_values(client, session_factory):
    _make_premium(session_factory)
    too_many = _payload(limits={**default_limits(), "target_leads": 61})
    refused = client.put("/api/scout/settings", json=too_many)
    assert refused.status_code == 422
    assert "from 1 to 60 leads" in refused.json()["detail"]
    chosen = {**default_limits(), "target_leads": 40, "max_pages": 300}
    saved = client.put("/api/scout/settings", json=_payload(limits=chosen))
    assert saved.status_code == 200
    assert saved.json()["limits"] == chosen
    # An older page that sends a credit cap and no limits keeps what was saved.
    older = {key: value for key, value in _payload(credit_cap=30).items() if key != "limits"}
    kept = client.put("/api/scout/settings", json=older)
    assert kept.status_code == 200
    assert kept.json()["limits"] == chosen
    assert client.put("/api/scout/settings", json=_payload(fit_mode="loose")).status_code == 422
    bad_agency = _payload(agency_mode="sometimes")
    assert client.put("/api/scout/settings", json=bad_agency).status_code == 422
    odd_window = _payload(posted_within_days=3)
    assert client.put("/api/scout/settings", json=odd_window).status_code == 422
    long_text = _payload(description="x" * 601)
    assert client.put("/api/scout/settings", json=long_text).status_code == 422
    long_place = _payload(location="x" * 121)
    assert client.put("/api/scout/settings", json=long_place).status_code == 422


def test_title_phrases_are_checked_before_saving(client, session_factory):
    _make_premium(session_factory)

    def save(phrases):
        return client.put("/api/scout/settings", json=_payload(title_families=phrases))

    fifteen = [f"clinical systems {n}" for n in range(15)]
    assert save(fifteen).json()["title_families"] == fifteen
    assert save(["QA", "x" * 60]).status_code == 200
    assert save([*fifteen, "one more"]).status_code == 422
    short = save(["GxP", " a "])
    assert short.status_code == 422
    assert "2 to 60 characters" in short.json()["detail"][0]["msg"]
    assert save(['""']).status_code == 422
    assert save(["x" * 61]).status_code == 422
    twice = save(["Validation Engineer", "validation  engineer"])
    assert twice.status_code == 422
    assert "listed twice" in twice.json()["detail"][0]["msg"]
    assert client.get("/api/scout").json()["settings"]["title_families"] == ["QA", "x" * 60]


def _save_settings(client, session_factory, **changes):
    _make_premium(session_factory)
    response = client.put("/api/scout/settings", json=_payload(**changes))
    assert response.status_code == 200


def _result(**changes) -> ScoutServiceResult:
    body = {
        "leads": [
            {
                "company": "Acme",
                "title": "Internal Tools Engineer",
                "url": "https://jobs.lever.co/acme/123",
                "location": "Remote, US",
                "fit_summary": "Builds tools for ops.",
                "what_they_want": ["Automate finance workflows"],
                "requirements": ["Python", "PostgreSQL"],
                "company_notes": "Series B logistics.",
                "fit": "stretch",
                "agency": "Robert Half",
                "salary": "$130,000 - $160,000 per year",
                "level": "senior",
                "offer_summary": "Equity and full health cover.",
                "how_to_apply": ["A short form with two questions"],
                "application_tips": ["Lead with the internal tools you built"],
                "posting_text": "Senior Internal Tools Engineer. Requirements: Python, "
                "PostgreSQL, AWS. You will automate finance workflows.",
            }
        ],
        "stop_code": "target_reached",
        "stop_message": "Found 1 leads, the number you asked for.",
        "searches": 6,
        "pages": 4,
        "requests": 5,
        "usage": {"input_tokens": 1000, "output_tokens": 2000, "web_search_requests": 6},
        "cost_usd": 0.873,
        "model": "claude-sonnet-5-5",
        "steps": [
            {"kind": "search", "detail": "internal tools engineer remote", "request": 1},
            {"kind": "results", "detail": "8 results, 8 new", "request": 1},
            {"kind": "lead", "detail": "Acme, Internal Tools Engineer", "request": 2},
        ],
    }
    body.update(changes)
    return ScoutServiceResult.model_validate(body)


def _unused(payload):
    raise AssertionError("a model stage was called")


UNUSED_STAGES = CompanyStages(judge=_unused, same_company=_unused)


def _run_worker(session_factory, send) -> None:
    """Run the next queued run through the worker, with the engine faked by send(payload)."""

    def engine(db, user_id, profile_id, payload, stages, today, **_):
        return send(payload)

    with session_factory() as db, patch.object(scout_worker, "run_companies", engine):
        run = claim_next(db)
        assert run is not None
        run_one(db, run, date(2026, 11, 3), UNUSED_STAGES)


def test_lead_posting_text_names_the_skills():
    text = lead_posting_text(
        {"title": "Engineer", "company": "Acme", "url": "https://a.io", "requirements": ["Python"]}
    )
    assert "Engineer at Acme" in text
    assert "Requirements: Python" in text


def test_start_run_needs_premium_and_saved_settings(client, session_factory):
    assert client.post("/api/scout/runs").status_code == 403
    _make_premium(session_factory)
    response = client.post("/api/scout/runs")
    assert response.status_code == 409
    assert "Save your settings" in response.json()["detail"]
    client.put("/api/scout/settings", json=_payload(description=""))
    assert "Describe the work" in client.post("/api/scout/runs").json()["detail"]


def test_the_work_line_shows_and_can_be_edited_or_read_again(client, session_factory):
    from app.scout.work import profile_inputs_key

    _save_settings(client, session_factory, description="anything im desperate")
    assert client.get("/api/scout").json()["settings"]["work_boundary"] is None
    assert client.put("/api/scout/work", json={"work": "Pharma"}).status_code == 404
    with session_factory() as db:
        saved = db.scalar(select(ScoutSettings))
        saved.work_boundary = {
            "work": "Pharmaceutical brand marketing",
            "titles": ["brand manager"],
            "inputs_key": profile_inputs_key(db, saved.profile_id, saved.description),
            "made_at": "2026-11-10T00:00:00+00:00",
            "edited": False,
        }
        db.commit()
    shown = client.get("/api/scout").json()["settings"]["work_boundary"]
    assert shown == {
        "work": "Pharmaceutical brand marketing",
        "titles": ["brand manager"],
        "edited": False,
        "changed": False,
    }
    assert client.put("/api/scout/work", json={"work": "   "}).status_code == 422
    edited = client.put("/api/scout/work", json={"work": " Medical  affairs "}).json()
    assert edited["work_boundary"]["work"] == "Medical affairs"
    assert (edited["work_boundary"]["edited"], edited["work_boundary"]["changed"]) == (True, False)
    # Saving settings keeps it in the answer, so the page doesn't lose it.
    saved = client.put("/api/scout/settings", json=_payload(description="anything im desperate"))
    assert saved.json()["work_boundary"]["work"] == "Medical affairs"
    client.put("/api/fact-sheet", json={"content": "## Skills\n| Veeva CRM | Claimable |"})
    assert client.get("/api/scout").json()["settings"]["work_boundary"]["changed"] is True
    again = client.delete("/api/scout/work/edit").json()["work_boundary"]
    assert (again["edited"], again["changed"]) == (False, False)
    with session_factory() as db:
        assert db.scalar(select(ScoutSettings)).work_boundary["inputs_key"] == ""


def test_a_run_counts_today_and_only_one_runs_at_once(client, session_factory):
    _save_settings(client, session_factory)
    response = client.post("/api/scout/runs")
    assert response.status_code == 201
    assert response.json()["status"] == "queued"
    assert response.json()["budget_usd"] == 2.0
    allowance = client.get("/api/scout").json()["allowance"]
    assert allowance["runs_today"] == 1
    second = client.post("/api/scout/runs")
    assert second.status_code == 409
    assert "already going" in second.json()["detail"]


def test_worker_stores_leads_and_real_cost(client, session_factory):
    _save_settings(client, session_factory)
    client.put("/api/fact-sheet", json={"content": "## Skills status\n| Python | Claimable |"})
    client.post(
        "/api/postings",
        json={"company": "Known Co", "title": "Ops Engineer", "url": "https://known.io/1"},
    )
    sent: list[dict] = []

    def send(payload):
        sent.append(payload)
        return _result()

    client.post("/api/scout/runs")
    _run_worker(session_factory, send)

    payload = sent[0]
    assert payload["description"] == "Internal tools work with operations teams"
    assert payload["budget_usd"] == 2.0
    assert payload["location"] == "Philadelphia, PA"
    assert payload["fit_mode"] == "wide"
    assert payload["agency_mode"] == "find_employer"
    assert payload["fact_sheet"].startswith("## Skills status")
    assert {"company": "Known Co", "title": "Ops Engineer", "url": "https://known.io/1"} in (
        payload["known_jobs"]
    )

    run = client.get("/api/scout/runs").json()[0]
    assert run["status"] == "done"
    assert "credits_used" not in run
    assert run["cost_usd"] == 0.873
    assert run["engine"] == "companies"
    assert run["target_leads"] == default_limits()["target_leads"]
    assert run["searches"] == 6
    assert run["leads"][0]["company"] == "Acme"
    # Leads track themselves: there's no baseline here, so it isn't marked ready to apply.
    assert run["leads"][0]["posting_id"] is not None
    posting = client.get(f"/api/postings/{run['leads'][0]['posting_id']}").json()
    assert posting["source"] == "SmartSearch"
    assert posting["salary_min"] == 130000
    assert posting["raw_text"].startswith("Internal Tools Engineer at Acme")
    assert "automate finance workflows" in posting["raw_text"]
    assert posting["staged_at"] is None
    assert [step["kind"] for step in run["steps"]] == ["search", "results", "lead"]
    assert client.get("/api/scout").json()["allowance"]["runs_today"] == 1


def test_service_outage_fails_the_run_and_it_doesnt_count(client, session_factory):
    _save_settings(client, session_factory)
    client.post("/api/scout/runs")

    def send(payload):
        raise TailorUnavailable("The search service couldn't be reached.")

    _run_worker(session_factory, send)
    run = client.get("/api/scout/runs").json()[0]
    assert run["status"] == "failed"
    assert "doesn't count against today's runs" in run["stop_message"]
    assert client.get("/api/scout").json()["allowance"]["runs_today"] == 0


def test_leads_track_themselves_ready_to_apply(client, other_client, session_factory):
    _save_settings(client, session_factory)
    tex = (
        "\\begin{document}\n\\section*{Experience}\n\\job{Software Engineer}{Acme}\n"
        "{Jan 2020 -- Present}\n\\begin{itemize}\n\\item Built services with Python and "
        "PostgreSQL.\n\\end{itemize}\n\\end{document}"
    )
    uploaded = client.post(
        "/api/resumes/baselines", files={"file": ("b.tex", tex.encode(), "text/x-tex")}
    )
    assert uploaded.status_code in (200, 201), uploaded.text
    client.post("/api/scout/runs")
    _run_worker(session_factory, lambda payload: _result())
    run = client.get("/api/scout/runs").json()[0]
    lead = run["leads"][0]
    posting = client.get(f"/api/postings/{lead['posting_id']}").json()
    assert posting["staged_resume_id"] == uploaded.json()["id"]
    assert posting["staged_notes"].startswith("Found by Scout.\n\nFit: stretch")
    assert "Posted by an agency: Robert Half" in posting["staged_notes"]
    assert "stop the company hiring you directly" in posting["staged_notes"]
    assert "Lead with:\n- Lead with the internal tools you built" in posting["staged_notes"]
    assert posting["match"] is not None

    # Tracking again, as runs from before did by hand, keeps the one posting.
    path = f"/api/scout/runs/{run['id']}/leads/{lead['id']}/track"
    assert other_client.post(path).status_code == 404
    assert client.post(path).json()["leads"][0]["posting_id"] == lead["posting_id"]
    assert [p["company"] for p in client.get("/api/postings").json()] == ["Acme"]


def test_stale_run_fails_and_doesnt_count(client, session_factory):
    _save_settings(client, session_factory)
    client.post("/api/scout/runs")
    with session_factory() as db:
        run = claim_next(db)
        run.started_at = datetime(2026, 1, 1, tzinfo=UTC)
        db.commit()
        assert fail_stale(db, datetime(2026, 1, 1, 1, tzinfo=UTC)) == 1
    assert client.get("/api/scout/runs").json()[0]["status"] == "failed"


def test_report_endpoint_is_owner_only(client, other_client, session_factory):
    _save_settings(client, session_factory)
    client.post("/api/scout/runs")
    _run_worker(session_factory, lambda payload: _result(cost_breakdown={"web_search": 0.06}))
    run_id = client.get("/api/scout/runs").json()[0]["id"]
    report = client.get(f"/api/scout/runs/{run_id}/report")
    assert report.status_code == 200
    body = report.json()
    assert body["run"]["id"] == run_id
    assert body["costs"][0]["key"] == "web_search"
    assert body["lead_hosts"] == [{"name": "jobs.lever.co", "count": 1}]
    assert body["findings"][0]["title"] == "It found every lead you asked for"
    assert other_client.get(f"/api/scout/runs/{run_id}/report").status_code == 404


def test_agency_notes_say_which_posting_to_apply_through():
    agency_listing = {"agency": "Robert Half", "agency_url": ""}
    assert "often pay less" in lead_notes(agency_listing)
    direct = {"agency": "Robert Half", "agency_url": "https://roberthalf.com/jobs/9"}
    notes = lead_notes(direct)
    assert "Found through a listing by the agency Robert Half" in notes
    assert "https://roberthalf.com/jobs/9" in notes
    assert "apply here directly" in notes
    assert "Agency" not in lead_notes({"agency": "", "fit": "good"})


def test_direct_posting_found_through_an_agency_keeps_its_agency_url(client, session_factory):
    _save_settings(client, session_factory)
    client.post("/api/scout/runs")
    lead = {
        **_result().leads[0].model_dump(),
        "url": "https://boards.greenhouse.io/acme/77",
        "agency_url": "https://roberthalf.com/jobs/9",
    }
    _run_worker(session_factory, lambda payload: _result(leads=[lead]))
    run = client.get("/api/scout/runs").json()[0]
    assert run["leads"][0]["agency"] == "Robert Half"
    assert run["leads"][0]["agency_url"] == "https://roberthalf.com/jobs/9"
    posting = client.get(f"/api/postings/{run['leads'][0]['posting_id']}").json()
    assert posting["url"] == "https://boards.greenhouse.io/acme/77"


def test_lead_states_move_between_leads_and_tracking(client, session_factory):
    _save_settings(client, session_factory)
    client.post("/api/scout/runs")
    _run_worker(session_factory, lambda payload: _result())
    [item] = client.get("/api/scout/leads").json()
    assert item["state"] == "new" and item["application"] is None
    path = f"/api/scout/runs/{item['run_id']}/leads/{item['id']}/state"

    assert client.post(path, json={"state": "later"}).json()["state"] == "later"

    dismissed = client.post(path, json={"state": "dismissed", "reason": "Pay too low"}).json()
    assert dismissed["state"] == "dismissed"
    assert dismissed["decline_reason"] == "Pay too low"
    # The posting Scout made is gone, and the job is hidden for runs and listings.
    assert dismissed["posting_id"] is None
    assert client.get("/api/postings").json() == []
    hidden = client.get("/api/listings/hidden").json()
    assert [(h["company"], h["title"]) for h in hidden] == [("Acme", "Internal Tools Engineer")]

    sent: list[dict] = []
    client.post("/api/scout/runs")
    _run_worker(session_factory, lambda payload: sent.append(payload) or _result(leads=[]))
    assert sent[0]["declined"] == [
        {"company": "Acme", "title": "Internal Tools Engineer", "reason": "Pay too low"}
    ]

    restored = client.post(path, json={"state": "new"}).json()
    assert restored["posting_id"] is not None
    assert client.get("/api/listings/hidden").json() == []

    client.post(
        "/api/applications",
        json={
            "company": "Acme",
            "role_title": "Internal Tools Engineer",
            "status": "applied",
            "applied_on": "2026-11-03",
            "posting_id": restored["posting_id"],
        },
    )
    [tracked] = client.get("/api/scout/leads").json()
    assert tracked["application"]["status"] == "applied"
    # Other pages badge what Scout found, through the posting it made.
    assert [p["from_scout"] for p in client.get("/api/postings").json()] == [True]
    [application] = client.get("/api/applications").json()
    assert application["from_scout"] is True
    refused = client.post(path, json={"state": "dismissed", "reason": "x"})
    assert refused.status_code == 409


def test_passed_pages_come_only_from_runs_with_the_same_brief(client, session_factory):
    from app.models import ScoutRun
    from app.scout.leads import passed_pages

    _make_premium(session_factory)
    with session_factory() as db:
        user = db.scalar(select(User).where(User.email == "owner@example.com"))
        brief = {"description": "Internal tools", "fit_mode": "wide", "location": "Philly"}

        def run(settings, fetched, leads, when):
            row = ScoutRun(
                user_id=user.id,
                status="done",
                settings=settings,
                usage={},
                leads=leads,
                steps=[{"kind": "fetch", "detail": url, "request": 1} for url in fetched],
                created_at=when,
            )
            db.add(row)
            db.flush()
            return row

        run(
            brief,
            ["https://a.io/kept", "https://a.io/passed", "https://a.io/source"],
            [{"url": "https://a.io/kept", "sources": ["https://a.io/source"]}],
            datetime(2026, 11, 1, tzinfo=UTC),
        )
        # Judged under a different fit setting, so what it passed on may fit now.
        run(
            {**brief, "fit_mode": "close"},
            ["https://a.io/close-only"],
            [],
            datetime(2026, 11, 2, tzinfo=UTC),
        )
        current = run(brief, [], [], datetime(2026, 11, 3, tzinfo=UTC))
        db.commit()
        assert passed_pages(db, current, 200) == ["https://a.io/passed"]

        # A run from before title phrases had the old values, and the same titles still match.
        run(
            {**brief, "title_families": ["internal_tools", "automation"]},
            ["https://a.io/old-titles"],
            [],
            datetime(2026, 11, 4, tzinfo=UTC),
        )
        run(
            {**brief, "title_families": ["internal tools", "clinical systems"]},
            ["https://a.io/other-titles"],
            [],
            datetime(2026, 11, 5, tzinfo=UTC),
        )
        titled = {**brief, "title_families": ["Automation Engineer", "internal tools"]}
        current = run(titled, [], [], datetime(2026, 11, 6, tzinfo=UTC))
        db.commit()
        assert passed_pages(db, current, 200) == ["https://a.io/old-titles"]


def test_an_expired_listing_is_put_away_without_teaching_runs(client, session_factory):
    _save_settings(client, session_factory)
    client.post("/api/scout/runs")
    _run_worker(session_factory, lambda payload: _result())
    [item] = client.get("/api/scout/leads").json()
    path = f"/api/scout/runs/{item['run_id']}/leads/{item['id']}/state"

    expired = client.post(path, json={"state": "expired", "reason": "ignored"}).json()
    assert expired["state"] == "expired"
    assert expired["decline_reason"] == ""
    assert expired["posting_id"] is None
    assert client.get("/api/postings").json() == []
    assert len(client.get("/api/listings/hidden").json()) == 1

    # Later runs skip the job as known, but aren't told to avoid jobs like it.
    sent: list[dict] = []
    client.post("/api/scout/runs")
    _run_worker(session_factory, lambda payload: sent.append(payload) or _result(leads=[]))
    assert sent[0]["declined"] == []
    assert any(job["url"] for job in sent[0]["known_jobs"])

    restored = client.post(path, json={"state": "new"}).json()
    assert restored["posting_id"] is not None
    assert client.get("/api/listings/hidden").json() == []


def test_a_job_put_away_in_a_newer_run_isn_t_listed_again_from_an_older_one(
    client, session_factory
):
    """Its older copy kept the removed posting's id, so the page asked for it and got a 404."""
    _save_settings(client, session_factory)
    client.post("/api/scout/runs")
    _run_worker(session_factory, lambda payload: _result())
    [older] = client.get("/api/scout/leads").json()
    with session_factory() as db:
        old_run = db.get(ScoutRun, uuid.UUID(older["run_id"]))
        # A later run found the same job, as an older lead from before leads shared postings.
        copy = {**old_run.leads[0], "id": "newer-copy"}
        db.add(
            ScoutRun(
                user_id=old_run.user_id,
                profile_id=old_run.profile_id,
                status=old_run.status,
                leads=[copy],
                steps=[],
                created_at=old_run.created_at + timedelta(minutes=5),
            )
        )
        db.commit()
    [newer] = client.get("/api/scout/leads").json()
    assert newer["id"] == "newer-copy"

    path = f"/api/scout/runs/{newer['run_id']}/leads/{newer['id']}/state"
    assert client.post(path, json={"state": "expired"}).status_code == 200

    [listed] = client.get("/api/scout/leads").json()
    assert (listed["id"], listed["state"], listed["posting_id"]) == ("newer-copy", "expired", None)
    assert client.get(f"/api/postings/{older['posting_id']}").status_code == 404


def test_a_lead_whose_posting_was_deleted_is_listed_without_it(client, session_factory):
    _save_settings(client, session_factory)
    client.post("/api/scout/runs")
    _run_worker(session_factory, lambda payload: _result())
    [lead] = client.get("/api/scout/leads").json()
    assert client.delete(f"/api/postings/{lead['posting_id']}").status_code == 204

    [listed] = client.get("/api/scout/leads").json()
    assert (listed["id"], listed["posting_id"]) == (lead["id"], None)


def test_changing_a_lead_listed_under_a_newer_run_still_answers(client, session_factory):
    """The lead list shows a job once, so an older run's copy of it isn't in that list."""
    _save_settings(client, session_factory)
    client.post("/api/scout/runs")
    _run_worker(session_factory, lambda payload: _result())
    [older] = client.get("/api/scout/leads").json()
    second = _result().leads[0].model_dump(mode="json")
    second.update(url="https://jobs.lever.co/acme/456", title="Platform Engineer")
    client.post("/api/scout/runs")
    _run_worker(session_factory, lambda payload: _result(leads=[second]))
    with session_factory() as db:
        newer = db.scalars(select(ScoutRun).where(ScoutRun.id != uuid.UUID(older["run_id"]))).one()
        # The newer run found the same posting, so the list shows its copy only.
        newer.leads = [{**lead, "posting_id": older["posting_id"]} for lead in newer.leads]
        db.commit()
    assert [item["run_id"] for item in client.get("/api/scout/leads").json()] == [str(newer.id)]

    path = f"/api/scout/runs/{older['run_id']}/leads/{older['id']}/state"
    response = client.post(path, json={"state": "later"})

    assert response.status_code == 200, response.text
    assert response.json()["state"] == "later"
    assert response.json()["run_id"] == older["run_id"]


def _active_run(user_id, status=TailorStatus.QUEUED.value) -> ScoutRun:
    return ScoutRun(user_id=user_id, status=status, leads=[], steps=[])


def test_the_database_holds_one_active_run_per_account(client, other_client, session_factory):
    with session_factory() as db:
        mine = db.scalar(select(User.id).where(User.email == "owner@example.com"))
        theirs = db.scalar(select(User.id).where(User.email == "other@example.com"))
        db.add_all([_active_run(mine), _active_run(theirs, TailorStatus.RUNNING.value)])
        db.add_all([_active_run(mine, TailorStatus.DONE.value), _active_run(mine, "failed")])
        db.commit()
        db.add(_active_run(mine, TailorStatus.RUNNING.value))
        with pytest.raises(IntegrityError):
            db.commit()


def test_two_run_requests_at_once_queue_only_one(client, session_factory, monkeypatch):
    """The second request passed the active-run check before the first one was saved."""
    _save_settings(client, session_factory)
    real_runs_today = scout_router.runs_today

    def first_request_lands_meanwhile(db, user, now):
        db.add(_active_run(user.id))
        db.commit()
        return real_runs_today(db, user, now)

    monkeypatch.setattr(scout_router, "runs_today", first_request_lands_meanwhile)
    response = client.post("/api/scout/runs")

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "A run is already going. Wait for it to end"
    with session_factory() as db:
        assert len(db.scalars(select(ScoutRun)).all()) == 1


def test_lead_changes_lock_their_run_row():
    """SQLite has no row locks, so this checks the query Postgres is sent."""
    user = User(id=uuid.uuid4(), email="x@example.com", password_hash="x")
    run_id = uuid.uuid4()

    def sql(for_update):
        query = scout_router.run_query(user, run_id, for_update)
        return str(query.compile(dialect=postgresql.dialect()))

    assert sql(True).rstrip().endswith("FOR UPDATE")
    assert "FOR UPDATE" not in sql(False)


def test_an_outage_mid_run_records_the_cost_but_doesnt_count(client, session_factory):
    _save_settings(client, session_factory)
    client.post("/api/scout/runs")
    spent = {
        "model": "pipeline",
        "cost_usd": 0.03,
        "cost_breakdown": {"output": 0.03},
        "usage": {"output_tokens": 300},
        "request_costs": [{"request": 1, "output_tokens": 300, "cost_usd": 0.03}],
    }

    def down(payload):
        raise TailorUnavailable("The search service couldn't be reached.", spent)

    _run_worker(session_factory, down)
    with session_factory() as db:
        run = db.scalar(select(ScoutRun))
        assert run.status == "failed"
        assert run.cost_usd == pytest.approx(0.03)
        assert run.usage == {"output_tokens": 300}
        assert len(run.request_costs) == 1


def test_stale_runs_get_the_longest_time_limit_when_theirs_isnt_known():
    from app.scout.limits import LIMITS
    from app.scout.worker import default_minutes

    longest = next(spec.maximum for spec in LIMITS if spec.key == "max_minutes")
    assert default_minutes() == longest


def test_the_run_report_doesn_t_quote_a_search_price():
    """The price lives in tailor's settings, so a figure here would go stale."""
    from app.scout.report import _COST_TIPS

    assert "$" not in _COST_TIPS["web_search"]


def test_lead_lists_read_only_the_run_columns_they_use(client, session_factory, queries):
    from app.scout.leads import declined_jobs

    _save_settings(client, session_factory)
    client.post("/api/scout/runs")
    _run_worker(session_factory, lambda payload: _result())
    queries.clear()

    assert len(client.get("/api/scout/leads").json()) == 1
    with session_factory() as db:
        user_id = db.scalar(select(User.id).where(User.email == "owner@example.com"))
        declined_jobs(db, user_id, 50)

    heavy = ("request_costs", "cost_breakdown", "steps", "usage")
    assert [
        column for column in heavy if any(f"smartsearch_runs.{column}" in sql for sql in queries)
    ] == []


def test_lead_links_must_be_web_links_before_they_re_stored():
    """A lead's links are rendered as hrefs, so a javascript: link must never get through."""
    lead = _result().leads[0].model_dump(mode="json")
    result = _result(
        leads=[
            {
                **lead,
                "agency_url": "javascript:alert(1)",
                "company_website": "data:text/html,<script>x</script>",
                "company_linkedin": "https://www.linkedin.com/company/acme",
                "sources": ["https://acme.io/about", "JavaScript:alert(1)", "ftp://acme.io/x"],
            },
            {**lead, "url": "javascript:alert(document.cookie)", "title": "Bad link"},
        ]
    )

    [kept] = result.leads
    assert kept.url == "https://jobs.lever.co/acme/123"
    assert kept.agency_url == ""
    assert kept.company_website == ""
    assert kept.company_linkedin == "https://www.linkedin.com/company/acme"
    assert kept.sources == ["https://acme.io/about"]


def test_a_crash_during_a_run_fails_it_and_it_doesnt_count(client, session_factory):
    from app.scout.worker import _run_safely

    _save_settings(client, session_factory)
    client.post("/api/scout/runs")

    def crashes(*args, **kwargs):
        raise RuntimeError("a bug in the worker")

    with (
        session_factory() as db,
        patch.object(scout_worker, "run_companies", crashes),
    ):
        run = claim_next(db)
        _run_safely(db, run, UNUSED_STAGES)

    [run] = client.get("/api/scout/runs").json()
    assert run["status"] == "failed"
    assert "doesn't count against today's runs" in run["stop_message"]
    # It isn't counted, so another run can start.
    assert client.post("/api/scout/runs").status_code == 201


def _finished_run(user_id, created, status=TailorStatus.DONE.value) -> ScoutRun:
    return ScoutRun(user_id=user_id, status=status, created_at=created, leads=[], steps=[])


def test_daily_runs_count_today_utc_and_not_failed_runs(client, session_factory):
    now = datetime(2026, 11, 15, 12, tzinfo=UTC)
    with session_factory() as db:
        user = db.scalar(select(User).where(User.email == "owner@example.com"))
        db.add_all(
            [
                _finished_run(user.id, datetime(2026, 11, 14, 23, 59, tzinfo=UTC)),
                _finished_run(user.id, datetime(2026, 11, 15, 0, 0, tzinfo=UTC)),
                _finished_run(user.id, datetime(2026, 11, 15, 9, tzinfo=UTC), "failed"),
            ]
        )
        db.commit()
        assert scout_router.runs_today(db, user, now) == 1


def test_the_daily_cap_stops_a_run_with_a_clear_message(client, session_factory, set_config):
    _save_settings(client, session_factory)
    set_config("smartsearch_daily_runs", 1)
    with session_factory() as db:
        user = db.scalar(select(User).where(User.email == "owner@example.com"))
        db.add(_finished_run(user.id, datetime.now(UTC)))
        db.commit()
    refused = client.post("/api/scout/runs")
    assert refused.status_code == 429
    assert refused.json()["detail"] == (
        "You've used all 1 Scout runs for today. They reset at midnight UTC."
    )
    allowance = client.get("/api/scout").json()["allowance"]
    assert (allowance["runs_today"], allowance["daily_runs"]) == (1, 1)


def test_a_user_with_limits_overridden_runs_past_the_cap(client, session_factory, set_config):
    _save_settings(client, session_factory)
    set_config("smartsearch_daily_runs", 0)
    with session_factory() as db:
        db.scalar(select(User).where(User.email == "owner@example.com")).override_limits = True
        db.commit()
    assert client.post("/api/scout/runs").status_code == 201
    assert client.get("/api/scout").json()["allowance"]["daily_runs"] is None


def test_a_run_keeps_the_dollar_ceiling_it_was_queued_with(client, session_factory, set_config):
    _save_settings(client, session_factory)
    set_config("smartsearch_run_max_usd", 0.75)
    client.post("/api/scout/runs")
    # Raised after it was queued, which doesn't change the queued run.
    set_config("smartsearch_run_max_usd", 5.0)
    sent: list[dict] = []
    _run_worker(session_factory, lambda payload: sent.append(payload) or _result())
    assert sent[0]["budget_usd"] == 0.75
    assert client.get("/api/scout/runs").json()[0]["budget_usd"] == 0.75


def test_limits_are_saved_as_set_within_their_ranges():
    for limits in (default_limits(), fit_limits({"target_leads": 60, "max_pages": 300})):
        assert limit_errors(limits) == []


def test_the_page_gets_each_engines_past_cost(client, session_factory):
    with session_factory() as db:
        user = db.scalar(select(User).where(User.email == "owner@example.com"))
        for engine, leads, cost in (("companies", 23, 0.089), ("companies", 25, 0.146)):
            db.add(
                ScoutRun(
                    user_id=user.id,
                    status="done",
                    cost_usd=cost,
                    pages=150,
                    settings={"engine": engine},
                    leads=[{"id": str(index)} for index in range(leads)],
                    steps=[],
                )
            )
        db.commit()
    history = client.get("/api/scout").json()["history"]
    assert history == {"companies": {"runs": 2, "leads": 48, "pages": 300, "cost_usd": 0.235}}


def _result_at(url: str) -> ScoutServiceResult:
    result = _result()
    result.leads[0].url = url
    return result


def test_a_run_can_be_labelled_and_its_leads_carry_the_label(client, session_factory):
    _save_settings(client, session_factory)
    started = client.post("/api/scout/runs", json={"label": "  Philly   remote "})
    assert started.status_code == 201
    assert started.json()["label"] == "Philly remote"
    _run_worker(session_factory, lambda payload: _result())

    [item] = client.get("/api/scout/leads").json()
    assert item["label"] == "Philly remote"
    assert client.get("/api/scout/runs").json()[0]["label"] == "Philly remote"


def test_runs_without_a_label_have_none(client, session_factory):
    _save_settings(client, session_factory)
    # No body, as the page sent before labels.
    assert client.post("/api/scout/runs").json()["label"] is None
    _run_worker(session_factory, lambda payload: _result_at("https://jobs.lever.co/acme/1"))
    # A blank label counts as none.
    assert client.post("/api/scout/runs", json={"label": "   "}).json()["label"] is None
    _run_worker(session_factory, lambda payload: _result_at("https://jobs.lever.co/acme/2"))

    items = client.get("/api/scout/leads").json()
    assert [item["label"] for item in items] == [None, None]


def test_each_lead_keeps_the_label_of_its_own_run(client, session_factory, set_config):
    _save_settings(client, session_factory)
    set_config("smartsearch_daily_runs", 3)
    client.post("/api/scout/runs", json={"label": "Fintech"})
    _run_worker(session_factory, lambda payload: _result_at("https://jobs.lever.co/acme/1"))
    client.post("/api/scout/runs")
    _run_worker(session_factory, lambda payload: _result_at("https://jobs.lever.co/acme/2"))
    client.post("/api/scout/runs", json={"label": "Logistics"})
    _run_worker(session_factory, lambda payload: _result_at("https://jobs.lever.co/acme/3"))

    items = client.get("/api/scout/leads").json()
    by_url = {item["url"]: item["label"] for item in items}
    assert by_url == {
        "https://jobs.lever.co/acme/1": "Fintech",
        "https://jobs.lever.co/acme/2": None,
        "https://jobs.lever.co/acme/3": "Logistics",
    }

    # Changing a lead's state answers with its label too.
    first = next(item for item in items if item["label"] == "Fintech")
    path = f"/api/scout/runs/{first['run_id']}/leads/{first['id']}/state"
    assert client.post(path, json={"state": "later"}).json()["label"] == "Fintech"


def test_a_label_is_limited_in_length(client, session_factory):
    _save_settings(client, session_factory)
    too_long = client.post("/api/scout/runs", json={"label": "x" * 61})
    assert too_long.status_code == 422
    assert client.post("/api/scout/runs", json={"label": "x" * 60}).status_code == 201


def test_must_use_rule_out_and_levels_are_saved_and_reach_the_run(client, session_factory):
    _make_premium(session_factory)
    # Never saved: the description's technologies stand in, and a "no" rules one out.
    described = _payload(description="Laravel and Vue work, no Java please")
    saved = client.put("/api/scout/settings", json=described).json()
    assert saved["must_have"] == ["Laravel", "Vue"]
    assert saved["rule_out"] == ["Java"]
    assert saved["levels"] == []
    assert "Laravel" in client.get("/api/scout").json()["technology_names"]

    chosen = _payload(
        description="Laravel and Vue work",
        must_have=["laravel", " Laravel ", "postgres"],
        rule_out=[],
        levels=["senior", "mid", "senior"],
        engine="agent",
    )
    saved = client.put("/api/scout/settings", json=chosen).json()
    assert saved["must_have"] == ["Laravel", "PostgreSQL"]
    assert saved["rule_out"] == []
    assert saved["levels"] == ["senior", "mid"]
    # One engine, whatever an older page asks for.
    assert saved["engine"] == "companies"
    assert (
        client.put("/api/scout/settings", json={**chosen, "levels": ["wizard"]}).status_code == 422
    )

    # A later description doesn't change lists that were saved.
    client.put("/api/scout/settings", json={**chosen, "description": "Go work"})
    with session_factory() as db:
        row = db.scalar(select(ScoutSettings))
        assert row.must_have == ["Laravel", "PostgreSQL"]

    client.post("/api/scout/runs")
    with session_factory() as db:
        run = db.scalar(select(ScoutRun))
        assert run.settings["engine"] == "companies"
        payload = build_payload(db, run, date(2026, 11, 3))
        assert payload["must_have"] == ["Laravel", "PostgreSQL"]
        assert payload["rule_out"] == []
        assert payload["levels"] == ["senior", "mid"]


def test_a_run_from_before_must_use_reads_it_from_the_description(client, session_factory):
    from app.scout.match import spec_from_payload

    _save_settings(client, session_factory)
    with session_factory() as db:
        user = db.scalar(select(User).where(User.email == "owner@example.com"))
        old = ScoutRun(
            user_id=user.id,
            status=TailorStatus.QUEUED.value,
            settings={"description": "Laravel stacks", "engine": "agent", "limits": {}},
            leads=[],
            steps=[],
        )
        db.add(old)
        db.commit()
        payload = build_payload(db, old, date(2026, 11, 3))
        assert "must_have" not in payload and payload["levels"] == []
        assert spec_from_payload(payload).must_have == ("Laravel",)
