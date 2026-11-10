"""Pure rule-based parser that turns a job posting's text into normalized fields."""

import hashlib
import json
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlsplit, urlunsplit

from app.models import EmploymentType, PostingLevel, Requirement, RoleFamily, SourceKind, WorkMode

_BEFORE = r"(?<![\w+#.])"
_AFTER = r"(?![\w+#])"


def _any_case(pattern: str) -> str:
    return rf"(?i:{_BEFORE}(?:{pattern}){_AFTER})"


def _exact_case(pattern: str) -> str:
    return rf"{_BEFORE}(?:{pattern}){_AFTER}"


# Ordinary English words (React, Go, Rust, Spring, Swift) only match in their capitalized form.
_TECHNOLOGIES: tuple[tuple[str, str, str], ...] = (
    ("Python", "language", _any_case("python")),
    ("TypeScript", "language", _any_case("typescript")),
    ("JavaScript", "language", _any_case("javascript|ecmascript")),
    ("Java", "language", _exact_case("Java")),
    ("Kotlin", "language", _any_case("kotlin")),
    (
        "Go",
        "language",
        _any_case("golang")
        # Lookbehinds, so the match starts at "Go" itself, as "3+ years of Go" needs.
        + r"|(?:(?<=\bin )|(?<=\bof )|(?<=\bor )|(?<=\band )|(?<=\bwith )|(?<=[,/(])|(?<=[,/(] ))"
        + r"Go\b(?!\s+(?:to|above|beyond))"
        + r"|\bGo(?=\s*[,/)])"
        # Before a role word, as in a "Senior Go Engineer" title or "Go services".
        + r"|\bGo(?=\s+(?i:engineers?|developers?|programmers?|backend|(?:micro)?services?)\b)"
        # A bullet that is just "Go", as in a requirements list.
        + r"|(?m:^\s*(?:[-•·‣*]\s*)?Go\s*$)",
    ),
    ("Rust", "language", _exact_case("Rust")),
    ("Ruby", "language", _exact_case("Ruby")),
    ("PHP", "language", _any_case("php")),
    ("C#", "language", _any_case(r"c#")),
    ("C++", "language", _any_case(r"c\+\+")),
    (".NET", "language", r"(?i:(?<![\w.])(?:asp)?\.net\b)"),
    ("Swift", "language", _exact_case("Swift")),
    ("Scala", "language", _exact_case("Scala")),
    ("SQL", "language", _exact_case("SQL")),
    (
        "React",
        "frontend",
        _exact_case(r"React(?:\.?js)?(?! Native)") + "|" + _any_case(r"react\.?js"),
    ),
    ("Vue", "frontend", _any_case(r"vue(?:\.?js)?")),
    ("Angular", "frontend", _any_case("angular(?:js)?")),
    ("Next.js", "frontend", _any_case(r"next\.?js")),
    ("Svelte", "frontend", _any_case("svelte")),
    ("Tailwind", "frontend", _any_case(r"tailwind(?:\s*css)?")),
    ("Redux", "frontend", _any_case("redux")),
    ("Node.js", "backend", _any_case(r"node\.?js") + "|" + _exact_case("Node")),
    ("Express", "backend", _any_case(r"express\.?js")),
    ("NestJS", "backend", _any_case(r"nest\.?js")),
    ("Django", "backend", _any_case("django")),
    ("Flask", "backend", _any_case("flask")),
    ("FastAPI", "backend", _any_case("fastapi")),
    ("Rails", "backend", _any_case("ruby on rails") + "|" + _exact_case("Rails")),
    ("Spring", "backend", _exact_case("Spring(?: Boot)?")),
    ("Laravel", "backend", _any_case("laravel")),
    ("GraphQL", "api", _any_case("graphql")),
    ("REST", "api", _exact_case("REST(?:ful)?") + "|" + _any_case("restful")),
    ("Kafka", "messaging", _any_case("kafka")),
    ("RabbitMQ", "messaging", _any_case("rabbitmq")),
    ("PostgreSQL", "database", _any_case("postgres(?:ql)?")),
    ("MySQL", "database", _any_case("mysql")),
    ("MongoDB", "database", _any_case("mongo(?:db)?")),
    ("DynamoDB", "database", _any_case("dynamodb")),
    ("Redis", "database", _any_case("redis")),
    ("Elasticsearch", "database", _any_case(r"elastic\s?search")),
    ("Snowflake", "database", _exact_case("Snowflake")),
    ("AWS", "cloud", _exact_case("AWS") + "|" + _any_case("amazon web services")),
    ("Azure", "cloud", _exact_case("Azure")),
    ("GCP", "cloud", _exact_case("GCP") + "|" + _any_case("google cloud")),
    ("Docker", "infrastructure", _any_case("docker")),
    ("Kubernetes", "infrastructure", _any_case("kubernetes|k8s")),
    ("Terraform", "infrastructure", _any_case("terraform")),
    ("Linux", "infrastructure", _any_case("linux")),
    ("Spark", "data", _any_case("apache spark") + "|" + _exact_case("Spark")),
    ("Airflow", "data", _any_case("airflow")),
    ("dbt", "data", _any_case("dbt")),
    ("PyTorch", "machine_learning", _any_case("pytorch")),
    ("TensorFlow", "machine_learning", _any_case("tensorflow")),
    ("React Native", "mobile", _any_case("react native")),
    ("Flutter", "mobile", _any_case("flutter")),
    ("Android", "mobile", _exact_case("Android")),
    ("iOS", "mobile", _exact_case("iOS")),
    ("Elixir", "language", _any_case("elixir")),
    ("Elm", "language", _exact_case("Elm")),
    ("Haskell", "language", _any_case("haskell")),
    ("Clojure", "language", _any_case("clojure")),
    ("Perl", "language", _exact_case("Perl")),
    ("Bash", "language", _any_case(r"bash|shell scripting")),
    ("Objective-C", "language", _any_case("objective-c")),
    ("HTML", "frontend", _exact_case("HTML5?")),
    ("CSS", "frontend", _exact_case("CSS3?")),
    ("Sass", "frontend", _any_case("sass|scss")),
    ("jQuery", "frontend", _any_case("jquery")),
    ("Webpack", "frontend", _any_case("webpack")),
    ("Vite", "frontend", _exact_case("Vite")),
    ("Nuxt", "frontend", _any_case(r"nuxt(?:\.?js)?")),
    ("Celery", "backend", _any_case("celery")),
    ("SQLAlchemy", "backend", _any_case("sqlalchemy")),
    ("gRPC", "api", _any_case("grpc")),
    ("OpenAPI", "api", _any_case("openapi|swagger")),
    ("SQS", "messaging", _exact_case("SQS")),
    ("SQLite", "database", _any_case("sqlite")),
    ("SQL Server", "database", _any_case(r"sql server|mssql|t-sql")),
    ("Oracle", "database", _exact_case("Oracle")),
    ("Cassandra", "database", _any_case("cassandra")),
    ("BigQuery", "database", _any_case("bigquery")),
    ("Redshift", "database", _any_case("redshift")),
    ("Memcached", "database", _any_case("memcached?")),
    ("AWS Lambda", "cloud", _any_case(r"aws lambda") + "|" + _exact_case("Lambda")),
    ("S3", "cloud", _exact_case("S3")),
    ("CloudWatch", "cloud", _any_case("cloudwatch")),
    ("Serverless", "cloud", _any_case("serverless")),
    ("Helm", "infrastructure", _exact_case("Helm")),
    ("Ansible", "infrastructure", _any_case("ansible")),
    ("Nginx", "infrastructure", _any_case("nginx")),
    ("Prometheus", "observability", _exact_case("Prometheus")),
    ("Grafana", "observability", _any_case("grafana")),
    ("Datadog", "observability", _any_case("datadog")),
    (
        "CI/CD",
        "tooling",
        _any_case(r"ci\s?/\s?cd|continuous (?:integration|delivery|deployment)"),
    ),
    ("GitHub Actions", "tooling", _any_case("github actions")),
    ("Jenkins", "tooling", _any_case("jenkins")),
    ("Git", "tooling", _exact_case("Git") + "|" + _any_case("github|gitlab")),
    ("Jest", "testing", _exact_case("Jest")),
    ("Cypress", "testing", _exact_case("Cypress")),
    ("Playwright", "testing", _any_case("playwright")),
    ("Selenium", "testing", _any_case("selenium")),
    ("pytest", "testing", _any_case("pytest")),
    ("PHPUnit", "testing", _any_case("phpunit")),
    ("Pandas", "data", _any_case("pandas")),
    ("NumPy", "data", _any_case("numpy")),
    ("Hadoop", "data", _any_case("hadoop")),
)
# A user's own term counts as a skill. Built-in ones keep their category (language, framework...).
TERM_CATEGORY = "skill"
# A spelling this short, written in capitals ("AP", "CPA"), only matches in capitals.
_CAPS_ONLY_LENGTH = 4
# A spelling this long that ends in a letter also matches with "s" or "es" added.
_PLURAL_MIN_LENGTH = 5
_SPELLING_GAP = re.compile(r"[\s-]+")


@dataclass(frozen=True)
class SkillTerm:
    name: str
    category: str
    pattern: re.Pattern[str]


class Vocabulary:
    """The skills a parse looks for: the built-in software list, a user's own terms, or both.

    key is the same for the same terms, so a parse can be cached per vocabulary. version is the
    owner's vocabulary_version, stamped on postings parsed with it.
    """

    def __init__(self, terms: Iterable[SkillTerm], key: str, version: int = 0) -> None:
        self.terms = tuple(terms)
        self.key = key
        self.version = version
        self.names = tuple(sorted(term.name for term in self.terms))
        self._by_name = {term.name.lower(): term for term in self.terms}

    def term(self, name: str) -> SkillTerm | None:
        return self._by_name.get(name.strip().lower())

    def category(self, name: str) -> str:
        term = self.term(name)
        return term.category if term else "other"

    def pattern(self, name: str) -> re.Pattern[str]:
        """The term's pattern, or its exact name when it isn't one of the terms."""
        term = self.term(name)
        return term.pattern if term else re.compile(_any_case(re.escape(name.strip().lower())))

    def in_text(self, name: str, text: str) -> bool:
        return self.pattern(name).search(text) is not None

    def canonical(self, name: str) -> str:
        """The vocabulary's name for a skill typed by hand, so "postgres" is "PostgreSQL".

        A name the vocabulary doesn't know is kept as typed.
        """
        cleaned = name.strip()
        if (term := self.term(cleaned)) is not None:
            return term.name
        # Names like "Node" only match capitalized, so a lowercase entry is tried that way too.
        for candidate in (cleaned, cleaned[:1].upper() + cleaned[1:]):
            for term in self.terms:
                if term.pattern.fullmatch(candidate):
                    return term.name
        return cleaned

    def mentions(self, text: str) -> Counter[str]:
        """How many times each term appears in a text."""
        counts: Counter[str] = Counter()
        for term in self.terms:
            found = sum(1 for _ in term.pattern.finditer(text))
            if found:
                counts[term.name] = found
        return counts


BUILTIN = Vocabulary(
    (SkillTerm(name, category, re.compile(pattern)) for name, category, pattern in _TECHNOLOGIES),
    key="builtin",
)
TECHNOLOGY_NAMES = BUILTIN.names


def _spelling_pattern(spelling: str) -> str:
    """One way of writing a term: spaces and hyphens alike, capitals kept only for a short one."""
    words = [re.escape(word) for word in _SPELLING_GAP.split(spelling.strip()) if word]
    body = r"[\s-]+".join(words)
    if len(spelling) >= _PLURAL_MIN_LENGTH and spelling[-1].isalpha():
        body += "(?:e?s)?"
    if len(spelling) <= _CAPS_ONLY_LENGTH and spelling.isupper():
        return _exact_case(body)
    return _any_case(body)


def term_pattern(name: str, aliases: Sequence[str] = ()) -> str:
    """The pattern for a user's term. One with a built-in name keeps the built-in pattern."""
    builtin = BUILTIN.term(name)
    parts = [builtin.pattern.pattern if builtin else _spelling_pattern(name)]
    parts += [_spelling_pattern(alias) for alias in aliases if alias.strip()]
    return "|".join(parts)


def build_vocabulary(
    software: bool, terms: Iterable[tuple[str, Sequence[str]]], version: int = 0
) -> Vocabulary:
    """The built-in list when software is on, plus each (name, aliases) term.

    The same name from two profiles is one term with both profiles' aliases.
    """
    merged: dict[str, tuple[str, list[str]]] = {}
    for name, aliases in terms:
        cleaned = name.strip()
        if not cleaned:
            continue
        builtin = BUILTIN.term(cleaned)
        _, known = merged.setdefault(cleaned.lower(), (builtin.name if builtin else cleaned, []))
        for alias in aliases:
            if alias not in known:
                known.append(alias)
    if software and not merged:
        return Vocabulary(BUILTIN.terms, BUILTIN.key, version)
    chosen = {term.name.lower(): term for term in BUILTIN.terms} if software else {}
    for key, (shown, aliases) in merged.items():
        builtin = BUILTIN.term(key)
        chosen[key] = SkillTerm(
            shown,
            builtin.category if builtin else TERM_CATEGORY,
            re.compile(term_pattern(shown, aliases)),
        )
    spec = json.dumps(
        [software, sorted([key, sorted(aliases)] for key, (_, aliases) in merged.items())]
    )
    return Vocabulary(chosen.values(), hashlib.sha256(spec.encode()).hexdigest()[:16], version)


_MAX_HEADING_LENGTH = 60
# A leading "**" is bold text rather than a bullet.
_BULLET = re.compile(r"^\s*(?:[-•·‣]|\*(?!\*))")
# Markdown headings and fully bold lines are headings whatever they say.
_STYLED_HEADING = re.compile(r"^(?:#+\s|\*\*.*\*\*$)")
_INLINE_NICE = re.compile(r"\b(?:is|are)(?: considered)? a (?:plus|bonus)\b|nice to have", re.I)
_NICE_HEADING = re.compile(r"nice[- ]to[- ]have|preferred|bonus|desired|pluses|\bplus\b", re.I)
_REQUIRED_HEADING = re.compile(
    r"required|requirements|qualifications|must[- ]have|what you['’]?ll need|what you bring"
    r"|what we['’]?re looking for|who you are|^skills|skills (?:&|and) experience",
    re.I,
)
_OTHER_HEADING = re.compile(
    r"about|benefits|perks|how to apply|responsibilit|what you['’]?ll do|what we offer|the role"
    r"|compensation|salary|description",
    re.I,
)

_WORD_NUMBERS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}
_YEARS = re.compile(
    r"\b(\d{1,2}|" + "|".join(_WORD_NUMBERS) + r")\s*(?:\+|plus)?\s*"
    r"(?:(?:-|–|—|to)\s*\d{1,2}\s*\+?\s*)?(?:years?|yrs?)\b",
    re.I,
)
_YEARS_CONTEXT = re.compile(r"experience|professional|background|expertise", re.I)
_LADDER_LEVEL = re.compile(r"\blevel\b", re.I)
_MAX_YEARS = 25

_AMOUNT = r"(\d{1,3}(?:,\d{3})+|\d{2,3}(?:\.\d+)?\s*[kK]|\d{5,6})"
_SALARY_RANGE = re.compile(r"\$\s*" + _AMOUNT + r"\s*(?:-|–|—|to)\s*(?:USD\s*)?\$?\s*" + _AMOUNT)
_SALARY_SINGLE = re.compile(r"\$\s*" + _AMOUNT)
_SALARY_LINE = re.compile(r"salary|compensation|pay range|base pay", re.I)
# "Not listed" lines and application-form brackets name amounts that aren't the job's salary.
_NOT_A_SALARY = re.compile(r"not listed|not stated|requirements?|under \$|over \$", re.I)
_MIN_SALARY = 20_000
_MAX_SALARY = 1_000_000

_TITLE_LEVELS: tuple[tuple[PostingLevel, re.Pattern[str]], ...] = (
    (PostingLevel.INTERN, re.compile(r"\bintern(?:ship)?\b", re.I)),
    (
        PostingLevel.MANAGER,
        re.compile(r"\b(?:manager|head of|director|vp|vice president|cto)\b", re.I),
    ),
    (PostingLevel.PRINCIPAL, re.compile(r"\b(?:principal|distinguished|architect)\b", re.I)),
    (PostingLevel.STAFF, re.compile(r"\b(?:staff|iv|4)\s*$|\bstaff\b", re.I)),
    (PostingLevel.SENIOR, re.compile(r"\b(?:senior|sr|lead)\b|\b(?:iii|3)\s*$", re.I)),
    (
        PostingLevel.JUNIOR,
        re.compile(r"\b(?:junior|jr|entry[- ]level|associate|new grad)\b|\b(?:i|1)\s*$", re.I),
    ),
    (PostingLevel.MID, re.compile(r"\b(?:mid[- ]level|intermediate)\b|\b(?:ii|2)\s*$", re.I)),
)

_ROLE_FAMILIES: tuple[tuple[RoleFamily, re.Pattern[str]], ...] = (
    (RoleFamily.MANAGEMENT, re.compile(r"\b(?:manager|head of|director|vp|cto)\b", re.I)),
    (RoleFamily.FULL_STACK, re.compile(r"full[- ]?stack", re.I)),
    (RoleFamily.MACHINE_LEARNING, re.compile(r"machine learning|\bml\b|\bai\b", re.I)),
    (RoleFamily.DATA, re.compile(r"\bdata\b|analytics", re.I)),
    (RoleFamily.SECURITY, re.compile(r"security", re.I)),
    (RoleFamily.QA, re.compile(r"\bqa\b|quality|\bsdet\b|\btest\b", re.I)),
    (
        RoleFamily.DEVOPS,
        re.compile(r"devops|\bsre\b|site reliability|platform|infrastructure|\bcloud\b", re.I),
    ),
    (RoleFamily.MOBILE, re.compile(r"mobile|\bios\b|android", re.I)),
    (RoleFamily.FRONTEND, re.compile(r"front[- ]?end|\bui\b", re.I)),
    (RoleFamily.BACKEND, re.compile(r"back[- ]?end|\bserver\b", re.I)),
    (RoleFamily.GENERAL_SOFTWARE, re.compile(r"software|developer|engineer|programmer", re.I)),
)

_WORK_MODES: tuple[tuple[WorkMode, re.Pattern[str]], ...] = (
    (WorkMode.HYBRID, re.compile(r"\bhybrid\b", re.I)),
    (WorkMode.REMOTE, re.compile(r"\bremote\b", re.I)),
    (WorkMode.ONSITE, re.compile(r"\bon[- ]?site\b|\bin[- ]office\b", re.I)),
)

_EMPLOYMENT_TYPES: tuple[tuple[EmploymentType, re.Pattern[str]], ...] = (
    (EmploymentType.INTERNSHIP, re.compile(r"\binternship\b", re.I)),
    (EmploymentType.FULL_TIME, re.compile(r"\bfull[- ]time\b", re.I)),
    (EmploymentType.PART_TIME, re.compile(r"\bpart[- ]time\b", re.I)),
    (
        EmploymentType.CONTRACT,
        re.compile(r"\bcontract[- ]to[- ]hire\b|\bcontract (?:role|position)\b|\b1099\b", re.I),
    ),
)

_REFERRAL = re.compile(r"\breferr?al\b|\breferred\b", re.I)
_REFERRER = re.compile(r"\(([^)]+)\)|\bby\s+([^;,]+)", re.I)
_JOB_BOARD = re.compile(
    r"linkedin|indeed|idealist|glassdoor|wellfound|angel\.?list|\bdice\b|ziprecruiter"
    r"|built\s?in|hacker\s?news|\botta\b|monster|remotive|remote\s?ok|arbeitnow|jobicy"
    r"|himalayas|the\s?muse|adzuna|usajobs|jooble|findwork|larabench",
    re.I,
)
_RECRUITER = re.compile(r"recruiter|headhunter|staffing", re.I)
_COMPANY_SITE = re.compile(r"career|company site|website|\bdirect\b", re.I)


class DegreeLevel(StrEnum):
    BACHELOR = "bachelor"
    MASTER = "master"
    DOCTORATE = "doctorate"


DEGREE_ORDER = (DegreeLevel.BACHELOR, DegreeLevel.MASTER, DegreeLevel.DOCTORATE)
# Abbreviations such as BS and MS only count before "in", "or", "/" or "degree", so "MS Office"
# and "BS" in other senses don't read as degrees.
_DEGREE_ABBREVIATION_END = r"\.?(?=\s*(?:/|or\b|in\b|degree\b|\)|,))"
_DEGREES: tuple[tuple[DegreeLevel, re.Pattern[str]], ...] = (
    (DegreeLevel.DOCTORATE, re.compile(r"\b(?i:ph\.?\s?d\b|doctora(?:te|l) degree)")),
    (
        DegreeLevel.MASTER,
        re.compile(
            r"\b(?i:master['’]?s?\s+(?:degree|of|in)\b|master['’]s\b|mba\b)"
            r"|\bM\.?S(?:c)?" + _DEGREE_ABBREVIATION_END
        ),
    ),
    (
        DegreeLevel.BACHELOR,
        re.compile(
            r"\b(?i:bachelor|undergraduate degree|b\.?eng\b)"
            r"|\bB\.?[AS](?:c)?" + _DEGREE_ABBREVIATION_END
        ),
    ),
)
_DEGREE_PATTERNS = dict(_DEGREES)
_DEGREE_WORD = re.compile(r"\bdegree\b", re.I)
_DEGREE_FIELD = re.compile(
    r"computer science|engineering|related (?:technical )?field|technical field|\bstem\b", re.I
)
_DEGREE_EQUIVALENT = re.compile(r"equivalent|or (?:\w+ )?experience|in lieu", re.I)
_PREFERRED = re.compile(r"\bpreferred\b|\bideally\b", re.I)
# A security clearance, in a posting or on a resume.
CLEARANCE = re.compile(r"\bclearance\b|\bts/sci\b|\btop secret\b", re.I)
# "No clearance required", "does not require a clearance", "Clearance: not needed". Narrow on
# purpose, so "if you can't obtain a clearance" still reads as a requirement.
_NO_CLEARANCE = re.compile(
    r"(?:\bnot|n['’]t)\s+(?:require[sd]?|need(?:ed)?|necessary)\b"
    r"|\bno\b[^.]{0,30}\bclearance\b"
    r"|\bclearance\b[^.]{0,20}"
    r"(?:\bnot\s+(?:required|needed|necessary)\b|\bnone\b|\bn/?a\b)",
    re.I,
)
_CITIZENSHIP = re.compile(
    r"\bu\.?s\.?\s+citizen|\bcitizenship\b.*\brequired\b|\bmust be a citizen\b", re.I
)
# Equal-opportunity statements name citizenship among things a decision ignores.
_NOT_A_REQUIREMENT = re.compile(r"\bregard(?:less)?\b|\bwithout\b", re.I)
# Between "3+ years" and the technology it applies to, as in "3+ years of hands-on Python".
_YEARS_LEAD = re.compile(
    r"\s*(?:of\s+)?(?:(?:professional|hands[- ]on|production|commercial|recent|relevant)\s+)?"
    r"(?:experience\s+)?(?:(?:with|in|using|writing|building|developing)\s+)?",
    re.I,
)
# Between a technology and its years, as in "Python (3+ years)" or "Python: 3 years".
_YEARS_TRAIL = re.compile(r"\s*[(:\-–—]?\s*")

_COMPANY_SUFFIX = re.compile(
    r"\b(?:inc|incorporated|llc|ltd|limited|corp|corporation|co|company|gmbh|plc)\b\.?\s*$"
)
_NOT_WORD = re.compile(r"[^a-z0-9&+ ]+")


@dataclass(frozen=True)
class ParsedTechnology:
    name: str
    category: str
    requirement: Requirement


@dataclass(frozen=True)
class ParsedPosting:
    level: PostingLevel | None
    role_family: RoleFamily
    years_required: int | None
    salary_min: int | None
    salary_max: int | None
    work_mode: WorkMode | None
    employment_type: EmploymentType | None
    source_kind: SourceKind | None
    referred: bool
    referrer: str | None
    technologies: list[ParsedTechnology]


@dataclass(frozen=True)
class DegreeRequirement:
    level: DegreeLevel
    # False when the posting only prefers the degree.
    required: bool
    # True when the posting accepts equivalent experience instead.
    equivalent_ok: bool


@dataclass(frozen=True)
class PostingRequirements:
    """Requirements beyond the stack, read from the posting text each time it's scored."""

    skill_years: tuple[tuple[str, int], ...] = ()
    degree: DegreeRequirement | None = None
    clearance: bool = False
    citizenship: bool = False

    def years_for(self, name: str) -> int | None:
        return next((years for skill, years in self.skill_years if skill == name), None)


def canonical_url(url: str | None) -> str | None:
    """Drop the fragment and trailing slash. The query stays, because it can identify the job."""
    if not url:
        return None
    parts = urlsplit(url.strip())
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), parts.query, ""))


def company_key(company: str) -> str:
    """Normalize a company name, so "Acme, Inc." and "ACME" count as one company."""
    words = _NOT_WORD.sub(" ", company.lower())
    key = " ".join(words.split())
    return " ".join(_COMPANY_SUFFIX.sub("", key).split()) or key


def technology_category(name: str) -> str:
    return BUILTIN.category(name)


def canonical_technology(name: str) -> str:
    """The built-in name for a technology typed by hand, so "postgres" is "PostgreSQL"."""
    return BUILTIN.canonical(name)


def technology_in_text(name: str, text: str) -> bool:
    """Find a technology by the parser's rules, or by its exact name if it isn't in the list."""
    return BUILTIN.in_text(name, text)


def technology_position(name: str, text: str) -> int:
    """Where a technology is first named in a text, or the text's length when it isn't."""
    match = BUILTIN.pattern(name).search(text)
    return match.start() if match else len(text)


def technology_requirement(names: list[str], text: str) -> Requirement | None:
    """How strongly a posting's text asks for any of these technologies: REQUIRED when one is
    under a requirements heading, MENTIONED when only named elsewhere outside a nice-to-have,
    NICE_TO_HAVE when only named as a plus, None when none is named at all."""
    if not names or not any(technology_in_text(name, text) for name in names):
        return None
    best: Requirement | None = None
    rank = {Requirement.NICE_TO_HAVE: 0, Requirement.MENTIONED: 1, Requirement.REQUIRED: 2}
    for requirement, line in _sections(text):
        if best is not None and rank[requirement] <= rank[best]:
            continue
        if any(technology_in_text(name, line) for name in names):
            best = requirement
            if best is Requirement.REQUIRED:
                break
    # Named across a line break, so no single line holds it.
    return best or Requirement.MENTIONED


def _sections(text: str) -> list[tuple[Requirement, str]]:
    """Label each line by the section it sits under. Unsectioned lines count as only mentioned."""
    current = Requirement.MENTIONED
    labelled = []
    for line in text.splitlines():
        stripped = line.strip()
        is_heading = (
            0 < len(stripped) <= _MAX_HEADING_LENGTH
            and not _BULLET.match(line)
            and not stripped.endswith((".", ","))
        )
        if is_heading:
            if _NICE_HEADING.search(stripped):
                current = Requirement.NICE_TO_HAVE
            elif _REQUIRED_HEADING.search(stripped):
                current = Requirement.REQUIRED
            elif _OTHER_HEADING.search(stripped) or _STYLED_HEADING.match(stripped):
                current = Requirement.MENTIONED
        # A heading stays in its own section, since "Required: Python" also names a technology.
        softened = current is Requirement.REQUIRED and _INLINE_NICE.search(line)
        labelled.append((Requirement.NICE_TO_HAVE if softened else current, line))
    return labelled


def _parse_technologies(
    sections: list[tuple[Requirement, str]], vocabulary: Vocabulary
) -> list[ParsedTechnology]:
    priority = {Requirement.MENTIONED: 0, Requirement.NICE_TO_HAVE: 1, Requirement.REQUIRED: 2}
    found: dict[str, Requirement] = {}
    for requirement, line in sections:
        for term in vocabulary.terms:
            known = found.get(term.name)
            if known is not None and priority[known] >= priority[requirement]:
                continue
            if term.pattern.search(line):
                found[term.name] = requirement
    return [
        ParsedTechnology(name, vocabulary.category(name), requirement)
        for name, requirement in sorted(found.items())
    ]


def _years_in(lines: list[str]) -> int | None:
    values = []
    for line in lines:
        if not _YEARS_CONTEXT.search(line):
            continue
        for match in _YEARS.finditer(line):
            raw = match.group(1).lower()
            value = _WORD_NUMBERS.get(raw) or int(raw)
            if 0 < value <= _MAX_YEARS:
                values.append(value)
    return max(values) if values else None


def _parse_years(sections: list[tuple[Requirement, str]]) -> int | None:
    # Several "Level 2: 10 years" lines describe many roles, so no one figure fits the posting.
    ladder = [line for _, line in sections if _LADDER_LEVEL.search(line) and _YEARS.search(line)]
    usable = sections if len(ladder) < 2 else [s for s in sections if s[1] not in ladder]
    required = [line for requirement, line in usable if requirement is Requirement.REQUIRED]
    years = _years_in(required)
    return years if years is not None else _years_in([line for _, line in usable])


def _amount(raw: str) -> int:
    cleaned = raw.replace(",", "").replace(" ", "").lower()
    if cleaned.endswith("k"):
        return round(float(cleaned[:-1]) * 1000)
    return int(cleaned)


def _plausible_salary(value: int) -> bool:
    return _MIN_SALARY <= value <= _MAX_SALARY


def _parse_salary(text: str) -> tuple[int | None, int | None]:
    for line in text.splitlines():
        if _NOT_A_SALARY.search(line):
            continue
        for match in _SALARY_RANGE.finditer(line):
            low, high = _amount(match.group(1)), _amount(match.group(2))
            if _plausible_salary(low) and _plausible_salary(high) and low <= high:
                return low, high
    for line in text.splitlines():
        if not _SALARY_LINE.search(line) or _NOT_A_SALARY.search(line):
            continue
        for match in _SALARY_SINGLE.finditer(line):
            value = _amount(match.group(1))
            if _plausible_salary(value):
                return value, value
    return None, None


def _parse_level(title: str, years: int | None) -> PostingLevel | None:
    for level, pattern in _TITLE_LEVELS:
        if pattern.search(title):
            return level
    if years is None:
        return None
    if years < 2:
        return PostingLevel.JUNIOR
    if years < 5:
        return PostingLevel.MID
    return PostingLevel.SENIOR if years < 8 else PostingLevel.STAFF


def parse_role_family(title: str) -> RoleFamily:
    for family, pattern in _ROLE_FAMILIES:
        if pattern.search(title):
            return family
    return RoleFamily.OTHER


def _first_match[T](rules: tuple[tuple[T, re.Pattern[str]], ...], text: str) -> T | None:
    for value, pattern in rules:
        if pattern.search(text):
            return value
    return None


def _parse_referral(source: str | None) -> tuple[bool, str | None]:
    if not source or not _REFERRAL.search(source):
        return False, None
    match = _REFERRER.search(source)
    if not match:
        return True, None
    return True, (match.group(1) or match.group(2)).strip() or None


def _parse_source_kind(source: str | None, url: str | None, referred: bool) -> SourceKind | None:
    text = source or ""
    if _JOB_BOARD.search(text):
        return SourceKind.JOB_BOARD
    if _RECRUITER.search(text):
        return SourceKind.RECRUITER
    if referred:
        return SourceKind.REFERRAL
    if _COMPANY_SITE.search(text):
        return SourceKind.COMPANY_SITE
    if url:
        host = urlsplit(url).netloc
        return SourceKind.JOB_BOARD if _JOB_BOARD.search(host) else SourceKind.COMPANY_SITE
    return None


def parse_posting(
    title: str,
    raw_text: str,
    source: str | None = None,
    location: str | None = None,
    url: str | None = None,
    vocabulary: Vocabulary = BUILTIN,
) -> ParsedPosting:
    sections = _sections(raw_text)
    years = _parse_years(sections)
    salary_min, salary_max = _parse_salary(raw_text)
    referred, referrer = _parse_referral(source)
    return ParsedPosting(
        level=_parse_level(title, years),
        role_family=parse_role_family(title),
        years_required=years,
        salary_min=salary_min,
        salary_max=salary_max,
        work_mode=_first_match(_WORK_MODES, location or "") or _first_match(_WORK_MODES, raw_text),
        employment_type=_first_match(_EMPLOYMENT_TYPES, f"{title}\n{raw_text}"),
        source_kind=_parse_source_kind(source, url, referred),
        referred=referred,
        referrer=referrer,
        technologies=_parse_technologies(sections, vocabulary),
    )


def degree_levels_in(text: str) -> list[DegreeLevel]:
    """Every degree level a piece of text names, lowest first."""
    return [level for level in DEGREE_ORDER if _DEGREE_PATTERNS[level].search(text)]


def _parse_degree(sections: list[tuple[Requirement, str]]) -> DegreeRequirement | None:
    """The lowest degree a line asks for. A required line wins over a preferred one."""
    found: list[DegreeRequirement] = []
    for requirement, line in sections:
        levels = degree_levels_in(line)
        # Outside a requirements section, a level counts only when the line says "degree".
        says_degree = _DEGREE_WORD.search(line) is not None
        if levels and requirement is Requirement.MENTIONED and not says_degree:
            continue
        if not levels and says_degree and _DEGREE_FIELD.search(line):
            levels = [DegreeLevel.BACHELOR]
        if not levels:
            continue
        found.append(
            DegreeRequirement(
                level=levels[0],
                required=requirement is not Requirement.NICE_TO_HAVE
                and not _PREFERRED.search(line),
                equivalent_ok=_DEGREE_EQUIVALENT.search(line) is not None,
            )
        )
    required = [degree for degree in found if degree.required]
    return (required or found or [None])[0]


def _years_by_technology(lines: list[str], vocabulary: Vocabulary) -> tuple[tuple[str, int], ...]:
    """Years tied to one technology, as in "3+ years of Python" or "Python (3+ years)"."""
    found: dict[str, int] = {}
    for line in lines:
        for match in _YEARS.finditer(line):
            raw = match.group(1).lower()
            years = _WORD_NUMBERS.get(raw) or int(raw)
            if not 0 < years <= _MAX_YEARS:
                continue
            lead = _YEARS_LEAD.match(line, match.end())
            for term in vocabulary.terms:
                after = lead is not None and term.pattern.match(line, lead.end())
                before = any(
                    _YEARS_TRAIL.fullmatch(line, tech.end(), match.start())
                    for tech in term.pattern.finditer(line, 0, match.start())
                )
                if after or before:
                    found[term.name] = max(found.get(term.name, 0), years)
    return tuple(sorted(found.items()))


def parse_requirements(raw_text: str, vocabulary: Vocabulary = BUILTIN) -> PostingRequirements:
    sections = _sections(raw_text)
    wanted = [line for requirement, line in sections if requirement is not Requirement.NICE_TO_HAVE]
    return PostingRequirements(
        skill_years=_years_by_technology([line for _, line in sections], vocabulary),
        degree=_parse_degree(sections),
        clearance=any(CLEARANCE.search(line) and not _NO_CLEARANCE.search(line) for line in wanted),
        citizenship=any(
            _CITIZENSHIP.search(line) and not _NOT_A_REQUIREMENT.search(line) for line in wanted
        ),
    )


def technology_mentions(text: str) -> Counter[str]:
    """How many times each built-in technology appears in a text."""
    return BUILTIN.mentions(text)


_REQUIREMENT_WEIGHT = {
    Requirement.REQUIRED.value: 2,
    Requirement.NICE_TO_HAVE.value: 1,
    Requirement.MENTIONED.value: 1,
}


def requirement_overlap(left: dict[str, str], right: dict[str, str]) -> float:
    """Weighted Jaccard of two stacks, keyed by name with the requirement as value.

    A technology both require counts twice as much as one both only mention, and one required
    by one job and mentioned by the other counts as a partial match.
    """
    names = left.keys() | right.keys()

    def weight(stack: dict[str, str], name: str) -> int:
        return _REQUIREMENT_WEIGHT.get(stack[name], 1) if name in stack else 0

    shared = sum(min(weight(left, name), weight(right, name)) for name in names)
    total = sum(max(weight(left, name), weight(right, name)) for name in names)
    return shared / total if total else 0.0
