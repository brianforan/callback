"""Plain filters every Scout run shares: places, work modes, age, known jobs, and the
run's tally of spend and steps. No model calls.

Moved here from the pipeline engine when Scout became one engine (docs/scout_spec.md).
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from app.postings.parser import canonical_url, company_key
from app.scout.limits import title_query
from app.scout.match import search_titles, spec_from_payload
from app.workers import TailorUnavailable

logger = logging.getLogger(__name__)

_WORK_MODES = {"remote": "remote", "hybrid": "hybrid", "onsite": "onsite", "on-site": "onsite"}
# Job board searches made from the run's settings: its Must use technologies, or a phrase for
# each title family picked when it has none, up to a few searches.
_MAX_BOARD_QUERIES = 3
# A Location that names the US, a state or a US time zone narrows job board results to jobs open
# to someone in the US, which is the one place Job listings can check. Anything else sends none.
_US_STATES = (
    "Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|Delaware|Florida|Georgia|"
    "Hawaii|Idaho|Illinois|Indiana|Iowa|Kansas|Kentucky|Louisiana|Maine|Maryland|Massachusetts|"
    "Michigan|Minnesota|Mississippi|Missouri|Montana|Nebraska|Nevada|New Hampshire|New Jersey|"
    "New Mexico|New York|North Carolina|North Dakota|Ohio|Oklahoma|Oregon|Pennsylvania|"
    "Rhode Island|South Carolina|South Dakota|Tennessee|Texas|Utah|Vermont|Virginia|Washington|"
    "West Virginia|Wisconsin|Wyoming|District of Columbia"
)
# Two-letter codes, except those that are also everyday words (IN, OR, ME, OK, HI).
_US_CODES = (
    "AL|AK|AZ|AR|CA|CO|CT|DC|DE|FL|GA|IA|ID|IL|KS|KY|LA|MA|MD|MI|MN|MO|MS|MT|NC|ND|NE|NH|NJ|NM|"
    "NV|NY|OH|PA|RI|SC|SD|TN|TX|UT|VA|VT|WA|WI|WV|WY"
)
_US_LOCATION = (
    re.compile(
        r"\b(?:USA?|U\.S\.(?:A\.)?|United States|"
        r"(?:east|west) coast|eastern|pacific|central time|mountain time|"
        rf"{_US_STATES})(?![\w.])",
        re.I,
    ),
    re.compile(rf"(?:^|[\s,(])(?:{_US_CODES}|E[SD]?T|P[SD]?T|C[SD]?T|M[SD]?T)(?![\w.])"),
)
# Other places that share a word with the US ones above, such as Australia's east coast.
_NOT_US = re.compile(
    r"\b(?:Australia|Canada|Latin America|LATAM|Europe|EMEA|APAC|Asia|UK|United Kingdom)\b", re.I
)
# Places abroad that postings name. With a US Location, a posting whose location names one of
# them and nowhere in the US isn't open to the person, so code leaves it out before judging.
# Georgia is left out, since it's a state too.
_ABROAD = re.compile(
    r"\b(?:Canada|Mexico|Brazil|Argentina|Chile|Colombia|Peru|Uruguay|Costa Rica|Guatemala|"
    r"Latin America|LATAM|South America|Central America|Caribbean|"
    r"United Kingdom|UK|England|Scotland|Wales|Ireland|Germany|France|Spain|Portugal|Italy|"
    r"Netherlands|Belgium|Switzerland|Austria|Poland|Czechia|Czech Republic|Hungary|Romania|"
    r"Bulgaria|Greece|Sweden|Norway|Denmark|Finland|Estonia|Latvia|Lithuania|Ukraine|Serbia|"
    r"Croatia|Turkey|Türkiye|Europe|EMEA|EU|"
    r"Israel|United Arab Emirates|UAE|Egypt|Nigeria|Kenya|South Africa|"
    r"India|Pakistan|Bangladesh|Sri Lanka|Philippines|Vietnam|Thailand|Malaysia|Singapore|"
    r"Indonesia|China|Hong Kong|Taiwan|Japan|Korea|Australia|New Zealand|APAC|Asia|"
    r"London|Berlin|Munich|Paris|Amsterdam|Dublin|Toronto|Vancouver|Montreal|Budapest|Warsaw|"
    r"Krakow|Kraków|Lisbon|Madrid|Barcelona|Bangalore|Bengaluru|Hyderabad|Sydney|Melbourne|"
    r"Tel Aviv|Vilnius|Kaunas|Riga|Tallinn|Helsinki|Stockholm|Oslo|Copenhagen|Prague|"
    r"Bucharest|Belgrade|Kyiv|Kiev|Lviv|Zagreb|Ljubljana|Bratislava|Vienna|Zurich|Zürich|"
    r"Geneva|Brussels|Rotterdam|Hamburg|Frankfurt|Cologne|Milan|Valencia|Porto|Istanbul|"
    r"Edinburgh|Glasgow|Belfast|Cairo|Lagos|Nairobi|Cape Town|Johannesburg|Dubai|Abu Dhabi|"
    r"Riyadh|Doha|Karachi|Lahore|Islamabad|Pune|Chennai|Mumbai|Delhi|Noida|Gurgaon|Gurugram|"
    r"Kolkata|Dhaka|Colombo|Manila|Cebu|Jakarta|Bangkok|Ho Chi Minh|Hanoi|Kuala Lumpur|Seoul|"
    r"Tokyo|Osaka|Shanghai|Beijing|Shenzhen|Taipei|Auckland|Wellington|Brisbane|Perth|"
    r"Adelaide|Ottawa|Calgary|Edmonton|Mexico City|Guadalajara|Monterrey|Bogota|Bogotá|"
    r"Medellin|Medellín|Buenos Aires|Sao Paulo|São Paulo|Rio de Janeiro|Florianopolis|"
    r"Florianópolis|Montevideo|Quito|Caracas)\b",
    re.I,
)
# A US place in a posting's location. Stricter than _US_LOCATION, whose region words would count
# "Eastern Europe" or "Asia Pacific" as the US.
_IN_US = (
    re.compile(rf"\b(?:USA?|U\.S\.(?:A\.)?|United States|{_US_STATES})(?![\w.])", re.I),
    re.compile(rf"(?:^|[\s,(])(?:{_US_CODES}|E[SD]?T|P[SD]?T|C[SD]?T|M[SD]?T)(?![\w.])"),
)


@dataclass
class _Tally:
    """Spend, usage, per-request costs and steps across every stage call in the run."""

    spent: float = 0.0
    costs: dict[str, float] = field(default_factory=dict)
    usage: dict[str, int] = field(default_factory=dict)
    request_costs: list[dict[str, Any]] = field(default_factory=list)
    steps: list[dict[str, Any]] = field(default_factory=list)
    last: dict[str, float] = field(default_factory=dict)

    def add(self, stage: str, answer: dict[str, Any]) -> None:
        cost = float(answer.get("cost_usd") or 0.0)
        self.spent += cost
        self.last[stage] = cost
        for part, dollars in (answer.get("cost_breakdown") or {}).items():
            self.costs[part] = self.costs.get(part, 0.0) + float(dollars)
        for name, value in (answer.get("usage") or {}).items():
            self.usage[name] = self.usage.get(name, 0) + int(value or 0)
        for request in answer.get("request_costs") or []:
            self.request_costs.append(
                {
                    **request,
                    "request": len(self.request_costs) + 1,
                    "stage": stage,
                    "model": answer.get("model", ""),
                }
            )

    def step(self, kind: str, detail: str) -> None:
        self.steps.append(
            {"kind": kind, "detail": detail[:300], "request": len(self.request_costs)}
        )

    def add_failed(self, stage: str, error: TailorUnavailable) -> None:
        """A stage call that failed but was billed still counts toward the run's spend."""
        if error.spent:
            self.add(stage, error.spent)

    def spent_fields(self) -> dict[str, Any]:
        """What the run spent so far, in the shape a stage's answer has."""
        return {
            "model": "pipeline",
            "cost_usd": round(self.spent, 6),
            "cost_breakdown": {part: round(value, 6) for part, value in self.costs.items()},
            "usage": dict(self.usage),
            "request_costs": list(self.request_costs),
        }


def _known(payload: dict[str, Any]) -> tuple[set[str], set[tuple[str, str]]]:
    urls = {canonical_url(job.get("url")) for job in payload.get("known_jobs") or []}
    pairs = {
        (company_key(job.get("company") or ""), (job.get("title") or "").strip().lower())
        for job in payload.get("known_jobs") or []
        if job.get("title")
    }
    urls |= {canonical_url(url) for url in payload.get("passed_pages") or []}
    return {url for url in urls if url}, pairs


def _too_old(posted_at: str, days: int | None, today: date) -> bool:
    if not days or not posted_at:
        return False
    try:
        posted = date.fromisoformat(posted_at[:10])
    except ValueError:
        return False
    return posted < today - timedelta(days=days)


def _wrong_mode(page_mode: str, wanted: list[str]) -> bool:
    mode = _WORK_MODES.get(page_mode.strip().lower())
    return bool(wanted) and mode is not None and mode not in wanted


def board_location(location: str) -> str:
    """ "us" when a Scout Location names the US, a state or a US time zone, else nothing."""
    text = location or ""
    if _NOT_US.search(text):
        return ""
    return "us" if any(pattern.search(text) for pattern in _US_LOCATION) else ""


def _abroad(page_location: str, us_only: bool) -> bool:
    """With a US Location, a posting placed only outside the US. One that says nothing is left
    to the judge, as is one that names the US anywhere, such as "London or New York"."""
    text = page_location or ""
    if not us_only or not _ABROAD.search(text):
        return False
    return not any(pattern.search(text) for pattern in _IN_US)


def board_queries(payload: dict[str, Any]) -> list[str]:
    """Job board searches for a brief: each Must use technology, else each title phrase it
    searches under as an exact phrase, up to a few searches."""
    spec = spec_from_payload(payload)
    queries = [f'"{name}"' if " " in name else name for name in spec.must_have]
    if not queries:
        queries = [title_query(phrase) for phrase in search_titles(payload)]
    return list(dict.fromkeys(queries))[:_MAX_BOARD_QUERIES]
