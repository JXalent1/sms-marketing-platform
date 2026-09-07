"""The line-type cache. A number is looked up once, ever.

`app/sms/lookup.py` owns the carrier conversation; this owns the table in front
of it. The split is the same one that keeps the send path portable, and it is
also what makes the money argument work: the provider charges per call, so the
value of this module is the calls it does *not* make.

## The cache is the feature

A carrier lookup is about $0.0025. Screening 10,000 scraped businesses is $25 —
once. Without a persistent cache it is $25 every time a search is re-run,
every time a second source finds the same restaurant, and every time somebody
reloads the review queue. `phone_lookups.phone` is uniquely indexed, and that
constraint is the guarantee; this module is the only writer.

## An answer is permanent. A failure is not.

`status` separates them and the distinction is worth stating twice. A carrier
that says "landline" has answered, and asking again next month cannot produce a
different truth about a copper line — that row is final. A carrier that times
out has not answered, and freezing that into the cache would turn one bad
afternoon into a permanent hole: every number screened during it filed
`unknown`, and `unknown` is not promote-eligible, so those businesses would sit
in the review queue forever with nothing on screen explaining why.

## Why `unknown` cannot be promoted

The gate's whole claim is "we know this number can receive a text". A number
nobody has screened has not passed the gate — it has skipped it. Promoting it
would put the 2,526-landline campaign back, one prospect at a time, and the
error is asymmetric: a mobile wrongly held back waits in a queue, while a
landline wrongly promoted is paid for on every send from now on. Make the cheap
error.

`toll_free` is excluded on the same reasoning. Toll-free messaging exists, but a
scraped toll-free number is a switchboard rather than a person, and this
pipeline is looking for owners who answer their own phone.

## Two things this module refuses to spend on

**Money it does not have.** Lookups and sends draw on the same carrier balance.
A 10,000-number screening run takes $25 out of the pot the campaign pre-flight
check measures, so an overnight scrape can make the next morning's campaign
refuse to start — and nothing on any screen would connect the two events.
`PROSPECT_LOOKUP_MONTHLY_CAP` is a hard ceiling checked **before** each call,
never after, so the balance is never partially drained past it and what went
unscreened is logged rather than discovered.

**Numbers nobody will ever text.** A permanently rejected prospect, a blocklisted
number, and anything already in this table are all money for an answer that
changes nothing. The first was P1's own defect: the docstring said rejected
records were not paid for and the query said otherwise. The rule lives in
`unusable_numbers()` and is enforced inside `line_type_for()` — the one place a
call is actually made — because a guard with one call site is a guard on one
path, and this one has two public entry points.
"""

from decimal import Decimal, InvalidOperation
from datetime import datetime
from typing import Iterable, Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.blocked_number import BlockedNumber
from app.models.prospect import ProspectRejection
from app.models.scrape import PhoneLookup
from app.sms.lookup import (
    LINE_TYPES, LineTypeProvider, cost_per_lookup, get_lookup_provider,
)
from app.sms.phone import normalize, is_valid, scrub_provider_text
import logging

logger = logging.getLogger("lookup")

# `cost_per_lookup` is imported rather than defined: the carrier charges per
# call, so the price belongs next to the carrier conversation in
# `app/sms/lookup.py`, and the provider — which cannot import a service — needs
# the same number this module's spend cap is measured in. It stays reachable as
# `lookup_service.cost_per_lookup()` because that is what this layer already
# calls it.

# The line types a prospect may be promoted on. See the module docstring for why
# `unknown` and `toll_free` are not in it. This tuple is the single definition —
# `prospect_service` imports it rather than repeating the membership test, so
# the promote guard and the queue's "promote-eligible" filter cannot disagree
# about which numbers the gate lets through.
PROMOTABLE_LINE_TYPES = ("mobile", "voip")

# What a reviewer is told about a number the gate stopped. Cause, and the
# remedy that actually works on this object — decision 006's shape. Neither
# names a carrier.
LANDLINE_REFUSAL = (
    "This is a landline, so a text to it would fail and still be charged for. "
    "It stays on the list with its line type recorded, so the same number found "
    "by another search is known instantly and never looked up again."
)
TOLL_FREE_REFUSAL = (
    "This is a toll-free switchboard rather than somebody's handset. It stays "
    "on the list with its line type recorded, so the same number found by "
    "another search is known instantly and never looked up again."
)
UNSCREENED_REFUSAL = (
    "This number has not been screened yet, so there is no way to tell whether "
    "a text would reach it. Screening runs with the discovery job; until it "
    "has, nothing here can be promoted."
)

REFUSALS = {
    "landline": LANDLINE_REFUSAL,
    "toll_free": TOLL_FREE_REFUSAL,
}


def refusal_for(line_type: Optional[str]) -> Optional[str]:
    """Why the gate stops this line type, or None when it does not stop it.

    Written as a lookup with a fall-through rather than as a chain of `if`s so
    that a line type added to `LINE_TYPES` and forgotten here gets the
    unscreened refusal — the safe answer — instead of falling out with None and
    being promoted.
    """
    if line_type in PROMOTABLE_LINE_TYPES:
        return None
    return REFUSALS.get(line_type, UNSCREENED_REFUSAL)


def _charged(reported: str, answered: bool, provider_name: str) -> Decimal:
    """What one completed lookup cost us, as Decimal, and never an exception.

    A provider reports its own spend because only it knows whether the call it
    just made was billable. When it does not say, an answered lookup is charged
    at the configured price and an unanswered one at nothing — a call that never
    reached the carrier is not a charge.

    Unparseable is treated as zero and logged rather than raised. The raise
    would land *after* the money was spent and *before* the row was written, so
    it would throw away an answer we had paid for and take the rest of the
    screening pass with it — the failure `LineTypeProvider.lookup()`'s "must not
    raise" contract exists to prevent, arriving one layer down.
    """
    if reported:
        try:
            return Decimal(str(reported))
        except InvalidOperation:
            logger.error(
                "The line-type provider %r reported a cost of %r, which is not "
                "a number. Recording the lookup as free — the monthly screening "
                "cap will under-count by one call.", provider_name, reported)
            return Decimal("0")
    return cost_per_lookup() if answered else Decimal("0")


def monthly_cap() -> Decimal:
    """The ceiling on screening spend for one calendar month, as Decimal.

    Zero or less means screening is switched off, not unlimited. That reading is
    deliberate and it is the same asymmetry the rest of this module runs on: a
    misread cap that stops screening leaves prospects in a queue, and a misread
    cap that permits it drains the balance campaigns send from.
    """
    return Decimal(str(settings.PROSPECT_LOOKUP_MONTHLY_CAP))


def spend_this_month(db: Session, month: Optional[str] = None) -> Decimal:
    """What screening has already cost us this calendar month.

    Summed from `phone_lookups.cost` over rows whose last attempt falls in the
    month, which works because **this module is the only writer of that
    column**. `looked_up_at` has no server default, so there is no second writer
    keeping a different clock and no second ISO spelling — the failure mode
    `contact_list_members.added_at` has, where SQLite's `CURRENT_TIMESTAMP`
    wrote UTC alongside our local `datetime.now()` and every defaulted row read
    up to five hours newer than it was. `tests/test_lookup_provider.py` asserts
    that single-writer property rather than trusting this paragraph.

    Rows whose call failed before the carrier billed us carry no cost and
    contribute nothing, which is correct: a timeout is not a charge.
    """
    month = month or datetime.now().strftime("%Y-%m")
    rows = (db.query(PhoneLookup.cost)
            .filter(PhoneLookup.cost.isnot(None),
                    PhoneLookup.looked_up_at.like(f"{month}%"))
            .all())
    return sum((Decimal(cost) for (cost,) in rows), Decimal("0"))


class LookupBudget:
    """How much of this month's cap is left, carried through one screening pass.

    Read from the database once and then decremented in memory. The alternative
    — re-summing the table before each of ten thousand calls — is a query per
    number on the one path whose whole purpose is to be run over ten thousand
    numbers.

    `spent` is seeded from what is already recorded, so a pass that starts on a
    box which has screened all month inherits that spend rather than starting
    from zero. The cap is monthly, not per-run; a per-run cap would be no cap at
    all the moment anything runs twice.
    """

    def __init__(self, cap: Decimal, spent: Decimal):
        self.cap = cap
        self.spent = spent

    @classmethod
    def for_month(cls, db: Session, month: Optional[str] = None) -> "LookupBudget":
        return cls(monthly_cap(), spend_this_month(db, month))

    @property
    def remaining(self) -> Decimal:
        return self.cap - self.spent

    def allows(self, cost: Decimal) -> bool:
        """Is there room for one more call of this price?

        A cap of zero refuses regardless of the price, including a price of
        zero. Otherwise a free provider under a switched-off cap would screen
        without limit, which is the reading that turns "off" into "unlimited".
        """
        if self.cap <= 0:
            return False
        return cost <= self.remaining

    def charge(self, cost: Decimal) -> None:
        self.spent += cost


def unusable_numbers(db: Session, phones: Iterable[str]) -> set:
    """The subset of `phones` we must never pay to look up.

    Two populations, both permanent, both meaning the same thing here: whatever
    the carrier answers about this number, nobody will ever act on it.

      - **Permanently rejected prospects.** A human said no; `record_prospect()`
        suppresses the number on arrival from every future source, so it never
        enters this cache — which means every nightly re-run of the search that
        keeps finding it would be a fresh charge for an answer nobody wants.
      - **Blocklisted numbers.** An opt-out or an auto-block. `promote()`
        refuses them, so a line type for one buys nothing at all.

    Queried against the two tables directly rather than through
    `prospect_ingest.is_suppressed()` and `blocklist_service.is_blocked()`,
    because those answer about one number and this runs over a whole scrape, and
    because `prospect_ingest` reaches this module through `prospect_service` —
    the reverse import is a
    cycle. That leaves two statements of one rule, which is the shape this
    codebase keeps getting bitten by, so the test asserts the *property* that
    they agree rather than pinning either.
    """
    wanted = [p for p in {normalize(p) for p in phones} if p]
    if not wanted:
        return set()

    rejected = {row[0] for row in
                db.query(ProspectRejection.phone)
                .filter(ProspectRejection.phone.in_(wanted)).all()}
    blocked = {row[0] for row in
               db.query(BlockedNumber.phone)
               .filter(BlockedNumber.phone.in_(wanted)).all()}
    return rejected | blocked


def cached_line_types(db: Session, phones: Iterable[str]) -> dict:
    """{E.164: line_type} for the numbers this table has *answered* about.

    Error rows are excluded, so a caller reading this to decide "do I need to
    look these up" gets the retryable ones back in its miss list.
    """
    wanted = [p for p in {normalize(p) for p in phones} if p]
    if not wanted:
        return {}
    rows = (db.query(PhoneLookup.phone, PhoneLookup.line_type)
            .filter(PhoneLookup.phone.in_(wanted), PhoneLookup.status == "ok")
            .all())
    return {phone: line_type for phone, line_type in rows}


def _refused(reason: str) -> dict:
    """The answer when no call was made because we declined to make one.

    `ok=False` and no row: a number we refused to spend on has not been screened
    and must stay screenable later — the cap lifts next month and a blocklisted
    number can be unblocked. Writing `unknown` down here would be caching a
    decision of ours as though the carrier had answered, which is the same
    mistake as caching a timeout.
    """
    return {"line_type": "unknown", "cached": False, "ok": False,
            "skipped": reason}


def line_type_for(db: Session, phone: str, provider: LineTypeProvider = None,
                  budget: "LookupBudget" = None,
                  unusable: Optional[set] = None) -> dict:
    """Screen one number. Returns {line_type, cached, ok, skipped}.

    `cached=True` means no call was made and nothing was spent. That flag is
    what the job cost record counts, and what the acceptance criterion asserts
    on — elapsed time would pass whether or not the cache existed. `skipped`
    names the reason no call was made when the answer is not an answer:
    `spend_cap` or `not_usable`, and None on every ordinary path.

    `budget` and `unusable` are the two things a batch can work out once for the
    whole pass. Passing them in is an optimisation and nothing more — **the
    rules themselves live here**, at the one place a call is actually made, so
    that a caller which forgets to pass either still cannot spend money on a
    rejected number or past the monthly ceiling. P1's bug was a rule that
    existed on one of two paths; this is the same rule reached from both.
    """
    normalized = normalize(phone)
    if not normalized or not is_valid(normalized):
        return {"line_type": "unknown", "cached": False, "ok": False,
                "skipped": None}

    row = db.query(PhoneLookup).filter(PhoneLookup.phone == normalized).first()
    if row is not None and row.status == "ok":
        return {"line_type": row.line_type, "cached": True, "ok": True,
                "skipped": None}

    # Everything below this line costs money, so both guards sit above the call
    # and neither is advisory.
    if unusable is None:
        unusable = unusable_numbers(db, [normalized])
    if normalized in unusable:
        return _refused("not_usable")

    owns_budget = budget is None
    budget = budget or LookupBudget.for_month(db)
    if not budget.allows(cost_per_lookup()):
        if owns_budget:
            # Only the caller that created the budget logs, so a screening pass
            # over ten thousand numbers writes one line naming the total rather
            # than ten thousand naming themselves. `screen()` does the same for
            # its own pass.
            logger.error(
                "Line-type lookup refused: the monthly screening cap of $%s is "
                "reached ($%s spent). 1 number went unscreened and no charge was "
                "made. It will be screened when the month rolls over, or sooner "
                "if PROSPECT_LOOKUP_MONTHLY_CAP is raised.",
                budget.cap, budget.spent)
        return _refused("spend_cap")

    provider = provider or get_lookup_provider()
    result = provider.lookup(normalized)

    if result.line_type not in LINE_TYPES:
        # Belt and braces: LineTypeResult validates on construction, so this can
        # only be reached by a provider that returned something that is not one.
        raise ValueError(f"{provider.name} answered {result.line_type!r}, which "
                         f"is not a line type")

    now = datetime.now().isoformat()
    # Carrier free text, scrubbed before it is stored. The one time this
    # codebase let raw carrier text into a column at volume it surfaced on the
    # client's Opt-outs page; storing it clean means nothing downstream has to
    # remember.
    error = scrub_provider_text(result.error) if result.error else None
    # Parsed here and written back canonical, so the column this module's
    # monthly total is summed from can only ever hold something `Decimal` can
    # read. A provider is a third party's SDK behind one of our classes, and a
    # cost string we cannot parse must not cost us the answer we have already
    # paid for — nor poison every later cap check with one unreadable row.
    charged = _charged(result.cost, result.ok, provider.name)
    budget.charge(charged)
    cost = str(charged) if charged else None

    if row is None:
        db.add(PhoneLookup(
            phone=normalized,
            line_type=result.line_type,
            status="ok" if result.ok else "error",
            provider=provider.name,
            error=error,
            attempts=1,
            looked_up_at=now,
            cost=cost,
        ))
    else:
        # A retry of a previous failure. `attempts` accumulates so a number the
        # provider can never answer about is visible as such rather than looking
        # like a fresh failure every time.
        #
        # `cost` **accumulates** rather than being overwritten, and that is the
        # spend cap's arithmetic rather than bookkeeping neatness: the monthly
        # total is summed from this column, so a number billed twice inside one
        # month would otherwise count once and the cap would permit more than it
        # says. Across a month boundary this over-states slightly instead, which
        # is the direction to be wrong in — the cheap error is screening that
        # stops early, and the expensive one is a lookup run eating the balance
        # the next campaign sends on.
        total = Decimal(row.cost or "0") + charged
        row.line_type = result.line_type
        row.status = "ok" if result.ok else "error"
        row.provider = provider.name
        row.error = error
        row.attempts = (row.attempts or 0) + 1
        row.looked_up_at = now
        row.cost = str(total) if total else None
    # Committed per number rather than batched by the caller. This is a paid
    # call: a crash halfway through screening 10,000 businesses must not throw
    # away the answers already bought, and a durable row is what makes the
    # cache's "never look it up again" promise survive a restart.
    db.commit()

    return {"line_type": result.line_type, "cached": False,
            "ok": result.ok, "skipped": None}


def screen(db: Session, phones: Iterable[str],
           provider: LineTypeProvider = None) -> dict:
    """Screen many numbers. Returns {results, performed, cached, cost, skipped}.

    One SELECT for everything already answered, then one call per miss. The
    per-number path would be a query per number, which on a 10,000-row scrape is
    the difference between a screening pass and an afternoon.

    The budget and the unusable set are read once for the whole pass and handed
    down. `line_type_for()` still applies both rules itself — see its docstring
    — so this is two fewer queries per number, not a second copy of the rules.

    `skipped` counts what went unscreened and why. A pass that hits the cap
    stops spending; it does not stop, and the numbers it could not screen stay
    screenable next month. Nothing about the cap reaches a client-facing
    surface: the review queue tells him a number is unscreened, which is true,
    and our own screening budget is not his business — it is the
    `WHOLESALE_COST_PER_SEGMENT` rule with a different column name.
    """
    unique = []
    seen = set()
    for raw in phones:
        normalized = normalize(raw)
        if normalized and is_valid(normalized) and normalized not in seen:
            seen.add(normalized)
            unique.append(normalized)

    known = cached_line_types(db, unique)
    results = dict(known)
    performed = 0
    skipped = {"spend_cap": 0, "not_usable": 0}
    # What this pass actually cost, read off the budget rather than computed as
    # `rate x performed`. The two differ on the case that matters: a call the
    # carrier never billed us for — a timeout — is a lookup performed and not a
    # charge, and one module reporting our spend two ways is how the ledger and
    # the ceiling come to disagree.
    charged = Decimal("0")

    if len(known) < len(unique):
        provider = provider or get_lookup_provider()
        budget = LookupBudget.for_month(db)
        opening = budget.spent
        unusable = unusable_numbers(db, [p for p in unique if p not in known])
        for phone in unique:
            if phone in known:
                continue
            outcome = line_type_for(db, phone, provider=provider,
                                    budget=budget, unusable=unusable)
            results[phone] = outcome["line_type"]
            if outcome["skipped"]:
                skipped[outcome["skipped"]] += 1
            else:
                performed += 1

        charged = budget.spent - opening

        if skipped["spend_cap"]:
            # One line for the pass, naming the count and the remedy. The
            # operator cannot see this anywhere else: by design the client's
            # screens say only that a number is unscreened.
            logger.error(
                "Screening stopped at the monthly cap: $%s spent of $%s, %d "
                "number(s) went unscreened and nothing was charged for them. "
                "They will be screened next month, or sooner if "
                "PROSPECT_LOOKUP_MONTHLY_CAP is raised.",
                budget.spent, budget.cap, skipped["spend_cap"])

    logger.info("Screened %d numbers | %d cached | %d looked up | %d skipped",
                len(unique), len(known), performed, sum(skipped.values()))
    return {"results": results, "performed": performed, "cached": len(known),
            "cost": str(charged), "skipped": skipped}
