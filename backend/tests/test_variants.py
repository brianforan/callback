import uuid
from datetime import date, datetime

from app.models import JobPosting, PostingTechnology, Resume
from app.postings.parser import PostingRequirements
from app.resumes.matching import JobFacts, build_profile, score_job
from app.resumes.variants import build_variant_report, rule_failures
from app.schemas import VariantRules
from app.similarity import tex_to_text

TODAY = date(2026, 11, 2)


def _tex(*technologies, title="Software Engineer"):
    bullets = "\n".join(rf"\item Built services with {name}." for name in technologies)
    return (
        "\\begin{document}\n\\section*{Experience}\n"
        f"\\job{{{title}}}{{Acme}}\n{{Jan 2020 -- Present}}\n"
        f"\\begin{{itemize}}\n{bullets}\n\\end{{itemize}}\n\\end{{document}}"
    )


def _variant(label, *technologies, rules=None, is_variant=True):
    tex = _tex(*technologies)
    return Resume(
        id=uuid.uuid4(),
        label=label,
        source_tex=tex,
        plain_text=tex_to_text(tex),
        created_at=datetime(2026, 10, 1),
        is_variant=is_variant,
        variant_rules=(rules or VariantRules()).model_dump(mode="json"),
    )


def _posting(company, required=(), mentioned=(), raw_text="", level=None, role="backend"):
    technologies = [
        PostingTechnology(name=name, category="language", requirement="required")
        for name in required
    ] + [
        PostingTechnology(name=name, category="language", requirement="mentioned")
        for name in mentioned
    ]
    return JobPosting(
        id=uuid.uuid4(),
        company=company,
        title="Engineer",
        url=f"https://example.test/{company}",
        raw_text=raw_text or "Requirements",
        level=level,
        role_family=role,
        years_required=None,
        corrected_fields=[],
        technologies=technologies,
    )


def _job(required=(), mentioned=(), level=None, role="backend", years=None, clearance=False):
    return JobFacts(
        title="Engineer",
        level=level,
        role_family=role,
        years_required=years,
        technologies=(
            *((name, "required") for name in required),
            *((name, "mentioned") for name in mentioned),
        ),
        requirements=PostingRequirements(clearance=clearance),
    )


def _match(job):
    resume = _variant("Main", "Python")
    return score_job(build_profile([resume], [], TODAY), job)


def test_rules_turn_down_jobs_for_stated_reasons():
    rules = VariantRules(
        role_families=["backend"],
        levels=["senior"],
        focus_skills=["python"],
        deal_breakers=["Java"],
        max_years=8,
        min_score=0,
    )
    assert rules.focus_skills == ["Python"]
    good = _job(required=("Python",), level="senior")
    assert rule_failures(rules, good, _match(good)) == []

    bad = _job(required=("Java", "Rust"), level="staff", role="frontend", years=10, clearance=True)
    reasons = rule_failures(rules, bad, _match(bad))
    assert len(reasons) == 6
    assert "Role type is frontend, which this baseline skips." in reasons
    assert "Requires Java, a deal-breaker for this baseline." in reasons
    assert "Requires a security clearance." in reasons


def test_fields_a_job_doesnt_state_never_fail():
    rules = VariantRules(role_families=["backend"], levels=["senior"], max_years=5, min_score=0)
    job = _job(mentioned=("Python",), level=None, role="general_software", years=None)
    assert rule_failures(rules, job, _match(job)) == []


def test_minimum_score_and_allowed_clearance():
    job = _job(required=("Python",), clearance=True)
    match = _match(job)
    assert rule_failures(VariantRules(min_score=0, allow_clearance=True), job, match) == []
    assert rule_failures(VariantRules(min_score=0), job, match) == [
        "Requires a security clearance."
    ]
    assert rule_failures(VariantRules(min_score=100), job, match)[-1].startswith("Scores ")


def test_report_routes_each_posting_to_its_best_passing_variant():
    backend = _variant("Backend", "Python", "Kafka", rules=VariantRules(min_score=0))
    frontend = _variant(
        "Frontend", "React", "TypeScript", rules=VariantRules(focus_skills=["React"], min_score=0)
    )
    plain = _variant("Not a variant", "Rust", is_variant=False)
    postings = [
        _posting("Data Co", required=("Python", "Kafka")),
        _posting("UI Co", required=("React", "TypeScript")),
        _posting("Rust Co", required=("Rust",), raw_text="Requirements\n- Security clearance"),
        # A placeholder posting has no link and no text, so it's left out.
        JobPosting(
            id=uuid.uuid4(),
            company="Empty",
            title="Engineer",
            url=None,
            raw_text="",
            corrected_fields=[],
            technologies=[],
        ),
    ]
    report = build_variant_report([frontend, plain, backend], postings, TODAY)

    assert report.postings == 3
    assert [variant.label for variant in report.variants] == ["Backend", "Frontend"]
    by_label = {variant.label: variant for variant in report.variants}
    assert by_label["Backend"].passes == 2
    assert by_label["Backend"].best_for == 1
    assert by_label["Frontend"].passes == 1
    assert by_label["Frontend"].best_for == 1
    data_fit = next(fit for fit in by_label["Backend"].fits if fit.company == "Data Co")
    assert data_fit.best is True
    ui_fit = next(fit for fit in by_label["Frontend"].fits if fit.company == "UI Co")
    assert ui_fit.best is True
    assert by_label["Backend"].evidence.backed == ["Kafka", "Python"]

    assert [posting.company for posting in report.uncovered] == ["Rust Co"]
    assert "Requires a security clearance." in report.uncovered[0].reasons
    assert report.overlaps[0].first == "Backend"
    assert report.overlaps[0].postings == 1


def test_report_without_variants_is_empty():
    report = build_variant_report([_variant("Main", "Python", is_variant=False)], [], TODAY)
    assert report.variants == []
    assert report.uncovered == []


def test_variant_api_round_trip(client):
    tex = _tex("Python").encode()
    resume = client.post("/api/resumes", files={"file": ("r.tex", tex, "text/x-tex")}).json()
    assert resume["is_variant"] is False
    assert resume["variant_rules"]["min_score"] == 60

    rules = {"focus_skills": ["python", "Python"], "levels": ["senior"], "min_score": 50}
    response = client.put(
        f"/api/resumes/{resume['id']}/variant", json={"is_variant": True, "variant_rules": rules}
    )
    assert response.status_code == 200, response.text
    assert response.json()["variant_rules"]["focus_skills"] == ["Python"]
    assert client.get("/api/resumes").json()[0]["is_variant"] is True

    client.post(
        "/api/postings",
        json={"company": "Acme", "title": "Senior Engineer", "raw_text": "Requirements\n- Python"},
    )
    report = client.get("/api/analytics/variants").json()
    assert report["postings"] == 1
    [variant] = report["variants"]
    assert variant["rules"]["levels"] == ["senior"]
    assert variant["fits"][0]["company"] == "Acme"

    bad = client.put(
        f"/api/resumes/{resume['id']}/variant",
        json={"is_variant": True, "variant_rules": {"min_score": 101}},
    )
    assert bad.status_code == 422


def test_variants_are_isolated_between_users(client, other_client):
    tex = _tex("Python").encode()
    resume = client.post("/api/resumes", files={"file": ("r.tex", tex, "text/x-tex")}).json()
    response = other_client.put(f"/api/resumes/{resume['id']}/variant", json={"is_variant": True})
    assert response.status_code == 404
    assert other_client.get("/api/analytics/variants").json()["variants"] == []


def _upload_baseline(client, technology, **form):
    tex = _tex(technology).encode()
    return client.post(
        "/api/resumes/baselines",
        files={"file": (f"{technology}.tex", tex, "text/x-tex")},
        data=form,
    )


def test_baselines_upload_in_one_step_and_stop_at_three(client):
    first = _upload_baseline(client, "Python", label="Backend", rules='{"focus_skills": ["aws"]}')
    assert first.status_code == 201, first.text
    assert first.json()["is_variant"] is True
    assert first.json()["label"] == "Backend"
    assert first.json()["variant_rules"]["focus_skills"] == ["AWS"]
    _upload_baseline(client, "React", label="Full-stack")
    _upload_baseline(client, "PHP", label="Laravel")

    fourth = _upload_baseline(client, "Rust", label="Systems")
    assert fourth.status_code == 409
    assert "3 baselines" in fourth.json()["detail"]
    # The refused upload isn't kept as a resume either.
    assert len(client.get("/api/resumes").json()) == 3

    plain = client.post(
        "/api/resumes", files={"file": ("go.tex", _tex("Go").encode(), "text/x-tex")}
    ).json()
    refused = client.put(f"/api/resumes/{plain['id']}/variant", json={"is_variant": True})
    assert refused.status_code == 409


def test_replacing_a_baseline_carries_its_rules_over(client):
    old = _upload_baseline(client, "Python", label="Backend", rules='{"min_score": 70}').json()
    new = _upload_baseline(client, "Kafka", label="Backend v2", replaces=old["id"])
    assert new.status_code == 201, new.text
    assert new.json()["variant_rules"]["min_score"] == 70
    resumes = {resume["label"]: resume for resume in client.get("/api/resumes").json()}
    assert resumes["Backend"]["is_variant"] is False
    assert resumes["Backend v2"]["is_variant"] is True

    bad = _upload_baseline(client, "Go", rules='{"min_score": 500}')
    assert bad.status_code == 422


def test_a_tied_score_goes_to_the_baseline_whose_focus_skills_the_job_names_most():
    both = ("PHP", "Vue")
    first = _variant("A full-stack", *both, rules=VariantRules(focus_skills=["Vue"], min_score=0))
    second = _variant(
        "B laravel", *both, rules=VariantRules(focus_skills=["PHP", "Laravel"], min_score=0)
    )
    posting = _posting("Tailspin", required=("PHP", "Laravel"), mentioned=("Vue",))
    report = build_variant_report([first, second], [posting], TODAY)
    scores = {variant.label: variant.fits[0] for variant in report.variants}
    assert scores["A full-stack"].score == scores["B laravel"].score
    assert scores["B laravel"].best is True
    assert scores["A full-stack"].best is False
