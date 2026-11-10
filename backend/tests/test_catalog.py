"""The Companies catalog's jobs: links, titles, duplicates across boards, and their sources."""

import uuid
from types import SimpleNamespace

from psycopg.errors import DeadlockDetected
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from app.companies.catalog import (
    CatalogListing,
    CatalogRecorder,
    fingerprint,
    hiring_ref,
    record_kept,
    record_listing,
    text_distance,
    title_key,
)
from app.companies.identity import check_pending_pairs, resolve_company
from app.listings.boards import RawListing
from app.models import (
    BoardStat,
    CatalogJob,
    Company,
    CompanySource,
    Role,
    User,
)
from app.scout.worker import record_run

GH = "https://job-boards.greenhouse.io/acme/jobs/4374366009"
# A posting long enough to compare by its text.
POSTING = (
    "Acme builds the tools our operations team runs on every day. You'll own internal services "
    "written in Python and PHP, automate the work people now do by hand, and talk with the "
    "people who use what you build. We ship small changes often and review each other's code. "
    "You have five years building web applications, know SQL well, and like working directly "
    "with the people who use your tools. Experience with Laravel, queues and integrations with "
    "third-party systems such as Salesforce or NetSuite is a plus. We offer health insurance, "
    "a learning budget, and four weeks of paid time off. The team is spread across US time "
    "zones and meets in person twice a year."
)


def _count(db, model) -> int:
    return db.scalar(select(func.count()).select_from(model))


def _listing(**changes) -> CatalogListing:
    values = {
        "source": "greenhouse",
        "url": GH,
        "company": "Acme Inc",
        "title": "Senior Software Engineer",
        "text": POSTING,
        "location": "Remote, US",
    }
    values.update(changes)
    return CatalogListing(**values)


def test_hiring_system_links_give_their_company_and_job():
    cases = {
        GH: ("greenhouse", "acme", "4374366009"),
        "https://boards.greenhouse.io/Acme/jobs/4374366009?gh_src=x": (
            "greenhouse",
            "acme",
            "4374366009",
        ),
        "https://boards.greenhouse.io/embed/job_app?for=acme&token=4374366009": (
            "greenhouse",
            "acme",
            "4374366009",
        ),
        "https://boards-api.greenhouse.io/v1/boards/acme/jobs/12": ("greenhouse", "acme", "12"),
        "https://acme.com/careers?gh_jid=4374366009": ("greenhouse", None, "4374366009"),
        "https://jobs.lever.co/acme/6ed76ce8-4156-4b60-b120-403538bd66cd/apply": (
            "lever",
            "acme",
            "6ed76ce8-4156-4b60-b120-403538bd66cd",
        ),
        "https://jobs.lever.co/acme": ("lever", "acme", None),
        "https://jobs.ashbyhq.com/acme/1f0a-22": ("ashby", "acme", "1f0a-22"),
    }
    for url, expected in cases.items():
        ref = hiring_ref(url)
        assert (ref.system, ref.token, ref.job_id) == expected, url


def test_tokens_that_could_reach_another_address_are_refused():
    for url in (
        "https://jobs.lever.co/acme.com/123",
        "https://boards.greenhouse.io/embed/job_app?for=acme/../x&token=1",
        "https://jobs.ashbyhq.com/a%2Fb/1",
        "https://example.com/jobs/1",
        "",
        None,
    ):
        assert hiring_ref(url) is None, url


def test_title_keys_settle_spelling_but_keep_levels_and_teams():
    assert title_key("Sr. Software Engineer - Remote") == "senior software engineer"
    assert title_key("Senior Software Engineer (Remote, US)") == "senior software engineer"
    assert title_key("Senior Software Engineer - New York, NY") == "senior software engineer"
    assert title_key("Senior Software Engineer [Hybrid] - US") == "senior software engineer"
    assert title_key("Jr Eng Mgr") == "junior engineer manager"
    assert title_key("R&D Engineer") == "r and d engineer"
    assert title_key("Engineer II") != title_key("Engineer III")
    assert title_key("Software Engineer - Platform") == "software engineer platform"


def test_fingerprints_tell_near_copies_from_other_postings():
    assert fingerprint("too short") is None
    first = fingerprint(POSTING)
    reworded = fingerprint(POSTING.replace("four weeks", "five weeks"))
    other = fingerprint(POSTING.replace("Acme builds", "Globex sells").upper()[::-1] + POSTING[:50])
    assert text_distance(first, reworded) <= 3
    assert text_distance(first, other) > 3


def test_one_job_from_four_places_with_the_employer_posting_as_its_link(session_factory):
    with session_factory() as db:
        feed = record_listing(db, _listing())
        other_link = record_listing(
            db, _listing(url="https://boards.greenhouse.io/acme/jobs/4374366009", text="")
        )
        indeed = record_listing(
            db,
            _listing(
                source="indeed",
                url="https://www.indeed.com/viewjob?jk=abc",
                company="Acme",
                direct_url=GH,
                text="Snippet only.",
            ),
        )
        linkedin = record_listing(
            db,
            _listing(
                source="linkedin",
                url="https://www.linkedin.com/jobs/view/99",
                company="ACME",
                title="Sr. Software Engineer - Remote",
                text="Snippet only.",
                location="United States",
            ),
        )
        db.commit()
        assert feed.id == other_link.id == indeed.id == linkedin.id
        assert _count(db, CatalogJob) == 1 and _count(db, Company) == 1
        sources = {source.url: source for source in feed.sources}
        assert set(sources) == {
            GH,
            "https://boards.greenhouse.io/acme/jobs/4374366009",
            "https://www.indeed.com/viewjob?jk=abc",
            "https://www.linkedin.com/jobs/view/99",
        }
        assert [url for url, source in sources.items() if source.is_primary] == [GH]
        assert sources["https://www.linkedin.com/jobs/view/99"].source == "linkedin"
        # Its own hiring system and the boards it's on are noted for the company.
        assert {
            (row.source, row.identifier, row.is_own) for row in db.scalars(select(CompanySource))
        } == {
            ("greenhouse", "acme", True),
            ("indeed", "Acme", False),
            ("linkedin", "ACME", False),
        }
        stats = {stat.board: stat.listings_recorded for stat in db.scalars(select(BoardStat))}
        assert stats == {"greenhouse": 2, "indeed": 1, "linkedin": 1}


def test_the_employer_posting_becomes_the_link_when_it_arrives_later(session_factory):
    with session_factory() as db:
        board = record_listing(
            db,
            _listing(
                source="linkedin",
                url="https://www.linkedin.com/jobs/view/99",
                company="Acme",
                title="Sr. Software Engineer (Remote)",
            ),
        )
        assert [s.is_primary for s in board.sources] == [True]
        job = record_listing(db, _listing())
        db.commit()
        assert job.id == board.id
        primary = [source for source in job.sources if source.is_primary]
        assert [source.url for source in primary] == [GH]
        assert job.title == "Senior Software Engineer"


def test_a_repost_with_a_new_id_and_the_same_text_is_the_same_job(session_factory):
    with session_factory() as db:
        first = record_listing(db, _listing())
        repost = record_listing(
            db,
            _listing(
                url="https://job-boards.greenhouse.io/acme/jobs/5000000001",
                text=POSTING.replace("four weeks", "five weeks"),
            ),
        )
        assert repost.id == first.id
        assert len(first.sources) == 2


def test_near_identical_text_on_another_board_joins_a_reworded_title(session_factory):
    with session_factory() as db:
        first = record_listing(db, _listing(title="Software Engineer, Internal Tools"))
        elsewhere = record_listing(
            db,
            _listing(
                source="remotive",
                url="https://remotive.com/jobs/7",
                title="Internal Tools Software Engineer",
            ),
        )
        assert elsewhere.id == first.id


def test_one_boards_templated_postings_with_different_titles_stay_apart(session_factory):
    """One posting per language, the same text apart from the language, is one job each."""
    with session_factory() as db:
        maltese = record_listing(
            db,
            _listing(
                source="jobicy",
                url="https://jobicy.com/jobs/158982",
                company="Welo Global",
                title="English to Maltese Review Lead",
                text=POSTING.replace("four weeks", "five weeks"),
            ),
        )
        lao = record_listing(
            db,
            _listing(
                source="jobicy",
                url="https://jobicy.com/jobs/158980",
                company="Welo Global",
                title="English to Lao Review Lead",
            ),
        )
        assert lao.id != maltese.id


def test_tracking_parameters_dont_make_a_new_link(session_factory):
    with session_factory() as db:
        base = "https://jooble.org/away/6978840992360744269"
        first = record_listing(
            db, _listing(source="jooble", url=f"{base}?p=1&pos=2&rgn=55157&ckey=Laravel")
        )
        again = record_listing(
            db, _listing(source="jooble", url=f"{base}?pos=7&brelb=10&scr=420.8&aq=-2")
        )
        tracked = record_listing(
            db, _listing(source="indeed", url="https://www.indeed.com/viewjob?jk=1&utm_source=x")
        )
        assert again.id == first.id
        assert {source.url for source in first.sources} >= {base}
        assert "https://www.indeed.com/viewjob?jk=1" in {s.url for s in tracked.sources}


def test_the_same_title_in_two_cities_is_two_jobs(session_factory):
    with session_factory() as db:
        austin = record_listing(
            db,
            _listing(url="https://jobs.lever.co/acme/a1", location="Austin, TX", text=""),
        )
        boston = record_listing(
            db,
            _listing(url="https://jobs.lever.co/acme/b2", location="Boston, MA", text=""),
        )
        assert austin.id != boston.id


def test_agency_listings_join_the_employer_job_they_repost(session_factory):
    with session_factory() as db:
        job = record_listing(db, _listing(company="Syllo"))
        named = record_listing(
            db,
            _listing(
                source="linkedin",
                url="https://www.linkedin.com/jobs/view/1",
                company="Syllo (via TLA Tech)",
                text="Snippet only.",
            ),
        )
        assert named.id == job.id
        tla = resolve_company(db, "TLA Tech").company
        assert tla.kind == "agency"
        agency_source = next(s for s in job.sources if s.url.endswith("/view/1"))
        assert agency_source.agency_id == tla.id and not agency_source.is_primary

        # TLA Tech posts for Syllo, so its own listing with Syllo's text is Syllo's job.
        unnamed = record_listing(
            db,
            _listing(
                source="indeed",
                url="https://www.indeed.com/viewjob?jk=tla",
                company="TLA Tech",
                title="Backend Engineer (Contract)",
                text=POSTING.replace("four weeks", "five weeks"),
            ),
        )
        assert unnamed.id == job.id

        # A company TLA Tech never posted for isn't matched by text.
        globex_text = (
            "Globex runs payment infrastructure for regional banks across the Midwest. This "
            "platform role keeps our Kubernetes clusters, Terraform modules and observability "
            "stack healthy, and you'll be on call one week in six. You know Go or Rust, have "
            "run Postgres at scale, and have led incident reviews. We work from our Chicago "
            "office three days a week and offer a pension, private health cover, a yearly "
            "bonus, and a budget for conferences. Our interview has a take-home exercise, a "
            "systems design conversation and a chat with two engineers from the team you'd join."
        )
        globex = record_listing(
            db,
            _listing(company="Globex", url="https://jobs.lever.co/globex/9", text=globex_text),
        )
        stranger = record_listing(
            db,
            _listing(
                source="indeed",
                url="https://www.indeed.com/viewjob?jk=tla2",
                company="TLA Tech",
                title="Platform Engineer",
                text=globex_text,
            ),
        )
        db.commit()
        assert stranger.company_id == tla.id and stranger.id != globex.id


def test_a_judge_named_agency_makes_the_listing_the_employers(session_factory):
    with session_factory() as db:
        job = record_listing(
            db,
            _listing(
                source="web",
                url="https://roberthalf.com/jobs/7",
                company="Initech",
                agency="Robert Half",
                text="",
            ),
        )
        db.commit()
        assert db.get(Company, job.company_id).name == "Initech"
        [source] = job.sources
        assert db.get(Company, source.agency_id).kind == "agency"


def test_merging_two_companies_merges_their_copies_of_a_job(session_factory):
    with session_factory() as db:
        clair = record_listing(db, _listing(company="Clair", url="https://jobs.lever.co/clair/1"))
        claire = record_listing(
            db, _listing(company="Claire", url="https://www.linkedin.com/jobs/view/5", text="x")
        )
        assert clair.company_id != claire.company_id

        def ask(payload):
            return {"answers": [{"same": True}] * len(payload["pairs"])}

        check_pending_pairs(db, ask)
        db.commit()
        assert _count(db, Company) == 1 and _count(db, CatalogJob) == 1
        job = db.scalar(select(CatalogJob))
        assert {source.url for source in job.sources} == {
            "https://jobs.lever.co/clair/1",
            "https://www.linkedin.com/jobs/view/5",
        }
        assert [s.url for s in job.sources if s.is_primary] == ["https://jobs.lever.co/clair/1"]
        assert db.scalar(select(CompanySource.company_id).where(CompanySource.source == "lever"))


def test_a_kept_lead_counts_for_every_place_its_job_is_listed(session_factory):
    with session_factory() as db:
        record_listing(db, _listing())
        record_listing(
            db,
            _listing(source="remotive", url="https://remotive.com/jobs/1", company="Acme"),
        )
        record_kept(db, GH)
        record_kept(db, "https://never.seen/job")
        db.commit()
        kept = {stat.board: stat.kept for stat in db.scalars(select(BoardStat))}
        assert kept == {"greenhouse": 1, "remotive": 1}
        assert all(stat.last_kept_at for stat in db.scalars(select(BoardStat)))


def test_the_recorder_records_board_results_in_the_background(session_factory):
    recorder = CatalogRecorder(session_factory)
    raws = [
        RawListing(
            company="Acme",
            title="Platform Engineer",
            url="https://www.indeed.com/viewjob?jk=1",
            description="Text.",
            location="Remote",
            direct_url="https://jobs.lever.co/acme/123",
        ),
        RawListing(company="!!!", title="Nameless", url="https://x.io/1", description=""),
    ]
    assert recorder.submit("indeed", raws).result(timeout=30) == 1
    with session_factory() as db:
        job = db.scalar(select(CatalogJob))
        assert {(s.source, s.is_primary) for s in job.sources} == {
            ("indeed", False),
            ("lever", True),
        }


def _deadlock():
    return OperationalError("UPDATE catalog_jobs ...", {}, DeadlockDetected("deadlock detected"))


def _failing(real, errors):
    """real, raising each of errors on its first calls in turn, and counting every call."""
    calls = []

    def call(*args, **kwargs):
        calls.append(1)
        if len(calls) <= len(errors):
            raise errors[len(calls) - 1]
        return real(*args, **kwargs)

    return call, calls


def _run(**changes):
    lead = {
        "url": GH,
        "company": "Acme",
        "title": "Senior Software Engineer",
        "posting_text": POSTING,
        "location": "Remote, US",
        "work_mode": "Remote",
        "agency": "",
        "posted": "2026-11-01",
    }
    return SimpleNamespace(
        **{"id": "run-1", "user_id": uuid.uuid4(), "profile_id": None, "leads": [lead], **changes}
    )


def test_recording_a_run_is_tried_once_more_after_a_deadlock(session_factory, monkeypatch):
    import app.scout.worker as worker

    failing, calls = _failing(worker.record_listing, [_deadlock()])
    monkeypatch.setattr(worker, "record_listing", failing)
    with session_factory() as db:
        record_run(db, _run())
        assert len(calls) == 2
        assert db.scalar(select(func.count()).select_from(CatalogJob)) == 1


def test_recording_a_run_gives_up_on_other_errors_and_a_second_deadlock(
    session_factory, monkeypatch
):
    import app.scout.worker as worker

    for errors, tries in (
        ([OperationalError("UPDATE", {}, Exception("server closed"))], 1),
        ([_deadlock(), _deadlock()], 2),
    ):
        failing, calls = _failing(worker.record_listing, errors)
        monkeypatch.setattr(worker, "record_listing", failing)
        with session_factory() as db:
            record_run(db, _run())
            assert len(calls) == tries
            assert db.scalar(select(func.count()).select_from(CatalogJob)) == 0


def test_the_recorder_tries_once_more_after_a_deadlock(session_factory, monkeypatch):
    import app.companies.catalog as catalog

    failing, calls = _failing(catalog.record_all, [_deadlock()])
    monkeypatch.setattr(catalog, "record_all", failing)
    raws = [RawListing(company="Acme", title="Platform Engineer", url=GH, description="Text.")]
    assert CatalogRecorder(session_factory).submit("greenhouse", raws).result(timeout=30) == 1
    assert len(calls) == 2


def test_a_finished_run_records_what_it_kept(session_factory):
    run = SimpleNamespace(
        id="run-1",
        user_id=uuid.uuid4(),
        profile_id=None,
        leads=[
            {
                "url": GH,
                "company": "Acme",
                "title": "Senior Software Engineer",
                "posting_text": POSTING,
                "location": "Remote, US",
                "work_mode": "Remote",
                "agency": "",
                "posted": "2026-11-01",
            }
        ],
    )
    # Scout reads only catalog jobs now, which are recorded as they're read, so a finished
    # run records just what it kept.
    with session_factory() as db:
        record_run(db, run)
        [job] = db.scalars(select(CatalogJob)).all()
        assert (job.title, job.work_mode) == ("Senior Software Engineer", "remote")
        assert db.get(BoardStat, "greenhouse").kept == 1


def test_the_boards_tab_shows_where_listings_came_from(client, other_client, session_factory):
    with session_factory() as db:
        record_listing(db, _listing())
        record_listing(db, _listing(source="remotive", url="https://remotive.com/jobs/1"))
        record_kept(db, GH)
        owner = db.scalar(select(User).where(User.email == "owner@example.com"))
        owner.role = Role.ADMIN
        db.commit()
    assert other_client.get("/api/admin/catalog").status_code == 404
    catalog = client.get("/api/admin/catalog").json()
    assert (catalog["companies"], catalog["agencies"], catalog["jobs"]) == (1, 0, 1)
    names = [
        (row["name"], row["listings_recorded"], row["kept"], row["companies"])
        for row in catalog["sources"]
    ]
    assert names == [("Greenhouse", 1, 1, 1), ("Remotive", 1, 1, 1)]
