from datetime import date

import pytest

from app.postings.parser import (
    DegreeLevel,
    parse_requirements,
    requirement_overlap,
    technology_mentions,
)
from app.resumes.structure import (
    Evidence,
    experience_months,
    highest_degree,
    mentions_clearance,
    parse_resume,
    skill_evidence,
)

TODAY = date(2026, 11, 2)

RESUME = r"""
\documentclass{article}
\newcommand{\job}[3]{\textbf{#1}\hfill\textit{#3}\par\textit{#2}}
\begin{document}
{\large Senior Software Engineer \textbar{} Backend, AWS}
\section*{Technical Skills}
\textbf{Languages:} Python, PHP, Go\\[0.08em]
\textbf{Tools:} Docker, Git
\section*{Professional Experience}
\job{Software Engineer}{Example Corp \quad|\quad Remote}
{Jan 2025 -- Present}
\begin{itemize}
  \item Built a Laravel REST API in PHP, with jobs on
  Redis queues.
\end{itemize}

\job{Software Engineer}{Sample Media \quad|\quad Remote}
{Jan 2022 -- Dec 2024}
\begin{itemize}
  \item Designed a serverless AWS pipeline in Python.
\end{itemize}

\job{Ops Engineer}{Test Company}
{06/2018 – 2020}
\begin{itemize}
  \item Python tools and a React dashboard.
\end{itemize}
\section*{Projects}
A Kafka consumer, written for fun.
\section*{Education}
\textbf{Example University} \quad|\quad Bachelor's in Computer Science \hfill 2017
\end{document}
"""


@pytest.fixture
def structure():
    return parse_resume(RESUME, TODAY)


def test_sections_and_dated_roles_are_read(structure):
    assert structure.sections == (
        "Technical Skills",
        "Professional Experience",
        "Projects",
        "Education",
    )
    assert [role.heading for role in structure.roles] == [
        "Software Engineer Example Corp | Remote",
        "Software Engineer Sample Media | Remote",
        "Ops Engineer Test Company",
    ]
    first, _, last = structure.roles
    assert (first.start, first.end) == (2025 * 12, 2026 * 12 + 10)
    # A year alone runs January to December, and a numeric month is read as one.
    assert (last.start, last.end) == (2018 * 12 + 5, 2020 * 12 + 11)
    assert "Redis" in first.text


def test_experience_months_merge_overlaps_and_end_at_today(structure):
    # Jun 2018 – Dec 2020 is 31 months, Jan 2022 – Nov 2026 is 59.
    assert experience_months(structure) == 31 + 59


def test_evidence_is_graded_by_where_a_skill_appears(structure):
    python = skill_evidence(structure, "Python")
    assert python.evidence is Evidence.EXPERIENCE
    assert python.roles == 2
    assert python.months == 31 + 36
    assert python.last_used == date(2024, 12, 1)
    assert skill_evidence(structure, "PHP").last_used == date(2026, 11, 1)
    assert skill_evidence(structure, "Kafka").evidence is Evidence.PROJECT
    assert skill_evidence(structure, "Go").evidence is Evidence.LISTED
    # The headline above the first section counts as listed, not as a role.
    assert skill_evidence(structure, "Docker").evidence is Evidence.LISTED
    assert skill_evidence(structure, "Rust").evidence is Evidence.NONE


def test_degree_and_clearance(structure):
    assert highest_degree(structure) is DegreeLevel.BACHELOR
    assert mentions_clearance(structure) is False
    cleared = parse_resume(r"\begin{document}Active TS/SCI clearance\end{document}", TODAY)
    assert mentions_clearance(cleared) is True


def test_a_resume_without_sections_is_all_listed():
    plain = parse_resume(r"\begin{document}Python and Rust\end{document}", TODAY)
    assert plain.roles == ()
    assert skill_evidence(plain, "Python").evidence is Evidence.LISTED


def test_an_experience_section_without_dates_is_project_evidence():
    tex = r"""\begin{document}
\section*{Experience}
\begin{itemize}\item Python at a startup\end{itemize}
\end{document}"""
    assert skill_evidence(parse_resume(tex, TODAY), "Python").evidence is Evidence.PROJECT


POSTING = """About us
Founded by PhDs. We consider applicants without regard to citizenship status.
Requirements
- 5+ years of experience in software engineering
- 3+ years of hands-on Python
- Kubernetes (2+ years)
- 4 years of backend experience with Go, Rust or Java
- BS/MS in Computer Science or equivalent experience
- Ability to obtain a security clearance
Nice to have
- Master's degree
"""


def test_requirements_beyond_the_stack():
    requirements = parse_requirements(POSTING)
    # Years attach only when the line ties them to one technology directly.
    assert requirements.skill_years == (("Kubernetes", 2), ("Python", 3))
    assert requirements.years_for("Go") is None
    assert requirements.degree.level is DegreeLevel.BACHELOR
    assert requirements.degree.required is True
    assert requirements.degree.equivalent_ok is True
    assert requirements.clearance is True
    # The equal-opportunity line names citizenship but doesn't require it.
    assert requirements.citizenship is False


def test_degree_only_preferred_and_citizenship_required():
    requirements = parse_requirements(
        "Requirements\n- Python\n- Must be a U.S. citizen\n"
        "Preferred\n- Master's degree in a technical field\n"
    )
    assert requirements.degree.level is DegreeLevel.MASTER
    assert requirements.degree.required is False
    assert requirements.citizenship is True
    assert requirements.clearance is False
    assert parse_requirements("Requirements\n- MS Office\n").degree is None


def test_technology_mentions_count_known_skills_only():
    counts = technology_mentions("Python, python and Kafka with a great team")
    assert counts == {"Python": 2, "Kafka": 1}


def test_requirement_overlap_weighs_required_more():
    assert requirement_overlap({"Python": "required"}, {"Python": "required"}) == 1
    assert requirement_overlap({"Python": "required"}, {"Python": "mentioned"}) == 0.5
    assert requirement_overlap({"Python": "required"}, {"Rust": "required"}) == 0
    assert requirement_overlap({}, {}) == 0


def test_go_as_its_own_bullet_counts_but_not_as_a_verb():
    assert technology_mentions("Nice to have\n- Go\n- Terraform") == {"Go": 1, "Terraform": 1}
    assert technology_mentions("Ready to go above and beyond") == {}
    assert parse_requirements("Requirements\n- 3+ years of Go\n").skill_years == (("Go", 3),)
