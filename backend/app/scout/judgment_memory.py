"""What Companies runs remember from the judge, and the filters they learn from it.

Every verdict is stored per profile, per catalog job and per brief, so a job is never judged
twice for the same brief unless its posting changed. A rejection that wasn't about place also
covers the job's twins: the same title at the same company, posted in other places.

From the stored verdicts and the user's own "Not for me" choices, a run learns two filters by
plain counting, no model involved:

- places the judge has always rejected for this brief (three or more times, never accepted),
  for postings placed only there and not remote;
- title words the profile has always passed on (five or more times, never kept), such as
  "manager" for someone who doesn't manage;
- levels the user has turned down as "Wrong level" (twice or more, none saved for later).

What the judge reads from a posting alone (the level it hires at, and whether someone in the US
can be hired) is stored on the shared catalog job, so every later run, by anyone, uses it in code.

One posting in ten that a learned filter would skip still goes to the judge, so a filter that
stops being true is unlearned. Every skip is counted in the run's notes, with the learned places
and words named.
"""

import hashlib
import json
import logging
import re
import uuid
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.companies.catalog import catalog_url
from app.models import (
    CatalogJob,
    CatalogJobSource,
    JobJudgment,
    PostingLevel,
    ScoutRun,
    utcnow,
)
from app.scout.limits import title_keys
from app.scout.match import level_from_value, parsed_level, search_titles, spec_from_payload

logger = logging.getLogger(__name__)

PLACE_REJECTIONS = 3
WORD_REJECTIONS = 5
LEVEL_REJECTIONS = 2
US_HIREABLE = ("yes", "no", "unclear")
# One posting in this many that a learned filter would skip is judged anyway.
EXPLORE_ONE_IN = 10
_MAX_NAMED = 6

# Title words that say nothing about whether a job suits someone.
_PLAIN_WORDS = {
    "and", "of", "the", "for", "to", "in", "a", "an", "with", "at", "on",
    "engineer", "engineering", "software", "senior", "developer", "development", "full",
    "stack", "fullstack", "backend", "frontend", "front", "back", "end", "web", "remote", "us",
    "usa", "ii", "iii", "iv", "i", "1", "2", "3", "team", "platform", "product", "application",
    "applications", "systems",
}  # fmt: skip
_PLACE_SPLIT = re.compile(r"\s*(?:;|/|\||\bor\b)\s*", re.I)
_REMOTE = re.compile(r"\b(?:remote|anywhere|distributed|work from home|wfh)\b", re.I)
_COUNTRY_TAIL = re.compile(r",\s*(?:united states(?: of america)?|usa|us)$")
_LOCATION_REASONS = ("wrong location",)
_LEVEL_REASONS = ("wrong level",)


def brief_key(payload: dict[str, Any]) -> str:
    """A fingerprint of what the judge is told about the person. A changed brief or fact sheet
    is a new key, so earlier verdicts stop applying."""
    parts = {
        "description": payload.get("description") or "",
        "title_families": title_keys(search_titles(payload)),
        "work_modes": sorted(payload.get("work_modes") or []),
        "location": payload.get("location") or "",
        "fit_mode": payload.get("fit_mode") or "",
        "agency_mode": payload.get("agency_mode") or "",
        "fact_sheet": payload.get("fact_sheet") or "",
    }
    # Only when set, so briefs saved before relocation existed keep their verdicts.
    if (payload.get("relocation") or "none") != "none":
        parts["relocation"] = payload["relocation"]
    # The work line from the profile's work boundary, once there is one.
    if payload.get("work"):
        parts["work"] = payload["work"]
    # The stack and levels the judge is held to. Verdicts made before strict matching didn't
    # check them, so they never carry over.
    spec = spec_from_payload(payload)
    parts["must_have"] = list(spec.must_have)
    parts["rule_out"] = list(spec.rule_out)
    parts["levels"] = sorted(level.value for level in spec.levels)
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()


def text_key(job: CatalogJob) -> str:
    """A fingerprint of the posting as the judge reads it."""
    text = f"{job.title}\n{job.location or ''}\n{job.work_mode or ''}\n{job.text}"
    return hashlib.sha256(text.encode()).hexdigest()


def title_words(title_key: str) -> set[str]:
    return {
        word
        for word in title_key.split()
        if word not in _PLAIN_WORDS and len(word) > 1 and not word.isdigit()
    }


def places(location: str | None) -> list[str]:
    """The places a job's location names, normalized: "San Francisco, CA; New York, NY" gives
    two. Remote parts are left out."""
    found = []
    for part in _PLACE_SPLIT.split((location or "").strip().lower()):
        part = " ".join(part.replace("(", " ").replace(")", " ").split()).strip(" ,-")
        part = _COUNTRY_TAIL.sub("", part)
        if part and not _REMOTE.search(part):
            found.append(part)
    return found


def _rejected(verdict: JobJudgment) -> bool:
    return not verdict.keep or verdict.fit == "none" or not verdict.location_ok


def _explored(job: CatalogJob) -> bool:
    """The posting that gets past a learned filter anyway, the same one every time."""
    return int(job.id.hex[:8], 16) % EXPLORE_ONE_IN == 0


@dataclass
class Memory:
    """A profile's verdicts for one brief, and what they've taught."""

    verdicts: dict[uuid.UUID, JobJudgment]
    # (company, title key) pairs rejected for reasons other than place.
    twin_rejections: set[tuple[uuid.UUID, str]] = field(default_factory=set)
    places: set[str] = field(default_factory=set)
    words: set[str] = field(default_factory=set)
    # Levels the user keeps turning down as the wrong level. The code gate leaves them out.
    levels: frozenset[PostingLevel] = frozenset()
    counts: Counter[str] = field(default_factory=Counter)
    skipped_places: Counter[str] = field(default_factory=Counter)
    skipped_words: Counter[str] = field(default_factory=Counter)

    def skip(self, job: CatalogJob) -> bool:
        """Whether to leave this job out without asking the judge. Counts why."""
        verdict = self.verdicts.get(job.id)
        if verdict is not None and verdict.text_key == text_key(job):
            self.counts["judged"] += 1
            return True
        if (job.company_id, job.title_key) in self.twin_rejections:
            self.counts["twin"] += 1
            return True
        if _explored(job):
            return False
        named = places(job.location)
        if (
            named
            and not _REMOTE.search(job.location or "")
            and all(place in self.places for place in named)
        ):
            self.counts["place"] += 1
            self.skipped_places.update(named)
            return True
        hits = title_words(job.title_key) & self.words
        if hits:
            self.counts["word"] += 1
            self.skipped_words.update(hits)
            return True
        return False

    def reset_counts(self) -> None:
        """Forget what was counted, before the catalog is scanned again in the same run."""
        self.counts.clear()
        self.skipped_places.clear()
        self.skipped_words.clear()

    def note_rejection(self, job: CatalogJob, location_ok: bool) -> None:
        """A rejection in this run also covers the job's twins later in the run."""
        if location_ok:
            self.twin_rejections.add((job.company_id, job.title_key))

    def notes(self) -> list[str]:
        parts = [
            f"{self.counts['judged']} judged before for this brief"
            if self.counts["judged"]
            else "",
            f"{self.counts['twin']} the same job as one turned down, posted elsewhere"
            if self.counts["twin"]
            else "",
            f"{self.counts['place']} only at places the judge has always turned down for you "
            f"({_named(self.skipped_places)})"
            if self.counts["place"]
            else "",
            f"{self.counts['word']} with title words you've always passed on "
            f"({_named(self.skipped_words)})"
            if self.counts["word"]
            else "",
        ]
        said = "; ".join(part for part in parts if part)
        return [f"Remembered, so not judged again: {said}."] if said else []


def _named(counter: Counter[str]) -> str:
    return ", ".join(name for name, _ in counter.most_common(_MAX_NAMED))


def _lead_jobs(
    db: Session, profile_id: uuid.UUID, states: tuple[str, ...]
) -> list[tuple[CatalogJob, str, str]]:
    """Catalog jobs behind this profile's leads in these states, with each state and reason."""
    picked: list[tuple[str, str, str]] = []
    for leads in db.scalars(select(ScoutRun.leads).where(ScoutRun.profile_id == profile_id)):
        for lead in leads or []:
            if lead.get("state") in states:
                picked.append(
                    (
                        lead.get("url") or "",
                        str(lead["state"]),
                        str(lead.get("decline_reason") or ""),
                    )
                )
    links = {link for url, _, _ in picked if (link := catalog_url(url))}
    if not links:
        return []
    by_url = {
        source.url: source.job
        for source in db.scalars(select(CatalogJobSource).where(CatalogJobSource.url.in_(links)))
    }
    return [
        (by_url[link], state, reason)
        for url, state, reason in picked
        if (link := catalog_url(url)) in by_url
    ]


def _dismissed_jobs(db: Session, profile_id: uuid.UUID) -> list[tuple[CatalogJob, str]]:
    """Catalog jobs the user turned down from this profile's leads, with their reasons."""
    return [(job, reason) for job, _, reason in _lead_jobs(db, profile_id, ("dismissed",))]


def job_level(job: CatalogJob) -> PostingLevel | None:
    """The job's level: what a judge read from this posting, else what the parser reads."""
    if job.facts_key and job.facts_key == text_key(job) and job.level:
        return level_from_value(job.level)
    return parsed_level(job.title, job.text)


def _learned_levels(db: Session, profile_id: uuid.UUID) -> frozenset[PostingLevel]:
    """Levels turned down as the wrong level at least twice, none of them saved for later."""
    turned_down: Counter[PostingLevel] = Counter()
    saved: set[PostingLevel] = set()
    for job, state, reason in _lead_jobs(db, profile_id, ("dismissed", "later")):
        level = job_level(job)
        if level is None:
            continue
        if state == "later":
            saved.add(level)
        elif any(marker in reason.lower() for marker in _LEVEL_REASONS):
            turned_down[level] += 1
    return frozenset(
        level
        for level, count in turned_down.items()
        if count >= LEVEL_REJECTIONS and level not in saved
    )


def load_memory(
    db: Session, profile_id: uuid.UUID, key: str, protected: set[str] | None = None
) -> Memory:
    """The profile's verdicts for this brief, and the filters they and the user's choices teach.

    protected words, the brief's own technologies and title families, are never learned as
    words to pass on, however many jobs with them were turned down for other reasons.
    """
    verdicts = {
        verdict.catalog_job_id: verdict
        for verdict in db.scalars(
            select(JobJudgment).where(
                JobJudgment.profile_id == profile_id, JobJudgment.brief_key == key
            )
        )
    }
    jobs = (
        {
            job.id: job
            for job in db.scalars(select(CatalogJob).where(CatalogJob.id.in_(list(verdicts))))
        }
        if verdicts
        else {}
    )
    memory = Memory(verdicts=verdicts)
    place_no: Counter[str] = Counter()
    place_yes: Counter[str] = Counter()
    word_no: Counter[str] = Counter()
    word_yes: Counter[str] = Counter()
    for job_id, verdict in verdicts.items():
        job = jobs.get(job_id)
        if job is None:
            continue
        named = places(job.location)
        words = title_words(job.title_key)
        if not verdict.location_ok:
            place_no.update(named)
            continue
        place_yes.update(named)
        if _rejected(verdict):
            memory.twin_rejections.add((job.company_id, job.title_key))
            word_no.update(words)
        else:
            word_yes.update(words)
    for job, reason in _dismissed_jobs(db, profile_id):
        if any(marker in reason.lower() for marker in _LOCATION_REASONS):
            place_no.update(places(job.location))
        else:
            word_no.update(title_words(job.title_key))
    memory.places = {
        place
        for place, count in place_no.items()
        if count >= PLACE_REJECTIONS and not place_yes[place]
    }
    memory.words = {
        word
        for word, count in word_no.items()
        if count >= WORD_REJECTIONS and not word_yes[word] and word not in (protected or set())
    }
    memory.levels = _learned_levels(db, profile_id)
    return memory


def remember(
    db: Session,
    user_id: uuid.UUID,
    profile_id: uuid.UUID,
    run_id: uuid.UUID | None,
    job: CatalogJob,
    key: str,
    judgment: dict[str, Any],
) -> None:
    """Store a verdict, replacing an earlier one for the same job and brief. Flushes only."""
    verdict = db.scalar(
        select(JobJudgment).where(
            JobJudgment.profile_id == profile_id,
            JobJudgment.catalog_job_id == job.id,
            JobJudgment.brief_key == key,
        )
    )
    if verdict is None:
        verdict = JobJudgment(
            user_id=user_id, profile_id=profile_id, catalog_job_id=job.id, brief_key=key
        )
        db.add(verdict)
    verdict.text_key = text_key(job)
    verdict.keep = bool(judgment.get("keep"))
    verdict.fit = str(judgment.get("fit") or "none")[:20]
    verdict.location_ok = bool(judgment.get("location_ok"))
    stack_ok = judgment.get("stack_ok")
    verdict.stack_ok = None if stack_ok is None else bool(stack_ok)
    level = level_from_value(judgment.get("posting_level"))
    verdict.posting_level = level.value if level else None
    verdict.verified = bool(judgment.get("verified"))
    verdict.reason = str(judgment.get("reason") or "")[:2_000]
    verdict.run_id = run_id
    verdict.judged_at = utcnow()
    db.flush()


def store_facts(job: CatalogJob, judgment: dict[str, Any]) -> None:
    """Keep what the judge read from the posting alone on the shared catalog job: the level it
    hires at and whether someone in the US can be hired. An unclear answer never replaces a
    clear one read from the same text. Flushes nothing."""
    key = text_key(job)
    same_text = job.facts_key == key
    level = level_from_value(judgment.get("posting_level"))
    hireable = str(judgment.get("us_hireable") or "").strip().lower()
    if hireable not in US_HIREABLE:
        hireable = "unclear"
    if level is not None or not same_text:
        job.level = level.value if level else None
    if hireable != "unclear" or not same_text:
        job.us_hireable = hireable
    job.facts_key = key
