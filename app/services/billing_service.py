"""Usage metering and billing.

The plan (monthly fee, included segments, per-segment rate, cycle day) is
configuration, not code. In the reference system the base fee existed *only* as
a hardcoded string in an HTML template while the rates lived in a Python
function, so nobody could answer "what are we actually charging?" without
reading two files, and the two disagreed.

The plan itself:

    billable_segments = max(0, segments_this_cycle - BILLING_SEGMENTS_INCLUDED)
    cost              = BILLING_MONTHLY_FEE + billable_segments * BILLING_PRICE_PER_SEGMENT

Three rules that took real money to learn:

  1. Bill on ('sent', 'delivered'). Delivery webhooks flip 'sent' -> 'delivered'
     minutes later; counting only 'sent' makes finished campaigns vanish from the
     meter and silently under-bills.

  2. Bill on the carrier's segment count, not len(text)/160. Emoji force UCS-2
     and roughly 2.4x the segments. Billing the flat estimate while the carrier
     bills real parts is a straight transfer from your margin to the client's.

  3. Round once, at the point of display. Rounding each campaign to cents and
     then summing gives a different total than summing and rounding once, and
     the client will be the one who notices.
"""

from sqlalchemy.orm import Session
from app.core.config import settings
from app.models.app_setting import get_setting
from app.models.sms_message import SMSMessage, BILLABLE_STATUSES
from datetime import date
from calendar import monthrange
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Tuple
import logging

logger = logging.getLogger("billing")

MONTH_NAMES = ["", "January", "February", "March", "April", "May", "June",
               "July", "August", "September", "October", "November", "December"]

# The day of the month Stripe anchors the subscription's billing cycle to,
# written once by `stripe_billing` when checkout completes.
#
# It lives here rather than in stripe_billing because **this module is the
# reader and the reader owns the name**: `stripe_billing` imports it from here,
# so there is one spelling and the dependency runs one way. A literal in each
# file is how a screen ends up looking for a row nothing writes — the mistake
# `HELD_BACK_STATUS` was extracted to prevent.
CYCLE_ANCHOR_DAY_KEY = "billing_cycle_anchor_day"


def _clamp_day(year: int, month: int, day: int) -> date:
    """Safe date construction for cycle days like the 31st in February."""
    return date(year, month, min(day, monthrange(year, month)[1]))


def cycle_day(db: Optional[Session] = None) -> int:
    """The day of the month the allowance resets.

    The stored Stripe anchor wins over `BILLING_CYCLE_DAY`, and that ordering is
    the whole point. Stripe anchors the cycle to the moment checkout completes,
    which is what Jordan wants — billing runs from the day the client pays. The
    trap is on our side: `BILLING_CYCLE_DAY` defaults to 1, so a client who
    subscribes on the 9th would read a 1st-to-1st cycle on `/usage` while Stripe
    invoiced him 9th to 9th. He would be right to distrust both numbers.

    Config stays the fallback, for a box that has never subscribed — and for
    every box before Stripe exists at all, which is most of this project's life
    so far.

    `db` is optional, and the honest reason is narrower than the first version
    of this comment claimed. It said three outside callers had no session to
    hand; two of them — `preflight_totals` and `report_service` — have one in
    scope and pass it to `compute_usage()` on the very next line, and both now
    pass it here too. `preflight_totals` matters most: it is a synchronous call
    inside an async route on the composer's *polled* quote path, which is where
    5i's ten-minute join held the event loop and made the scheduler miss its
    tick. A second `SessionLocal()` per poll is a smaller version of the same
    mistake and there was no reason to take it.

    The parameter stays optional for `/api/usage/pricing`, which genuinely has
    no session, and for any future caller in the same position.

    A missing or unreadable row falls back to config rather than raising: an
    anchor is a refinement of a cycle that already worked, and a database hiccup
    must not take out the page that renders it. The fallback is logged, never
    silent.
    """
    own_session = db is None
    if own_session:
        from app.core.database import SessionLocal
        db = SessionLocal()
    try:
        stored = get_setting(db, CYCLE_ANCHOR_DAY_KEY)
        if stored is None:
            return settings.BILLING_CYCLE_DAY
        day = int(stored)
        if not 1 <= day <= 31:
            raise ValueError(f"anchor day {day} is not a day of the month")
        return day
    except Exception as exc:
        logger.error("Stored billing-cycle anchor unusable (%s); falling back to "
                     "BILLING_CYCLE_DAY=%s", exc, settings.BILLING_CYCLE_DAY)
        return settings.BILLING_CYCLE_DAY
    finally:
        # In a `finally` with every return above it, not on each way out: the
        # unknown-slug leak in `links.py` was one `return` that skipped the
        # close, and forty requests left seven connections checked out.
        if own_session:
            db.close()


def get_billing_cycle(for_date: date = None,
                      db: Optional[Session] = None) -> Tuple[date, date, date, str]:
    """Return (cycle_start, cycle_end, next_reset, label) for the cycle containing for_date.

    `cycle_end` is the last day **in** the cycle, one day before `next_reset`.
    `stripe_billing.subscription_cycle()` converts Stripe's own period, whose
    end is the instant the next cycle begins, to the same convention — one
    function each, so the two windows are comparable by construction rather
    than by two authors agreeing about an off-by-one.
    """
    for_date = for_date or date.today()
    day = cycle_day(db)

    if for_date.day >= day:
        cycle_start = _clamp_day(for_date.year, for_date.month, day)
        ny, nm = (for_date.year + 1, 1) if for_date.month == 12 else (for_date.year, for_date.month + 1)
    else:
        py, pm = (for_date.year - 1, 12) if for_date.month == 1 else (for_date.year, for_date.month - 1)
        cycle_start = _clamp_day(py, pm, day)
        ny, nm = for_date.year, for_date.month

    next_reset = _clamp_day(ny, nm, day)
    cycle_end = date.fromordinal(next_reset.toordinal() - 1)
    label = f"{MONTH_NAMES[cycle_start.month]} {cycle_start.year}"
    return cycle_start, cycle_end, next_reset, label


def to_money(amount: Decimal | float) -> float:
    """Round half-up to cents. Call this at the point of display, nowhere else.

    Python's round() is banker's rounding: round(0.125, 2) is 0.12, not 0.13.
    On one invoice that is a cent; across a year of cycles it is a systematic
    drift, and it disagrees with the number any human writing the invoice by
    hand would produce. Half-up is what a client expects and what an accountant
    checks against.

    A Decimal is passed straight through. Half-up rounding can only be correct
    if the value still sits exactly on the half-cent boundary when it gets here,
    and a float that has been through `n * 0.015` no longer does — see
    cost_for_segments().
    """
    exact = amount if isinstance(amount, Decimal) else Decimal(str(amount))
    return float(exact.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def billable_segments(segments: int) -> int:
    """Segments above the included allowance. Never negative."""
    return max(0, segments - settings.BILLING_SEGMENTS_INCLUDED)


def cost_for_segments(segments: int) -> Decimal:
    """Unrounded cost for a cycle's segment total, as an exact Decimal.

    Deliberately not rounded: callers accumulate first and round once via
    to_money(). Returning cents from here would push rounding into the middle
    of every sum.

    Decimal, not float, and that is the whole point of this function. At
    $0.015 every odd billable count lands exactly on a half-cent, and in binary
    floating point about a quarter of them land just *under* it:

        11 * 0.015 -> 0.16499999999999998, so half-up gives 0.16, not 0.17

    11,782 of the first 50,000 odd counts were wrong that way, always one cent
    low, always in the client's favour and against ours. Rounding half-up in
    Decimal at the end cannot fix it — by then the boundary is already gone —
    so the multiplication itself has to be exact. `Decimal(str(rate))` is used
    rather than `Decimal(rate)` because the settings are floats: str() gives
    the 0.015 that was written in .env, Decimal() gives the binary expansion of
    it.
    """
    fee = Decimal(str(settings.BILLING_MONTHLY_FEE))
    rate = Decimal(str(settings.BILLING_PRICE_PER_SEGMENT))
    return fee + billable_segments(segments) * rate


def legacy_segment_count(message: str) -> int:
    """Segments for a row written before per-message segment tracking.

    The old 160-character basis, kept so closed cycles are never silently
    re-priced. Extracted from `compute_usage()` because session B1 needed the
    same rule for the Stripe meter and wrote a second, *similar* one instead:
    `segments or 1`, which is right for a short message and wrong for every
    legacy row over 160 characters. The two figures then disagreed by a factor
    of three on the same row, with a docstring in between asserting they agreed.

    One implementation, two callers. The dashboard and the invoice cannot
    describe the same message differently.
    """
    length = len(message) if message else 0
    return max(1, -(-length // 160))


def compute_usage(db: Session, cycle_start: date, cycle_end: date) -> Tuple[int, int]:
    """Return (message_count, segment_count) billable in the window.

    sent_at is stored as an ISO string, so a lexicographic range works: any
    timestamp on the end date sorts before end_date + "T99".
    """
    start_str = cycle_start.isoformat()
    end_str = cycle_end.isoformat() + "T99"

    rows = db.query(SMSMessage.segments, SMSMessage.message).filter(
        SMSMessage.status.in_(BILLABLE_STATUSES),
        SMSMessage.sent_at >= start_str,
        SMSMessage.sent_at <= end_str,
    ).all()

    count = 0
    segments = 0
    for stored_segments, message in rows:
        segments += (stored_segments if stored_segments
                     else legacy_segment_count(message))
        count += 1
    return count, segments


def current_usage(db: Session) -> dict:
    """Everything the usage dashboard needs for the open cycle."""
    cycle_start, cycle_end, next_reset, label = get_billing_cycle(db=db)
    count, segments = compute_usage(db, cycle_start, cycle_end)

    included = settings.BILLING_SEGMENTS_INCLUDED
    billable = billable_segments(segments)

    return {
        "month": label,
        "included_segments": included,
        "used_segments": segments,
        "message_count": count,
        "remaining": max(0, included - segments),
        "percentage_used": min(100, round((segments / included) * 100)) if included else 0,
        "billable_segments": billable,
        "monthly_fee": to_money(settings.BILLING_MONTHLY_FEE),
        "price_per_segment": settings.BILLING_PRICE_PER_SEGMENT,
        "total_due": to_money(cost_for_segments(segments)),
        "billing_start": cycle_start.isoformat(),
        "reset_date": next_reset.isoformat(),
    }


def usage_history(db: Session, cycles: int = 6) -> list:
    """Closed cycles, most recent first."""
    out = []
    cursor = date.today()
    current_start = get_billing_cycle(db=db)[0]
    for _ in range(cycles):
        cycle_start, cycle_end, _, label = get_billing_cycle(cursor, db=db)
        count, segments = compute_usage(db, cycle_start, cycle_end)

        out.append({
            "month": label,
            "billing_start": cycle_start.isoformat(),
            "billing_end": cycle_end.isoformat(),
            "total_segments": segments,
            "messages": count,
            "billable_segments": billable_segments(segments),
            "total_due": to_money(cost_for_segments(segments)),
            "status": "current" if cycle_start == current_start else "closed",
        })
        cursor = date.fromordinal(cycle_start.toordinal() - 1)
    return out


def pricing_table(db: Optional[Session] = None) -> dict:
    """The plan, for display. One source of truth — the UI renders this."""
    return {
        "monthly_fee": to_money(settings.BILLING_MONTHLY_FEE),
        "included_segments": settings.BILLING_SEGMENTS_INCLUDED,
        "price_per_segment": settings.BILLING_PRICE_PER_SEGMENT,
        # The day actually in force, not the configured default. This table is
        # what the UI renders, and a page that showed the 1st while the invoice
        # ran from the 9th is exactly the disagreement `cycle_day()` exists to
        # remove — showing config here would put it back one layer up.
        "cycle_day": cycle_day(db),
    }
