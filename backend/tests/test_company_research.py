"""Company research snapshots: the checks before anything is stored, queueing at rejection and
from the drawer, the worker with the tailor service faked, and snapshots surviving a merge."""

import copy
import json
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.app_config import SPECS_BY_KEY
from app.companies.identity import mark_agency, merge_companies, resolve_company
from app.models import Company, CompanyResearch, RejectAnalysis, TailorStatus
from app.research.snapshot_worker import claim_next_research, fail_stale_research, run_research
from app.research.snapshots import clean_research, is_person_source
from app.schemas import CompanyResearchServiceResult, ResearchUsage
from app.workers import TailorUnavailable

TODAY = date(2026, 11, 6)

RAW = {
    "sources": [
        {"url": "https://acme.com/about", "title": "About Acme", "published_on": "2026-01-10"},
        {"url": "https://www.linkedin.com/in/jane-doe", "title": "Jane Doe", "published_on": ""},
        {
            "url": "https://techcrunch.com/acme-series-b",
            "title": "Acme raises",
            "published_on": "2025-03",
        },
        {"url": "https://www.spokeo.com/Jane-Doe", "title": "Jane", "published_on": ""},
        {
            "url": "https://www.linkedin.com/company/acme",
            "title": "Acme on LinkedIn",
            "published_on": "",
        },
    ],
    "profile": {
        "what_it_does": "Payments software for small businesses.",
        "industry": "Fintech",
        "hq_city": "Austin",
        "hq_country": "United States",
        "founded_year": 2015,
        "website_domain": "Acme.com",
        "source": 0,
    },
    "ownership": {"kind": "private_vc", "ticker": "", "exchange": "", "parent": "", "source": 2},
    "funding": {
        "stage": "series_b",
        "total_raised_usd": 60_000_000,
        "source": 2,
        "rounds": [
            {"date": "2025-03", "round": "Series B", "amount_usd": 40_000_000, "source": 2},
            {"date": "2030-01", "round": "Series C", "amount_usd": 90_000_000, "source": 2},
        ],
    },
    "size": {"headcount": 420, "as_of": "2026-06", "trend": "growing", "source": 4},
    "events": [
        {
            "date": "2026-08",
            "kind": "leadership_change",
            "detail": "A new CTO in 2026-08",
            "count": 0,
            "percent": 0,
            "source": 0,
        },
        {
            "date": "2026-02-01",
            "kind": "layoff",
            "detail": "Cut 10% of staff",
            "count": 0,
            "percent": 10,
            "source": 1,
        },
        {
            "date": "2025-11",
            "kind": "layoff",
            "detail": "Email jane@example.com for details",
            "count": 40,
            "percent": 140,
            "source": 0,
        },
        {
            "date": "2025-01",
            "kind": "party",
            "detail": "Holiday party",
            "count": 0,
            "percent": 0,
            "source": 0,
        },
    ],
    "engineering": {
        "stack": [
            {"name": "postgres", "evidence": "blog", "source": 0},
            {"name": "PostgreSQL", "evidence": "github", "source": 0},
            {"name": "Go", "evidence": "other_postings", "source": 9},
        ],
        "eng_blog": True,
        "open_source": False,
        "team_size": 0,
        "source": 0,
    },
    "workforce": {
        "typical_years_low": 8,
        "typical_years_high": 5,
        "seniority_mix": "mostly_senior",
        "prior_industries": ["Payments", "Banking", "Payments"],
        "prior_employer_kinds": ["big_tech", "startup", "unicorn"],
        "cs_degree_percent": 0,
        "team_locations": ["Austin", "Call +1 (512) 555-0100"],
        "remote_policy": "hybrid",
        "source": 3,
    },
    "interviewing": {
        "stages": ["Recruiter screen", "Take-home", "Onsite"],
        "take_home": "yes",
        "typical_weeks": 4,
        "weighs": ["System design"],
        "source": 0,
    },
    "confidence": {
        "profile": "stated",
        "ownership": "stated",
        "funding": "stated",
        "size": "estimated",
        "events": "stated",
        "engineering": "stated",
        "workforce": "estimated",
        "interviewing": "maybe",
    },
}


def _clean(raw=RAW):
    return clean_research(copy.deepcopy(raw), TODAY)


# --- The checks ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "person"),
    [
        ("https://www.linkedin.com/in/jane-doe", True),
        ("https://uk.linkedin.com/pub/jane/1/2/3", True),
        ("https://www.linkedin.com/company/acme", False),
        ("https://www.crunchbase.com/person/jane-doe", True),
        ("https://www.crunchbase.com/organization/acme", False),
        ("https://www.zoominfo.com/p/Jane-Doe/123", True),
        ("https://www.zoominfo.com/c/acme/456", False),
        ("https://rocketreach.co/acme-management_b5c", True),
        ("https://x.com/acme", True),
        ("https://acme.com/team", False),
    ],
)
def test_person_sources(url, person):
    assert is_person_source(url) is person


def test_person_sources_and_the_facts_citing_only_them_are_dropped():
    clean = _clean()
    assert [source["url"] for source in clean.sources] == [
        "https://acme.com/about",
        "https://techcrunch.com/acme-series-b",
        "https://www.linkedin.com/company/acme",
    ]
    # The workforce section cited only the people-search page.
    assert clean.sections["workforce"] == {}
    assert clean.confidence["workforce"] == "unknown"
    # A layoff cited only to a LinkedIn profile is gone; the CTO change, by role, stays.
    assert [event["kind"] for event in clean.sections["events"]] == ["leadership_change", "layoff"]
    assert clean.dropped["person_source"] == 2
    assert clean.dropped["fact_person_source"] == 2


def test_facts_cite_the_kept_sources_by_their_new_index():
    clean = _clean()
    assert clean.sections["ownership"]["source"] == 1
    assert clean.sections["size"]["source"] == 2
    assert clean.sections["size"]["headcount"] == 420


def test_contact_details_are_dropped_from_free_text():
    events = _clean().sections["events"]
    assert events[1]["detail"] == ""
    assert _clean().dropped["contact_details"] == 1


def test_dates_ranges_and_choices():
    clean = _clean()
    funding = clean.sections["funding"]
    # A round dated in the future is dropped; the rest stays.
    assert [item["round"] for item in funding["rounds"]] == ["Series B"]
    assert funding["total_raised_usd"] == 60_000_000
    # Percent over 100 is unknown, an unknown event kind is dropped.
    layoff = clean.sections["events"][1]
    assert (layoff["count"], layoff["percent"]) == (40, 0)
    assert clean.dropped["bad_event"] == 1
    assert clean.dropped["out_of_range"] == 1
    assert clean.sections["profile"]["website_domain"] == "acme.com"
    assert clean.confidence["interviewing"] == "unknown"
    assert clean.confidence["size"] == "estimated"


def test_technologies_use_the_parsers_names_and_cite_a_kept_source():
    stack = _clean().sections["engineering"]["stack"]
    assert stack == [{"name": "PostgreSQL", "evidence": "blog", "source": 0}]


def test_workforce_is_aggregate_and_tidied():
    raw = copy.deepcopy(RAW)
    raw["workforce"]["source"] = 0
    workforce = clean_research(raw, TODAY).sections["workforce"]
    assert (workforce["typical_years_low"], workforce["typical_years_high"]) == (5, 8)
    assert workforce["prior_industries"] == ["Payments", "Banking"]
    assert workforce["prior_employer_kinds"] == ["big_tech", "startup"]
    # A phone number in a list item is dropped, the rest kept.
    assert workforce["team_locations"] == ["Austin"]


def test_long_text_is_cut_and_sources_are_capped():
    raw = copy.deepcopy(RAW)
    raw["profile"]["what_it_does"] = "word " * 200
    raw["sources"] = [
        {"url": f"https://news.example.com/{index}", "title": "", "published_on": ""}
        for index in range(20)
    ]
    clean = clean_research(raw, TODAY)
    assert len(clean.sources) == 15
    assert clean.dropped["too_many_sources"] == 5
    assert len(clean.sections["profile"]["what_it_does"]) == 300


def test_nonsense_keeps_nothing_but_doesnt_fail():
    clean = clean_research({"sources": "nope", "profile": [], "events": [{"kind": "ipo"}]}, TODAY)
    assert clean.sources == []
    assert all(not value for value in clean.sections.values())
    assert set(clean.confidence.values()) == {"unknown"}


# --- Queueing, the worker and the API ---------------------------------------------------------


class FakeResearcher:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.payloads: list[dict] = []

    def __call__(self, payload):
        self.payloads.append(json.loads(json.dumps(payload)))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def _answer(raw=RAW):
    return CompanyResearchServiceResult(
        model="claude-haiku-5-5",
        usage=ResearchUsage(input_tokens=4_000, output_tokens=900, web_search_requests=5),
        cost_usd=0.061,
        research=copy.deepcopy(raw),
        searches=5,
    )


def _apply(
    client, company="Acme", status="rejected", url="https://boards.greenhouse.io/acme/jobs/1"
):
    response = client.post(
        "/api/applications",
        json={
            "company": company,
            "role_title": "Backend Engineer",
            "job_url": url,
            "location": "Remote, US",
            "status": status,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _work(session_factory, researcher):
    with session_factory() as db:
        research = claim_next_research(db)
        assert research is not None
        run_research(db, research, researcher, TODAY)
        return research.id


def _rows(session_factory):
    with session_factory() as db:
        return db.scalar(select(func.count(CompanyResearch.id)))


def _view(client, application_id):
    response = client.get(f"/api/applications/{application_id}/company-research")
    assert response.status_code == 200, response.text
    return response.json()


def test_a_rejection_researches_its_company_and_stores_only_checked_facts(client, session_factory):
    application = _apply(client)
    assert _view(client, application["id"])["active"] == "queued"
    researcher = FakeResearcher(_answer())

    research_id = _work(session_factory, researcher)

    payload = researcher.payloads[0]
    assert payload["company"] == "Acme"
    assert payload["title"] == "Backend Engineer"
    assert payload["location"] == "Remote, US"
    assert "greenhouse:acme (own)" in payload["catalog_sources"]
    view = _view(client, application["id"])
    assert view["active"] is None
    with session_factory() as db:
        assert view["company_id"] == str(db.get(CompanyResearch, research_id).company_id)
    latest = view["latest"]
    assert latest["status"] == "done"
    assert latest["profile"]["what_it_does"] == "Payments software for small businesses."
    assert latest["workforce"] == {}
    assert view["blocked"].startswith("Researched recently")
    with session_factory() as db:
        row = db.scalar(select(CompanyResearch))
        assert row.cost_usd == pytest.approx(0.061)
        assert row.usage["web_search_requests"] == 5
        assert row.dropped["person_source"] == 2
        stored = json.dumps([row.sources, row.events, row.profile])
        # Nothing about the person who applied is kept on the shared snapshot.
        assert "Backend Engineer" not in stored and "linkedin.com/in" not in stored


def test_other_statuses_research_nothing(client, session_factory):
    _apply(client, status="applied")
    assert _rows(session_factory) == 0


def test_a_recent_snapshot_is_reused_by_every_user(client, other_client, session_factory):
    first = _apply(client)
    _work(session_factory, FakeResearcher(_answer()))
    _apply(other_client)
    assert _rows(session_factory) == 1
    again = client.post(f"/api/applications/{first['id']}/company-research")
    assert again.status_code == 409
    assert "Researched recently" in again.json()["detail"]


def test_an_old_snapshot_is_researched_again_and_kept(client, session_factory):
    first = _apply(client)
    _work(session_factory, FakeResearcher(_answer()))
    with session_factory() as db:
        old = db.scalar(select(CompanyResearch))
        age = timedelta(days=SPECS_BY_KEY["company_research_max_age_days"].default + 1)
        old.finished_at = datetime.now(UTC) - age
        db.commit()
    response = client.post(f"/api/applications/{first['id']}/company-research")
    assert response.status_code == 202, response.text
    assert response.json()["active"] == "queued"
    # The older snapshot stays; a new one is added beside it.
    assert _rows(session_factory) == 2


def test_research_already_waiting_isnt_queued_twice(client, session_factory):
    application = _apply(client, status="applied")
    first = client.post(f"/api/applications/{application['id']}/company-research")
    second = client.post(f"/api/applications/{application['id']}/company-research")
    assert first.status_code == 202 and second.status_code == 202
    assert _rows(session_factory) == 1


def test_agencies_arent_researched(client, session_factory):
    with session_factory() as db:
        mark_agency(db, "Talent Partners")
        db.commit()
    application = _apply(client, company="Talent Partners", url="https://example.com/job/1")
    assert _rows(session_factory) == 0
    assert _view(client, application["id"])["agency"] is True
    response = client.post(f"/api/applications/{application['id']}/company-research")
    assert response.status_code == 422


def test_the_daily_limit_stops_new_snapshots(client, session_factory, set_config):
    set_config("company_research_daily_limit", 1)
    _apply(client, company="Acme")
    second = _apply(client, company="Globex", url="https://example.com/job/2")
    assert _rows(session_factory) == 1
    response = client.post(f"/api/applications/{second['id']}/company-research")
    assert response.status_code == 429
    assert _view(client, second["id"])["blocked"].startswith("You've started 1")


def test_a_failed_call_keeps_its_cost_and_can_be_tried_again(client, session_factory):
    application = _apply(client)
    spent = {"usage": {"input_tokens": 900, "web_search_requests": 3}, "cost_usd": 0.03}
    _work(
        session_factory, FakeResearcher(TailorUnavailable("The model couldn't be reached.", spent))
    )

    view = _view(client, application["id"])
    assert view["latest"] is None
    assert "couldn't be reached" in view["failure"]
    assert view["blocked"] is None
    with session_factory() as db:
        row = db.scalar(select(CompanyResearch))
        assert (row.status, row.cost_usd) == (TailorStatus.FAILED.value, 0.03)
    assert client.post(f"/api/applications/{application['id']}/company-research").status_code == 202


def test_research_left_running_is_failed(client, session_factory):
    _apply(client)
    with session_factory() as db:
        research = claim_next_research(db)
        research.started_at = datetime.now(UTC) - timedelta(hours=2)
        db.commit()
        assert fail_stale_research(db, datetime.now(UTC), 900) == 1
        assert db.scalar(select(CompanyResearch.status)) == TailorStatus.FAILED.value


def test_snapshots_follow_a_merged_company(client, session_factory):
    _apply(client)
    _work(session_factory, FakeResearcher(_answer()))
    with session_factory() as db:
        acme = db.scalar(select(Company).where(Company.name == "Acme"))
        other = resolve_company(db, "Acme Payments Holdings").company
        db.commit()
        merge_companies(db, other, acme)
        db.commit()
        assert db.scalar(select(CompanyResearch.company_id)) == other.id


# --- One starting point for a report ----------------------------------------------------------


def _forget(session_factory):
    """As a rejection from before reports and research existed: neither was ever queued."""
    with session_factory() as db:
        db.query(RejectAnalysis).delete()
        db.query(CompanyResearch).delete()
        db.commit()


def test_analyze_researches_the_company_first(client, session_factory):
    application = _apply(client)
    _forget(session_factory)

    response = client.post(f"/api/applications/{application['id']}/reject-analysis")

    assert response.status_code == 202, response.text
    assert response.json()["researching"] is True
    assert _rows(session_factory) == 1
    # The worker takes the research before the report.
    researcher = FakeResearcher(_answer())
    _work(session_factory, researcher)
    status = client.get(f"/api/applications/{application['id']}/reject-analysis").json()
    assert status["researching"] is False
    assert status["analysis"]["status"] == "queued"


def test_analyze_them_researches_each_company_without_a_report(client, session_factory):
    _apply(client, company="Acme")
    _apply(client, company="Globex", url="https://example.com/jobs/2")
    _forget(session_factory)
    # One already has a report, so its company isn't researched again for it.
    reported = _apply(client, company="Initech", url="https://example.com/jobs/3")
    with session_factory() as db:
        db.query(CompanyResearch).delete()
        db.commit()

    response = client.post("/api/reject-analyses/backfill")

    assert response.json()["queued"] == 2
    with session_factory() as db:
        names = set(
            db.scalars(
                select(Company.name).join(CompanyResearch, CompanyResearch.company_id == Company.id)
            )
        )
    assert names == {"Acme", "Globex"}
    assert reported["status"] == "rejected"


def test_run_again_renews_an_old_snapshot_first(client, session_factory):
    application = _apply(client)
    _work(session_factory, FakeResearcher(_answer()))
    old = datetime.now(UTC) - timedelta(
        days=SPECS_BY_KEY["company_research_max_age_days"].default + 2
    )
    with session_factory() as db:
        db.scalar(select(CompanyResearch)).finished_at = old
        analysis = db.scalar(select(RejectAnalysis))
        analysis.status = TailorStatus.DONE.value
        analysis.started_at = old
        analysis.finished_at = old
        db.commit()

    response = client.post(f"/api/applications/{application['id']}/reject-analysis")

    assert response.status_code == 202, response.text
    assert response.json()["researching"] is True
    assert _rows(session_factory) == 2
