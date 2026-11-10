"""Estimates how well a job fits your resumes, as an ATS checklist would. Pure functions, no models.

Each requirement a posting states gets a verdict against each resume: met, weak or missing. The
best resume's checklist becomes the score, so the number always traces back to stated rules.
"""

import re
import uuid
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any

from app.analytics import got_callback
from app.models import Application, ApplicationStatus, JobPosting, PostingLevel, Resume, RoleFamily
from app.postings.parser import (
    BUILTIN,
    DEGREE_ORDER,
    DegreeLevel,
    PostingRequirements,
    Vocabulary,
    parse_requirements,
    requirement_overlap,
)
from app.resumes.structure import (
    Evidence,
    ResumeStructure,
    SkillEvidence,
    experience_months,
    highest_degree,
    latest_resume,
    mentions_clearance,
    parse_resume,
    skill_evidence,
)
from app.schemas import (
    CallbackEstimate,
    MatchEstimate,
    MatchPart,
    MatchResume,
    RequirementCheck,
    SimilarApplication,
    Verdict,
)
from app.similarity import term_counts

_RECENCY_HALF_LIFE_DAYS = 30
_CREDIT: dict[Verdict, float] = {"met": 1.0, "weak": 0.5, "missing": 0.0, "unknown": 0.5}
_WEIGHT_REQUIRED = 45
_WEIGHT_STACK_ONLY = 30
_WEIGHT_NICE = 5
_WEIGHT_TITLE = 15
_WEIGHT_SENIORITY = 15
_WEIGHT_EDUCATION = 10
_WEIGHT_ROLE = 10
# A stack score starts as if this many technologies were half matched, so a job naming one
# technology you know can't outrank a job naming six you know.
_STACK_PRIOR_COUNT = 2
_STACK_PRIOR_CREDIT = 0.5
# A missing clearance filters a resume out, so the score can't read as a good fit.
_KNOCKOUT_CAP = 40
# Within this many years of what's asked is weak rather than missing.
_YEARS_WEAK_MARGIN = 2
_LEVEL_WORDS = frozenset(
    "senior sr staff lead principal junior jr mid intern entry level ii iii iv".split()
)
_TITLE_NOISE = _LEVEL_WORDS | frozenset(
    "remote hybrid onsite contract full part time new grad llc inc".split()
)
# Rewrites applied before comparing titles, so "Back-end Developer" matches "Backend Engineer".
_TITLE_SYNONYMS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bdev[- ]?ops\b", re.I), "devops"),
    (re.compile(r"\bsoftware development engineer\b|\bsde\b|\bswe\b", re.I), "software engineer"),
    (re.compile(r"\b(?:developer|programmer|dev)\b", re.I), "engineer"),
    (re.compile(r"\bfull[- ]?stack\b", re.I), "fullstack"),
    (re.compile(r"\bback[- ]?end\b", re.I), "backend"),
    (re.compile(r"\bfront[- ]?end\b", re.I), "frontend"),
    (re.compile(r"\bsite reliability\b", re.I), "sre"),
    (re.compile(r"\bmachine learning\b", re.I), "ml"),
)
_DEGREE_NAMES = {
    DegreeLevel.BACHELOR: "Bachelor's degree",
    DegreeLevel.MASTER: "Master's degree",
    DegreeLevel.DOCTORATE: "Doctorate",
}
_LEVEL_ORDER = (
    PostingLevel.INTERN,
    PostingLevel.JUNIOR,
    PostingLevel.MID,
    PostingLevel.SENIOR,
    PostingLevel.STAFF,
    PostingLevel.PRINCIPAL,
    PostingLevel.MANAGER,
)
_LEVEL_RANK = {level.value: rank for rank, level in enumerate(_LEVEL_ORDER)}
_LEVELS_TO_NO_FIT = 2
_YEARS_TO_NO_FIT = 4
# Titles like a plain "Software Engineer" say nothing about the kind of role.
UNINFORMATIVE_ROLES = {RoleFamily.GENERAL_SOFTWARE.value, RoleFamily.OTHER.value, None}
# Past applications are compared by stack, then by level and role type.
_SIMILARITY_STACK_SHARE = 0.7
_SIMILARITY_LEVEL_SHARE = 0.15
_SIMILARITY_ROLE_SHARE = 0.15
_MAX_SIMILAR = 5
_SIMILAR_SHOWN = 3
# How many applications' worth of weight the overall rate carries against the similar ones.
_PRIOR_STRENGTH = 2
_HIGH_REQUIRED = 3
_HIGH_APPLICATIONS = 20
_MEDIUM_APPLICATIONS = 5


@dataclass(frozen=True)
class JobFacts:
    """The parts of a listing or posting that matching reads."""

    title: str
    level: str | None
    role_family: str | None
    years_required: int | None
    technologies: tuple[tuple[str, str], ...]
    requirements: PostingRequirements = PostingRequirements()
    posting_id: uuid.UUID | None = None


@dataclass(frozen=True)
class _PastApplication:
    company: str
    title: str
    status: ApplicationStatus
    callback: bool
    posting_id: uuid.UUID | None
    stack: dict[str, str]
    level: str | None
    role_family: str | None


@dataclass
class MatchProfile:
    """Your resumes and applications reduced to what a job is compared against."""

    resumes: list[Resume]
    resume_shares: dict[uuid.UUID, float]
    latest_resume: Resume
    structures: dict[uuid.UUID, ResumeStructure]
    applied_title_words: frozenset[str]
    level_weights: dict[str, float]
    role_weights: dict[str, float]
    past: list[_PastApplication]
    applications: int
    callbacks: int
    # The applications it was built from, and the day, so one posting's can be left out cheaply.
    _sources: tuple[Application, ...] = ()
    _today: date | None = None
    _evidence: dict[tuple[uuid.UUID, str], SkillEvidence] = field(default_factory=dict)
    # The skills the user's profiles know, so a term finds its other spellings in a resume.
    vocabulary: Vocabulary = BUILTIN
    _title_words: dict[uuid.UUID, frozenset[str]] = field(default_factory=dict)

    def without_posting(self, posting_id: uuid.UUID | None) -> "MatchProfile":
        """This profile without one posting's applications, so a job isn't scored against itself.

        The resumes are already parsed, so only the history is worked out again.
        """
        if posting_id is None or self._today is None:
            return self
        kept = [source for source in self._sources if source.posting_id != posting_id]
        if len(kept) == len(self._sources):
            return self
        history = _history(self.resumes, kept, self._today)
        if history is None:
            return self
        return replace(self, **history, _sources=tuple(kept), _title_words={})

    def evidence(self, resume: Resume, technology: str) -> SkillEvidence:
        key = (resume.id, technology)
        if key not in self._evidence:
            self._evidence[key] = skill_evidence(
                self.structures[resume.id], technology, self.vocabulary
            )
        return self._evidence[key]

    def years(self, resume: Resume) -> float | None:
        structure = self.structures[resume.id]
        return round(experience_months(structure) / 12, 1) if structure.roles else None

    def title_words(self, resume: Resume) -> frozenset[str]:
        """Words from the titles you've applied to and the role headings on this resume."""
        if resume.id not in self._title_words:
            headings = " ".join(role.heading for role in self.structures[resume.id].roles)
            self._title_words[resume.id] = self.applied_title_words | title_words(headings)
        return self._title_words[resume.id]


@dataclass(frozen=True)
class _Evaluation:
    resume: Resume
    score: float
    parts: list[MatchPart]
    checks: list[RequirementCheck]
    knocked_out: bool


def title_words(title: str) -> frozenset[str]:
    """A title's meaningful words after synonyms, without level or work-mode words."""
    text = title.lower()
    for pattern, replacement in _TITLE_SYNONYMS:
        text = pattern.sub(replacement, text)
    return frozenset(term for term in term_counts(text) if term not in _TITLE_NOISE)


def _recency(day: date, today: date) -> float:
    return 0.5 ** (max((today - day).days, 0) / _RECENCY_HALF_LIFE_DAYS)


def facts_from_posting(posting: JobPosting, vocabulary: Vocabulary = BUILTIN) -> JobFacts:
    return JobFacts(
        title=posting.title,
        level=posting.level,
        role_family=posting.role_family,
        years_required=posting.years_required,
        technologies=tuple((tech.name, tech.requirement) for tech in posting.technologies),
        requirements=parse_requirements(posting.raw_text, vocabulary),
        posting_id=posting.id,
    )


def _normalized(weights: Counter[str] | dict[str, float]) -> dict[str, float]:
    total = sum(weights.values())
    return {key: value / total for key, value in weights.items()} if total else {}


def build_profile(
    resumes: Sequence[Resume],
    applications: Sequence[Application],
    today: date,
    vocabulary: Vocabulary = BUILTIN,
) -> MatchProfile | None:
    """Parse each resume once. Recent applications weigh more in level and role history."""
    if not resumes:
        return None
    # The database returns rows in no fixed order, so sorting here settles every later tie.
    resumes = sorted(resumes, key=lambda resume: str(resume.id))
    applications = sorted(applications, key=lambda application: str(application.id))
    history = _history(resumes, applications, today)
    if history is None:
        return None
    return MatchProfile(
        resumes=resumes,
        structures={
            resume.id: parse_resume(resume.source_tex or resume.plain_text or "", today)
            for resume in resumes
        },
        **history,
        _sources=tuple(applications),
        _today=today,
        vocabulary=vocabulary,
    )


def _history(
    resumes: Sequence[Resume], applications: Sequence[Application], today: date
) -> dict[str, Any] | None:
    """Everything in a match profile that comes from the applications, for sorted inputs."""
    by_id = {resume.id: resume for resume in resumes}
    use: dict[uuid.UUID, float] = dict.fromkeys(by_id, 0.0)
    for application in applications:
        if application.resume_id in by_id:
            use[application.resume_id] += _recency(application.applied_on, today)
    # An uploaded resume you haven't sent yet counts as one use today, since it's likely current.
    for resume_id, amount in use.items():
        if not amount:
            use[resume_id] = 1.0

    levels: Counter[str] = Counter()
    roles: Counter[str] = Counter()
    past: list[_PastApplication] = []
    for application in applications:
        posting = application.posting
        if posting is None:
            continue
        weight = _recency(application.applied_on, today)
        if posting.level:
            levels[posting.level] += weight
        if posting.role_family:
            roles[posting.role_family] += weight
        past.append(
            _PastApplication(
                company=application.company,
                title=application.role_title,
                status=ApplicationStatus(application.status),
                callback=got_callback(application),
                posting_id=posting.id,
                stack={tech.name: tech.requirement for tech in posting.technologies},
                level=posting.level,
                role_family=posting.role_family,
            )
        )

    latest = latest_resume(resumes, applications)
    if latest is None:
        return None
    return {
        "resume_shares": _normalized(use),
        "latest_resume": latest,
        "applied_title_words": title_words(
            " ".join(application.role_title for application in applications)
        ),
        "level_weights": _normalized(levels),
        "role_weights": _normalized(roles),
        "past": past,
        "applications": len(applications),
        "callbacks": sum(got_callback(application) for application in applications),
    }


def _months_text(months: int) -> str:
    if months < 12:
        return f"{months} month{'' if months == 1 else 's'}"
    return f"{months / 12:.1f} years"


def _skill_check(evidence: SkillEvidence, requirement: str, years: int | None) -> RequirementCheck:
    """Met needs a role or project behind the skill, and enough dated months if years are asked."""
    verdict: Verdict
    if evidence.evidence is Evidence.NONE:
        verdict, detail = "missing", "Not on this resume."
    elif years is not None and evidence.evidence is Evidence.EXPERIENCE:
        if evidence.months >= years * 12:
            verdict = "met"
            detail = f"Asks {years}+ years; your roles show {_months_text(evidence.months)}."
        else:
            verdict = "weak"
            detail = f"Asks {years}+ years; your roles show only {_months_text(evidence.months)}."
    elif years is not None:
        verdict = "weak"
        detail = f"Asks {years}+ years, but no dated role on this resume names it."
    elif evidence.evidence is Evidence.EXPERIENCE:
        verdict = "met"
        last = evidence.last_used.strftime("%b %Y") if evidence.last_used else "unknown"
        detail = (
            f"In {evidence.roles} role{'' if evidence.roles == 1 else 's'}, "
            f"{_months_text(evidence.months)}, last used {last}."
        )
    elif evidence.evidence is Evidence.PROJECT:
        verdict, detail = "met", "In a project or undated entry."
    else:
        verdict, detail = "weak", "Only listed. No role or project mentions it."
    return RequirementCheck(
        kind="skill", name=evidence.name, requirement=requirement, verdict=verdict, detail=detail
    )


def _stack_part(
    checks: Sequence[RequirementCheck], label: str, weight: int, nouns: tuple[str, str]
) -> MatchPart:
    """Nouns are the singular and plural, as in ("nice-to-have", "nice-to-haves")."""
    verdicts = Counter(check.verdict for check in checks)
    credit = sum(_CREDIT[check.verdict] for check in checks)
    prior = _STACK_PRIOR_COUNT * _STACK_PRIOR_CREDIT
    return MatchPart(
        label=label,
        score=round((credit + prior) / (len(checks) + _STACK_PRIOR_COUNT) * 100),
        weight=weight,
        detail=(
            f"Of {len(checks)} {nouns[len(checks) != 1]}: {verdicts['met']} met, "
            f"{verdicts['weak']} weak, {verdicts['missing']} missing."
        ),
    )


def _unknown_stack_part() -> MatchPart:
    """Stack is the strongest signal, so a job that hides it can't score well on the rest."""
    return MatchPart(
        label="Stack not stated",
        score=round(_STACK_PRIOR_CREDIT * 100),
        weight=_WEIGHT_REQUIRED,
        detail="No known technologies were found in this text, so stack counts as neutral.",
    )


def _title_part(profile: MatchProfile, resume: Resume, job: JobFacts) -> MatchPart | None:
    # Seniority has its own part, so "Senior" doesn't also count as a title match here.
    words = title_words(job.title)
    if not words:
        return None
    shared = sorted(words & profile.title_words(resume))
    return MatchPart(
        label="Title",
        score=round(len(shared) / len(words) * 100),
        weight=_WEIGHT_TITLE,
        detail=(
            f"{len(shared)} of {len(words)} title words match your role titles or the jobs "
            "you've gone for" + (f": {', '.join(shared)}." if shared else ".")
        ),
    )


def _years_check(required: int, years: float | None) -> RequirementCheck:
    verdict: Verdict
    if years is None:
        verdict, detail = "unknown", "No dated roles were found on this resume."
    elif years >= required:
        verdict, detail = "met", f"Your dated roles cover {years:g} years."
    elif years >= required - _YEARS_WEAK_MARGIN:
        verdict, detail = "weak", f"Your dated roles cover {years:g} years, a little short."
    else:
        verdict, detail = "missing", f"Your dated roles cover {years:g} years."
    return RequirementCheck(
        kind="years",
        name=f"{required}+ years of experience",
        requirement="required",
        verdict=verdict,
        detail=detail,
    )


def _seniority_part(profile: MatchProfile, job: JobFacts, years: float | None) -> MatchPart | None:
    scores = []
    details = []
    if job.level in _LEVEL_RANK and profile.level_weights:
        usual = sum(_LEVEL_RANK[level] * share for level, share in profile.level_weights.items())
        distance = abs(_LEVEL_RANK[job.level] - usual)
        scores.append(max(0.0, 1 - distance / _LEVELS_TO_NO_FIT))
        common = max(sorted(profile.level_weights), key=lambda level: profile.level_weights[level])
        details.append(f"This is {job.level} level, and you've mostly applied to {common}.")
    if job.years_required is not None and years is not None:
        short = max(0.0, job.years_required - years)
        scores.append(max(0.0, 1 - short / _YEARS_TO_NO_FIT))
        details.append(
            f"It asks for {job.years_required} years, and your dated roles cover {years:g}."
        )
    if not scores:
        return None
    return MatchPart(
        label="Seniority",
        score=round(sum(scores) / len(scores) * 100),
        weight=_WEIGHT_SENIORITY,
        detail=" ".join(details),
    )


def _degree_check(profile: MatchProfile, resume: Resume, job: JobFacts) -> RequirementCheck | None:
    degree = job.requirements.degree
    if degree is None:
        return None
    held = highest_degree(profile.structures[resume.id])
    verdict: Verdict
    if held is not None and DEGREE_ORDER.index(held) >= DEGREE_ORDER.index(degree.level):
        verdict, detail = "met", f"Your resume shows a {_DEGREE_NAMES[held].lower()}."
    elif degree.equivalent_ok:
        verdict, detail = (
            "weak",
            "No matching degree, but the posting accepts equivalent experience.",
        )
    else:
        verdict = "missing"
        detail = (
            f"Your resume shows a {_DEGREE_NAMES[held].lower()}." if held else "No degree found."
        )
    return RequirementCheck(
        kind="degree",
        name=_DEGREE_NAMES[degree.level],
        requirement="required" if degree.required else "nice_to_have",
        verdict=verdict,
        detail=detail,
    )


def _education_part(check: RequirementCheck | None) -> MatchPart | None:
    if check is None or check.requirement != "required":
        return None
    return MatchPart(
        label="Education",
        score=round(_CREDIT[check.verdict] * 100),
        weight=_WEIGHT_EDUCATION,
        detail=f"{check.name}: {check.detail}",
    )


def _knockout_checks(
    profile: MatchProfile, resume: Resume, job: JobFacts
) -> list[RequirementCheck]:
    checks = []
    if job.requirements.clearance:
        held = mentions_clearance(profile.structures[resume.id])
        checks.append(
            RequirementCheck(
                kind="clearance",
                name="Security clearance",
                requirement="knockout",
                verdict="met" if held else "missing",
                detail="Your resume mentions a clearance."
                if held
                else "Your resume doesn't mention a clearance, so this is likely a hard filter.",
            )
        )
    if job.requirements.citizenship:
        checks.append(
            RequirementCheck(
                kind="citizenship",
                name="Citizenship",
                requirement="knockout",
                verdict="unknown",
                detail="A resume can't show this, so it isn't scored. Check it yourself.",
            )
        )
    return checks


def _role_part(profile: MatchProfile, job: JobFacts) -> MatchPart | None:
    # A plain "Software Engineer" title says nothing about the kind of role.
    if job.role_family in UNINFORMATIVE_ROLES or not profile.role_weights:
        return None
    share = profile.role_weights.get(job.role_family, 0.0)
    return MatchPart(
        label="Role type",
        score=round(share / max(profile.role_weights.values()) * 100),
        weight=_WEIGHT_ROLE,
        detail=f"{round(share * 100)}% of your recent applications were this role type.",
    )


def _names_with(job: JobFacts, requirement: str) -> list[str]:
    return [name for name, value in job.technologies if value == requirement]


def _evaluate(profile: MatchProfile, resume: Resume, job: JobFacts) -> _Evaluation | None:
    required = _names_with(job, "required")
    nice = _names_with(job, "nice_to_have")
    # Mentioned technologies only stand in when the posting marks nothing as required.
    mentioned = [] if required else _names_with(job, "mentioned")

    def skill_checks(names: Iterable[str], requirement: str) -> list[RequirementCheck]:
        return [
            _skill_check(
                profile.evidence(resume, name), requirement, job.requirements.years_for(name)
            )
            for name in names
        ]

    required_checks = skill_checks(required, "required")
    nice_checks = skill_checks(nice, "nice_to_have")
    mentioned_checks = skill_checks(mentioned, "mentioned")
    years = profile.years(resume)
    years_check = (
        _years_check(job.years_required, years) if job.years_required is not None else None
    )
    degree_check = _degree_check(profile, resume, job)
    knockouts = _knockout_checks(profile, resume, job)

    if required_checks:
        stack = _stack_part(
            required_checks,
            "Required stack",
            _WEIGHT_REQUIRED,
            ("required technology", "required technologies"),
        )
    elif mentioned_checks:
        stack = _stack_part(
            mentioned_checks,
            "Stack mentioned",
            _WEIGHT_STACK_ONLY,
            ("technology mentioned", "technologies mentioned"),
        )
    else:
        stack = _unknown_stack_part()
    candidates: Iterable[MatchPart | None] = (
        stack,
        _stack_part(nice_checks, "Nice to have", _WEIGHT_NICE, ("nice-to-have", "nice-to-haves"))
        if nice_checks
        else None,
        _title_part(profile, resume, job),
        _seniority_part(profile, job, years),
        _education_part(degree_check),
        _role_part(profile, job),
    )
    parts = [part for part in candidates if part is not None]
    if not parts:
        return None
    total_weight = sum(part.weight for part in parts)
    score = sum(part.score * part.weight for part in parts) / total_weight
    knocked_out = any(check.verdict == "missing" for check in knockouts)
    if knocked_out:
        score = min(score, _KNOCKOUT_CAP)

    checks = [
        *knockouts,
        *required_checks,
        *([years_check] if years_check else []),
        *([degree_check] if degree_check else []),
        *mentioned_checks,
        *nice_checks,
    ]
    return _Evaluation(
        resume=resume, score=score, parts=parts, checks=checks, knocked_out=knocked_out
    )


def _past_similarity(job: JobFacts, past: _PastApplication) -> float:
    """Shared requirements first. Level and role type only add to a shared stack."""
    stack = requirement_overlap(dict(job.technologies), past.stack)
    if not stack:
        return 0.0
    same_level = job.level is not None and job.level == past.level
    same_role = job.role_family not in UNINFORMATIVE_ROLES and job.role_family == past.role_family
    return (
        _SIMILARITY_STACK_SHARE * stack
        + _SIMILARITY_LEVEL_SHARE * same_level
        + _SIMILARITY_ROLE_SHARE * same_role
    )


def _callback_estimate(profile: MatchProfile, job: JobFacts) -> CallbackEstimate | None:
    """Callback rate of the closest past applications, pulled toward your overall rate.

    With little data the overall rate dominates, so one lucky application can't read as a sure
    thing. Applications still waiting to hear back count as no callback yet.
    """
    if not profile.applications:
        return None
    scored = []
    for past in profile.past:
        if job.posting_id is not None and past.posting_id == job.posting_id:
            continue
        similarity = _past_similarity(job, past)
        if similarity > 0:
            scored.append((similarity, past))
    scored.sort(key=lambda pair: (-pair[0], pair[1].company, pair[1].title))
    closest = scored[:_MAX_SIMILAR]

    overall = (profile.callbacks + 1) / (profile.applications + 2)
    weight = sum(similarity for similarity, _ in closest)
    hits = sum(similarity for similarity, past in closest if past.callback)
    return CallbackEstimate(
        rate=round((hits + _PRIOR_STRENGTH * overall) / (weight + _PRIOR_STRENGTH), 4),
        similar_applications=len(closest),
        similar_callbacks=sum(past.callback for _, past in closest),
        applications=profile.applications,
        callbacks=profile.callbacks,
        closest=[
            SimilarApplication(
                company=past.company,
                title=past.title,
                status=past.status,
                similarity=round(similarity, 4),
            )
            for similarity, past in closest[:_SIMILAR_SHOWN]
        ],
    )


def _confidence(required_total: int, applications: int) -> str:
    if required_total >= _HIGH_REQUIRED and applications >= _HIGH_APPLICATIONS:
        return "high"
    if required_total >= 1 and applications >= _MEDIUM_APPLICATIONS:
        return "medium"
    return "low"


def score_job(
    profile: MatchProfile, job: JobFacts, only: Resume | None = None
) -> MatchEstimate | None:
    """Score a job from 0 to 100 against the resume that fits it best, or only the one given.

    Parts with no data drop out and the others are reweighted.
    """
    evaluations = [
        evaluation
        for resume in ([only] if only is not None else profile.resumes)
        if (evaluation := _evaluate(profile, resume, job)) is not None
    ]
    if not evaluations:
        return None
    best = max(
        evaluations,
        key=lambda item: (item.score, profile.resume_shares[item.resume.id], str(item.resume.id)),
    )

    def verdicts(requirement: str) -> dict[str, Verdict]:
        return {
            check.name: check.verdict
            for check in best.checks
            if check.kind == "skill" and check.requirement == requirement
        }

    required = verdicts("required")
    nice = verdicts("nice_to_have")
    latest = profile.latest_resume
    return MatchEstimate(
        score=round(best.score),
        confidence=_confidence(len(required), profile.applications),
        applications=profile.applications,
        resumes=1 if only is not None else len(profile.resumes),
        parts=best.parts,
        checklist=best.checks,
        knocked_out=best.knocked_out,
        resume_years=profile.years(best.resume),
        matched_required=[name for name, verdict in required.items() if verdict == "met"],
        weak_required=[name for name, verdict in required.items() if verdict == "weak"],
        missing_required=[name for name, verdict in required.items() if verdict == "missing"],
        missing_nice=[name for name, verdict in nice.items() if verdict == "missing"],
        faded_required=[
            name
            for name in required
            if profile.evidence(latest, name).evidence is Evidence.NONE
            and any(
                profile.evidence(resume, name).evidence is not Evidence.NONE
                for resume in profile.resumes
            )
        ],
        latest_resume=latest.label,
        best_resume=MatchResume(
            id=best.resume.id,
            label=best.resume.label,
            required_matched=sum(verdict == "met" for verdict in required.values()),
            required_weak=sum(verdict == "weak" for verdict in required.values()),
            required_total=len(required),
            nice_matched=sum(verdict != "missing" for verdict in nice.values()),
            nice_total=len(nice),
        ),
        callback=_callback_estimate(profile, job),
    )
