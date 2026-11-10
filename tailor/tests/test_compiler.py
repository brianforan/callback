"""The compiler client, against a fake HTTP transport. No compiler service is needed."""

import json

import httpx
import pytest

from tailor.compiler import CompilerUnavailable, http_compiler


def _compiler(handler):
    client = httpx.Client(base_url="http://compiler.test", transport=httpx.MockTransport(handler))
    return http_compiler(client)


def test_sends_the_source_and_page_limit():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={"ok": True, "pdf_base64": "JVBERg==", "pages": 1})

    compile_tex = _compiler(handler)

    result = compile_tex("\\documentclass{article}", 1)

    assert seen == [("POST", "/compile", {"tex": "\\documentclass{article}", "max_pages": 1})]
    assert result.ok is True
    assert result.pages == 1


def test_returns_the_compilers_reasons():
    reasons = ["The PDF is 2 pages. It must be exactly 1."]
    compile_tex = _compiler(
        lambda request: httpx.Response(200, json={"ok": False, "reasons": reasons})
    )

    result = compile_tex("x", 1)

    assert result.ok is False
    assert result.reasons == reasons


def test_an_error_status_is_unavailable():
    compile_tex = _compiler(lambda request: httpx.Response(500))

    with pytest.raises(CompilerUnavailable, match="returned an error \\(500\\)"):
        compile_tex("x", 1)


def test_an_unreadable_answer_is_unavailable():
    compile_tex = _compiler(lambda request: httpx.Response(200, json={"unexpected": 1}))

    with pytest.raises(CompilerUnavailable, match="couldn't be read"):
        compile_tex("x", 1)


def test_a_network_failure_is_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    compile_tex = _compiler(handler)

    with pytest.raises(CompilerUnavailable, match="couldn't be reached"):
        compile_tex("x", 1)


def test_a_timeout_is_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    compile_tex = _compiler(handler)

    with pytest.raises(CompilerUnavailable, match="took too long"):
        compile_tex("x", 1)
