"""Session LT1 — the lighting groups, the metro sweeps and the chain exclusions.

Acceptance criteria 1-4 and 6. Pure planning and matching: nothing here touches
the database or a network, so each test stands alone (`accept-5e.sh`'s rule —
a criterion is run in isolation, which is how it will fail).
"""

import pytest

from app.sources import exclusions, metros, taxonomy

LIGHTING = ("lighting_retail", "lighting_trade", "lighting_decor")


# ─── Criterion 1: the plan ──────────────────────────────────────────────────

def test_retail_and_trade_plan_400_metro_centred_searches_in_priority_order():
    plan = list(taxonomy.searches(group_slugs=["lighting_retail", "lighting_trade"]))
    terms = {s.term for s in plan}
    assert len(terms) == 8
    assert len(plan) == 8 * len(metros.METRO_SWEEPS) == 400

    # Every one centred on a metro, and a metro from the list — not the home
    # sweep a group falls back to when it forgets to name its sweeps.
    assert all(s.sweep.center is not None for s in plan)
    assert {s.sweep for s in plan} == set(metros.METRO_SWEEPS)

    priorities = [s.group.priority for s in plan]
    assert priorities == sorted(priorities)
    # Retail before trade (declaration order inside priority 1), and the ledger
    # keys are all distinct, so no search can make another look already-run.
    groups = [s.group.slug for s in plan]
    assert groups.index("lighting_trade") == 5 * len(metros.METRO_SWEEPS)
    assert len({s.ledger_key for s in plan}) == 400


# ─── Criterion 2: the widen-the-net group runs only when named ──────────────

def test_lighting_decor_is_absent_unless_named():
    everything = {s.group.slug for s in taxonomy.searches()}
    by_category = {s.group.slug for s in taxonomy.searches(category_slug="lighting")}
    named_others = {s.group.slug for s in
                    taxonomy.searches(group_slugs=["lighting_retail", "lighting_trade"])}
    assert "lighting_decor" not in everything
    assert "lighting_decor" not in by_category
    assert "lighting_decor" not in named_others
    # And the category plan still carries the two that are not explicit-only,
    # so the filter removed one group rather than the category.
    assert by_category == {"lighting_retail", "lighting_trade"}


def test_lighting_decor_runs_when_named():
    named = list(taxonomy.searches(group_slugs=["lighting_decor"]))
    assert {s.group.slug for s in named} == {"lighting_decor"}
    assert len(named) == 3 * len(metros.METRO_SWEEPS)


# ─── Criterion 3: rationale and radius ──────────────────────────────────────

@pytest.mark.parametrize("slug", LIGHTING)
def test_each_lighting_group_has_a_rationale_and_is_national(slug):
    item = taxonomy.group(slug)
    assert item.category_slug == "lighting"
    assert item.buyer_rationale.strip()
    assert taxonomy.radius_for_group(item) is None
    # Stated, not inherited: the fallback reads `.env`, and a box pinning the
    # old map would run these at 150 miles with nothing on screen saying so.
    assert item.radius_miles is None
    assert item.radius_miles is not taxonomy.INHERIT


# ─── Criterion 4: the metro list ────────────────────────────────────────────

CONUS = (24.3, 49.5, -125.0, -66.8)       # lat min/max, lon min/max
ALASKA = (51.0, 71.5, -180.0, -129.0)
HAWAII = (18.5, 22.5, -160.5, -154.5)


def _inside(box, lat, lon):
    return box[0] <= lat <= box[1] and box[2] <= lon <= box[3]


def test_fifty_metros_with_unique_names_all_inside_the_united_states():
    sweeps = metros.METRO_SWEEPS
    assert len(sweeps) == 50
    assert len({s.name for s in sweeps}) == 50
    assert len({s.label for s in sweeps}) == 50
    assert len({s.center for s in sweeps}) == 50
    for s in sweeps:
        lat, lon = s.center
        assert any(_inside(b, lat, lon) for b in (CONUS, ALASKA, HAWAII)), s


def test_the_bounding_box_check_rejects_a_centre_outside_it():
    # Known answer first: a check that cannot fail proves nothing. London and a
    # transposed (lon, lat) pair are both outside every box.
    for lat, lon in ((51.5, -0.12), (-80.19, 25.76)):
        assert not any(_inside(b, lat, lon) for b in (CONUS, ALASKA, HAWAII))


def test_no_metro_shares_a_sweep_name_with_an_existing_sweep():
    others = {taxonomy.NATIONAL.name, taxonomy.GULF_COAST.name,
              taxonomy.home_sweep().name}
    assert not others & {s.name for s in metros.METRO_SWEEPS}


# ─── Criterion 6: chains out, independents in ───────────────────────────────

CHAINS = (
    "Lamps Plus", "The Home Depot", "Lowe's Home Improvement", "Lowes", "Lumens",
    "Ferguson Bath, Kitchen & Lighting Gallery", "RH", "RH Outlet",
    "Restoration Hardware", "Pottery Barn", "Wayfair", "Crate & Barrel",
    "Crate and Barrel", "Arhaus", "Kirkland's", "Kirklands Home", "At Home",
    "HomeGoods", "IKEA", "Walmart Supercenter", "Wal-Mart", "Target", "Costco",
    "Costco Wholesale", "Menards", "Floor & Decor", "Floor and Decor Outlets",
)

INDEPENDENTS = (
    "Capitol Lighting", "Lighting Showcase of Tampa", "ABC Lighting Distributors",
    # The word-closed phrases must not fire on ordinary words that start the
    # same way — the reason those phrases end in a space.
    "Rhode Island Lighting", "Targeted Lighting Design", "Kirkland Lighting Center",
    "Lumenstar Fixtures",
)


@pytest.mark.parametrize("name", CHAINS)
def test_every_named_chain_is_excluded(name):
    reason = exclusions.excluded_reason(name)
    assert reason is not None, name
    assert reason.startswith("National chain"), reason


@pytest.mark.parametrize("name", INDEPENDENTS)
def test_independent_lighting_stores_and_distributors_are_accepted(name):
    assert exclusions.excluded_reason(name) is None, (
        name, exclusions.excluded_reason(name))


def test_the_chain_list_names_no_wholesaler_importer_or_distributor():
    """decisions/009's guard, restated for the rule this session added to.

    "Costco Wholesale" is refused above because it starts `costco`, not because
    any phrase names a wholesaler — the distinction the guard exists to keep.
    """
    chain = next(r for r in exclusions.EXCLUSIONS if r.slug == "national_chain")
    for phrase in chain.phrases:
        for word in ("wholesal", "importer", "distribut"):
            assert word not in phrase.lower(), phrase
