"""The billing worker and the owner's Spend page, with Anthropic's reports faked."""

import uuid
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.billing import worker as billing_worker
from app.billing.worker import (
    BillingUnavailable,
    http_fetcher,
    reconcile,
    run_once,
    sum_usage,
    sync_costs,
)
from app.config import get_settings
from app.models import BillingCost, BillingState, Role, ScoutRun, TailorJob, User

NOW = datetime(2026, 11, 3, 22, 0, tzinfo=UTC)


def _bucket(start: str, **result):
    return {"starting_at": start, "ending_at": start, "results": [result] if result else []}


USAGE = [
    _bucket(
        "2026-11-03T21:00:00Z",
        uncached_input_tokens=40,
        output_tokens=900,
        cache_creation={"ephemeral_5m_input_tokens": 3000, "ephemeral_1h_input_tokens": 100},
        cache_read_input_tokens=20000,
        server_tool_use={"web_search_requests": 3},
    ),
    _bucket("2026-11-03T21:01:00Z"),
    _bucket(
        "2026-11-03T21:02:00Z",
        uncached_input_tokens=6,
        output_tokens=100,
        cache_creation={},
        cache_read_input_tokens=1000,
        server_tool_use={"web_search_requests": 1},
    ),
]


class Fetch:
    def __init__(self, buckets=None, error=None):
        self.buckets = buckets if buckets is not None else USAGE
        self.error = error
        self.calls: list[tuple[str, list]] = []

    def __call__(self, path, params):
        self.calls.append((path, params))
        if self.error:
            raise self.error
        return self.buckets


@pytest.fixture
def settings(monkeypatch, client, session_factory):
    """Billing settings with a key ID and workspace, and the first test user as an admin."""
    configured = get_settings().model_copy(
        update={
            "billing_api_key_id": "apikey_callback",
            "billing_workspace_id": "wrkspc_callback",
        }
    )
    monkeypatch.setattr(billing_worker, "get_settings", lambda: configured)
    with session_factory() as db:
        db.scalar(select(User).where(User.email == "owner@example.com")).role = Role.ADMIN
        db.commit()
    from app.routers import spend as spend_router

    monkeypatch.setattr(spend_router, "get_settings", lambda: configured)
    return configured


def _user(session_factory, email="owner@example.com"):
    with session_factory() as db:
        return db.scalar(select(User).where(User.email == email)).id


def _run(session_factory, user_id, started, finished, **changes):
    values = {
        "user_id": user_id,
        "status": "done",
        "settings": {},
        "cost_usd": 0.07,
        "usage": {"input_tokens": 46, "output_tokens": 1000, "web_search_requests": 4},
        "leads": [{"id": "a"}, {"id": "b"}],
        "request_costs": [{"request": 1, "output_tokens": 1000, "cost_usd": 0.07}],
        "started_at": started,
        "finished_at": finished,
        "created_at": started,
        **changes,
    }
    with session_factory() as db:
        run = ScoutRun(**values)
        db.add(run)
        db.commit()
        return run.id


def test_sum_usage_adds_both_cache_lifetimes():
    assert sum_usage(USAGE) == {
        "input_tokens": 46,
        "output_tokens": 1000,
        "cache_creation_input_tokens": 3100,
        "cache_read_input_tokens": 21000,
        "web_search_requests": 4,
    }


def test_reconcile_stores_billed_usage_for_the_runs_minutes(client, session_factory, settings):
    user_id = _user(session_factory)
    start = NOW - timedelta(hours=1)
    run_id = _run(session_factory, user_id, start, start + timedelta(minutes=2, seconds=13))
    # Ended a moment ago, so its usage may not be reported yet.
    recent = _run(session_factory, user_id, NOW - timedelta(minutes=3), NOW - timedelta(minutes=1))
    posting = client.post("/api/postings", json={"company": "Acme", "title": "Engineer"}).json()
    with session_factory() as db:
        db.add(
            TailorJob(
                user_id=user_id,
                posting_id=uuid.UUID(posting["id"]),
                status="done",
                started_at=start + timedelta(minutes=1),
                finished_at=start + timedelta(minutes=2),
            )
        )
        db.flush()
        db.commit()
    fetch = Fetch()
    with session_factory() as db:
        assert reconcile(db, fetch, NOW) == 2
    path, params = fetch.calls[0]
    assert path == "/v1/organizations/usage_report/messages"
    assert ("bucket_width", "1m") in params
    assert ("api_key_ids[]", "apikey_callback") in params
    assert ("workspace_ids[]", "wrkspc_callback") in params
    assert ("starting_at", "2026-11-03T21:00:00Z") in params
    assert ("ending_at", "2026-11-03T21:04:00Z") in params
    with session_factory() as db:
        billed = db.get(ScoutRun, run_id).billed
        assert billed["output_tokens"] == 1000
        assert billed["shared_with"] == 1
        assert db.get(ScoutRun, recent).billed is None
        # Checked runs aren't fetched again.
        assert reconcile(db, Fetch(), NOW) == 0


def test_sync_costs_stores_dollars_and_replaces_the_days(client, session_factory, settings):
    day = {
        "starting_at": "2026-11-03T00:00:00Z",
        "ending_at": "2026-11-04T00:00:00Z",
        "results": [
            {
                "amount": "84.05",
                "currency": "USD",
                "cost_type": "tokens",
                "description": "Claude Sonnet 5.5 Usage - Cache Write",
                "model": "claude-sonnet-5-5",
                "token_type": "cache_creation.ephemeral_5m_input_tokens",
                "workspace_id": "wrkspc_callback",
            },
            {
                "amount": "12",
                "currency": "USD",
                "cost_type": "web_search",
                "description": "Web Search",
                "workspace_id": "wrkspc_other",
            },
        ],
    }
    fetch = Fetch([day])
    with session_factory() as db:
        assert sync_costs(db, fetch, NOW.date()) == 2
        assert sync_costs(db, fetch, NOW.date()) == 2
        amounts = sorted(cost.amount_usd for cost in db.scalars(select(BillingCost)).all())
    assert amounts == [0.12, 0.8405]
    params = fetch.calls[0][1]
    assert ("group_by[]", "workspace_id") in params
    assert ("group_by[]", "description") in params


def test_a_failed_sync_is_kept_for_the_page(client, session_factory, settings):
    fetch = Fetch(error=BillingUnavailable("Anthropic's billing reports returned an error (401)."))
    with session_factory() as db:
        run_once(db, fetch, NOW, sync_cost=True)
        assert "401" in db.get(BillingState, "last_error").value


def test_http_fetcher_follows_pages_and_reports_errors():
    pages = {
        None: {"data": [{"n": 1}], "has_more": True, "next_page": "p2"},
        "p2": {"data": [{"n": 2}], "has_more": False, "next_page": None},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=pages[request.url.params.get("page")])

    client = httpx.Client(base_url="https://api.test", transport=httpx.MockTransport(handler))
    assert http_fetcher(client)("/report", [("bucket_width", "1d")]) == [{"n": 1}, {"n": 2}]

    denied = httpx.Client(
        base_url="https://api.test",
        transport=httpx.MockTransport(lambda request: httpx.Response(401, json={})),
    )
    with pytest.raises(BillingUnavailable, match="tied to a workspace"):
        http_fetcher(denied)("/report", [])


def test_spend_page_is_admin_only(client, other_client, settings):
    assert client.get("/api/auth/me").json()["is_admin"] is True
    assert other_client.get("/api/auth/me").json()["is_admin"] is False
    assert other_client.get("/api/admin/spend").status_code == 404
    assert client.get("/api/admin/spend").status_code == 200


def test_spend_page_sums_costs_balance_and_budgets(client, session_factory, settings):
    user_id = _user(session_factory)
    start = NOW - timedelta(days=1)
    _run(session_factory, user_id, start, start + timedelta(minutes=2), cost_usd=1.0)
    _run(session_factory, user_id, start, start + timedelta(minutes=3), cost_usd=2.0)
    with session_factory() as db:
        db.add(BillingCost(day=date(2026, 11, 1), description="x", amount_usd=0.5))
        db.add(
            BillingCost(
                day=date(2026, 11, 2), workspace_id="wrkspc_callback", description="y", amount_usd=3
            )
        )
        db.commit()
    added = client.post(
        "/api/admin/spend/topups",
        json={"amount_usd": 10, "added_on": "2026-11-02", "note": "First"},
    )
    assert added.status_code == 201

    body = client.get("/api/admin/spend").json()
    # Billed since the first top-up counts only the 2nd's $3.
    assert body["balance"]["balance_usd"] == 7.0
    quote = body["quote"]
    assert quote["median_run_usd"] == 1.5
    assert quote["median_per_lead_usd"] == 0.75
    assert quote["budgets"][0] == {"budget_usd": 5.0, "runs": 3, "leads": 6, "tailorings": 0}
    [user] = body["users"]
    # Both runs found the same two jobs, so there are two leads, not four.
    assert (user["runs"], user["leads"], user["measured_usd"]) == (2, 2, 3.0)
    assert user["per_lead_usd"] == 1.5
    assert body["jobs"][0]["requests"][0]["output_tokens"] == 1000
    assert body["setup"]["api_key_filter"] is True

    assert client.delete(f"/api/admin/spend/topups/{added.json()['id']}").status_code == 204
    assert client.get("/api/admin/spend").json()["balance"]["topups_usd"] == 0


def _heavy(session_factory, client, user_id):
    """A finished run with a long step log and a tailoring with a PDF, as real ones have."""
    _run(
        session_factory,
        user_id,
        NOW - timedelta(hours=2),
        NOW - timedelta(hours=1),
        steps=[{"kind": "note", "detail": "x" * 300, "request": 1}] * 50,
    )
    posting = client.post("/api/postings", json={"company": "Acme", "title": "Engineer"}).json()
    with session_factory() as db:
        db.add(
            TailorJob(
                user_id=user_id,
                posting_id=uuid.UUID(posting["id"]),
                status="done",
                result_pdf=b"%PDF-1.5 " + b"x" * 50_000,
                cost_usd=0.05,
                started_at=NOW - timedelta(hours=2),
                finished_at=NOW - timedelta(hours=1),
            )
        )
        db.commit()


def _loaded(statements, *columns):
    return [column for column in columns if any(column in sql for sql in statements)]


def test_billing_reads_only_when_and_whether_each_job_was_billed(
    client, session_factory, settings, queries
):
    _heavy(session_factory, client, _user(session_factory))
    queries.clear()
    with session_factory() as db:
        assert reconcile(db, Fetch(), NOW) == 2
    assert (
        _loaded(
            queries,
            "tailor_jobs.result_pdf",
            "smartsearch_runs.steps",
            "smartsearch_runs.leads",
            "smartsearch_runs.request_costs",
            "smartsearch_runs.settings",
        )
        == []
    )


def test_the_spend_page_loads_no_pdfs_or_step_logs(client, session_factory, settings, queries):
    _heavy(session_factory, client, _user(session_factory))
    queries.clear()
    body = client.get("/api/admin/spend").json()
    assert body["quote"]["median_tailoring_usd"] == 0.05
    assert (
        _loaded(
            queries, "tailor_jobs.result_pdf", "smartsearch_runs.steps", "smartsearch_runs.settings"
        )
        == []
    )
