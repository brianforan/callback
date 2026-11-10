"""Tailoring: the fact sheet table, the checks on a result, the endpoints, and the worker.

The tailor service is replaced by a fake sender, so no request reaches it or a model.
"""

import base64
import json
import uuid
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select
from test_listings import boards  # noqa: F401 - a fixture: job boards with no network

from app.app_config import SPECS_BY_KEY
from app.config import get_settings
from app.models import JobPosting, Resume, TailorJob, TailorStatus, User
from app.schemas import (
    CompileServiceResult,
    MixSkill,
    RequirementCheck,
    TailorServiceResult,
    TailorUsage,
)
from app.similarity import tex_to_text
from app.tailoring.rules import (
    SkillStatus,
    check_job_lines,
    check_technologies,
    closable_gaps,
    job_entries,
    parse_skills_status,
    staged_notes,
)
from app.tailoring.worker import (
    TailorUnavailable,
    claim_next,
    compile_next_pdf,
    fail_stale,
    http_compiler,
    http_sender,
    run_job,
)

TODAY = date(2026, 11, 3)

PREAMBLE = r"""\documentclass{article}
\newcommand{\job}[3]{\textbf{#1}\hfill\textit{#3}\par\textit{#2}\par}
\begin{document}
"""


def resume_tex(*backend_bullets: str, skills: str = "Python, SQL, AWS Lambda, Docker") -> str:
    bullets = "\n".join(rf"  \item {line}" for line in backend_bullets) or r"  \item Built APIs."
    return (
        PREAMBLE
        + "\\section*{Technical Skills}\n"
        + f"\\textbf{{Languages:}} {skills}\n\n"
        + "\\section*{Professional Experience}\n"
        + "\\job{Software Engineer}{Example Corp \\quad|\\quad Remote}\n{Jan 2021 -- Oct 2026}\n"
        + f"\\begin{{itemize}}\n{bullets}\n\\end{{itemize}}\n"
        + "\\job{Systems Engineer}{Sample Media}\n{Mar 2018 -- Dec 2020}\n"
        + "\\begin{itemize}\n  \\item Deployed streams with Helm on Kubernetes.\n\\end{itemize}\n"
        + "\\section*{Education}\nExample University, Bachelor's in Computer Science, 2017\n"
        + "\\end{document}\n"
    )


BASELINE_TEX = resume_tex(
    "Built Python services on AWS Lambda with SQL reporting.",
    "Moved processing to queue workers.",
)

FACT_SHEET = """# Fact sheet

## Skills status

| Skill | Status |
|---|---|
| Python (Flask), SQL Server, AWS (Lambda, S3), Docker, Redis | Claimable |
| JavaScript, React | Secondary. Never claim deep front-end experience. |
| Kubernetes, Helm | Inside the Sample Media bullet only |
| DNSSEC | Inside the old job's bullets only |
| PostgreSQL | Minimal use. Do not claim. |
| AWS IAM, AWS API Gateway, EC2 | Not used. Do not claim. |
| Terraform, Go, Kafka, team leadership | Never claim |
| Claude Code | Not on the skills line |

## Bullet bank

- Something else entirely.
"""

POSTING_TEXT = """Backend Engineer

Requirements:
- 3+ years of Python
- AWS Lambda and SQL
- Redis
"""


def _result(tex: str, ok: bool = True, reasons: list[str] | None = None) -> TailorServiceResult:
    return TailorServiceResult(
        ok=ok,
        tex=tex if ok else None,
        pdf_base64=base64.b64encode(b"%PDF-1.5 tailored").decode() if ok else None,
        pages=1 if ok else None,
        changelog=["Moved Redis onto the skills line"],
        gaps=["Kafka"],
        reasons=reasons or [],
        model="claude-sonnet-5-5",
        usage=TailorUsage(input_tokens=100, output_tokens=2000, cache_read_input_tokens=3000),
    )


class FakeSender:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.payloads: list[dict] = []

    def __call__(self, payload):
        self.payloads.append(json.loads(json.dumps(payload)))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


# --- The fact sheet table -------------------------------------------------------------------


def test_skills_table_is_read_by_status():
    skills = parse_skills_status(FACT_SHEET)

    assert skills.technologies["Python"] is SkillStatus.CLAIMABLE
    assert skills.technologies["Redis"] is SkillStatus.CLAIMABLE
    assert skills.technologies["React"] is SkillStatus.CLAIMABLE
    assert skills.technologies["Kubernetes"] is SkillStatus.RESTRICTED
    assert skills.technologies["PostgreSQL"] is SkillStatus.NEVER
    assert skills.technologies["Kafka"] is SkillStatus.NEVER
    # AWS IAM names AWS, but a claimable row for AWS wins.
    assert skills.technologies["AWS"] is SkillStatus.CLAIMABLE
    assert skills.phrases["AWS IAM"] is SkillStatus.NEVER
    assert skills.phrases["EC2"] is SkillStatus.NEVER
    assert skills.phrases["DNSSEC"] is SkillStatus.RESTRICTED
    # Lowercase phrases are ideas, not names, and would match ordinary words.
    assert "team leadership" not in skills.phrases
    assert "Claude Code" not in skills.phrases


def test_a_fact_sheet_without_the_table_allows_nothing_new():
    skills = parse_skills_status("# Notes\n\nI know Python.")

    assert skills.technologies == {}
    assert skills.phrases == {}


# --- Checks on a result ---------------------------------------------------------------------


def _resume(tex: str, label: str = "Backend") -> Resume:
    return Resume(id=uuid.uuid4(), label=label, source_tex=tex, plain_text=tex_to_text(tex))


def test_job_entries_span_lines_and_skip_the_definition():
    assert job_entries(BASELINE_TEX) == [
        ("Software Engineer", "Example Corp \\quad|\\quad Remote", "Jan 2021 -- Oct 2026"),
        ("Systems Engineer", "Sample Media", "Mar 2018 -- Dec 2020"),
    ]


def test_job_lines_must_match_exactly():
    reordered = BASELINE_TEX.replace("Built Python", "Shipped Python")
    retitled = BASELINE_TEX.replace("{Software Engineer}", "{Senior Software Engineer}")
    redated = BASELINE_TEX.replace("Jan 2021", "Jan 2020")

    assert check_job_lines(BASELINE_TEX, reordered, TODAY) == []
    assert "Senior Software Engineer" in check_job_lines(BASELINE_TEX, retitled, TODAY)[0]
    assert "Jan 2020" in check_job_lines(BASELINE_TEX, redated, TODAY)[0]


def test_dropping_a_job_is_caught():
    start = BASELINE_TEX.index("\\job{Systems Engineer}")
    end = BASELINE_TEX.index("\\section*{Education}")
    dropped = BASELINE_TEX[:start] + BASELINE_TEX[end:]

    assert check_job_lines(BASELINE_TEX, dropped, TODAY) == [
        "The baseline has 2 jobs, but the new resume has 1."
    ]


def test_technologies_need_backing():
    skills = parse_skills_status(FACT_SHEET)
    source = _resume(BASELINE_TEX)
    other = _resume(resume_tex(skills="Vue, TypeScript"), "Full-stack")

    # Redis is claimable, Vue is in another baseline, Kubernetes is already in the source.
    allowed = resume_tex(
        "Built Python services on AWS Lambda.", skills="Python, Redis, Vue, Kubernetes"
    )
    assert check_technologies(allowed, source, [other], skills) == []

    invented = resume_tex("Built Kafka pipelines with Rust and EC2.", skills="Python")
    assert check_technologies(invented, source, [other], skills) == [
        "Kafka is named, but your fact sheet says not to claim it here.",
        "Rust is named, but it isn't in your fact sheet or any baseline.",
        "EC2 is named, but your fact sheet says not to claim it here.",
    ]


def test_staged_notes_list_changes_and_gaps():
    assert staged_notes("Backend", ["Led with Redis"], []) == (
        "Tailored from the Backend baseline.\n\nChanges:\n- Led with Redis\n\nGaps: none"
    )


def _check(name, verdict, requirement="required", kind="skill"):
    return RequirementCheck(
        kind=kind, name=name, requirement=requirement, verdict=verdict, detail=""
    )


def test_closable_gaps_are_only_what_a_rewrite_can_fix():
    skills = parse_skills_status(FACT_SHEET)
    checklist = [
        _check("Python", "met"),
        _check("Redis", "missing"),  # claimable, left out of the baseline
        _check("Vue", "missing"),  # another baseline backs it
        _check("SQL Server", "weak"),  # only listed: the fact sheet can't add a role for it
        _check("Kafka", "missing"),  # never claim
        _check("Docker", "missing", requirement="mentioned"),
        _check("Years", "weak", kind="years"),
    ]

    gaps = closable_gaps(checklist, [MixSkill(name="Vue", baseline="Full-stack")], skills)

    assert [(gap.name, gap.source, gap.baseline) for gap in gaps] == [
        ("Redis", "fact_sheet", None),
        ("Vue", "baseline", "Full-stack"),
    ]


# --- Endpoints ------------------------------------------------------------------------------


def _upload_baseline(client, tex=BASELINE_TEX, label="Backend"):
    response = client.post(
        "/api/resumes/baselines",
        files={"file": ("backend.tex", tex.encode(), "application/x-tex")},
        data={"label": label, "rules": json.dumps({"min_score": 0})},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _posting(client, text=POSTING_TEXT):
    response = client.post(
        "/api/postings",
        json={
            "company": "Acme",
            "title": "Backend Engineer",
            "url": f"https://jobs.example.com/{uuid.uuid4()}",
            "raw_text": text,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _ready(client):
    baseline = _upload_baseline(client)
    assert client.put("/api/fact-sheet", json={"content": FACT_SHEET}).status_code == 200
    return baseline, _posting(client)


def test_posting_says_whether_tailoring_could_help(client):
    posting = _posting(client)
    advice = client.get(f"/api/postings/{posting['id']}").json()["tailor_advice"]
    assert advice == {
        "ready": False,
        "missing": "Add a baseline on the Resumes page first.",
        "gaps": [],
        "unfixable": [],
    }

    _upload_baseline(client)
    advice = client.get(f"/api/postings/{posting['id']}").json()["tailor_advice"]
    assert advice["ready"] is False
    assert advice["missing"].startswith("Fill in your fact sheet first")

    client.put("/api/fact-sheet", json={"content": FACT_SHEET})
    advice = client.get(f"/api/postings/{posting['id']}").json()["tailor_advice"]
    assert advice["ready"] is True
    assert [(gap["name"], gap["source"]) for gap in advice["gaps"]] == [("Redis", "fact_sheet")]
    assert advice["unfixable"] == []

    kafka = _posting(client, text=POSTING_TEXT + "- Kafka\n")
    advice = client.get(f"/api/postings/{kafka['id']}").json()["tailor_advice"]
    assert advice["unfixable"] == ["Kafka"]


def test_tailoring_needs_a_fact_sheet_and_a_baseline(client):
    posting = _posting(client)
    assert client.post(f"/api/postings/{posting['id']}/tailor").status_code == 422

    client.put("/api/fact-sheet", json={"content": FACT_SHEET})
    response = client.post(f"/api/postings/{posting['id']}/tailor")
    assert response.status_code == 422
    assert "baseline" in response.json()["detail"]


def test_tailoring_is_queued_once(client):
    baseline, posting = _ready(client)

    response = client.post(f"/api/postings/{posting['id']}/tailor")

    assert response.status_code == 202
    body = response.json()
    assert body["job"]["status"] == "queued"
    assert body["job"]["baseline_label"] == "Backend"
    assert body["job"]["baseline_id"] == baseline["id"]
    assert body["used_today"] == 1
    assert body["daily_limit"] == SPECS_BY_KEY["tailor_daily_limit"].default
    assert client.post(f"/api/postings/{posting['id']}/tailor").status_code == 409
    again = client.get(f"/api/postings/{posting['id']}/tailor").json()
    assert again["job"]["id"] == body["job"]["id"]
    assert again["job"]["status"] == "queued"


def test_daily_limit(client, set_config):
    set_config("tailor_daily_limit", 1)
    _, first = _ready(client)
    second = _posting(client)

    assert client.post(f"/api/postings/{first['id']}/tailor").status_code == 202
    response = client.post(f"/api/postings/{second['id']}/tailor")

    assert response.status_code == 429
    assert "reset at midnight UTC" in response.json()["detail"]


def test_a_user_with_limits_overridden_has_no_daily_limit(client, set_config, session_factory):
    set_config("tailor_daily_limit", 1)
    with session_factory() as db:
        db.scalar(select(User)).override_limits = True
        db.commit()
    _, first = _ready(client)
    second = _posting(client)

    assert client.post(f"/api/postings/{first['id']}/tailor").status_code == 202
    response = client.post(f"/api/postings/{second['id']}/tailor")

    assert response.status_code == 202, response.text
    assert response.json()["daily_limit"] is None


def test_applied_postings_are_not_tailored(client):
    _, posting = _ready(client)
    client.post(
        "/api/applications",
        json={
            "company": "Acme",
            "role_title": "Backend Engineer",
            "status": "applied",
            "applied_on": "2026-11-03",
            "posting_id": posting["id"],
        },
    )

    assert client.post(f"/api/postings/{posting['id']}/tailor").status_code == 409


def test_other_users_cannot_see_or_start_a_tailoring(client, other_client):
    _, posting = _ready(client)
    job = client.post(f"/api/postings/{posting['id']}/tailor").json()["job"]

    assert other_client.get(f"/api/postings/{posting['id']}/tailor").status_code == 404
    assert other_client.post(f"/api/postings/{posting['id']}/tailor").status_code == 404
    assert other_client.get(f"/api/tailor-jobs/{job['id']}/pdf").status_code == 404


# --- The worker -----------------------------------------------------------------------------


def _queue(client):
    _, posting = _ready(client)
    client.post(f"/api/postings/{posting['id']}/tailor")
    return posting


def _work(session_factory, sender):
    with session_factory() as db:
        job = claim_next(db)
        assert job is not None
        run_job(db, job, sender, TODAY)
        return job.id


TAILORED_TEX = resume_tex(
    "Built Python services on AWS Lambda with SQL reporting and Redis caching.",
    "Moved processing to queue workers.",
    skills="Python, SQL, AWS Lambda, Redis, Docker",
)


def test_a_passing_result_is_saved_and_staged(client, session_factory):
    posting = _queue(client)
    sender = FakeSender(_result(TAILORED_TEX))

    job_id = _work(session_factory, sender)

    payload = sender.payloads[0]
    assert payload["baseline_tex"] == BASELINE_TEX
    assert payload["posting"]["text"] == POSTING_TEXT.strip()
    assert payload["fact_sheet"] == FACT_SHEET
    assert payload["feedback"] == []
    assert {gap["name"] for gap in payload["gaps"]} >= {"Redis"}
    detail = client.get(f"/api/postings/{posting['id']}").json()
    assert detail["staged_resume_label"] == "Tailored: Acme - Backend Engineer"
    assert detail["staged_notes"].startswith("Tailored from the Backend baseline.")
    assert "- Kafka" in detail["staged_notes"]
    status = client.get(f"/api/postings/{posting['id']}/tailor").json()["job"]
    assert status["status"] == "done"
    assert status["attempts"] == 1
    assert status["result_resume_id"] == detail["staged_resume_id"]
    assert status["has_pdf"] is True
    pdf = client.get(f"/api/tailor-jobs/{job_id}/pdf")
    assert pdf.content == b"%PDF-1.5 tailored"
    assert pdf.headers["content-disposition"] == 'attachment; filename="Acme-Backend-Engineer.pdf"'
    resume_pdf = client.get(f"/api/resumes/{detail['staged_resume_id']}/pdf")
    assert resume_pdf.content == b"%PDF-1.5 tailored"
    resume = client.get(f"/api/resumes/{detail['staged_resume_id']}").json()
    assert resume["pdf_pages"] == 1
    assert resume["is_variant"] is False
    assert resume["source_tex"] == TAILORED_TEX
    with session_factory() as db:
        assert db.get(TailorJob, job_id).usage["cache_read_input_tokens"] == 3000


def test_a_failed_check_is_retried_once_with_the_reasons(client, session_factory):
    posting = _queue(client)
    invented = TAILORED_TEX.replace("Redis caching", "Kafka streaming")
    sender = FakeSender(_result(invented), _result(TAILORED_TEX))

    _work(session_factory, sender)

    assert sender.payloads[1]["feedback"] == [
        "Kafka is named, but your fact sheet says not to claim it here."
    ]
    status = client.get(f"/api/postings/{posting['id']}/tailor").json()["job"]
    assert status["status"] == "done"
    assert status["attempts"] == 2


def test_two_failures_fail_the_job_with_the_reasons(client, session_factory):
    posting = _queue(client)
    retitled = TAILORED_TEX.replace("{Software Engineer}", "{Lead Software Engineer}")
    sender = FakeSender(_result(retitled), _result(retitled))

    _work(session_factory, sender)

    status = client.get(f"/api/postings/{posting['id']}/tailor").json()["job"]
    assert status["status"] == "failed"
    assert status["attempts"] == 2
    assert "Lead Software Engineer" in status["reasons"][0]
    assert status["has_pdf"] is False
    assert client.get(f"/api/postings/{posting['id']}").json()["staged_at"] is None


def test_a_lower_score_is_turned_down(client, session_factory):
    posting = _queue(client)
    weaker = resume_tex("Moved processing to queue workers.", skills="Docker")
    sender = FakeSender(_result(weaker), _result(weaker))

    _work(session_factory, sender)

    reasons = client.get(f"/api/postings/{posting['id']}/tailor").json()["job"]["reasons"]
    assert any(reason.startswith("The checklist score dropped from") for reason in reasons)


def test_service_rejections_and_outages_are_retried(client, session_factory):
    posting = _queue(client)
    sender = FakeSender(
        _result("", ok=False, reasons=["The PDF is 2 pages. It must be exactly 1."]),
        TailorUnavailable("The tailoring service couldn't be reached."),
    )

    _work(session_factory, sender)

    assert sender.payloads[1]["feedback"] == ["The PDF is 2 pages. It must be exactly 1."]
    status = client.get(f"/api/postings/{posting['id']}/tailor").json()["job"]
    assert status["status"] == "failed"
    assert status["reasons"] == ["The tailoring service couldn't be reached."]


def test_a_posting_applied_to_meanwhile_is_not_staged(client, session_factory):
    posting = _queue(client)

    def apply_then_answer(payload):
        client.post(
            "/api/applications",
            json={
                "company": "Acme",
                "role_title": "Backend Engineer",
                "status": "applied",
                "applied_on": "2026-11-03",
                "posting_id": posting["id"],
            },
        )
        return _result(TAILORED_TEX)

    _work(session_factory, apply_then_answer)

    status = client.get(f"/api/postings/{posting['id']}/tailor").json()["job"]
    assert status["status"] == "done"
    assert status["reasons"] == [
        "You applied before tailoring finished, so the posting wasn't staged."
    ]
    assert client.get(f"/api/postings/{posting['id']}").json()["staged_at"] is None


def test_oldest_job_first_and_abandoned_jobs_fail(client, session_factory):
    first = _queue(client)
    second = _posting(client)
    client.post(f"/api/postings/{second['id']}/tailor")

    with session_factory() as db:
        job = claim_next(db)
        assert job.posting_id == uuid.UUID(first["id"])
        assert job.status == TailorStatus.RUNNING.value
        later = datetime.now(UTC) + timedelta(hours=1)
        assert fail_stale(db, later, attempt_timeout=60) == 1
        assert db.get(TailorJob, job.id).status == TailorStatus.FAILED.value
        assert db.get(JobPosting, job.posting_id).staged_at is None


def test_nothing_queued(session_factory):
    with session_factory() as db:
        assert claim_next(db) is None


# --- Resume PDFs ----------------------------------------------------------------------------


class FakeCompiler:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.sources: list[str] = []

    def __call__(self, tex):
        self.sources.append(tex)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def _compiled(pages=1):
    return CompileServiceResult(
        ok=True, pdf_base64=base64.b64encode(b"%PDF-1.5 baseline").decode(), pages=pages
    )


def test_baselines_are_compiled_first_and_once(client, session_factory):
    plain = client.post(
        "/api/resumes",
        files={"file": ("plain.tex", resume_tex(skills="Python").encode(), "application/x-tex")},
    ).json()
    baseline = _upload_baseline(client)
    assert client.get(f"/api/resumes/{baseline['id']}/pdf").status_code == 404
    compiler = FakeCompiler(_compiled(), _compiled(pages=2))

    with session_factory() as db:
        assert compile_next_pdf(db, compiler) is True
        assert compile_next_pdf(db, compiler) is True
        assert compile_next_pdf(db, compiler) is False

    assert compiler.sources == [BASELINE_TEX, resume_tex(skills="Python")]
    assert client.get(f"/api/resumes/{baseline['id']}/pdf").content == b"%PDF-1.5 baseline"
    resumes = {resume["id"]: resume for resume in client.get("/api/resumes").json()}
    assert resumes[baseline["id"]]["pdf_pages"] == 1
    assert resumes[plain["id"]]["pdf_pages"] == 2


def test_a_resume_that_does_not_compile_keeps_the_reason(client, session_factory):
    baseline = _upload_baseline(client)
    failed = CompileServiceResult(ok=False, reasons=["LaTeX didn't compile: Undefined control"])

    with session_factory() as db:
        assert compile_next_pdf(db, FakeCompiler(failed)) is True
        assert compile_next_pdf(db, FakeCompiler()) is False

    resume = client.get("/api/resumes").json()[0]
    assert resume["id"] == baseline["id"]
    assert resume["pdf_pages"] is None
    assert resume["pdf_error"] == "LaTeX didn't compile: Undefined control"


def test_an_outage_leaves_the_resume_for_later(client, session_factory):
    _upload_baseline(client)

    with session_factory() as db:
        with pytest.raises(TailorUnavailable):
            compile_next_pdf(db, FakeCompiler(TailorUnavailable("down")))
        assert compile_next_pdf(db, FakeCompiler(_compiled())) is True


def _compiler_client(handler) -> httpx.Client:
    return httpx.Client(base_url="http://compiler.test", transport=httpx.MockTransport(handler))


def test_the_compiler_client_reads_the_services_answer():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={"ok": True, "pdf_base64": "JVBERg==", "pages": 2})

    result = http_compiler(_compiler_client(handler))("\\documentclass{article}")

    assert seen == [("/compile", {"tex": "\\documentclass{article}"})]
    assert result.ok is True
    assert result.pages == 2


def test_the_compiler_client_names_the_compiler_when_it_is_down():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(TailorUnavailable, match="The compiler service couldn't be reached"):
        http_compiler(_compiler_client(handler))("x")


def test_the_compiler_client_reports_a_bad_status():
    compile_tex = http_compiler(_compiler_client(lambda request: httpx.Response(503)))

    with pytest.raises(TailorUnavailable, match="compiler service returned an error \\(503\\)"):
        compile_tex("x")


def test_a_resume_the_compiler_turns_down_keeps_its_reason(client, session_factory):
    baseline = _upload_baseline(client)
    reason = "The PDF is 3 pages. It must be at most 2."

    with session_factory() as db:
        compile_next_pdf(db, FakeCompiler(CompileServiceResult(ok=False, reasons=[reason])))

    resume = client.get("/api/resumes").json()[0]
    assert resume["id"] == baseline["id"]
    assert resume["pdf_error"] == reason


def test_an_upload_over_the_size_limit_is_refused(client):
    too_big = resume_tex() + "%" * get_settings().max_resume_bytes

    response = client.post(
        "/api/resumes", files={"file": ("big.tex", too_big.encode(), "application/x-tex")}
    )

    assert response.status_code == 413


@pytest.mark.usefixtures("boards")
def test_listings_carry_tailoring_advice(client):
    _ready(client)

    listings = client.get("/api/listings", params={"q": "backend", "tracking": "tracked"}).json()[
        "listings"
    ]

    assert [listing["company"] for listing in listings] == ["Acme"]
    advice = listings[0]["tailor_advice"]
    assert advice["ready"] is True
    assert [gap["name"] for gap in advice["gaps"]] == ["Redis"]


def test_retry_feedback_fits_the_tailor_service_s_limits(client, session_factory):
    """tailor accepts at most 20 feedback lines of 2,000 characters each."""
    _queue(client)
    reasons = [f"Reason {index}: " + "x" * 2_500 for index in range(25)]
    sender = FakeSender(_result("", ok=False, reasons=reasons), _result(TAILORED_TEX))

    _work(session_factory, sender)

    feedback = sender.payloads[1]["feedback"]
    assert len(feedback) == 20
    assert all(len(line) <= 2_000 for line in feedback)
    assert feedback[0].startswith("Reason 0: ")


def test_deleting_the_posting_keeps_its_tailoring_in_the_daily_count(client, set_config):
    """Every start counts, so deleting the posting mustn't hand the tailoring back."""
    set_config("tailor_daily_limit", 1)
    _, first = _ready(client)
    assert client.post(f"/api/postings/{first['id']}/tailor").status_code == 202

    assert client.delete(f"/api/postings/{first['id']}").status_code == 204
    second = _posting(client)
    response = client.post(f"/api/postings/{second['id']}/tailor")

    assert response.status_code == 429


def test_a_finished_tailoring_outlives_its_posting(client, session_factory):
    """Its cost stays on the Spend page, which reads tailor jobs."""
    posting = _queue(client)
    job_id = _work(session_factory, FakeSender(_result(TAILORED_TEX)))
    with session_factory() as db:
        cost = db.get(TailorJob, job_id).cost_usd

    assert client.delete(f"/api/postings/{posting['id']}").status_code == 204

    with session_factory() as db:
        job = db.get(TailorJob, job_id)
        assert job is not None
        assert job.posting_id is None
        assert job.cost_usd == cost
        assert job.status == "done"


def test_a_queued_tailoring_whose_posting_was_deleted_fails_with_why(client, session_factory):
    posting = _queue(client)
    assert client.delete(f"/api/postings/{posting['id']}").status_code == 204
    sender = FakeSender()

    job_id = _work(session_factory, sender)

    assert sender.payloads == []
    with session_factory() as db:
        job = db.get(TailorJob, job_id)
        assert job.status == "failed"
        assert job.reasons == ["The posting was deleted."]


def test_a_posting_deleted_during_the_call_fails_the_job_and_keeps_its_cost(
    client, session_factory
):
    posting = _queue(client)

    def delete_meanwhile(payload):
        with session_factory() as other:
            other.delete(other.get(JobPosting, uuid.UUID(posting["id"])))
            other.commit()
        return _result(TAILORED_TEX).model_copy(update={"cost_usd": 0.04})

    job_id = _work(session_factory, delete_meanwhile)

    with session_factory() as db:
        job = db.get(TailorJob, job_id)
        assert job.status == "failed"
        assert job.reasons == ["The posting was deleted while it was being tailored."]
        assert job.cost_usd == 0.04
        assert job.result_resume_id is None


def test_a_paid_attempt_that_errors_still_counts_its_cost(client, session_factory):
    """tailor sends what a draft cost even when the compiler couldn't be reached after it."""
    _queue(client)
    spent = {"model": "claude-sonnet-5-5", "usage": {"output_tokens": 900}, "cost_usd": 0.03}
    sender = FakeSender(
        TailorUnavailable("The compiler couldn't be reached.", spent=spent),
        _result(TAILORED_TEX).model_copy(update={"cost_usd": 0.05}),
    )

    job_id = _work(session_factory, sender)

    with session_factory() as db:
        job = db.get(TailorJob, job_id)
        assert job.status == "done"
        assert job.cost_usd == pytest.approx(0.08)
        assert job.usage["output_tokens"] == 900 + 2000


def test_the_service_client_reads_an_error_s_message_and_cost():
    detail = {
        "message": "The model gave no usable answer for this posting.",
        "spent": {"model": "claude-haiku-5-5", "cost_usd": 0.004, "usage": {"output_tokens": 50}},
    }
    send = http_sender(
        _compiler_client(lambda request: httpx.Response(502, json={"detail": detail}))
    )

    with pytest.raises(TailorUnavailable, match="no usable answer") as raised:
        send({})

    assert raised.value.spent == detail["spent"]


def test_tailoring_status_says_there_is_a_pdf_without_loading_it(client, session_factory, queries):
    posting = _queue(client)
    _work(session_factory, FakeSender(_result(TAILORED_TEX)))
    queries.clear()

    status = client.get(f"/api/postings/{posting['id']}/tailor").json()["job"]

    assert status["has_pdf"] is True
    # Asking whether it's there is fine; reading the bytes isn't.
    selected = [sql.replace("result_pdf IS NOT NULL", "") for sql in queries]
    assert not any("result_pdf" in sql for sql in selected)


def test_a_crash_while_tailoring_fails_the_job_instead_of_leaving_it_running(
    client, session_factory
):
    """A bug must not hold the user's daily slot or stop the worker."""
    from app.tailoring.worker import _run_safely

    posting = _queue(client)

    def crashes(payload):
        raise RuntimeError("a bug in the worker")

    with session_factory() as db:
        job = claim_next(db)
        _run_safely(db, job, crashes)

    status = client.get(f"/api/postings/{posting['id']}/tailor").json()["job"]
    assert status["status"] == "failed"
    assert status["reasons"] == ["Something went wrong while tailoring. Try again."]
    with session_factory() as db:
        assert claim_next(db) is None
