"""Tailoring, API side: what's sent to the tailor service, and the checks on what comes back.

Pure functions, no models. The model in the tailor service only proposes a rewrite. These rules
decide whether it's kept: the job lines match the baseline exactly, every technology named is
backed by the fact sheet or one of the user's baselines, and the checklist score doesn't drop.
"""

import re
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from app.models import Resume
from app.postings.parser import TECHNOLOGY_NAMES, canonical_technology, technology_mentions
from app.resumes.matching import JobFacts, build_profile, score_job
from app.resumes.structure import parse_resume
from app.resumes.variants import Baselines
from app.schemas import BaselinePick, MixSkill, RequirementCheck, TailorAdvice, TailorGap
from app.similarity import LATEX_COMMENT, tex_to_text

# Sent with every request and cached by the tailor service, so keep it byte-for-byte stable.
# Anything specific to one person belongs in their fact sheet, which comes after these rules.
TAILORING_RULES = """\
1. Claims. Claim only what the fact sheet marks as claimable, or what the baseline already says. \
Never invent metrics, tools, titles, employers, dates, scope or leadership. A requirement the \
person can't truthfully meet goes in gaps, never on the resume.
2. Fact sheet first. Where the fact sheet gives a rule (headline wording, skills-line format, \
approved bullets, cut order, words to avoid), follow it over these general rules.
3. Length. One page. Keep the baseline's layout, macros, preamble, fonts, margins, metadata and \
contact line. If you add content, cut the least relevant bullet so the line count stays at or \
under the baseline's.
4. Fixed facts. Keep job titles, companies, locations, dates and education exactly as in the \
baseline, in the same order. Every role keeps at least one bullet.
5. Headline. Adjust only the short part of the headline toward the posting, using terms the fact \
sheet supports.
6. Keywords. Mirror the posting's exact phrasing where a truthful equivalent exists in the fact \
sheet or the baseline.
7. Skills lines. Reorder groups and items so the posting's matches come first. Drop items that \
don't matter for this posting to make room. Add only claimable skills the posting names.
8. Bullets. Reorder bullets within each role by relevance to the posting. Swap in approved \
alternates from the fact sheet. Rephrase with the posting's terms without changing facts.
9. Tools such as Git and Docker stay on the skills lines, not in bullets, unless the fact sheet \
says otherwise or the posting is specifically about them.
10. Changelog. Name what changed compared with the baseline, in at most 6 short lines.
11. Gaps. List each required or preferred item from the posting the person can't truthfully \
claim, including knockouts such as a clearance, a degree or years in a specific stack."""

# The fact sheet's skills table lives under a heading with this name.
_SKILLS_HEADING = re.compile(r"^#{1,6}\s*skills status\s*$", re.IGNORECASE)
_TABLE_SEPARATOR = re.compile(r"^\|?\s*:?-{3,}")
_NEVER = re.compile(r"\b(?:do not|don't|never)\s+(?:claim|list)\b")
_NAME_SPLIT = re.compile(r"[,/;()]")
_JOB_MACRO = re.compile(r"\\job(?![A-Za-z@])\s*")
_JOB_ARGUMENTS = 3
_MAX_DIFFERENCES = 3
_MAX_MIX_EVIDENCE = 3
_MAX_EVIDENCE_LENGTH = 300
_MAX_DETAIL_LENGTH = 500
_MAX_NOTES_LENGTH = 20_000


class SkillStatus(StrEnum):
    CLAIMABLE = "claimable"
    # Allowed only where the baseline already has it, such as inside one role's bullet.
    RESTRICTED = "restricted"
    NEVER = "never"


_STRICTNESS = {SkillStatus.CLAIMABLE: 0, SkillStatus.RESTRICTED: 1, SkillStatus.NEVER: 2}


@dataclass(frozen=True)
class FactSheetSkills:
    """The fact sheet's skills table, readable by a program.

    technologies holds names from the posting parser's list, keyed by their canonical spelling.
    phrases holds other names from rows that limit or forbid a claim, matched as written.
    """

    technologies: dict[str, SkillStatus]
    phrases: dict[str, SkillStatus]


def _status_of(cell: str) -> SkillStatus | None:
    text = cell.strip().lower()
    if text.startswith(("claimable", "secondary")):
        return SkillStatus.CLAIMABLE
    if text.startswith("inside"):
        return SkillStatus.RESTRICTED
    if _NEVER.search(text):
        return SkillStatus.NEVER
    return None


def _table_rows(markdown: str) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    in_section = False
    seen_table = False
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            in_section = bool(_SKILLS_HEADING.match(stripped))
            continue
        if not in_section:
            continue
        if not stripped.startswith("|"):
            if seen_table and stripped:
                break
            continue
        seen_table = True
        if _TABLE_SEPARATOR.match(stripped):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) >= 2:
            rows.append((cells[0], cells[1]))
    # The first row is the header.
    return rows[1:]


def _merge(found: dict[str, SkillStatus], name: str, status: SkillStatus) -> None:
    """A claimable row wins, since "do not claim" rows name narrower things (AWS IAM, not AWS)."""
    current = found.get(name)
    if current is None:
        found[name] = status
    elif SkillStatus.CLAIMABLE in (current, status):
        found[name] = SkillStatus.CLAIMABLE
    elif _STRICTNESS[status] > _STRICTNESS[current]:
        found[name] = status


def parse_skills_status(markdown: str) -> FactSheetSkills:
    """Read the "Skills status" table: a Skill column and a Status column.

    A status starting "Claimable" or "Secondary" allows the skill, one starting "Inside" allows it
    only where the baseline has it, and one saying "do not claim", "never claim" or "do not list"
    forbids it. Other rows are ignored.
    """
    technologies: dict[str, SkillStatus] = {}
    phrases: dict[str, SkillStatus] = {}
    known = {name.lower() for name in TECHNOLOGY_NAMES}
    for skill, status_cell in _table_rows(markdown):
        status = _status_of(status_cell)
        if status is None:
            continue
        for name in technology_mentions(skill):
            _merge(technologies, name, status)
        if status is SkillStatus.CLAIMABLE:
            continue
        for item in _NAME_SPLIT.split(skill):
            phrase = item.strip()
            if not phrase or not phrase[0].isupper():
                continue
            if canonical_technology(phrase).lower() in known:
                continue
            _merge(phrases, phrase, status)
    return FactSheetSkills(technologies=technologies, phrases=phrases)


def _phrase_in(phrase: str, text: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text) is not None


def check_technologies(
    new_tex: str, source: Resume, other_baselines: Iterable[Resume], skills: FactSheetSkills
) -> list[str]:
    """Every technology the new resume names is in the source baseline, or is allowed elsewhere.

    One the fact sheet limits or forbids is only allowed where the source baseline already has
    it. Otherwise it may come from another baseline or the fact sheet's claimable skills.
    """
    new_text = tex_to_text(new_tex)
    source_text = source.plain_text or tex_to_text(source.source_tex)
    source_names = set(technology_mentions(source_text))
    other_names: set[str] = set()
    for resume in other_baselines:
        other_names |= set(technology_mentions(resume.plain_text or tex_to_text(resume.source_tex)))

    reasons = []
    for name in sorted(technology_mentions(new_text)):
        if name in source_names:
            continue
        status = skills.technologies.get(name)
        if status in (SkillStatus.NEVER, SkillStatus.RESTRICTED):
            reasons.append(f"{name} is named, but your fact sheet says not to claim it here.")
        elif name not in other_names and status is not SkillStatus.CLAIMABLE:
            reasons.append(f"{name} is named, but it isn't in your fact sheet or any baseline.")
    for phrase in sorted(skills.phrases):
        if _phrase_in(phrase, new_text) and not _phrase_in(phrase, source_text):
            reasons.append(f"{phrase} is named, but your fact sheet says not to claim it here.")
    return reasons


def _braced(text: str, start: int) -> tuple[str, int] | None:
    """The contents of the brace group opening at start, and the index just after it."""
    if start >= len(text) or text[start] != "{":
        return None
    depth = 0
    index = start
    while index < len(text):
        char = text[index]
        if char == "\\":
            # An escaped character, such as \{ or \%, never opens or closes a group.
            index += 2
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1 : index], index + 1
        index += 1
    return None


def job_entries(tex: str) -> list[tuple[str, ...]]:
    """The arguments of every \\job call outside the macro's definition, whitespace collapsed."""
    source = LATEX_COMMENT.sub("", tex)
    entries = []
    for match in _JOB_MACRO.finditer(source):
        position = match.end()
        arguments = []
        for _ in range(_JOB_ARGUMENTS):
            while position < len(source) and source[position].isspace():
                position += 1
            group = _braced(source, position)
            if group is None:
                break
            arguments.append(" ".join(group[0].split()))
            position = group[1]
        # \newcommand{\job}[3]{...} has no brace group right after \job.
        if len(arguments) == _JOB_ARGUMENTS:
            entries.append(tuple(arguments))
    return entries


def check_job_lines(baseline_tex: str, new_tex: str, today: date) -> list[str]:
    """Titles, companies and dates must match the baseline exactly and stay in order.

    A template without a \\job macro is compared by the roles' date ranges instead.
    """
    before = job_entries(baseline_tex)
    if not before:
        old_roles = [(role.start, role.end) for role in parse_resume(baseline_tex, today).roles]
        new_roles = [(role.start, role.end) for role in parse_resume(new_tex, today).roles]
        if old_roles != new_roles:
            return ["The roles or their dates no longer match the baseline."]
        return []
    after = job_entries(new_tex)
    if before == after:
        return []
    if len(before) != len(after):
        return [f"The baseline has {len(before)} jobs, but the new resume has {len(after)}."]
    reasons = []
    for old, new in zip(before, after, strict=True):
        if old != new:
            reasons.append(f'A job line changed: "{" | ".join(old)}" became "{" | ".join(new)}".')
    return reasons[:_MAX_DIFFERENCES]


@dataclass(frozen=True)
class ScoreComparison:
    baseline: int
    tailored: int


def compare_scores(
    baselines: Sequence[Resume], source: Resume, new_resume: Resume, job: JobFacts, today: date
) -> ScoreComparison | None:
    """Score the source baseline and the new resume each on its own, as the reverse ATS does."""
    resumes = {resume.id: resume for resume in [*baselines, source, new_resume]}
    profile = build_profile(list(resumes.values()), [], today)
    if profile is None:
        return None
    before = score_job(profile, job, only=source)
    after = score_job(profile, job, only=new_resume)
    if before is None or after is None:
        return None
    return ScoreComparison(baseline=before.score, tailored=after.score)


def check_score(comparison: ScoreComparison | None) -> list[str]:
    if comparison is None:
        return ["The new resume couldn't be scored against the posting."]
    if comparison.tailored < comparison.baseline:
        return [f"The checklist score dropped from {comparison.baseline} to {comparison.tailored}."]
    return []


def transient_resume(tex: str, like: Resume) -> Resume:
    """An unsaved copy of a resume with new source, for scoring a result before keeping it.

    It takes the original's upload time, so ordering by date treats the two alike.
    """
    plain = tex_to_text(tex)
    return Resume(
        id=uuid.uuid4(),
        label=f"{like.label} (tailored)",
        filename="tailored.tex",
        source_tex=tex,
        plain_text=plain,
        word_count=len(plain.split()),
        content_sha256="",
        is_variant=False,
        variant_rules={},
        created_at=like.created_at,
    )


def gap_lines(checklist: Iterable[RequirementCheck]) -> list[dict[str, str]]:
    """The checklist lines the baseline doesn't fully meet, for the model to work on."""
    return [
        {
            "kind": check.kind,
            "name": check.name,
            "requirement": check.requirement,
            "verdict": check.verdict,
            "detail": check.detail[:_MAX_DETAIL_LENGTH],
        }
        for check in checklist
        if check.verdict != "met"
    ]


def _paragraphs(tex: str) -> list[str]:
    """Plain text per source paragraph or bullet, so one bullet is one line of evidence."""
    lines = []
    for chunk in re.split(r"\n\s*\n|\\item\b", tex):
        text = tex_to_text(f"\\begin{{document}}{chunk}\\end{{document}}")
        if text:
            lines.append(text)
    return lines


def mix_lines(pick: BaselinePick, baselines: Sequence[Resume]) -> list[dict[str, object]]:
    """Required skills another baseline backs, with up to three of its lines that name each."""
    by_label = {resume.label: resume for resume in baselines}
    mix = []
    for skill in pick.mix:
        donor = by_label.get(skill.baseline)
        evidence: list[str] = []
        if donor is not None:
            for line in _paragraphs(donor.source_tex):
                if skill.name in technology_mentions(line):
                    evidence.append(line[:_MAX_EVIDENCE_LENGTH])
                if len(evidence) == _MAX_MIX_EVIDENCE:
                    break
        mix.append({"name": skill.name, "baseline": skill.baseline, "evidence": evidence})
    return mix


def staged_notes(baseline_label: str, changelog: Sequence[str], gaps: Sequence[str]) -> str:
    """The notes a tailored posting is staged with: what changed and what's still missing."""
    lines = [f"Tailored from the {baseline_label} baseline."]
    if changelog:
        lines += ["", "Changes:", *(f"- {item}" for item in changelog)]
    lines += ["", "Gaps:", *(f"- {item}" for item in gaps)] if gaps else ["", "Gaps: none"]
    return "\n".join(lines)[:_MAX_NOTES_LENGTH]


_CLOSABLE_REQUIREMENTS = {"required", "nice_to_have"}


def closable_gaps(
    checklist: Iterable[RequirementCheck], mix: Iterable[MixSkill], skills: FactSheetSkills
) -> list[TailorGap]:
    """Checklist gaps a rewrite could close without inventing anything.

    A skill another baseline backs can be borrowed from it. A missing skill the fact sheet marks
    claimable can at least go on the skills line. Anything else, such as years the roles don't
    cover or a skill only listed, a rewrite can't fix.
    """
    donors = {skill.name: skill.baseline for skill in mix}
    gaps = []
    for check in checklist:
        if check.kind != "skill" or check.verdict == "met":
            continue
        if check.requirement not in _CLOSABLE_REQUIREMENTS:
            continue
        if check.name in donors:
            gaps.append(
                TailorGap(
                    name=check.name,
                    requirement=check.requirement,
                    verdict=check.verdict,
                    source="baseline",
                    baseline=donors[check.name],
                )
            )
        elif (
            check.verdict == "missing"
            and skills.technologies.get(check.name) is SkillStatus.CLAIMABLE
        ):
            gaps.append(
                TailorGap(
                    name=check.name,
                    requirement=check.requirement,
                    verdict=check.verdict,
                    source="fact_sheet",
                )
            )
    return gaps


def fact_sheet_skills(content: str | None) -> FactSheetSkills | None:
    """The fact sheet's skills table, or None when there's no fact sheet to tailor from."""
    return parse_skills_status(content) if content and content.strip() else None


def tailor_advice(
    baselines: Baselines | None,
    pick: BaselinePick | None,
    job: JobFacts,
    skills: FactSheetSkills | None,
) -> TailorAdvice:
    """Whether tailoring the suggested baseline could improve on sending it as it is."""
    variant = None
    if baselines is not None and pick is not None:
        variant = next((item for item in baselines.variants if item.id == pick.resume_id), None)
    if baselines is None or pick is None or variant is None:
        return TailorAdvice(ready=False, missing="Add a baseline on the Resumes page first.")
    # Without a fact sheet, tailoring can still borrow what the other baselines back.
    if skills is None:
        skills = FactSheetSkills(technologies={}, phrases={})
    match = score_job(baselines.profile, job, only=variant)
    checklist = match.checklist if match else []
    gaps = closable_gaps(checklist, pick.mix, skills)
    closable = {gap.name for gap in gaps}
    unfixable = [
        check.name
        for check in checklist
        if check.kind == "skill"
        and check.requirement == "required"
        and check.verdict == "missing"
        and check.name not in closable
    ]
    return TailorAdvice(ready=True, gaps=gaps, unfixable=unfixable)
