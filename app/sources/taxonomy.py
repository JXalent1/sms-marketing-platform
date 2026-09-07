"""What to search for, and the written claim that justifies each search.

## The one rule

**Buyers, never sellers.** He has no shortage of consignors. A supplier on this
list costs money to text, dilutes the audience and puts a competitor on his own
marketing channel.

Every group below carries a `buyer_rationale` — a sentence a human can disagree
with, written for the person working the review queue rather than for the code.
`prospect_ingest.record_prospect()` refuses a record without one, so a term
nobody justified cannot reach a reviewer. That is the first of the three places
the plan of record enforces this rule; the queue showing the rationale on every
row is the second, and `seller_or_consignor` / `competitor` being permanent
reject reasons is the third.

**A group is a set of terms that share one claim.** If a term needs a different
sentence, it is a different group — that is what makes the per-term rejection
flag (`PROSPECT_TERM_FLAG_SHARE`) mean something when it retires a term.

## The two inversions that have already caught us out

- **Estate liquidators are sellers.** They consign to him. Excluded.
- **Shell wholesalers, importers and distributors are BUYERS, and they are the
  priority group** — `decisions/009`, 2026-09-04. The plan had inferred "seller"
  from the word "wholesaler" without asking the client. A wholesaler is a
  merchant: he buys cheaply and resells at margin, so a discounted lot at
  auction is exactly what he bids on. As originally specced, P2 would have found
  Atlantic Coral Enterprise and thrown it away.

  The general form is worth more than the instance: **"do they sell this thing?"
  is the wrong question. "Would they raise a paddle for a lot of it?" is the
  question.** A trader is on both sides on different days, and being a possible
  consignor disqualifies nobody.

  `tests/test_taxonomy.py` guards the reversal in the direction it was wrong:
  no exclusion may name a wholesaler, an importer or a distributor.

The never-prospect list is the other half of this taxonomy and lives one file
over, in `app/sources/exclusions.py` — including the reasoning about which way
that matcher should fail. It is enforced in `record_prospect()`, so every source
inherits it.

## Radius belongs to the GROUP, with the category as its fallback

Also `decisions/009`. The seashell niche is three industries sharing a material,
inside one category, and they do not share a radius: two are national and the
third — crushed shell sold by the cubic yard, where freight dominates the price
— must not be. A per-category value cannot express that.

So a group states its own radius and `radius_for_group()` falls back to
`prospect_scoring.radius_for()` when it does not. The groups that must be
national say so **explicitly** rather than relying on the fallback, because the
fallback reads `PROSPECT_CATEGORY_RADIUS_MILES` from `.env` and a box whose
`.env` pins the old five-key map would quietly run them at 150 miles with
nothing on any screen saying so.

## Sweeps are geography; radius is the rule

A **sweep** is where a search is centred. A **radius** is how far a buyer will
travel to collect. They are different things and conflating them is what makes a
national group look regional: the seashell trade centres on the Gulf coast, so
groups 1 and 2 run a national sweep *and* a dense Florida one — two searches for
the same national group, because Google's national ranking buries the small Gulf
coast firms that are the whole point.
"""

from dataclasses import dataclass
from typing import Iterator, Optional, Tuple

from app.core.config import settings
from app.services import prospect_scoring

# A group that does not state a radius inherits its category's. Distinct from
# `None`, which is a stated radius meaning "national, distance does not
# constrain this buyer" — the two must not be the same value.
INHERIT = object()

# Google's Text Search takes a *bias* circle of at most 50 km, so a 150-mile
# group cannot be expressed as one request. The bias steers the search and the
# radius rule is then applied to the results by distance, in `google_places.py`.
# Written down because it looks like an arbitrary constant and is not.
MAX_BIAS_RADIUS_METERS = 50_000.0


@dataclass(frozen=True)
class Sweep:
    """Where one search is centred. `center=None` is a national search."""

    name: str
    label: str
    center: Optional[Tuple[float, float]] = None


def home_sweep() -> Sweep:
    """The auction house itself. Read at call time so `.env` still moves it."""
    return Sweep("home", "Around the auction house",
                 (settings.PROSPECT_ORIGIN_LAT, settings.PROSPECT_ORIGIN_LON))


# Sanibel / Fort Myers. The centre of the American shell trade, and far enough
# from Fort Lauderdale that a search centred there returns a different set.
GULF_COAST = Sweep("gulf_coast", "Gulf coast shell trade", (26.4615, -82.0906))
NATIONAL = Sweep("national", "United States")


@dataclass(frozen=True)
class TermGroup:
    """A set of search terms that share one claim about why these people bid."""

    slug: str
    label: str
    category_slug: str
    terms: Tuple[str, ...]
    buyer_rationale: str
    # `INHERIT` (the category's value), `None` (national) or a number of miles.
    radius_miles: object = INHERIT
    sweeps: Tuple[Sweep, ...] = ()
    # 1 is "run this first". Only meaningful within a category, and it is the
    # client's ordering, not a score: `decisions/009` puts shell wholesalers
    # first and demotes retail shell shops to fourth.
    priority: int = 2


# ─── The taxonomy ───────────────────────────────────────────────────────────
#
# Bias every term toward **businesses that answer their own phone**. A
# restaurant lists a landline; a food truck lists the owner's cell, because the
# business is the person. That distinction is worth more than any other
# targeting choice here — it is the difference between a 30% and a 70% mobile
# rate, and every landline costs $0.0025 to discover and returns nothing.

GROUPS: Tuple[TermGroup, ...] = (
    TermGroup(
        slug="food_service_mobile",
        label="Mobile food businesses",
        category_slug="food_service",
        priority=1,
        terms=("food truck", "mobile food vendor", "food cart",
               "concession trailer", "mobile bartender", "mobile catering"),
        buyer_rationale=(
            "A food truck is fitted out on a budget and the owner answers his "
            "own phone. Used fryers, prep tables and refrigeration are exactly "
            "what he buys, and he can tow it home the same day."),
    ),
    TermGroup(
        slug="food_service_kitchens",
        label="Small and new kitchens",
        category_slug="food_service",
        terms=("ghost kitchen", "commissary kitchen", "caterer", "deli",
               "juice bar", "coffee shop", "bakery"),
        buyer_rationale=(
            "A small kitchen fits out with used prep and refrigeration rather "
            "than new — a walk-in cooler at auction is a third of dealer price "
            "and it is close enough to collect."),
    ),
    TermGroup(
        slug="equipment_owner_operators",
        label="Owner-operator trades",
        category_slug="equipment",
        priority=1,
        terms=("mobile welder", "tree service", "landscaping contractor",
               "hauling service", "junk removal service",
               "pressure washing service", "handyman service"),
        buyer_rationale=(
            "An owner-operator buys tools and machinery below dealer price, "
            "and the business is the person — the number on the listing is his "
            "mobile, not a switchboard."),
    ),
    TermGroup(
        slug="equipment_shops",
        label="Small shops and yards",
        category_slug="equipment",
        terms=("machine shop", "small engine repair", "equipment rental",
               "auto repair shop", "welding shop"),
        buyer_rationale=(
            "A small shop replaces machinery at auction rather than at list "
            "price, and a rental yard buys its fleet the same way. Both send a "
            "truck for it."),
    ),
    TermGroup(
        slug="estates_resale",
        label="Resale, staging and renovation",
        category_slug="estates",
        terms=("interior designer", "home stager", "house flipper",
               "property renovation company", "resale shop", "thrift store",
               "used furniture store"),
        buyer_rationale=(
            "They buy furnishings and inventory to place in a job or resell on "
            "their own floor. Note the trap the plan of record names: resale "
            "and thrift shops BUY, while consignment shops take goods on "
            "consignment and rarely do — they read alike and behave "
            "oppositely."),
    ),
    TermGroup(
        slug="memorabilia_dealers",
        label="Card, coin, comic and pawn dealers",
        category_slug="memorabilia",
        priority=1,
        # National, and stated rather than inherited. This is the group that
        # supplies the volume — 9,755 pawn shops and 3,256 sports card stores
        # nationally — so a silent fallback to 150 miles would cost the whole
        # target on the first run.
        radius_miles=None,
        terms=("sports card shop", "trading card store", "comic book store",
               "coin dealer", "pawn shop", "collectibles dealer",
               "vintage toy store", "record store"),
        buyer_rationale=(
            "A dealer buys inventory for his own shelves and a discounted lot "
            "is his margin. Distance does not constrain him — a signed rookie "
            "card ships in an envelope."),
    ),
    TermGroup(
        slug="general_resellers",
        label="Flea-market and closeout resellers",
        category_slug="general",
        terms=("flea market vendor", "discount store", "closeout store",
               "surplus store", "overstock store", "swap meet vendor"),
        buyer_rationale=(
            "Pallets and mixed lots to break down and resell. The margin is in "
            "the discount, which is what a general lot at auction is, and they "
            "collect it themselves."),
    ),
    TermGroup(
        slug="marine_trade",
        label="Marine trade",
        category_slug="marine",
        # National, stated. `PROSPECT_CATEGORY_RADIUS_MILES` had no `marine`
        # key at all before this session, so the whole category fell through to
        # the 150-mile default — see `decisions/009`.
        radius_miles=None,
        terms=("yacht broker", "marina services", "boat repair yard",
               "boat detailing service", "marine refit yard", "boat dealer"),
        buyer_rationale=(
            "Yards and brokers buy equipment, tenders and fittings at a "
            "fraction of chandlery price. A boat is moved on its own bottom or "
            "on a trailer, so the market is national."),
    ),

    # ─── Seashells: three industries sharing a material ─────────────────────
    # `decisions/009`. Treating them as one group is what produced the original
    # error, and they do not share a radius.

    TermGroup(
        slug="shell_wholesale",
        label="Shell wholesalers, importers and distributors",
        category_slug="seashells",
        priority=1,
        radius_miles=None,
        sweeps=(NATIONAL, GULF_COAST),
        terms=("seashell wholesaler", "seashell importer",
               "shell distributor", "bulk seashell supplier",
               "wholesale seashells", "coral and shell importer"),
        buyer_rationale=(
            "A shell wholesaler is a merchant: containers in, sold on by the "
            "pound and the case. A discounted lot at auction is inventory at "
            "his own margin, which is his entire business — this is the group "
            "the client named first. He may also consign surplus one day, and "
            "that disqualifies nobody from bidding."),
    ),
    TermGroup(
        slug="shell_makers",
        label="Businesses that use shells as material",
        category_slug="seashells",
        priority=1,
        radius_miles=None,
        sweeps=(NATIONAL, GULF_COAST),
        terms=("shell decor manufacturer", "seashell furniture maker",
               "shell mosaic fabricator", "shell craft manufacturer",
               "coastal interior designer", "shell mirror maker",
               "shell wall installer"),
        buyer_rationale=(
            "Shells are the raw input for what they build — the tables, the "
            "mirrors and the wall designs. A pallet of shells is a bill of "
            "materials, and it ships anywhere."),
    ),
    TermGroup(
        slug="shell_aggregate",
        label="Shell aggregate and landscape supply",
        category_slug="seashells",
        # **Regional, and this is not a detail.** Crushed shell is sold by the
        # cubic yard: heavy, low value per ton, freight dominating its price.
        # Nobody buys a yard of it from a thousand miles away, so "can they
        # collect it" binds harder here than it does for a walk-in cooler.
        radius_miles=150,
        sweeps=(),                                    # home sweep only
        terms=("crushed shell supplier", "shell rock supplier",
               "shell driveway material", "landscape supply yard",
               "decorative aggregate supplier", "hardscape contractor"),
        buyer_rationale=(
            "Crushed and washed shell by the cubic yard for driveways, paths "
            "and hardscape. A bulk lot is job material at a discount, and the "
            "whole decision is whether a truck can fetch it."),
    ),
    TermGroup(
        slug="shell_retail",
        label="Retail shell and beach shops",
        category_slug="seashells",
        # Demoted, not removed. They were the plan's entire model of this niche
        # and the client did not name them; `PROSPECT_TERM_FLAG_SHARE` will
        # retire them on their own evidence if they find the wrong side of the
        # room.
        priority=4,
        radius_miles=None,
        sweeps=(NATIONAL,),
        terms=("seashell shop", "beach gift shop", "nautical decor store",
               "coastal souvenir shop", "aquarium and reef shop"),
        buyer_rationale=(
            "A shell shop buys inventory to stock its shelves — the shop is "
            "the customer here, not the supplier."),
    ),
)


# ─── Lookups ────────────────────────────────────────────────────────────────

def group(slug: str) -> TermGroup:
    for item in GROUPS:
        if item.slug == slug:
            return item
    raise ValueError(f"Unknown search-term group {slug!r}. Known: "
                     f"{', '.join(g.slug for g in GROUPS)}")


def groups_for_category(category_slug: str) -> Tuple[TermGroup, ...]:
    return tuple(g for g in GROUPS if g.category_slug == category_slug)


def categories() -> Tuple[str, ...]:
    seen = []
    for item in GROUPS:
        if item.category_slug not in seen:
            seen.append(item.category_slug)
    return tuple(seen)


def radius_for_group(item: TermGroup) -> Optional[int]:
    """Miles this group's buyers will travel, or None for national.

    The group's own value wins; `INHERIT` falls back to the category's, which is
    `prospect_scoring.radius_for()` — the same function the scorer's distance
    cliff reads, so a search and the score it produces cannot disagree about how
    far is too far.
    """
    if item.radius_miles is INHERIT:
        return prospect_scoring.radius_for(item.category_slug)
    return item.radius_miles


def sweeps_for(item: TermGroup) -> Tuple[Sweep, ...]:
    """Where this group is searched from. Defaults to the auction house."""
    return item.sweeps or (home_sweep(),)


@dataclass(frozen=True)
class Search:
    """One planned request: a term, a place to centre it, and the radius rule."""

    group: TermGroup
    term: str
    sweep: Sweep
    radius_miles: Optional[int]

    @property
    def query(self) -> str:
        """The text sent to the API.

        A national sweep names the country in the query because there is no
        circle to centre it on, and a request with neither lands wherever the
        caller's IP says it is — which on a datacenter box is nowhere useful.
        """
        return self.term if self.sweep.center else f"{self.term} in the United States"

    @property
    def bias_meters(self) -> Optional[float]:
        if self.sweep.center is None:
            return None
        return MAX_BIAS_RADIUS_METERS

    @property
    def ledger_key(self) -> str:
        """How this search is recorded on `scrape_jobs.search_term`.

        The sweep is part of the key: the same term centred on the Gulf coast
        is a different search returning a different set, and collapsing the two
        would make one of them look already-run and skip it.
        """
        return f"{self.group.slug}:{self.sweep.name}:{self.term}"


def searches(group_slugs=None, category_slug: str = None) -> Iterator[Search]:
    """Every planned search, in priority order then declaration order.

    Priority first because the client's first live run is a subset: memorabilia
    and seashells, both national, which is where the volume and the conversion
    are. Which sweep runs first is a scheduling decision and it is Jordan's.
    """
    chosen = GROUPS
    if group_slugs is not None:
        wanted = tuple(group_slugs)
        chosen = tuple(group(slug) for slug in wanted)
    if category_slug is not None:
        chosen = tuple(g for g in chosen if g.category_slug == category_slug)

    for item in sorted(chosen, key=lambda g: (g.priority, GROUPS.index(g))):
        radius = radius_for_group(item)
        for sweep in sweeps_for(item):
            for term in item.terms:
                yield Search(group=item, term=term, sweep=sweep,
                             radius_miles=radius)
