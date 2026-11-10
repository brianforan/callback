"""The company around a rejection: the lines the analysis model reads, the report's company
lines worked out when it's opened, and the rough estimate and repeat companies in Insights."""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import select
from test_reject_analysis import POSTING_TEXT, FakeAnalyzer, _answer, _report, _setup, _work

from app.companies.catalog import CatalogListing, record_listing
from app.models import Company, CompanyResearch, RejectAnalysis, TailorStatus
from app.research.context import (
    load_history,
    posting_lines,
    posting_profile,
    roles_before,
    signal_lines,
    workforce_lines,
)
from app.research.history import CANT_SEE, Opening
from app.schemas import ComparisonOut, EstimateOut

REJECTED = datetime(2026, 11, 1, 12, tzinfo=UTC)
LINK = "https://boards.greenhouse.io/acme/jobs/5001"


def _snapshot(**changes):
    values = {
        "events": [
            {
                "date": "2026-09",
                "kind": "layoff",
                "detail": "Cut 10% of staff",
                "count": 0,
                "percent": 10,
                "source": 0,
            },
            {
                "date": "2026-02",
                "kind": "hiring_freeze",
                "detail": "",
                "count": 0,
                "percent": 0,
                "source": 0,
            },
            {
                "date": "2026-10",
                "kind": "office_opened",
                "detail": "Denver",
                "count": 0,
                "percent": 0,
                "source": 0,
            },
        ],
        "workforce": {
            "typical_years_low": 8,
            "typical_years_high": 12,
            "seniority_mix": "mostly_senior",
            "prior_industries": ["Payments"],
            "prior_employer_kinds": ["big_tech", "startup"],
            "cs_degree_percent": 0,
            "team_locations": [],
            "remote_policy": "hybrid",
            "source": 0,
        },
        "confidence": {"workforce": "estimated"},
        "sources": [
            {"url": "https://acme.com/about", "title": "About", "published_on": "2026-09-01"}
        ],
    }
    values.update(changes)
    return SimpleNamespace(**values)


def _opening(days_before_open, days_before_close=None, engineering=True):
    return Opening(
        job_id=uuid.uuid4(),
        title="Engineer",
        title_key="engineer",
        fingerprint=None,
        engineering=engineering,
        opened=REJECTED - timedelta(days=days_before_open),
        closed=None if days_before_close is None else REJECTED - timedelta(days=days_before_close),
        already_open=False,
        urls=frozenset(),
    )


# --- The model's company section ------------------------------------------------------------


def test_workforce_lines_are_aggregate_and_skip_unknowns():
    assert workforce_lines(_snapshot()) == [
        "Engineers typically have 8 to 12 years of experience",
        "Engineering seniority: mostly senior",
        "Engineers often come from: Payments",
        "Engineers often come from big tech, startups",
    ]
    assert workforce_lines(_snapshot(confidence={"workforce": "unknown"})) == []
    assert workforce_lines(None) == []


def test_signals_name_layoffs_and_freezes_shortly_before_and_falling_openings():
    # Eight engineering roles open 60 days before, three at the rejection.
    found = [_opening(100) for _ in range(3)] + [_opening(100, 20) for _ in range(5)]
    lines = signal_lines(_snapshot(), found, True, REJECTED)
    assert lines[0].startswith("Layoff: Cut 10% of staff, 10% (2026-09, about 61 days before")
    # A freeze nine months earlier, and an office opening, aren't signals.
    assert len(lines) == 2
    assert lines[1] == (
        "Open engineering roles: 8 sixty days before the rejection, 3 at it, down by half or more"
    )


def test_signals_say_when_hiring_can_t_be_seen_or_compared():
    assert signal_lines(None, [], False, REJECTED) == [CANT_SEE]
    short = signal_lines(None, [_opening(20)], True, REJECTED)
    assert short == [
        "Callback hasn't read this company's job board long enough to compare openings."
    ]
    assert roles_before([_opening(20)], REJECTED) is None
    assert roles_before([_opening(90), _opening(90, 10, engineering=False)], REJECTED) == (1, 1)


# --- End to end: rejection, report, Insights ---------------------------------------------------


def _research(session_factory, workforce=None):
    """Acme on the catalog with its own Greenhouse board, and a finished snapshot of it."""
    with session_factory() as db:
        job = record_listing(
            db,
            CatalogListing(
                source="greenhouse",
                url=LINK,
                company="Acme",
                title="Backend Engineer",
                text=POSTING_TEXT,
            ),
        )
        snapshot = _snapshot() if workforce is None else _snapshot(workforce=workforce)
        db.add(
            CompanyResearch(
                company_id=job.company_id,
                status=TailorStatus.DONE.value,
                sources=snapshot.sources,
                profile={"what_it_does": "Payments software.", "industry": "Fintech", "source": 0},
                ownership={},
                funding={},
                size={"headcount": 420, "as_of": "2026-06", "trend": "shrinking", "source": 0},
                events=snapshot.events,
                engineering={},
                workforce=snapshot.workforce,
                interviewing={},
                confidence=snapshot.confidence,
                dropped={},
                usage={},
                cost_usd=0.04,
                created_at=datetime.now(UTC),
                finished_at=datetime.now(UTC),
            )
        )
        db.commit()


def _reject(client, resume_id, url=LINK):
    response = client.post(
        "/api/applications",
        json={
            "company": "Acme",
            "role_title": "Backend Engineer",
            "job_url": url,
            "status": "rejected",
            "resume_id": resume_id,
            "posting_text": POSTING_TEXT,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _answer_with_estimate():
    answer = _answer()
    answer.analysis.comparison = [
        ComparisonOut(measure="years", typical="8 to 12 years", yours="5 years", note="Fewer years")
    ]
    answer.analysis.estimate = EstimateOut(band="possibly", reasons=["A layoff two months before"])
    return answer


def test_the_report_is_written_with_the_company_and_shows_it_now(client, session_factory):
    sent = _setup(client)
    _research(session_factory)
    application = _reject(client, sent["id"])
    analyzer = FakeAnalyzer(_answer_with_estimate())

    _work(session_factory, analyzer)

    payload = analyzer.payloads[0]
    assert payload["resume_years"] is not None
    company = payload["company"]
    assert company["name"] == "Acme"
    assert "Engineers typically have 8 to 12 years of experience" in company["workforce"]
    # Callback has only just read Acme's board, so openings can't be compared yet.
    assert any("long enough" in line for line in company["signals"])

    report = _report(client, application["id"])
    analysis = report["analysis"]
    assert analysis["estimate"] == {"band": "possibly", "reasons": ["A layoff two months before"]}
    assert analysis["comparison"][0]["typical"] == "8 to 12 years"
    lines = {line["label"]: line for line in report["company"]["lines"]}
    assert lines["What it is"]["text"] == "Payments software. (Fintech)"
    assert lines["What it is"]["source_url"] == "https://acme.com/about"
    assert lines["Size"]["text"] == "About 420 people, shrinking"
    assert lines["Size"]["as_of"] == "2026-06"
    assert lines["This job afterwards"]["text"].startswith(
        "Still listed 0 days after the rejection"
    )
    assert (
        lines["This job afterwards"]["source_title"] == "Callback's catalog of Acme's own job board"
    )
    with session_factory() as db:
        stored = db.scalar(select(RejectAnalysis))
        assert stored.company_research_id == db.scalar(select(CompanyResearch.id))


def test_an_unresearched_company_sends_no_company_section(client, session_factory):
    sent = _setup(client)
    _reject(client, sent["id"], url="https://example.com/jobs/9")
    with session_factory() as db:
        # Research was queued at the rejection; leave it unfinished.
        assert db.scalar(select(CompanyResearch.status)) == TailorStatus.QUEUED.value
        db.query(CompanyResearch).delete()
        db.commit()
    analyzer = FakeAnalyzer(_answer())
    _work(session_factory, analyzer)
    assert analyzer.payloads[0]["company"] is None


def test_a_report_from_before_estimates_shows_none(client, session_factory):
    sent = _setup(client)
    application = _reject(client, sent["id"])
    _work(session_factory, FakeAnalyzer(_answer()))
    with session_factory() as db:
        stored = db.scalar(select(RejectAnalysis))
        stored.estimate = {}
        db.commit()
    assert _report(client, application["id"])["analysis"]["estimate"] is None


def test_insights_count_estimates_and_list_companies_that_rejected_twice(client, session_factory):
    sent = _setup(client)
    _research(session_factory)
    _reject(client, sent["id"])
    _reject(client, sent["id"], url="https://boards.greenhouse.io/acme/jobs/5002")
    _work(session_factory, FakeAnalyzer(_answer_with_estimate()))
    _work(session_factory, FakeAnalyzer(_answer()))

    summary = client.get("/api/reject-analyses/summary").json()

    assert summary["estimates"] == {"possibly": 1, "cant_tell": 1}
    [repeat] = summary["repeat_companies"]
    assert repeat["company"] == "Acme"
    assert repeat["rejections"] == 2
    assert repeat["what_it_does"] == "Payments software."
    assert repeat["size"] == "About 420 people"
    assert repeat["recent_events"][0] == "2026-09: Layoff: Cut 10% of staff, 10%"
    with session_factory() as db:
        assert db.scalar(select(Company.name)) == "Acme"


# --- What its own postings ask for ------------------------------------------------------------

POSTINGS = [
    ("Backend Engineer", 4, "Python"),
    ("Senior Backend Engineer", 6, "Python\n- Kafka"),
    ("Senior Platform Engineer", 8, "Python\n- Kafka"),
    ("Staff Engineer", 10, "Python"),
    ("Account Executive", 3, "Salesforce"),
    ("Software Engineering Internship, Summer 2027", 1, "Python"),
]


def _postings(session_factory, postings=POSTINGS):
    """Acme's own Greenhouse board with these postings, and Acme's company id."""
    with session_factory() as db:
        for index, (title, years, stack) in enumerate(postings):
            job = record_listing(
                db,
                CatalogListing(
                    source="greenhouse",
                    url=f"https://boards.greenhouse.io/acme/jobs/{6000 + index}",
                    company="Acme",
                    title=title,
                    text=(
                        f"{title}\n\nWe build payments software for small businesses.\n\n"
                        f"Requirements:\n- {years}+ years of professional experience\n- {stack}\n"
                    ),
                ),
            )
        db.commit()
        return job.company_id


def test_a_postings_profile_reads_its_engineering_postings(session_factory):
    company_id = _postings(session_factory)
    with session_factory() as db:
        boards, listings = load_history(db, company_id)
        profile = posting_profile(db, boards, listings, datetime.now(UTC))

    assert profile is not None
    # The account executive isn't an engineering posting, and the internship is left out.
    assert profile.postings == 4
    assert (profile.years_low, profile.years_high, profile.years_median) == (6, 10, 7)
    assert profile.levels == [("mid", 25), ("senior", 50), ("staff", 25)]
    assert profile.technologies == ["Python", "Kafka"]
    assert posting_lines(profile) == [
        "Its own engineering postings ask for 6 to 10 years of experience (median 7), in the 4 of "
        "4 that state years",
        "Levels its own engineering postings hire at: mid 25%, senior 50%, staff 25%",
        "Technologies its own engineering postings most often require: Python, Kafka",
    ]


def test_too_few_postings_say_nothing(session_factory):
    company_id = _postings(session_factory, POSTINGS[:2])
    with session_factory() as db:
        boards, listings = load_history(db, company_id)
        assert posting_profile(db, boards, listings, datetime.now(UTC)) is None
    assert posting_lines(None) == []


def test_postings_fill_the_company_section_without_research(client, session_factory):
    sent = _setup(client)
    _postings(session_factory)
    application = _reject(client, sent["id"], url="https://boards.greenhouse.io/acme/jobs/6001")
    with session_factory() as db:
        # Research was queued at the rejection; leave it unfinished.
        db.query(CompanyResearch).delete()
        db.commit()
    analyzer = FakeAnalyzer(_answer())

    _work(session_factory, analyzer)

    company = analyzer.payloads[0]["company"]
    assert company["name"] == "Acme"
    assert company["workforce"][0].startswith("Its own engineering postings ask for 6 to 10 years")
    lines = {
        line["label"]: line["text"]
        for line in _report(client, application["id"])["company"]["lines"]
    }
    assert lines["What its postings ask"] == (
        "6 to 10 years of experience (median 7); senior 50%, mid 25%; often Python, Kafka, "
        "from 4 engineering postings"
    )
