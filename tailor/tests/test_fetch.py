"""The pipeline's code fetcher, with every request answered by a fake transport."""

import json

import httpx
import pytest

from tailor.fetch import Fetcher, FetchRefused, check_url, html_to_text

PUBLIC = "93.184.216.34"


def _resolver(mapping=None):
    def resolve(host):
        return (mapping or {}).get(host, [PUBLIC])

    return resolve


def _fetcher(handler, resolve=None, max_bytes=100_000):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return Fetcher(client, max_bytes=max_bytes, max_chars=5_000, resolve=resolve or _resolver())


def test_check_url_refuses_local_and_private_addresses():
    check_url("https://jobs.lever.co/acme/1", _resolver())
    with pytest.raises(FetchRefused, match="http"):
        check_url("file:///etc/passwd", _resolver())
    with pytest.raises(FetchRefused, match="public host"):
        check_url("http://compiler:8000/compile", _resolver())
    with pytest.raises(FetchRefused, match="private"):
        check_url("http://127.0.0.1/", _resolver())
    with pytest.raises(FetchRefused, match="private"):
        check_url("http://intranet.example.com/", _resolver({"intranet.example.com": ["10.0.0.5"]}))
    with pytest.raises(FetchRefused, match="private"):
        check_url("http://[::1]/", _resolver())


def test_html_to_text_drops_scripts_and_keeps_lists():
    text, title = html_to_text(
        "<html><head><title>Engineer</title><script>x()</script></head>"
        "<body><nav>Menu</nav><p>We build tools.</p><ul><li>Python</li><li>SQL</li></ul></body>"
    )
    assert title == "Engineer"
    assert "x()" not in text and "Menu" not in text
    assert "We build tools." in text and "- Python" in text


def test_greenhouse_posting_comes_from_its_feed():
    def handler(request):
        assert request.url.host == "boards-api.greenhouse.io"
        assert request.url.path == "/v1/boards/acme/jobs/123"
        return httpx.Response(
            200,
            json={
                "title": "Internal Tools Engineer",
                "company_name": "Acme",
                "location": {"name": "Remote, United States"},
                "first_published": "2026-10-30T10:00:00-04:00",
                "content": "&lt;p&gt;Automate finance workflows with Python.&lt;/p&gt;",
            },
        )

    page = _fetcher(handler).fetch("https://job-boards.greenhouse.io/acme/jobs/123")
    assert page.ok and page.source == "greenhouse"
    assert (page.title, page.company, page.location) == (
        "Internal Tools Engineer",
        "Acme",
        "Remote, United States",
    )
    assert page.posted_at == "2026-10-30"
    assert page.text == "Automate finance workflows with Python."


def test_lever_posting_joins_its_sections():
    def handler(request):
        assert request.url.path == "/v0/postings/acme/6ed76ce8-4156-4b60-b120-403538bd66cd"
        return httpx.Response(
            200,
            json={
                "text": "Automation Engineer",
                "descriptionPlain": "Work with ops.",
                "lists": [{"text": "What we require", "content": "<li>Python</li><li>AWS</li>"}],
                "additionalPlain": "Benefits.",
                "categories": {"location": "New York, NY", "commitment": "Full-time"},
                "workplaceType": "hybrid",
                "salaryRange": {
                    "currency": "USD",
                    "min": 140000,
                    "max": 170000,
                    "interval": "per-year",
                },
                "createdAt": 1786469891368,
            },
        )

    page = _fetcher(handler).fetch(
        "https://jobs.lever.co/acme/6ed76ce8-4156-4b60-b120-403538bd66cd"
    )
    assert page.source == "lever" and page.work_mode == "hybrid"
    assert page.salary == "USD 140000-170000 per-year"
    assert "What we require\n- Python" in page.text
    assert page.posted_at.startswith("2026-")


def test_ashby_posting_is_found_on_the_board():
    job_id = "34413f8d-26bf-4bbc-8ade-eb309a0e2245"

    def handler(request):
        assert request.url.path == "/posting-api/job-board/ramp"
        return httpx.Response(
            200,
            json={
                "jobs": [
                    {
                        "id": job_id,
                        "title": "Business Systems Engineer",
                        "location": "New York, NY",
                        "workplaceType": "Hybrid",
                        "employmentType": "FullTime",
                        "descriptionPlain": "Own internal systems.",
                        "publishedAt": "2026-04-07T17:12:35.753+00:00",
                        "compensation": {"scrapeableCompensationSalarySummary": "$150K - $190K"},
                    }
                ]
            },
        )

    page = _fetcher(handler).fetch(f"https://jobs.ashbyhq.com/ramp/{job_id}")
    assert (page.source, page.salary, page.posted_at) == ("ashby", "$150K - $190K", "2026-04-07")


def test_plain_pages_redirects_and_limits():
    body = "<p>" + "We automate how teams work. " * 20 + "</p>"

    html_type = {"content-type": "text/html"}

    def handler(request):
        if request.url.path == "/old":
            return httpx.Response(302, headers={"location": "/new"})
        if request.url.path == "/to-private":
            return httpx.Response(302, headers={"location": "http://10.1.1.1/admin"})
        if request.url.path == "/big":
            return httpx.Response(
                200, headers={"content-type": "text/html"}, content=b"x" * 200_000
            )
        if request.url.path == "/script-only":
            return httpx.Response(
                200, headers={"content-type": "text/html"}, content=b"<div id=app>"
            )
        return httpx.Response(200, headers=html_type, content=body.encode())

    fetcher = _fetcher(handler)
    page = fetcher.fetch("https://careers.acme.com/old")
    assert page.ok and page.text.startswith("We automate")
    assert "private" in fetcher.fetch("https://careers.acme.com/to-private").error
    assert "too large" in fetcher.fetch("https://careers.acme.com/big").error
    assert "browser" in fetcher.fetch("https://careers.acme.com/script-only").error
    assert json.dumps(fetcher.fetch("ftp://x.example/").error)


def test_a_feed_shaped_unlike_the_readers_expect_is_reported_unread():
    def handler(request):
        return httpx.Response(200, json=["not", "an", "object"])

    page = _fetcher(handler).fetch("https://job-boards.greenhouse.io/acme/jobs/123")
    assert page.ok is False
    assert page.error == "The page couldn't be read."


def test_icon_titles_inside_svg_stay_out_of_the_page_title():
    text, title = html_to_text(
        "<html><head><title>Senior Engineer at Acme</title></head><body>"
        "<a><svg><title>Facebook</title><path d='M0'/></svg></a>"
        "<a><svg><title>LinkedIn</title></svg></a><p>We build tools.</p></body></html>"
    )
    assert title == "Senior Engineer at Acme"
    assert "Facebook" not in text


def test_a_page_that_trickles_in_is_cut_off_at_the_deadline():
    """httpx's timeout is per read, so a server sending a little at a time never trips it."""

    def chunks():
        for _ in range(100):
            yield b"x" * 100

    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/html"}, content=chunks())

    ticks = iter(range(0, 10_000, 5))
    client = httpx.Client(transport=httpx.MockTransport(handler))
    fetcher = Fetcher(
        client,
        max_bytes=100_000,
        max_chars=5_000,
        resolve=_resolver(),
        deadline_seconds=30,
        clock=lambda: float(next(ticks)),
    )
    page = fetcher.fetch("https://careers.acme.com/slow")
    assert page.ok is False
    assert page.error == "The page took too long to load."


def test_greenhouse_embed_links_are_read_from_the_feed():
    def handler(request):
        assert request.url.host == "boards-api.greenhouse.io"
        assert request.url.path == "/v1/boards/acme/jobs/123"
        return httpx.Response(
            200,
            json={"title": "Engineer", "company_name": "Acme", "content": "&lt;p&gt;Hi&lt;/p&gt;"},
        )

    page = _fetcher(handler).fetch("https://boards.greenhouse.io/embed/job_app?for=acme&token=123")
    assert page.ok and page.source == "greenhouse"
    assert page.title == "Engineer"


def test_postings_fetch_endpoint_reads_a_link_with_no_model(monkeypatch):
    """POST /postings/fetch: a feed posting comes back whole, an unreadable link with its
    reason, an oversized title clipped, and no API key is needed."""
    from fastapi.testclient import TestClient

    from tailor import main

    def handler(request):
        if request.url.host == "boards-api.greenhouse.io":
            return httpx.Response(
                200,
                json={
                    "title": "Staff Engineer " + "x" * 400,
                    "content": "&lt;p&gt;Build payments in Go.&lt;/p&gt;",
                    "location": {"name": "Remote"},
                },
            )
        return httpx.Response(404)

    monkeypatch.setattr(main, "get_fetcher", lambda: _fetcher(handler))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    main.get_settings.cache_clear()
    try:
        api = TestClient(main.app)
        found = api.post(
            "/postings/fetch", json={"url": "https://boards.greenhouse.io/acme/jobs/123"}
        )
        gone = api.post("/postings/fetch", json={"url": "https://example.com/jobs/9"})
        local = api.post("/postings/fetch", json={"url": "http://127.0.0.1/jobs/9"})
        empty = api.post("/postings/fetch", json={"url": ""})
    finally:
        main.get_settings.cache_clear()

    assert found.status_code == 200
    assert found.json()["ok"] is True
    assert found.json()["text"] == "Build payments in Go."
    assert found.json()["source"] == "greenhouse"
    assert len(found.json()["title"]) == 300
    assert gone.json() == {**gone.json(), "ok": False, "error": "The page answered 404."}
    assert local.json()["ok"] is False
    assert "private or local" in local.json()["error"]
    assert empty.status_code == 422
