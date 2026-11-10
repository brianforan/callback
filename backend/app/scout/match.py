"""What a Scout brief asks of a job, and the code gate that checks it, with no model call.

The stack is strict: a brief that names a framework only gets jobs that name it in the title or
outside a nice-to-have section, and the judge confirms the job's own work uses it. Level and
place are flexible within fixed rules: one level either side of what's wanted is a stretch, and
location keeps its own rules (see app.scout.filters).

A brief's technologies come from its "Must use" and "Rule out" settings. When those were never
saved, they're read from the description by the posting parser's own list of technologies.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.models import PostingLevel, Requirement
from app.postings.parser import (
    canonical_technology,
    parse_posting,
    technology_in_text,
    technology_mentions,
    technology_position,
    technology_requirement,
)

# The most technologies read from a description when the brief doesn't name them itself.
MAX_DERIVED = 4
MAX_TECHNOLOGIES = 12
MAX_TECHNOLOGY_LENGTH = 40
# Less text than this is a snippet, too little for code or the judge to tell the stack from.
MIN_TEXT = 300

# Names that only turn up in jobs using the framework, so they count as naming it.
_IMPLIED: dict[str, tuple[str, ...]] = {
    "Laravel": ("Livewire", "Lumen", "FilamentPHP", "Filament PHP", "Laravel Nova"),
    "Django": ("Wagtail",),
    "Rails": ("Hotwire", "Sidekiq"),
    "Vue": ("Nuxt", "Pinia", "Vuex"),
}
_NEGATION = re.compile(
    r"\b(?:no|not|without|avoid(?:ing)?|except|exclud(?:e|ing)|never)\b[\s:]+([^.,;:!?\n()]{1,40})",
    re.I,
)

# Levels from lowest to highest. Manager isn't on it: it's kept only when wanted.
LADDER = (
    PostingLevel.INTERN,
    PostingLevel.JUNIOR,
    PostingLevel.MID,
    PostingLevel.SENIOR,
    PostingLevel.STAFF,
    PostingLevel.PRINCIPAL,
)
_RUNG = {level: index for index, level in enumerate(LADDER)}
LEVEL_VALUES = tuple(level.value for level in PostingLevel)


class LevelFit(StrEnum):
    EXACT = "exact"
    NEAR = "near"
    UNKNOWN = "unknown"
    OUT = "out"


class StackPlace(StrEnum):
    """Where a posting names one of the brief's technologies, strongest first."""

    TITLE = "title"
    REQUIRED = "required"
    MENTIONED = "mentioned"
    NICE_ONLY = "nice_only"
    NONE = "none"


_STACK_RANK = {
    StackPlace.TITLE: 3,
    StackPlace.REQUIRED: 2,
    StackPlace.MENTIONED: 1,
    StackPlace.NICE_ONLY: 0,
    StackPlace.NONE: 0,
}


def clean_technologies(names: Iterable[str]) -> list[str]:
    """Typed names in the parser's spelling ("postgres" is "PostgreSQL"), deduplicated."""
    cleaned: list[str] = []
    seen: set[str] = set()
    for name in names:
        text = " ".join(str(name).split())[:MAX_TECHNOLOGY_LENGTH]
        if not text:
            continue
        canonical = canonical_technology(text)
        if canonical.lower() not in seen:
            seen.add(canonical.lower())
            cleaned.append(canonical)
    return cleaned[:MAX_TECHNOLOGIES]


def derived_rule_out(description: str) -> list[str]:
    """Technologies the description rules out: "NO MULESOFT", "not Java"."""
    names: list[str] = []
    for match in _NEGATION.finditer(description or ""):
        names += [name for name, _ in technology_mentions(match.group(1)).most_common()]
    return clean_technologies(names)


def derived_must_have(description: str) -> list[str]:
    """The technologies the description names, most mentioned first, except ruled-out ones."""
    text = description or ""
    ruled = {name.lower() for name in derived_rule_out(text)}
    mentions = technology_mentions(text)
    # Most mentioned first, then in the order the description names them.
    ordered = sorted(mentions, key=lambda name: (-mentions[name], technology_position(name, text)))
    named = [name for name in ordered if name.lower() not in ruled]
    return clean_technologies(named[:MAX_DERIVED])


def effective_must_have(saved: list[str] | None, description: str) -> list[str]:
    """The brief's Must use list: the saved one, or the description's when never saved."""
    return clean_technologies(saved) if saved is not None else derived_must_have(description)


def effective_rule_out(saved: list[str] | None, description: str) -> list[str]:
    return clean_technologies(saved) if saved is not None else derived_rule_out(description)


def clean_levels(values: Iterable[str]) -> list[str]:
    known = set(LEVEL_VALUES)
    return list(dict.fromkeys(value for value in values if value in known))


@dataclass(frozen=True)
class MatchSpec:
    """What a brief asks of a job, read once per run."""

    must_have: tuple[str, ...]
    rule_out: tuple[str, ...]
    levels: frozenset[PostingLevel]

    @property
    def stack_terms(self) -> list[str]:
        """The Must use names and the names that imply them, for searching text."""
        terms: list[str] = []
        for name in self.must_have:
            terms.append(name)
            terms.extend(_IMPLIED.get(name, ()))
        return list(dict.fromkeys(terms))

    @property
    def strict(self) -> bool:
        return bool(self.must_have)

    def describe_levels(self) -> str:
        if not self.levels:
            return "any level except intern and manager"
        order = (*LADDER, PostingLevel.MANAGER)
        return ", ".join(level.value for level in order if level in self.levels)


def search_titles(payload: dict[str, Any]) -> list[str]:
    """The title phrases a run searches under: the ones picked, else, with no Must use list, the
    titles of the profile's work boundary (app.scout.work)."""
    picked = list(payload.get("title_families") or [])
    if picked or spec_from_payload(payload).strict:
        return picked
    return list(payload.get("boundary_titles") or [])


def spec_from_payload(payload: dict[str, Any]) -> MatchSpec:
    """The run's match spec. A payload without must_have or rule_out reads them from its
    description, as a brief saved before they existed would."""
    description = str(payload.get("description") or "")
    must_have = effective_must_have(payload.get("must_have"), description)
    rule_out = [
        name
        for name in effective_rule_out(payload.get("rule_out"), description)
        if name.lower() not in {item.lower() for item in must_have}
    ]
    levels = frozenset(PostingLevel(value) for value in clean_levels(payload.get("levels") or []))
    return MatchSpec(tuple(must_have), tuple(rule_out), levels)


def stack_place(spec: MatchSpec, title: str, text: str) -> StackPlace:
    """Where the job names one of the brief's technologies. NONE when the brief has none."""
    terms = spec.stack_terms
    if not terms:
        return StackPlace.NONE
    if any(technology_in_text(term, title) for term in terms):
        return StackPlace.TITLE
    found = technology_requirement(terms, text)
    if found is None:
        return StackPlace.NONE
    if found is Requirement.REQUIRED:
        return StackPlace.REQUIRED
    if found is Requirement.MENTIONED:
        return StackPlace.MENTIONED
    return StackPlace.NICE_ONLY


def stack_rank(place: StackPlace) -> int:
    return _STACK_RANK[place]


def stack_passes(spec: MatchSpec, place: StackPlace) -> bool:
    """Strict: with a Must use list, the job must name one outside a nice-to-have."""
    return not spec.strict or stack_rank(place) > 0


def ruled_out(spec: MatchSpec, title: str, text: str) -> str | None:
    """The first Rule out technology the job requires (in its title or a required line)."""
    for name in spec.rule_out:
        if technology_in_text(name, title):
            return name
        if technology_requirement([name], text) is Requirement.REQUIRED:
            return name
    return None


def parsed_level(title: str, text: str) -> PostingLevel | None:
    """The level the parser reads from the title, then from the years the posting asks for."""
    return parse_posting(title, text).level


def level_fit(
    spec: MatchSpec, level: PostingLevel | None, learned_out: frozenset[PostingLevel] = frozenset()
) -> LevelFit:
    """How a job's level compares with what's wanted. One rung either side is NEAR, a stretch
    the judge may keep. Levels the profile has learned to turn down are OUT unless wanted."""
    if level is None:
        return LevelFit.UNKNOWN
    if level in spec.levels:
        return LevelFit.EXACT
    if level in learned_out:
        return LevelFit.OUT
    if not spec.levels:
        unwanted = level in (PostingLevel.INTERN, PostingLevel.MANAGER)
        return LevelFit.OUT if unwanted else LevelFit.EXACT
    if level is PostingLevel.MANAGER or level is PostingLevel.INTERN:
        return LevelFit.OUT
    wanted = [_RUNG[item] for item in spec.levels if item in _RUNG]
    if wanted and min(abs(_RUNG[level] - rung) for rung in wanted) == 1:
        return LevelFit.NEAR
    return LevelFit.OUT


def level_from_value(value: str | None) -> PostingLevel | None:
    """A stored or judged level as a PostingLevel. None for "unclear" or anything unknown."""
    try:
        return PostingLevel(str(value or "").strip().lower())
    except ValueError:
        return None


def brief_lines(spec: MatchSpec) -> dict[str, Any]:
    """What the judge is told about the stack and levels, as payload fields."""
    return {
        "must_have": spec.stack_terms[:40],
        "rule_out": list(spec.rule_out),
        "levels": sorted(level.value for level in spec.levels),
    }
