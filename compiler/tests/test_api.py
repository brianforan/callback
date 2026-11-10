"""The /compile endpoint: the PDF, the reasons it fails, and the size and page limits."""

import base64

from compiler import main
from compiler.config import get_settings


def _two_pages(resume_tex: str) -> str:
    return resume_tex.replace("\\end{document}", "\\newpage More\\end{document}")


def test_compile_returns_the_pdf(client, resume_tex):
    result = client.post("/compile", json={"tex": resume_tex}).json()

    assert result["ok"] is True
    assert result["pages"] == 1
    assert base64.b64decode(result["pdf_base64"]).startswith(b"%PDF")
    assert result["reasons"] == []


def test_compile_accepts_two_pages(client, resume_tex):
    result = client.post("/compile", json={"tex": _two_pages(resume_tex)}).json()

    assert result["ok"] is True
    assert result["pages"] == 2


def test_compile_rejects_three_pages(client, resume_tex):
    long = resume_tex.replace("\\end{document}", "\\newpage A\\newpage B\\end{document}")

    result = client.post("/compile", json={"tex": long}).json()

    assert result["ok"] is False
    assert result["pdf_base64"] is None
    assert result["reasons"] == ["The PDF is 3 pages. It must be at most 2."]


def test_a_request_can_ask_for_fewer_pages(client, resume_tex):
    result = client.post("/compile", json={"tex": _two_pages(resume_tex), "max_pages": 1}).json()

    assert result["ok"] is False
    assert result["reasons"] == ["The PDF is 2 pages. It must be exactly 1."]


def test_a_request_cannot_raise_the_page_limit(client, resume_tex):
    long = resume_tex.replace("\\end{document}", "\\newpage A\\newpage B\\end{document}")

    result = client.post("/compile", json={"tex": long, "max_pages": 10}).json()

    assert result["ok"] is False
    assert result["reasons"] == ["The PDF is 3 pages. It must be at most 2."]


def test_compile_rejects_a_source_over_the_size_limit(monkeypatch, client, resume_tex):
    small = get_settings().model_copy(update={"max_source_bytes": 2_048})
    monkeypatch.setattr(main, "get_settings", lambda: small)
    called = []
    monkeypatch.setattr(main, "compile_pdf", lambda *args: called.append(args))

    result = client.post("/compile", json={"tex": resume_tex + "%" * 3_000}).json()

    assert result["ok"] is False
    assert result["reasons"][0].startswith("The .tex is ")
    assert "at most 2 KB" in result["reasons"][0]
    assert called == []


def test_the_size_limit_counts_bytes_not_characters(monkeypatch, client):
    small = get_settings().model_copy(update={"max_source_bytes": 2_048})
    monkeypatch.setattr(main, "get_settings", lambda: small)

    # 1,500 characters, but 3,000 bytes.
    result = client.post("/compile", json={"tex": "é" * 1_500}).json()

    assert result["ok"] is False
    assert result["reasons"][0].startswith("The .tex is 2 KB")


def test_compile_reports_latex_errors(client, resume_tex):
    broken = resume_tex.replace("\\end{document}", "\\nosuchmacro\\end{document}")

    result = client.post("/compile", json={"tex": broken}).json()

    assert result["ok"] is False
    assert result["reasons"][0].startswith("LaTeX didn't compile")


def test_refuses_an_empty_or_enormous_body(client):
    assert client.post("/compile", json={"tex": ""}).status_code == 422
    assert client.post("/compile", json={"tex": "x" * 200_001}).status_code == 422


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}
