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
"""

from decimal import Decimal
from datetime import datetime
from typing import Iterable, Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.scrape import PhoneLookup
from app.sms.lookup import LINE_TYPES, LineTypeProvider, get_lookup_provider
from app.sms.phone import normalize, is_valid, scrub_provider_text
import logging

logger = logging.getLogger("lookup")

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


def cost_per_lookup() -> Decimal:
    """Our per-call spend, as Decimal. Never rendered — see the model docstring.

    Decimal from end to end rather than at the rounding step: session 1b's
    lesson is that float arithmetic on money drifts below half-cent boundaries,
    and a job cost is a sum of several thousand of these.
    """
    return Decimal(str(settings.PROSPECT_LOOKUP_COST_PER_NUMBER))


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


def line_type_for(db: Session, phone: str,
                  provider: LineTypeProvider = None) -> dict:
    """Screen one number. Returns {line_type, cached, ok}.

    `cached=True` means no call was made and nothing was spent. That flag is
    what the job cost record counts, and what the acceptance criterion asserts
    on — elapsed time would pass whether or not the cache existed.
    """
    normalized = normalize(phone)
    if not normalized or not is_valid(normalized):
        return {"line_type": "unknown", "cached": False, "ok": False}

    row = db.query(PhoneLookup).filter(PhoneLookup.phone == normalized).first()
    if row is not None and row.status == "ok":
        return {"line_type": row.line_type, "cached": True, "ok": True}

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
    cost = result.cost or (str(cost_per_lookup()) if result.ok else None)

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
        row.line_type = result.line_type
        row.status = "ok" if result.ok else "error"
        row.provider = provider.name
        row.error = error
        row.attempts = (row.attempts or 0) + 1
        row.looked_up_at = now
        row.cost = cost
    # Committed per number rather than batched by the caller. This is a paid
    # call: a crash halfway through screening 10,000 businesses must not throw
    # away the answers already bought, and a durable row is what makes the
    # cache's "never look it up again" promise survive a restart.
    db.commit()

    return {"line_type": result.line_type, "cached": False, "ok": result.ok}


def screen(db: Session, phones: Iterable[str],
           provider: LineTypeProvider = None) -> dict:
    """Screen many numbers. Returns {results, performed, cached, cost}.

    One SELECT for everything already answered, then one call per miss. The
    per-number path would be a query per number, which on a 10,000-row scrape is
    the difference between a screening pass and an afternoon.
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

    if len(known) < len(unique):
        provider = provider or get_lookup_provider()
        for phone in unique:
            if phone in known:
                continue
            outcome = line_type_for(db, phone, provider=provider)
            results[phone] = outcome["line_type"]
            performed += 1

    spent = cost_per_lookup() * performed
    logger.info("Screened %d numbers | %d cached | %d looked up",
                len(unique), len(known), performed)
    return {"results": results, "performed": performed,
            "cached": len(known), "cost": str(spent)}
