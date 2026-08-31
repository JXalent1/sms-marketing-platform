"""Estimate against actual, at OUR wholesale cost. Operator-only, by design.

`campaigns.estimated_cost` has carried a comment since the skeleton saying it
exists so the damage can be "reconciled against the invoice afterwards". Nothing
stored the actual, so for the whole of the build there was nothing to reconcile
it against. 5f A4 captures what the carrier reported per message; this is what
reads it back.

**Nothing here may reach a client-facing surface, and there is no route that
exposes it.** `WHOLESALE_COST_PER_SEGMENT` and the `carrier_cost*` columns are
our cost. Showing them discloses our margin and under-states the client's bill
by roughly 40%, which is the leak session 1b removed and CLAUDE.md still names.
The client's report lives in `report_service.py` and is denominated in
`BILLING_PRICE_PER_SEGMENT`.

The admin login *is* the client, so "operator-only" cannot mean "behind auth" —
it has to mean "not served". The reader is `scripts/cost_report.py`, run by us
on the box, plus a log line at campaign completion.

Why it is worth having: `WHOLESALE_COST_PER_SEGMENT` is 0.009 and is a
deliberate over-reserve for the capacity guard rather than a measurement. The
real blended rate is believed to be around half that, per-carrier pass-through
differs, and until this existed nobody could prove either without subtracting
account balances by hand. The true figure is per-campaign, not a constant, which
is why this reports by campaign.

Decimal end to end. The carrier reports amounts as strings and they are summed
as `Decimal(str(...))` — a Float column between the carrier's figure and ours is
the defect 1b fixed one layer up, and here it would be summed four thousand
times before anybody looked at it.
"""

import logging
from decimal import Decimal, InvalidOperation
from typing import Optional

from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.sms_message import BILLABLE_STATUSES, SMSMessage
from app.services.campaign_builder import wholesale_cost

logger = logging.getLogger("campaign")


def _amount(raw: Optional[str]) -> Optional[Decimal]:
    """A carrier-reported amount as an exact Decimal, or None.

    None rather than zero for anything unreadable. A message the carrier priced
    at nothing and a message it did not price are different facts, and treating
    the second as $0.00 would make an unpriced campaign look free — the
    direction nobody sanity-checks.
    """
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return Decimal(str(raw).strip())
    except (InvalidOperation, ValueError):
        return None


def record(message: SMSMessage, result) -> None:
    """Copy a carrier's cost fields off a `SendResult` onto the message row.

    The only writer of the four `carrier_cost*` columns, living beside their
    only reader. Keeping the pair in one module is what makes "this is our cost
    and it does not leave the server" a property of a file rather than a habit.

    Assigned even when the carrier said nothing: None is the honest record of an
    unpriced message — most carriers price at delivery rather than at
    submission — and `_amount()` above is what turns that back into "not
    counted" rather than "free".
    """
    message.carrier_cost = result.cost
    message.carrier_cost_rate = result.cost_rate
    message.carrier_cost_fee = result.cost_carrier_fee
    message.carrier_cost_currency = result.cost_currency


def reconcile(db: Session, campaign_id: int) -> Optional[dict]:
    """Estimated wholesale cost against what the carrier actually charged.

    `coverage` is the number the reader has to look at first: a total assembled
    from 12 priced messages out of 4,200 is not a campaign's cost, and reporting
    it without saying so is how an over-reserve gets "confirmed" by a sample.
    Most carriers price at delivery rather than at submission, so a fresh
    campaign legitimately has low coverage for a few minutes.
    """
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        return None

    rows = (db.query(SMSMessage.segments, SMSMessage.carrier_cost,
                     SMSMessage.carrier_cost_rate, SMSMessage.carrier_cost_fee,
                     SMSMessage.carrier_cost_currency)
            .filter(SMSMessage.campaign_id == campaign_id,
                    SMSMessage.status.in_(BILLABLE_STATUSES)).all())

    actual = Decimal("0")
    rate_total = Decimal("0")
    fee_total = Decimal("0")
    priced = 0
    segments = 0
    currencies = set()

    for row_segments, cost, rate, fee, currency in rows:
        segments += int(row_segments or 0)
        amount = _amount(cost)
        if amount is None:
            continue
        priced += 1
        actual += amount
        rate_total += _amount(rate) or Decimal("0")
        fee_total += _amount(fee) or Decimal("0")
        if currency:
            currencies.add(currency.upper())

    estimate = wholesale_cost(segments)
    return {
        "campaign_id": campaign.id,
        "campaign_name": campaign.name,
        "messages": len(rows),
        "priced_messages": priced,
        # 0.0 when nothing is priced, and the caller is expected to say so
        # rather than print a total. See the docstring.
        "coverage": round(priced / len(rows), 4) if rows else 0.0,
        "segments": segments,
        "estimated": estimate,
        "actual": actual if priced else None,
        "rate_component": rate_total if priced else None,
        "carrier_fee_component": fee_total if priced else None,
        # Reported rather than reconciled away. Summing two currencies into one
        # figure is the kind of arithmetic that is wrong quietly; if this is ever
        # longer than one entry the total above is meaningless and the caller
        # has to say so.
        "currencies": sorted(currencies),
        "effective_rate_per_segment": (actual / segments) if (priced and segments)
        else None,
    }


def log_reconciliation(db: Session, campaign_id: int) -> None:
    """One INFO line per finished campaign, in our log and nowhere else.

    The cheapest possible reader. It exists so the figure is visible without
    anyone remembering to run a script, and it is in the log rather than on a
    screen because the only screen belongs to the client.
    """
    summary = reconcile(db, campaign_id)
    if not summary or not summary["priced_messages"]:
        return
    logger.info(
        "Campaign #%s wholesale reconciliation | %s segments | estimate $%s | "
        "actual $%s (%s of %s messages priced, rate $%s + carrier fee $%s)",
        summary["campaign_id"], summary["segments"],
        summary["estimated"].quantize(Decimal("0.0001")),
        summary["actual"].quantize(Decimal("0.0001")),
        summary["priced_messages"], summary["messages"],
        summary["rate_component"].quantize(Decimal("0.0001")),
        summary["carrier_fee_component"].quantize(Decimal("0.0001")),
    )
