"""The helpers every Scout stage shares. The agent these used to test is retired."""

from types import SimpleNamespace

from tailor.scout import _AGENCY, _result_urls, normalize_url

POSTING = "https://jobs.lever.co/acme/123"


def test_normalize_url_compares_equal_links():
    assert normalize_url("HTTPS://Jobs.Lever.co/acme/123/#apply") == POSTING
    assert normalize_url("javascript:alert(1)") is None
    assert normalize_url("") is None


def test_result_urls_read_search_and_fetch_results_and_skip_errors():
    search = SimpleNamespace(
        type="web_search_tool_result",
        content=[
            SimpleNamespace(type="web_search_result", url=POSTING + "/"),
            SimpleNamespace(type="web_search_result", url="https://a.io/x"),
        ],
    )
    fetched = SimpleNamespace(
        type="web_fetch_tool_result",
        content=SimpleNamespace(type="web_fetch_result", url="https://b.io"),
    )
    error = SimpleNamespace(
        type="web_search_tool_result",
        content=SimpleNamespace(type="web_search_tool_result_error", error_code="unavailable"),
    )
    assert _result_urls(search) == {POSTING, "https://a.io/x"}
    assert _result_urls(fetched) == {"https://b.io"}
    assert _result_urls(error) is None


def test_find_employer_no_longer_asks_the_judge_to_research():
    """The judge can't search, so the old agent's research wording would only confuse it."""
    assert "web research" not in _AGENCY["find_employer"]
    assert "Name the agency" in _AGENCY["find_employer"]
