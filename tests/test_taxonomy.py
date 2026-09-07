"""The taxonomy, the exclusion list, and the radius rule.

Pure functions and configuration — no database, no network, no fixtures. The
end-to-end versions of these claims (an excluded business never becoming a
prospect, an out-of-radius result never being screened) live in
`test_google_places.py`; this module is about the rules themselves.

Session P2, acceptance criteria 3, 6 and 6b.
"""

import pytest

from app.core.config import settings
from app.services import prospect_scoring
from app.sources import exclusions, taxonomy


# ─── The buyer rationale is not optional ────────────────────────────────────

def test_every_group_carries_a_written_buyer_rationale():
    """The first of the three places "buyers, never sellers" is enforced.

    `record_prospect()` refuses a record without one, so a group that shipped
    with a blank would produce a search whose every result is counted `invalid`
    and never reaches a reviewer. Length is asserted because "yes" is a string.
    """
    for group in taxonomy.GROUPS:
        assert group.buyer_rationale.strip(), group.slug
        assert len(group.buyer_rationale) > 60, (
            f"{group.slug}'s rationale is too short to disagree with: "
            f"{group.buyer_rationale!r}")
        assert group.terms, f"{group.slug} has no search terms"


def test_every_search_carries_its_groups_rationale_and_a_term():
    """The rationale travels with the search, which is what reaches the queue."""
    for search in taxonomy.searches():
        assert search.term.strip()
        assert search.group.buyer_rationale.strip()
        assert search.query, search.ledger_key


def test_no_term_appears_in_two_groups():
    """A term with two rationales cannot be judged.

    `PROSPECT_TERM_FLAG_SHARE` retires a term on the *term's* rejections, and
    the queue shows one rationale per term. A term in two groups would be
    flagged on evidence gathered under a claim it was not searched under.
    """
    seen = {}
    for group in taxonomy.GROUPS:
        for term in group.terms:
            assert term not in seen, (
                f"{term!r} is in both {seen.get(term)} and {group.slug}")
            seen[term] = group.slug


def test_a_ledger_key_names_the_sweep_as_well_as_the_term():
    """Two sweeps of one term are two searches, and cost twice.

    `scrape_jobs.search_term` is the ledger the repeat-window reads. If the key
    were the bare term, running the national sweep of "seashell wholesaler"
    would mark the Gulf coast sweep of it as already done — and the Gulf sweep
    is the entire reason `decisions/009` gives for running two.
    """
    national = taxonomy.Search(taxonomy.group("shell_wholesale"),
                               "seashell wholesaler", taxonomy.NATIONAL, None)
    gulf = taxonomy.Search(taxonomy.group("shell_wholesale"),
                           "seashell wholesaler", taxonomy.GULF_COAST, None)
    assert national.ledger_key != gulf.ledger_key


# ─── Criterion 3: the radius rule, per category ─────────────────────────────

@pytest.mark.parametrize("category_slug,expected_miles", [
    ("food_service", 150),
    ("equipment", 150),
    ("general", 150),
    ("estates", 100),
    ("memorabilia", None),
    ("marine", None),
    ("seashells", None),
])
def test_the_configured_radius_for_each_category(category_slug, expected_miles):
    """The plan of record's table, asserted as configuration.

    `marine` and `seashells` are the two `decisions/009` found missing: absent
    from the map they fell through to `PROSPECT_DEFAULT_RADIUS_MILES = 150`, so
    the two categories the plan has always called national would have run as
    regional searches with nothing on any screen saying so.
    """
    assert prospect_scoring.radius_for(category_slug) == expected_miles


def test_every_category_the_taxonomy_names_has_a_configured_radius():
    """No category may reach the 150-mile fallback by accident.

    The fallback is deliberately conservative and is the right answer for a
    category nobody has thought about. It is the wrong answer for one that is in
    the taxonomy, and the failure is silent — which is precisely how `marine`
    spent this whole project running at 150 miles on paper.
    """
    configured = settings.PROSPECT_CATEGORY_RADIUS_MILES or {}
    missing = [slug for slug in taxonomy.categories() if slug not in configured]
    assert not missing, (
        f"{missing} are searched by the taxonomy and have no radius entry, so "
        f"they silently inherit PROSPECT_DEFAULT_RADIUS_MILES")


# ─── Criterion 6b: the three seashell groups, individually ──────────────────

def test_the_three_seashell_groups_have_three_different_radius_rules():
    """`decisions/009`, asserted one group at a time.

    Two national and one regional **inside one category**, which is the whole
    reason radius became a property of the search-term group. A single
    per-category value cannot pass all three of these, and that is the defect
    this test exists to catch.
    """
    wholesale = taxonomy.group("shell_wholesale")
    makers = taxonomy.group("shell_makers")
    aggregate = taxonomy.group("shell_aggregate")

    assert taxonomy.radius_for_group(wholesale) is None, (
        "shell wholesalers, importers and distributors are national — they are "
        "the priority group and the trade is not local")
    assert taxonomy.radius_for_group(makers) is None, (
        "businesses that use shells as material are national — a pallet of "
        "shells is a bill of materials and it ships")
    assert taxonomy.radius_for_group(aggregate) == 150, (
        "crushed shell is sold by the cubic yard and freight dominates its "
        "price; nobody buys a yard of it from a thousand miles away")


def test_no_single_value_satisfies_all_three_seashell_groups():
    """The property criterion 6b is really about, stated as a property.

    Three assertions can all pass on one shared number if somebody later
    "simplifies" the group radius back onto the category. This fails then,
    because the three answers are not the same answer.
    """
    radii = {taxonomy.radius_for_group(taxonomy.group(slug))
             for slug in ("shell_wholesale", "shell_makers", "shell_aggregate")}
    assert len(radii) > 1, (
        "all three seashell groups resolved to the same radius, which means "
        "radius has gone back to being a property of the category")


def test_the_two_national_seashell_groups_do_not_inherit_their_radius():
    """Stated, not inherited — because the fallback reads `.env`.

    `PROSPECT_CATEGORY_RADIUS_MILES` can be pinned in a client's `.env` as the
    five-key map it was before this session, and a box with that file would run
    the priority group at 150 miles. The group states `None` so no environment
    can make it regional.
    """
    for slug in ("shell_wholesale", "shell_makers", "memorabilia_dealers",
                 "marine_trade"):
        group = taxonomy.group(slug)
        assert group.radius_miles is not taxonomy.INHERIT, (
            f"{slug} must state its own radius rather than inheriting one from "
            f"a setting an .env file can override")
        assert group.radius_miles is None


def test_the_seashell_groups_run_the_sweeps_the_decision_describes():
    """Three sweeps, not two: national, a dense Gulf coast one, and regional.

    Sweeps are geography and radius is the rule, and the two are different
    things. Groups 1 and 2 are national *and* get a Florida sweep, because the
    trade centres on the Gulf coast and a national ranking buries the small
    firms there.
    """
    for slug in ("shell_wholesale", "shell_makers"):
        names = [s.name for s in taxonomy.sweeps_for(taxonomy.group(slug))]
        assert names == ["national", "gulf_coast"], (slug, names)

    aggregate = [s.name for s in taxonomy.sweeps_for(taxonomy.group("shell_aggregate"))]
    assert aggregate == ["home"], (
        "the aggregate group is regional-only; a national sweep of it would pay "
        "to find businesses that can never collect a cubic yard of shell")

    retail = [s.name for s in taxonomy.sweeps_for(taxonomy.group("shell_retail"))]
    assert retail == ["national"]


def test_a_national_sweep_names_the_country_and_a_centred_one_biases():
    """A request with neither a circle nor a country lands on the caller's IP."""
    national = taxonomy.Search(taxonomy.group("shell_wholesale"),
                               "seashell wholesaler", taxonomy.NATIONAL, None)
    gulf = taxonomy.Search(taxonomy.group("shell_wholesale"),
                           "seashell wholesaler", taxonomy.GULF_COAST, None)

    assert national.query.endswith("in the United States")
    assert national.bias_meters is None
    assert gulf.query == "seashell wholesaler"
    assert gulf.bias_meters == taxonomy.MAX_BIAS_RADIUS_METERS


def test_the_priority_group_is_searched_first():
    """`decisions/009` names the wholesalers first and demotes retail to fourth."""
    order = [search.group.slug
             for search in taxonomy.searches(category_slug="seashells")]
    assert order[0] == "shell_wholesale"
    assert order[-1] == "shell_retail"


# ─── Criterion 6: every excluded business type, one test each ───────────────

def test_an_auction_house_is_excluded():
    assert exclusions.excluded_reason("Tidewater Auction House") is not None
    assert exclusions.excluded_reason("Palm Beach Auctioneers LLC") is not None


def test_an_estate_sale_company_is_excluded():
    assert exclusions.excluded_reason("Sunrise Estate Sales of Broward") is not None


def test_an_estate_liquidator_is_excluded():
    """The inversion the plan of record caught once already: they consign."""
    assert exclusions.excluded_reason("Sanibel Estate Liquidators LLC") is not None
    assert exclusions.excluded_reason("Coastal Estate Liquidation Co") is not None


def test_an_appraiser_is_excluded():
    assert exclusions.excluded_reason("Beacon Hill Appraisers") is not None
    assert exclusions.excluded_reason("Gulf Coast Appraisal Group") is not None


def test_a_consignment_gallery_is_excluded():
    assert exclusions.excluded_reason("Coastline Consignment Gallery") is not None


def test_a_we_buy_houses_operator_is_excluded():
    assert exclusions.excluded_reason("We Buy Houses Miami") is not None
    assert exclusions.excluded_reason("Cash For Homes South Florida") is not None


def test_every_exclusion_is_reachable_by_at_least_one_name():
    """The list itself, swept — an entry nothing can match is not a guard.

    `CLAUDE.md`: prove the check fires against a case whose answer you know
    before quoting it as evidence.
    """
    for rule in exclusions.EXCLUSIONS:
        for phrase in rule.phrases:
            name = f"Test {phrase.title()} Company"
            reason = exclusions.excluded_reason(name)
            assert reason is not None, (rule.slug, phrase, name)
            assert reason.startswith(rule.label), (rule.slug, phrase, reason)


def test_the_refusal_names_the_type_and_the_reason():
    """A count nobody can read is a count nobody acts on."""
    reason = exclusions.excluded_reason("Tidewater Auction House")
    assert "auction house" in reason.lower()
    assert "competitor" in reason.lower()


# ─── decisions/009: the reversal, guarded in the direction it was wrong ─────

@pytest.mark.parametrize("business_name", [
    "Gulf Coral Trading Co - Seashell Wholesaler",
    "Seashell Importers of Pine Island",
    "Bayfront Shell Distributors",
    "Wholesale Seashells Direct",
    "Atlantic Coral Enterprise",
    "US Shell Inc",
    "Worldwide Wildlife Products",
    "California Seashell Company",
    "Blue Seas Trading",
])
def test_a_shell_wholesaler_importer_or_distributor_is_accepted(business_name):
    """`decisions/009`, 2026-09-04. **They are buyers and they are the priority.**

    The plan of record had them in the exclusion list, inferred from the word
    "wholesaler" rather than from anything the client said. A wholesaler is a
    merchant: he buys cheaply and resells at margin, so a discounted lot at
    auction is exactly what he bids on. As originally specced, P2 would have
    found every business named here and thrown it away.

    The last five names are the real companies `decisions/009` quotes, which is
    the point — this test fails the day somebody re-adds the exclusion from the
    plan's old wording.
    """
    assert exclusions.excluded_reason(business_name) is None, (
        "decisions/009: shell wholesalers, importers and distributors are "
        "BUYERS and the priority group. Do not re-add this exclusion.")


def test_no_exclusion_phrase_names_a_wholesaler_importer_or_distributor():
    """The structural half of the same guard.

    The parametrized test above catches the exclusion coming back as a phrase
    that happens to match those names. This catches it coming back at all —
    including as a phrase whose only victims are businesses nobody thought to
    put in the list above.
    """
    forbidden = ("wholesal", "importer", "import", "distributor", "distribut")
    for rule in exclusions.EXCLUSIONS:
        for phrase in rule.phrases:
            for word in forbidden:
                assert word not in phrase.lower(), (
                    f"{rule.slug} excludes {phrase!r}. decisions/009 removed "
                    f"exactly one line from this list — shell wholesalers and "
                    f"importers — because they are the niche's best prospects.")


def test_the_priority_group_is_in_the_taxonomy_and_is_first():
    """The other direction: not merely un-excluded, but actually searched."""
    group = taxonomy.group("shell_wholesale")
    assert group.priority == 1
    assert any("wholesaler" in term for term in group.terms)
    assert any("importer" in term for term in group.terms)
    assert any("distributor" in term for term in group.terms)


# ─── The matcher's own failure modes ────────────────────────────────────────

def test_a_liquidation_store_is_not_an_estate_liquidator():
    """One word, two industries, opposite sides of the paddle.

    A liquidation or closeout store buys stock cheaply to resell — `closeout
    store` is one of our own search terms. `estate liquidat` is the phrase that
    separates it from the company that consigns an estate to the auction house.
    A bare `liquidat` would have taken both, which is the ambiguity
    `decisions/004` removed `"unreachable"` for.
    """
    assert exclusions.excluded_reason("Broward Liquidation Outlet") is None
    assert exclusions.excluded_reason("Sunshine Closeout Store") is None
    assert exclusions.excluded_reason("Miami Estate Liquidators") is not None


def test_a_resale_or_thrift_shop_is_not_a_consignment_gallery():
    """The trap the plan of record names: they read alike and behave oppositely."""
    assert exclusions.excluded_reason("Hollywood Thrift & Resale") is None
    assert exclusions.excluded_reason("Second Chance Resale Shop") is None
    assert exclusions.excluded_reason("Las Olas Consignment Gallery") is not None


def test_a_phrase_matches_at_a_word_boundary_rather_than_anywhere():
    """`21610` matched a Brevard County phone number. Same defect class.

    An unanchored substring matches things that are not words of that kind, so
    the phrases are anchored to the start of a word. They are deliberately open
    at the *end* — that is what catches `auctioneers` and `appraisals` from one
    entry — and that asymmetry is the whole rule.
    """
    assert exclusions.excluded_reason("Precaution Safety Supply") is None
    assert exclusions.excluded_reason("Reappraisal Analytics") is None
    assert exclusions.excluded_reason("Broward Auctioneers") is not None
    assert exclusions.excluded_reason("Gulf Appraisals Inc") is not None


def test_punctuation_and_case_do_not_defeat_the_matcher():
    for name in ("TIDEWATER AUCTION HOUSE", "Tidewater  Auction-House",
                 "Tidewater, Auction House Inc.", "tidewater auction house"):
        assert exclusions.excluded_reason(name) is not None, name


def test_a_business_with_no_name_is_not_excluded():
    """A missing name is a source's gap, not evidence of anything."""
    assert exclusions.excluded_reason(None) is None
    assert exclusions.excluded_reason("") is None
    assert exclusions.excluded_reason("   ") is None
