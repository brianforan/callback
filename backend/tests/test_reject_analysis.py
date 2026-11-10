"""Rejection analysis: queued on every rejection, graded in code against the resume sent and the
fact sheet, written by the tailor service (faked here), and added up across rejections."""

import json
import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from test_tailoring import BASELINE_TEX, FACT_SHEET, resume_tex

from app.models import Application, RejectAnalysis, TailorStatus, User
from app.research.analysis import furthest_stage, technology_gaps
from app.research.analysis_worker import (
    claim_next_analysis,
    fail_stale_analyses,
    run_analysis,
)
from app.schemas import (
    ExperienceGapOut,
    FocusAreaOut,
    RejectAnalysisFieldsIn,
    RejectAnalysisServiceResult,
    RequirementCheck,
    TailorUsage,
)
from app.tailoring.rules import parse_skills_status
from app.workers import TailorUnavailable

TODAY = date(2026, 11, 3)

POSTING_TEXT = """Backend Engineer

Requirements:
- 3+ years of Python
- Kafka and Redis
- Terraform
- GraphQL APIs

You'll own your services in production, including an on-call rotation.
"""

OTHER_BASELINE_TEX = resume_tex(
    "Built GraphQL APIs for partner apps.",
    "Moved processing to queue workers.",
)


def _answer(focus: str = "Kafka", area: str = "ownership_and_impact"):
    return RejectAnalysisServiceResult(
        model="claude-haiku-5-5",
        usage=TailorUsage(input_tokens=40, output_tokens=300, cache_creation_input_tokens=900),
        cost_usd=0.0021,
        analysis=RejectAnalysisFieldsIn(
            summary="A reasonable match. Kafka and on-call ownership most likely separated you.",
            strengths=["Python services", "AWS Lambda"],
            experience_gaps=[
                ExperienceGapOut(
                    area=area,
                    need="On-call for production services",
                    evidence="nothing",
                    suggestion="Join your team's rotation.",
                )
            ],
            focus_areas=[
                FocusAreaOut(
                    kind="technology",
                    title=focus,
                    why="Required here and common in similar postings.",
                    action="Build a small consumer service.",
                )
            ],
        ),
    )


class FakeAnalyzer:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.payloads: list[dict] = []

    def __call__(self, payload):
        self.payloads.append(json.loads(json.dumps(payload)))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def _baseline(client, tex=BASELINE_TEX, label="Backend"):
    response = client.post(
        "/api/resumes/baselines",
        files={"file": ("resume.tex", tex.encode(), "application/x-tex")},
        data={"label": label, "rules": json.dumps({"min_score": 0})},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _setup(client):
    """Two baselines and the fact sheet. Returns the one sent with applications."""
    sent = _baseline(client)
    _baseline(client, OTHER_BASELINE_TEX, label="APIs")
    assert client.put("/api/fact-sheet", json={"content": FACT_SHEET}).status_code == 200
    return sent


def _apply(client, resume_id=None, status="applied", text=POSTING_TEXT, company="Acme"):
    response = client.post(
        "/api/applications",
        json={
            "company": company,
            "role_title": "Backend Engineer",
            "job_url": f"https://jobs.example.com/{uuid.uuid4()}",
            "status": status,
            "resume_id": resume_id,
            "posting_text": text,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _set_status(client, application_id, status):
    response = client.patch(f"/api/applications/{application_id}", json={"status": status})
    assert response.status_code == 200, response.text


def _report(client, application_id):
    response = client.get(f"/api/applications/{application_id}/reject-analysis")
    assert response.status_code == 200, response.text
    return response.json()


def _work(session_factory, analyzer, limit=20, now=None):
    with session_factory() as db:
        analysis = claim_next_analysis(db, now or datetime.now(UTC), limit)
        assert analysis is not None
        run_analysis(db, analysis, analyzer, TODAY)
        return analysis.id


def _rows(session_factory):
    with session_factory() as db:
        return db.scalar(select(func.count(RejectAnalysis.id)))


# --- Queued on every rejection --------------------------------------------------------------


def test_marking_an_application_rejected_queues_one_analysis(client, session_factory):
    application = _apply(client)
    assert _report(client, application["id"])["analysis"] is None

    _set_status(client, application["id"], "rejected")
    report = _report(client, application["id"])
    assert report["analysis"]["status"] == "queued"
    assert report["daily_limit"] == 20

    # Moving it off rejected and back doesn't pay for a second report.
    _set_status(client, application["id"], "applied")
    _set_status(client, application["id"], "rejected")
    assert _rows(session_factory) == 1


def test_only_rejections_are_queued(client, session_factory):
    _apply(client, status="interview")
    _apply(client, status="ghosted")
    assert _rows(session_factory) == 0
    rejected = _apply(client, status="rejected")
    assert _report(client, rejected["id"])["analysis"]["status"] == "queued"
    assert _rows(session_factory) == 1


def test_other_users_cannot_see_or_run_an_analysis(client, other_client):
    application = _apply(client, status="rejected")
    path = f"/api/applications/{application['id']}/reject-analysis"
    assert other_client.get(path).status_code == 404
    assert other_client.post(path).status_code == 404


# --- The report -----------------------------------------------------------------------------


def test_the_report_grades_in_code_and_adds_the_written_part(client, session_factory):
    sent = _setup(client)
    application = _apply(client, resume_id=sent["id"])
    _set_status(client, application["id"], "interview")
    _set_status(client, application["id"], "rejected")
    analyzer = FakeAnalyzer(_answer())

    analysis_id = _work(session_factory, analyzer)

    payload = analyzer.payloads[0]
    assert payload["stage"] == "interview"
    assert payload["resume_label"] == "Backend"
    assert "Python services on AWS Lambda" in payload["resume_text"]
    assert payload["fact_sheet"] == FACT_SHEET
    assert "on-call rotation" in payload["posting"]["text"]
    assert {"Kafka", "Redis", "Python"} <= {check["name"] for check in payload["checklist"]}

    report = _report(client, application["id"])["analysis"]
    assert report["id"] == str(analysis_id)
    assert report["status"] == "done"
    assert report["stage"] == "interview"
    assert report["resume_id"] == sent["id"]
    assert report["score"] is not None
    actions = {gap["name"]: gap["action"] for gap in report["technology_gaps"]}
    # The fact sheet allows Redis, so it's a resume fix.
    assert actions["Redis"] == "add_to_resume"
    # The fact sheet forbids Kafka and Terraform, so they're real gaps.
    assert actions["Kafka"] == "learn"
    assert actions["Terraform"] == "learn"
    # Another baseline shows GraphQL in a role.
    assert actions["GraphQL"] == "borrow"
    assert "APIs" in next(
        gap["note"] for gap in report["technology_gaps"] if gap["name"] == "GraphQL"
    )
    assert "Python" not in actions
    assert report["summary"].startswith("A reasonable match")
    assert report["focus_areas"][0]["title"] == "Kafka"
    assert report["experience_gaps"][0]["area"] == "ownership_and_impact"
    assert report["note"] is None
    with session_factory() as db:
        stored = db.get(RejectAnalysis, analysis_id)
        assert stored.cost_usd == 0.0021
        assert stored.usage["cache_creation_input_tokens"] == 900
        assert stored.model == "claude-haiku-5-5"


def test_with_no_resume_sent_the_best_baseline_is_compared(client, session_factory):
    _setup(client)
    application = _apply(client, status="rejected")
    _work(session_factory, FakeAnalyzer(_answer()))
    report = _report(client, application["id"])["analysis"]
    assert report["resume_label"] in {"Backend", "APIs"}
    assert report["stage"] == "applied"


def test_a_posting_without_text_skips_the_model(client, session_factory):
    sent = _setup(client)
    application = _apply(client, resume_id=sent["id"], status="rejected", text="")
    analyzer = FakeAnalyzer()

    _work(session_factory, analyzer)

    assert analyzer.payloads == []
    report = _report(client, application["id"])["analysis"]
    assert report["status"] == "done"
    assert "no text" in report["note"]
    assert report["summary"] == ""


def test_an_outage_keeps_the_graded_part_and_can_be_retried(client, session_factory):
    sent = _setup(client)
    application = _apply(client, resume_id=sent["id"], status="rejected")
    spent = {"usage": {"input_tokens": 10, "output_tokens": 5}, "cost_usd": 0.0004}
    analyzer = FakeAnalyzer(TailorUnavailable("The model couldn't be reached.", spent))

    analysis_id = _work(session_factory, analyzer)

    status = _report(client, application["id"])
    report = status["analysis"]
    assert report["status"] == "failed"
    assert "couldn't be made: The model couldn't be reached." in report["note"]
    assert {gap["name"] for gap in report["technology_gaps"]} >= {"Kafka", "Redis"}
    assert status["rerun_blocked"] is None
    with session_factory() as db:
        assert db.get(RejectAnalysis, analysis_id).cost_usd == 0.0004

    response = client.post(f"/api/applications/{application['id']}/reject-analysis")
    assert response.status_code == 202
    assert response.json()["analysis"]["status"] == "queued"
    _work(session_factory, FakeAnalyzer(_answer()))
    assert _report(client, application["id"])["analysis"]["status"] == "done"


def test_a_finished_report_runs_again_only_from_the_next_day(client, session_factory):
    sent = _setup(client)
    application = _apply(client, resume_id=sent["id"], status="rejected")
    _work(session_factory, FakeAnalyzer(_answer()))
    path = f"/api/applications/{application['id']}/reject-analysis"

    assert "tomorrow" in _report(client, application["id"])["rerun_blocked"]
    assert client.post(path).status_code == 409

    with session_factory() as db:
        stored = db.scalar(select(RejectAnalysis))
        stored.started_at = datetime.now(UTC) - timedelta(days=1)
        db.commit()
    assert client.post(path).status_code == 202


def test_only_a_rejected_application_can_be_analyzed(client):
    application = _apply(client)
    response = client.post(f"/api/applications/{application['id']}/reject-analysis")
    assert response.status_code == 409


def test_over_the_daily_limit_analyses_wait_for_the_next_day(client, session_factory):
    sent = _setup(client)
    _apply(client, resume_id=sent["id"], status="rejected")
    _apply(client, resume_id=sent["id"], status="rejected", company="Beta")
    now = datetime.now(UTC)
    _work(session_factory, FakeAnalyzer(_answer()), limit=1, now=now)

    with session_factory() as db:
        assert claim_next_analysis(db, now, 1) is None
        tomorrow = claim_next_analysis(db, now + timedelta(days=1), 1)
        assert tomorrow is not None


def test_a_user_with_limits_overridden_isnt_held_to_the_daily_limit(client, session_factory):
    sent = _setup(client)
    _apply(client, resume_id=sent["id"], status="rejected")
    _apply(client, resume_id=sent["id"], status="rejected", company="Beta")
    now = datetime.now(UTC)
    _work(session_factory, FakeAnalyzer(_answer()), limit=1, now=now)
    with session_factory() as db:
        db.scalar(select(User)).override_limits = True
        db.commit()
        assert claim_next_analysis(db, now, 1) is not None


def test_an_abandoned_analysis_fails(client, session_factory):
    _apply(client, status="rejected")
    with session_factory() as db:
        analysis = claim_next_analysis(db, datetime.now(UTC), 20)
        later = datetime.now(UTC) + timedelta(hours=1)
        assert fail_stale_analyses(db, later, timeout_seconds=60) == 1
        assert db.get(RejectAnalysis, analysis.id).status == TailorStatus.FAILED.value


def test_deleting_the_application_deletes_its_analysis(client, session_factory):
    application = _apply(client, status="rejected")
    assert client.delete(f"/api/applications/{application['id']}").status_code == 204
    assert _rows(session_factory) == 0


# --- Across rejections ----------------------------------------------------------------------


def test_the_summary_adds_up_the_profiles_rejections(client, session_factory):
    sent = _setup(client)
    first = _apply(client, resume_id=sent["id"], status="rejected", company="Acme")
    second = _apply(client, resume_id=sent["id"], company="Beta")
    _set_status(client, second["id"], "screening")
    _set_status(client, second["id"], "rejected")
    _work(session_factory, FakeAnalyzer(_answer()))
    _work(session_factory, FakeAnalyzer(_answer(focus="Redis")))
    # A rejection from before the feature has no analysis.
    old = _apply(client, resume_id=sent["id"], status="rejected", company="Gamma")
    with session_factory() as db:
        db.delete(
            db.scalar(
                select(RejectAnalysis).where(RejectAnalysis.application_id == uuid.UUID(old["id"]))
            )
        )
        db.commit()

    summary = client.get("/api/reject-analyses/summary").json()

    assert summary["rejections"] == 3
    assert summary["analyzed"] == 2
    assert summary["not_analyzed"] == 1
    assert summary["waiting"] == 0
    assert summary["stages"] == {"applied": 2, "screening": 1, "interview": 0, "offer": 0}
    kafka = next(row for row in summary["technologies"] if row["name"] == "Kafka")
    assert kafka["total"] == 2
    assert kafka["action"] == "learn"
    redis = next(row for row in summary["technologies"] if row["name"] == "Redis")
    assert redis["action"] == "add_to_resume"
    area = summary["experience"][0]
    assert area["area"] == "ownership_and_impact"
    assert area["count"] == 2
    assert {example["company"] for example in area["examples"]} == {"Acme", "Beta"}
    assert {row["top_focus"] for row in summary["recent"]} == {"Kafka", "Redis"}
    assert {row["application_id"] for row in summary["recent"]} == {first["id"], second["id"]}

    backfill = client.post("/api/reject-analyses/backfill")
    assert backfill.json() == {"queued": 1}
    assert client.get("/api/reject-analyses/summary").json()["waiting"] == 1
    assert client.post("/api/reject-analyses/backfill").json() == {"queued": 0}


def test_the_summary_is_empty_with_no_rejections(client):
    summary = client.get("/api/reject-analyses/summary").json()
    assert summary["rejections"] == 0
    assert summary["technologies"] == []
    assert summary["average_score"] is None


# --- Pure parts -----------------------------------------------------------------------------


def _check(name, verdict, requirement="required"):
    return RequirementCheck(
        kind="skill", name=name, requirement=requirement, verdict=verdict, detail="."
    )


def test_gaps_are_ordered_and_say_what_to_do():
    skills = parse_skills_status(FACT_SHEET)
    checks = [
        _check("Python", "met"),
        _check("GraphQL", "missing", "nice_to_have"),
        _check("Docker", "weak"),
        _check("Kafka", "missing"),
        _check("Kubernetes", "missing"),
        _check("Rust", "weak"),
        RequirementCheck(
            kind="years", name="5+ years", requirement="required", verdict="weak", detail="."
        ),
    ]

    gaps = technology_gaps(checks, skills, {"GraphQL": "APIs"})

    assert [(gap.name, gap.action) for gap in gaps] == [
        ("Kafka", "learn"),
        ("Kubernetes", "limited"),
        ("Docker", "show_in_a_role"),
        ("Rust", "show_in_a_role"),
        ("GraphQL", "borrow"),
    ]


def test_without_a_fact_sheet_missing_skills_are_to_learn():
    gaps = technology_gaps([_check("Kafka", "missing")], None, {})
    assert gaps[0].action == "learn"


def test_the_furthest_stage_before_the_rejection(client, session_factory):
    application = _apply(client)
    _set_status(client, application["id"], "interview")
    _set_status(client, application["id"], "screening")
    _set_status(client, application["id"], "rejected")
    with session_factory() as db:
        stored = db.get(Application, uuid.UUID(application["id"]))
        assert furthest_stage(stored) == "interview"
