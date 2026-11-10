"""Scout's stop limits and what a run may cost, as plain rules.

A run reads the shared catalog of open jobs, rules out in code what the brief can't want, and
has Haiku screen and Sonnet verify the rest (app.scout.engine, docs/scout_spec.md).
Nothing here calls a model. These rules decide which limits a user may set. What a user may
spend is counted in runs and briefs a day (app.app_config), not here.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Plan(StrEnum):
    FREE = "free"
    PREMIUM = "premium"


class TitleFamily(StrEnum):
    """Job titles that tend to mean building with or for internal teams."""

    FORWARD_DEPLOYED = "forward_deployed"
    SOLUTIONS = "solutions"
    IMPLEMENTATION = "implementation"
    INTERNAL_TOOLS = "internal_tools"
    BUSINESS_SYSTEMS = "business_systems"
    AUTOMATION = "automation"
    INTEGRATION = "integration"


class FitMode(StrEnum):
    """How close a fit a job must be for the agent to keep it."""

    WIDE = "wide"
    BALANCED = "balanced"
    CLOSE = "close"


class Relocation(StrEnum):
    """Where the person would move for an onsite or hybrid job."""

    # Only jobs near where they live, or remote.
    NONE = "none"
    # Also onsite or hybrid jobs in a major US metro area.
    METRO = "metro"
    # Also onsite or hybrid jobs anywhere in the US.
    ANYWHERE = "anywhere"


class AgencyMode(StrEnum):
    """What the agent does with a job a staffing or recruiting agency posted."""

    # Keep agency listings as leads, shown apart from jobs the employer posted.
    INCLUDE = "include"
    # Work out the employer and record its own posting when it can be found.
    FIND_EMPLOYER = "find_employer"
    # Record no agency listings at all.
    SKIP = "skip"


# Scout has one engine. Its stored id stays "companies", the engine it grew from, so its
# run history and limits carry on. Runs made before it may name "agent" or
# "pipeline"; they're shown as they ran and never started again.
ENGINE = "companies"
DEFAULT_ENGINE = ENGINE
PAST_ENGINES = ("agent", "pipeline", "companies")


@dataclass(frozen=True)
class LimitRange:
    """One engine's own range for a limit, and its own wording when it means something else."""

    minimum: int
    maximum: int
    default: int
    label: str | None = None
    help: str | None = None


@dataclass(frozen=True)
class LimitSpec:
    key: str
    label: str
    help: str
    unit: str
    minimum: int
    maximum: int
    default: int
    # Engines with their own range. The rest use the one above.
    by_engine: Mapping[str, LimitRange] = field(default_factory=dict)

    def range_for(self, engine: str) -> LimitRange:
        own = self.by_engine.get(engine)
        if own is not None:
            return LimitRange(
                own.minimum,
                own.maximum,
                own.default,
                own.label or self.label,
                own.help or self.help,
            )
        return LimitRange(self.minimum, self.maximum, self.default, self.label, self.help)


# The order here is the order the page shows them in.
LIMITS: tuple[LimitSpec, ...] = (
    LimitSpec(
        key="target_leads",
        label="Stop after this many good leads",
        help="The run ends as soon as it has this many jobs that fit. Fewer leads, cheaper run.",
        unit="leads",
        minimum=1,
        maximum=25,
        default=10,
        by_engine={"companies": LimitRange(1, 60, 25)},
    ),
    LimitSpec(
        key="dry_streak",
        label="Give up after this many searches with nothing new",
        help="A search counts when every result is one it has already seen. Stops a run that's "
        "searching in circles.",
        unit="searches",
        minimum=2,
        maximum=10,
        default=4,
    ),
    LimitSpec(
        key="max_searches",
        label="Most web searches",
        help="A hard ceiling on searches, whatever the agent decides.",
        unit="searches",
        minimum=3,
        maximum=40,
        default=15,
        by_engine={
            "companies": LimitRange(
                1,
                10,
                6,
                label="Most web searches to find new companies",
                help="Used only when the catalog has too few matching jobs. Reading companies' "
                "jobs needs none.",
            )
        },
    ),
    LimitSpec(
        key="max_pages",
        label="Most pages read",
        help="Postings, careers pages and company news, counted together.",
        unit="pages",
        minimum=3,
        maximum=60,
        default=25,
        by_engine={
            "companies": LimitRange(
                10,
                300,
                150,
                label="Most postings to check",
                help="Each is one small Haiku call. Jobs code rules out first don't count.",
            )
        },
    ),
    LimitSpec(
        key="max_minutes",
        label="Time limit",
        help="Wall-clock time before the run stops and returns what it has.",
        unit="minutes",
        minimum=2,
        maximum=20,
        default=10,
    ),
)

LIMITS_BY_KEY = {spec.key: spec for spec in LIMITS}

# The source on postings and applications Scout made, so other pages can badge them.
SCOUT_SOURCE = "SmartSearch"

POSTED_WITHIN_DAYS = (7, 14, 30)
MAX_DESCRIPTION_LENGTH = 600
MAX_LOCATION_LENGTH = 120
MAX_LABEL_LENGTH = 60


def default_limits(engine: str = DEFAULT_ENGINE) -> dict[str, int]:
    return {spec.key: spec.range_for(engine).default for spec in LIMITS}


def fit_limits(saved: dict[str, int], engine: str = DEFAULT_ENGINE) -> dict[str, int]:
    """Saved limits brought into today's ranges for the engine, so a later change to LIMITS, or
    switching engine, never breaks them."""
    fitted = {}
    for spec in LIMITS:
        allowed = spec.range_for(engine)
        value = saved.get(spec.key, allowed.default)
        fitted[spec.key] = max(allowed.minimum, min(allowed.maximum, value))
    return fitted


def limit_errors(limits: dict[str, int], engine: str = DEFAULT_ENGINE) -> list[str]:
    """Why these limits can't be saved for the engine, in plain words. Empty when they're fine."""
    errors = []
    for key in limits:
        if key not in LIMITS_BY_KEY:
            errors.append(f"{key} isn't a Scout limit")
    for spec in LIMITS:
        allowed = spec.range_for(engine)
        value = limits.get(spec.key)
        if value is None:
            errors.append(f"{allowed.label} is missing")
        elif not allowed.minimum <= value <= allowed.maximum:
            errors.append(
                f"{allowed.label} must be from {allowed.minimum} to {allowed.maximum} {spec.unit}"
            )
    return errors


def lead_posting_text(lead: dict[str, Any]) -> str:
    """Text for the posting a tracked lead becomes, so the posting parser can read it.

    The posting's own words come first when the agent copied them, then the pay, level and type
    it read, and the agent's summary, which says where it came from.
    """
    sections = [f"{lead.get('title', '')} at {lead.get('company', '')}"]
    if lead.get("posting_text"):
        sections.append(str(lead["posting_text"]))
    for label, key in (
        ("Salary", "salary"),
        ("Level", "level"),
        ("Employment type", "employment_type"),
        ("Location", "location"),
    ):
        if lead.get(key):
            sections.append(f"{label}: {lead[key]}")
    sections.append("Summary written by Scout from the posting at " + str(lead.get("url", "")))
    if lead.get("what_they_want"):
        sections.append(
            "What they want:\n" + "\n".join(f"- {item}" for item in lead["what_they_want"])
        )
    if lead.get("requirements"):
        sections.append("Requirements: " + ", ".join(lead["requirements"]))
    if lead.get("company_notes"):
        sections.append(f"About the company: {lead['company_notes']}")
    return "\n\n".join(sections)
