"""Posting text for postings saved with a link and none: copied from the catalog in the request,
else read from the link by the tailor worker (the tailor service is faked here)."""

import uuid

from sqlalchemy import select
from test_reject_analysis import FakeAnalyzer, _answer, _report, _setup, _work

from app.companies.catalog import CatalogListing, record_listing
from app.models import JobPosting, RejectAnalysis, TailorStatus
from app.postings.text import (
    SHORT_TEXT_ERROR,
    FetchedText,
    fetch_next_text,
    queue_missing_texts,
)
from app.workers import TailorUnavailable

LINK = "https://boards.greenhouse.io/acme/jobs/4001"
TEXT = (
    "Backend Engineer\n\n"
    "Requirements:\n"
    "- 3+ years of Python\n"
    "- Kafka and Redis\n\n"
    "About the team: we run the payments platform that moves money for thousands of small "
    "businesses, and we care about careful engineering, clear writing and calm on-call. You will "
    "design services, review code, and work with product and support to understand how the "
    "platform is used day to day. We hire across the United States and meet in person twice a "
    "year. The role reports to the payments engineering manager and works closely with the "
    "risk team on fraud controls and with finance on settlement."
)
assert len(TEXT) >= 500


class FakeFetch:
    def __init__(self, *answers, during=None):
        self.answers = list(answers)
        self.urls: list[str] = []
        self.during = during

    def __call__(self, url):
        self.urls.append(url)
        if self.during is not None:
            self.during()
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def _catalog(session_factory, text=TEXT, url=LINK):
    with session_factory() as db:
        record_listing(
            db,
            CatalogListing(
                source="greenhouse", url=url, company="Acme", title="Backend Engineer", text=text
            ),
        )
        db.commit()


def _save(client, url=LINK, text=None):
    response = client.post(
        "/api/postings",
        json={"company": "Acme", "title": "Backend Engineer", "url": url, "raw_text": text},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _posting(client, posting_id):
    response = client.get(f"/api/postings/{posting_id}")
    assert response.status_code == 200, response.text
    return response.json()


def _fetch(session_factory, fetch):
    with session_factory() as db:
        return fetch_next_text(db, fetch)


# --- In the request -------------------------------------------------------------------------


def test_a_link_in_the_catalog_gets_its_text_at_once(client, session_factory):
    _catalog(session_factory)
    # Tracking parameters don't hide it from the catalog.
    posting = _save(client, url=f"{LINK}?gh_src=abc&utm_source=x")
    assert posting["raw_text"] == TEXT
    assert posting["text_status"] is None
    assert {item["name"] for item in posting["technologies"]} >= {"Python", "Kafka", "Redis"}


def test_a_catalog_snippet_isnt_kept_and_the_link_waits_to_be_read(client, session_factory):
    _catalog(session_factory, text="Backend Engineer. Python, Kafka.")
    posting = _save(client)
    assert posting["raw_text"] == ""
    assert posting["text_status"] == "pending"


def test_an_application_logged_with_a_link_and_no_text_waits_too(client):
    response = client.post(
        "/api/applications",
        json={"company": "Acme", "role_title": "Backend Engineer", "job_url": LINK},
    )
    assert response.status_code == 201, response.text
    assert _posting(client, response.json()["posting_id"])["text_status"] == "pending"


def test_nothing_waits_with_text_or_without_a_link(client):
    assert _save(client, text=TEXT)["text_status"] is None
    assert _save(client, url=None)["text_status"] is None


# --- The worker reads the link ----------------------------------------------------------------


def test_the_worker_reads_a_pending_link_once(client, session_factory):
    posting = _save(client)
    fetch = FakeFetch(FetchedText(ok=True, text=f"  {TEXT}\n"))

    assert _fetch(session_factory, fetch) is True
    assert _fetch(session_factory, fetch) is False

    assert fetch.urls == [LINK]
    saved = _posting(client, posting["id"])
    assert saved["raw_text"] == TEXT
    assert saved["text_status"] == "fetched"
    assert {item["name"] for item in saved["technologies"]} >= {"Python", "Kafka"}
    with session_factory() as db:
        assert db.get(JobPosting, uuid.UUID(posting["id"])).text_fetched_at is not None


def test_a_failed_read_says_why_and_try_again_waits_again(client, session_factory):
    unreachable = _save(client)
    short = _save(client, url="https://careers.example.com/jobs/77")
    fetch = FakeFetch(
        FetchedText(ok=False, error="The page couldn't be reached."),
        FetchedText(ok=True, text="Loading..."),
    )
    _fetch(session_factory, fetch)
    _fetch(session_factory, fetch)

    failed = _posting(client, unreachable["id"])
    assert (failed["text_status"], failed["text_error"]) == (
        "failed",
        "The page couldn't be reached.",
    )
    assert _posting(client, short["id"])["text_error"] == SHORT_TEXT_ERROR
    # Not tried again on its own.
    assert _fetch(session_factory, FakeFetch()) is False

    again = client.post(f"/api/postings/{unreachable['id']}/fetch-text")
    assert again.status_code == 200, again.text
    assert (again.json()["text_status"], again.json()["text_error"]) == ("pending", None)


def test_try_again_needs_a_link_and_no_text(client):
    no_link = _save(client, url=None)
    has_text = _save(client, url="https://example.com/jobs/1", text=TEXT)
    assert client.post(f"/api/postings/{no_link['id']}/fetch-text").status_code == 422
    assert client.post(f"/api/postings/{has_text['id']}/fetch-text").status_code == 409


def test_an_outage_leaves_the_posting_waiting(client, session_factory):
    posting = _save(client)
    fetch = FakeFetch(TailorUnavailable("The tailoring service couldn't be reached."))
    try:
        _fetch(session_factory, fetch)
    except TailorUnavailable:
        pass
    else:
        raise AssertionError("an outage should reach the worker, which waits before retrying")
    assert _posting(client, posting["id"])["text_status"] == "pending"


# --- Edits ------------------------------------------------------------------------------------


def test_pasting_text_clears_the_status_and_wins_over_a_read_in_progress(client, session_factory):
    posting = _save(client)
    pasted = f"Pasted by hand.\n{TEXT}"

    def paste():
        response = client.patch(f"/api/postings/{posting['id']}", json={"raw_text": pasted})
        assert response.status_code == 200, response.text
        assert response.json()["text_status"] is None

    _fetch(session_factory, FakeFetch(FetchedText(ok=True, text=TEXT), during=paste))

    saved = _posting(client, posting["id"])
    assert saved["raw_text"] == pasted
    assert saved["text_status"] is None


def test_a_form_save_keeps_a_failed_read_but_a_new_link_is_looked_for(client, session_factory):
    posting = _save(client)
    _fetch(session_factory, FakeFetch(FetchedText(ok=False, error="The page answered 404.")))

    # The drawer's form sends the link with every save.
    same = client.patch(f"/api/postings/{posting['id']}", json={"url": LINK, "company": "Acme Co"})
    assert same.json()["text_status"] == "failed"

    moved = client.patch(
        f"/api/postings/{posting['id']}", json={"url": "https://jobs.lever.co/acme/" + "a" * 36}
    )
    assert (moved.json()["text_status"], moved.json()["text_error"]) == ("pending", None)

    unlinked = client.patch(f"/api/postings/{posting['id']}", json={"url": None})
    assert unlinked.json()["text_status"] is None


# --- Reports written without the text -----------------------------------------------------------


def test_text_arriving_later_queues_a_report_written_without_it(client, session_factory):
    sent = _setup(client)
    application = client.post(
        "/api/applications",
        json={
            "company": "Acme",
            "role_title": "Backend Engineer",
            "job_url": LINK,
            "status": "rejected",
            "resume_id": sent["id"],
        },
    ).json()
    analyzer = FakeAnalyzer(_answer())
    _work(session_factory, analyzer)
    assert analyzer.payloads == []
    assert "no text" in _report(client, application["id"])["analysis"]["note"]

    _fetch(session_factory, FakeFetch(FetchedText(ok=True, text=TEXT)))

    with session_factory() as db:
        analysis = db.scalar(select(RejectAnalysis))
        assert (analysis.status, analysis.note) == (TailorStatus.QUEUED.value, None)
    # Run again the same day, which a re-run asked for by hand couldn't be.
    _work(session_factory, analyzer)
    assert analyzer.payloads[0]["posting"]["text"] == TEXT
    assert _report(client, application["id"])["analysis"]["summary"]


def test_a_report_with_another_note_isnt_queued_again(client, session_factory):
    sent = _setup(client)
    application = client.post(
        "/api/applications",
        json={
            "company": "Acme",
            "role_title": "Backend Engineer",
            "job_url": LINK,
            "status": "rejected",
            "resume_id": sent["id"],
        },
    ).json()
    _work(session_factory, FakeAnalyzer())
    with session_factory() as db:
        analysis = db.scalar(select(RejectAnalysis))
        analysis.note = "The written report couldn't be made."
        db.commit()

    client.patch(f"/api/postings/{application['posting_id']}", json={"raw_text": TEXT})

    with session_factory() as db:
        assert db.scalar(select(RejectAnalysis)).status == TailorStatus.DONE.value


# --- Postings saved before this existed ---------------------------------------------------------


def test_queue_missing_texts_copies_from_the_catalog_or_marks_pending(client, session_factory):
    _catalog(session_factory)
    in_catalog = uuid.UUID(_save(client, url=f"{LINK}?utm_source=x")["id"])
    elsewhere = uuid.UUID(_save(client, url="https://example.com/jobs/2")["id"])
    _save(client, url="https://example.com/jobs/3", text=TEXT)
    _save(client, url=None)
    with session_factory() as db:
        # As they were before this chunk: no text and no status.
        for posting in db.scalars(select(JobPosting)):
            if not posting.raw_text.strip() or posting.id == in_catalog:
                posting.raw_text = ""
                posting.text_status = None
        user_id = db.get(JobPosting, elsewhere).user_id
        db.commit()

        assert queue_missing_texts(db, user_id) == (1, 1)

    assert _posting(client, str(in_catalog))["raw_text"] == TEXT
    assert _posting(client, str(elsewhere))["text_status"] == "pending"
    with session_factory() as db:
        # Running it again finds nothing new: the pending one already waits.
        assert queue_missing_texts(db, user_id) == (0, 0)
