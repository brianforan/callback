"""Pricing past Scout runs again after a pricing fix."""

from sqlalchemy import select

from app.billing.reprice import reprice_runs
from app.models import ScoutRun, User


def _run(session_factory, status="done"):
    with session_factory() as db:
        user = db.scalar(select(User).where(User.email == "owner@example.com"))
        run = ScoutRun(
            user_id=user.id,
            status=status,
            cost_usd=0.5,
            settings={"engine": "pipeline"},
            usage={"web_search_requests": 3},
            leads=[],
            steps=[
                {"kind": "search", "detail": "laravel", "request": 1},
                {"kind": "error", "detail": "Search failed: max_uses_exceeded", "request": 1},
            ],
            request_costs=[
                {
                    "request": 1,
                    "stage": "find",
                    "model": "claude-haiku-5-5",
                    "cache_read_input_tokens": 200_000,
                    "web_search_requests": 3,
                    "cost_usd": 0.45,
                },
                {"request": 2, "stage": "judge", "model": "claude-haiku-5-5", "cost_usd": 0.05},
            ],
        )
        db.add(run)
        db.commit()
        return run.id


def _price(requests):
    # Tailor, faked: a cent a search and nothing for tokens.
    return [
        {
            "cost_usd": item["web_search_requests"] * 0.01,
            "cost_breakdown": {"web_search": item["web_search_requests"] * 0.01},
        }
        for item in requests
    ]


def test_a_dry_run_changes_nothing(client, session_factory):
    run_id = _run(session_factory)
    seen = []
    with session_factory() as db:
        [change] = reprice_runs(
            db, lambda requests: seen.extend(requests) or _price(requests), False
        )
        assert (change.old_cost, change.new_cost) == (0.5, 0.02)
        # The failed search comes off the request it belonged to, and agent-less models default.
        assert [item["web_search_requests"] for item in seen] == [2, 0]
        assert seen[0]["model"] == "claude-haiku-5-5"
    with session_factory() as db:
        assert db.get(ScoutRun, run_id).cost_usd == 0.5


def test_applying_saves_the_new_cost(client, session_factory):
    run_id = _run(session_factory)
    failed_id = _run(session_factory, status="failed")
    with session_factory() as db:
        reprice_runs(db, _price, True)
    with session_factory() as db:
        run = db.get(ScoutRun, run_id)
        assert run.cost_usd == 0.02
        assert run.request_costs[0]["cost_usd"] == 0.02
        assert run.request_costs[0]["web_search_requests"] == 2
        assert run.usage["web_search_requests"] == 2
        assert run.cost_breakdown == {"web_search": 0.02}
        # A failed run's measured cost is priced again too.
        assert db.get(ScoutRun, failed_id).cost_usd == 0.02
