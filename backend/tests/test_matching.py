import uuid
from datetime import date, datetime, timedelta

import httpx
import pytest

from app.listings import search as listings_module
from app.listings.query import parse_query
from app.models import (
    Application,
    ApplicationStatus,
    JobPosting,
    PostingTechnology,
    Resume,
    StatusEvent,
)
from app.postings.parser import DegreeLevel, DegreeRequirement, PostingRequirements
from app.resumes.matching import JobFacts, build_profile, score_job, title_words
from app.schemas import ListingTracking
from app.similarity import tex_to_text

TODAY = date(2026, 11, 2)


def _tex(skills="", roles=(), projects="", education=""):
    """A resume shaped like a real one: a skills list, dated roles with bullets, education."""
    lines = [r"\begin{document}"]
    if skills:
        lines += [r"\section*{Technical Skills}", skills, ""]
    if roles:
        lines.append(r"\section*{Professional Experience}")
        for title, dates, bullets in roles:
            lines += [rf"\job{{{title}}}{{Acme \quad|\quad Remote}}", f"{{{dates}}}"]
            lines += [r"\begin{itemize}", *(rf"  \item {bullet}" for bullet in bullets)]
            lines += [r"\end{itemize}", ""]
    if projects:
        lines += [r"\section*{Projects}", projects, ""]
    if education:
        lines += [r"\section*{Education}", education, ""]
    lines.append(r"\end{document}")
    return "\n".join(lines)


def _resume(label, tex, days_ago=0):
    return Resume(
        id=uuid.uuid4(),
        label=label,
        source_tex=tex,
        plain_text=tex_to_text(tex),
        created_at=datetime(2026, 1, 1) + timedelta(days=300 - days_ago),
    )


def _worked(*technologies, years=4, title="Software Engineer"):
    """A resume with one role of the given length that names each technology in a bullet."""
    start = 2026 - years
    return _tex(
        roles=[
            (
                title,
                f"Nov {start} -- Present",
                [f"Built services with {name}." for name in technologies],
            )
        ]
    )


def _application(
    resume,
    *,
    days_ago=0,
    status="applied",
    level="senior",
    role="backend",
    stack=(),
    company="Acme",
):
    posting = JobPosting(
        id=uuid.uuid4(),
        title="Engineer",
        raw_text="",
        level=level,
        role_family=role,
        years_required=5,
        technologies=[
            PostingTechnology(name=name, category="language", requirement="required")
            for name in stack
        ],
    )
    events = [StatusEvent(status="applied", occurred_at=datetime(2026, 1, 1))]
    if status != "applied":
        events.append(StatusEvent(status=status, occurred_at=datetime(2026, 1, 2)))
    return Application(
        company=company,
        role_title="Engineer",
        status=status,
        resume_id=resume.id if resume else None,
        applied_on=TODAY - timedelta(days=days_ago),
        created_at=datetime(2026, 1, 1),
        posting=posting,
        events=events,
    )


def _job(
    required=(),
    nice=(),
    mentioned=(),
    level="senior",
    role="backend",
    years=None,
    title="Senior",
    requirements=PostingRequirements(),
):
    return JobFacts(
        title=title,
        level=level,
        role_family=role,
        years_required=years,
        technologies=(
            *((name, "required") for name in required),
            *((name, "nice_to_have") for name in nice),
            *((name, "mentioned") for name in mentioned),
        ),
        requirements=requirements,
    )


def _part(match, label):
    return next(part for part in match.parts if part.label == label)


def _check(match, name):
    return next(check for check in match.checklist if check.name == name)


def test_no_resume_means_no_profile():
    assert build_profile([], [], TODAY) is None


def test_skills_are_graded_by_where_the_resume_names_them():
    tex = _tex(
        skills="Python, Docker, Kafka",
        roles=[("Software Engineer", "Jan 2022 -- Present", ["Shipped Python services."])],
        projects="A Kafka consumer for fun.",
    )
    profile = build_profile([_resume("Main", tex)], [], TODAY)
    match = score_job(profile, _job(required=("Python", "Docker", "Kafka", "Rust")))
    assert _check(match, "Python").verdict == "met"
    assert "4.9 years" in _check(match, "Python").detail
    assert _check(match, "Kafka").verdict == "met"
    assert _check(match, "Docker").verdict == "weak"
    assert _check(match, "Rust").verdict == "missing"
    assert match.matched_required == ["Python", "Kafka"]
    assert match.weak_required == ["Docker"]
    assert match.missing_required == ["Rust"]
    assert match.best_resume.required_weak == 1


def test_years_per_skill_need_enough_dated_months():
    tex = _tex(
        skills="Go, Ruby",
        roles=[
            ("Software Engineer", "Jan 2020 -- Present", ["Python APIs."]),
            ("Intern", "Jun 2019 -- Aug 2019", ["Python scripts."]),
        ],
    )
    profile = build_profile([_resume("Main", tex)], [], TODAY)
    asks = PostingRequirements(skill_years=(("Python", 6), ("Go", 2), ("Rust", 1)))
    match = score_job(profile, _job(required=("Python", "Go"), requirements=asks))
    assert _check(match, "Python").verdict == "met"
    asks_more = PostingRequirements(skill_years=(("Python", 10),))
    assert (
        _check(
            score_job(profile, _job(required=("Python",), requirements=asks_more)), "Python"
        ).verdict
        == "weak"
    )
    assert _check(match, "Go").verdict == "weak"
    assert "no dated role" in _check(match, "Go").detail
    assert match.resume_years == 7.2


def test_full_stack_match_outscores_missing_stack():
    resume = _resume("Main", _worked("Python", "Django", "PostgreSQL", "AWS"))
    profile = build_profile([resume], [_application(resume, stack=("Python",))], TODAY)
    good = score_job(profile, _job(required=("Python", "PostgreSQL")))
    bad = score_job(profile, _job(required=("Rust", "Scala")))
    assert good.score > bad.score
    assert good.matched_required == ["Python", "PostgreSQL"]
    assert bad.missing_required == ["Rust", "Scala"]
    assert _part(bad, "Required stack").score == 25


def test_scoring_is_deterministic():
    resume = _resume("Main", _worked("Python", "Django"))
    profile = build_profile([resume], [_application(resume, stack=("Python",))], TODAY)
    job = _job(required=("Python", "Go"), nice=("AWS",))
    assert score_job(profile, job) == score_job(profile, job)


def test_best_resume_is_scored_and_dropped_skills_are_flagged():
    backend = _resume("Backend", _worked("Python", "Kafka"), days_ago=200)
    frontend = _resume("Frontend", _worked("React", "TypeScript"), days_ago=1)
    applications = [_application(backend, days_ago=180), _application(frontend, days_ago=1)]
    profile = build_profile([backend, frontend], applications, TODAY)
    match = score_job(profile, _job(required=("React", "TypeScript"), nice=("Python",)))
    assert match.best_resume.label == "Frontend"
    assert match.best_resume.required_matched == 2
    assert match.best_resume.nice_matched == 0
    kafka = score_job(profile, _job(required=("Kafka", "React")))
    assert kafka.faded_required == ["Kafka"]
    assert kafka.latest_resume == "Frontend"


def test_parts_without_data_drop_out_and_the_rest_reweight():
    profile = build_profile([_resume("Main", _worked("Python"))], [], TODAY)
    match = score_job(profile, _job(required=("Python",)))
    assert [part.label for part in match.parts] == ["Required stack"]
    assert match.score == 67
    assert match.confidence == "low"


def test_a_job_with_no_stated_stack_counts_as_neutral():
    profile = build_profile([_resume("Main", _worked(title="Backend Engineer"))], [], TODAY)
    match = score_job(profile, _job(title="Backend Engineer"))
    assert _part(match, "Stack not stated").score == 50
    assert _part(match, "Title").score == 100
    assert match.score < 80


def test_one_known_technology_cant_outrank_many():
    names = ("Python", "Django", "PostgreSQL", "Redis", "AWS", "Docker")
    profile = build_profile([_resume("Main", _worked(*names))], [], TODAY)
    thin = score_job(profile, _job(required=("Python",)))
    broad = score_job(profile, _job(required=names))
    assert broad.score > thin.score


def test_title_words_use_synonyms_and_drop_level_words():
    assert title_words("Senior Back-end Developer (Remote)") == {"backend", "engineer"}
    assert title_words("SWE II") == {"software", "engineer"}
    assert title_words("Full Stack Dev") == {"fullstack", "engineer"}


def test_title_compares_with_role_titles_and_jobs_applied_to():
    resume = _resume("Main", _worked("Python", title="Backend Engineer"))
    applications = [_application(resume, company="Acme")]
    applications[0].role_title = "Senior Platform Engineer"
    profile = build_profile([resume], applications, TODAY)
    assert _part(score_job(profile, _job(title="Staff Back-end Developer")), "Title").score == 100
    assert _part(score_job(profile, _job(title="Platform Engineer")), "Title").score == 100
    far = score_job(profile, _job(title="Senior Solution Engineer (Pre-sales)"))
    assert _part(far, "Title").score == 33
    assert "engineer" in _part(far, "Title").detail


def test_mentioned_stack_stands_in_when_no_requirements_are_stated():
    profile = build_profile([_resume("Main", _worked("Python"))], [], TODAY)
    match = score_job(profile, _job(mentioned=("Python", "Rust")))
    assert _part(match, "Stack mentioned").weight == 30
    assert match.matched_required == []
    assert [check.requirement for check in match.checklist] == ["mentioned", "mentioned"]


def test_seniority_compares_years_with_dated_roles_and_level_with_history():
    resume = _resume("Main", _worked("Python", years=6))
    applications = [_application(resume, level="senior") for _ in range(3)]
    profile = build_profile([resume], applications, TODAY)
    assert _part(score_job(profile, _job(level="senior", years=5)), "Seniority").score == 100
    assert _part(score_job(profile, _job(level="principal", years=5)), "Seniority").score == 50
    assert _part(score_job(profile, _job(level="senior", years=8)), "Seniority").score == 76
    assert _check(score_job(profile, _job(years=8)), "8+ years of experience").verdict == "weak"
    assert (
        _check(score_job(profile, _job(years=12)), "12+ years of experience").verdict == "missing"
    )


def test_degree_is_checked_against_the_education_section():
    education = "\\section*{Education}\nState University, Bachelor's in Computer Science\n"
    tex = _worked("Python").replace(r"\end{document}", education + r"\end{document}")
    profile = build_profile([_resume("Main", tex)], [], TODAY)

    def degree(level, required=True, equivalent_ok=False):
        asks = PostingRequirements(degree=DegreeRequirement(level, required, equivalent_ok))
        return score_job(profile, _job(required=("Python",), requirements=asks))

    met = degree(DegreeLevel.BACHELOR)
    assert _check(met, "Bachelor's degree").verdict == "met"
    assert _part(met, "Education").score == 100
    assert _check(degree(DegreeLevel.MASTER), "Master's degree").verdict == "missing"
    assert (
        _check(degree(DegreeLevel.MASTER, equivalent_ok=True), "Master's degree").verdict == "weak"
    )
    preferred = degree(DegreeLevel.MASTER, required=False)
    assert _check(preferred, "Master's degree").requirement == "nice_to_have"
    assert "Education" not in [part.label for part in preferred.parts]


def test_a_missing_clearance_caps_the_score_and_citizenship_isnt_scored():
    profile = build_profile([_resume("Main", _worked("Python"))], [], TODAY)
    plain = score_job(profile, _job(required=("Python",)))
    cleared = score_job(
        profile,
        _job(
            required=("Python",), requirements=PostingRequirements(clearance=True, citizenship=True)
        ),
    )
    assert plain.score > 40
    assert cleared.knocked_out is True
    assert cleared.score == 40
    assert _check(cleared, "Security clearance").verdict == "missing"
    assert _check(cleared, "Citizenship").verdict == "unknown"
    citizen = score_job(
        profile, _job(required=("Python",), requirements=PostingRequirements(citizenship=True))
    )
    assert citizen.score == plain.score
    assert citizen.knocked_out is False


def test_generic_role_says_nothing_about_role_fit():
    resume = _resume("Main", _worked("Python"))
    profile = build_profile([resume], [_application(resume, role="backend")], TODAY)
    labels = [part.label for part in score_job(profile, _job(role="general_software")).parts]
    assert "Role type" not in labels
    assert _part(score_job(profile, _job(role="frontend")), "Role type").score == 0
    assert _part(score_job(profile, _job(role="backend")), "Role type").score == 100


def test_callback_estimate_leans_on_similar_applications_but_stays_cautious():
    resume = _resume("Main", _worked("Python", "React"))
    applications = [
        _application(resume, status="interview", stack=("Python",), company="Hit"),
        _application(resume, status="rejected", stack=("Rust",), company="Miss"),
        _application(resume, status="rejected", stack=("Scala",), company="Miss 2"),
    ]
    profile = build_profile([resume], applications, TODAY)
    close = score_job(profile, _job(required=("Python",))).callback
    far = score_job(profile, _job(required=("Rust",))).callback
    assert close.applications == 3
    assert close.callbacks == 1
    assert close.similar_applications == 1
    assert close.closest[0].company == "Hit"
    assert close.closest[0].similarity == 1
    assert close.rate > far.rate
    # One matching callback must not read as a certainty.
    assert close.rate < 0.75


def test_ties_dont_depend_on_the_order_rows_arrive_in():
    first = _resume("First", _worked("Python"), days_ago=0)
    second = _resume("Second", _worked("Python"), days_ago=0)
    second.created_at = first.created_at
    applications = [
        _application(
            first if index % 2 else second,
            status="interview" if index % 2 else "rejected",
            stack=("Python",),
        )
        for index in range(8)
    ]
    for application in applications:
        application.id = uuid.uuid4()
    job = _job(required=("Python",), nice=("Rust",))
    forward = score_job(build_profile([first, second], applications, TODAY), job)
    backward = score_job(build_profile([second, first], list(reversed(applications)), TODAY), job)
    assert forward == backward
    assert forward.callback.similar_applications == 5


def test_a_posting_isnt_counted_as_similar_to_itself():
    resume = _resume("Main", _worked("Python"))
    application = _application(resume, status="interview", stack=("Python",))
    profile = build_profile([resume], [application], TODAY)
    job = _job(required=("Python",))
    own = JobFacts(**{**job.__dict__, "posting_id": application.posting.id})
    assert score_job(profile, own).callback.similar_applications == 0
    assert score_job(profile, job).callback.similar_applications == 1


@pytest.mark.parametrize(
    ("query", "title", "text", "expected"),
    [
        ("python", "Engineer", "We use Python", True),
        ("python django", "Engineer", "We use Python", False),
        ("python OR django", "Engineer", "We use Django", True),
        ("(python OR go) AWS", "Engineer", "Python on AWS", True),
        ("(python OR go) AWS", "Engineer", "Ruby on AWS", False),
        ("python -clearance", "Engineer", "Python, clearance needed", False),
        ('"platform engineer"', "Senior Platform Engineer", "", True),
        ('"platform engineer"', "Platform Support Engineer", "", False),
        ("title:senior python", "Senior Engineer", "Python", True),
        ("title:senior python", "Engineer", "Senior Python role", False),
        ("-title:manager python", "Engineering Manager", "Python", False),
        ("company:acme python", "Engineer", "Python", True),
        ("company:globex python", "Engineer", "Python", False),
        ("-(clearance OR citizenship) python", "Engineer", "Python, US citizenship", False),
        ("python OR", "Engineer", "Python", True),
        ("(python", "Engineer", "Python", True),
        ("python)", "Engineer", "Python", True),
        ("c++", "Engineer", "Modern C++ code", True),
        ("full-stack", "Full-Stack Developer", "", True),
    ],
)
def test_query_syntax(query, title, text, expected):
    assert parse_query(query).matches(title, "Acme", text) is expected


def test_boards_receive_plain_words_only():
    assert parse_query('"platform engineer" (python OR go) -clearance').board_keywords == (
        "platform engineer python"
    )
    assert parse_query("title:senior python").board_keywords == "senior python"
    assert parse_query("-clearance").board_keywords == ""


def test_exclusions_apply_even_to_boards_that_search_themselves():
    query = parse_query("python -clearance")
    assert query.matches("Engineer", "Acme", "short snippet", trust_text=True) is True
    assert query.matches("Engineer", "Acme", "needs clearance", trust_text=True) is False


def _listing_job(index, **overrides):
    job = {
        "url": f"https://remotive.com/{index}",
        "title": f"Python Engineer {index}",
        "company_name": f"Company {index}",
        "job_type": "full_time",
        "publication_date": (TODAY - timedelta(days=index)).isoformat(),
        "candidate_required_location": "USA",
        "description": "<h3>Requirements</h3><ul><li>Python</li></ul>",
    }
    return {**job, **overrides}


@pytest.fixture
def remotive(monkeypatch):
    listings_module.clear_cache()
    jobs = []

    def handler(request):
        host = request.url.host
        if host == "remotive.com":
            return httpx.Response(200, json={"jobs": jobs})
        if host == "remoteok.com":
            return httpx.Response(200, json=[])
        if host == "www.arbeitnow.com":
            return httpx.Response(200, json={"data": []})
        if host == "www.themuse.com":
            return httpx.Response(200, json={"results": []})
        return httpx.Response(200, json={"jobs": []})

    monkeypatch.setattr(
        listings_module, "new_client", lambda: httpx.Client(transport=httpx.MockTransport(handler))
    )
    yield jobs
    listings_module.clear_cache()


def _search(client, **params):
    response = client.get("/api/listings", params={"q": "python", "location": "", **params})
    assert response.status_code == 200, response.text
    return response.json()


def _titles(body):
    return [listing["title"] for listing in body["listings"]]


def _upload(client, text):
    tex = rf"\begin{{document}}{text}\end{{document}}".encode()
    response = client.post("/api/resumes", files={"file": ("r.tex", tex, "text/x-tex")})
    assert response.status_code in (200, 201), response.text
    return response.json()


def test_route_has_no_match_without_a_resume(client, remotive):
    remotive.append(_listing_job(1))
    body = _search(client)
    assert body["has_profile"] is False
    assert body["sort"] == "newest"
    assert body["listings"][0]["match"] is None


def test_route_scores_and_sorts_by_match_with_a_resume(client, remotive):
    _upload(client, "Python and Rust")
    remotive.extend(
        [
            _listing_job(
                1,
                title="Python Newer",
                description="<h3>Requirements</h3><p>Python, Scala, Kotlin</p>",
            ),
            _listing_job(
                2, title="Python Older", description="<h3>Requirements</h3><p>Python, Rust</p>"
            ),
        ]
    )
    body = _search(client)
    assert body["has_profile"] is True
    assert body["sort"] == "match"
    assert _titles(body) == ["Python Older", "Python Newer"]
    assert body["listings"][0]["match"]["missing_required"] == []
    assert _titles(_search(client, sort="newest")) == ["Python Newer", "Python Older"]
    assert _titles(_search(client, min_match=body["listings"][0]["match"]["score"])) == [
        "Python Older"
    ]


def test_route_filters(client, remotive):
    remotive.extend(
        [
            _listing_job(
                1,
                title="Senior Python Engineer",
                salary="$150,000 - $170,000",
                description="<h3>Requirements</h3><p>Python, AWS. 5+ years of experience</p>",
            ),
            _listing_job(
                3,
                title="Junior Python Engineer",
                job_type="contract",
                description="<h3>Requirements</h3><p>Python, Django. 1+ years of experience</p>",
            ),
            _listing_job(
                60, title="Python Developer", description="<p>Python, security clearance</p>"
            ),
        ]
    )
    assert _titles(_search(client, level="senior")) == ["Senior Python Engineer"]
    assert _titles(_search(client, level=["senior", "junior"], sort="newest")) == [
        "Senior Python Engineer",
        "Junior Python Engineer",
    ]
    assert _titles(_search(client, employment_type="contract")) == ["Junior Python Engineer"]
    assert _titles(_search(client, stack="AWS")) == ["Senior Python Engineer"]
    assert _titles(_search(client, stack=["AWS", "Django"])) == []
    assert "Junior Python Engineer" not in _titles(_search(client, exclude_stack="Django"))
    assert _titles(_search(client, has_salary=True)) == ["Senior Python Engineer"]
    # A job with no stated salary or years isn't assumed to fail.
    assert len(_titles(_search(client, salary_min=200_000))) == 2
    assert "Senior Python Engineer" not in _titles(_search(client, years_max=3))
    assert "Python Developer" not in _titles(_search(client, posted_within_days=30))
    assert _titles(_search(client, source="Jobicy")) == []
    assert _titles(_search(client, q="python -clearance", sort="newest")) == [
        "Senior Python Engineer",
        "Junior Python Engineer",
    ]
    technologies = {row["label"] for row in _search(client, level="junior")["technologies"]}
    assert "AWS" in technologies


def test_route_salary_sort_and_pages(client, remotive):
    remotive.extend(_listing_job(index) for index in range(1, 56))
    remotive.append(_listing_job(100, title="Python Top Pay", salary="$200,000 - $220,000"))
    first = _search(client, sort="salary")
    assert first["total"] == 56
    assert len(first["listings"]) == first["page_size"] == 50
    assert first["listings"][0]["title"] == "Python Top Pay"
    second = _search(client, sort="salary", page=2)
    assert second["page"] == 2
    assert len(second["listings"]) == 6


def test_route_hides_tracked_and_rejects_exclusion_only_search(client, remotive):
    remotive.append(_listing_job(1))
    listing = _search(client)["listings"][0]
    client.post(
        "/api/postings",
        json={"company": listing["company"], "title": listing["title"], "url": listing["url"]},
    )
    assert _titles(_search(client, hide_tracked=True)) == []
    assert client.get("/api/listings", params={"q": "-clearance"}).status_code == 422
    invalid = client.get("/api/listings", params={"q": "python", "level": "nonsense"})
    assert invalid.status_code == 422


def test_route_filters_by_tracking_and_adds_saved_postings(client, remotive):
    remotive.extend(
        [
            _listing_job(1, title="Python Tracked"),
            _listing_job(2, title="Python Applied"),
            _listing_job(3, title="Python Fresh"),
        ]
    )
    rows = {row["title"]: row for row in _search(client)["listings"]}
    postings = {}
    for title in ("Python Tracked", "Python Applied"):
        row = rows[title]
        response = client.post(
            "/api/postings",
            json={"company": row["company"], "title": title, "url": row["url"]},
        )
        postings[title] = response.json()["id"]
    # Saved by hand, with no URL and no board result to match.
    offline = client.post(
        "/api/postings",
        json={"company": "Offline Co", "title": "Python Offline", "raw_text": "Python"},
    ).json()
    applied = client.post(
        "/api/applications",
        json={
            "company": rows["Python Applied"]["company"],
            "role_title": "Python Applied",
            "posting_id": postings["Python Applied"],
            "status": "screening",
        },
    )
    assert applied.status_code == 201, applied.text

    everything = _search(client, sort="newest")
    # Without a tracking filter, only board results show.
    assert _titles(everything) == ["Python Tracked", "Python Applied", "Python Fresh"]
    assert everything["tracking_counts"] == {"untracked": 1, "tracked": 2, "screening": 1}

    assert _titles(_search(client, tracking="untracked")) == ["Python Fresh"]
    tracked = _search(client, tracking="tracked", sort="newest")
    assert set(_titles(tracked)) == {"Python Tracked", "Python Offline"}
    saved = next(row for row in tracked["listings"] if row["title"] == "Python Offline")
    assert saved["posting_id"] == offline["id"]
    assert saved["url"] is None
    assert saved["source"] == "Saved"
    assert _titles(_search(client, tracking=["screening", "untracked"], sort="newest")) == [
        "Python Applied",
        "Python Fresh",
    ]
    # The other filters still apply to saved postings.
    assert _titles(_search(client, tracking="tracked", stack="Rust")) == []
    invalid = client.get("/api/listings", params={"q": "python", "tracking": "nonsense"})
    assert invalid.status_code == 422


def test_listing_tracking_covers_every_application_status():
    statuses = {status.value for status in ApplicationStatus}
    assert statuses <= {item.value for item in ListingTracking}


def test_posting_detail_includes_a_match_estimate(client):
    _upload(client, "Python and Rust")
    response = client.post(
        "/api/postings",
        json={
            "company": "Acme",
            "title": "Backend Engineer",
            "raw_text": "Requirements\n- Python\n- Scala\n",
        },
    )
    assert response.status_code == 201, response.text
    match = response.json()["match"]
    assert match["missing_required"] == ["Scala"]
    # The uploaded resume has no sections, so Python is only listed: named, but not backed.
    assert match["weak_required"] == ["Python"]
    assert match["best_resume"]["required_weak"] == 1
    assert [check["verdict"] for check in match["checklist"]] == ["weak", "missing"]
    assert match["callback"] is None


def test_a_posting_is_scored_without_its_own_applications(client):
    resume = _upload(client, "Python developer")
    created = client.post(
        "/api/applications",
        json={
            "company": "Acme",
            "role_title": "Unusual Wizard",
            "resume_id": resume["id"],
            "applied_on": TODAY.isoformat(),
            "status": "interview",
            "posting_text": "Requirements\n- Python\n",
        },
    )
    assert created.status_code == 201, created.text
    posting = client.get(f"/api/postings/{created.json()['posting_id']}").json()
    assert posting["match"]["applications"] == 0
    assert posting["match"]["callback"] is None
    title = next(part for part in posting["match"]["parts"] if part["label"] == "Title")
    assert title["score"] == 0


def test_listing_shows_the_latest_application_status(client, remotive):
    remotive.append(_listing_job(1))
    listing = _search(client)["listings"][0]
    assert listing["application_status"] is None
    created = client.post(
        "/api/applications",
        json={
            "company": listing["company"],
            "role_title": listing["title"],
            "job_url": listing["url"],
            "posting_text": listing["description"],
            "applied_on": TODAY.isoformat(),
        },
    )
    assert created.status_code == 201, created.text
    updated = _search(client)["listings"][0]
    assert updated["posting_id"] == created.json()["posting_id"]
    assert updated["application_status"] == "applied"
    client.patch(f"/api/applications/{created.json()['id']}", json={"status": "interview"})
    assert _search(client)["listings"][0]["application_status"] == "interview"


def test_route_shows_baselines_filters_by_them_and_marks_staged(client, remotive):
    tex = _tex(roles=[("Backend Engineer", "Jan 2020 -- Present", ["Python services."])])
    baseline = client.post(
        "/api/resumes/baselines",
        files={"file": ("b.tex", tex.encode(), "text/x-tex")},
        data={"label": "Backend", "rules": '{"focus_skills": ["Python"], "min_score": 0}'},
    ).json()
    remotive.extend(
        [
            _listing_job(1, title="Python Engineer"),
            _listing_job(
                2,
                title="Python Rust Engineer",
                description="<h3>Requirements</h3><p>Rust</p>",
            ),
        ]
    )
    body = _search(client)
    picks = {listing["title"]: listing["baseline"] for listing in body["listings"]}
    assert picks["Python Engineer"]["label"] == "Backend"
    assert picks["Python Engineer"]["covered"] is True
    assert picks["Python Rust Engineer"]["covered"] is False
    assert _titles(_search(client, fits_baseline=True)) == ["Python Engineer"]

    listing = next(item for item in body["listings"] if item["title"] == "Python Engineer")
    posting = client.post(
        "/api/postings",
        json={"company": listing["company"], "title": listing["title"], "url": listing["url"]},
    ).json()
    client.post(f"/api/postings/{posting['id']}/stage", json={"resume_id": baseline["id"]})
    staged = _search(client, tracking="staged")
    assert _titles(staged) == ["Python Engineer"]
    assert staged["listings"][0]["staged"] is True
    assert staged["listings"][0]["staged_resume_id"] == baseline["id"]
    assert staged["tracking_counts"]["staged"] == 1


def test_a_listing_s_callback_estimate_leaves_out_its_own_application(client, remotive):
    """The posting drawer leaves a job's own applications out, and the listing row must too."""
    resume = _upload(client, "Python developer")
    remotive.append(_listing_job(1, title="Python Engineer"))
    listing = _search(client)["listings"][0]
    created = client.post(
        "/api/applications",
        json={
            "company": listing["company"],
            "role_title": listing["title"],
            "job_url": listing["url"],
            "posting_text": listing["description"],
            "resume_id": resume["id"],
            "applied_on": TODAY.isoformat(),
            "status": "interview",
        },
    )
    assert created.status_code == 201, created.text

    match = _search(client)["listings"][0]["match"]

    # Its only application is its own, so there's nothing to estimate from, as in the drawer.
    assert match["callback"] is None
    assert match["applications"] == 0


def test_a_listing_s_title_score_leaves_out_its_own_application(client, remotive):
    """The posting drawer scores a job without its own applications, so the row must too."""
    resume = _upload(client, "Python developer")
    remotive.append(_listing_job(1, title="Unusual Wizard"))
    listing = _search(client, q="wizard")["listings"][0]
    client.post(
        "/api/applications",
        json={
            "company": listing["company"],
            "role_title": listing["title"],
            "job_url": listing["url"],
            "posting_text": listing["description"],
            "resume_id": resume["id"],
            "applied_on": TODAY.isoformat(),
        },
    )

    match = _search(client, q="wizard")["listings"][0]["match"]

    title = next(part for part in match["parts"] if part["label"] == "Title")
    assert title["score"] == 0
