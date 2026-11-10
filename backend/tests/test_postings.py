import uuid
from datetime import date, timedelta

from app.cli import sync_user_postings
from app.models import Application, Profile, User
from app.postings.parser import (
    canonical_technology,
    canonical_url,
    company_key,
    parse_posting,
    parse_requirements,
    parse_role_family,
    technology_in_text,
)
from app.postings.store import load_match_profile
from app.schemas import VariantRules

POSTING = """Senior Software Engineer

About the Role
You'll work with Kubernetes during our 10 year anniversary.

Required Skills & Experience
- 5+ years of professional software engineering experience
- Deep expertise in TypeScript and Node.js
- Strong experience with Vue.js or React
- Hands-on experience with PostgreSQL
- 2+ years of experience with AWS

Nice to Have
- Experience with NestJS
- Experience with Terraform

Salary: USD $135,000 - $150,000 / year
Full-time. Remote anywhere in the US. You will mentor contract engineers.
"""


def _names(parsed, requirement):
    return {t.name for t in parsed.technologies if t.requirement.value == requirement}


def test_parser_extracts_normalized_fields():
    parsed = parse_posting(
        "Sr. Software Engineer",
        POSTING,
        source="Referral (family member); listing on Idealist",
        url="https://jobs.example.com/1",
    )
    assert parsed.level == "senior"
    assert parsed.role_family == "general_software"
    assert parsed.years_required == 5
    assert (parsed.salary_min, parsed.salary_max) == (135_000, 150_000)
    assert parsed.work_mode == "remote"
    assert parsed.employment_type == "full_time"
    assert parsed.referred is True
    assert parsed.referrer == "family member"
    assert parsed.source_kind == "job_board"
    assert {"TypeScript", "Node.js", "Vue", "React", "PostgreSQL", "AWS"} == _names(
        parsed, "required"
    )
    assert {"NestJS", "Terraform"} == _names(parsed, "nice_to_have")
    assert {"Kubernetes"} == _names(parsed, "mentioned")


def test_parser_avoids_common_false_positives():
    text = (
        "We go above and beyond. Go to our site. We react quickly to incidents.\n"
        "Salary: not listed (the form asks for ranges from under $120,000 to over $170,000)\n"
        "Our $50,000 grant has run for 12 years.\n"
    )
    parsed = parse_posting("Software Engineer", text)
    assert parsed.technologies == []
    assert (parsed.salary_min, parsed.salary_max) == (None, None)
    assert parsed.years_required is None
    assert parsed.level is None


def test_parser_finds_go_when_named_as_a_language():
    named = {t.name for t in parse_posting("Dev", "Python, Go and SQL").technologies}
    assert {"Go", "Python"} <= named
    assert "Go" in {t.name for t in parse_posting("Dev", "Written in golang").technologies}


def test_parser_reads_bold_and_markdown_headings():
    text = (
        "**Key Responsibilities:**\n"
        "- Build things in Kafka\n"
        "**Skills & Qualifications:**\n"
        "- 5 years of professional experience\n"
        "- Experience with GraphQL\n"
        "- Experience with AWS is a plus.\n"
        "**What we offer:**\n"
        "- Docker stickers\n"
        "## Application questions\n"
        "- Have you used Redis?\n"
    )
    parsed = parse_posting("Engineer", text)
    assert _names(parsed, "required") == {"GraphQL"}
    assert _names(parsed, "nice_to_have") == {"AWS"}
    assert _names(parsed, "mentioned") == {"Kafka", "Docker", "Redis"}
    assert parsed.years_required == 5


def test_preferred_inside_a_required_line_stays_required():
    parsed = parse_posting("Dev", "Required Skills\n- Strong Vue.js (preferred) or React\n")
    assert _names(parsed, "required") == {"Vue", "React"}


def test_multi_level_posting_has_no_single_level_or_years():
    text = (
        "Multiple Roles Available:\n"
        "- Senior Level (Level 1): 20+ years of relevant experience.\n"
        "- Mid-Level (Level 2, Level 3): 15 – 20 years of relevant experience.\n"
        "- Entry Level (Level 6): 0-2 years of relevant experience.\n"
        "- Must be 18 years of age or older.\n"
    )
    parsed = parse_posting("Software Engineer (Level 1-5)", text)
    assert parsed.years_required is None
    assert parsed.level is None


def test_one_level_line_still_counts_as_years():
    assert parse_posting("Dev", "Senior level role, 7 years of experience").years_required == 7


def test_level_comes_from_title_then_years():
    assert parse_posting("Staff Engineer", "").level == "staff"
    assert parse_posting("Senior Engineering Manager", "").level == "manager"
    assert parse_posting("Software Engineer II", "").level == "mid"
    assert parse_posting("Software Engineer", "3+ years of experience").level == "mid"
    assert parse_posting("Software Engineer", "8 years of experience").level == "staff"
    assert parse_posting("Senior Full Stack Web & Mobile Engineer", "").role_family == "full_stack"


MANAGES = "You'll manage a team of eight engineers and run their performance reviews."


def test_a_director_or_vp_naming_a_contributor_job_needs_text_to_be_a_manager():
    bank = parse_posting("Vice President, Software Engineer", "Build trading systems in Java.")
    assert bank.level == "senior"
    assert bank.role_family == "general_software"
    lead = parse_posting("Vice President, Software Engineer", MANAGES)
    assert lead.level == "manager"
    assert lead.role_family == "management"
    assert parse_posting("Senior Vice President, Engineer", "").level == "staff"
    assert parse_posting("Managing Director, Quantitative Analyst", "").level == "principal"
    assert parse_posting("AVP, Data Engineer", "").level == "mid"


def test_a_director_or_vp_naming_no_contributor_job_stays_a_manager():
    director = parse_posting("Director, Embedded Systems", "Own the roadmap.")
    assert director.level == "manager"
    assert director.role_family == "management"
    assert parse_posting("Vice President of Engineering", "").role_family == "management"
    assert parse_posting("Associate Director, Quality", "").level == "manager"


def test_a_manager_title_is_a_people_manager_only_when_it_runs_people():
    brand = parse_posting("Brand Manager, Diabetes", "Own the brand plan with sales and agencies.")
    assert brand.level is None
    assert brand.role_family == "other"
    assert parse_posting("Brand Manager", "5+ years of brand marketing experience").level == (
        "senior"
    )
    assert parse_posting("Associate Brand Manager", "").level == "junior"
    assert parse_posting("Senior Product Manager", "").level == "senior"
    assert parse_posting("Territory Account Manager", "3+ years of experience").level == "mid"
    # Business and leadership development managers are individual contributors.
    assert parse_posting("Business Development Manager", "").level is None
    assert parse_posting("Leadership Development Manager", "").level is None

    # Text that shows people management makes any Manager title a manager.
    product = parse_posting("Product Manager", MANAGES)
    assert (product.level, product.role_family) == ("manager", "management")

    # Titles that run people or a place are managers whatever the text says.
    for title in (
        "Engineering Manager",
        "Software Development Manager",
        "Manager, Machine Learning Engineering, Checkout",
        "Senior Manager of Software Engineering",
        "Store Manager",
        "Nurse Manager",
        "Head of Marketing",
    ):
        parsed = parse_posting(title, "Own the roadmap.")
        assert (parsed.level, parsed.role_family) == ("manager", "management"), title
    assert parse_posting("Marketing Intern", "").level == "intern"


def test_rank_words_take_years_before_the_built_in_ladder():
    assert parse_posting("Vice President, Developer", "3+ years of experience").level == "mid"
    assert parse_posting("Senior Associate Scientist", "3+ years of experience").level == "mid"
    assert parse_posting("Senior Associate Scientist", "").level == "mid"
    assert parse_posting("Associate Scientist", "").level == "junior"
    # A title that runs people, or an intern word, still decides; a product manager doesn't.
    assert parse_posting("Associate Engineering Manager", "3+ years").level == "manager"
    assert parse_posting("Associate Product Manager", "3+ years of experience").level == "mid"
    assert parse_posting("Associate Intern", "").level == "intern"


def test_staff_and_architect_keep_their_usual_meaning_outside_known_exceptions():
    assert parse_posting("Staff Accountant", "").level == "junior"
    assert parse_posting("Staff Accountant", "6 years of experience").level == "senior"
    assert parse_posting("Staff Data Engineer", "").level == "staff"
    assert parse_posting("Staff Engineering Manager", "").level == "manager"
    assert parse_posting("Solutions Architect", "").level == "principal"
    assert parse_posting("Landscape Architect", "").level is None
    assert parse_posting("Staff Product Designer", "").level == "staff"
    assert parse_posting("Staff Scientist", "").level == "staff"
    assert parse_posting("Member of Technical Staff", "").level is None
    assert parse_posting("Member of Technical Staff", "6 years of experience").level == "senior"
    assert parse_posting("Principal Laravel Architect", "").level == "principal"
    assert parse_posting("Associate Director, Quality Scientist", "").level == "staff"
    assert parse_posting("Vice President, Software Engineer III", "").level == "senior"


def test_role_family_without_text_reads_the_title_alone():
    assert parse_role_family("Director of Engineering") == "management"
    assert parse_role_family("Director, Software Engineer", "Design APIs.") == "general_software"


def test_normalizers():
    assert company_key("Acme, Inc.") == company_key("ACME") == "acme"
    assert canonical_url("https://a.com/jobs/1/#top") == "https://a.com/jobs/1"
    assert canonical_url("https://a.com/jobs?id=2") == "https://a.com/jobs?id=2"


def _add_posting(client, **overrides):
    payload = {"company": "Acme", "title": "Senior Engineer", "raw_text": POSTING, **overrides}
    response = client.post("/api/postings", json=payload)
    assert response.status_code in (200, 201), response.text
    return response


def test_manual_posting_parses_and_url_is_deduplicated(client):
    first = _add_posting(client, url="https://a.com/jobs/1")
    assert first.status_code == 201
    body = first.json()
    assert body["level"] == "senior"
    assert body["salary_max"] == 150_000
    assert body["applications"] == []
    again = _add_posting(client, url="https://a.com/jobs/1/")
    assert again.status_code == 200
    assert again.json()["id"] == body["id"]
    assert len(client.get("/api/postings").json()) == 1


def test_corrections_survive_a_reparse(client):
    posting = _add_posting(client).json()
    url = f"/api/postings/{posting['id']}"

    edited = client.patch(url, json={"level": "mid", "technologies": ["Python", "aws"]}).json()
    assert edited["level"] == "mid"
    assert {t["name"] for t in edited["technologies"]} == {"Python", "AWS"}
    assert set(edited["corrected_fields"]) == {"level", "technologies"}

    reparsed = client.post(f"{url}/reparse").json()
    assert reparsed["level"] == "mid"
    assert {t["name"] for t in reparsed["technologies"]} == {"Python", "AWS"}
    assert reparsed["years_required"] == 5

    changed = client.patch(url, json={"raw_text": "10+ years of experience"}).json()
    assert changed["years_required"] == 10
    assert changed["level"] == "mid"


def test_edit_rejects_bad_salary_and_duplicate_url(client):
    first = _add_posting(client, url="https://a.com/1").json()
    second = _add_posting(client, url="https://a.com/2").json()
    url = f"/api/postings/{second['id']}"
    assert client.patch(url, json={"salary_min": 9, "salary_max": 1}).status_code == 422
    assert client.patch(url, json={"url": first["url"]}).status_code == 409


def test_application_creates_or_reuses_a_posting(client):
    application = client.post(
        "/api/applications",
        json={
            "company": "Acme",
            "role_title": "Senior Engineer",
            "job_url": "https://a.com/1",
            "posting_text": POSTING,
            "referrer": "Sam",
        },
    ).json()
    posting = client.get(f"/api/postings/{application['posting_id']}").json()
    assert posting["referred"] is True
    assert posting["referrer"] == "Sam"
    assert posting["seen_on"] == application["applied_on"]
    assert [item["id"] for item in posting["applications"]] == [application["id"]]

    repeat = client.post(
        "/api/applications",
        json={"company": "Acme", "role_title": "Senior Engineer", "job_url": "https://a.com/1/"},
    ).json()
    assert repeat["posting_id"] == application["posting_id"]

    without_url = client.post("/api/applications", json={"company": "B", "role_title": "C"}).json()
    assert without_url["posting_id"] not in (None, application["posting_id"])


def test_relinking_removes_an_unused_placeholder_posting(client):
    application = client.post(
        "/api/applications", json={"company": "Acme", "role_title": "Engineer", "referrer": "Sam"}
    ).json()
    placeholder_id = application["posting_id"]
    assert client.get(f"/api/postings/{placeholder_id}").json()["placeholder"] is True
    listing = client.post(
        "/api/postings",
        json={
            "company": "Acme",
            "title": "Engineer",
            "url": "https://a.com/9",
            "raw_text": POSTING,
        },
    ).json()
    assert listing["placeholder"] is False

    linked = client.patch(
        f"/api/applications/{application['id']}", json={"posting_id": listing["id"]}
    ).json()
    assert linked["posting_id"] == listing["id"]
    assert client.get(f"/api/postings/{placeholder_id}").status_code == 404
    moved = client.get(f"/api/postings/{listing['id']}").json()
    assert moved["referrer"] == "Sam"
    assert [item["id"] for item in moved["applications"]] == [application["id"]]


def test_relinking_keeps_a_posting_with_text_or_other_applications(client):
    with_text = client.post(
        "/api/applications",
        json={"company": "Acme", "role_title": "Engineer", "posting_text": POSTING},
    ).json()
    shared = client.post("/api/applications", json={"company": "B", "role_title": "C"}).json()
    sibling = client.post(
        "/api/applications",
        json={"company": "B", "role_title": "C", "posting_id": shared["posting_id"]},
    ).json()
    target = client.post(
        "/api/postings", json={"company": "Acme", "title": "Engineer", "url": "https://a.com/8"}
    ).json()

    for application in (with_text, shared):
        client.patch(f"/api/applications/{application['id']}", json={"posting_id": target["id"]})
    assert client.get(f"/api/postings/{with_text['posting_id']}").status_code == 200
    kept = client.get(f"/api/postings/{shared['posting_id']}").json()
    assert [item["id"] for item in kept["applications"]] == [sibling["id"]]


def test_deleting_a_posting_keeps_its_application(client):
    application = client.post("/api/applications", json={"company": "B", "role_title": "C"}).json()
    assert client.delete(f"/api/postings/{application['posting_id']}").status_code == 204
    assert client.get(f"/api/applications/{application['id']}").json()["posting_id"] is None


def test_postings_are_isolated_between_users(client, other_client):
    posting = _add_posting(client).json()
    assert other_client.get("/api/postings").json() == []
    assert other_client.get(f"/api/postings/{posting['id']}").status_code == 404
    assert other_client.delete(f"/api/postings/{posting['id']}").status_code == 404
    foreign = {"company": "X", "role_title": "Y", "posting_id": posting["id"]}
    assert other_client.post("/api/applications", json=foreign).status_code == 422


def test_related_postings_flag_likely_repeats(client):
    first = _add_posting(client, url="https://a.com/1").json()
    _add_posting(client, url="https://a.com/2", company="Beta").json()
    _add_posting(client, url="https://a.com/3", company="Gamma", raw_text="Rust only").json()
    detail = client.get(f"/api/postings/{first['id']}").json()
    assert detail["related"][0]["company"] == "Beta"
    assert detail["related"][0]["likely_repeat"] is True
    assert detail["related"][0]["stack_overlap"] == 1
    assert "React" in detail["related"][0]["shared_stack"]


def test_trends_cover_applied_postings_by_week(client):
    old = (date.today() - timedelta(days=21)).isoformat()
    first = client.post(
        "/api/applications",
        json={
            "company": "Acme, Inc.",
            "role_title": "Senior Engineer",
            "applied_on": old,
            "posting_text": POSTING,
            "referrer": "Sam",
        },
    ).json()
    client.patch(f"/api/applications/{first['id']}", json={"status": "screening"})
    client.post(
        "/api/applications",
        json={"company": "ACME", "role_title": "Junior Engineer", "posting_text": "Python"},
    )
    _add_posting(client, company="Never Applied", url="https://a.com/9")

    trends = client.get("/api/analytics/postings").json()
    assert trends["total_postings"] == 3
    assert trends["applied_postings"] == 2
    assert {row["label"]: row["count"] for row in trends["levels"]} == {"senior": 1, "junior": 1}
    assert len(trends["weekly"]) >= 4
    assert trends["weekly"][0]["levels"] == {"senior": 1}
    assert trends["weekly"][0]["median_years"] == 5
    assert trends["weekly"][0]["median_salary"] == 142_500
    assert trends["weekly"][1]["postings"] == 0
    assert trends["stack_weekly"][0]["shares"]["AWS"] == 1
    python = next(row for row in trends["stack"] if row["name"] == "Python")
    assert python["postings"] == 1
    assert python["share"] == 0.5
    acme = next(row for row in trends["companies"] if row["applications"] == 2)
    assert acme["postings"] == 2
    rates = {row["source"]: row["callback_rate"] for row in trends["referrals"]}
    assert rates == {"Referred": 1, "Not referred": 0}


def test_sync_backfills_from_notes_and_is_repeatable(session_factory, client, monkeypatch):
    monkeypatch.setattr("app.cli.SessionLocal", session_factory)
    with session_factory() as db:
        user = db.query(User).one()
        db.add(
            Application(
                user_id=user.id,
                company="Acme",
                role_title="Sr. Software Engineer",
                source="Referral (family member); listing on Idealist",
                job_url="https://a.com/1",
                status="applied",
                applied_on=date(2026, 10, 27),
                notes=POSTING,
            )
        )
        db.commit()

    assert sync_user_postings("owner@example.com") == 0
    assert sync_user_postings("owner@example.com") == 0
    postings = client.get("/api/postings").json()
    assert len(postings) == 1
    assert postings[0]["seen_on"] == "2026-10-27"
    assert postings[0]["level"] == "senior"
    assert postings[0]["referred"] is True
    assert sync_user_postings("nobody@example.com") == 1


RESUME_TEX = rb"\begin{document}TypeScript, node.js, React.js, Postgres and AWS\end{document}"
THIN_RESUME_TEX = rb"\begin{document}Only TypeScript here\end{document}"


def _upload_resume(client, content=RESUME_TEX):
    files = {"file": ("r.tex", content, "text/x-tex")}
    return client.post("/api/resumes", files=files).json()


def _apply_with(client, resume, **overrides):
    payload = {
        "company": "Acme",
        "role_title": "Senior Engineer",
        "posting_text": POSTING,
        "resume_id": resume["id"] if resume else None,
        **overrides,
    }
    response = client.post("/api/applications", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_technology_in_text_uses_parser_rules_and_exact_names():
    assert technology_in_text("PostgreSQL", "Built on postgres daily")
    assert technology_in_text("Go", "Services in Go, mostly")
    assert not technology_in_text("Go", "We go above and beyond")
    assert not technology_in_text("Java", "JavaScript only")
    assert technology_in_text("Hasura", "Used hasura for APIs")
    assert not technology_in_text("Hasura", "Used hasurable things")


def test_coverage_compares_required_and_nice_with_the_resume_sent(client):
    application = _apply_with(client, _upload_resume(client))
    posting = client.get(f"/api/postings/{application['posting_id']}").json()
    coverage = posting["coverage"]
    assert coverage["required_total"] == 6
    assert coverage["required_matched"] == 5
    assert coverage["missing_required"] == ["Vue"]
    assert coverage["coverage"] == 0.8333
    assert coverage["nice_total"] == 2
    assert coverage["nice_matched"] == 0
    assert set(coverage["missing_nice"]) == {"NestJS", "Terraform"}
    assert client.get("/api/postings").json()[0]["coverage"]["required_matched"] == 5


def test_coverage_is_empty_without_a_resume_or_required_stack(client):
    without_resume = _apply_with(client, None)
    posting = client.get(f"/api/postings/{without_resume['posting_id']}").json()
    assert posting["coverage"] is None

    no_section = _apply_with(client, _upload_resume(client), posting_text="We use Python.")
    coverage = client.get(f"/api/postings/{no_section['posting_id']}").json()["coverage"]
    assert coverage["required_total"] == 0
    assert coverage["coverage"] is None


def test_coverage_uses_the_latest_application_with_a_resume(client):
    url = "https://a.com/1"
    thin = _upload_resume(client, THIN_RESUME_TEX)
    full = _upload_resume(client)
    first = _apply_with(client, full, job_url=url, applied_on="2026-10-01")
    _apply_with(client, thin, job_url=url, applied_on="2026-10-08")
    coverage = client.get(f"/api/postings/{first['posting_id']}").json()["coverage"]
    assert coverage["required_matched"] == 1


def test_trends_report_gaps_medians_and_callbacks_by_match(client):
    full = _upload_resume(client)
    thin = _upload_resume(client, THIN_RESUME_TEX)
    strong = _apply_with(client, full)
    client.patch(f"/api/applications/{strong['id']}", json={"status": "screening"})
    _apply_with(client, thin)
    _apply_with(client, thin, posting_text="We use Python.")
    _apply_with(client, None, company="No Resume")

    trends = client.get("/api/analytics/postings").json()
    assert trends["scored_postings"] == 2
    assert trends["median_coverage"] == (0.8333 + 0.1667) / 2
    assert trends["weekly"][-1]["median_coverage"] == trends["median_coverage"]
    gaps = {gap["name"]: gap for gap in trends["gaps"]}
    assert gaps["Vue"]["missing_in"] == 2
    assert gaps["Vue"]["required_in"] == 2
    assert gaps["AWS"]["missing_in"] == 1
    assert "TypeScript" not in gaps
    assert trends["gaps"][0]["name"] == "Vue"
    rates = {row["source"]: row for row in trends["coverage_callbacks"]}
    assert rates["80% and up"]["callback_rate"] == 1
    assert rates["Under 50%"]["callback_rate"] == 0
    assert "50–79%" not in rates


def test_a_clearance_the_posting_says_isnt_needed_is_not_a_requirement():
    def clearance(text):
        return parse_requirements(f"Requirements\n{text}\n").clearance

    assert clearance("- This role does not require a security clearance.") is False
    assert clearance("- No security clearance required") is False
    assert clearance("- You don't need a clearance for this role") is False
    assert clearance("Clearance: Not required") is False
    assert clearance("- Active TS/SCI clearance required") is True
    assert clearance("- Must be able to obtain a security clearance") is True
    assert clearance("- If you can't obtain a clearance, this role isn't for you") is True


def test_hand_typed_skills_resolve_to_the_parser_s_names():
    assert canonical_technology("postgres") == "PostgreSQL"
    assert canonical_technology("k8s") == "Kubernetes"
    assert canonical_technology("golang") == "Go"
    assert canonical_technology("node") == "Node.js"
    assert canonical_technology(" python ") == "Python"
    assert canonical_technology("Terraform Cloud") == "Terraform Cloud"
    rules = VariantRules(focus_skills=["postgres", "PostgreSQL"], deal_breakers=["k8s"])
    assert rules.focus_skills == ["PostgreSQL"]
    assert rules.deal_breakers == ["Kubernetes"]


def test_go_before_a_role_word_is_the_language():
    assert technology_in_text("go", "Senior Go Engineer")
    assert technology_in_text("go", "Go Developer")
    assert technology_in_text("go", "Build Go services on AWS")
    assert not technology_in_text("go", "Go to market lead")
    assert not technology_in_text("go", "Let's go build something")


def test_deleting_an_application_removes_its_placeholder_posting(client):
    application = client.post(
        "/api/applications", json={"company": "Acme", "role_title": "Engineer"}
    ).json()
    placeholder_id = application["posting_id"]
    assert client.get(f"/api/postings/{placeholder_id}").json()["placeholder"] is True

    assert client.delete(f"/api/applications/{application['id']}").status_code == 204

    assert client.get(f"/api/postings/{placeholder_id}").status_code == 404
    assert client.get("/api/postings").json() == []


def test_deleting_an_application_keeps_a_real_posting(client):
    application = client.post(
        "/api/applications",
        json={"company": "Acme", "role_title": "Engineer", "job_url": "https://a.com/real"},
    ).json()
    assert client.delete(f"/api/applications/{application['id']}").status_code == 204
    assert client.get(f"/api/postings/{application['posting_id']}").status_code == 200


def test_an_application_whose_posting_was_deleted_still_counts_for_matching(
    client, session_factory
):
    tex = rb"\begin{document}Python engineer\end{document}"
    resume = client.post("/api/resumes", files={"file": ("r.tex", tex, "text/x-tex")}).json()
    application = client.post(
        "/api/applications",
        json={
            "company": "Acme",
            "role_title": "Engineer",
            "job_url": "https://a.com/gone",
            "resume_id": resume["id"],
        },
    ).json()
    assert client.delete(f"/api/postings/{application['posting_id']}").status_code == 204

    with session_factory() as db:
        profile = db.query(Profile).one()
        everything = load_match_profile(db, profile.id)
        leaving_one_out = load_match_profile(db, profile.id, exclude_posting_id=uuid.uuid4())
    assert everything.applications == 1
    assert leaving_one_out.applications == 1


def test_a_posting_s_detail_doesn_t_load_every_other_posting_s_resumes(client, queries):
    """Related postings only need each one's technologies and text."""
    tex = rb"\begin{document}Python engineer\end{document}"
    resume = client.post("/api/resumes", files={"file": ("r.tex", tex, "text/x-tex")}).json()
    applications = [
        client.post(
            "/api/applications",
            json={
                "company": f"Company {n}",
                "role_title": "Engineer",
                "job_url": f"https://a.com/{n}",
                "resume_id": resume["id"],
                "posting_text": "Requirements\n- Python\n",
            },
        ).json()
        for n in range(3)
    ]
    queries.clear()

    detail = client.get(f"/api/postings/{applications[0]['posting_id']}")

    assert detail.status_code == 200
    assert len(detail.json()["related"]) == 2
    # Its own applications' resumes, and the active profile's for matching: no more.
    assert sum("FROM resumes" in sql for sql in queries) <= 2
