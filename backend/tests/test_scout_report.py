from datetime import UTC, datetime

from app.models import ScoutRun
from app.scout.report import build_report


def _step(kind: str, detail: str, request: int = 1) -> dict:
    return {"kind": kind, "detail": detail, "request": request}


def _run(**changes) -> ScoutRun:
    values = {
        "status": "done",
        "settings": {
            "description": "Internal tools",
            "limits": {
                "target_leads": 10,
                "dry_streak": 4,
                "max_searches": 15,
                "max_pages": 25,
                "max_minutes": 10,
            },
            "budget_usd": 2.0,
        },
        "cost_usd": 1.44,
        "searches": 12,
        "pages": 3,
        "stop_code": "finished",
        "stop_message": "The agent finished: it found no more jobs worth recording.",
        "cost_breakdown": {
            "cache_write": 0.84,
            "cache_read": 0.37,
            "web_search": 0.12,
            "output": 0.11,
        },
        "leads": [
            {"url": "https://jobs.lever.co/acme/1", "sources": []},
            {"url": "https://jobgether.com/offer/2", "sources": ["https://gray.tv/careers"]},
        ],
        "steps": [
            _step("search", 'site:jobs.lever.co "internal tools"'),
            _step("search", 'site:jobs.lever.co "internal tools"'),
            _step("results", "9 results, 9 new"),
            _step("results", "9 results, 0 new"),
            _step("fetch", "https://jobs.lever.co/acme/1/"),
            _step("fetch", "https://gray.tv/careers"),
            _step("fetch", "https://other.io/job"),
            _step("lead", "Acme, Engineer"),
            _step("skipped", "Known Co, Ops: Not recorded: the person already has this job."),
            _step("error", "Search failed: max_uses_exceeded", 2),
            _step("note", "Asked to keep going: You've recorded 1 of 10", 2),
            _step("stop", "The agent finished.", 3),
        ],
        "started_at": datetime(2026, 11, 3, 21, 31, 16, tzinfo=UTC),
        "finished_at": datetime(2026, 11, 3, 21, 33, 29, tzinfo=UTC),
    }
    values.update(changes)
    return ScoutRun(**values)


def _titles(report) -> list[str]:
    return [finding.title for finding in report.findings]


def test_report_reads_limits_costs_queries_and_pages():
    report = build_report(_run())
    assert report.minutes_used == 2.2
    assert report.requests == 3
    assert report.nudges == 1
    assert [limit.key for limit in report.limits] == [
        "leads",
        "searches",
        "pages",
        "minutes",
        "spend",
    ]
    assert not any(limit.stopped_run for limit in report.limits)
    assert report.costs[0].key == "cache_write"
    assert round(sum(part.share for part in report.costs), 2) == 1.0
    assert report.cost_per_lead_usd == 0.72
    assert [(q.results, q.new, q.repeated) for q in report.queries] == [
        (9, 9, False),
        (9, 0, True),
    ]
    # A trailing slash or a page that backs a lead still counts as becoming one.
    assert [page.became_lead for page in report.pages] == [True, True, False]


def test_findings_explain_the_run():
    report = build_report(_run())
    titles = _titles(report)
    assert titles[0] == "The agent ended the run itself, with budget left"
    assert "It still had 3 searches, 22 page reads when" in report.findings[0].detail
    assert "asked to keep going 1 time" in report.findings[0].detail
    cost = next(f for f in report.findings if f.title.startswith("About $0.72"))
    # $0.72 a lead, so 10 leads would cost about $7.20: over the $2.00 ceiling.
    assert cost.level == "warning"
    assert "would cost about $7.20, more than this run's $2.00 spending ceiling" in cost.detail
    assert "58% of the cost was adding pages and results to the run" in titles
    assert "1 lead came from job aggregators" in titles
    assert "1 lead came straight from a company's hiring system" in titles
    assert "Some searches were wasted" in titles
    refused = next(f for f in report.findings if f.title.startswith("It tried to keep"))
    assert "you already have the job" in refused.detail
    assert "It reached a step's search or page cap 1 time" in titles


def test_a_run_stopped_by_a_limit_names_it():
    report = build_report(
        _run(stop_code="max_searches", stop_message="Used all 15 web searches.", searches=15)
    )
    assert report.findings[0].title == "It stopped at your web searches limit"
    assert "raise the web searches limit" in report.findings[0].detail
    assert next(limit for limit in report.limits if limit.key == "searches").stopped_run


def test_a_run_stopped_by_its_spending_ceiling_says_who_sets_it():
    report = build_report(
        _run(stop_code="budget", stop_message="Reached this run's spending ceiling ($2.00).")
    )
    assert report.findings[0].title == "It reached the most one run may spend"
    assert "an admin sets" in report.findings[0].detail
    assert next(limit for limit in report.limits if limit.key == "spend").stopped_run


def test_a_failed_run_with_nothing_recorded():
    report = build_report(
        _run(
            status="failed",
            stop_code="failed",
            stop_message="The search service couldn't be reached. Nothing was charged.",
            cost_usd=0.0,
            cost_breakdown={},
            leads=[],
            steps=[],
            finished_at=None,
        )
    )
    assert report.findings[0].level == "warning"
    assert report.costs == []
    assert report.minutes_used is None
    assert report.cost_per_lead_usd is None
