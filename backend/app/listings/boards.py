"""Connectors for public job board APIs. Each turns one board's JSON into plain listings."""

import html
import logging
import math
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote

import httpx

from app.config import Settings

USER_AGENT = "Callback job tracker (self-hosted)"
REQUEST_TIMEOUT_SECONDS = 10.0
PAGES_PER_FEED = 3
PAGE_SIZE = 100
# The Muse doesn't sort by date and keeps jobs for years, so old ones are likely closed.
MUSE_PAGES = 5
MUSE_MAX_AGE_DAYS = 90
# LaraBench's API refuses more than 50 a page. Its search matches an exact phrase, so "senior
# vue" finds nothing, and the whole feed (about 400 jobs) is fetched and filtered here instead.
LARABENCH_PAGE_SIZE = 50
LARABENCH_PAGES = 10
# Jooble answers a blank or nationwide location with HTTP 500, and fails at times on "Remote".
JOOBLE_NATIONWIDE_PLACES = ("Remote", "New York", "San Francisco", "Chicago", "Austin")
US_NAMES = {"us", "u.s.", "usa", "u.s.a.", "united states", "united states of america"}
# Indeed and LinkedIn have no public API, so JobSpy reads the ones their own pages use.
JOBSPY_MAX_AGE_DAYS = 14
# Indeed sends 100 jobs, with their text, in one request.
INDEED_RESULTS = 100
# LinkedIn sends 10 a page, 3 to 7 seconds apart, and each job's text takes one more request.
LINKEDIN_RESULTS = 25
# A deep search, for a Scout run's stack, goes further back and further down the results.
# It runs in the background worker, where taking a few minutes is fine, so only the scraped
# boards, whose answers are cut short, search deeper. LinkedIn stays modest, since it turns
# away an address that asks for too many pages.
DEEP_MAX_AGE_DAYS = 30
INDEED_DEEP_RESULTS = 300
LINKEDIN_DEEP_RESULTS = 75
# JobSpy writes job types as one word, and the posting parser looks for "full-time".
_JOBSPY_JOB_TYPES = {"fulltime": "full_time", "parttime": "part_time", "perdiem": "per_diem"}

logger = logging.getLogger(__name__)

# An httpx error names the URL it failed on, and some boards take their key in the URL.
FETCH_ERRORS = (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError)

_BLOCK_TAGS = {
    "div",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "ol",
    "p",
    "section",
    "table",
    "tr",
    "ul",
}
_SKIPPED_TAGS = {"script", "style"}
_BULLET = "\n- "
_WHITESPACE = re.compile(r"[ \t\r\n\f\v\xa0]+")
_BLANK_LINES = re.compile(r"\n{3,}")
_CAMEL_CASE = re.compile(r"(?<=[a-z])(?=[A-Z])")
_MILLISECOND_EPOCH = 100_000_000_000
_NOT_ANNUAL = re.compile(r"month|week|hour|\bday\b|daily|/\s*(?:mo|wk|hr|h)\b|\btask\b", re.I)


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skipping = 0

    def _break_line(self) -> None:
        # A paragraph inside a list item stays on the bullet's line.
        if not self.parts or self.parts[-1] != _BULLET:
            self.parts.append("\n")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIPPED_TAGS:
            self._skipping += 1
        elif tag == "li":
            self.parts.append(_BULLET)
        elif tag in _BLOCK_TAGS or tag == "br":
            self._break_line()

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIPPED_TAGS:
            self._skipping = max(0, self._skipping - 1)
        elif tag in _BLOCK_TAGS or tag == "li":
            self._break_line()

    def handle_data(self, data: str) -> None:
        if self._skipping:
            return
        text = _WHITESPACE.sub(" ", data)
        if text.strip() or (self.parts and self.parts[-1] != _BULLET):
            self.parts.append(text)


def html_to_text(markup: str | None) -> str:
    """Keep the line breaks, because the posting parser reads requirements from its headings."""
    if not markup:
        return ""
    # Some Arbeitnow listings entity-encode their HTML, which would show as tags.
    if "&lt;" in markup:
        markup = html.unescape(markup)
    extractor = _TextExtractor()
    extractor.feed(markup)
    extractor.close()
    lines = [line.strip() for line in "".join(extractor.parts).split("\n")]
    return _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()


@dataclass(frozen=True)
class BoardQuery:
    keywords: str
    location: str
    # Further back and further down the results, on boards that search deeper.
    deep: bool = False


@dataclass(frozen=True)
class RawListing:
    company: str
    title: str
    url: str
    description: str
    location: str | None = None
    published_on: date | None = None
    # The employer's own posting, when the board links to it as well as its own page.
    direct_url: str | None = None


@dataclass(frozen=True)
class BoardFetch:
    listings: list[RawListing]
    warning: str | None = None


FetchFunction = Callable[[httpx.Client, Settings, BoardQuery], BoardFetch]


@dataclass(frozen=True)
class Board:
    id: str
    name: str
    site: str
    fetch: FetchFunction
    required_settings: tuple[str, ...] = ()
    setup_url: str | None = None
    # A board that searches for us is trusted, since it may return only a snippet to check.
    searches_keywords: bool = False
    searches_location: bool = False
    # Read by scraping the site's own pages through JobSpy, which can take half a minute.
    scrapes: bool = False
    # Fetches more for a deep search, so a deep search's results are cached apart.
    deepens: bool = False


def missing_settings(board: Board, settings: Settings) -> list[str]:
    return [name.upper() for name in board.required_settings if not getattr(settings, name, None)]


def describe_error(error: Exception) -> str:
    """Name a failure without quoting it, because its text can hold a URL with a key in it."""
    if isinstance(error, httpx.HTTPStatusError):
        return f"HTTP {error.response.status_code}"
    if isinstance(error, httpx.TimeoutException):
        return "Timed out"
    if isinstance(error, httpx.HTTPError):
        return "Couldn't connect"
    return "Unexpected response"


def _get_json(
    client: httpx.Client,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    keyed: bool = False,
) -> Any:
    # A redirect would forward custom key headers to whichever host it names.
    response = client.get(url, params=params, headers=headers, follow_redirects=not keyed)
    response.raise_for_status()
    return response.json()


def _clean(value: Any) -> str:
    return " ".join(str(value).split()) if value is not None else ""


def _names(values: Any) -> list[str]:
    """Flatten a list of strings or of {"name": ...} objects, as boards use both."""
    if not isinstance(values, list):
        return []
    names = [value.get("name") if isinstance(value, dict) else value for value in values]
    return [_clean(name) for name in names if name]


def _section(payload: Any, key: str) -> dict[str, Any]:
    value = payload.get(key)
    return value if isinstance(value, dict) else {}


def _published(value: Any) -> date | None:
    """Read an ISO date or a Unix timestamp in seconds or milliseconds. Anything else is unknown."""
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        if isinstance(value, int | float):
            seconds = value / 1000 if value > _MILLISECOND_EPOCH else value
            return datetime.fromtimestamp(seconds, UTC).date()
        return date.fromisoformat(str(value)[:10])
    except (ValueError, OverflowError, OSError):
        return None


def _amount(value: Any) -> int | None:
    try:
        amount = round(float(value))
    except (TypeError, ValueError, OverflowError):
        return None
    return amount if amount > 0 else None


def _salary_line(low: Any, high: Any, currency: Any = "USD", period: Any = None) -> str | None:
    """Write a board's salary the way a posting would, so the parser reads it like any other.

    Trends compare annual US dollars, so other currencies and pay periods are left out.
    """
    if str(currency or "").upper() != "USD":
        return None
    interval = str(period or "").lower()
    if interval and "year" not in interval and "annual" not in interval and interval != "pa":
        return None
    minimum, maximum = _amount(low), _amount(high)
    if minimum and maximum and minimum != maximum:
        return f"Salary: ${minimum:,} - ${maximum:,}"
    single = minimum or maximum
    return f"Salary: ${single:,}" if single else None


def _stated_salary_line(value: Any) -> str | None:
    """Pass on a salary a board gives as free text, unless it is plainly not an annual figure."""
    text = _clean(value)
    return f"Salary: {text}" if text and not _NOT_ANNUAL.search(text) else None


def _employment_line(value: Any) -> str | None:
    if isinstance(value, list):
        value = value[0] if value else None
    if not value:
        return None
    kind = _CAMEL_CASE.sub("-", str(value)).lower().replace("_", "-")
    if "contract" in kind or "freelance" in kind:
        kind = "contract role"
    elif "intern" in kind:
        kind = "internship"
    return f"Employment type: {kind}"


def _describe(body: str, *facts: str | None) -> str:
    """Put a board's structured facts above the text, so they survive into a tracked posting."""
    lines = [fact for fact in facts if fact]
    return "\n".join(lines) + "\n\n" + body if lines else body


def _remote_location(region: Any) -> str:
    """Spell out "remote" in the location, since remote-only boards often name just a region."""
    text = _clean(region)
    if not text:
        return "Remote"
    return text if "remote" in text.lower() else f"Remote ({text})"


def _listings(candidates: Iterable[RawListing]) -> list[RawListing]:
    return [
        candidate
        for candidate in candidates
        if candidate.company and candidate.title and candidate.url
    ]


def parse_remotive(payload: dict[str, Any]) -> list[RawListing]:
    return _listings(
        RawListing(
            company=_clean(job.get("company_name")),
            title=_clean(job.get("title")),
            url=_clean(job.get("url")),
            location=_remote_location(job.get("candidate_required_location")),
            published_on=_published(job.get("publication_date")),
            description=_describe(
                html_to_text(job.get("description")),
                _stated_salary_line(job.get("salary")),
                _employment_line(job.get("job_type")),
            ),
        )
        for job in payload["jobs"]
    )


def parse_remoteok(payload: list[dict[str, Any]]) -> list[RawListing]:
    # The first element is the board's legal notice rather than a job.
    return _listings(
        RawListing(
            company=_clean(job.get("company")),
            title=_clean(job.get("position")),
            url=_clean(job.get("url")),
            location=_remote_location(job.get("location")),
            published_on=_published(job.get("date") or job.get("epoch")),
            description=_describe(
                html_to_text(job.get("description")),
                _salary_line(job.get("salary_min"), job.get("salary_max")),
            ),
        )
        for job in payload
        if job.get("position")
    )


def parse_arbeitnow(payload: dict[str, Any]) -> list[RawListing]:
    return _listings(
        RawListing(
            company=_clean(job.get("company_name")),
            title=_clean(job.get("title")),
            url=_clean(job.get("url")),
            location=(
                _remote_location(job.get("location"))
                if job.get("remote")
                else _clean(job.get("location")) or None
            ),
            published_on=_published(job.get("created_at")),
            description=_describe(
                html_to_text(job.get("description")),
                _employment_line(job.get("job_types")),
            ),
        )
        for job in payload["data"]
    )


def parse_jobicy(payload: dict[str, Any]) -> list[RawListing]:
    return _listings(
        RawListing(
            company=_clean(job.get("companyName")),
            title=_clean(job.get("jobTitle")),
            url=_clean(job.get("url")),
            location=_remote_location(job.get("jobGeo")),
            published_on=_published(job.get("pubDate")),
            description=_describe(
                html_to_text(job.get("jobDescription") or job.get("jobExcerpt")),
                _salary_line(
                    job.get("salaryMin"),
                    job.get("salaryMax"),
                    job.get("salaryCurrency"),
                    job.get("salaryPeriod"),
                ),
                _employment_line(job.get("jobType")),
            ),
        )
        for job in payload["jobs"]
    )


def parse_himalayas(payload: dict[str, Any]) -> list[RawListing]:
    return _listings(
        RawListing(
            company=_clean(job.get("companyName")),
            title=_clean(job.get("title")),
            # Himalayas' terms require every apply action to use this link.
            url=_clean(job.get("applicationLink")),
            location=_remote_location(", ".join(_names(job.get("locationRestrictions")))),
            published_on=_published(job.get("pubDate")),
            description=_describe(
                html_to_text(job.get("description") or job.get("excerpt")),
                _salary_line(
                    job.get("minSalary"),
                    job.get("maxSalary"),
                    job.get("currency"),
                    job.get("salaryPeriod"),
                ),
                _employment_line(job.get("employmentType")),
            ),
        )
        for job in payload["jobs"]
    )


def parse_muse(payload: dict[str, Any]) -> list[RawListing]:
    return _listings(
        RawListing(
            company=_clean(_section(job, "company").get("name")),
            title=_clean(job.get("name")),
            url=_clean(_section(job, "refs").get("landing_page")),
            location=", ".join(_names(job.get("locations"))) or None,
            published_on=_published(job.get("publication_date")),
            description=html_to_text(job.get("contents")),
        )
        for job in payload["results"]
    )


def parse_jooble(payload: dict[str, Any]) -> list[RawListing]:
    return _listings(
        RawListing(
            company=_clean(job.get("company")),
            title=_clean(html_to_text(job.get("title"))),
            url=_clean(job.get("link")),
            location=_clean(job.get("location")) or None,
            published_on=_published(job.get("updated")),
            description=_describe(
                html_to_text(job.get("snippet")),
                _stated_salary_line(job.get("salary")),
                _employment_line(job.get("type")),
            ),
        )
        for job in payload["jobs"]
    )


def parse_findwork(payload: dict[str, Any]) -> list[RawListing]:
    return _listings(
        RawListing(
            company=_clean(job.get("company_name")),
            title=_clean(job.get("role")),
            url=_clean(job.get("url")),
            location=(
                _remote_location(job.get("location"))
                if job.get("remote")
                else _clean(job.get("location")) or None
            ),
            published_on=_published(job.get("date_posted")),
            description=_describe(
                html_to_text(job.get("text")),
                _employment_line(job.get("employment_type")),
            ),
        )
        for job in payload["results"]
    )


def _larabench_location(location: dict[str, Any]) -> str | None:
    """Add LaraBench's work arrangement, which the posting parser can't find in a place name."""
    text = _clean(location.get("text"))
    arrangement = _clean(location.get("workArrangement")).lower()
    if arrangement == "remote":
        return _remote_location(text)
    label = {"hybrid": "Hybrid", "onsite": "On-site"}.get(arrangement)
    if text and label and label.lower() not in text.lower():
        return f"{text} ({label})"
    return text or label


def parse_larabench(payload: dict[str, Any]) -> list[RawListing]:
    listings: list[RawListing] = []
    for job in payload["jobs"]:
        sources = job.get("sources")
        origin = sources[0] if isinstance(sources, list) and sources else {}
        salary = _section(job, "salary")
        listings.append(
            RawListing(
                company=_clean(_section(job, "company").get("name")),
                title=_clean(job.get("title")),
                # The original board's page, since an apply URL is at times only a slug.
                url=_clean(origin.get("listingUrl") or job.get("detailUrl")),
                location=_larabench_location(_section(job, "location")),
                published_on=_published(job.get("postedAt") or job.get("firstSeenAt")),
                # The text is already plain, with line breaks that html_to_text would flatten.
                description=_describe(
                    str(job.get("description") or "").strip(),
                    _salary_line(
                        salary.get("minimum"),
                        salary.get("maximum"),
                        salary.get("currency"),
                        salary.get("period"),
                    ),
                    _employment_line(job.get("employmentType")),
                ),
            )
        )
    return _listings(listings)


def _present(value: Any) -> Any:
    """Read a JobSpy cell, where pandas fills a missing number or flag with NaN."""
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def _jobspy_job_type(value: Any) -> str | None:
    """Take the first of JobSpy's job types, which it joins as "fulltime, contract"."""
    if isinstance(value, list):
        value = value[0] if value else None
    first = _clean(value).split(",")[0].strip().lower()
    return _JOBSPY_JOB_TYPES.get(first, first) or None


def parse_jobspy(rows: list[dict[str, Any]]) -> list[RawListing]:
    """Read JobSpy's rows, one dict per job, as pandas' to_dict("records") gives them."""
    listings: list[RawListing] = []
    for row in rows:
        job = {key: _present(value) for key, value in row.items()}
        location = _clean(job.get("location")) or None
        # JobSpy guesses LinkedIn's pay from the text when the page has none. The posting parser
        # reads that text itself, so only pay the board states is passed on.
        stated_pay = job.get("salary_source") != "description"
        listings.append(
            RawListing(
                company=_clean(job.get("company")),
                title=_clean(job.get("title")),
                # The board's own page, since the employer's link is missing for jobs applied to
                # there.
                url=_clean(job.get("job_url")),
                direct_url=_clean(job.get("job_url_direct")) or None,
                location=_remote_location(location) if job.get("is_remote") else location,
                published_on=_published(job.get("date_posted")),
                description=_describe(
                    html_to_text(job.get("description")),
                    _salary_line(
                        job.get("min_amount"),
                        job.get("max_amount"),
                        job.get("currency"),
                        job.get("interval"),
                    )
                    if stated_pay
                    else None,
                    _employment_line(_jobspy_job_type(job.get("job_type"))),
                ),
            )
        )
    return _listings(listings)


def _scrape_jobspy(
    site: str, keywords: str, location: str | None, max_age_days: int, **options: Any
) -> tuple[list[dict[str, Any]], str | None]:
    """One site's jobs as rows, and the error JobSpy's scraper stopped on, if any.

    JobSpy is imported here, so the API loads it and pandas only once a board using it is on.
    """
    from jobspy import scrape_jobs

    frame = scrape_jobs(
        site_name=site,
        search_term=keywords or None,
        location=location,
        hours_old=max_age_days * 24,
        # html_to_text keeps the headings the posting parser reads requirements from.
        description_format="html",
        verbose=0,
        **options,
    )
    errors = frame.attrs.get("errors") or {}
    return frame.to_dict("records"), errors.get(site)


def _fetch_jobspy(
    site: str, name: str, keywords: str, location: str | None, deep: bool, **options: Any
) -> BoardFetch:
    # JobSpy makes its own requests, so the shared client isn't used.
    max_age_days = DEEP_MAX_AGE_DAYS if deep else JOBSPY_MAX_AGE_DAYS
    try:
        rows, error = _scrape_jobspy(site, keywords, location, max_age_days=max_age_days, **options)
    except Exception as failure:
        # JobSpy can raise from requests, curl_cffi, pydantic or pandas. Any of them marks the
        # board failed, which waits a few minutes before asking the site again.
        logger.warning("%s search raised %s", name, type(failure).__name__)
        raise ValueError(f"{name} search failed") from failure
    listings = parse_jobspy(rows)
    if error and not listings:
        # JobSpy has already logged the reason, such as a 403 when the site blocks this address.
        raise ValueError(f"{name} search failed")
    warning = f"{name} stopped partway, so this shows only some of its jobs." if error else None
    return BoardFetch(listings, warning)


def _fetch_indeed(_: httpx.Client, __: Settings, query: BoardQuery) -> BoardFetch:
    # Indeed searches the whole country when no place is given.
    nationwide = not query.location or query.location.lower() in US_NAMES
    return _fetch_jobspy(
        "indeed",
        "Indeed",
        query.keywords,
        None if nationwide else query.location,
        query.deep,
        results_wanted=INDEED_DEEP_RESULTS if query.deep else INDEED_RESULTS,
        country_indeed="usa",
    )


def _fetch_linkedin(_: httpx.Client, __: Settings, query: BoardQuery) -> BoardFetch:
    # LinkedIn searches the whole world when no place is given.
    nationwide = not query.location or query.location.lower() in US_NAMES
    fetched = _fetch_jobspy(
        "linkedin",
        "LinkedIn",
        query.keywords,
        "United States" if nationwide else query.location,
        query.deep,
        results_wanted=LINKEDIN_DEEP_RESULTS if query.deep else LINKEDIN_RESULTS,
        fetch_description=True,
    )
    # JobSpy only logs a job page it couldn't read, which leaves that job with no text.
    untexted = sum(not listing.description for listing in fetched.listings)
    if fetched.warning or not untexted:
        return fetched
    return BoardFetch(
        fetched.listings,
        f"LinkedIn didn't send the text of {untexted} of its jobs, so their requirements "
        "and pay are unknown.",
    )


def _fetch_remotive(client: httpx.Client, _: Settings, __: BoardQuery) -> BoardFetch:
    payload = _get_json(
        client, "https://remotive.com/api/remote-jobs", params={"category": "software-dev"}
    )
    return BoardFetch(parse_remotive(payload))


def _fetch_remoteok(client: httpx.Client, _: Settings, __: BoardQuery) -> BoardFetch:
    return BoardFetch(parse_remoteok(_get_json(client, "https://remoteok.com/api")))


def _fetch_arbeitnow(client: httpx.Client, _: Settings, __: BoardQuery) -> BoardFetch:
    # One page already holds the newest 300 jobs, and this board covers every line of work.
    payload = _get_json(client, "https://www.arbeitnow.com/api/job-board-api", params={"page": 1})
    return BoardFetch(parse_arbeitnow(payload))


def _fetch_jobicy(client: httpx.Client, _: Settings, __: BoardQuery) -> BoardFetch:
    payload = _get_json(
        client,
        "https://jobicy.com/api/jobs",
        params={"count": PAGE_SIZE, "industry": "engineering"},
    )
    return BoardFetch(parse_jobicy(payload))


def _fetch_himalayas(client: httpx.Client, _: Settings, query: BoardQuery) -> BoardFetch:
    listings: list[RawListing] = []
    for page in range(1, PAGES_PER_FEED + 1):
        payload = _get_json(
            client,
            "https://himalayas.app/jobs/api/search",
            params={"q": query.keywords, "page": page},
        )
        found = parse_himalayas(payload)
        if not found:
            break
        listings.extend(found)
    return BoardFetch(listings)


def _fetch_muse(client: httpx.Client, settings: Settings, _: BoardQuery) -> BoardFetch:
    oldest = datetime.now(UTC).date() - timedelta(days=MUSE_MAX_AGE_DAYS)
    listings: list[RawListing] = []
    for page in range(MUSE_PAGES):
        params: dict[str, Any] = {
            "category": "Software Engineering",
            "descending": "true",
            "page": page,
        }
        if settings.muse_api_key:
            params["api_key"] = settings.muse_api_key
        payload = _get_json(
            client,
            "https://www.themuse.com/api/public/jobs",
            params=params,
            keyed=bool(settings.muse_api_key),
        )
        found = parse_muse(payload)
        if not found:
            break
        listings.extend(
            listing
            for listing in found
            if listing.published_on is None or listing.published_on >= oldest
        )
    return BoardFetch(listings)


def _fetch_jooble(client: httpx.Client, settings: Settings, query: BoardQuery) -> BoardFetch:
    url = f"https://jooble.org/api/{quote(settings.jooble_api_key or '', safe='')}"
    nationwide = not query.location or query.location.lower() in US_NAMES
    places = JOOBLE_NATIONWIDE_PLACES if nationwide else (query.location,)
    listings: list[RawListing] = []
    failed: list[str] = []
    last_error: Exception | None = None
    for place in places:
        try:
            response = client.post(
                url, json={"keywords": query.keywords, "location": place, "page": "1"}
            )
            response.raise_for_status()
            listings.extend(parse_jooble(response.json()))
        except FETCH_ERRORS as error:
            failed.append(place)
            last_error = error
    if last_error is not None and len(failed) == len(places):
        raise last_error
    if not nationwide:
        return BoardFetch(listings)
    worked = [place for place in places if place not in failed]
    return BoardFetch(
        listings,
        f"Jooble can't search a whole country, so this shows {', '.join(worked)}.",
    )


def _fetch_findwork(client: httpx.Client, settings: Settings, query: BoardQuery) -> BoardFetch:
    params = {"search": query.keywords}
    # Few Findwork jobs state a location, so asking for the whole US would discard nearly all.
    if query.location and query.location.lower() not in US_NAMES:
        params["location"] = query.location
    payload = _get_json(
        client,
        "https://findwork.dev/api/jobs/",
        params=params,
        headers={"Authorization": f"Token {settings.findwork_api_key}"},
        keyed=True,
    )
    return BoardFetch(parse_findwork(payload))


def _fetch_larabench(client: httpx.Client, _: Settings, __: BoardQuery) -> BoardFetch:
    listings: list[RawListing] = []
    for page in range(1, LARABENCH_PAGES + 1):
        payload = _get_json(
            client,
            "https://larabench.com/api/v1/jobs",
            params={"sort": "newest", "perPage": LARABENCH_PAGE_SIZE, "page": page},
        )
        listings.extend(parse_larabench(payload))
        if not _section(payload, "pagination").get("hasNextPage"):
            break
    return BoardFetch(listings)


BOARDS: tuple[Board, ...] = (
    Board("remotive", "Remotive", "https://remotive.com", _fetch_remotive),
    Board("remoteok", "Remote OK", "https://remoteok.com", _fetch_remoteok),
    Board("arbeitnow", "Arbeitnow", "https://www.arbeitnow.com", _fetch_arbeitnow),
    Board("jobicy", "Jobicy", "https://jobicy.com", _fetch_jobicy),
    Board(
        "himalayas",
        "Himalayas",
        "https://himalayas.app",
        _fetch_himalayas,
        searches_keywords=True,
    ),
    Board("muse", "The Muse", "https://www.themuse.com", _fetch_muse),
    Board("larabench", "LaraBench", "https://larabench.com/laravel-jobs", _fetch_larabench),
    Board(
        "jooble",
        "Jooble",
        "https://jooble.org",
        _fetch_jooble,
        required_settings=("jooble_api_key",),
        setup_url="https://jooble.org/api/about",
        searches_keywords=True,
        searches_location=True,
    ),
    Board(
        "findwork",
        "Findwork",
        "https://findwork.dev",
        _fetch_findwork,
        required_settings=("findwork_api_key",),
        setup_url="https://findwork.dev/developers/",
        searches_keywords=True,
        searches_location=True,
    ),
    # Off unless turned on, since neither site's terms allow automated searches.
    Board(
        "indeed",
        "Indeed",
        "https://www.indeed.com",
        _fetch_indeed,
        required_settings=("indeed_enabled",),
        searches_keywords=True,
        searches_location=True,
        scrapes=True,
        deepens=True,
    ),
    Board(
        "linkedin",
        "LinkedIn",
        "https://www.linkedin.com/jobs",
        _fetch_linkedin,
        required_settings=("linkedin_enabled",),
        searches_keywords=True,
        searches_location=True,
        scrapes=True,
        deepens=True,
    ),
)
