import uuid
from datetime import date, datetime

from app.models import Resume
from app.postings.parser import PostingRequirements
from app.resumes.matching import JobFacts
from app.resumes.variants import load_baselines, pick_baseline
from app.schemas import VariantRules
from app.similarity import tex_to_text

TODAY = date(2026, 11, 2)


def _tex(*technologies):
    bullets = "\n".join(rf"\item Built services with {name}." for name in technologies)
    return (
        "\\begin{document}\n\\section*{Experience}\n"
        "\\job{Software Engineer}{Acme}\n{Jan 2020 -- Present}\n"
        f"\\begin{{itemize}}\n{bullets}\n\\end{{itemize}}\n\\end{{document}}"
    )


def _baseline(label, *technologies, rules=None):
    tex = _tex(*technologies)
    return Resume(
        id=uuid.uuid4(),
        label=label,
        source_tex=tex,
        plain_text=tex_to_text(tex),
        created_at=datetime(2026, 10, 1),
        is_variant=True,
        variant_rules=(rules or VariantRules(min_score=0)).model_dump(mode="json"),
    )


def _job(*required, role="backend"):
    return JobFacts(
        title="Engineer",
        level=None,
        role_family=role,
        years_required=None,
        technologies=tuple((name, "required") for name in required),
        requirements=PostingRequirements(),
    )


def test_pick_names_the_best_baseline_and_what_another_could_add():
    backend = _baseline("Backend", "Python", "AWS")
    frontend = _baseline("Frontend", "React")
    baselines = load_baselines([frontend, backend], TODAY)
    pick = pick_baseline(baselines, _job("Python", "AWS", "React"))
    assert pick.label == "Backend"
    assert pick.covered is True
    assert [(item.name, item.baseline) for item in pick.mix] == [("React", "Frontend")]
    assert pick_baseline(baselines, _job("Python")).mix == []


def test_pick_falls_back_to_the_closest_baseline_when_rules_skip_it():
    picky = _baseline("Picky", "Python", rules=VariantRules(deal_breakers=["Go"], min_score=0))
    baselines = load_baselines([picky], TODAY)
    pick = pick_baseline(baselines, _job("Python", "Go"))
    assert pick.covered is False
    assert pick.reasons == ["Requires Go, a deal-breaker for this baseline."]


def test_no_baselines_means_no_pick():
    plain = _baseline("Plain", "Python")
    plain.is_variant = False
    assert load_baselines([plain], TODAY) is None


def _upload(client, *technologies, baseline=True):
    tex = _tex(*technologies).encode()
    path = "/api/resumes/baselines" if baseline else "/api/resumes"
    response = client.post(path, files={"file": ("r.tex", tex, "text/x-tex")})
    assert response.status_code in (200, 201), response.text
    return response.json()


def _posting(client, raw_text="Requirements\n- Python\n- AWS\n", url="https://a.test/1"):
    response = client.post(
        "/api/postings",
        json={"company": "Acme", "title": "Backend Engineer", "url": url, "raw_text": raw_text},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_posting_detail_names_its_baseline(client):
    _upload(client, "Python", "AWS")
    posting = _posting(client)
    assert posting["baseline"]["covered"] is True
    assert posting["baseline"]["label"].startswith("r v")


def test_staging_then_applying_sends_the_staged_resume_and_clears_the_stage(client):
    resume = _upload(client, "Python", "AWS", baseline=False)
    posting = _posting(client)

    staged = client.post(
        f"/api/postings/{posting['id']}/stage",
        json={"resume_id": resume["id"], "notes": "  Title of record: API Engineer  "},
    )
    assert staged.status_code == 200, staged.text
    body = staged.json()
    assert body["staged_at"] is not None
    assert body["staged_resume_label"] == resume["label"]
    assert body["staged_notes"] == "Title of record: API Engineer"
    assert body["staged_match"]["checklist"][0]["verdict"] == "met"
    listed = client.get("/api/postings").json()[0]
    assert listed["staged_resume_id"] == resume["id"]

    application = client.post(
        "/api/applications",
        json={"company": "Acme", "role_title": "Backend Engineer", "posting_id": posting["id"]},
    ).json()
    assert application["resume_id"] == resume["id"]
    after = client.get(f"/api/postings/{posting['id']}").json()
    assert after["staged_at"] is None
    assert after["staged_resume_id"] is None

    again = client.post(f"/api/postings/{posting['id']}/stage", json={"resume_id": resume["id"]})
    assert again.status_code == 409


def test_unstage_and_validation(client, other_client):
    resume = _upload(client, "Python", baseline=False)
    posting = _posting(client)
    client.post(f"/api/postings/{posting['id']}/stage", json={"resume_id": resume["id"]})
    cleared = client.delete(f"/api/postings/{posting['id']}/stage").json()
    assert cleared["staged_at"] is None

    unknown = client.post(
        f"/api/postings/{posting['id']}/stage", json={"resume_id": str(uuid.uuid4())}
    )
    assert unknown.status_code == 422
    foreign = other_client.post(
        f"/api/postings/{posting['id']}/stage", json={"resume_id": resume["id"]}
    )
    assert foreign.status_code == 404


def test_fact_sheet_round_trip_and_isolation(client, other_client):
    active_id = client.get("/api/profiles").json()["active_id"]
    assert client.get("/api/fact-sheet").json() == {
        "profile_id": active_id,
        "content": "",
        "updated_at": None,
    }
    saved = client.put("/api/fact-sheet", json={"content": "# Facts\n- Python: claimable"})
    assert saved.status_code == 200
    assert saved.json()["updated_at"] is not None
    assert client.get("/api/fact-sheet").json()["content"].startswith("# Facts")
    assert other_client.get("/api/fact-sheet").json()["content"] == ""
    too_long = client.put("/api/fact-sheet", json={"content": "x" * 100_001})
    assert too_long.status_code == 422
