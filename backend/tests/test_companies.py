"""Company identity for the Companies engine: aliases, agencies, similar names and merging."""

import pytest
from sqlalchemy import func, select

from app.companies.identity import (
    check_pending_pairs,
    company_domain,
    matching_key,
    parse_company_name,
    resolve_company,
    trigram_similarity,
)
from app.models import Company, CompanyAlias, CompanyMatchCheck


def _count(db, model) -> int:
    return db.scalar(select(func.count()).select_from(model))


def _pending(db) -> list[tuple[str, str]]:
    return [
        (check.key_a, check.key_b)
        for check in db.scalars(
            select(CompanyMatchCheck).where(CompanyMatchCheck.same.is_(None))
        ).all()
    ]


class Ask:
    """The model check, faked: answers each pair from a fixed set of same pairs."""

    def __init__(self, same=()):
        self.same = {frozenset(pair) for pair in same}
        self.calls = []

    def __call__(self, payload):
        self.calls.append(payload)
        return {
            "model": "claude-haiku-5-5",
            "cost_usd": 0.001,
            "answers": [
                {"same": frozenset((pair["a"]["name"], pair["b"]["name"])) in self.same}
                for pair in payload["pairs"]
            ],
        }


def test_matching_keys_settle_case_suffixes_spacing_and_ampersands():
    assert matching_key("Acme, Inc.") == matching_key("ACME") == matching_key("acme inc") == "acme"
    assert matching_key("Open AI") == matching_key("OpenAI") == "openai"
    assert matching_key("Johnson & Johnson") == matching_key("Johnson and Johnson")
    assert matching_key("The Trade Desk") == matching_key("Trade Desk")
    assert matching_key("...") == ""


def test_trigram_similarity_scores_like_pg_trgm():
    assert trigram_similarity("clair", "claire") == pytest.approx(5 / 8)
    assert trigram_similarity("loop", "loopio") == pytest.approx(0.5)
    assert trigram_similarity("acme", "acme") == 1.0


def test_company_domains_come_from_websites_not_job_boards():
    assert company_domain("https://careers.acme.com/jobs?x=1") == "acme.com"
    assert company_domain("www.Acme.com") == "acme.com"
    assert company_domain("https://shop.acme.co.uk") == "acme.co.uk"
    for link in (
        "https://job-boards.greenhouse.io/acme/jobs/1",
        "https://jobs.lever.co/acme/2",
        "https://www.linkedin.com/company/acme",
        "acme",
        "",
        None,
    ):
        assert company_domain(link) is None, link


def test_one_company_however_its_name_is_written(session_factory):
    with session_factory() as db:
        first = resolve_company(db, "Acme, Inc.").company
        assert resolve_company(db, "ACME").company.id == first.id
        assert resolve_company(db, "  acme   inc ").company.id == first.id
        assert resolve_company(db, "Open AI").company.id == resolve_company(db, "OpenAI").company.id
        assert _count(db, Company) == 2
        assert first.name == "Acme, Inc." and first.key == "acme" and first.kind == "employer"


def test_similar_names_wait_for_a_check_instead_of_merging(session_factory):
    with session_factory() as db:
        clair = resolve_company(db, "Clair").company
        claire = resolve_company(db, "Claire").company
        assert claire.id != clair.id
        assert _pending(db) == [("clair", "claire")]
        # Asking again doesn't queue the pair twice.
        resolve_company(db, "Claire")
        assert _pending(db) == [("clair", "claire")]
        # Too far apart to check at all.
        resolve_company(db, "Loop")
        resolve_company(db, "Loopio")
        assert _pending(db) == [("clair", "claire")]


def test_a_shared_website_makes_similar_names_one_company(session_factory):
    with session_factory() as db:
        clair = resolve_company(db, "Clair", domain="https://clair.com").company
        careers = "https://www.clair.com/careers"
        assert resolve_company(db, "Claire", domain=careers).company is clair
        assert db.get(CompanyAlias, "claire").decided_by == "source"
        assert _pending(db) == []

    with session_factory() as db:
        # Seen first without a website, then with one: the two become one.
        solo = resolve_company(db, "Mercury Labs").company
        resolve_company(db, "Mercury Labz", domain="mercurylabs.io")
        merged = resolve_company(db, "Mercury Labs", domain="mercurylabs.io").company
        assert merged.domain == "mercurylabs.io"
        assert db.get(CompanyAlias, "mercurylabs").company_id == merged.id
        assert db.get(CompanyAlias, "mercurylabz").company_id == merged.id
        assert merged.id == solo.id


def test_different_websites_settle_similar_names_as_different(session_factory):
    with session_factory() as db:
        resolve_company(db, "Clair", domain="clair.com")
        claire = resolve_company(db, "Claire", domain="claire.io").company
        assert claire.domain == "claire.io"
        check = db.get(CompanyMatchCheck, ("clair", "claire"))
        assert (check.same, check.decided_by) == (False, "source")
        assert _pending(db) == []


def test_the_same_name_with_another_website_is_another_company(session_factory):
    with session_factory() as db:
        first = resolve_company(db, "Mercury", domain="mercury.com").company
        second = resolve_company(db, "Mercury", domain="mercuryinsurance.com").company
        assert second.id != first.id
        # The name stays with the first one seen.
        assert resolve_company(db, "Mercury").company.id == first.id


def test_agency_wording_names_the_employer_and_the_agency(session_factory):
    with session_factory() as db:
        via = resolve_company(db, "Syllo (via TLA Tech)")
        assert (via.company.name, via.agency.name) == ("Syllo", "TLA Tech")
        assert via.agency.kind == "agency"
        # Once TLA Tech is known as an agency, "Agency (Employer)" reads the same way.
        parens = resolve_company(db, "TLA Tech (Syllo)")
        assert (parens.company.id, parens.agency.id) == (via.company.id, via.agency.id)

        behalf = resolve_company(db, "Northwind Staffing on behalf of Contoso")
        assert behalf.company.name == "Contoso" and behalf.agency.kind == "agency"
        known = resolve_company(db, "Robert Half for Fabrikam")
        assert (known.company.name, known.agency.name) == ("Fabrikam", "Robert Half")

        unnamed = resolve_company(db, "Robert Half (client employer unnamed)")
        assert unnamed.company.id == known.agency.id and unnamed.agency is None

        # Not agency wording: one company each.
        assert resolve_company(db, "Code for America").company.name == "Code for America"
        assert resolve_company(db, "Meta (Facebook)").company.name == "Meta (Facebook)"


def test_an_agency_names_website_isnt_given_to_its_employer(session_factory):
    with session_factory() as db:
        resolved = resolve_company(db, "Syllo (via TLA Tech)", domain="tlatech.com")
        assert resolved.company.domain is None and resolved.agency.domain is None


def test_parse_company_name_needs_a_known_agency_for_ambiguous_wording():
    never = lambda name: False  # noqa: E731
    assert parse_company_name("Acme (via Jobot)", never).agency == "Jobot"
    assert parse_company_name("Jobot (Acme)", never).agency is None
    assert parse_company_name("Jobot (Acme)", lambda name: name == "Jobot").employer == "Acme"


def test_a_model_check_merges_same_pairs_and_remembers_every_answer(session_factory):
    with session_factory() as db:
        clair = resolve_company(db, "Clair", domain="clair.com").company
        claire = resolve_company(db, "Claire").company
        resolve_company(db, "Stripe")
        resolve_company(db, "Stripes")
        ask = Ask(same=[("Clair", "Claire")])
        spent = check_pending_pairs(db, ask)
        db.commit()

        assert spent == {"model": "claude-haiku-5-5", "cost_usd": 0.001}
        assert len(ask.calls) == 1 and len(ask.calls[0]["pairs"]) == 2
        assert ask.calls[0]["pairs"][0]["a"] == {"name": "Clair", "domain": "clair.com"}
        # The older company is kept, with both names; the newer is gone.
        assert db.get(Company, claire.id) is None
        assert db.get(CompanyAlias, "claire").company_id == clair.id
        stripes = db.get(CompanyMatchCheck, ("stripe", "stripes"))
        assert (stripes.same, stripes.decided_by) == (False, "model")

        # Nothing waits, so nothing is asked, and the answers are used from now on.
        assert check_pending_pairs(db, ask) is None
        assert resolve_company(db, "Claire").company.id == clair.id
        assert resolve_company(db, "Stripes").company.id != resolve_company(db, "Stripe").company.id
        assert _pending(db) == [] and len(ask.calls) == 1


def test_chained_merges_in_one_check_end_as_one_company(session_factory):
    with session_factory() as db:
        for name in ("Acmetools", "Acmetool", "Acmetoolz"):
            resolve_company(db, name)
        assert len(_pending(db)) == 3
        ask = Ask(
            same=[
                ("Acmetools", "Acmetool"),
                ("Acmetool", "Acmetoolz"),
                ("Acmetools", "Acmetoolz"),
            ]
        )
        check_pending_pairs(db, ask)
        db.commit()
        assert _count(db, Company) == 1
        assert _count(db, CompanyAlias) == 3


def test_a_check_that_misses_pairs_settles_none_of_them(session_factory):
    with session_factory() as db:
        resolve_company(db, "Clair")
        resolve_company(db, "Claire")

        def short(payload):
            return {"cost_usd": 0.002, "answers": []}

        assert check_pending_pairs(db, short) == {"cost_usd": 0.002}
        assert _pending(db) == [("clair", "claire")]


def test_a_name_needs_a_letter_or_digit(session_factory):
    with session_factory() as db, pytest.raises(ValueError):
        resolve_company(db, "!!!")
