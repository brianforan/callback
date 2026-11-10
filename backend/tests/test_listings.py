import json
import logging
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

from app.config import get_settings
from app.listings import boards as job_boards
from app.listings import search as listings_module
from app.listings.boards import (
    html_to_text,
    parse_arbeitnow,
    parse_findwork,
    parse_himalayas,
    parse_jobicy,
    parse_jobspy,
    parse_jooble,
    parse_larabench,
    parse_muse,
    parse_remoteok,
    parse_remotive,
)
from app.listings.search import ListingFilters, filter_listings, search_listings
from app.postings.parser import parse_posting

DESCRIPTION = (
    "<h3>Requirements</h3><ul><li><p>5+ years of experience with Python</p></li></ul>"
    "<h3>Nice to have</h3><ul><li>Terraform</li></ul>"
)
SECRET = "super-secret-key"


def _remotive_job(**overrides):
    job = {
        "url": "https://remotive.com/remote-jobs/software-dev/backend-1",
        "title": "Senior Backend Engineer",
        "company_name": "Acme",
        "job_type": "full_time",
        "publication_date": "2026-10-30T10:00:00",
        "candidate_required_location": "USA",
        "salary": "$120,000 - $150,000",
        "description": DESCRIPTION,
    }
    return {**job, **overrides}


def _settings(**overrides):
    blank = {
        "jooble_api_key": None,
        "findwork_api_key": None,
        "muse_api_key": None,
        "indeed_enabled": False,
        "linkedin_enabled": False,
    }
    return get_settings().model_copy(update={**blank, **overrides})


def _indeed_row(**overrides):
    """One job as JobSpy's DataFrame gives it, with NaN where pandas has no number or flag."""
    row = {
        "site": "indeed",
        "job_url": "https://www.indeed.com/viewjob?jk=abc123",
        "job_url_direct": None,
        "title": "Senior Backend Engineer",
        "company": "Acme",
        "location": "Austin, TX, USA",
        "date_posted": date(2026, 10, 30),
        "job_type": "fulltime, contract",
        "interval": "yearly",
        "min_amount": 120_000.0,
        "max_amount": 150_000.0,
        "currency": "USD",
        "is_remote": False,
        "description": DESCRIPTION,
    }
    return {**row, **overrides}


def _linkedin_row(**overrides):
    """A LinkedIn job as JobSpy gives it once it has read the job's own page."""
    row = _indeed_row(
        site="linkedin",
        job_url="https://www.linkedin.com/jobs/view/4012345678",
        interval=None,
        min_amount=float("nan"),
        max_amount=float("nan"),
        currency=None,
        job_type="fulltime",
    )
    return {**row, **overrides}


@pytest.fixture
def jobspy(monkeypatch):
    """Stand in for JobSpy. Each site answers with the rows and error set for it, or raises."""
    sites = {site: {"rows": [], "error": None, "raise": None} for site in ("indeed", "linkedin")}
    calls = []

    def scrape(site, keywords, location, **options):
        calls.append((site, keywords, location, options))
        answer = sites[site]
        if answer["raise"] is not None:
            raise answer["raise"]
        return answer["rows"], answer["error"]

    monkeypatch.setattr(job_boards, "_scrape_jobspy", scrape)
    return {**sites, "calls": calls}


def _empty_feed(request: httpx.Request) -> httpx.Response:
    host = request.url.host
    if host == "remoteok.com":
        return httpx.Response(200, json=[{"legal": "notice"}])
    if host == "www.arbeitnow.com":
        return httpx.Response(200, json={"data": []})
    if host == "www.themuse.com":
        return httpx.Response(200, json={"results": []})
    return httpx.Response(200, json={"jobs": []})


@pytest.fixture
def boards(monkeypatch):
    """Route every board request to a handler for a host, and everything else to an empty feed."""
    listings_module.clear_cache()
    hosts = {}
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        respond = hosts.get(request.url.host, _empty_feed)
        return respond(request)

    monkeypatch.setattr(
        listings_module,
        "new_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    hosts["calls"] = calls
    yield hosts
    listings_module.clear_cache()


def test_html_to_text_keeps_the_structure_the_parser_needs():
    text = html_to_text(
        "<h3>Requirements</h3><ul><li><p>5+ years of Python &amp; SQL</p></li></ul>"
        "<script>alert(1)</script><p>Line one<br>Line two</p>"
    )
    assert text == "Requirements\n\n- 5+ years of Python & SQL\n\nLine one\nLine two"


def test_html_to_text_decodes_entity_encoded_html():
    text = html_to_text(
        "&lt;h3&gt;Requirements&amp;nbsp;&lt;/h3&gt;&lt;ul&gt;&lt;li&gt;Python&lt;/li&gt;&lt;/ul&gt;"
    )
    assert text == "Requirements\n\n- Python"


def test_search_decodes_names_but_leaves_urls_alone(boards):
    boards["jobicy.com"] = lambda _: httpx.Response(
        200,
        json={
            "jobs": [
                {
                    "url": "https://jobicy.com/jobs/1?a=1&region=2",
                    "jobTitle": "Python R&amp;D Engineer",
                    "companyName": "hims &#038; hers",
                    "jobGeo": "USA",
                    "jobDescription": "<p>Build</p>",
                }
            ]
        },
    )
    [listing] = search_listings(_settings(), "python").listings
    assert listing.company == "hims & hers"
    assert listing.title == "Python R&D Engineer"
    assert listing.url == "https://jobicy.com/jobs/1?a=1&region=2"


def test_board_description_parses_into_requirements_and_salary():
    [listing] = parse_remotive({"jobs": [_remotive_job()]})
    parsed = parse_posting(listing.title, listing.description, "Remotive", listing.location)
    assert listing.location == "Remote (USA)"
    assert (parsed.salary_min, parsed.salary_max) == (120_000, 150_000)
    assert parsed.work_mode == "remote"
    assert parsed.employment_type == "full_time"
    assert parsed.source_kind == "job_board"
    assert parsed.years_required == 5
    requirements = {tech.name: tech.requirement.value for tech in parsed.technologies}
    assert requirements == {"Python": "required", "Terraform": "nice_to_have"}


def test_free_text_pay_that_is_not_annual_is_left_out():
    monthly, annual = parse_remotive(
        {
            "jobs": [
                _remotive_job(salary="$25k–$30k/month"),
                _remotive_job(salary="$120k - $150k"),
            ]
        }
    )
    assert "Salary" not in monthly.description
    assert "Salary: $120k - $150k" in annual.description


def test_muse_drops_old_jobs_but_keeps_undated_ones(boards):
    today = datetime.now(UTC).date()

    def job(name, published):
        return {
            "name": f"Python {name}",
            "contents": "<p>Build</p>",
            "publication_date": published,
            "locations": [{"name": "Boston, MA"}],
            "refs": {"landing_page": f"https://www.themuse.com/jobs/acme/{name}"},
            "company": {"name": f"Acme {name}"},
        }

    def muse(request):
        if request.url.params["page"] != "0":
            return httpx.Response(200, json={"results": []})
        return httpx.Response(
            200,
            json={
                "results": [
                    job("recent", (today - timedelta(days=5)).isoformat()),
                    job("old", (today - timedelta(days=400)).isoformat()),
                    job("undated", None),
                ]
            },
        )

    boards["www.themuse.com"] = muse
    results = search_listings(_settings(), "python")
    assert {listing.title for listing in results.listings} == {"Python recent", "Python undated"}


def test_remoteok_skips_the_legal_notice():
    payload = [
        {"legal": "API terms"},
        {
            "position": "Staff Engineer",
            "company": "Acme",
            "url": "https://remoteok.com/remote-jobs/1",
            "description": "<p>Build</p>",
            "location": "",
            "salary_min": 100000,
            "salary_max": 0,
            "date": "2026-10-30T10:00:00+00:00",
        },
    ]
    [listing] = parse_remoteok(payload)
    assert listing.location == "Remote"
    assert listing.description.startswith("Salary: $100,000\n")
    assert listing.published_on.isoformat() == "2026-10-30"


def test_arbeitnow_uses_the_unix_timestamp_and_remote_flag():
    payload = {
        "data": [
            {
                "company_name": "Acme",
                "title": "Developer",
                "url": "https://www.arbeitnow.com/jobs/1",
                "description": "<p>Build</p>",
                "remote": True,
                "location": "Berlin",
                "job_types": ["Full-Time"],
                "created_at": 1_790_000_000,
            }
        ]
    }
    [listing] = parse_arbeitnow(payload)
    assert listing.location == "Remote (Berlin)"
    assert listing.published_on is not None
    assert "Employment type: full-time" in listing.description


def test_jobicy_leaves_out_other_currencies():
    job = {
        "url": "https://jobicy.com/jobs/1",
        "jobTitle": "Engineer",
        "companyName": "Acme",
        "jobGeo": "Europe",
        "jobType": ["Full-Time"],
        "jobDescription": "<p>Build</p>",
        "pubDate": "2026-10-30 10:00:00",
        "salaryMin": 80000,
        "salaryMax": 90000,
        "salaryCurrency": "EUR",
        "salaryPeriod": "yearly",
    }
    [listing] = parse_jobicy({"jobs": [job]})
    assert "Salary" not in listing.description
    assert listing.published_on.isoformat() == "2026-10-30"


def test_himalayas_links_to_the_application_link():
    job = {
        "title": "Engineer",
        "companyName": "Acme",
        "applicationLink": "https://himalayas.app/apply/1",
        "guid": "https://himalayas.app/companies/acme/jobs/engineer",
        "locationRestrictions": ["United States", "Canada"],
        "description": "<p>Build</p>",
        "minSalary": 100000,
        "maxSalary": 120000,
        "currency": "USD",
        "salaryPeriod": "annual",
        "employmentType": "Full Time",
        "pubDate": 1_790_000_000,
    }
    [listing] = parse_himalayas({"jobs": [job]})
    assert listing.url == "https://himalayas.app/apply/1"
    assert listing.location == "Remote (United States, Canada)"
    assert "Salary: $100,000 - $120,000" in listing.description


def test_muse_reads_nested_fields():
    job = {
        "name": "Engineer",
        "contents": "<p>Build</p>",
        "publication_date": "2026-10-30T10:00:00Z",
        "locations": [{"name": "New York, NY"}, {"name": "Flexible / Remote"}],
        "refs": {"landing_page": "https://www.themuse.com/jobs/acme/engineer"},
        "company": {"name": "Acme"},
    }
    [listing] = parse_muse({"results": [job]})
    assert listing.company == "Acme"
    assert listing.location == "New York, NY, Flexible / Remote"


def test_larabench_links_to_the_original_board_and_keeps_line_breaks():
    job = {
        "title": "Senior Laravel Developer",
        "description": "Requirements\n5+ years of PHP\n\nNice to have\nVue",
        "detailUrl": "https://larabench.com/laravel-jobs/1/senior-laravel-developer",
        "employmentType": "full-time",
        "location": {"text": "Delft, Netherlands", "workArrangement": "hybrid"},
        "salary": {"minimum": 90000, "maximum": 110000, "currency": "USD", "period": "year"},
        "company": {"name": "Acme"},
        "postedAt": "2026-10-30T10:00:00+00:00",
        "sources": [
            {"listingUrl": "https://devitjobs.nl/jobs/acme", "applyUrl": "acme-slug"},
        ],
    }
    remote = {
        **job,
        "location": {"text": None, "workArrangement": "remote"},
        "salary": {"minimum": 80000, "maximum": 80000, "currency": "EUR", "period": "year"},
        "sources": [],
    }
    hybrid, anywhere = parse_larabench({"jobs": [job, remote]})
    assert hybrid.url == "https://devitjobs.nl/jobs/acme"
    assert hybrid.location == "Delft, Netherlands (Hybrid)"
    assert hybrid.published_on.isoformat() == "2026-10-30"
    assert "Salary: $90,000 - $110,000\nEmployment type: full-time" in hybrid.description
    assert "5+ years of PHP\n\nNice to have" in hybrid.description
    assert anywhere.url == "https://larabench.com/laravel-jobs/1/senior-laravel-developer"
    assert anywhere.location == "Remote"
    assert "Salary" not in anywhere.description


def test_jooble_and_findwork_map_their_fields():
    [jooble] = parse_jooble(
        {
            "jobs": [
                {
                    "title": "<b>Software</b> Engineer",
                    "company": "Acme",
                    "location": "Boston",
                    "snippet": "Build &nbsp;things",
                    "link": "https://jooble.org/desc/1",
                    "updated": "2026-10-30T10:00:00.000",
                }
            ]
        }
    )
    assert jooble.title == "Software Engineer"
    [findwork] = parse_findwork(
        {
            "results": [
                {
                    "role": "Engineer",
                    "company_name": "Acme",
                    "url": "https://findwork.dev/1",
                    "text": "<p>Build</p>",
                    "remote": True,
                    "location": None,
                    "date_posted": "2026-10-30T10:00:00Z",
                    "employment_type": "full time",
                }
            ]
        }
    )
    assert findwork.location == "Remote"


def test_search_flags_boards_that_need_setup_and_never_calls_them(boards):
    results = search_listings(_settings(), "python")
    by_id = {source.id: source for source in results.sources}
    for board in ("jooble", "findwork"):
        assert by_id[board].state == "needs_setup"
    assert by_id["jooble"].missing_settings == ("JOOBLE_API_KEY",)
    assert by_id["jooble"].setup_url
    assert by_id["remotive"].state == "ok"
    called = {request.url.host for request in boards["calls"]}
    assert "jooble.org" not in called
    assert "findwork.dev" not in called


def test_search_filters_ranks_and_merges_across_boards(boards):
    boards["remotive.com"] = lambda _: httpx.Response(
        200,
        json={
            "jobs": [
                _remotive_job(),
                _remotive_job(
                    url="https://remotive.com/2",
                    title="Sales Manager",
                    description="<p>Sell our Python product</p>",
                    publication_date="2026-10-31T10:00:00",
                ),
                _remotive_job(
                    url="https://remotive.com/3", title="Designer", description="<p>Draw</p>"
                ),
                _remotive_job(url="javascript:alert(1)", title="Python Hacker"),
            ]
        },
    )
    boards["jobicy.com"] = lambda _: httpx.Response(
        200,
        json={
            "jobs": [
                {
                    "url": "https://jobicy.com/jobs/1",
                    "jobTitle": "Senior Backend Engineer",
                    "companyName": "ACME, Inc.",
                    "jobGeo": "USA",
                    "jobDescription": "<p>Python</p>",
                    "pubDate": "2026-10-01",
                }
            ]
        },
    )
    results = search_listings(_settings(), "python")
    titles = [listing.title for listing in results.listings]
    # The newest match wins unless a title matches, and the same job on two boards appears once.
    assert titles == ["Sales Manager", "Senior Backend Engineer"]
    assert results.listings[1].source == "Remotive"
    assert len(results.listings) == 2

    backend = search_listings(_settings(), "backend python")
    assert [listing.title for listing in backend.listings] == ["Senior Backend Engineer"]


def test_search_applies_location_and_remote_filters_to_feed_boards(boards):
    boards["www.arbeitnow.com"] = lambda request: httpx.Response(
        200,
        json={
            "data": []
            if request.url.params["page"] != "1"
            else [
                {
                    "company_name": "Acme",
                    "title": "Python Developer",
                    "url": "https://www.arbeitnow.com/jobs/berlin",
                    "description": "<p>Build</p>",
                    "remote": False,
                    "location": "Berlin",
                },
                {
                    "company_name": "Beta",
                    "title": "Python Engineer",
                    "url": "https://www.arbeitnow.com/jobs/remote",
                    "description": "<p>Build</p>",
                    "remote": True,
                    "location": "Germany",
                },
            ]
        },
    )
    berlin = search_listings(_settings(), "python", location="berlin")
    assert [listing.company for listing in berlin.listings] == ["Acme"]
    everything = search_listings(_settings(), "python").listings
    filters = ListingFilters(work_modes=frozenset({"remote"}))
    remote = filter_listings(everything, filters, date.today())
    assert [listing.company for listing in remote] == ["Beta"]


@pytest.mark.parametrize("search", ["USA", "us", "United States", "u.s."])
def test_us_location_search_finds_every_spelling(boards, search):
    def job(name, location):
        return {
            "company_name": f"Acme {name}",
            "title": "Python Engineer",
            "url": f"https://www.arbeitnow.com/jobs/{name}",
            "description": "<p>Build</p>",
            "remote": False,
            "location": location,
        }

    boards["www.arbeitnow.com"] = lambda _: httpx.Response(
        200,
        json={
            "data": [
                job("a", "Remote - USA"),
                job("b", "United States, Canada"),
                job("c", "Austin, US"),
                job("d", "U.S."),
                job("e", "Russia"),
                job("f", "Austria"),
                job("g", "Austin, TX"),
                job("h", "Remote"),
                job("i", "Remote (Worldwide)"),
                job("j", "Remote (Germany)"),
                job("k", "Berlin, Germany"),
            ]
        },
    )
    results = search_listings(_settings(), "python", location=search)
    assert {listing.company for listing in results.listings} == {
        "Acme a",
        "Acme b",
        "Acme c",
        "Acme d",
        "Acme g",
        "Acme h",
        "Acme i",
    }


@pytest.mark.parametrize("search", ["USA", ""])
def test_jooble_searches_several_places_for_a_nationwide_search(boards, search):
    seen = []

    def jooble(request):
        place = json.loads(request.content)["location"]
        seen.append(place)
        if place == "Remote":
            return httpx.Response(500)
        return httpx.Response(
            200,
            json={
                "jobs": [
                    {
                        "title": "Python Engineer",
                        "company": f"Acme {place}",
                        "link": f"https://jooble.org/desc/{place.replace(' ', '')}",
                    }
                ]
            },
        )

    boards["jooble.org"] = jooble
    results = search_listings(_settings(jooble_api_key=SECRET), "python", location=search)
    source = next(source for source in results.sources if source.id == "jooble")
    assert seen == ["Remote", "New York", "San Francisco", "Chicago", "Austin"]
    assert source.state == "ok"
    assert source.listings == 4
    assert source.message == (
        "Jooble can't search a whole country, so this shows New York, San Francisco, Chicago, "
        "Austin."
    )


def test_jooble_sends_a_specific_place_as_given(boards):
    seen = []

    def jooble(request):
        seen.append(json.loads(request.content)["location"])
        return httpx.Response(200, json={"jobs": []})

    boards["jooble.org"] = jooble
    results = search_listings(_settings(jooble_api_key=SECRET), "python", location="Boston")
    source = next(source for source in results.sources if source.id == "jooble")
    assert seen == ["Boston"]
    assert source.message is None


def test_findwork_gets_a_city_but_not_the_whole_us(boards):
    seen = []

    def findwork(request):
        seen.append(request.url.params.get("location"))
        return httpx.Response(200, json={"results": []})

    boards["findwork.dev"] = findwork
    settings = _settings(findwork_api_key=SECRET)
    search_listings(settings, "python", location="USA")
    search_listings(settings, "python", location="Boston")
    assert seen == [None, "Boston"]


def test_feed_board_is_fetched_once_across_searches(boards):
    search_listings(_settings(), "python")
    search_listings(_settings(), "golang", location="Boston")
    hits = [request for request in boards["calls"] if request.url.host == "remotive.com"]
    assert len(hits) == 1


def test_searching_board_is_fetched_per_query(boards):
    boards["himalayas.app"] = lambda _: httpx.Response(200, json={"jobs": []})
    search_listings(_settings(), "python")
    search_listings(_settings(), "python")
    search_listings(_settings(), "golang")
    queries = {
        request.url.params["q"]
        for request in boards["calls"]
        if request.url.host == "himalayas.app"
    }
    hits = [request for request in boards["calls"] if request.url.host == "himalayas.app"]
    assert queries == {"python", "golang"}
    assert len(hits) == 2


def test_failed_board_is_marked_without_leaking_its_key(boards, caplog):
    boards["jooble.org"] = lambda _: httpx.Response(401, json={"error": "bad key"})
    boards["remotive.com"] = lambda _: httpx.Response(200, json={"jobs": [_remotive_job()]})
    with caplog.at_level(logging.DEBUG):
        results = search_listings(_settings(jooble_api_key=SECRET), "backend")
    by_id = {source.id: source for source in results.sources}
    assert by_id["jooble"].state == "failed"
    assert by_id["jooble"].message == "HTTP 401"
    assert by_id["remotive"].state == "ok"
    assert len(results.listings) == 1
    logged = [record.getMessage() for record in caplog.records if record.name.startswith("app.")]
    assert not any(SECRET in message for message in logged)
    assert logging.getLogger("httpx").level == logging.WARNING


def test_malformed_response_marks_the_board_failed(boards):
    boards["remotive.com"] = lambda _: httpx.Response(200, text="<html>not json</html>")
    results = search_listings(_settings(), "python")
    by_id = {source.id: source for source in results.sources}
    assert by_id["remotive"].state == "failed"
    assert by_id["remotive"].message == "Unexpected response"


def test_keyed_boards_send_their_credentials(boards):
    def findwork(request):
        assert request.headers["authorization"] == f"Token {SECRET}"
        return httpx.Response(200, json={"results": []})

    def jooble(request):
        assert request.url.path == f"/api/{SECRET}"
        return httpx.Response(200, json={"jobs": []})

    boards["findwork.dev"] = findwork
    boards["jooble.org"] = jooble
    results = search_listings(
        _settings(findwork_api_key=SECRET, jooble_api_key=SECRET),
        "python",
    )
    by_id = {source.id: source for source in results.sources}
    for board in ("findwork", "jooble"):
        assert by_id[board].state == "ok"


def test_indeed_rows_parse_into_requirements_salary_and_type():
    [listing] = parse_jobspy([_indeed_row()])
    parsed = parse_posting(listing.title, listing.description, "Indeed", listing.location)
    assert listing.url == "https://www.indeed.com/viewjob?jk=abc123"
    assert listing.location == "Austin, TX, USA"
    assert listing.published_on == date(2026, 10, 30)
    assert (parsed.salary_min, parsed.salary_max) == (120_000, 150_000)
    assert parsed.employment_type == "full_time"
    assert parsed.years_required == 5
    requirements = {tech.name: tech.requirement.value for tech in parsed.technologies}
    assert requirements == {"Python": "required", "Terraform": "nice_to_have"}


def test_indeed_reads_missing_cells_and_leaves_out_hourly_pay():
    nan = float("nan")
    remote, hourly = parse_jobspy(
        [
            _indeed_row(
                location=None,
                is_remote=True,
                min_amount=nan,
                max_amount=nan,
                currency=None,
                interval=None,
                job_type=None,
                date_posted=None,
                description=None,
            ),
            _indeed_row(interval="hourly", min_amount=50.0, max_amount=70.0, is_remote=nan),
            _indeed_row(company=None),
            _indeed_row(company=nan),
        ]
    )
    assert remote.location == "Remote"
    assert remote.published_on is None
    assert remote.description == ""
    assert "Salary" not in hourly.description
    assert "Employment type: full-time" in hourly.description
    assert hourly.location == "Austin, TX, USA"


def test_pay_jobspy_guessed_from_the_text_is_left_to_the_parser():
    [listing] = parse_jobspy(
        [
            _linkedin_row(
                salary_source="description",
                interval="yearly",
                min_amount=1_000.0,
                max_amount=2_000.0,
                currency="USD",
            )
        ]
    )
    assert "Salary" not in listing.description
    assert "Employment type: full-time" in listing.description


def test_scraped_boards_stay_off_until_turned_on(boards, jobspy):
    results = search_listings(_settings(), "python")
    by_id = {source.id: source for source in results.sources}
    for board, setting in (("indeed", "INDEED_ENABLED"), ("linkedin", "LINKEDIN_ENABLED")):
        assert by_id[board].state == "needs_setup"
        assert by_id[board].missing_settings == (setting,)
        assert by_id[board].setup_url is None
    assert jobspy["calls"] == []


def test_indeed_searches_the_whole_country_or_the_place_given(boards, jobspy):
    jobspy["indeed"]["rows"] = [_indeed_row()]
    settings = _settings(indeed_enabled=True)
    results = search_listings(settings, "python", "United States")
    by_id = {source.id: source for source in results.sources}
    assert by_id["indeed"].state == "ok"
    assert by_id["linkedin"].state == "needs_setup"
    assert [listing.source for listing in results.listings] == ["Indeed"]
    search_listings(settings, "python", "Austin, TX")
    searched = [(site, keywords, location) for site, keywords, location, _ in jobspy["calls"]]
    assert searched == [("indeed", "python", None), ("indeed", "python", "Austin, TX")]


def test_linkedin_searches_the_us_and_reads_each_job_page(boards, jobspy):
    jobspy["linkedin"]["rows"] = [_linkedin_row()]
    settings = _settings(linkedin_enabled=True)
    results = search_listings(settings, "python")
    by_id = {source.id: source for source in results.sources}
    assert by_id["linkedin"].state == "ok"
    assert by_id["linkedin"].message is None
    [listing] = results.listings
    assert listing.source == "LinkedIn"
    assert listing.url == "https://www.linkedin.com/jobs/view/4012345678"
    search_listings(settings, "python", "Austin, TX")
    [(_, _, nationwide, options), (_, _, city, _)] = jobspy["calls"]
    assert (nationwide, city) == ("United States", "Austin, TX")
    assert options["fetch_description"] is True


def test_linkedin_says_how_many_jobs_came_without_text(boards, jobspy):
    jobspy["linkedin"]["rows"] = [
        _linkedin_row(),
        _linkedin_row(
            job_url="https://www.linkedin.com/jobs/view/2",
            title="Python Platform Engineer",
            job_type=None,
            description=None,
        ),
    ]
    results = search_listings(_settings(linkedin_enabled=True), "python")
    by_id = {source.id: source for source in results.sources}
    assert by_id["linkedin"].state == "ok"
    assert by_id["linkedin"].message == (
        "LinkedIn didn't send the text of 1 of its jobs, so their requirements and pay are unknown."
    )
    assert len(results.listings) == 2


@pytest.mark.parametrize(
    "failure",
    [
        {"error": "bad response status code: 403"},
        {"raise": OSError("connection reset")},
    ],
)
def test_indeed_blocked_or_raising_marks_only_that_board_failed(boards, jobspy, failure):
    jobspy["indeed"].update(failure)
    boards["remotive.com"] = lambda _: httpx.Response(200, json={"jobs": [_remotive_job()]})
    results = search_listings(_settings(indeed_enabled=True), "backend")
    by_id = {source.id: source for source in results.sources}
    assert by_id["indeed"].state == "failed"
    assert by_id["indeed"].message == "Unexpected response"
    assert by_id["remotive"].state == "ok"
    assert len(results.listings) == 1


def test_indeed_stopping_partway_keeps_what_it_found(boards, jobspy):
    jobspy["indeed"]["rows"] = [_indeed_row()]
    jobspy["indeed"]["error"] = "bad response status code: 429"
    results = search_listings(_settings(indeed_enabled=True), "python")
    by_id = {source.id: source for source in results.sources}
    assert by_id["indeed"].state == "ok"
    assert by_id["indeed"].message == (
        "Indeed stopped partway, so this shows only some of its jobs."
    )
    assert len(results.listings) == 1


def test_a_deep_search_asks_the_scraped_boards_for_more_and_is_cached_apart(boards, jobspy):
    from app.listings.boards import (
        DEEP_MAX_AGE_DAYS,
        INDEED_DEEP_RESULTS,
        INDEED_RESULTS,
        JOBSPY_MAX_AGE_DAYS,
        LINKEDIN_DEEP_RESULTS,
    )

    jobspy["indeed"]["rows"] = [_indeed_row()]
    jobspy["linkedin"]["rows"] = [_linkedin_row()]
    settings = _settings(indeed_enabled=True, linkedin_enabled=True)
    search_listings(settings, "laravel")
    search_listings(settings, "laravel", deep=True, deadline_seconds=60)
    asked = {
        (site, options["results_wanted"], options["max_age_days"])
        for site, _, _, options in jobspy["calls"]
    }
    # The plain search's cached results don't stand in for the deep one.
    assert ("indeed", INDEED_RESULTS, JOBSPY_MAX_AGE_DAYS) in asked
    assert ("indeed", INDEED_DEEP_RESULTS, DEEP_MAX_AGE_DAYS) in asked
    assert ("linkedin", LINKEDIN_DEEP_RESULTS, DEEP_MAX_AGE_DAYS) in asked
    assert len(jobspy["calls"]) == 4
    # Another deep search is answered from the cache.
    search_listings(settings, "laravel", deep=True)
    assert len(jobspy["calls"]) == 4


def test_listings_route_requires_login(session_factory):
    from fastapi.testclient import TestClient

    from app.main import app

    assert TestClient(app).get("/api/listings", params={"q": "python"}).status_code == 401


def test_listings_route_rejects_a_blank_search(client, boards):
    assert client.get("/api/listings", params={"q": "   "}).status_code == 422


def test_tracking_a_listing_marks_it_for_that_user_only(client, other_client, boards):
    boards["remotive.com"] = lambda _: httpx.Response(200, json={"jobs": [_remotive_job()]})
    found = client.get("/api/listings", params={"q": "backend"})
    assert found.status_code == 200
    [listing] = found.json()["listings"]
    assert listing["posting_id"] is None
    assert listing["level"] == "senior"
    assert listing["technologies"]

    posting = client.post(
        "/api/postings",
        json={
            "company": listing["company"],
            "title": listing["title"],
            "url": listing["url"],
            "location": listing["location"],
            "source": listing["source"],
            "raw_text": listing["description"],
        },
    )
    assert posting.status_code == 201
    assert posting.json()["source_kind"] == "job_board"
    assert posting.json()["salary_min"] == 120_000

    [tracked] = client.get("/api/listings", params={"q": "backend"}).json()["listings"]
    assert tracked["posting_id"] == posting.json()["id"]
    [untracked] = other_client.get("/api/listings", params={"q": "backend"}).json()["listings"]
    assert untracked["posting_id"] is None


def _hide_jobs():
    base = "https://remotive.com/remote-jobs/software-dev"
    return [
        _remotive_job(),
        _remotive_job(url=f"{base}/backend-2", title="Backend Engineer"),
        _remotive_job(url=f"{base}/backend-3", company_name="Globex"),
    ]


def _search(client, **params):
    return client.get("/api/listings", params={"q": "backend", **params}).json()


def test_hiding_a_job_or_a_company_removes_it_for_that_user_only(client, other_client, boards):
    boards["remotive.com"] = lambda _: httpx.Response(200, json={"jobs": _hide_jobs()})
    job = client.post(
        "/api/listings/hidden",
        json={
            "kind": "listing",
            "company": "Globex",
            "title": "Senior Backend Engineer",
            "url": "https://remotive.com/remote-jobs/software-dev/backend-3/",
        },
    )
    assert job.status_code == 201
    assert job.json()["url"] == "https://remotive.com/remote-jobs/software-dev/backend-3"
    results = _search(client)
    assert results["hidden_total"] == 1
    assert {row["company"] for row in results["listings"]} == {"Acme"}

    company = client.post("/api/listings/hidden", json={"kind": "company", "company": "ACME, Inc."})
    assert company.json()["title"] is None
    assert _search(client)["listings"] == []
    assert _search(client)["hidden_total"] == 3

    shown = _search(client, show_hidden="true")
    assert {(row["company"], row["hidden"]["kind"]) for row in shown["listings"]} == {
        ("Acme", "company"),
        ("Globex", "listing"),
    }
    assert all(row["hidden"] is None for row in _search(other_client)["listings"])
    assert len(_search(other_client)["listings"]) == 3

    hidden = client.get("/api/listings/hidden").json()
    assert [row["kind"] for row in hidden] == ["company", "listing"]
    assert other_client.get("/api/listings/hidden").json() == []

    # Another user's rule can't be removed, and removing your own shows the jobs again.
    other_client.delete(f"/api/listings/hidden/{company.json()['id']}")
    assert len(client.get("/api/listings/hidden").json()) == 2
    assert client.delete(f"/api/listings/hidden/{company.json()['id']}").status_code == 204
    assert len(_search(client)["listings"]) == 2


def test_a_hidden_job_stays_hidden_on_another_board_and_hiding_twice_is_one_rule(client, boards):
    boards["remotive.com"] = lambda _: httpx.Response(200, json={"jobs": [_remotive_job()]})
    payload = {
        "kind": "listing",
        "company": "Acme Inc",
        "title": "senior backend engineer",
        "url": "https://elsewhere.example/jobs/1",
    }
    first = client.post("/api/listings/hidden", json=payload).json()
    second = client.post("/api/listings/hidden", json=payload).json()
    assert first["id"] == second["id"]
    [shown] = _search(client, show_hidden="true")["listings"]
    assert shown["hidden"] == {"id": first["id"], "kind": "listing"}


def test_hiding_one_job_needs_its_url_and_title(client):
    response = client.post("/api/listings/hidden", json={"kind": "listing", "company": "Acme"})
    assert response.status_code == 422
    unsafe = {"kind": "listing", "company": "A", "title": "B", "url": "javascript:alert(1)"}
    assert client.post("/api/listings/hidden", json=unsafe).status_code == 422


# --- Results kept between searches ------------------------------------------------------------


class MemoryStore:
    """A store that keeps results in a dict, standing in for the database."""

    def __init__(self):
        self.rows = {}

    def load(self, key):
        return self.rows.get(key)

    def save(self, key, results):
        self.rows[key] = results


def _settle():
    """Wait for background refreshes to finish."""
    with listings_module._lock:
        running = list(listings_module._in_flight.values())
    for future in running:
        future.result(timeout=10)


def _remotive_with(titles):
    def respond(_request):
        jobs = [
            _remotive_job(url=f"https://remotive.com/remote-jobs/{index}", title=title)
            for index, title in enumerate(titles)
        ]
        return httpx.Response(200, json={"jobs": jobs})

    return respond


def _remotive_titles(results):
    return [listing.title for listing in results.listings if listing.source == "Remotive"]


def test_saved_results_survive_a_restart(boards):
    store = MemoryStore()
    boards["remotive.com"] = _remotive_with(["Python Engineer"])
    search_listings(_settings(), "python", store=store)
    calls = len(boards["calls"])

    listings_module.clear_cache()
    boards["remotive.com"] = _remotive_with(["Python Engineer v2"])
    results = search_listings(_settings(), "python", store=store)

    assert _remotive_titles(results) == ["Python Engineer"]
    assert results.refreshing is False
    assert len(boards["calls"]) == calls


def test_older_results_show_at_once_while_newer_are_fetched(boards):
    store = MemoryStore()
    boards["remotive.com"] = _remotive_with(["Python Engineer"])
    search_listings(_settings(listings_cache_minutes=0), "python", store=store)
    boards["remotive.com"] = _remotive_with(["Python Engineer", "Python Lead"])

    stale = search_listings(_settings(), "python", store=store)
    _settle()
    fresh = search_listings(_settings(), "python", store=store)

    assert _remotive_titles(stale) == ["Python Engineer"]
    assert stale.refreshing is True
    remotive = next(source for source in stale.sources if source.id == "remotive")
    assert remotive.refreshing is True
    assert sorted(_remotive_titles(fresh)) == ["Python Engineer", "Python Lead"]
    assert fresh.refreshing is False


def test_a_failed_refresh_keeps_the_older_results(boards):
    store = MemoryStore()
    boards["remotive.com"] = _remotive_with(["Python Engineer"])
    search_listings(_settings(listings_cache_minutes=0), "python", store=store)
    boards["remotive.com"] = lambda _: httpx.Response(503)

    search_listings(_settings(), "python", store=store)
    _settle()
    results = search_listings(_settings(), "python", store=store)

    assert _remotive_titles(results) == ["Python Engineer"]
    remotive = next(source for source in results.sources if source.id == "remotive")
    assert remotive.state == "ok"
    assert remotive.message.startswith("Couldn't refresh")


def test_results_past_the_keep_limit_are_not_shown(boards, monkeypatch):
    monkeypatch.setattr(listings_module, "KEEP_RESULTS", timedelta(0))
    store = MemoryStore()
    boards["remotive.com"] = _remotive_with(["Python Engineer"])
    search_listings(_settings(listings_cache_minutes=0), "python", store=store)
    boards["remotive.com"] = _remotive_with(["Python Lead"])

    results = search_listings(_settings(), "python", store=store)

    assert _remotive_titles(results) == ["Python Lead"]
    assert results.refreshing is False


def test_the_database_store_round_trips(session_factory):
    from app.listings.boards import RawListing
    from app.listings.cache import SqlListingStore, StoredResults, cache_key
    from app.models import utcnow

    store = SqlListingStore(session_factory, timedelta(hours=48))
    key = cache_key("remotive", "", "")
    now = utcnow()
    listing = RawListing(
        company="Acme",
        title="Python Engineer",
        url="https://example.com/1",
        description="Build things",
        location="USA",
        published_on=date(2026, 11, 1),
    )
    store.save(key, StoredResults("remotive", [listing], now, now + timedelta(hours=6)))

    loaded = store.load(key)

    assert loaded.listings == [listing]
    assert loaded.fresh_until > loaded.fetched_at
    assert store.load(cache_key("remotive", "go", "")) is None
    old = SqlListingStore(session_factory, timedelta(hours=0))
    assert old.load(key) is None


def test_scraped_boards_get_their_own_threads():
    """A slow LinkedIn scrape per search mustn't hold up the feed boards' refreshes."""
    scraped = [board.id for board in job_boards.BOARDS if board.scrapes]
    assert scraped == ["indeed", "linkedin"]
    linkedin = next(board for board in job_boards.BOARDS if board.id == "linkedin")
    remotive = next(board for board in job_boards.BOARDS if board.id == "remotive")
    assert listings_module.executor_for(linkedin) is not listings_module.executor_for(remotive)
    assert listings_module.executor_for(linkedin)._max_workers == 2
