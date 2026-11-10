"""What every Scout stage says the same way: the fit, relocation and agency rules in a
brief, and the helpers its web searches and model errors share.

Scout's read-only agent used to live here. Scout now has one engine, run by
Callback's worker (docs/scout_spec.md); this service only judges postings, writes briefs
and finds companies, in tailor.pipeline and tailor.companies.
"""

import logging
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import anthropic

from tailor.metering import get as _get

logger = logging.getLogger(__name__)

_FIT = {
    "wide": "Cast a wide net. Keep any job whose work matches the description even when the "
    "person meets only some of what it asks: stretches are wanted, since they can learn fast. "
    "Skip only jobs needing something they plainly can't get soon, such as a licence, a "
    "clearance or many years in a specialty they've never worked in.",
    "balanced": "Keep jobs the person meets most of. A missing skill or two is fine.",
    "close": "Keep only close fits: the person meets nearly everything the job requires.",
}

_RELOCATION = {
    "none": "won't relocate: onsite and hybrid jobs must be within commuting distance.",
    "metro": "would move to a major US metro area for an onsite or hybrid job: any of the 50 "
    "or so largest, from New York and Los Angeles down to places like Orlando, Indianapolis, "
    "Cincinnati, Kansas City, Raleigh or Salt Lake City, with their suburbs. Not a small city "
    "or town.",
    "anywhere": "would move anywhere in the US for an onsite or hybrid job.",
}

_AGENCY = {
    "include": "Keep agency listings that fit. Name the agency in agency, put the employer in "
    "company if the posting shows it (otherwise the agency), and leave "
    "agency_url empty.",
    # The agent that looked for the employer's own posting is retired, so these are kept as
    # agency listings, and Callback's catalog links them to the employer's posting when it can.
    "find_employer": "Keep agency listings that fit. Name the agency in agency, and put the "
    "employer in company if the posting shows it (otherwise the agency).",
    "skip": "Skip every listing posted by a staffing or recruiting agency. Record only jobs "
    "the employer posted itself.",
}


def normalize_url(url: str | None) -> str | None:
    """A URL in a comparable form: http(s) only, lowercase host, no fragment or trailing slash."""
    if not url:
        return None
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
        return None
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, parts.query, ""))


def _result_urls(block: Any) -> set[str] | None:
    """The URLs one search or fetch result block returned, or None if it isn't one that worked."""
    kind = _get(block, "type")
    result = _get(block, "content")
    if kind == "web_search_tool_result" and isinstance(result, list):
        candidates = [_get(item, "url") for item in result]
    elif kind == "web_fetch_tool_result" and _get(result, "type") == "web_fetch_result":
        candidates = [_get(result, "url")]
    else:
        return None
    return {url for url in map(normalize_url, candidates) if url}


def _model_error(exc: anthropic.APIError) -> str:
    if isinstance(exc, anthropic.AuthenticationError):
        logger.error("The Anthropic API key was rejected")
        return "The search service's API key was rejected."
    if isinstance(exc, anthropic.RateLimitError):
        logger.warning("Rate limited by the Anthropic API")
        return "The model is rate limited right now. Try again later."
    if isinstance(exc, anthropic.BadRequestError):
        logger.error("The model request was invalid: %s", exc.message)
        return "The model rejected the request."
    if isinstance(exc, anthropic.APIStatusError):
        logger.error("The Anthropic API returned %s", exc.status_code)
        return f"The model returned an error ({exc.status_code})."
    logger.error("Could not reach the Anthropic API: %s", exc)
    return "The model couldn't be reached."
