"""Uploaded resume PDFs: read into an outline for analysis, and never compiled or tailored."""

import json
from datetime import date

import pytest
from test_tailoring import FACT_SHEET, FakeCompiler, _posting

from app.config import get_settings
from app.resumes.pdf_text import MAX_PAGES, PdfReadError, outline_from_text, read_pdf
from app.resumes.structure import Evidence, parse_resume, skill_evidence
from app.similarity import tex_to_text
from app.tailoring.worker import compile_next_pdf

TODAY = date(2026, 11, 4)

RESUME_LINES = (
    "Jordan Lee",
    "PROFESSIONAL EXPERIENCE",
    "Software Engineer, Acme Corp",
    "Jan 2022 – Present",
    "• Built Kafka pipelines in Python & Go",
    "EDUCATION",
    "Bachelor of Science, 2017",
)


def _escape_pdf(text: str) -> bytes:
    raw = text.encode("cp1252")
    return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def make_pdf(lines: tuple[str, ...] = RESUME_LINES, pages: int = 1) -> bytes:
    """A minimal PDF with each line printed in Helvetica on every page."""
    page_ids = [4 + 2 * index for index in range(pages)]
    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    objects: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: f"<< /Type /Pages /Kids [{kids}] /Count {pages} >>".encode(),
        3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    }
    shown = b" T* ".join(b"(" + _escape_pdf(line) + b") Tj" for line in lines)
    stream = b"BT /F1 11 Tf 14 TL 72 720 Td " + shown + b" ET"
    for page_id in page_ids:
        objects[page_id] = (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 3 0 R >> >> /Contents %d 0 R >>" % (page_id + 1)
        )
        objects[page_id + 1] = b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream"

    out = b"%PDF-1.4\n"
    offsets = {}
    for number in sorted(objects):
        offsets[number] = len(out)
        out += b"%d 0 obj\n" % number + objects[number] + b"\nendobj\n"
    xref = len(out)
    size = len(objects) + 1
    out += b"xref\n0 %d\n0000000000 65535 f \n" % size
    out += b"".join(b"%010d 00000 n \n" % offsets[number] for number in sorted(objects))
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (size, xref)
    return out


def upload_pdf(client, content: bytes | None = None, name: str = "resume.pdf"):
    payload = make_pdf() if content is None else content
    return client.post("/api/resumes", files={"file": (name, payload, "application/pdf")})


def test_outline_marks_headings_and_bullets_and_escapes_the_rest():
    text = (
        "Summary\n"
        "Efﬁcient engineer\n"
        "TECHNICAL SKILLS:\n"
        "Python, C#, 100% uptime_ok {x}\n"
        "Senior Project Manager\n"
        "▪ Ran Kuber\x02\r\nnetes clusters\n"
        "Leadership & Activities\n"
        " jordan@example.com\n"
    )

    outline = outline_from_text(text)

    assert outline.splitlines() == [
        r"\section{Summary}",
        "Efficient engineer",
        r"\section{TECHNICAL SKILLS}",
        r"Python, C\#, 100\% uptime\_ok \{x\}",
        "Senior Project Manager",
        r"\item Ran Kubernetes clusters",
        r"\section{Leadership \& Activities}",
        "jordan@example.com",
    ]
    plain = tex_to_text(outline)
    assert "C#" in plain
    assert "100%" in plain
    assert "Kubernetes" in plain


def test_lowercase_or_long_lines_are_not_headings():
    outline = outline_from_text("Grew team leadership\nExperience leading five engineers\n")
    assert r"\section" not in outline


def test_outline_is_parsed_into_dated_roles():
    structure = parse_resume(outline_from_text("\n".join(RESUME_LINES)), TODAY)

    assert structure.sections == ("PROFESSIONAL EXPERIENCE", "EDUCATION")
    assert len(structure.roles) == 1
    role = structure.roles[0]
    assert "Acme Corp" in role.heading
    assert "Kafka" in role.text
    kafka = skill_evidence(structure, "Kafka")
    assert kafka.evidence == Evidence.EXPERIENCE
    assert kafka.months > 0


def test_read_pdf_extracts_lines_and_pages():
    parsed = read_pdf(make_pdf(pages=2))

    assert parsed.pages == 2
    assert r"\section{PROFESSIONAL EXPERIENCE}" in parsed.outline
    assert r"\item Built Kafka pipelines in Python \& Go" in parsed.outline


@pytest.mark.parametrize(
    ("content", "message"),
    [
        # PDFium may open a broken file as an empty one, so either refusal is right.
        (b"%PDF-1.4\nnot really a pdf", "can't be opened|has no pages"),
        (make_pdf(lines=()), "no selectable text"),
        (make_pdf(pages=MAX_PAGES + 1), "at most"),
    ],
    ids=["damaged", "textless", "too-long"],
)
def test_unusable_pdfs_are_refused_with_a_reason(content, message):
    with pytest.raises(PdfReadError, match=message):
        read_pdf(content)


def test_pdf_upload_is_analysed_and_keeps_the_original(client):
    pdf = make_pdf()

    response = upload_pdf(client, pdf)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["source_format"] == "pdf"
    assert body["pdf_pages"] == 1
    assert body["word_count"] > 10
    detail = client.get(f"/api/resumes/{body['id']}").json()
    assert r"\section{EDUCATION}" in detail["source_tex"]
    assert client.get(f"/api/resumes/{body['id']}/pdf").content == pdf
    again = upload_pdf(client, pdf, name="renamed.pdf")
    assert again.status_code == 200
    assert again.json()["id"] == body["id"]


def test_pdf_upload_rejects_files_that_are_not_pdfs(client):
    assert upload_pdf(client, b"\\begin{document}Hi\\end{document}").status_code == 422
    assert upload_pdf(client, make_pdf(lines=())).status_code == 422
    assert upload_pdf(client, name="resume.docx").status_code == 422


def test_pdf_upload_has_its_own_size_limit(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_resume_pdf_bytes", 100)
    assert upload_pdf(client).status_code == 413


def test_tex_uploads_still_report_their_format(client):
    response = client.post(
        "/api/resumes",
        files={"file": ("resume.tex", rb"\begin{document}Python\end{document}", "text/x-tex")},
    )
    assert response.status_code == 201
    assert response.json()["source_format"] == "tex"


def test_the_compile_worker_leaves_pdf_uploads_alone(client, session_factory):
    assert upload_pdf(client).status_code == 201
    compiler = FakeCompiler()

    with session_factory() as db:
        assert compile_next_pdf(db, compiler) is False

    assert compiler.sources == []


def test_a_pdf_baseline_is_not_tailored(client):
    response = client.post(
        "/api/resumes/baselines",
        files={"file": ("backend.pdf", make_pdf(), "application/pdf")},
        data={"label": "Backend PDF", "rules": json.dumps({"min_score": 0})},
    )
    assert response.status_code == 201, response.text
    assert response.json()["is_variant"] is True
    assert client.put("/api/fact-sheet", json={"content": FACT_SHEET}).status_code == 200
    posting = _posting(client)

    refused = client.post(f"/api/postings/{posting['id']}/tailor")

    assert refused.status_code == 422
    assert "is a PDF" in refused.json()["detail"]
