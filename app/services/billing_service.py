"""Usage metering and overage billing.

The plan (base fee, included segments, overage tiers, cycle day) is configuration,
not code. In the reference system the base fee existed *only* as a hardcoded
string in an HTML template while the tiers lived in a Python function, so nobody
could answer "what are we actually charging?" without reading two files, and the
two disagreed.

Two rules that took real money to learn:

  1. Bill on ('sent', 'delivered'). Delivery webhooks flip 'sent' -> 'delivered'
     minutes later; counting only 'sent' makes finished campaigns vanish from the
     meter and silently under-bills.

  2. Bill on the carrier's segment count, not len(text)/160. Emoji force UCS-2
     and roughly 2.4x the segments. Billing the flat estimate while the carrier
     bills real parts is a straight transfer from your margin to the client's.
"""

from sqlalchemy.orm import Session
from app.core.config import settings
from app.models.sms_message import SMSMessage, BILLABLE_STATUSES
from datetime import date
from calendar import monthrange
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


def calculate_overage_cost(overage_segments: int) -> float:
    """Apply the configured tiers to segments above the allowance."""
    if overage_segments <= 0:
        return 0.0
    cost, remaining = 0.0, overage_segments
    for tier_size, rate in settings.overage_tiers():
        chunk = min(remaining, tier_size)
        cost += chunk * rate
        remaining -= chunk
        if remaining <= 0:
            break
    return round(cost, 2)


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

    allowance = settings.BILLING_INCLUDED_SEGMENTS
    overage = max(0, segments - allowance)
    overage_cost = calculate_overage_cost(overage)

    return {
        "month": label,
        "allowance": allowance,
        "used_segments": segments,
        "message_count": count,
        "remaining": max(0, allowance - segments),
        "percentage_used": min(100, round((segments / allowance) * 100)) if allowance else 0,
        "overage": overage,
        "overage_cost": overage_cost,
        "base_fee": settings.BILLING_BASE_FEE,
        "total_due": round(settings.BILLING_BASE_FEE + overage_cost, 2),
        "billing_start": cycle_start.isoformat(),
        "reset_date": next_reset.isoformat(),
    }


def usage_history(db: Session, cycles: int = 6) -> list:
    """Closed cycles, most recent first."""
    out = []
    cursor = date.today()
    for _ in range(cycles):
        cycle_start, cycle_end, _, label = get_billing_cycle(cursor)
        count, segments = compute_usage(db, cycle_start, cycle_end)
        overage = max(0, segments - settings.BILLING_INCLUDED_SEGMENTS)
        is_current = cycle_start == get_billing_cycle()[0]

        out.append({
            "month": label,
            "billing_start": cycle_start.isoformat(),
            "billing_end": cycle_end.isoformat(),
            "total_segments": segments,
            "messages": count,
            "overage": overage,
            "overage_cost": calculate_overage_cost(overage),
            "total_due": round(settings.BILLING_BASE_FEE + calculate_overage_cost(overage), 2),
            "status": "current" if is_current else "closed",
        })
        cursor = date.fromordinal(cycle_start.toordinal() - 1)
    return out


def pricing_table() -> dict:
    """The plan, for display. One source of truth — the UI renders this."""
    tiers, floor = [], settings.BILLING_INCLUDED_SEGMENTS
    for tier_size, rate in settings.overage_tiers():
        if tier_size == float("inf"):
            tiers.append({"range": f"{floor + 1:,}+", "rate": rate})
        else:
            ceiling = floor + int(tier_size)
            tiers.append({"range": f"{floor + 1:,}–{ceiling:,}", "rate": rate})
            floor = ceiling
    return {
        "base_fee": settings.BILLING_BASE_FEE,
        "included_segments": settings.BILLING_INCLUDED_SEGMENTS,
        "cycle_day": settings.BILLING_CYCLE_DAY,
        "tiers": tiers,
    }
