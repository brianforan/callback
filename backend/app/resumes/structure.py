"""Pure functions that read a LaTeX resume's structure: sections, dated roles and skill evidence.

An ATS keyword search treats a skill in a dated role differently from one only in a skills list,
so evidence is graded by where a skill appears, and dated roles give months of use.
"""

import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from app.models import Application, Resume
from app.postings.parser import BUILTIN, CLEARANCE, DegreeLevel, Vocabulary, degree_levels_in
from app.similarity import DOCUMENT_BODY, LATEX_COMMENT, tex_to_text

_SECTION = re.compile(r"\\section\*?\{([^{}]*)\}")
_MONTH_NAMES = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


def _month(prefix: str) -> str:
    names = "|".join(_MONTH_NAMES)
    return (
        rf"(?:\b(?P<{prefix}_name>{names})[a-z]*\.?\s+|(?P<{prefix}_number>\d{{1,2}})\s*/\s*)?"
        rf"(?P<{prefix}_year>(?:19|20)\d{{2}})"
    )


# "Jan 2022 -- Dec 2024", "03/2020 – Present", "2017 to 2019". LaTeX dashes are still raw here.
_DATE_RANGE = re.compile(
    _month("start")
    + r"\s*(?:-{1,3}|–|—|to|\\textendash|\\textemdash|\$-\$)\s*"
    + rf"(?:{_month('end')}|(?P<ongoing>present|current|now|today))",
    re.I,
)
# A role's heading is the date line plus up to this many lines just above it.
_MAX_HEADING_LINES = 2
_HEADING_STOP = re.compile(r"\\item\b|\\(?:begin|end)\{|^\s*$")

_EXPERIENCE = re.compile(r"experience|employment|work history|career", re.I)
_PROJECTS = re.compile(r"project", re.I)
_EDUCATION = re.compile(r"education|academic", re.I)


class Evidence(StrEnum):
    # Named in a dated role, so it has months of use.
    EXPERIENCE = "experience"
    # Named in a project or an undated entry: backed by work, but with no dates.
    PROJECT = "project"
    # Named only in a skills list, summary or header.
    LISTED = "listed"
    NONE = "none"


@dataclass(frozen=True)
class Role:
    heading: str
    # Months counted from year 0, so intervals subtract cleanly. Both ends are inclusive.
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class ResumeStructure:
    sections: tuple[str, ...]
    roles: tuple[Role, ...]
    project_text: str
    listed_text: str
    education_text: str
    full_text: str


@dataclass(frozen=True)
class SkillEvidence:
    name: str
    evidence: Evidence
    months: int
    roles: int
    last_used: date | None


def _month_index(year: int, month: int) -> int:
    return year * 12 + month - 1


def month_to_date(index: int) -> date:
    return date(index // 12, index % 12 + 1, 1)


def _range_months(match: re.Match[str], today: date) -> tuple[int, int] | None:
    def side(prefix: str, default_month: int) -> int:
        name = match.group(f"{prefix}_name")
        number = match.group(f"{prefix}_number")
        month = _MONTH_NAMES[name[:3].lower()] if name else int(number) if number else default_month
        return _month_index(int(match.group(f"{prefix}_year")), min(max(month, 1), 12))

    now = _month_index(today.year, today.month)
    start = side("start", 1)
    end = now if match.group("ongoing") else side("end", 12)
    end = min(end, now)
    if start > end:
        return None
    return start, end


def _roles(section: str, today: date) -> tuple[list[Role], list[str]]:
    """Split an experience section at each date range. Text before the first role is returned."""
    lines = section.splitlines(keepends=True)

    # Each role is (first heading line, date line, date match).
    starts: list[tuple[int, int, re.Match[str]]] = []
    for index, line in enumerate(lines):
        match = _DATE_RANGE.search(line)
        if match is None:
            continue
        first = index
        while (
            first > 0
            and index - first < _MAX_HEADING_LINES
            and not _HEADING_STOP.search(lines[first - 1])
            and (not starts or first - 1 > starts[-1][1])
        ):
            first -= 1
        starts.append((first, index, match))

    if not starts:
        return [], [section]
    roles = []
    for position, (first, date_line, match) in enumerate(starts):
        span = _range_months(match, today)
        if span is None:
            continue
        last = starts[position + 1][0] if position + 1 < len(starts) else len(lines)
        heading = "".join(lines[first : date_line + 1]).replace(match.group(0), " ", 1)
        chunk = "".join(lines[first:last]).replace(match.group(0), " ", 1)
        roles.append(
            Role(heading=tex_to_text(heading), start=span[0], end=span[1], text=tex_to_text(chunk))
        )
    return roles, ["".join(lines[: starts[0][0]])]


def parse_resume(tex: str, today: date) -> ResumeStructure:
    """Read sections by their \\section headings. A resume with none is all listed text."""
    uncommented = LATEX_COMMENT.sub("", tex)
    body_match = DOCUMENT_BODY.search(uncommented)
    body = body_match.group(1) if body_match else uncommented

    headings = list(_SECTION.finditer(body))
    parts = [("", body[: headings[0].start()] if headings else body)]
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(body)
        parts.append((heading.group(1).strip(), body[heading.end() : end]))

    roles: list[Role] = []
    project: list[str] = []
    listed: list[str] = []
    education: list[str] = []
    for name, text in parts:
        if name and _EXPERIENCE.search(name):
            found, rest = _roles(text, today)
            roles.extend(found)
            # An experience section with no dates is still work, just without months.
            (project if not found else listed).extend(rest)
        elif name and _PROJECTS.search(name):
            project.append(text)
        elif name and _EDUCATION.search(name):
            education.append(text)
            listed.append(text)
        else:
            listed.append(text)

    return ResumeStructure(
        sections=tuple(name for name, _ in parts if name),
        roles=tuple(roles),
        project_text=tex_to_text("\n".join(project)),
        listed_text=tex_to_text("\n".join(listed)),
        education_text=tex_to_text("\n".join(education)),
        full_text=tex_to_text(body),
    )


def _merged_months(spans: Sequence[tuple[int, int]]) -> int:
    """Total months covered, so overlapping roles aren't counted twice."""
    total = 0
    current: tuple[int, int] | None = None
    for start, end in sorted(spans):
        if current is None or start > current[1] + 1:
            if current is not None:
                total += current[1] - current[0] + 1
            current = (start, end)
        else:
            current = (current[0], max(current[1], end))
    if current is not None:
        total += current[1] - current[0] + 1
    return total


def experience_months(structure: ResumeStructure) -> int:
    return _merged_months([(role.start, role.end) for role in structure.roles])


def skill_evidence(
    structure: ResumeStructure, name: str, vocabulary: Vocabulary = BUILTIN
) -> SkillEvidence:
    roles = [role for role in structure.roles if vocabulary.in_text(name, role.text)]
    if roles:
        return SkillEvidence(
            name=name,
            evidence=Evidence.EXPERIENCE,
            months=_merged_months([(role.start, role.end) for role in roles]),
            roles=len(roles),
            last_used=month_to_date(max(role.end for role in roles)),
        )
    if vocabulary.in_text(name, structure.project_text):
        evidence = Evidence.PROJECT
    elif vocabulary.in_text(name, structure.listed_text):
        evidence = Evidence.LISTED
    else:
        evidence = Evidence.NONE
    return SkillEvidence(name=name, evidence=evidence, months=0, roles=0, last_used=None)


def highest_degree(structure: ResumeStructure) -> DegreeLevel | None:
    levels = degree_levels_in(structure.education_text or structure.full_text)
    return levels[-1] if levels else None


def mentions_clearance(structure: ResumeStructure) -> bool:
    return CLEARANCE.search(structure.full_text) is not None


def latest_resume(resumes: Sequence[Resume], applications: Sequence[Application]) -> Resume | None:
    """The resume on your newest application, or the newest upload if none has been sent."""
    if not resumes:
        return None
    by_id: dict[uuid.UUID, Resume] = {resume.id: resume for resume in resumes}
    sent = [application for application in applications if application.resume_id in by_id]
    if sent:
        newest = max(sent, key=lambda item: (item.applied_on, item.created_at, str(item.id)))
        return by_id[newest.resume_id]
    return max(resumes, key=lambda resume: (resume.created_at, str(resume.id)))
