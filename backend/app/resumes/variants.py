"""The reverse ATS report: each resume variant checked against every saved posting.

Pure functions, no models. A variant is scored on its own, with no application history, so the
result is what an ATS would see from that resume alone. Its rules then say whether it suits the job.
"""

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from itertools import combinations

from pydantic import ValidationError

from app.analytics import resume_evidence
from app.models import JobPosting, Resume
from app.postings.parser import BUILTIN, Vocabulary
from app.postings.store import is_placeholder
from app.resumes.matching import (
    UNINFORMATIVE_ROLES,
    JobFacts,
    MatchProfile,
    build_profile,
    facts_from_posting,
    score_job,
)
from app.schemas import (
    BaselinePick,
    MatchEstimate,
    MixSkill,
    UncoveredPosting,
    VariantFit,
    VariantOverlap,
    VariantReport,
    VariantRules,
    VariantSummary,
)

logger = logging.getLogger(__name__)


def _label(value: str) -> str:
    return value.replace("_", " ")


def variant_rules(resume: Resume) -> VariantRules:
    """The stored rules, or the defaults if they no longer validate."""
    try:
        return VariantRules.model_validate(resume.variant_rules or {})
    except ValidationError:
        logger.warning("Resume %s has invalid variant rules; using defaults", resume.id)
        return VariantRules()


def rule_failures(rules: VariantRules, job: JobFacts, match: MatchEstimate) -> list[str]:
    """Why a variant's rules turn a job down. Fields the job doesn't state never fail."""
    reasons = []
    targets = {family.value for family in rules.role_families}
    if targets and job.role_family not in UNINFORMATIVE_ROLES and job.role_family not in targets:
        reasons.append(f"Role type is {_label(job.role_family or '')}, which this baseline skips.")
    levels = {level.value for level in rules.levels}
    if levels and job.level is not None and job.level not in levels:
        reasons.append(f"Level is {job.level}, which this baseline skips.")
    named = {name for name, _ in job.technologies}
    if rules.focus_skills and not named & set(rules.focus_skills):
        reasons.append(f"Names none of the focus skills: {', '.join(rules.focus_skills)}.")
    required = {name for name, requirement in job.technologies if requirement == "required"}
    blocked = sorted(required & set(rules.deal_breakers))
    if blocked:
        reasons.append(f"Requires {', '.join(blocked)}, a deal-breaker for this baseline.")
    if job.requirements.clearance and not rules.allow_clearance:
        reasons.append("Requires a security clearance.")
    if (
        rules.max_years is not None
        and job.years_required is not None
        and job.years_required > rules.max_years
    ):
        reasons.append(f"Asks for {job.years_required} years, over the limit of {rules.max_years}.")
    if match.score < rules.min_score:
        reasons.append(f"Scores {match.score}, under the minimum of {rules.min_score}.")
    return reasons


@dataclass(frozen=True)
class Baselines:
    """The user's baselines, parsed once, with a profile that has no application history."""

    variants: list[Resume]
    rules: dict[uuid.UUID, VariantRules]
    profile: MatchProfile


@dataclass(frozen=True)
class _Scored:
    variant: Resume
    match: MatchEstimate
    reasons: list[str]


def load_baselines(
    resumes: Sequence[Resume], today: date, vocabulary: Vocabulary = BUILTIN
) -> Baselines | None:
    variants = sorted(
        (resume for resume in resumes if resume.is_variant),
        key=lambda resume: (resume.label.lower(), str(resume.id)),
    )
    profile = build_profile(variants, [], today, vocabulary)
    if profile is None:
        return None
    return Baselines(
        variants=variants,
        rules={variant.id: variant_rules(variant) for variant in variants},
        profile=profile,
    )


def _score_all(baselines: Baselines, job: JobFacts) -> list[_Scored]:
    """Every baseline scored on its own against the job, in label order."""
    scored = []
    for variant in baselines.variants:
        match = score_job(baselines.profile, job, only=variant)
        if match is not None:
            reasons = rule_failures(baselines.rules[variant.id], job, match)
            scored.append(_Scored(variant=variant, match=match, reasons=reasons))
    return scored


def _choose(baselines: Baselines, job: JobFacts, scored: list[_Scored]) -> _Scored | None:
    """The best passing baseline, or the closest one when none passes.

    A tied score goes to the baseline whose focus skills the job names most. Baselines are in
    label order, so max keeps the first of any remaining tie.
    """
    named = {name for name, _ in job.technologies}

    def focus_hits(item: _Scored) -> int:
        return len(named & set(baselines.rules[item.variant.id].focus_skills))

    passing = [item for item in scored if not item.reasons]
    if passing:
        return max(passing, key=lambda item: (item.match.score, focus_hits(item)))
    return max(scored, key=lambda item: item.match.score, default=None)


def _required_verdicts(match: MatchEstimate) -> dict[str, str]:
    return {
        check.name: check.verdict
        for check in match.checklist
        if check.kind == "skill" and check.requirement == "required"
    }


def pick_baseline(baselines: Baselines, job: JobFacts) -> BaselinePick | None:
    """Which baseline to start a job from, and which gaps another baseline could fill.

    A mix is a required skill the chosen baseline doesn't meet but another baseline does, so
    tailoring would combine the two instead of starting from one as it is.
    """
    scored = _score_all(baselines, job)
    chosen = _choose(baselines, job, scored)
    if chosen is None:
        return None
    others = [item for item in scored if item is not chosen]
    mix = []
    for name, verdict in _required_verdicts(chosen.match).items():
        if verdict == "met":
            continue
        donor = next(
            (item for item in others if _required_verdicts(item.match).get(name) == "met"), None
        )
        if donor is not None:
            mix.append(MixSkill(name=name, baseline=donor.variant.label))
    return BaselinePick(
        resume_id=chosen.variant.id,
        label=chosen.variant.label,
        score=chosen.match.score,
        covered=not chosen.reasons,
        reasons=chosen.reasons,
        mix=mix,
    )


def build_variant_report(
    resumes: Sequence[Resume],
    postings: Sequence[JobPosting],
    today: date,
    vocabulary: Vocabulary = BUILTIN,
) -> VariantReport:
    usable = sorted(
        (posting for posting in postings if not is_placeholder(posting)),
        key=lambda posting: (posting.company.lower(), posting.title.lower(), str(posting.id)),
    )
    baselines = load_baselines(resumes, today, vocabulary)
    if baselines is None:
        return VariantReport(postings=len(usable), variants=[], overlaps=[], uncovered=[])

    fits: dict[tuple[uuid.UUID, uuid.UUID], VariantFit] = {}
    uncovered = []
    for posting in usable:
        job = facts_from_posting(posting, vocabulary)
        scored = _score_all(baselines, job)
        chosen = _choose(baselines, job, scored)
        for item in scored:
            fits[(item.variant.id, posting.id)] = VariantFit(
                posting_id=posting.id,
                company=posting.company,
                title=posting.title,
                score=item.match.score,
                passes=not item.reasons,
                reasons=item.reasons,
                best=item is chosen and not item.reasons,
                knocked_out=item.match.knocked_out,
                missing_required=item.match.missing_required,
                weak_required=item.match.weak_required,
            )
        if chosen is None or chosen.reasons:
            uncovered.append(
                UncoveredPosting(
                    posting_id=posting.id,
                    company=posting.company,
                    title=posting.title,
                    closest_variant=chosen.variant.label if chosen else None,
                    closest_score=chosen.match.score if chosen else None,
                    reasons=chosen.reasons if chosen else [],
                )
            )

    summaries = []
    for variant in baselines.variants:
        mine = [fit for (variant_id, _), fit in fits.items() if variant_id == variant.id]
        mine.sort(key=lambda fit: (not fit.passes, -fit.score, fit.company.lower()))
        summaries.append(
            VariantSummary(
                resume_id=variant.id,
                label=variant.label,
                rules=baselines.rules[variant.id],
                evidence=resume_evidence(
                    variant, baselines.profile.structures[variant.id], vocabulary
                ),
                passes=sum(fit.passes for fit in mine),
                best_for=sum(fit.best for fit in mine),
                fits=mine,
            )
        )

    overlaps = []
    for first, second in combinations(baselines.variants, 2):
        shared = 0
        for posting in usable:
            left = fits.get((first.id, posting.id))
            right = fits.get((second.id, posting.id))
            shared += bool(left and right and left.passes and right.passes)
        overlaps.append(VariantOverlap(first=first.label, second=second.label, postings=shared))

    return VariantReport(
        postings=len(usable), variants=summaries, overlaps=overlaps, uncovered=uncovered
    )
