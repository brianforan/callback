"""Editing a baseline's LaTeX in place: same id, fresh text, and a PDF compiled again."""

import json

from test_api import create_application
from test_pdf_resumes import make_pdf
from test_tailoring import BASELINE_TEX, FakeCompiler, _compiled, _posting, _upload_baseline

from app.config import get_settings
from app.schemas import CompileServiceResult
from app.tailoring.worker import compile_next_pdf

EDITED_TEX = BASELINE_TEX.replace(
    "Moved processing to queue workers.", "Moved processing to Kafka consumers in Go."
)


def _save(client, resume_id, source):
    return client.put(f"/api/resumes/{resume_id}/source", json={"source_tex": source})


def _compile_all(session_factory, compiler):
    with session_factory() as db:
        while compile_next_pdf(db, compiler):
            pass


def test_an_edit_overwrites_the_same_baseline_and_keeps_its_name_and_rules(client):
    baseline = _upload_baseline(client)
    before = client.get(f"/api/resumes/{baseline['id']}").json()

    response = _save(client, baseline["id"], EDITED_TEX)

    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["id"] == baseline["id"]
    assert saved["label"] == "Backend"
    assert saved["is_variant"] is True
    assert saved["variant_rules"] == before["variant_rules"]
    assert saved["word_count"] == before["word_count"] + 2
    detail = client.get(f"/api/resumes/{baseline['id']}").json()
    assert detail["source_tex"] == EDITED_TEX
    assert [resume["id"] for resume in client.get("/api/resumes").json()] == [baseline["id"]]


def test_an_edit_clears_the_pdf_and_the_worker_compiles_the_new_source(client, session_factory):
    baseline = _upload_baseline(client)
    _compile_all(session_factory, FakeCompiler(_compiled()))
    assert client.get(f"/api/resumes/{baseline['id']}/pdf").status_code == 200

    saved = _save(client, baseline["id"], EDITED_TEX).json()

    assert saved["pdf_pages"] is None
    assert saved["pdf_error"] is None
    assert client.get(f"/api/resumes/{baseline['id']}/pdf").status_code == 404
    compiler = FakeCompiler(_compiled(pages=2))
    _compile_all(session_factory, compiler)
    assert compiler.sources == [EDITED_TEX]
    assert client.get("/api/resumes").json()[0]["pdf_pages"] == 2


def test_a_baseline_that_did_not_compile_is_tried_again_after_an_edit(client, session_factory):
    baseline = _upload_baseline(client)
    failed = CompileServiceResult(ok=False, reasons=["Undefined control sequence"])
    _compile_all(session_factory, FakeCompiler(failed))
    assert client.get("/api/resumes").json()[0]["pdf_error"] == "Undefined control sequence"

    assert _save(client, baseline["id"], EDITED_TEX).json()["pdf_error"] is None

    _compile_all(session_factory, FakeCompiler(_compiled()))
    assert client.get("/api/resumes").json()[0]["pdf_pages"] == 1


def test_an_edit_made_while_compiling_is_compiled_again(client, session_factory):
    baseline = _upload_baseline(client)
    sources = []

    def edit_meanwhile(tex):
        sources.append(tex)
        if len(sources) == 1:
            assert _save(client, baseline["id"], EDITED_TEX).status_code == 200
        return _compiled()

    _compile_all(session_factory, edit_meanwhile)

    # The PDF of the old source was dropped, and the edited source compiled next.
    assert sources == [BASELINE_TEX, EDITED_TEX]
    assert client.get("/api/resumes").json()[0]["pdf_pages"] == 1


def test_what_points_at_the_baseline_still_does_after_an_edit(client):
    baseline = _upload_baseline(client)
    applied = create_application(client, resume_id=baseline["id"])
    staged = client.post("/api/postings", json={"company": "Globex", "title": "Engineer"}).json()
    stage = client.post(f"/api/postings/{staged['id']}/stage", json={"resume_id": baseline["id"]})
    assert stage.status_code == 200, stage.text

    saved = _save(client, baseline["id"], EDITED_TEX).json()

    assert saved["application_count"] == 1
    assert client.get(f"/api/applications/{applied['id']}").json()["resume_id"] == baseline["id"]
    posting = client.get(f"/api/postings/{staged['id']}").json()
    assert posting["staged_resume_id"] == baseline["id"]


def test_the_matcher_reads_the_edited_text(client):
    baseline = _upload_baseline(client)
    _posting(client)
    before = client.get("/api/analytics/variants").json()["variants"][0]["evidence"]
    assert "Kafka" not in before["backed"]

    _save(client, baseline["id"], EDITED_TEX)

    after = client.get("/api/analytics/variants").json()["variants"][0]["evidence"]
    assert after["resume_id"] == baseline["id"]
    assert {"Go", "Kafka"} <= set(after["backed"])


def test_saving_the_same_source_keeps_the_pdf(client, session_factory):
    baseline = _upload_baseline(client)
    _compile_all(session_factory, FakeCompiler(_compiled()))

    response = _save(client, baseline["id"], BASELINE_TEX)

    assert response.status_code == 200
    assert response.json()["pdf_pages"] == 1
    assert client.get(f"/api/resumes/{baseline['id']}/pdf").status_code == 200


def test_an_edit_matching_another_resume_is_refused(client):
    baseline = _upload_baseline(client)
    other = client.post(
        "/api/resumes", files={"file": ("other.tex", EDITED_TEX.encode(), "text/x-tex")}
    ).json()

    response = _save(client, baseline["id"], EDITED_TEX)

    assert response.status_code == 409
    assert other["id"] != baseline["id"]
    assert client.get(f"/api/resumes/{baseline['id']}").json()["source_tex"] == BASELINE_TEX


def test_only_tex_baselines_can_be_edited_in_place(client):
    plain = client.post(
        "/api/resumes", files={"file": ("plain.tex", BASELINE_TEX.encode(), "text/x-tex")}
    ).json()
    pdf = client.post(
        "/api/resumes/baselines",
        files={"file": ("backend.pdf", make_pdf(), "application/pdf")},
        data={"label": "Backend PDF", "rules": json.dumps({"min_score": 0})},
    ).json()

    assert _save(client, plain["id"], EDITED_TEX).status_code == 409
    assert _save(client, pdf["id"], EDITED_TEX).status_code == 409
    assert client.get(f"/api/resumes/{plain['id']}").json()["source_tex"] == BASELINE_TEX


def test_an_edit_must_be_usable_text_within_the_size_limit(client):
    baseline = _upload_baseline(client)
    too_large = "%" * (get_settings().max_resume_bytes + 1)

    assert _save(client, baseline["id"], too_large).status_code == 413
    assert _save(client, baseline["id"], "   \n").status_code == 422
    assert _save(client, baseline["id"], "\\begin{document}\x00").status_code == 422
    assert client.put(f"/api/resumes/{baseline['id']}/source", json={}).status_code == 422
    assert client.get(f"/api/resumes/{baseline['id']}").json()["source_tex"] == BASELINE_TEX


def test_another_user_s_baseline_cannot_be_edited(client, other_client):
    baseline = _upload_baseline(client)

    assert _save(other_client, baseline["id"], EDITED_TEX).status_code == 404
    assert client.get(f"/api/resumes/{baseline['id']}").json()["source_tex"] == BASELINE_TEX
