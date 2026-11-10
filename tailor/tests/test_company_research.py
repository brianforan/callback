"""Company research with the model faked: the search loop, saving, and failures."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from tailor import main
from tailor.company_research import SAVE_RESEARCH, research_company
from tailor.config import get_settings
from tailor.pipeline import StageFailed
from tailor.schemas import CompanyResearchRequest

SNAPSHOT = {
    "sources": [{"url": "https://acme.com/about", "title": "About Acme", "published_on": ""}],
    "profile": {
        "what_it_does": "Payments software for small businesses.",
        "industry": "Fintech",
        "hq_city": "Austin",
        "hq_country": "United States",
        "founded_year": 2015,
        "website_domain": "acme.com",
        "source": 0,
    },
}
REQUEST = {
    "company": "Acme",
    "domain": "acme.com",
    "title": "Backend Engineer",
    "location": "Remote, US",
    "catalog_sources": ["greenhouse:acme (own)"],
    "open_jobs": 12,
    "today": "2026-11-06",
}


def _message(content, stop_reason="end_turn", searches=0):
    usage = SimpleNamespace(
        input_tokens=500,
        output_tokens=200,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
        server_tool_use=SimpleNamespace(web_search_requests=searches),
    )
    return SimpleNamespace(content=content, stop_reason=stop_reason, usage=usage)


def _text(text):
    return SimpleNamespace(type="text", text=text)


def _save(data=SNAPSHOT):
    return SimpleNamespace(type="tool_use", id="toolu_1", name=SAVE_RESEARCH, input=data)


class Script:
    def __init__(self, *messages):
        self.messages = list(messages)
        self.calls = []

    def __call__(self, **params):
        self.calls.append(params)
        return self.messages.pop(0)


def _request():
    return CompanyResearchRequest.model_validate(REQUEST)


def test_searches_then_saves_the_snapshot(settings):
    script = Script(
        _message([_text("Searching.")], stop_reason="pause_turn", searches=3),
        _message([_text("Done."), _save()], stop_reason="tool_use", searches=2),
    )

    found = research_company(script, _request(), settings)

    assert found.research == SNAPSHOT
    assert found.searches == 5
    assert found.model == settings.company_research_model
    assert found.cost_usd > 0
    first = script.calls[0]
    assert first["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert first["tools"][0] == {
        "type": "web_search_20250305",
        "name": "web_search",
        "max_uses": settings.company_research_max_searches,
    }
    assert first["tools"][1]["name"] == SAVE_RESEARCH
    brief = first["messages"][0]["content"]
    assert "Acme" in brief and "greenhouse:acme (own)" in brief and "12" in brief
    # A paused turn is resumed with what came back so far.
    assert script.calls[1]["messages"][1]["role"] == "assistant"


def test_a_turn_that_ends_without_saving_is_nudged_once(settings):
    script = Script(
        _message([_text("Here is what I found.")], searches=2),
        _message([_save()], stop_reason="tool_use"),
    )
    assert research_company(script, _request(), settings).research == SNAPSHOT
    assert "save_research" in script.calls[1]["messages"][-1]["content"]


def test_used_up_searches_are_nudged_to_save_instead_of_resumed(settings):
    script = Script(
        _message([_text("More to read.")], stop_reason="pause_turn", searches=6),
        _message([_save()], stop_reason="tool_use"),
    )
    research_company(script, _request(), settings)
    assert script.calls[1]["messages"][-1]["role"] == "user"


@pytest.mark.parametrize(
    "messages",
    [
        [_message([_text("No.")], stop_reason="refusal")],
        [_message([_text("…")], stop_reason="max_tokens")],
        [_message([_text("Found things.")]), _message([_text("Still nothing saved.")])],
        [_message([_save({"sources": ["x" * 200_000]})], stop_reason="tool_use")],
    ],
)
def test_no_usable_snapshot_fails_with_its_cost(settings, messages):
    with pytest.raises(StageFailed) as failed:
        research_company(Script(*messages), _request(), settings)
    assert failed.value.cost.spent > 0


def test_endpoint_returns_the_snapshot(monkeypatch):
    script = Script(_message([_save()], stop_reason="tool_use", searches=1))
    monkeypatch.setattr(main, "get_search_message", lambda: script)
    answer = TestClient(main.app).post("/companies/research", json=REQUEST)
    assert answer.status_code == 200, answer.text
    assert answer.json()["research"] == SNAPSHOT
    assert answer.json()["searches"] == 1


def test_endpoint_reports_what_a_failure_cost(monkeypatch):
    script = Script(_message([_text("No.")], stop_reason="refusal"))
    monkeypatch.setattr(main, "get_search_message", lambda: script)
    answer = TestClient(main.app).post("/companies/research", json=REQUEST)
    assert answer.status_code == 502
    assert answer.json()["detail"]["spent"]["cost_usd"] > 0


def test_endpoint_needs_an_api_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    get_settings.cache_clear()
    try:
        answer = TestClient(main.app).post("/companies/research", json=REQUEST)
    finally:
        get_settings.cache_clear()
    assert answer.status_code == 503
