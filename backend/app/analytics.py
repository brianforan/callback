"""Turns applications and resumes into dashboard data. Pure functions, no database access."""

import uuid
from collections import Counter, defaultdict
from collections.abc import Collection, Iterable, Sequence
from datetime import date, timedelta
from statistics import median

from app.models import Application, ApplicationStatus, JobPosting, Resume
from app.postings.parser import BUILTIN, Vocabulary, requirement_overlap
from app.resumes.structure import (
    Evidence,
    ResumeStructure,
    experience_months,
    highest_degree,
    latest_resume,
    parse_resume,
    skill_evidence,
)
from app.schemas import (
    CompanyRow,
    FunnelStage,
    KeywordRow,
    KeywordTrends,
    LabelCount,
    Overview,
    PostingTrends,
    PostingWeek,
    RelatedPosting,
    ResumeEvidence,
    ResumeInsights,
    ResumePerformance,
    SkillDemand,
    SourcePerformance,
    StackCoverage,
    StackGap,
    StackRow,
    StackWeek,
    StaleApplication,
    Totals,
    WeeklyPoint,
)
from app.similarity import cosine_similarity, term_counts

_STAGES = (
    ApplicationStatus.APPLIED,
    ApplicationStatus.SCREENING,
    ApplicationStatus.INTERVIEW,
    ApplicationStatus.OFFER,
)
_STAGE_RANK = {stage.value: rank for rank, stage in enumerate(_STAGES)}
_CALLBACK_RANK = _STAGE_RANK[ApplicationStatus.SCREENING]
_INTERVIEW_RANK = _STAGE_RANK[ApplicationStatus.INTERVIEW]
_OFFER_RANK = _STAGE_RANK[ApplicationStatus.OFFER]
_ACTIVE = {
    ApplicationStatus.APPLIED.value,
    ApplicationStatus.SCREENING.value,
    ApplicationStatus.INTERVIEW.value,
}
_MAX_STALE = 8
_MAX_DEMAND = 20
_MAX_EVIDENCE_RESUMES = 6
_MAX_RELATED = 5
_MAX_STACK_ROWS = 20
_MAX_COMPANY_ROWS = 20
_REPEAT_TEXT_SIMILARITY = 0.9
_NOT_SET = "not_set"
_MAX_GAPS = 15
_COVERAGE_BUCKETS = ((0.8, "80% and up"), (0.5, "50–79%"), (0.0, "Under 50%"))
_MIN_KEYWORD_APPLICATIONS = 10
_MIN_KEYWORD_WINDOW = 3
_MIN_KEYWORD_CHANGE = 0.4
_MIN_KEYWORD_SHARE = 0.5
_MIN_KEYWORD_CHART_USES = 2
_MAX_KEYWORD_ROWS = 25
_MAX_KEYWORD_MOVERS = 15
_MAX_KEYWORD_CHART_TERMS = 5


def furthest_rank(application: Application) -> int:
    """Furthest funnel stage ever reached, so a rejection after an interview still counts."""
    return max((_STAGE_RANK.get(event.status, 0) for event in application.events), default=0)


def got_callback(application: Application) -> bool:
    return furthest_rank(application) >= _CALLBACK_RANK


def _rate(part: int, whole: int) -> float:
    return round(part / whole, 4) if whole else 0.0


def _days_to_callback(application: Application) -> int | None:
    for event in application.events:
        if _STAGE_RANK.get(event.status, 0) >= _CALLBACK_RANK:
            return max((event.occurred_at.date() - application.applied_on).days, 0)
    return None


def _week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _weekly(applications: Sequence[Application], today: date) -> list[WeeklyPoint]:
    if not applications:
        return []
    counts: Counter[date] = Counter()
    callbacks: Counter[date] = Counter()
    for application in applications:
        week = _week_start(application.applied_on)
        counts[week] += 1
        callbacks[week] += got_callback(application)

    points = []
    week = min(counts)
    last_week = max(max(counts), _week_start(today))
    while week <= last_week:
        points.append(
            WeeklyPoint(week_start=week, applications=counts[week], callbacks=callbacks[week])
        )
        week += timedelta(days=7)
    return points


def _performance(label: str, members: Sequence[Application]) -> SourcePerformance:
    """How often one group of applications got a callback."""
    callbacks = sum(got_callback(member) for member in members)
    return SourcePerformance(
        source=label,
        applications=len(members),
        callbacks=callbacks,
        callback_rate=_rate(callbacks, len(members)),
    )


def _sources(applications: Sequence[Application]) -> list[SourcePerformance]:
    groups: dict[str, list[Application]] = defaultdict(list)
    for application in applications:
        groups[application.source or "Not set"].append(application)
    rows = []
    for source, members in groups.items():
        rows.append(_performance(source, members))
    return sorted(rows, key=lambda row: -row.applications)


def _resume_performance(
    resumes: Iterable[Resume], applications: Sequence[Application]
) -> list[ResumePerformance]:
    by_resume: dict[uuid.UUID, list[Application]] = defaultdict(list)
    for application in applications:
        if application.resume_id:
            by_resume[application.resume_id].append(application)
    rows = []
    for resume in sorted(resumes, key=lambda item: item.created_at):
        members = by_resume[resume.id]
        callbacks = sum(got_callback(member) for member in members)
        rows.append(
            ResumePerformance(
                resume_id=resume.id,
                label=resume.label,
                uploaded_at=resume.created_at,
                applications=len(members),
                callbacks=callbacks,
                interviews=sum(furthest_rank(member) >= _INTERVIEW_RANK for member in members),
                callback_rate=_rate(callbacks, len(members)),
            )
        )
    return rows


def _last_contact(application: Application) -> date:
    """The application date, or the latest logged contact about it if that's later."""
    dates = [communication.occurred_on for communication in application.communications]
    return max([application.applied_on, *dates])


def _stale(
    applications: Sequence[Application], today: date, stale_after_days: int
) -> list[StaleApplication]:
    """Applications still at Applied with nothing heard or sent for a while, oldest first.

    Logged contact, such as a follow-up email or a recruiter's reply, restarts the clock.
    """
    waiting = [
        (application, _last_contact(application))
        for application in applications
        if application.status == ApplicationStatus.APPLIED.value
    ]
    waiting = [
        (application, last)
        for application, last in waiting
        if (today - last).days >= stale_after_days
    ]
    waiting.sort(key=lambda item: item[1])
    return [
        StaleApplication(
            id=application.id,
            company=application.company,
            role_title=application.role_title,
            applied_on=application.applied_on,
            days_waiting=(today - last).days,
            last_contact_on=last if last != application.applied_on else None,
        )
        for application, last in waiting[:_MAX_STALE]
    ]


def build_overview(
    applications: Sequence[Application],
    resumes: Sequence[Resume],
    today: date,
    stale_after_days: int,
) -> Overview:
    ranks = [furthest_rank(application) for application in applications]
    callbacks = sum(rank >= _CALLBACK_RANK for rank in ranks)
    response_days = [days for days in map(_days_to_callback, applications) if days is not None]
    this_week = _week_start(today)
    totals = Totals(
        applications=len(applications),
        active=sum(application.status in _ACTIVE for application in applications),
        this_week=sum(application.applied_on >= this_week for application in applications),
        callbacks=callbacks,
        callback_rate=_rate(callbacks, len(applications)),
        interviews=sum(rank >= _INTERVIEW_RANK for rank in ranks),
        offers=sum(rank >= _OFFER_RANK for rank in ranks),
        median_days_to_callback=median(response_days) if response_days else None,
    )
    funnel = [
        FunnelStage(stage=stage.value, count=sum(rank >= stage_rank for rank in ranks))
        for stage_rank, stage in enumerate(_STAGES)
    ]
    return Overview(
        totals=totals,
        funnel=funnel,
        weekly=_weekly(applications, today),
        sources=_sources(applications),
        resumes=_resume_performance(resumes, applications),
        stale=_stale(applications, today, stale_after_days),
    )


def _is_ignored(term: str, ignored: Collection[str]) -> bool:
    """A phrase counts as ignored if it, or any word in it, is ignored."""
    return term in ignored or any(word in ignored for word in term.split(" "))


_VERDICTS = {
    Evidence.EXPERIENCE: "met",
    Evidence.PROJECT: "met",
    Evidence.LISTED: "weak",
    Evidence.NONE: "missing",
}


def resume_evidence(
    resume: Resume, structure: ResumeStructure, vocabulary: Vocabulary = BUILTIN
) -> ResumeEvidence:
    """What the parser read from one resume: sections, dated roles and how each skill is backed."""
    found = {
        name: skill_evidence(structure, name, vocabulary).evidence for name in vocabulary.names
    }
    degree = highest_degree(structure)
    return ResumeEvidence(
        resume_id=resume.id,
        label=resume.label,
        uploaded_at=resume.created_at,
        sections=list(structure.sections),
        roles=len(structure.roles),
        years=round(experience_months(structure) / 12, 1) if structure.roles else None,
        degree=degree.value if degree else None,
        backed=[
            name
            for name, level in found.items()
            if level in (Evidence.EXPERIENCE, Evidence.PROJECT)
        ],
        listed_only=[name for name, level in found.items() if level is Evidence.LISTED],
    )


def build_resume_insights(
    resumes: Sequence[Resume],
    applications: Sequence[Application],
    postings: Sequence[JobPosting],
    today: date,
    vocabulary: Vocabulary = BUILTIN,
) -> ResumeInsights:
    """What postings ask for against what your latest resume backs, and what each resume shows."""
    structures = {
        resume.id: parse_resume(resume.source_tex or resume.plain_text, today) for resume in resumes
    }
    latest = latest_resume(resumes, applications)

    required_in: Counter[str] = Counter()
    named_in: Counter[str] = Counter()
    for posting in postings:
        for technology in posting.technologies:
            named_in[technology.name] += 1
            required_in[technology.name] += technology.requirement == "required"
    demand = []
    for name in sorted(named_in, key=lambda name: (-required_in[name], -named_in[name], name))[
        :_MAX_DEMAND
    ]:
        evidence = skill_evidence(structures[latest.id], name, vocabulary) if latest else None
        level = evidence.evidence if evidence else Evidence.NONE
        demand.append(
            SkillDemand(
                name=name,
                category=vocabulary.category(name),
                required_in=required_in[name],
                postings=named_in[name],
                evidence=level.value,
                months=evidence.months if evidence else 0,
                verdict=_VERDICTS[level],
            )
        )

    newest = sorted(resumes, key=lambda resume: (resume.created_at, str(resume.id)), reverse=True)
    evidence_rows = [
        resume_evidence(resume, structures[resume.id], vocabulary)
        for resume in newest[:_MAX_EVIDENCE_RESUMES]
    ]
    return ResumeInsights(
        latest_resume=latest.label if latest else None,
        postings=len(postings),
        demand=demand,
        resumes=evidence_rows,
    )


def related_postings(target: JobPosting, others: Sequence[JobPosting]) -> list[RelatedPosting]:
    """Closest other postings by shared requirements. Near-identical text marks a likely repeat."""
    target_stack = {technology.name: technology.requirement for technology in target.technologies}
    target_terms = term_counts(target.raw_text)
    related = []
    for other in others:
        if other.id == target.id:
            continue
        other_stack = {technology.name: technology.requirement for technology in other.technologies}
        overlap = requirement_overlap(target_stack, other_stack)
        likely_repeat = (
            bool(target.raw_text.strip())
            and cosine_similarity(target_terms, term_counts(other.raw_text))
            >= _REPEAT_TEXT_SIMILARITY
        )
        if not overlap and not likely_repeat:
            continue
        related.append(
            RelatedPosting(
                id=other.id,
                company=other.company,
                title=other.title,
                level=other.level,
                shared_stack=sorted(target_stack.keys() & other_stack.keys()),
                stack_overlap=round(overlap, 4),
                likely_repeat=likely_repeat,
            )
        )
    related.sort(key=lambda item: (-item.likely_repeat, -item.stack_overlap, item.company))
    return related[:_MAX_RELATED]


def _coverage_against(posting: JobPosting, resume: Resume, vocabulary: Vocabulary) -> StackCoverage:
    """Which of a posting's technologies a resume names. Absent means unnamed, not unknown."""

    def split(requirement: str) -> tuple[int, list[str]]:
        names = [t.name for t in posting.technologies if t.requirement == requirement]
        return len(names), [n for n in names if not vocabulary.in_text(n, resume.plain_text)]

    required_total, missing_required = split("required")
    nice_total, missing_nice = split("nice_to_have")
    required_matched = required_total - len(missing_required)
    return StackCoverage(
        resume_label=resume.label,
        required_total=required_total,
        required_matched=required_matched,
        missing_required=missing_required,
        nice_total=nice_total,
        nice_matched=nice_total - len(missing_nice),
        missing_nice=missing_nice,
        coverage=round(required_matched / required_total, 4) if required_total else None,
    )


def stack_coverage(posting: JobPosting, vocabulary: Vocabulary = BUILTIN) -> StackCoverage | None:
    """Compare a posting with the resume on its most recent application that has one."""
    sent = [application for application in posting.applications if application.resume]
    if not sent:
        return None
    latest = max(sent, key=lambda application: application.applied_on)
    return _coverage_against(posting, latest.resume, vocabulary)


def _first_applied(posting: JobPosting) -> date | None:
    return min((application.applied_on for application in posting.applications), default=None)


def _label_counts(values: Iterable[str | None]) -> list[LabelCount]:
    counts = Counter(value or _NOT_SET for value in values)
    rows = [LabelCount(label=label, count=count) for label, count in counts.items()]
    return sorted(rows, key=lambda row: (-row.count, row.label))


def _median_or_none(values: Sequence[float]) -> float | None:
    return median(values) if values else None


def _salary_midpoint(posting: JobPosting) -> float | None:
    if posting.salary_min is None and posting.salary_max is None:
        return None
    low = posting.salary_min if posting.salary_min is not None else posting.salary_max
    high = posting.salary_max if posting.salary_max is not None else posting.salary_min
    return (low + high) / 2


def _posting_weeks(
    applied: Sequence[tuple[JobPosting, date]],
    coverage: dict[uuid.UUID, StackCoverage | None],
    today: date,
) -> tuple[list[PostingWeek], list[StackWeek]]:
    if not applied:
        return [], []
    by_week: dict[date, list[JobPosting]] = defaultdict(list)
    for posting, first_applied in applied:
        by_week[_week_start(first_applied)].append(posting)

    weekly = []
    stack_weekly = []
    week = min(by_week)
    last_week = max(max(by_week), _week_start(today))
    while week <= last_week:
        members = by_week[week]
        years = [m.years_required for m in members if m.years_required is not None]
        salaries = [mid for mid in map(_salary_midpoint, members) if mid is not None]
        scores = _coverage_scores(members, coverage)
        weekly.append(
            PostingWeek(
                week_start=week,
                postings=len(members),
                referred=sum(member.referred for member in members),
                levels=Counter(member.level or _NOT_SET for member in members),
                median_years=_median_or_none(years),
                median_salary=_median_or_none(salaries),
                median_coverage=_median_or_none(scores),
            )
        )
        names = Counter(t.name for member in members for t in member.technologies)
        stack_weekly.append(
            StackWeek(
                week_start=week,
                shares={name: _rate(count, len(members)) for name, count in names.items()},
            )
        )
        week += timedelta(days=7)
    return weekly, stack_weekly


def _coverage_scores(
    postings: Iterable[JobPosting], coverage: dict[uuid.UUID, StackCoverage | None]
) -> list[float]:
    results = (coverage.get(posting.id) for posting in postings)
    return [result.coverage for result in results if result and result.coverage is not None]


def _stack_gaps(
    postings: Iterable[JobPosting], coverage: dict[uuid.UUID, StackCoverage | None]
) -> list[StackGap]:
    """Required technologies the resumes you sent didn't name, most often missed first."""
    gaps: dict[str, StackGap] = {}
    for posting in postings:
        result = coverage.get(posting.id)
        if result is None:
            continue
        for technology in posting.technologies:
            if technology.requirement != "required":
                continue
            gap = gaps.get(technology.name)
            if gap is None:
                gap = gaps[technology.name] = StackGap(
                    name=technology.name, category=technology.category, required_in=0, missing_in=0
                )
            gap.required_in += 1
            gap.missing_in += technology.name in result.missing_required
    missed = [gap for gap in gaps.values() if gap.missing_in]
    missed.sort(key=lambda gap: (-gap.missing_in, -gap.required_in, gap.name))
    return missed[:_MAX_GAPS]


def _coverage_callbacks(
    postings: Iterable[JobPosting], vocabulary: Vocabulary
) -> list[SourcePerformance]:
    """Callback rate by coverage, scoring each application against the resume it sent."""
    buckets: dict[str, list[Application]] = defaultdict(list)
    for posting in postings:
        for application in posting.applications:
            if not application.resume:
                continue
            score = _coverage_against(posting, application.resume, vocabulary).coverage
            if score is None:
                continue
            label = next(name for floor, name in _COVERAGE_BUCKETS if score >= floor)
            buckets[label].append(application)
    rows = []
    for _, label in _COVERAGE_BUCKETS:
        members = buckets.get(label)
        if not members:
            continue
        rows.append(_performance(label, members))
    return rows


def _recommendation_rows(postings: Iterable[JobPosting]) -> list[SourcePerformance]:
    """Callback rate when the resume sent was the suggested one, and when it wasn't.

    Only applications logged with both a suggestion and a resume count.
    """
    followed: dict[bool, list[Application]] = {True: [], False: []}
    for posting in postings:
        for application in posting.applications:
            if application.recommended_resume_id is None or application.resume_id is None:
                continue
            followed[application.resume_id == application.recommended_resume_id].append(application)
    rows = []
    for label, was_followed in (("Sent the suggested resume", True), ("Sent another", False)):
        members = followed[was_followed]
        if not members:
            continue
        rows.append(_performance(label, members))
    return rows


def _stack_rows(applied: Sequence[tuple[JobPosting, date]]) -> list[StackRow]:
    rows: dict[str, StackRow] = {}
    for posting, first_applied in applied:
        for technology in posting.technologies:
            row = rows.get(technology.name)
            if row is None:
                row = rows[technology.name] = StackRow(
                    name=technology.name,
                    category=technology.category,
                    postings=0,
                    share=0.0,
                    required=0,
                    first_seen=first_applied,
                    last_seen=first_applied,
                )
            row.postings += 1
            row.required += technology.requirement == "required"
            row.first_seen = min(row.first_seen, first_applied)
            row.last_seen = max(row.last_seen, first_applied)
    for row in rows.values():
        row.share = _rate(row.postings, len(applied))
    ordered = sorted(rows.values(), key=lambda row: (-row.postings, row.name))
    return ordered[:_MAX_STACK_ROWS]


def _company_rows(postings: Sequence[JobPosting]) -> list[CompanyRow]:
    groups: dict[str, list[JobPosting]] = defaultdict(list)
    for posting in postings:
        groups[posting.company_key].append(posting)
    rows = []
    for members in groups.values():
        latest = max(members, key=lambda member: member.seen_on)
        applied_on = [a.applied_on for member in members for a in member.applications]
        rows.append(
            CompanyRow(
                company=latest.company,
                postings=len(members),
                applications=len(applied_on),
                last_applied=max(applied_on, default=None),
            )
        )
    rows.sort(key=lambda row: (-row.applications, -row.postings, row.company))
    return rows[:_MAX_COMPANY_ROWS]


def _referral_rows(postings: Sequence[JobPosting]) -> list[SourcePerformance]:
    rows = []
    for label, referred in (("Referred", True), ("Not referred", False)):
        members = [a for p in postings if p.referred is referred for a in p.applications]
        if not members:
            continue
        rows.append(_performance(label, members))
    return rows


def build_posting_trends(
    postings: Sequence[JobPosting], today: date, vocabulary: Vocabulary = BUILTIN
) -> PostingTrends:
    """Trends cover postings you applied to, dated by the first application to each."""
    applied = sorted(
        (
            (posting, first_applied)
            for posting in postings
            if (first_applied := _first_applied(posting)) is not None
        ),
        key=lambda pair: pair[1],
    )
    applied_postings = [posting for posting, _ in applied]
    coverage = {posting.id: stack_coverage(posting, vocabulary) for posting in applied_postings}
    scores = _coverage_scores(applied_postings, coverage)
    weekly, stack_weekly = _posting_weeks(applied, coverage, today)
    return PostingTrends(
        total_postings=len(postings),
        applied_postings=len(applied),
        levels=_label_counts(posting.level for posting, _ in applied),
        role_families=_label_counts(posting.role_family for posting, _ in applied),
        source_kinds=_label_counts(posting.source_kind for posting, _ in applied),
        referrals=_referral_rows(postings),
        weekly=weekly,
        stack=_stack_rows(applied),
        stack_weekly=stack_weekly,
        companies=_company_rows(postings),
        scored_postings=len(scores),
        median_coverage=_median_or_none(scores),
        gaps=_stack_gaps(applied_postings, coverage),
        coverage_callbacks=_coverage_callbacks(applied_postings, vocabulary),
        recommendation_callbacks=_recommendation_rows(applied_postings),
    )


def _keyword_rows(
    sent: Sequence[Application], counts: dict[uuid.UUID, Counter[str]]
) -> list[KeywordRow]:
    """One row per term across the applications sent, in date order."""
    windowed = len(sent) >= _MIN_KEYWORD_APPLICATIONS
    window = max(_MIN_KEYWORD_WINDOW, len(sent) // 3)
    early = sent[:window]
    recent = sent[-window:]

    def share(term: str, group: Sequence[Application]) -> float:
        return _rate(sum(term in counts[item.resume_id] for item in group), len(group))

    rows: dict[str, KeywordRow] = {}
    for application in sent:
        for term, mentions in counts[application.resume_id].items():
            row = rows.get(term)
            if row is None:
                row = rows[term] = KeywordRow(
                    term=term,
                    applications=0,
                    share=0.0,
                    mentions=0,
                    first_used=application.applied_on,
                    last_used=application.applied_on,
                    early_share=share(term, early) if windowed else None,
                    recent_share=share(term, recent) if windowed else None,
                )
            row.applications += 1
            row.mentions += mentions
            row.last_used = max(row.last_used, application.applied_on)
    for row in rows.values():
        row.share = _rate(row.applications, len(sent))
    return sorted(rows.values(), key=lambda row: (-row.applications, -row.mentions, row.term))


def _keyword_weeks(
    sent: Sequence[Application],
    counts: dict[uuid.UUID, Counter[str]],
    rows: Sequence[KeywordRow],
    today: date,
) -> tuple[list[str], list[StackWeek]]:
    """Weekly shares for the words whose share moved most. Empty weeks have no shares."""
    by_week: dict[date, list[Application]] = defaultdict(list)
    for application in sent:
        by_week[_week_start(application.applied_on)].append(application)
    weeks = []
    week = min(by_week)
    last_week = max(max(by_week), _week_start(today))
    while week <= last_week:
        weeks.append(week)
        week += timedelta(days=7)

    def share_in(term: str, members: Sequence[Application]) -> float:
        return _rate(sum(term in counts[item.resume_id] for item in members), len(members))

    used_weeks = [members for members in by_week.values() if members]
    moves = {}
    for row in rows:
        if row.applications < _MIN_KEYWORD_CHART_USES:
            continue
        shares = [share_in(row.term, members) for members in used_weeks]
        moves[row.term] = max(shares) - min(shares)
    # Rows are already ordered by use, and this sort is stable, so ties keep that order.
    terms = sorted(moves, key=lambda term: -moves[term])[:_MAX_KEYWORD_CHART_TERMS]

    points = []
    for week in weeks:
        members = by_week.get(week, [])
        shares = {term: share_in(term, members) for term in terms} if members else {}
        points.append(StackWeek(week_start=week, shares=shares))
    return terms, points


def build_keyword_trends(
    resumes: Sequence[Resume],
    applications: Sequence[Application],
    ignored: Collection[str],
    today: date,
    vocabulary: Vocabulary = BUILTIN,
) -> KeywordTrends:
    """How often each known skill appears in the resumes you sent, across applications over time.

    Only dictionary skills count, so ordinary words like "team" never crowd out a technology.
    """
    counts = {
        resume.id: Counter(
            {
                term: count
                for term, count in vocabulary.mentions(resume.plain_text).items()
                if not _is_ignored(term.lower(), ignored)
            }
        )
        for resume in resumes
    }
    sent = sorted(
        (application for application in applications if application.resume_id in counts),
        key=lambda application: (application.applied_on, application.created_at),
    )
    base = KeywordTrends(
        applications=len(sent),
        min_applications=_MIN_KEYWORD_APPLICATIONS,
        most_used=[],
        faded=[],
        rising=[],
        chart_terms=[],
        weekly=[],
        ignored=sorted(ignored),
    )
    if not sent:
        return base

    rows = _keyword_rows(sent, counts)
    change = {
        row.term: row.recent_share - row.early_share
        for row in rows
        if row.early_share is not None and row.recent_share is not None
    }
    faded = [
        row
        for row in rows
        if change.get(row.term, 0) <= -_MIN_KEYWORD_CHANGE and row.early_share >= _MIN_KEYWORD_SHARE
    ]
    rising = [
        row
        for row in rows
        if change.get(row.term, 0) >= _MIN_KEYWORD_CHANGE and row.recent_share >= _MIN_KEYWORD_SHARE
    ]
    faded.sort(key=lambda row: change[row.term])
    rising.sort(key=lambda row: -change[row.term])
    chart_terms, weekly = _keyword_weeks(sent, counts, rows, today)
    return base.model_copy(
        update={
            "most_used": rows[:_MAX_KEYWORD_ROWS],
            "faded": faded[:_MAX_KEYWORD_MOVERS],
            "rising": rising[:_MAX_KEYWORD_MOVERS],
            "chart_terms": chart_terms,
            "weekly": weekly,
        }
    )
