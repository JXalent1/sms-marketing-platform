"""What a person reads back when checking the invoice against the dashboard.

Split from `stripe_meter.py` on the 500-line rule, along the boundary the two
already had: that module *reports* — the pass, the batch, the mark — and this
one *reads back*: it prices a window with `billing_service`'s own functions,
and it says which billable rows in a window have not reached the meter and
why. Nothing here writes, nothing here calls Stripe, and `stripe_meter`
imports nothing from here. `tools/bill_period.py` renders both halves.

## Why the breakdown exists

The property B1b guarantees is that the figure metered equals the figure
`compute_usage()` reports for the same window. When somebody reconciles an
invoice and the two differ, the question is never "are they different" — it is
"which rows, and why". A row can be unmetered because it is not settled yet,
because it is older than Stripe's 35-day timestamp horizon and needs a manual
invoice, because it predates the subscription and was settled by the one-time
balance, or because the pass simply has not run since it settled. Those are
four different remedies, and `unmetered_reason()` is the one place they are
told apart.
"""

from datetime import date, datetime
from typing import Dict, Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import sms_message as message_model
from app.models.sms_message import SMSMessage
from app.services import billing_service
from app.services.stripe_meter import (
    METER_TIMESTAMP_MAX_AGE_DAYS, row_segments, settle_cutoff,
    subscription_start, too_old_cutoff,
)

# Why a billable row in a window is not (yet) metered. `tools/bill_period.py
# --unmetered` groups by these; `unmetered_reason()` is the one implementation.
NO_SUBSCRIPTION = "no subscription start is stored, so nothing is in scope"
BEFORE_SUBSCRIPTION = "sent before the subscription began (settled by the balance)"
NO_SEND_TIME = "no send time recorded"
TOO_OLD = f"older than {METER_TIMESTAMP_MAX_AGE_DAYS} days — the meter cannot take it"
NOT_SETTLED = "not yet settled (inside the settle window, no receipt)"
AWAITING_PASS = "settled, awaiting the next metering pass"


def unmetered_reason(row: SMSMessage, now: datetime, since: Optional[date]) -> str:
    """Why this billable, unmarked row has not reached the meter. One rule.

    The order is the order the pass decides in: scope first (no subscription,
    then before it), then the 35-day horizon, then settlement. A row that
    clears all three is simply waiting for the next pass — which, if the pass
    runs hourly, should describe almost nothing for more than an hour.
    """
    if since is None:
        return NO_SUBSCRIPTION
    if not row.sent_at:
        return NO_SEND_TIME
    if row.sent_at < since.isoformat():
        return BEFORE_SUBSCRIPTION
    if row.sent_at < too_old_cutoff(now).isoformat():
        return TOO_OLD
    if (row.status not in message_model.SETTLED_STATUSES
            and row.sent_at > settle_cutoff(now).isoformat()):
        return NOT_SETTLED
    return AWAITING_PASS


def unmetered_breakdown(db: Session, start: date, end: date,
                        now: Optional[datetime] = None) -> dict:
    """The window's billable segments, split into metered and unmetered-by-reason.

    Selected the way `compute_usage()` selects — the same `sent_at` range, the
    same status set read through the model — so `metered + unmetered` is the
    figure `/usage` shows for the window, and a test asserts that rather than
    trusting this sentence.

    Also reports the residue running the other way: rows that were metered and
    have since left the billable set. That is the only thing that can make
    Stripe's total exceed the dashboard's, and it is the first question anyone
    reconciling will ask.
    """
    now = now or datetime.now()
    since = subscription_start(db)
    start_str, end_str = start.isoformat(), end.isoformat() + "T99"
    rows = (db.query(SMSMessage)
            .filter(SMSMessage.sent_at >= start_str, SMSMessage.sent_at <= end_str)
            .all())
    metered = unmetered = 0
    by_reason: Dict[str, dict] = {}
    residue = {"rows": 0, "segments": 0}
    for row in rows:
        segments = row_segments(row.segments, row.message)
        billable = row.status in message_model.BILLABLE_STATUSES
        if billable and row.metered_at:
            metered += segments
        elif billable:
            unmetered += segments
            bucket = by_reason.setdefault(unmetered_reason(row, now, since),
                                          {"rows": 0, "segments": 0})
            bucket["rows"] += 1
            bucket["segments"] += segments
        elif row.metered_at:
            residue["rows"] += 1
            residue["segments"] += segments
    return {"start": start.isoformat(), "end": end.isoformat(),
            "billable_segments": metered + unmetered,
            "metered_segments": metered, "unmetered_segments": unmetered,
            "unmetered": by_reason, "metered_no_longer_billable": residue}


# ─── A7's arithmetic, shared with tools/bill_period.py ──────────────────────


def period_usage(db: Session, start: date, end: date) -> dict:
    """A closed period priced by `billing_service`'s own functions.

    The back-bill tool and `/usage` render from this, so the invoice and the
    dashboard cannot disagree — the same argument that put `SENT_STATUSES` and
    the opt-out definition in one place each.
    """
    messages, segments = billing_service.compute_usage(db, start, end)
    exact = billing_service.cost_for_segments(segments)
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "messages": messages,
        "segments": segments,
        "included_segments": settings.BILLING_SEGMENTS_INCLUDED,
        "billable_segments": billing_service.billable_segments(segments),
        "price_per_segment": settings.BILLING_PRICE_PER_SEGMENT,
        "exact_total": exact,
        "total_due": billing_service.to_money(exact),
    }
