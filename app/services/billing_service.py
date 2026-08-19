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
from app.models.sms_message import SMSMessage, BILLABLE_STATUSES
from datetime import date
from calendar import monthrange
from decimal import Decimal, ROUND_HALF_UP
from typing import Tuple

MONTH_NAMES = ["", "January", "February", "March", "April", "May", "June",
               "July", "August", "September", "October", "November", "December"]


def _clamp_day(year: int, month: int, day: int) -> date:
    """Safe date construction for cycle days like the 31st in February."""
    return date(year, month, min(day, monthrange(year, month)[1]))


def get_billing_cycle(for_date: date = None) -> Tuple[date, date, date, str]:
    """Return (cycle_start, cycle_end, next_reset, label) for the cycle containing for_date."""
    for_date = for_date or date.today()
    day = settings.BILLING_CYCLE_DAY

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


def to_money(amount: float) -> float:
    """Round half-up to cents. Call this at the point of display, nowhere else.

    Python's round() is banker's rounding: round(0.125, 2) is 0.12, not 0.13.
    On one invoice that is a cent; across a year of cycles it is a systematic
    drift, and it disagrees with the number any human writing the invoice by
    hand would produce. Half-up is what a client expects and what an accountant
    checks against.
    """
    return float(Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def billable_segments(segments: int) -> int:
    """Segments above the included allowance. Never negative."""
    return max(0, segments - settings.BILLING_SEGMENTS_INCLUDED)


def cost_for_segments(segments: int) -> float:
    """Unrounded cost for a cycle's segment total.

    Deliberately not rounded: callers accumulate first and round once via
    to_money(). Returning cents from here would push rounding into the middle
    of every sum.
    """
    return (settings.BILLING_MONTHLY_FEE
            + billable_segments(segments) * settings.BILLING_PRICE_PER_SEGMENT)


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
        if stored_segments:
            segments += stored_segments
        else:
            # Legacy rows from before per-message segment tracking. Keep the old
            # 160-char basis so closed cycles are never silently re-priced.
            length = len(message) if message else 0
            segments += max(1, -(-length // 160))
        count += 1
    return count, segments


def current_usage(db: Session) -> dict:
    """Everything the usage dashboard needs for the open cycle."""
    cycle_start, cycle_end, next_reset, label = get_billing_cycle()
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
    current_start = get_billing_cycle()[0]
    for _ in range(cycles):
        cycle_start, cycle_end, _, label = get_billing_cycle(cursor)
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


def pricing_table() -> dict:
    """The plan, for display. One source of truth — the UI renders this."""
    return {
        "monthly_fee": to_money(settings.BILLING_MONTHLY_FEE),
        "included_segments": settings.BILLING_SEGMENTS_INCLUDED,
        "price_per_segment": settings.BILLING_PRICE_PER_SEGMENT,
        "cycle_day": settings.BILLING_CYCLE_DAY,
    }
