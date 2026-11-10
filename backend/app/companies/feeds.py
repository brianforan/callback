"""Reading every open job at a company from its own hiring system, in one free request.

Greenhouse, Lever and Ashby each publish a company's open jobs as JSON, with no key. The hosts
are fixed and the token is checked before it's used, so a request can only reach those systems.
"""

import html
import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import httpx

from app.listings.boards import USER_AGENT, html_to_text

logger = logging.getLogger(__name__)

FEED_TIMEOUT_SECONDS = 20.0
# A big company's Greenhouse feed, with every job's text, can run to several megabytes.
MAX_FEED_BYTES = 25_000_000
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
_MODES = {"remote": "remote", "hybrid": "hybrid", "onsite": "onsite", "on-site": "onsite"}


class FeedGone(Exception):
    """The hiring system says this company has no board there (404 or 410)."""


@dataclass(frozen=True)
class FeedJob:
    """One open job as its company's own hiring system lists it."""

    url: str
    title: str
    company: str
    location: str | None
    work_mode: str | None
    posted_on: date | None
    text: str


def feed_url(system: str, token: str) -> str:
    if not _TOKEN.match(token):
        raise ValueError(f"{token!r} isn't a hiring-system token")
    if system == "greenhouse":
        return f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
    if system == "lever":
        return f"https://api.lever.co/v0/postings/{token}?mode=json"
    if system == "ashby":
        return f"https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true"
    raise ValueError(f"{system!r} isn't a hiring system Callback reads")


def new_feed_client() -> httpx.Client:
    return httpx.Client(
        timeout=FEED_TIMEOUT_SECONDS,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        follow_redirects=False,
    )


def _download(client: httpx.Client, url: str) -> Any:
    with client.stream("GET", url) as response:
        if response.status_code in (404, 410):
            raise FeedGone(f"answered {response.status_code}")
        response.raise_for_status()
        body = bytearray()
        for chunk in response.iter_bytes():
            body.extend(chunk)
            if len(body) > MAX_FEED_BYTES:
                raise ValueError("the feed is larger than Callback reads")
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValueError("the feed isn't JSON") from exc


def _mode(value: Any) -> str | None:
    return _MODES.get(str(value or "").strip().lower())


def _day(value: Any) -> date | None:
    text = str(value or "")[:10]
    try:
        return date.fromisoformat(text) if text else None
    except ValueError:
        return None


def _from_ms(value: Any) -> date | None:
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=UTC).date() if value else None
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _clean(text: str) -> str:
    # Postgres refuses NUL characters.
    return text.replace("\x00", "")


def _greenhouse(data: Any) -> list[FeedJob]:
    jobs = []
    for job in (data or {}).get("jobs") or []:
        text = html_to_text(html.unescape(str(job.get("content") or "")))
        jobs.append(
            FeedJob(
                url=str(job.get("absolute_url") or ""),
                title=str(job.get("title") or ""),
                company=str(job.get("company_name") or ""),
                location=str((job.get("location") or {}).get("name") or "") or None,
                work_mode=None,
                posted_on=_day(job.get("first_published") or job.get("updated_at")),
                text=_clean(text),
            )
        )
    return jobs


def _lever(data: Any) -> list[FeedJob]:
    jobs = []
    for job in data if isinstance(data, list) else []:
        sections = [str(job.get("descriptionPlain") or "")]
        for item in job.get("lists") or []:
            sections.append(f"{item.get('text') or ''}\n{html_to_text(item.get('content'))}")
        sections.append(str(job.get("additionalPlain") or ""))
        categories = job.get("categories") or {}
        jobs.append(
            FeedJob(
                url=str(job.get("hostedUrl") or ""),
                title=str(job.get("text") or ""),
                company="",
                location=str(categories.get("location") or "") or None,
                work_mode=_mode(job.get("workplaceType")),
                posted_on=_from_ms(job.get("createdAt")),
                text=_clean("\n\n".join(part for part in sections if part.strip())),
            )
        )
    return jobs


def _ashby(data: Any) -> list[FeedJob]:
    jobs = []
    for job in (data or {}).get("jobs") or []:
        if job.get("isListed") is False:
            continue
        mode = job.get("workplaceType") or ("remote" if job.get("isRemote") else "")
        jobs.append(
            FeedJob(
                url=str(job.get("jobUrl") or ""),
                title=str(job.get("title") or ""),
                company="",
                location=str(job.get("location") or "") or None,
                work_mode=_mode(mode),
                posted_on=_day(job.get("publishedAt")),
                text=_clean(str(job.get("descriptionPlain") or "")),
            )
        )
    return jobs


_PARSERS = {"greenhouse": _greenhouse, "lever": _lever, "ashby": _ashby}


def read_feed(client: httpx.Client, system: str, token: str) -> list[FeedJob]:
    """Every open job on one company's board. Raises FeedGone when the board doesn't exist, and
    httpx or ValueError errors when it can't be read."""
    data = _download(client, feed_url(system, token))
    jobs = [job for job in _PARSERS[system](data) if job.url and job.title]
    logger.info("Read %d open jobs from %s:%s", len(jobs), system, token)
    return jobs
