"""Skills per profile (docs/skills_spec.md, chunk 1): the user's own terms, the software switch,
and postings read again when the vocabulary changes."""

from datetime import date

from sqlalchemy import select

from app.listings.search import Listing, ListingFilters, filter_listings, listing_facts
from app.models import JobPosting, User
from app.postings.parser import BUILTIN, build_vocabulary, parse_posting
from app.postings.store import reread_skills

TEX_ACCOUNTANT = rb"""\begin{document}
\section*{Experience}
\textbf{Staff Accountant} \hfill Jan 2020 -- Dec 2024
\begin{itemize}
\item Ran month-end close and AP for 40 vendors in NetSuite.
\end{itemize}
\section*{Skills}
GAAP, Excel
\end{document}"""

ACCOUNTING_POSTING = """We're hiring a senior accountant.

Requirements:
- 3+ years of accounts payable experience
- NetSuite
- Knowledge of GAAP

Nice to have:
- Excel macros
"""

ACCOUNTING_TERMS = [
    {"name": "Accounts Payable", "aliases": ["AP"]},
    {"name": "NetSuite", "aliases": []},
    {"name": "GAAP", "aliases": []},
    {"name": "Excel", "aliases": []},
]


def _add_terms(client, terms=ACCOUNTING_TERMS) -> dict:
    body = {}
    for term in terms:
        response = client.post("/api/skills/terms", json=term)
        assert response.status_code == 201, response.text
        body = response.json()
    return body


def _save_posting(client, text: str = ACCOUNTING_POSTING, title: str = "Senior Accountant"):
    response = client.post(
        "/api/postings", json={"company": "Globex", "title": title, "raw_text": text}
    )
    assert response.status_code in (200, 201), response.text
    return response.json()


def _stack(detail: dict) -> dict[str, str]:
    return {item["name"]: item["requirement"] for item in detail["technologies"]}


def _reread_all(session_factory) -> None:
    with session_factory() as session:
        while reread_skills(session):
            pass


def test_a_term_matches_its_spellings():
    vocabulary = build_vocabulary(
        False, [("Accounts Payable", ["AP"]), ("Reconciliation", []), ("Month-end close", [])]
    )
    assert vocabulary.in_text("Accounts Payable", "Runs the accounts-payable team")
    assert vocabulary.in_text("Accounts Payable", "Owns AP for the region")
    # A short alias in capitals only matches in capitals, so "ap" and "apply" don't count.
    assert not vocabulary.in_text("Accounts Payable", "please apply, ap clerks welcome")
    assert vocabulary.in_text("Reconciliation", "Daily bank reconciliations")
    assert vocabulary.in_text("Month-end close", "Leads the month end close")
    assert vocabulary.canonical("ap") == "ap"
    assert vocabulary.canonical("AP") == "Accounts Payable"


def test_the_software_switch_decides_whether_built_in_skills_count():
    off = build_vocabulary(False, [("GAAP", [])])
    on = build_vocabulary(True, [("GAAP", [])])
    assert off.names == ("GAAP",)
    assert "Python" in on.names and "GAAP" in on.names
    assert on.category("GAAP") == "skill"
    assert on.category("Python") == BUILTIN.category("Python")
    # Software alone is the built-in list itself, so its parses are shared with everyone's.
    assert build_vocabulary(True, []).key == BUILTIN.key


def test_a_term_with_a_built_in_name_keeps_the_built_in_pattern():
    vocabulary = build_vocabulary(False, [("go", ["golang"])])
    assert vocabulary.names == ("Go",)
    # The built-in "Go" doesn't match the verb, which a plain pattern for "go" would.
    assert not vocabulary.in_text("Go", "Ready to go above and beyond")
    assert vocabulary.in_text("Go", "3+ years of Go")


def test_one_name_from_two_profiles_is_one_term_with_both_aliases():
    vocabulary = build_vocabulary(
        False, [("Accounts Payable", ["AP"]), ("accounts payable", ["A/P"])]
    )
    assert vocabulary.names == ("Accounts Payable",)
    assert vocabulary.in_text("Accounts Payable", "Handles A/P")
    same = build_vocabulary(False, [("accounts payable", ["A/P"]), ("Accounts Payable", ["AP"])])
    assert same.key == vocabulary.key


def test_a_posting_is_parsed_with_the_user_s_terms():
    vocabulary = build_vocabulary(
        True, [(term["name"], term["aliases"]) for term in ACCOUNTING_TERMS]
    )
    parsed = parse_posting("Senior Accountant", ACCOUNTING_POSTING, vocabulary=vocabulary)
    stack = {item.name: item.requirement.value for item in parsed.technologies}
    assert stack == {
        "Accounts Payable": "required",
        "NetSuite": "required",
        "GAAP": "required",
        "Excel": "nice_to_have",
    }
    assert parse_posting("Senior Accountant", ACCOUNTING_POSTING).technologies == []


def test_listings_filter_and_score_with_the_user_s_terms():
    vocabulary = build_vocabulary(False, [("NetSuite", [])])
    listing = Listing(
        id="1",
        source="Remotive",
        company="Globex",
        title="Senior Accountant",
        url="https://example.com/1",
        location=None,
        description=ACCOUNTING_POSTING,
        published_on=None,
    )
    wanted = ListingFilters(stack_all=frozenset({"NetSuite"}))
    assert filter_listings([listing], wanted, date(2026, 11, 10)) == []
    assert filter_listings([listing], wanted, date(2026, 11, 10), vocabulary) == [listing]
    assert listing_facts(listing, vocabulary=vocabulary).technologies == (("NetSuite", "required"),)
    # The built-in parse, which other users see, is left alone.
    assert listing.parsed.technologies == []


def test_a_new_user_s_profile_uses_the_software_skills(client):
    skills = client.get("/api/skills").json()
    assert skills["software_skills"] is True
    assert skills["terms"] == []
    assert skills["software_count"] == len(BUILTIN.names)


def test_adding_terms_reads_saved_postings_again(client, session_factory):
    posting = _save_posting(client)
    assert _stack(posting) == {}
    body = _add_terms(client)
    assert [term["name"] for term in body["terms"]] == [
        "Accounts Payable",
        "Excel",
        "GAAP",
        "NetSuite",
    ]
    waiting = client.get(f"/api/postings/{posting['id']}").json()
    assert waiting["skills_updating"] is True

    _reread_all(session_factory)
    detail = client.get(f"/api/postings/{posting['id']}").json()
    assert detail["skills_updating"] is False
    assert _stack(detail) == {
        "Accounts Payable": "required",
        "NetSuite": "required",
        "GAAP": "required",
        "Excel": "nice_to_have",
    }
    counts = {term["name"]: term["postings"] for term in client.get("/api/skills").json()["terms"]}
    assert counts == {"Accounts Payable": 1, "Excel": 1, "GAAP": 1, "NetSuite": 1}


def test_a_new_posting_uses_the_terms_at_once(client):
    _add_terms(client)
    posting = _save_posting(client)
    assert posting["skills_updating"] is False
    assert "NetSuite" in _stack(posting)


def test_the_match_finds_a_term_by_its_alias_in_the_resume(client, session_factory):
    _add_terms(client)
    client.post("/api/resumes", files={"file": ("resume.tex", TEX_ACCOUNTANT, "text/x-tex")})
    posting = _save_posting(client)
    match = posting["match"]
    # "AP" in a dated role backs Accounts Payable, and GAAP is only in the skills list.
    assert "Accounts Payable" in match["matched_required"]
    assert "NetSuite" in match["matched_required"]
    assert "GAAP" in match["weak_required"]
    assert match["missing_required"] == []

    insights = client.get("/api/analytics/resumes").json()
    demand = {row["name"]: row for row in insights["demand"]}
    assert demand["Accounts Payable"]["category"] == "skill"
    assert demand["Accounts Payable"]["verdict"] == "met"


def test_turning_software_off_drops_built_in_skills_from_postings(client, session_factory):
    posting = _save_posting(client, "Requirements:\n- Python and Postgres", "Engineer")
    assert set(_stack(posting)) == {"Python", "PostgreSQL"}
    response = client.patch("/api/skills", json={"software_skills": False})
    assert response.json()["software_skills"] is False
    _reread_all(session_factory)
    assert _stack(client.get(f"/api/postings/{posting['id']}").json()) == {}


def test_a_correction_survives_a_vocabulary_change(client, session_factory):
    posting = _save_posting(client, "Requirements:\n- Python", "Engineer")
    edited = client.patch(f"/api/postings/{posting['id']}", json={"technologies": ["Rust"]})
    assert edited.status_code == 200, edited.text
    client.patch("/api/skills", json={"software_skills": False})
    _reread_all(session_factory)
    assert set(_stack(client.get(f"/api/postings/{posting['id']}").json())) == {"Rust"}


def test_terms_are_edited_and_removed(client, session_factory):
    term_id = _add_terms(client, [{"name": "GAAP", "aliases": []}])["terms"][0]["id"]
    with session_factory() as session:
        before = session.scalar(select(User.vocabulary_version))
    edited = client.patch(
        f"/api/skills/terms/{term_id}", json={"name": "US GAAP", "aliases": ["GAAP"]}
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["terms"][0]["aliases"] == ["GAAP"]
    removed = client.delete(f"/api/skills/terms/{term_id}")
    assert removed.status_code == 200
    assert removed.json()["terms"] == []
    with session_factory() as session:
        assert session.scalar(select(User.vocabulary_version)) == before + 2


def test_a_term_name_is_unique_and_its_aliases_differ(client):
    _add_terms(client, [{"name": "GAAP", "aliases": []}])
    duplicate = client.post("/api/skills/terms", json={"name": "gaap", "aliases": []})
    assert duplicate.status_code == 409
    own_name = client.post("/api/skills/terms", json={"name": "NetSuite", "aliases": ["netsuite"]})
    assert own_name.status_code == 422
    blank = client.post("/api/skills/terms", json={"name": "  ", "aliases": []})
    assert blank.status_code == 422


def test_another_user_s_term_is_not_found(client, other_client):
    term_id = _add_terms(client, [{"name": "GAAP", "aliases": []}])["terms"][0]["id"]
    assert other_client.delete(f"/api/skills/terms/{term_id}").status_code == 404
    assert (
        other_client.patch(
            f"/api/skills/terms/{term_id}", json={"name": "X", "aliases": []}
        ).status_code
        == 404
    )


def test_terms_belong_to_the_active_profile(client):
    _add_terms(client, [{"name": "GAAP", "aliases": []}])
    created = client.post("/api/profiles", json={"name": "Nursing"})
    assert created.json()["software_skills"] is False
    client.post(f"/api/profiles/{created.json()['id']}/activate")
    skills = client.get("/api/skills").json()
    assert skills["terms"] == []
    assert skills["software_skills"] is False


def test_a_posting_from_another_profile_s_terms_still_reads_them(client, session_factory):
    """Postings belong to the user, so they're read with every profile's terms."""
    _add_terms(client, [{"name": "GAAP", "aliases": []}])
    created = client.post("/api/profiles", json={"name": "Software", "software_skills": True})
    client.post(f"/api/profiles/{created.json()['id']}/activate")
    posting = _save_posting(client)
    assert "GAAP" in _stack(posting)
    with session_factory() as session:
        stored = session.scalar(select(JobPosting))
        assert stored.skills_version == session.scalar(select(User.vocabulary_version))
