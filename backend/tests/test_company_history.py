"""Hiring history from the catalog, on one fixed company worked through by hand.

Its own board is Greenhouse, first read on day 0 (a Monday). Day n is noon UTC n days later:
- a: Senior Backend Engineer, listed days 0-20, then closed. Also on LinkedIn.
- b: Product Designer, listed days 5-15, then again under a new link from day 30 (still listed).
- c: Data Engineer, two overlapping links, days 10-20 and 11-25, then closed.
- f: Senior Backend Engineer with different text, listed from day 45.
- d: Senior Backend Engineer with a's text, listed from day 50.
- e: listed on LinkedIn only, so it isn't part of the history.
Now is day 55.
"""

import uuid
from datetime import UTC, date, datetime, timedelta

from app.companies.catalog import fingerprint
from app.research.history import (
    AfterRejection,
    BoardRow,
    ListingRow,
    Week,
    after_rejection,
    is_engineering,
    median_days_open,
    openings,
    readable,
    reposts,
    weekly,
)

DAY0 = datetime(2026, 9, 7, 12, tzinfo=UTC)
NOW = DAY0 + timedelta(days=55)
TEXT = (
    "Senior Backend Engineer. You will design and run the services that move money for small "
    "businesses, from the ledger to payouts, and keep them fast and correct under load. You "
    "will write Python and Go, review code, plan work with product, and take a turn on call. "
    "We look for five years building backend systems, care with data, and clear writing. The "
    "team is spread across the United States and meets in person twice a year. Benefits "
    "include health cover, a learning budget, and four weeks of paid leave, plus parental "
    "leave. We are an equal opportunity employer and welcome people of every background to "
    "apply, including those who have taken time away from work."
)
TEXT_PRINT = fingerprint(TEXT)
assert TEXT_PRINT is not None
BOARDS = [BoardRow("greenhouse", True, "ok"), BoardRow("linkedin", False, "ok")]
JOBS = {name: uuid.UUID(int=index) for index, name in enumerate("abcdef", start=1)}
LINKEDIN_A = "https://www.linkedin.com/jobs/view/1001"


def day(number: float) -> datetime:
    return DAY0 + timedelta(days=number)


def _listing(job, url, title, first, last, closed=None, print_=TEXT_PRINT, source="greenhouse"):
    return ListingRow(
        job_id=JOBS[job],
        source=source,
        url=url,
        title=title,
        title_key=title.lower(),
        fingerprint=print_,
        first_seen=day(first),
        last_seen=day(last),
        job_closed_at=day(closed) if closed is not None else None,
    )


GH = "https://boards.greenhouse.io/acme/jobs/"
LISTINGS = [
    _listing("a", f"{GH}1", "Senior Backend Engineer", 0, 20, closed=21),
    _listing("a", LINKEDIN_A, "Senior Backend Engineer", 2, 20, closed=21, source="linkedin"),
    _listing("b", f"{GH}2", "Product Designer", 5, 15, print_=None),
    _listing("b", f"{GH}3", "Product Designer", 30, 55, print_=None),
    _listing("c", f"{GH}4", "Data Engineer", 10, 20, closed=26, print_=None),
    _listing("c", f"{GH}5", "Data Engineer", 11, 25, closed=26, print_=None),
    _listing("f", f"{GH}6", "Senior Backend Engineer", 45, 55, print_=TEXT_PRINT ^ 0xFFFF0000),
    _listing("d", f"{GH}7", "Senior Backend Engineer", 50, 55, print_=TEXT_PRINT ^ 0b101),
    _listing("e", "https://www.linkedin.com/jobs/view/2002", "Recruiter", 1, 55, source="linkedin"),
]


def _found():
    return openings(BOARDS, LISTINGS)


def _by_job(found):
    return [
        (job, opening.opened, opening.closed)
        for opening in found
        for job in JOBS
        if JOBS[job] == opening.job_id
    ]


# --- Which companies, and which roles ----------------------------------------------------------


def test_history_needs_an_own_board_that_still_answers():
    assert readable(BOARDS)
    assert readable([BoardRow("ashby", True, "empty")])
    assert not readable([BoardRow("greenhouse", True, "dead")])
    assert not readable([BoardRow("linkedin", False, "ok")])
    assert not readable([])


def test_engineering_roles_by_the_parsers_role_families():
    assert is_engineering("Senior Backend Engineer")
    assert is_engineering("Data Engineer")
    assert is_engineering("Engineering Manager, Payments")
    assert not is_engineering("Product Manager")
    assert not is_engineering("Product Designer")
    assert not is_engineering("Account Executive")


# --- Openings ------------------------------------------------------------------------------------


def test_openings_come_from_own_board_listings():
    found = _found()

    assert _by_job(found) == [
        ("a", day(0), day(20)),
        ("b", day(5), day(15)),
        # Two overlapping links are one opening, closed at the later one's last sighting.
        ("c", day(10), day(25)),
        # Listed again under a new link: a second opening, still listed.
        ("b", day(30), None),
        ("f", day(45), None),
        ("d", day(50), None),
    ]
    # Only what was listed when the board was first read has an unknown start.
    assert [opening.already_open for opening in found] == [True, False, False, False, False, False]
    assert found[0].urls == {f"{GH}1"}
    assert found[2].urls == {f"{GH}4", f"{GH}5"}


def test_naive_times_are_read_as_utc():
    naive = [
        ListingRow(
            job_id=JOBS["a"],
            source="greenhouse",
            url=f"{GH}1",
            title="Senior Backend Engineer",
            title_key="senior backend engineer",
            fingerprint=None,
            first_seen=day(0).replace(tzinfo=None),
            last_seen=day(3).replace(tzinfo=None),
            job_closed_at=day(4).replace(tzinfo=None),
        )
    ]
    [opening] = openings(BOARDS, naive)
    assert (opening.opened, opening.closed) == (day(0), day(3))


def test_no_own_board_listings_means_no_history():
    assert openings([BoardRow("linkedin", False, "ok")], LISTINGS) == []
    assert weekly([], NOW) == []
    assert median_days_open([]) is None


# --- Weekly series and time open ------------------------------------------------------------------


def test_weekly_counts_open_opened_and_closed():
    weeks = weekly(_found(), NOW)
    start = DAY0.date()
    assert weeks == [
        # a and b listed at the week's end; a was already listed, so only b opened.
        Week(start, open=2, open_engineering=1, opened=1, closed=0),
        Week(start + timedelta(days=7), open=3, open_engineering=2, opened=1, closed=0),
        Week(start + timedelta(days=14), open=1, open_engineering=1, opened=0, closed=2),
        Week(start + timedelta(days=21), open=0, open_engineering=0, opened=0, closed=1),
        Week(start + timedelta(days=28), open=1, open_engineering=0, opened=1, closed=0),
        Week(start + timedelta(days=35), open=1, open_engineering=0, opened=0, closed=0),
        Week(start + timedelta(days=42), open=2, open_engineering=1, opened=1, closed=0),
        # The current week counts what's listed now.
        Week(date(2026, 10, 26), open=3, open_engineering=2, opened=1, closed=0),
    ]


def test_median_days_open_leaves_out_unknown_starts_and_open_roles():
    # b's first opening (10 days) and c (15 days); a's start is unknown, the rest are open.
    assert median_days_open(_found()) == 12.5


# --- Reposts --------------------------------------------------------------------------------------


def test_reposts_are_the_same_job_again_or_its_twin_within_60_days():
    found = _found()
    pairs = [(item.original.job_id, item.repost.job_id, item.gap_days) for item in reposts(found)]
    # b listed again 15 days after closing; d has a's title and text 30 days after a closed.
    # f has a's title but different text, so it's a new role, not a repost.
    assert pairs == [(JOBS["b"], JOBS["b"], 15), (JOBS["a"], JOBS["d"], 30)]


def test_a_twin_after_60_days_isnt_a_repost():
    later = [
        *LISTINGS[:1],
        _listing("d", f"{GH}7", "Senior Backend Engineer", 81, 90, print_=TEXT_PRINT ^ 0b101),
    ]
    assert reposts(openings(BOARDS, later)) == []


# --- The job applied to, after the rejection ------------------------------------------------------


def _after(url=None, title="Senior Backend Engineer", text="", rejected=0.0, now=NOW):
    return after_rejection(
        _found(), LISTINGS, url=url, title=title, text=text, rejected=day(rejected), now=now
    )


def test_found_by_a_job_board_link_and_closed_within_the_30_days():
    # The LinkedIn link, with a tracking parameter, finds a's own-board opening.
    assert _after(url=f"{LINKEDIN_A}?trk=public_jobs", rejected=10) == AfterRejection(
        outcome="closed",
        matched_by="link",
        days_open=10,
        closed_on=day(20).date(),
        complete=True,
    )


def test_reposted_within_the_30_days():
    assert _after(url=f"{GH}2", rejected=8) == AfterRejection(
        outcome="reposted",
        matched_by="link",
        days_open=7,
        closed_on=day(15).date(),
        reposted_on=day(30).date(),
        complete=True,
    )
    # Closed before the rejection, and its twin opened 25 days after it.
    assert _after(url=f"{GH}1", rejected=25).outcome == "reposted"


def test_still_open_while_the_30_days_run():
    assert _after(url=f"{GH}6", rejected=46) == AfterRejection(
        outcome="still_open", matched_by="link", days_open=9, complete=False
    )


def test_found_by_title_and_text_without_a_link():
    # a and d match by title and text; d is the one listed at the rejection. f's text differs.
    assert _after(text=TEXT, rejected=52) == AfterRejection(
        outcome="still_open", matched_by="title_and_text", days_open=3, complete=False
    )


def test_not_found_without_a_known_link_or_matching_text():
    assert _after(url="https://example.com/jobs/1", rejected=10).outcome == "not_found"
    assert _after(text="", rejected=10).outcome == "not_found"
    # Listed only after the rejection: not the job applied to.
    assert _after(url=f"{GH}7", rejected=40).outcome == "not_found"


def test_a_job_already_listed_when_the_board_was_first_read_counts_from_before():
    # Rejected on day -3, before Callback first read the board on day 0, where the job was
    # already listed and still is: it was the job applied to, and it's still open.
    assert _after(url=f"{GH}1", rejected=-3, now=day(10)) == AfterRejection(
        outcome="still_open", matched_by="link", days_open=13, complete=False
    )
