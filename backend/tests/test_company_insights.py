"""Insights -> Companies, the company exports and research costs on the Spend page: a user sees
only companies they applied to, the full exports are the admin's, and exports hold company
facts only."""

import csv
import io
import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from test_company_context import LINK, _research

from app.models import Company, CompanyResearch, RejectAnalysis, Role, TailorStatus, User


def _apply(client, company="Acme", url=LINK):
    response = client.post(
        "/api/applications",
        json={"company": company, "role_title": "Backend Engineer", "job_url": url},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _acme(session_factory):
    with session_factory() as db:
        return db.scalar(select(Company).where(Company.name == "Acme")).id


def _older_snapshot(session_factory):
    """A snapshot from 40 days ago, with a headcount since changed and the same layoff."""
    with session_factory() as db:
        newest = db.scalar(select(CompanyResearch))
        db.add(
            CompanyResearch(
                company_id=newest.company_id,
                requested_by=db.scalar(select(User.id).where(User.email == "owner@example.com")),
                status=TailorStatus.DONE.value,
                sources=newest.sources,
                profile=newest.profile,
                ownership={
                    "kind": "private_vc",
                    "ticker": "",
                    "exchange": "",
                    "parent": "",
                    "source": 0,
                },
                funding={},
                size={"headcount": 450, "as_of": "2026-03", "trend": "growing", "source": 0},
                events=newest.events[:1],
                engineering={},
                workforce={},
                interviewing={},
                confidence={},
                dropped={},
                usage={"input_tokens": 100},
                cost_usd=0.05,
                created_at=datetime.now(UTC) - timedelta(days=40),
                finished_at=datetime.now(UTC) - timedelta(days=40),
            )
        )
        db.commit()


def _make_admin(session_factory, email="owner@example.com"):
    with session_factory() as db:
        db.scalar(select(User).where(User.email == email)).role = Role.ADMIN
        db.commit()


# --- Who sees which companies -------------------------------------------------------------------


def test_the_list_holds_only_researched_companies_the_user_applied_to(
    client, other_client, session_factory
):
    _research(session_factory)
    _apply(client)
    _apply(client, company="Globex", url="https://example.com/jobs/1")

    [card] = client.get("/api/companies/mine").json()
    assert card["name"] == "Acme"
    assert (card["headcount"], card["trend"], card["snapshots"]) == (420, "shrinking", 1)
    assert card["can_see_hiring"] is True
    assert card["open_roles"] == 1
    assert card["recent_events"][0] == "2026-09: Layoff: Cut 10% of staff, 10%"
    # Someone who never applied there doesn't see it, even by id.
    assert other_client.get("/api/companies/mine").json() == []
    assert other_client.get(f"/api/companies/{card['id']}").status_code == 404
    assert other_client.get(f"/api/companies/{card['id']}/hiring-history.csv").status_code == 404


def test_a_company_page_lists_its_snapshots_and_hiring_history(client, session_factory):
    _research(session_factory)
    _older_snapshot(session_factory)
    _apply(client)
    acme = _acme(session_factory)

    detail = client.get(f"/api/companies/{acme}").json()

    assert detail["snapshots"] == 2
    # Newest first, so a change in headcount shows.
    assert [item["size"]["headcount"] for item in detail["history"]] == [420, 450]
    assert detail["weeks"][-1]["open"] == 1
    assert detail["weeks"][-1]["open_engineering"] == 1
    response = client.get(f"/api/companies/{acme}/hiring-history.csv")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    rows = list(csv.reader(io.StringIO(response.text)))
    assert rows[0] == ["week_start", "open", "open_engineering", "opened", "closed"]
    assert rows[-1][1:3] == ["1", "1"]


def test_an_unresearched_company_has_no_page(client, session_factory):
    _research(session_factory)
    _apply(client)
    with session_factory() as db:
        db.query(CompanyResearch).delete()
        db.commit()
    assert client.get("/api/companies/mine").json() == []
    assert client.get(f"/api/companies/{_acme(session_factory)}").status_code == 404


# --- Exports ------------------------------------------------------------------------------------


def test_exports_are_the_admins_and_hold_company_facts_only(client, other_client, session_factory):
    _research(session_factory)
    _older_snapshot(session_factory)
    assert client.get("/api/company-research/export.jsonl").status_code == 404
    _make_admin(session_factory)

    response = client.get("/api/company-research/export.jsonl")

    assert response.status_code == 200
    lines = [json.loads(line) for line in response.text.splitlines()]
    assert len(lines) == 2
    first = lines[0]
    assert first["company"] == "Acme"
    assert first["sources"][0]["url"] == "https://acme.com/about"
    assert first["size"]["headcount"] == 450
    # Nothing about who asked, or what it cost.
    for line in lines:
        assert not {"requested_by", "cost_usd", "usage", "note"} & set(line)
    assert other_client.get("/api/company-research/events.csv").status_code == 404


def test_the_events_export_has_one_row_per_event(client, session_factory):
    _research(session_factory)
    _older_snapshot(session_factory)
    _make_admin(session_factory)

    rows = list(csv.DictReader(io.StringIO(client.get("/api/company-research/events.csv").text)))

    # The layoff is in both snapshots but is one event.
    assert [(row["date"], row["kind"]) for row in rows] == [
        ("2026-02", "hiring_freeze"),
        ("2026-09", "layoff"),
        ("2026-10", "office_opened"),
    ]
    layoff = rows[1]
    assert (layoff["company"], layoff["percent"], layoff["source_url"]) == (
        "Acme",
        "10",
        "https://acme.com/about",
    )


# --- Spend --------------------------------------------------------------------------------------


def test_spend_counts_rejection_reports_and_research(client, session_factory):
    _research(session_factory)
    _older_snapshot(session_factory)
    application = client.post(
        "/api/applications",
        json={
            "company": "Acme",
            "role_title": "Backend Engineer",
            "job_url": LINK,
            "status": "rejected",
        },
    ).json()
    with session_factory() as db:
        analysis = db.scalar(select(RejectAnalysis))
        analysis.status = TailorStatus.DONE.value
        analysis.cost_usd = 0.002
        analysis.usage = {"input_tokens": 900, "output_tokens": 300}
        analysis.finished_at = datetime.now(UTC)
        db.commit()
    _make_admin(session_factory)

    spend = client.get("/api/admin/spend").json()

    kinds = {job["kind"] for job in spend["jobs"]}
    assert {"rejection_analysis", "company_research"} <= kinds
    report = next(job for job in spend["jobs"] if job["kind"] == "rejection_analysis")
    assert (report["measured_usd"], report["billed"]) == (0.002, None)
    [owner] = [row for row in spend["users"] if row["email"] == "owner@example.com"]
    assert (owner["analyses"], owner["research"]) == (1, 1)
    # The older snapshot was the owner's; the newest has no requester.
    assert owner["measured_usd"] == 0.052
    # By day: the newest snapshot and the report; the older snapshot is outside the window.
    assert sum(day["measured_usd"] for day in spend["days"]) == pytest.approx(0.042)
    assert application["status"] == "rejected"
