"""Scout's engine: company feeds, the catalog worker, seeding, limits and runs.

The engine (app.scout.engine) is the only one now, and reads the whole catalog, not just a
profile's list (docs/scout_spec.md)."""

import importlib.util
import json
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from app.companies.catalog import CatalogListing, record_listing
from app.companies.catalog_worker import due_sources, read_due, request_reads, wait_for_reads
from app.companies.feeds import FeedGone, FeedJob, feed_url, read_feed
from app.companies.lists import add_to_profile, note_dismissed, seed_catalog
from app.models import (
    BoardCache,
    CatalogJob,
    Company,
    CompanySource,
    HiddenListing,
    JobJudgment,
    JobPosting,
    PostingLevel,
    ProfileCompany,
    ProfileCompanyOrigin,
    ScoutRun,
    ScoutSettings,
    User,
    utcnow,
)
from app.profiles import default_profile
from app.scout.engine import CompanyStages, run_companies, states_named
from app.scout.limits import (
    OLD_TITLE_PHRASES,
    default_limits,
    fit_limits,
    limit_errors,
)
from app.scout.worker import claim_next, run_one
from app.workers import TailorUnavailable

TODAY = date(2026, 11, 5)
_MIGRATION_PATH = Path(__file__).parents[1] / "alembic/versions/0045_scout_title_phrases.py"
_MIGRATION_SPEC = importlib.util.spec_from_file_location("scout_title_phrases", _MIGRATION_PATH)
MIGRATION = importlib.util.module_from_spec(_MIGRATION_SPEC)
_MIGRATION_SPEC.loader.exec_module(MIGRATION)
LARAVEL = (
    "We build our internal tools in Laravel and Vue. You'll own the back office: billing, "
    "support tooling and the integrations our operations team runs on. "
) * 8
OTHER = "We need a Kotlin engineer for our Android app and its release pipeline. " * 12


def _owner(db):
    user = db.scalar(select(User).where(User.email == "owner@example.com"))
    profile = default_profile(db, user)
    db.flush()
    return user, profile


def _job(db, user, profile, token, title, *, text=LARAVEL, read=True, **changes):
    """A company with its own Greenhouse board on the profile's list, and one open job there."""
    job_id = abs(hash((token, title))) % 10**9
    job = record_listing(
        db,
        CatalogListing(
            source="greenhouse",
            url=f"https://job-boards.greenhouse.io/{token}/jobs/{job_id}",
            company=token.capitalize(),
            title=title,
            text=text,
            **{"location": "Remote, US", **changes},
        ),
    )
    if read:
        for source in db.scalars(select(CompanySource).where(CompanySource.identifier == token)):
            source.last_checked = utcnow()
    add_to_profile(db, user.id, profile.id, job.company_id, ProfileCompanyOrigin.LEAD)
    db.flush()
    return job


def _payload(**changes):
    payload = {
        "description": "Laravel stacks, emphasis on internal tooling.",
        "title_families": [],
        "work_modes": [],
        "location": "east coast timezone (anywhere)",
        "posted_within_days": None,
        "fit_mode": "balanced",
        "agency_mode": "include",
        "limits": {
            "target_leads": 20,
            "dry_streak": 4,
            "max_searches": 5,
            "max_pages": 60,
            "max_minutes": 10,
        },
        "budget_usd": 2.0,
        "known_jobs": [],
        "hidden_companies": [],
        "passed_pages": [],
        "declined": [],
    }
    payload.update(changes)
    return payload


class Judge:
    """The Haiku judge, faked: it keeps postings that mention Laravel."""

    def __init__(self):
        self.calls = []

    def __call__(self, payload):
        self.calls.append(payload["posting"])
        keep = "Laravel" in payload["posting"]["text"]
        return {
            "model": "claude-haiku-5-5",
            "cost_usd": 0.002,
            "usage": {"output_tokens": 50},
            "request_costs": [{"request": 1, "cost_usd": 0.002}],
            "judgment": {
                "keep": keep,
                "fit": "good" if keep else "none",
                "work_match": "Laravel internal tools." if keep else "Android work.",
                "location_ok": True,
                "agency": "",
                "reason": "Matches." if keep else "Not the work.",
            },
        }


def _stages(judge=None, same=None):
    return CompanyStages(
        judge=judge or Judge(),
        same_company=same or (lambda payload: {"cost_usd": 0.0, "answers": []}),
    )


# Feeds


def _transport(routes):
    def handle(request):
        status, body = routes.get(str(request.url), (404, {}))
        return httpx.Response(status, content=json.dumps(body).encode())

    return httpx.MockTransport(handle)


def test_feeds_read_every_open_job_from_each_hiring_system():
    routes = {
        feed_url("greenhouse", "acme"): (
            200,
            {
                "jobs": [
                    {
                        "title": "Laravel Engineer",
                        "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/1",
                        "company_name": "Acme Inc",
                        "location": {"name": "Remote, US"},
                        "first_published": "2026-11-01T10:00:00Z",
                        "content": "&lt;p&gt;Build &lt;b&gt;tools&lt;/b&gt;.&lt;/p&gt;",
                    }
                ]
            },
        ),
        feed_url("lever", "globex"): (
            200,
            [
                {
                    "text": "Platform Engineer",
                    "hostedUrl": "https://jobs.lever.co/globex/abc",
                    "categories": {"location": "New York, NY"},
                    "workplaceType": "hybrid",
                    "descriptionPlain": "Run the platform.",
                    "lists": [{"text": "You have", "content": "<li>Go</li>"}],
                    "createdAt": 1762000000000,
                }
            ],
        ),
        feed_url("ashby", "initech"): (
            200,
            {
                "jobs": [
                    {
                        "title": "Data Engineer",
                        "jobUrl": "https://jobs.ashbyhq.com/initech/1",
                        "location": "Remote",
                        "workplaceType": "Remote",
                        "descriptionPlain": "Pipelines.",
                        "publishedAt": "2026-10-30T00:00:00Z",
                    },
                    {
                        "title": "Hidden",
                        "jobUrl": "https://jobs.ashbyhq.com/initech/2",
                        "isListed": False,
                    },
                ]
            },
        ),
    }
    with httpx.Client(transport=_transport(routes)) as client:
        [gh] = read_feed(client, "greenhouse", "acme")
        assert (gh.company, gh.location, gh.posted_on) == (
            "Acme Inc",
            "Remote, US",
            date(2026, 11, 1),
        )
        assert "Build tools." in gh.text
        [lever] = read_feed(client, "lever", "globex")
        assert lever.work_mode == "hybrid" and "Go" in lever.text and lever.posted_on
        [ashby] = read_feed(client, "ashby", "initech")
        assert ashby.work_mode == "remote" and ashby.posted_on == date(2026, 10, 30)
        with pytest.raises(FeedGone):
            read_feed(client, "greenhouse", "gone")
    for system, token in (("greenhouse", "a/b"), ("greenhouse", "a.b"), ("workday", "acme")):
        with pytest.raises(ValueError):
            feed_url(system, token)


# The catalog worker


def _feed(*jobs, company=""):
    return [
        FeedJob(
            url=url,
            title=title,
            company=company,
            location="Remote, US",
            work_mode="remote",
            posted_on=TODAY,
            text=LARAVEL,
        )
        for url, title in jobs
    ]


def test_the_worker_reads_listed_boards_and_closes_jobs_that_go(client, session_factory):
    first = ("https://job-boards.greenhouse.io/acme/jobs/1", "Laravel Engineer")
    second = ("https://job-boards.greenhouse.io/acme/jobs/2", "Support Engineer")
    with session_factory() as db:
        user, profile = _owner(db)
        _job(db, user, profile, "acme", "Laravel Engineer", read=False)
        # A company on no one's list is read too, after the listed ones, and daily after that.
        record_listing(
            db,
            CatalogListing(
                source="greenhouse",
                url="https://job-boards.greenhouse.io/lonely/jobs/9",
                company="Lonely",
                title="Engineer",
            ),
        )
        db.commit()
        boards = {"acme": _feed(first, second), "lonely": _feed()}
        reads = []

        def read(system, token):
            reads.append(token)
            return boards[token]

        assert [s.identifier for s in due_sources(db, utcnow())] == ["acme", "lonely"]
        assert read_due(db, read) == 2 and sorted(reads) == ["acme", "lonely"]
        source = db.scalar(select(CompanySource).where(CompanySource.identifier == "acme"))
        assert (source.status, source.open_jobs, source.failures) == ("ok", 2, 0)
        assert source.last_checked is not None
        # Read 6 hours ago at most, so not due again yet.
        assert read_due(db, read) == 0

        boards["acme"] = _feed(second)
        source.last_checked = utcnow() - timedelta(hours=7)
        db.commit()
        # Seven hours on, only the listed board is due; the other waits a day.
        assert [s.identifier for s in due_sources(db, utcnow())] == ["acme"]
        read_due(db, read)
        titles = {job.title: job.closed_at for job in db.scalars(select(CatalogJob))}
        assert titles["Laravel Engineer"] is not None and titles["Support Engineer"] is None


def test_failing_boards_stop_being_read_until_their_link_turns_up_again(client, session_factory):
    with session_factory() as db:
        user, profile = _owner(db)
        _job(db, user, profile, "acme", "Laravel Engineer", read=False)
        _job(db, user, profile, "gone", "Engineer", read=False)
        db.commit()

        def read(system, token):
            if token == "gone":
                raise FeedGone("answered 404")
            raise httpx.ConnectError("refused")

        for _ in range(3):
            for source in db.scalars(select(CompanySource)):
                source.last_checked = None
            db.commit()
            read_due(db, read)
        sources = {s.identifier: s for s in db.scalars(select(CompanySource))}
        assert sources["gone"].status == "dead"
        assert sources["acme"].failures == 3
        sources["acme"].last_checked = None
        db.commit()
        assert due_sources(db, utcnow()) == []

        # A new link to the board makes it worth reading again.
        record_listing(
            db,
            CatalogListing(
                source="linkedin",
                url="https://www.linkedin.com/jobs/view/5",
                company="Acme",
                title="Ops Engineer",
                direct_url="https://job-boards.greenhouse.io/acme/jobs/77",
            ),
        )
        db.commit()
        assert [s.identifier for s in due_sources(db, utcnow())] == ["acme"]


def test_one_board_is_read_once_for_two_peoples_lists(client, other_client, session_factory):
    with session_factory() as db:
        user, profile = _owner(db)
        job = _job(db, user, profile, "acme", "Laravel Engineer", read=False)
        other = db.scalar(select(User).where(User.email != user.email))
        other_profile = default_profile(db, other)
        db.flush()
        add_to_profile(db, other.id, other_profile.id, job.company_id, ProfileCompanyOrigin.LEAD)
        db.commit()
        reads = []
        read_due(db, lambda system, token: reads.append(token) or _feed())
        assert reads == ["acme"]


def test_a_run_waits_for_unread_boards_then_goes_on(client, session_factory):
    with session_factory() as db:
        user, profile = _owner(db)
        job = _job(db, user, profile, "acme", "Laravel Engineer", read=False)
        db.commit()
        waiting = request_reads(db, [job.company_id], utcnow())
        assert len(waiting) == 1
        source = db.get(CompanySource, waiting[0])
        assert source.wanted_by is not None

        now = [0.0]
        assert (
            wait_for_reads(
                db,
                waiting,
                60,
                clock=lambda: now[0],
                sleep=lambda s: now.__setitem__(0, now[0] + s),
            )
            == 1
        )
        assert now[0] >= 60

        source.last_checked = utcnow()
        db.commit()
        assert wait_for_reads(db, waiting, 60, clock=lambda: 0.0, sleep=lambda s: None) == 0


# Seeding


def test_seeding_fills_the_catalog_and_each_profiles_list(client, session_factory):
    with session_factory() as db:
        user, profile = _owner(db)
        db.add(
            JobPosting(
                user_id=user.id,
                profile_id=profile.id,
                company="what I typed",
                company_key="what i typed",
                title="Engineer",
                url="https://boards.greenhouse.io/acme-labs/jobs/42",
                seen_on=TODAY,
            )
        )
        db.add(
            ScoutRun(
                user_id=user.id,
                profile_id=profile.id,
                status="done",
                settings={},
                leads=[
                    {
                        "url": "https://jobs.lever.co/globex/abc",
                        "company": "Globex",
                        "title": "Laravel Developer",
                        "posting_text": LARAVEL,
                    }
                ],
                steps=[{"kind": "fetch", "detail": "https://jobs.ashbyhq.com/initech/1"}],
            )
        )
        db.add(
            BoardCache(
                key="k" * 64,
                board="indeed",
                listings=[
                    {
                        "company": "Hooli",
                        "title": "PHP Engineer",
                        "url": "https://www.indeed.com/viewjob?jk=1",
                        "description": "PHP.",
                        "direct_url": "https://jobs.lever.co/hooli/1",
                    },
                    {"company": "Nope", "title": "X", "url": "https://x.io/1", "description": ""},
                ],
                fetched_at=utcnow(),
                fresh_until=utcnow(),
            )
        )
        db.commit()
        summary = seed_catalog(db)
        assert (summary.postings, summary.leads, summary.candidates, summary.board_listings) == (
            1,
            1,
            1,
            1,
        )
        listed = {
            db.get(Company, row.company_id).name: row.origin
            for row in db.scalars(select(ProfileCompany))
        }
        # The posting's company is named from its board's token, not from what the user typed.
        assert listed == {"Acme Labs": "postings", "Globex": "lead", "Initech": "discovered"}
        own = {
            (s.source, s.identifier)
            for s in db.scalars(select(CompanySource).where(CompanySource.is_own))
        }
        assert own == {
            ("greenhouse", "acme-labs"),
            ("lever", "globex"),
            ("ashby", "initech"),
            ("lever", "hooli"),
        }
        # Seeding again adds nothing new.
        seed_catalog(db)
        assert len(db.scalars(select(ProfileCompany)).all()) == 3

        # A listing with the company's real name replaces the one made from its token.
        record_listing(
            db,
            CatalogListing(
                source="greenhouse",
                url="https://boards.greenhouse.io/acme-labs/jobs/43",
                company="ACME Laboratories",
                title="Engineer II",
            ),
        )
        assert db.scalar(
            select(Company.name).where(Company.domain.is_(None), Company.key == "acme laboratories")
        )


# Per-engine limits


def test_limits_are_the_one_engines_and_saved_as_set(client, session_factory):
    limits = {
        "target_leads": 60,
        "dry_streak": 4,
        "max_searches": 10,
        "max_pages": 150,
        "max_minutes": 10,
    }
    assert limit_errors(limits) == []
    assert fit_limits({**limits, "max_pages": 900})["max_pages"] == 300

    from test_scout import _make_premium
    from test_scout import _payload as settings_payload

    _make_premium(session_factory)
    saved = client.put("/api/scout/settings", json=settings_payload(limits=limits))
    assert saved.status_code == 200, saved.text
    assert saved.json()["limits"] == limits
    config = client.get("/api/scout").json()
    pages = next(limit for limit in config["limits"] if limit["key"] == "max_pages")
    assert pages["by_engine"]["companies"]["label"] == "Most postings to check"
    assert set(pages["by_engine"]) == {"companies"}
    assert "credit_rates" not in config


# Runs


def test_a_companies_run_finds_leads_in_stored_jobs(client, session_factory):
    with session_factory() as db:
        user, profile = _owner(db)
        _job(db, user, profile, "acme", "Senior Laravel Engineer")
        _job(db, user, profile, "acme", "Kotlin Engineer", text=OTHER)
        _job(db, user, profile, "globex", "Backend Engineer")
        closed = _job(db, user, profile, "initech", "Laravel Developer")
        closed.closed_at = utcnow()
        _job(db, user, profile, "hooli", "Laravel Lead", location="Berlin, Germany")
        _job(db, user, profile, "umbrella", "Laravel Engineer")
        db.add(
            HiddenListing(
                user_id=user.id,
                kind="company",
                key="umbrella",
                company="Umbrella",
                company_key="umbrella",
                title="",
                url=None,
            )
        )
        db.commit()
        judge = Judge()
        result = run_companies(
            db,
            user.id,
            profile.id,
            _payload(hidden_companies=["Umbrella"]),
            _stages(judge),
            TODAY,
        )
        judged = [page["title"] for page in judge.calls]
        # Laravel in the title first, then Laravel in the text only. The Kotlin job never
        # names Laravel, so the code gate leaves it out before anything is paid for.
        assert judged == ["Senior Laravel Engineer", "Backend Engineer"]
        assert [lead.title for lead in result.leads] == judged
        lead = result.leads[0]
        assert lead.url.startswith("https://job-boards.greenhouse.io/acme/jobs/")
        assert lead.fit_summary == "Laravel internal tools." and lead.posting_text
        notes = " ".join(step.detail for step in result.steps)
        assert "Matching: must use Laravel; levels: any level except intern and manager." in notes
        assert "4 companies on your list (1 hidden left out)" in notes
        assert "1 only outside the US" in notes
        assert "1 at companies you've hidden or turned down" in notes
        assert (result.stop_code, result.searches, result.pages) == ("finished", 0, 2)
        assert result.cost_usd == pytest.approx(0.004)
        assert result.model == "companies"


def test_a_companies_run_stops_at_its_limits(client, session_factory):
    with session_factory() as db:
        user, profile = _owner(db)
        # Enough that the second run, which skips what the first one judged, still has more.
        for index in range(8):
            _job(db, user, profile, f"co{index}", f"Laravel Engineer {index}")
        db.commit()
        limits = {**_payload()["limits"], "target_leads": 2}
        result = run_companies(db, user.id, profile.id, _payload(limits=limits), _stages(), TODAY)
        assert (result.stop_code, len(result.leads)) == ("target_reached", 2)
        limits = {**limits, "target_leads": 20, "max_pages": 3}
        result = run_companies(db, user.id, profile.id, _payload(limits=limits), _stages(), TODAY)
        assert (result.stop_code, result.pages) == ("max_pages", 3)
        result = run_companies(
            db, user.id, profile.id, _payload(budget_usd=0.005), _stages(), TODAY
        )
        assert result.stop_code == "budget"


def test_code_rules_out_known_old_far_and_wrong_mode_jobs(client, session_factory):
    with session_factory() as db:
        user, profile = _owner(db)
        known = _job(db, user, profile, "acme", "Laravel Engineer")
        _job(db, user, profile, "globex", "Laravel Developer", posted_on=TODAY - timedelta(days=40))
        _job(
            db, user, profile, "initech", "Laravel Lead", location="Austin, TX", work_mode="onsite"
        )
        _job(
            db,
            user,
            profile,
            "hooli",
            "Laravel Architect",
            location="Camden, NJ",
            work_mode="onsite",
        )
        _job(
            db, user, profile, "umbrella", "Laravel Dev", work_mode="hybrid", location="Remote, US"
        )
        db.commit()
        judge = Judge()
        payload = _payload(
            location="Philadelphia, PA",
            posted_within_days=14,
            work_modes=["onsite"],
            known_jobs=[{"company": "x", "title": "y", "url": known.sources[0].url}],
        )
        result = run_companies(db, user.id, profile.id, payload, _stages(judge), TODAY)
        assert [page["title"] for page in judge.calls] == ["Laravel Architect"]
        notes = " ".join(step.detail for step in result.steps)
        for part in (
            "1 already known",
            "1 posted too long ago",
            "1 onsite or hybrid too far away",
            "1 in a work mode not wanted",
        ):
            assert part in notes, part
    assert states_named("Philadelphia, PA") == {"PA"} and states_named("New York or Texas") == {
        "NY",
        "TX",
    }


def test_companies_whose_leads_were_all_turned_down_are_skipped(client, session_factory):
    with session_factory() as db:
        user, profile = _owner(db)
        job = _job(db, user, profile, "acme", "Laravel Engineer")
        listed = db.get(ProfileCompany, (profile.id, job.company_id))
        listed.leads = 3
        for _ in range(3):
            note_dismissed(db, profile.id, job.sources[0].url, 1)
        db.commit()
        assert listed.dismissed == 3
        result = run_companies(db, user.id, profile.id, _payload(), _stages(), TODAY)
        assert result.leads == []
        notes = " ".join(step.detail for step in result.steps)
        assert "1 whose leads you turned down" in notes
        note_dismissed(db, profile.id, job.sources[0].url, -1)
        assert listed.dismissed == 2


def test_an_empty_catalog_says_how_it_fills(client, session_factory):
    with session_factory() as db:
        user, profile = _owner(db)
        result = run_companies(db, user.id, profile.id, _payload(), _stages(), TODAY)
        assert result.stop_code == "finished"
        assert "Nothing open in the catalog matches" in result.stop_message


def test_jobs_at_companies_on_no_list_are_found_in_the_catalog(client, session_factory):
    with session_factory() as db:
        user, profile = _owner(db)
        # Seen only on a job board, at a company no one has listed.
        record_listing(
            db,
            CatalogListing(
                source="larabench",
                url="https://larabench.com/jobs/7",
                company="Vandelay",
                title="PHP Developer",
                text=LARAVEL,
                location="Remote, US",
            ),
        )
        # Not seen for longer than the catalog trusts a board listing to stay open.
        stale = record_listing(
            db,
            CatalogListing(
                source="larabench",
                url="https://larabench.com/jobs/8",
                company="Kramerica",
                title="Laravel Developer",
                text=LARAVEL,
                location="Remote, US",
            ),
        )
        stale.last_seen = utcnow() - timedelta(days=45)
        db.commit()
        judge = Judge()
        result = run_companies(db, user.id, profile.id, _payload(), _stages(judge), TODAY)
        assert [page["company"] for page in judge.calls] == ["Vandelay"]
        assert [lead.company for lead in result.leads] == ["Vandelay"]


def test_similar_names_among_a_runs_companies_are_checked_and_charged(client, session_factory):
    with session_factory() as db:
        user, profile = _owner(db)
        _job(db, user, profile, "clair", "Laravel Engineer")
        record_listing(
            db,
            CatalogListing(
                source="linkedin",
                url="https://www.linkedin.com/jobs/view/1",
                company="Claire",
                title="Laravel Engineer",
            ),
        )
        db.commit()
        asked = []

        def same(payload):
            asked.append(payload)
            return {"model": "claude-haiku-5-5", "cost_usd": 0.001, "answers": [{"same": True}]}

        result = run_companies(db, user.id, profile.id, _payload(), _stages(same=same), TODAY)
        assert len(asked) == 1
        assert result.cost_usd == pytest.approx(0.003)
        assert len(db.scalars(select(Company)).all()) == 1


def test_the_worker_sends_every_run_to_the_one_engine(client, session_factory):
    from test_scout import _save_settings

    with session_factory() as db:
        user, profile = _owner(db)
        _job(db, user, profile, "acme", "Laravel Engineer")
        db.commit()
    _save_settings(
        client,
        session_factory,
        engine="companies",
        title_families=[],
        location="east coast timezone (anywhere)",
        description="Laravel internal tools",
        limits=default_limits("companies"),
    )
    assert client.post("/api/scout/runs").status_code == 201

    with session_factory() as db:
        run = claim_next(db)
        # A run queued under a retired engine runs on this one too.
        run.settings = {**run.settings, "engine": "agent"}
        db.commit()
        run_one(db, run, TODAY, _stages())
        db.refresh(run)
        assert run.status == "done" and [lead["title"] for lead in run.leads] == [
            "Laravel Engineer"
        ]
        assert run.leads[0]["posting_id"]
        # No brief during the run: the lead offers one instead, and is staged without it.
        assert run.leads[0]["brief_status"] == "none"
        assert run.settings["engine"] == "companies"
        listed = db.scalar(select(ProfileCompany))
        assert listed.leads == 1


def test_a_stack_rule_is_strict_and_plain_titles_need_no_stack_rule(client, session_factory):
    with session_factory() as db:
        user, profile = _owner(db)
        _job(db, user, profile, "acme", "Senior Software Engineer", text=OTHER)
        _job(db, user, profile, "acme", "Full-Stack Developer", text=OTHER)
        _job(db, user, profile, "globex", "Laravel Engineer")
        _job(db, user, profile, "globex", "Account Executive", text=OTHER)
        _job(db, user, profile, "initech", "Mechanical Engineer", text=OTHER)
        # Laravel only as a nice-to-have doesn't count for a Laravel brief.
        _job(
            db,
            user,
            profile,
            "hooli",
            "Backend Developer",
            text=OTHER + "\n\nNice to have\n- Laravel\n- Docker\n",
        )
        db.commit()
        # The description names Laravel, so the job must use it.
        judge = Judge()
        result = run_companies(db, user.id, profile.id, _payload(), _stages(judge), TODAY)
        assert [page["title"] for page in judge.calls] == ["Laravel Engineer"]
        notes = " ".join(step.detail for step in result.steps)
        assert "1 naming Laravel only as a nice-to-have" in notes

        # With no stack rule, no title phrases and no work boundary, the judge sees every title.
        judge = Judge()
        run_companies(db, user.id, profile.id, _payload(must_have=[]), _stages(judge), TODAY)
        assert {page["title"] for page in judge.calls} == {
            "Senior Software Engineer",
            "Full-Stack Developer",
            "Laravel Engineer",
            "Account Executive",
            "Mechanical Engineer",
            "Backend Developer",
        }

        # With title phrases and no stack rule, the title must name one of them.
        judge = Judge()
        run_companies(
            db,
            user.id,
            profile.id,
            _payload(must_have=[], title_families=["internal tools"]),
            _stages(judge),
            TODAY,
        )
        assert judge.calls == []


PHARMA = (
    "Own the brand plan for a diabetes medicine, work with sales, market access and agencies, "
    "and take promotional pieces through medical, legal and regulatory review. "
) * 8


class Work:
    """tailor's /pipeline/work, faked: one answer, or an error, counting its calls."""

    def __init__(self, answer=None, error=None):
        self.answer = answer or {
            "work": "Pharmaceutical brand marketing and pharma sales",
            "titles": ["brand manager", "sales representative", "x"],
        }
        self.error = error
        self.calls = []

    def __call__(self, material):
        self.calls.append(material)
        if self.error:
            raise self.error
        return {"model": "claude-haiku-5-5", "cost_usd": 0.003, "usage": {}, **self.answer}


class BriefJudge(Judge):
    """The judge, also keeping the brief each posting was judged under."""

    def __init__(self):
        super().__init__()
        self.briefs = []

    def __call__(self, payload):
        self.briefs.append(payload["brief"])
        return super().__call__(payload)


def _work_stages(judge, work):
    return CompanyStages(
        judge=judge,
        same_company=lambda payload: {"cost_usd": 0.0, "answers": []},
        work=work,
    )


def _settings_row(db, user, profile, **changes):
    row = ScoutSettings(
        user_id=user.id,
        profile_id=profile.id,
        description="anything im desperate",
        title_families=[],
        work_modes=[],
        limits={},
        **changes,
    )
    db.add(row)
    db.flush()
    return row


def test_the_work_boundary_keeps_a_vague_brief_inside_the_profiles_work(client, session_factory):
    with session_factory() as db:
        user, profile = _owner(db)
        row = _settings_row(db, user, profile)
        _job(db, user, profile, "acme", "Brand Manager, Diabetes", text=PHARMA)
        _job(db, user, profile, "globex", "PHP Developer", text=LARAVEL)
        _job(db, user, profile, "initech", "Pharmaceutical Sales Representative", text=PHARMA)
        db.commit()
        payload = _payload(description="anything im desperate", must_have=[])
        judge, work = BriefJudge(), Work()
        result = run_companies(db, user.id, profile.id, payload, _work_stages(judge, work), TODAY)
        assert {page["title"] for page in judge.calls} == {
            "Brand Manager, Diabetes",
            "Pharmaceutical Sales Representative",
        }
        assert judge.briefs[0]["work"] == "Pharmaceutical brand marketing and pharma sales"
        assert judge.briefs[0]["title_families"] == ["brand manager", "sales representative"]
        notes = " ".join(step.detail for step in result.steps)
        assert "Read your work as: Pharmaceutical brand marketing and pharma sales" in notes
        # The read is the run's: two screens and one read.
        assert result.cost_usd == pytest.approx(0.007)
        db.refresh(row)
        assert row.work_boundary["titles"] == ["brand manager", "sales representative"]
        assert row.work_boundary["edited"] is False

        # Read once: the same material doesn't call the model again.
        run_companies(
            db, user.id, profile.id, _payload(**payload), _work_stages(BriefJudge(), work), TODAY
        )
        assert len(work.calls) == 1
        # A changed fact sheet does.
        changed = _payload(**{**payload, "fact_sheet": "## Skills\n| Veeva CRM | Claimable |"})
        run_companies(db, user.id, profile.id, changed, _work_stages(BriefJudge(), work), TODAY)
        assert len(work.calls) == 2
        assert work.calls[1]["fact_sheet"].startswith("## Skills")

        # With a Must use list, the boundary's titles don't gate, but the judge still reads it.
        judge = BriefJudge()
        laravel = _payload(**{**changed, "must_have": ["Laravel"]})
        run_companies(db, user.id, profile.id, laravel, _work_stages(judge, work), TODAY)
        assert [page["title"] for page in judge.calls] == ["PHP Developer"]
        assert judge.briefs[0]["work"] == "Pharmaceutical brand marketing and pharma sales"
        assert judge.briefs[0]["title_families"] == []


def test_an_edited_work_line_is_kept_and_a_failed_read_uses_the_last(client, session_factory):
    with session_factory() as db:
        user, profile = _owner(db)
        boundary = {
            "work": "Brand marketing for medicines",
            "titles": ["brand manager"],
            "inputs_key": "from before",
            "made_at": "2026-11-10T00:00:00+00:00",
            "edited": True,
        }
        row = _settings_row(db, user, profile, work_boundary=boundary)
        _job(db, user, profile, "acme", "Brand Manager, Diabetes", text=PHARMA)
        db.commit()
        payload = _payload(description="anything im desperate", must_have=[])
        judge, work = BriefJudge(), Work()
        run_companies(db, user.id, profile.id, payload, _work_stages(judge, work), TODAY)
        assert work.calls == []
        assert judge.briefs[0]["work"] == "Brand marketing for medicines"

        # Not edited and out of date, but tailor is down: the last one is used, with a note.
        row.work_boundary = {**boundary, "edited": False}
        db.commit()
        judge, work = BriefJudge(), Work(error=TailorUnavailable("Tailor is down."))
        changed = _payload(**{**payload, "description": "anything at all"})
        result = run_companies(db, user.id, profile.id, changed, _work_stages(judge, work), TODAY)
        assert len(work.calls) == 1
        assert judge.briefs[0]["work"] == "Brand marketing for medicines"
        notes = " ".join(step.detail for step in result.steps)
        assert "used what Scout read before" in notes


def test_a_run_with_no_work_to_go_on_fails_uncounted(client, session_factory):
    with session_factory() as db:
        user, profile = _owner(db)
        _settings_row(db, user, profile)
        db.commit()
        payload = _payload(description="anything im desperate", must_have=[])
        nothing = Work(answer={"work": "", "titles": []})
        with pytest.raises(TailorUnavailable, match="couldn't tell what work to look for"):
            run_companies(db, user.id, profile.id, payload, _work_stages(Judge(), nothing), TODAY)
        down = Work(error=TailorUnavailable("Tailor is down."))
        with pytest.raises(TailorUnavailable, match="Couldn't read your work") as raised:
            run_companies(db, user.id, profile.id, payload, _work_stages(Judge(), down), TODAY)
        assert raised.value.spent is not None


def test_the_work_line_is_part_of_the_brief():
    from app.scout.judgment_memory import brief_key

    assert brief_key(_payload(work="Pharma brand marketing")) != brief_key(_payload())
    assert brief_key(_payload(work="")) == brief_key(_payload())
    boundary = _payload(must_have=[], work="Pharma", boundary_titles=["brand manager"])
    assert brief_key(boundary) != brief_key({**boundary, "boundary_titles": ["sales manager"]})
    # With a Must use list, the boundary's titles aren't searched under, so they don't count.
    strict = {**boundary, "must_have": ["Laravel"]}
    assert brief_key(strict) == brief_key({**strict, "boundary_titles": ["sales manager"]})


def test_a_typed_title_phrase_gates_and_ranks_titles(client, session_factory):
    with session_factory() as db:
        user, profile = _owner(db)
        _job(db, user, profile, "acme", "Laravel Data Analyst")
        _job(db, user, profile, "globex", "Laravel Clinical Systems Analyst")
        _job(db, user, profile, "initech", "Senior Software Engineer", text=OTHER)
        _job(db, user, profile, "hooli", "Clinical Systemsx Lead", text=OTHER)
        db.commit()

        # No stack rule: only a title with the exact phrase, in any case, reaches the judge.
        judge = Judge()
        payload = _payload(must_have=[], title_families=["Clinical systems"])
        run_companies(db, user.id, profile.id, payload, _stages(judge), TODAY)
        assert [page["title"] for page in judge.calls] == ["Laravel Clinical Systems Analyst"]

        # With a stack rule, a title naming a phrase is judged first, whichever phrase it is.
        for phrase, first in (
            ("clinical systems", "Laravel Clinical Systems Analyst"),
            ("data analyst", "Laravel Data Analyst"),
        ):
            judge = Judge()
            payload = _payload(must_have=["Laravel"], title_families=[phrase])
            run_companies(db, user.id, profile.id, payload, _stages(judge), TODAY)
            titles = [page["title"] for page in judge.calls]
            assert titles[0] == first
            assert set(titles) == {"Laravel Data Analyst", "Laravel Clinical Systems Analyst"}


def test_levels_are_flexible_by_one_rung_and_learned_from_wrong_level(client, session_factory):
    from app.scout.judgment_memory import brief_key, load_memory

    with session_factory() as db:
        user, profile = _owner(db)
        for token, title in (
            ("a", "Laravel Engineer Intern"),
            ("b", "Junior Laravel Developer"),
            ("c", "Laravel Developer II"),
            ("d", "Senior Laravel Engineer"),
            ("e", "Staff Laravel Engineer"),
            ("f", "Principal Laravel Architect"),
            ("g", "Laravel Engineering Manager"),
            ("h", "Laravel Developer"),
        ):
            _job(db, user, profile, token, title)
        db.commit()
        judge = Judge()
        payload = _payload(levels=["senior"])
        result = run_companies(db, user.id, profile.id, payload, _stages(judge), TODAY)
        # Senior first, then a rung either side, and the one whose level isn't known.
        titles = [page["title"] for page in judge.calls]
        assert titles[0] == "Senior Laravel Engineer"
        assert set(titles) == {
            "Senior Laravel Engineer",
            "Laravel Developer II",
            "Staff Laravel Engineer",
            "Laravel Developer",
        }
        notes = " ".join(step.detail for step in result.steps)
        assert "4 at a level you don't want" in notes

        # Turned down twice as the wrong level: staff is left out for this profile from then on.
        staff = [
            _job(db, user, profile, f"s{index}", f"Staff Laravel Engineer {index}")
            for index in range(2)
        ]
        db.add(
            ScoutRun(
                user_id=user.id,
                profile_id=profile.id,
                status="done",
                settings={},
                leads=[
                    {
                        "url": job.sources[0].url,
                        "state": "dismissed",
                        "decline_reason": "Wrong level",
                    }
                    for job in staff
                ],
                steps=[],
            )
        )
        db.commit()
        memory = load_memory(db, profile.id, brief_key(payload))
        assert memory.levels == frozenset({PostingLevel.STAFF})


def test_the_judge_is_told_which_postings_are_in_a_major_metro(client, session_factory):
    with session_factory() as db:
        user, profile = _owner(db)
        _job(db, user, profile, "acme", "Laravel Engineer", location="Orlando, FL")
        _job(db, user, profile, "globex", "Laravel Developer", location="Ada, OK")
        db.commit()
        judge = Judge()
        run_companies(
            db,
            user.id,
            profile.id,
            _payload(location="usa", relocation="metro"),
            _stages(judge),
            TODAY,
        )
        metro = {page["title"]: page["major_metro"] for page in judge.calls}
        assert metro == {"Laravel Engineer": True, "Laravel Developer": False}


def test_the_judge_reads_the_facts_and_code_holds_the_rules(client, session_factory):
    with session_factory() as db:
        user, profile = _owner(db)
        _job(db, user, profile, "acme", "Laravel Engineer")
        _job(db, user, profile, "globex", "Laravel Developer")
        _job(db, user, profile, "initech", "Laravel Platform Engineer")
        db.commit()
        facts = {
            "Laravel Engineer": {"stack_ok": False, "posting_level": "senior"},
            "Laravel Developer": {"stack_ok": True, "posting_level": "principal"},
            "Laravel Platform Engineer": {
                "stack_ok": True,
                "posting_level": "senior",
                "us_hireable": "no",
            },
        }

        def judge(payload):
            assert payload["brief"]["must_have"][0] == "Laravel"
            assert "Livewire" in payload["brief"]["must_have"]
            answer = Judge()(payload)
            answer["judgment"].update(facts[payload["posting"]["title"]])
            return answer

        result = run_companies(
            db, user.id, profile.id, _payload(levels=["senior"]), _stages(judge), TODAY
        )
        # One doesn't really use the stack, one is two rungs up: only the third is kept.
        assert [lead.title for lead in result.leads] == ["Laravel Platform Engineer"]
        assert result.leads[0].level == "senior"
        # What the judge read from each posting is stored on the shared catalog job.
        stored = {job.title: (job.level, job.us_hireable) for job in db.scalars(select(CatalogJob))}
        assert stored["Laravel Developer"] == ("principal", "unclear")
        assert stored["Laravel Platform Engineer"] == ("senior", "no")

        # A later brief from the US leaves that job out in code, without asking again.
        judge = Judge()
        result = run_companies(
            db, user.id, profile.id, _payload(location="Philadelphia, PA"), _stages(judge), TODAY
        )
        assert "Laravel Platform Engineer" not in [page["title"] for page in judge.calls]
        notes = " ".join(step.detail for step in result.steps)
        assert "1 not open to people in the US" in notes


def test_each_judgment_is_committed_before_the_next_model_call(client, session_factory):
    # A run spends minutes on model calls. What it writes to a shared catalog job is committed
    # before the next call, so other writers to that job don't wait for the whole run.
    with session_factory() as db:
        user, profile = _owner(db)
        _job(db, user, profile, "acme", "Laravel Engineer")
        _job(db, user, profile, "globex", "Laravel Developer")
        db.commit()
        judged = []
        seen_elsewhere = []

        def judge(payload):
            if judged:
                with session_factory() as other:
                    first = other.scalar(select(CatalogJob).where(CatalogJob.title == judged[0]))
                    seen_elsewhere.append(first.level)
            judged.append(payload["posting"]["title"])
            answer = Judge()(payload)
            answer["judgment"]["posting_level"] = "senior"
            return answer

        run_companies(db, user.id, profile.id, _payload(), _stages(judge), TODAY)
        assert len(judged) == 2
        assert seen_elsewhere == ["senior"]


def test_a_kept_posting_is_verified_before_it_is_a_lead(client, session_factory):
    with session_factory() as db:
        user, profile = _owner(db)
        _job(db, user, profile, "acme", "Laravel Engineer")
        _job(db, user, profile, "globex", "Laravel Developer")
        _job(db, user, profile, "initech", "Kotlin Engineer", text=OTHER + " Laravel.")
        db.commit()
        verified = []

        def verify(payload):
            verified.append(payload["posting"]["title"])
            answer = Judge()(payload)
            answer["cost_usd"] = 0.02
            if payload["posting"]["title"] == "Laravel Developer":
                answer["judgment"].update(keep=False, fit="none", reason="Second look: no.")
            return answer

        stages = CompanyStages(
            judge=Judge(),
            verify=verify,
            same_company=lambda payload: {"cost_usd": 0.0, "answers": []},
        )
        result = run_companies(db, user.id, profile.id, _payload(), stages, TODAY)
        # The screen kept all three, so each had a second look.
        assert sorted(verified) == ["Kotlin Engineer", "Laravel Developer", "Laravel Engineer"]
        assert sorted(lead.title for lead in result.leads) == [
            "Kotlin Engineer",
            "Laravel Engineer",
        ]
        assert result.cost_usd == pytest.approx(0.002 * 3 + 0.02 * 3)
        assert {verdict.verified for verdict in db.scalars(select(JobJudgment))} == {True}


def test_job_boards_are_searched_for_the_stack_and_recorded_first(client, session_factory):
    with session_factory() as db:
        user, profile = _owner(db)
        asked = []

        def boards(payload):
            asked.append(payload)
            record_listing(
                db,
                CatalogListing(
                    source="larabench",
                    url="https://larabench.com/jobs/1",
                    company="Vandelay",
                    title="Senior Laravel Engineer",
                    text=LARAVEL,
                    location="Remote, US",
                ),
            )
            return {"pages": [{"url": "https://larabench.com/jobs/1"}], "failed": ["Jooble"]}

        stages = CompanyStages(
            judge=Judge(),
            same_company=lambda payload: {"cost_usd": 0.0, "answers": []},
            boards=boards,
        )
        result = run_companies(
            db, user.id, profile.id, _payload(location="Philadelphia, PA"), stages, TODAY
        )
        assert asked == [{"queries": ["Laravel"], "location": "us"}]
        assert [lead.company for lead in result.leads] == ["Vandelay"]
        notes = " ".join(step.detail for step in result.steps)
        assert "Job boards: 1 listings open to the US for Laravel" in notes
        assert any("Couldn't search Jooble" in step.detail for step in result.steps)


# Finding new companies (chunk 4) and the fixes that came with it


class Discoverer:
    """tailor's discover stage, faked: returns a fixed list of companies."""

    def __init__(self, companies, searches=3, cost=0.03):
        self.companies = companies
        self.searches = searches
        self.cost = cost
        self.requests = []

    def __call__(self, payload):
        self.requests.append(payload)
        return {
            "model": "claude-haiku-5-5",
            "cost_usd": self.cost,
            "usage": {"output_tokens": 300},
            "request_costs": [{"request": 1, "cost_usd": self.cost}],
            "companies": self.companies,
            "searches": self.searches,
            "stop_code": "finished",
            "stop_message": "Done.",
            "steps": [{"kind": "search", "detail": "site:jobs.lever.co laravel", "request": 1}],
        }


def _careers(pages):
    def careers(payload):
        return {
            "pages": [
                {"url": url, "ok": url in pages, "links": pages.get(url, []), "error": ""}
                for url in payload["urls"]
            ]
        }

    return careers


def _boards(boards):
    def read(system, token):
        if token not in boards:
            raise FeedGone("answered 404")
        return boards[token]

    return read


def test_discovery_adds_companies_whose_boards_have_open_jobs(client, session_factory):
    with session_factory() as db:
        user, profile = _owner(db)
        _job(db, user, profile, "acme", "Laravel Engineer")
        db.commit()
        found = Discoverer(
            [
                # A board link from the search.
                {
                    "name": "Globex",
                    "website": "https://globex.io",
                    "careers_url": "https://jobs.lever.co/globex",
                    "why": "",
                },
                # A careers page that embeds a Greenhouse board.
                {
                    "name": "Initech",
                    "website": "https://initech.com",
                    "careers_url": "https://initech.com/careers",
                    "why": "",
                },
                # Jobs somewhere Callback can't read.
                {
                    "name": "Hooli",
                    "website": "https://hooli.xyz",
                    "careers_url": "https://hooli.xyz/jobs",
                    "why": "",
                },
                # A board with nothing open.
                {
                    "name": "Vandelay",
                    "website": "",
                    "careers_url": "https://jobs.ashbyhq.com/vandelay",
                    "why": "",
                },
                {"name": "Robert Half", "website": "", "careers_url": "", "why": ""},
                {"name": "Acme", "website": "", "careers_url": "", "why": ""},
            ]
        )
        stages = CompanyStages(
            judge=Judge(),
            same_company=lambda payload: {"answers": []},
            discover=found,
            careers=_careers(
                {
                    "https://initech.com/careers": [
                        "https://boards.greenhouse.io/embed/job_board/js?for=initech"
                    ],
                    "https://hooli.xyz/jobs": [],
                }
            ),
            read_feed=_boards(
                {
                    "globex": _feed(("https://jobs.lever.co/globex/1", "Laravel Developer")),
                    "initech": _feed(
                        ("https://job-boards.greenhouse.io/initech/jobs/5", "PHP Engineer")
                    ),
                    "vandelay": [],
                }
            ),
        )
        result = run_companies(db, user.id, profile.id, _payload(budget_usd=2.0), stages, TODAY)

        listed = {
            db.get(Company, row.company_id).name: row.origin
            for row in db.scalars(select(ProfileCompany))
        }
        assert listed == {"Acme": "lead", "Globex": "discovered", "Initech": "discovered"}
        # Their jobs were read during the run, so they were judged in it.
        assert {lead.company for lead in result.leads} == {"Acme", "Globex", "Initech"}
        # Hooli is in the catalog with its website, but not read.
        hooli = db.scalar(select(Company).where(Company.name == "Hooli"))
        assert hooli.domain == "hooli.xyz"
        assert db.scalar(select(CompanySource).where(CompanySource.company_id == hooli.id)) is None
        notes = " ".join(step.detail for step in result.steps)
        assert "Looked for new companies with 3 web searches: 2 added to your list" in notes
        assert "1 already on it" in notes and "1 with no open jobs" in notes
        assert "1 agencies left out" in notes
        assert "aren't on a system Callback can read: Hooli" in notes
        # Discovery gets its share of the budget and time, and its cost counts.
        request = found.requests[0]
        assert request["budget_usd"] == pytest.approx(0.3)
        assert request["limits"]["max_minutes"] == 4
        assert request["technologies"] and request["listed_count"] == 1
        assert result.searches == 3
        assert result.cost_usd == pytest.approx(0.03 + 0.002 * 3)


def test_saving_a_posting_from_a_company_board_puts_it_on_the_list(client, session_factory):
    saved = client.post(
        "/api/postings",
        json={
            "company": "what I typed",
            "title": "Engineer",
            "url": "https://jobs.lever.co/acme-labs/123",
        },
    )
    assert saved.status_code == 201, saved.text
    client.post(
        "/api/postings",
        json={"company": "Elsewhere", "title": "Engineer", "url": "https://elsewhere.io/jobs/1"},
    )
    with session_factory() as db:
        [listed] = db.scalars(select(ProfileCompany)).all()
        assert listed.origin == "postings"
        assert db.get(Company, listed.company_id).name == "Acme Labs"


def test_more_places_abroad_are_ruled_out_but_us_towns_are_not():
    from app.scout.filters import _abroad

    for place in ("Vilnius", "Buenos Aires", "São Paulo", "Remote - Mexico City", "Bogotá"):
        assert _abroad(place, True), place
    for place in ("Vienna, VA", "Perth Amboy, NJ", "Delhi, NY", "Remote, US"):
        assert not _abroad(place, True), place


def test_companies_take_turns_so_one_cant_fill_a_run(client, session_factory):
    with session_factory() as db:
        user, profile = _owner(db)
        for index in range(4):
            _job(db, user, profile, "addepar", f"Laravel Engineer {index}")
        _job(db, user, profile, "globex", "Laravel Developer")
        _job(db, user, profile, "initech", "Laravel Lead")
        db.commit()
        judge = Judge()
        run_companies(db, user.id, profile.id, _payload(), _stages(judge), TODAY)
        companies = [page["company"] for page in judge.calls]
        assert companies[:3].count("Addepar") == 1
        assert companies[3:] == ["Addepar"] * 3


def test_board_embeds_name_the_board():
    from app.companies.catalog import hiring_ref

    ref = hiring_ref("https://boards.greenhouse.io/embed/job_board/js?for=acme&b=x")
    assert (ref.system, ref.token, ref.job_id) == ("greenhouse", "acme", None)
    assert hiring_ref("https://jobs.ashbyhq.com/acme/embed").job_id is None


def test_a_name_from_a_token_is_never_replaced_by_another_companys_name(client, session_factory):
    from app.companies.identity import resolve_company

    with session_factory() as db:
        acme = resolve_company(db, "Acme").company
        globex = resolve_company(db, "Globex", source=("lever", "globex")).company
        from app.companies.catalog import company_source

        company_source(db, globex, "lever", "globex", is_own=True)
        db.flush()
        assert resolve_company(db, "Acme", source=("lever", "globex")).company is globex
        assert globex.name == "Globex" and acme.name == "Acme"
        resolve_company(db, "Globex Systems", source=("lever", "globex"))
        assert globex.name == "Globex Systems"


# Remembering verdicts and learning from them


def test_a_job_judged_for_a_brief_isnt_judged_again(client, session_factory):
    with session_factory() as db:
        user, profile = _owner(db)
        job = _job(db, user, profile, "acme", "Kotlin Engineer", text=OTHER + " Laravel.")
        db.commit()
        judge = Judge()
        run_companies(db, user.id, profile.id, _payload(), _stages(judge), TODAY)
        assert len(judge.calls) == 1
        again = Judge()
        result = run_companies(db, user.id, profile.id, _payload(), _stages(again), TODAY)
        assert again.calls == []
        assert any("1 judged before for this brief" in step.detail for step in result.steps)
        assert result.stop_message.startswith("Every job in the catalog that matches")
        # A changed brief, or a changed posting, is judged afresh.
        changed = Judge()
        run_companies(
            db, user.id, profile.id, _payload(location="Philadelphia, PA"), _stages(changed), TODAY
        )
        assert len(changed.calls) == 1
        job.text = job.text + " Now hiring two."
        db.commit()
        edited = Judge()
        run_companies(db, user.id, profile.id, _payload(), _stages(edited), TODAY)
        assert len(edited.calls) == 1


def test_a_rejection_covers_the_same_job_posted_elsewhere(client, session_factory):
    with session_factory() as db:
        user, profile = _owner(db)
        for city in ("Austin, TX", "Boston, MA", "Denver, CO"):
            record_listing(
                db,
                CatalogListing(
                    source="greenhouse",
                    url=f"https://job-boards.greenhouse.io/acme/jobs/{abs(hash(city)) % 10**6}",
                    company="Acme",
                    title="Laravel Engineer",
                    text=OTHER,
                    location=city,
                ),
            )
        job = db.scalar(select(CatalogJob))
        add_to_profile(db, user.id, profile.id, job.company_id, ProfileCompanyOrigin.LEAD)
        for source in db.scalars(select(CompanySource)):
            source.last_checked = utcnow()
        db.commit()
        # Laravel in the title passes the work filter; the text without it, the judge rejects.
        judge = Judge()
        result = run_companies(db, user.id, profile.id, _payload(), _stages(judge), TODAY)
        assert len(judge.calls) == 1
        assert any("2 the same job as one turned down" in step.detail for step in result.steps)


def _location_judge(rejected_places):
    def judge(payload):
        posting = payload["posting"]
        bad = posting["location"] in rejected_places
        return {
            "cost_usd": 0.001,
            "judgment": {
                "keep": not bad,
                "fit": "none" if bad else "good",
                "work_match": "Laravel tools.",
                "location_ok": not bad,
                "agency": "",
                "reason": "Too far." if bad else "Fits.",
            },
            "calls": None,
        }

    return judge


def test_places_the_judge_always_turns_down_are_learned(client, session_factory):
    from app.scout.judgment_memory import EXPLORE_ONE_IN, brief_key, load_memory, places

    assert places("San Francisco, CA; Remote, US") == ["san francisco, ca"]
    assert places("New York, NY / Boston, MA, United States") == ["new york, ny", "boston, ma"]
    with session_factory() as db:
        user, profile = _owner(db)
        for index in range(4):
            _job(
                db,
                user,
                profile,
                f"sf{index}",
                f"Laravel Engineer {index}",
                location="San Francisco, CA",
            )
        db.commit()
        run_companies(
            db,
            user.id,
            profile.id,
            _payload(),
            _stages(_location_judge({"San Francisco, CA"})),
            TODAY,
        )
        memory = load_memory(db, profile.id, brief_key(_payload()))
        assert memory.places == {"san francisco, ca"}
        # New San Francisco postings are left out, apart from the one in ten still judged.
        for index in range(20):
            _job(
                db,
                user,
                profile,
                f"sfnew{index}",
                f"Laravel Dev {index}",
                location="San Francisco, CA",
            )
        _job(db, user, profile, "remote1", "Laravel Dev R", location="San Francisco, CA or Remote")
        db.commit()
        calls = []

        def judge(payload):
            calls.append(payload["posting"]["location"])
            return _location_judge({"San Francisco, CA"})(payload)

        result = run_companies(db, user.id, profile.id, _payload(), _stages(judge), TODAY)
        assert "San Francisco, CA or Remote" in calls
        assert calls.count("San Francisco, CA") < 20 // EXPLORE_ONE_IN + 3
        notes = " ".join(step.detail for step in result.steps)
        assert (
            "only at places the judge has always turned down for you (san francisco, ca)" in notes
        )


def test_title_words_always_passed_on_are_learned_and_unlearned(client, session_factory):
    from app.scout.judgment_memory import brief_key, load_memory

    # Manager roles are left out in code unless wanted, so this brief asks for them.
    managers = _payload(levels=["manager"])
    with session_factory() as db:
        user, profile = _owner(db)
        for index in range(6):
            _job(db, user, profile, f"m{index}", f"Laravel Engineering Manager {index}", text=OTHER)
        db.commit()
        run_companies(db, user.id, profile.id, managers, _stages(), TODAY)
        learned = load_memory(db, profile.id, brief_key(managers), {"laravel"}).words
        assert "manager" in learned
        # The brief's own stack is never learned as a word to pass on.
        assert "laravel" not in learned
        # One kept manager role is enough to unlearn it.
        keep = _job(db, user, profile, "goodmgr", "Laravel Engineering Manager Kept")
        db.flush()
        from app.scout.judgment_memory import remember

        remember(
            db,
            user.id,
            profile.id,
            None,
            keep,
            brief_key(managers),
            {"keep": True, "fit": "good", "location_ok": True, "reason": "Fits."},
        )
        db.commit()
        assert "manager" not in load_memory(db, profile.id, brief_key(managers), {"laravel"}).words


def test_east_coast_names_the_eastern_states():
    assert {"PA", "NY", "FL", "GA"} <= states_named("east coast timezone (anywhere)")
    assert "CA" not in states_named("east coast timezone (anywhere)")
    assert states_named("Remote (PST)") == {"CA", "OR", "WA", "NV"}


# Finding companies in the catalog


def _board_listing(db, company, title, url, text=LARAVEL, **changes):
    """A job seen on a job board, with no hiring-system link."""
    return record_listing(
        db,
        CatalogListing(
            source="remotive", url=url, company=company, title=title, text=text, **changes
        ),
    )


def test_board_names_come_from_the_company_name():
    from app.companies.catalog_discovery import board_tokens

    assert board_tokens("Acme Labs, Inc.") == [
        "acmelabs",
        "acme-labs",
        "acmelabsinc",
        "acme-labs-inc",
    ]
    assert board_tokens("The Trade Desk") == ["tradedesk", "trade-desk"]
    assert board_tokens("Q") == []


def test_the_catalog_adds_matching_companies_without_a_model(client, session_factory):
    from app.companies.catalog_discovery import discover_from_catalog

    with session_factory() as db:
        user, profile = _owner(db)
        # A known board, not on the list yet: added straight away.
        record_listing(
            db,
            CatalogListing(
                source="lever",
                url="https://jobs.lever.co/known/1",
                company="Known Co",
                title="Laravel Developer",
                text=LARAVEL,
            ),
        )
        # Seen only on a board; its own Greenhouse board shares a title with it.
        _board_listing(db, "Globex Corp", "Senior Laravel Engineer", "https://remotive.com/jobs/1")
        # A same-named stranger: a Lever board with none of its titles.
        _board_listing(db, "Initech", "Laravel Developer", "https://remotive.com/jobs/2")
        # Not the work, an agency, and a hidden company: never tried.
        _board_listing(db, "Hooli", "Kotlin Engineer", "https://remotive.com/jobs/3", text=OTHER)
        _board_listing(db, "Northwind Staffing", "Laravel Dev", "https://remotive.com/jobs/4")
        _board_listing(db, "Umbrella", "Laravel Dev", "https://remotive.com/jobs/5")
        db.commit()
        tried = []

        def read(system, token):
            tried.append(f"{system}:{token}")
            if (system, token) == ("greenhouse", "globexcorp"):
                return _feed(
                    (
                        "https://job-boards.greenhouse.io/globexcorp/jobs/9",
                        "Senior Laravel Engineer",
                    )
                )
            if (system, token) == ("lever", "initech"):
                return _feed(("https://jobs.lever.co/initech/1", "Office Manager"))
            raise FeedGone("answered 404")

        finds = discover_from_catalog(db, user.id, profile.id, ["Laravel"], {"umbrella"}, read)
        listed = {
            db.get(Company, row.company_id).name: row.origin
            for row in db.scalars(select(ProfileCompany))
        }
        assert listed == {"Known Co": "discovered", "Globex Corp": "discovered"}
        assert finds.found == ["Globex Corp"] and finds.probed == 2
        assert not any(
            token.split(":")[1] in ("hooli", "northwindstaffing", "umbrella") for token in tried
        )
        globex = db.scalar(select(Company).where(Company.name == "Globex Corp"))
        source = db.scalar(
            select(CompanySource).where(CompanySource.company_id == globex.id, CompanySource.is_own)
        )
        assert (source.source, source.identifier, source.last_checked is not None) == (
            "greenhouse",
            "globexcorp",
            True,
        )
        assert (
            "Looked for companies in Callback's catalog, with no searches: 2 added"
            in finds.notes()[0]
        )

        # Tried companies aren't tried again for a while.
        tried.clear()
        discover_from_catalog(db, user.id, profile.id, ["Laravel"], {"umbrella"}, read)
        assert tried == []


def test_web_search_for_companies_waits_when_the_catalog_gives_enough(client, session_factory):
    with session_factory() as db:
        user, profile = _owner(db)
        for token in ("acme", "globex", "initech"):
            _job(db, user, profile, token, "Laravel Engineer")
        db.commit()
        searched = []

        def discover(payload):
            searched.append(payload)
            return {"companies": [], "searches": 0, "steps": []}

        stages = CompanyStages(
            judge=Judge(),
            same_company=lambda payload: {"answers": []},
            discover=discover,
            careers=_careers({}),
            read_feed=_boards({}),
        )
        # Three jobs to check is enough for one lead: three times the target.
        limits = {**_payload()["limits"], "target_leads": 1}
        result = run_companies(
            db, user.id, profile.id, _payload(limits=limits), stages, TODAY, wait_seconds=0
        )
        assert searched == []
        notes = " ".join(step.detail for step in result.steps)
        assert "Didn't search the web for more companies: the catalog gave 3 jobs" in notes

        # Asking for more than the catalog can give sends the search out, for the stack only.
        run_companies(
            db, user.id, profile.id, _payload(location="Texas"), stages, TODAY, wait_seconds=0
        )
        assert len(searched) == 1
        assert searched[0]["technologies"][0] == "Laravel"
        assert "Livewire" in searched[0]["technologies"]


# Relocation


def test_major_metros_are_known_by_city_and_state():
    from app.scout.metros import in_major_metro

    for place in (
        "Pittsburgh, PA",
        "Mountain View, CA",
        "New York, NY; Remote",
        "Washington, DC",
        "Arlington, VA",
        "Arlington, TX",
        "Austin",
        "Denver, Colorado",
        "Boston Metro Area",
    ):
        assert in_major_metro(place), place
    for place in ("Portland, ME", "Bozeman, MT", "Remote", "", None, "Springfield, IL"):
        assert not in_major_metro(place), place


def test_relocation_lets_onsite_jobs_elsewhere_reach_the_judge(client, session_factory):
    with session_factory() as db:
        user, profile = _owner(db)
        _job(
            db,
            user,
            profile,
            "denver",
            "Laravel Engineer",
            location="Denver, CO",
            work_mode="onsite",
        )
        _job(
            db,
            user,
            profile,
            "bozeman",
            "Laravel Engineer",
            location="Bozeman, MT",
            work_mode="onsite",
        )
        _job(
            db,
            user,
            profile,
            "philly",
            "Laravel Engineer",
            location="Philadelphia, PA",
            work_mode="onsite",
        )
        db.commit()

        def judged(relocation):
            judge = Judge()
            run_companies(
                db,
                user.id,
                profile.id,
                _payload(location="east coast timezone (anywhere)", relocation=relocation),
                _stages(judge),
                TODAY,
            )
            return sorted(page["location"] for page in judge.calls)

        assert judged("none") == ["Philadelphia, PA"]
        assert judged("metro") == ["Denver, CO", "Philadelphia, PA"]
        assert judged("anywhere") == ["Bozeman, MT", "Denver, CO", "Philadelphia, PA"]


def test_relocation_changes_the_brief_only_when_set():
    from app.scout.judgment_memory import brief_key

    assert brief_key(_payload()) == brief_key(_payload(relocation="none"))
    assert brief_key(_payload(relocation="metro")) != brief_key(_payload())


def test_old_title_families_keep_their_brief_key_as_phrases():
    from app.scout.judgment_memory import brief_key

    old = _payload(title_families=["internal_tools", "automation"])
    migrated = _payload(title_families=[MIGRATION.PHRASES[v] for v in old["title_families"]])
    assert migrated["title_families"] == ["internal tools", "automation engineer"]
    assert brief_key(migrated) == brief_key(old)
    assert brief_key(_payload(title_families=[" Automation Engineer", "internal tools"])) == (
        brief_key(old)
    )
    assert brief_key(_payload(title_families=["internal tools", "clinical systems"])) != (
        brief_key(old)
    )


def test_the_migration_turns_each_old_value_into_its_phrase_and_back():
    assert MIGRATION.PHRASES == OLD_TITLE_PHRASES
    # The phrases each old value searched for before they could be typed.
    assert MIGRATION.PHRASES == {
        "forward_deployed": "forward deployed",
        "solutions": "solutions engineer",
        "implementation": "implementation engineer",
        "internal_tools": "internal tools",
        "business_systems": "business systems",
        "automation": "automation engineer",
        "integration": "integration engineer",
    }
    assert MIGRATION.to_phrases(["solutions", "integration"]) == [
        "solutions engineer",
        "integration engineer",
    ]
    assert MIGRATION.to_values(
        ["Solutions Engineer", "clinical systems", "solutions engineer"]
    ) == ["solutions"]


def test_the_stack_and_levels_are_part_of_the_brief():
    from app.scout.judgment_memory import brief_key

    # Read from the description or given outright, the same stack is the same brief.
    assert brief_key(_payload()) == brief_key(_payload(must_have=["Laravel"]))
    assert brief_key(_payload(must_have=["Django"])) != brief_key(_payload())
    assert brief_key(_payload(levels=["senior"])) != brief_key(_payload())
    assert brief_key(_payload(rule_out=["Java"])) != brief_key(_payload())
