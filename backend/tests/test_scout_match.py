"""Scout's code gate: what a brief asks of a job, read with no model call, and the plain
filters every run shares (docs/scout_spec.md)."""

from datetime import date

from app.models import PostingLevel, Requirement
from app.postings.parser import technology_requirement
from app.scout.filters import board_location, board_queries
from app.scout.match import (
    LevelFit,
    MatchSpec,
    StackPlace,
    derived_must_have,
    derived_rule_out,
    level_fit,
    parsed_level,
    ruled_out,
    spec_from_payload,
    stack_passes,
    stack_place,
)

LARAVEL_SPEC = MatchSpec(("Laravel",), (), frozenset())

POSTING = """About us
We run payments for small shops.

Requirements
- 5+ years of PHP
- Laravel and MySQL

Nice to have
- Kubernetes
- Python is a plus
"""


def _spec(**changes):
    return spec_from_payload({"description": "", **changes})


def test_the_description_names_the_stack_unless_it_rules_it_out():
    assert derived_must_have("Laravel stacks, emphasis on internal tooling.") == ["Laravel"]
    # Most mentioned first, then in the order written.
    assert derived_must_have("Vue or Laravel. Laravel above all, then Vue, then React.") == [
        "Vue",
        "Laravel",
        "React",
    ]
    assert derived_must_have("Laravel work, not Java") == ["Laravel"]
    assert derived_rule_out("Laravel work, not Java and no Kubernetes") == ["Java", "Kubernetes"]
    assert derived_must_have("internal tools for ops teams") == []


def test_saved_lists_win_over_the_description():
    spec = _spec(description="Laravel", must_have=["django", "Django"], levels=["senior", "x"])
    assert spec.must_have == ("Django",)
    assert spec.levels == frozenset({PostingLevel.SENIOR})
    assert _spec(description="Laravel", must_have=[]).strict is False
    # A technology can't be both required and ruled out.
    both = _spec(must_have=["Laravel"], rule_out=["Laravel", "Java"])
    assert both.rule_out == ("Java",)


def test_frameworks_count_their_own_tools():
    assert "Livewire" in LARAVEL_SPEC.stack_terms
    place = stack_place(LARAVEL_SPEC, "Full-stack developer", "We build with Livewire and Alpine.")
    assert place is StackPlace.MENTIONED


def test_the_stack_must_be_named_outside_a_nice_to_have():
    assert stack_place(LARAVEL_SPEC, "Senior Laravel Engineer", "") is StackPlace.TITLE
    assert stack_place(LARAVEL_SPEC, "Backend Engineer", POSTING) is StackPlace.REQUIRED
    nice = POSTING.replace("- Laravel and MySQL", "- MySQL").replace("- Kubernetes", "- Laravel")
    assert stack_place(LARAVEL_SPEC, "Backend Engineer", nice) is StackPlace.NICE_ONLY
    assert not stack_passes(LARAVEL_SPEC, StackPlace.NICE_ONLY)
    assert not stack_passes(LARAVEL_SPEC, StackPlace.NONE)
    assert stack_passes(_spec(must_have=[]), StackPlace.NONE)
    # Named only under "Nice to have", or not at all.
    assert technology_requirement(["Python"], POSTING) is Requirement.NICE_TO_HAVE
    assert technology_requirement(["Go"], POSTING) is None


def test_rule_out_needs_the_job_to_require_it():
    spec = MatchSpec(("Laravel",), ("Kubernetes", "MySQL"), frozenset())
    # Kubernetes is only nice to have, MySQL is required.
    assert ruled_out(spec, "Backend Engineer", POSTING) == "MySQL"
    assert ruled_out(MatchSpec((), ("Kubernetes",), frozenset()), "Engineer", POSTING) is None
    assert ruled_out(MatchSpec((), ("Java",), frozenset()), "Java Engineer", "") == "Java"


def test_levels_allow_one_rung_either_side():
    senior = MatchSpec((), (), frozenset({PostingLevel.SENIOR}))
    assert level_fit(senior, PostingLevel.SENIOR) is LevelFit.EXACT
    assert level_fit(senior, PostingLevel.MID) is LevelFit.NEAR
    assert level_fit(senior, PostingLevel.STAFF) is LevelFit.NEAR
    assert level_fit(senior, PostingLevel.JUNIOR) is LevelFit.OUT
    assert level_fit(senior, PostingLevel.PRINCIPAL) is LevelFit.OUT
    assert level_fit(senior, PostingLevel.MANAGER) is LevelFit.OUT
    assert level_fit(senior, None) is LevelFit.UNKNOWN
    # Nothing picked: any level but intern and manager.
    anything = MatchSpec((), (), frozenset())
    assert level_fit(anything, PostingLevel.PRINCIPAL) is LevelFit.EXACT
    assert level_fit(anything, PostingLevel.INTERN) is LevelFit.OUT
    assert level_fit(anything, PostingLevel.MANAGER) is LevelFit.OUT
    # Learned from "Wrong level", unless asked for outright.
    learned = frozenset({PostingLevel.STAFF})
    assert level_fit(senior, PostingLevel.STAFF, learned) is LevelFit.OUT
    staff = MatchSpec((), (), frozenset({PostingLevel.STAFF}))
    assert level_fit(staff, PostingLevel.STAFF, learned) is LevelFit.EXACT


def test_levels_come_from_the_title_then_the_years_asked():
    assert parsed_level("Senior Laravel Engineer", "") is PostingLevel.SENIOR
    assert parsed_level("Laravel Developer", "Requirements\n- 6+ years of experience") is (
        PostingLevel.SENIOR
    )
    assert parsed_level("Laravel Developer", "Build things.") is None


def test_board_queries_come_from_the_stack_or_the_title_phrases():
    payload = {
        "description": "Laravel and PHP work. Laravel first, Vue is fine.",
        "title_families": ["internal tools", "Clinical Systems"],
    }
    assert board_queries(payload) == ["Laravel", "PHP", "Vue"]
    phrases = {**payload, "must_have": []}
    assert board_queries(phrases) == ['"internal tools"', '"Clinical Systems"']
    assert board_queries({"description": "Anything at all", "title_families": []}) == []


def test_a_us_location_narrows_the_job_boards_to_the_us():
    for location in (
        "east coast timezone (anywhere)",
        "Philadelphia, PA",
        "Philadelphia PA",
        "Remote in the U.S.",
        "Texas",
        "Remote (EST)",
        "us",
    ):
        assert board_location(location) == "us", location
    for location in (
        "",
        "Berlin, Germany",
        "London",
        "Toronto, ON",
        "Latin America",
        "Australia east coast",
        "CET timezone",
        "Asia Pacific",
    ):
        assert board_location(location) == "", location


def test_board_stage_turns_listings_into_pages(monkeypatch):
    from app.listings.search import Listing, SearchResults, SourceStatus
    from app.scout import worker as scout_worker

    listing = Listing(
        id="1",
        source="LaraBench",
        company="Acme",
        title="Senior Laravel Engineer",
        url="https://larabench.com/jobs/1",
        location="Remote, US",
        description="Remote. Full-time. $140,000 - $170,000 a year. Build internal tools.",
        published_on=date(2026, 11, 1),
    )
    calls = []

    def search(settings, keywords, location, store, recorder=None, **options):
        calls.append((keywords, location, options))
        return SearchResults(
            [listing],
            [
                SourceStatus("larabench", "LaraBench", "https://larabench.com", "ok", listings=1),
                SourceStatus("jooble", "Jooble", "https://jooble.org", "failed"),
                SourceStatus("findwork", "Findwork", "https://findwork.dev", "needs_setup"),
            ],
        )

    monkeypatch.setattr(scout_worker, "search_listings", search)
    stage = scout_worker.board_stage(object(), object())
    answer = stage({"queries": ["Laravel", '"internal tools"']})

    # Every query at once, deeply. Without a location none is sent, and a listing found by two
    # searches is kept once.
    deep = {"deep": True, "deadline_seconds": scout_worker.BOARD_DEADLINE_SECONDS}
    assert sorted(calls, key=lambda call: call[0]) == [
        ('"internal tools"', "", deep),
        ("Laravel", "", deep),
    ]
    stage({"queries": ["Laravel"], "location": "us"})
    assert calls[-1] == ("Laravel", "us", deep)
    assert answer["failed"] == ["Jooble"]
    [page] = answer["pages"]
    assert page["url"] == listing.url
    assert (page["ok"], page["source"], page["company"]) == (True, "LaraBench", "Acme")
    assert page["posted_at"] == "2026-11-01"
    assert page["text"] == listing.description
