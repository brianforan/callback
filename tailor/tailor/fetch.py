"""Fetching job postings with code, not the model, for the Scout pipeline.

Greenhouse, Lever and Ashby postings are read from those systems' free public job feeds, which
give clean text, the location and often pay. Any other page is downloaded and reduced to text.
Nothing here spends tokens.

The links come from a model's search results, so they're treated as hostile: only http(s), only
hosts with a dot in the name, and every address the host resolves to must be public, checked
again on each redirect. That keeps a planted link from reaching this container's own networks.
"""

import html
import ipaddress
import json
import logging
import re
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, urljoin, urlsplit

import httpx

logger = logging.getLogger(__name__)

_MAX_REDIRECTS = 3
_SKIPPED_TAGS = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form"}
_BLOCK_TAGS = {"p", "div", "li", "br", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section"}
_GREENHOUSE_HOSTS = ("boards.greenhouse.io", "job-boards.greenhouse.io")
_GREENHOUSE = re.compile(r"^/([^/]+)/jobs/(\d+)")
# Embedded application forms name the board and the job in the query instead.
_GREENHOUSE_EMBED = "/embed/job_app"
# Lever and Ashby links are both /company/posting-id.
_COMPANY_AND_ID = re.compile(r"^/([^/]+)/([0-9a-f-]{20,})")
_USER_AGENT = "Callback/0.1 (personal job search tracker)"
# Ashby's feed is the company's whole job board, which can run to several megabytes.
_FEED_MAX_BYTES = 30_000_000

Resolver = Callable[[str], list[str]]


class FetchRefused(Exception):
    """The link isn't one this fetcher will open. The message is safe to show."""


@dataclass
class FetchedPage:
    url: str
    ok: bool
    source: str = "page"
    title: str = ""
    company: str = ""
    location: str = ""
    work_mode: str = ""
    employment_type: str = ""
    salary: str = ""
    posted_at: str = ""
    text: str = ""
    error: str = ""


def _resolve(host: str) -> list[str]:
    return sorted({info[4][0] for info in socket.getaddrinfo(host, None)})


def check_url(url: str, resolve: Resolver = _resolve) -> None:
    """Refuse links that could reach anything but the public internet."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise FetchRefused("Only http and https links are opened.")
    host = (parts.hostname or "").lower()
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is None and (not host or "." not in host):
        raise FetchRefused("That link has no public host name.")
    if literal is not None:
        addresses = [str(literal)]
    else:
        try:
            addresses = resolve(host)
        except (OSError, UnicodeError) as exc:
            raise FetchRefused("That host couldn't be found.") from exc
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise FetchRefused("That link points at a private or local address.")


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self._skipping = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIPPED_TAGS:
            self._skipping += 1
        elif tag == "title" and not self._skipping:
            # An icon's <title> inside an <svg> isn't the page's.
            self._in_title = True
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")
        if tag == "li":
            self.parts.append("- ")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIPPED_TAGS and self._skipping:
            self._skipping -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif not self._skipping:
            self.parts.append(data)


def html_to_text(markup: str) -> tuple[str, str]:
    """Readable text and the page title from HTML, with scripts and page furniture dropped."""
    parser = _TextParser()
    parser.feed(markup)
    lines = [" ".join(line.split()) for line in "".join(parser.parts).splitlines()]
    text = "\n".join(line for line in lines if line)
    return re.sub(r"\n{3,}", "\n\n", text), " ".join(parser.title.split())


def _iso_from_ms(value: Any) -> str:
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=UTC).date().isoformat()
    except (TypeError, ValueError, OverflowError):
        return ""


# Links to a company's board on Greenhouse, Lever or Ashby, as careers pages write them: links,
# embed scripts and iframes, sometimes without a scheme.
_HIRING_LINK = re.compile(
    r"(?:https?:)?//(?:(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io|jobs(?:\.eu)?\.lever\.co"
    r"|jobs\.ashbyhq\.com)/[^\s\"'<>\\)]+",
    re.I,
)
_MAX_HIRING_LINKS = 20


class Fetcher:
    """Opens posting links safely. The client, resolver and clock are passed in so tests need
    no net.

    One instance serves concurrent requests, so a link's deadline travels with the call rather
    than living on the instance.
    """

    def __init__(
        self,
        client: httpx.Client,
        max_bytes: int,
        max_chars: int,
        resolve: Resolver = _resolve,
        deadline_seconds: float = 45.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.client = client
        self.max_bytes = max_bytes
        self.max_chars = max_chars
        self.resolve = resolve
        self.deadline_seconds = deadline_seconds
        self.clock = clock

    def _check_time(self, deadline: float) -> None:
        if self.clock() > deadline:
            raise FetchRefused("The page took too long to load.")

    def _get(self, url: str, deadline: float, max_bytes: int | None = None) -> httpx.Response:
        """GET with each hop checked, at most a few redirects, a size limit and a deadline.

        The client's timeout is per read, so a server that sends a little at a time would
        otherwise hold one link for as long as it liked.
        """
        limit = max_bytes or self.max_bytes
        current = url
        for _ in range(_MAX_REDIRECTS + 1):
            self._check_time(deadline)
            check_url(current, self.resolve)
            with self.client.stream(
                "GET", current, headers={"User-Agent": _USER_AGENT}, follow_redirects=False
            ) as response:
                if response.is_redirect:
                    location = response.headers.get("location", "")
                    current = urljoin(current, location)
                    continue
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > limit:
                        raise FetchRefused("The page is too large to read.")
                    self._check_time(deadline)
                # The body is already decoded, so encoding headers would make it decode twice.
                headers = {
                    name: value
                    for name, value in response.headers.items()
                    if name.lower()
                    not in ("content-encoding", "content-length", "transfer-encoding")
                }
                return httpx.Response(
                    response.status_code,
                    headers=headers,
                    content=bytes(body),
                    request=response.request,
                )
        raise FetchRefused("The link redirected too many times.")

    def _json(self, url: str, deadline: float) -> Any:
        response = self._get(url, deadline, _FEED_MAX_BYTES)
        if response.status_code != httpx.codes.OK:
            raise FetchRefused(f"The job feed answered {response.status_code}.")
        try:
            return response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise FetchRefused("The job feed's answer couldn't be read.") from exc

    def _clip(self, text: str) -> str:
        return text[: self.max_chars]

    def _greenhouse(self, url: str, board: str, job_id: str, deadline: float) -> FetchedPage:
        data = self._json(
            f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job_id}", deadline
        )
        text, _ = html_to_text(html.unescape(str(data.get("content") or "")))
        return FetchedPage(
            url=url,
            ok=True,
            source="greenhouse",
            title=str(data.get("title") or ""),
            company=str(data.get("company_name") or ""),
            location=str((data.get("location") or {}).get("name") or ""),
            posted_at=str(data.get("first_published") or data.get("updated_at") or "")[:10],
            text=self._clip(text),
        )

    def _lever(self, url: str, company: str, posting_id: str, deadline: float) -> FetchedPage:
        data = self._json(f"https://api.lever.co/v0/postings/{company}/{posting_id}", deadline)
        sections = [str(data.get("descriptionPlain") or "")]
        for item in data.get("lists") or []:
            items, _ = html_to_text(str(item.get("content") or ""))
            sections.append(f"{item.get('text') or ''}\n{items}")
        sections.append(str(data.get("additionalPlain") or ""))
        categories = data.get("categories") or {}
        pay = data.get("salaryRange") or {}
        salary = (
            f"{pay.get('currency', '')} {pay.get('min')}-{pay.get('max')} {pay.get('interval', '')}"
            if pay.get("min") and pay.get("max")
            else ""
        )
        return FetchedPage(
            url=url,
            ok=True,
            source="lever",
            title=str(data.get("text") or ""),
            location=str(categories.get("location") or ""),
            work_mode=str(data.get("workplaceType") or ""),
            employment_type=str(categories.get("commitment") or ""),
            salary=salary.strip(),
            posted_at=_iso_from_ms(data.get("createdAt")),
            text=self._clip("\n\n".join(section for section in sections if section.strip())),
        )

    def _ashby(self, url: str, org: str, job_id: str, deadline: float) -> FetchedPage:
        data = self._json(
            f"https://api.ashbyhq.com/posting-api/job-board/{org}?includeCompensation=true",
            deadline,
        )
        job = next((item for item in data.get("jobs") or [] if item.get("id") == job_id), None)
        if job is None:
            raise FetchRefused("The posting isn't on the company's job board any more.")
        pay = job.get("compensation") or {}
        mode = str(job.get("workplaceType") or ("Remote" if job.get("isRemote") else ""))
        return FetchedPage(
            url=url,
            ok=True,
            source="ashby",
            title=str(job.get("title") or ""),
            location=str(job.get("location") or ""),
            work_mode=mode,
            employment_type=str(job.get("employmentType") or ""),
            salary=str(pay.get("scrapeableCompensationSalarySummary") or ""),
            posted_at=str(job.get("publishedAt") or "")[:10],
            text=self._clip(str(job.get("descriptionPlain") or "")),
        )

    def _page(self, url: str, deadline: float) -> FetchedPage:
        response = self._get(url, deadline)
        if response.status_code != httpx.codes.OK:
            raise FetchRefused(f"The page answered {response.status_code}.")
        kind = response.headers.get("content-type", "")
        if "html" in kind:
            text, title = html_to_text(response.text)
        elif "text/plain" in kind:
            text, title = response.text, ""
        else:
            raise FetchRefused("The page isn't text or HTML.")
        if len(text) < 200:
            raise FetchRefused("The page had almost no text, so it may need a browser.")
        return FetchedPage(url=url, ok=True, source="page", title=title, text=self._clip(text))

    def hiring_links(self, url: str) -> list[str]:
        """Links to Greenhouse, Lever or Ashby boards on a careers page, including where the page
        itself redirected. Raises FetchRefused or httpx errors when the page can't be read."""
        deadline = self.clock() + self.deadline_seconds
        response = self._get(url, deadline)
        if response.status_code != httpx.codes.OK:
            raise FetchRefused(f"The page answered {response.status_code}.")
        found = [str(response.request.url)]
        found += [match.group(0) for match in _HIRING_LINK.finditer(response.text)]
        links: list[str] = []
        for link in found:
            link = html.unescape(link)
            link = f"https:{link}" if link.startswith("//") else link
            if _HIRING_LINK.search(link) and link not in links:
                links.append(link[:2_048])
        return links[:_MAX_HIRING_LINKS]

    def fetch(self, url: str) -> FetchedPage:
        """One posting, from its system's feed when it has one, else from the page itself.

        Never raises: one link that fails in a way nobody planned for is reported as unread, so
        the rest of the batch is still read.
        """
        deadline = self.clock() + self.deadline_seconds
        try:
            parts = urlsplit(url)
            host = (parts.hostname or "").lower()
            path = parts.path
            if host in _GREENHOUSE_HOSTS:
                if path.rstrip("/") == _GREENHOUSE_EMBED:
                    query = parse_qs(parts.query)
                    board = (query.get("for") or [""])[0]
                    job_id = (query.get("token") or [""])[0]
                    if board and job_id.isdigit():
                        return self._greenhouse(url, board, job_id, deadline)
                elif match := _GREENHOUSE.match(path):
                    return self._greenhouse(url, match[1], match[2], deadline)
            if host == "jobs.lever.co" and (match := _COMPANY_AND_ID.match(path)):
                return self._lever(url, match[1], match[2], deadline)
            if host == "jobs.ashbyhq.com" and (match := _COMPANY_AND_ID.match(path)):
                return self._ashby(url, match[1], match[2], deadline)
            return self._page(url, deadline)
        except FetchRefused as exc:
            return FetchedPage(url=url, ok=False, error=str(exc))
        except httpx.HTTPError as exc:
            logger.info("Couldn't fetch %s: %s", url, exc)
            return FetchedPage(url=url, ok=False, error="The page couldn't be reached.")
        except Exception:
            # A malformed link, or a feed shaped unlike the ones these readers expect.
            logger.exception("Unexpected error reading %s", url)
            return FetchedPage(url=url, ok=False, error="The page couldn't be read.")
