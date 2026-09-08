"""What this send will actually cost, measured rather than estimated.

Split out of `preflight_service.py` when 5f pushed it past the 500-line rule.
The seam is *measuring* against *judging*: nothing here returns a verdict, and
none of the checks next door computes a number of its own. Both names are
re-exported from `preflight_service`, which is where every caller — the composer
endpoints and the report — reaches for them.

**Money here is the client's money.** Every dollar figure comes from
`billing_service` at `BILLING_PRICE_PER_SEGMENT`. `WHOLESALE_COST_PER_SEGMENT`
is what we pay the carrier; it funds the capacity check in `campaign_service`
and our own logs, and it must not appear in anything built here. Session 1b
removed a leak of exactly that kind.
"""

from decimal import Decimal
from typing import Any, Callable, Sequence

from sqlalchemy.orm import Session

from app.core.config import settings
from app.services import billing_service
from app.sms.segments import count_segments, describe


# ─── Segments, measured on what actually reaches a handset ──────────────────

def exact_segment_totals(message_template: str, recipients: Sequence,
                         render: Callable[[str, Any], str]) -> dict:
    """Segments this send really costs: the template rendered per recipient.

    The composer's live counter measures the raw template, where `{first_name}`
    is twelve literal characters. Nobody is called `{first_name}`. Usually that
    over-counts and the quote is merely pessimistic, but the reverse happens
    too: `{name}` is six characters, so a 158-character template counts as one
    segment and renders to 163 for a Christopher — two segments, at double the
    quoted rate, discovered on the invoice.

    So the keystroke counter stays an estimate and this is the exact figure.
    Pre-flight is a deliberate action against a resolved audience: rendering the
    template `len(recipients)` times is affordable exactly here and nowhere on
    the typing path.

    `render` is passed in rather than imported. `campaign_service` already
    imports this module, and the send path's own `render()` is the one whose
    output is billed — measuring with a second copy of that logic would
    eventually measure something the send path does not produce.

    With no recipients there is nothing to render, so the template's own count
    is returned and `exact` is False. Callers must not present that as measured.
    """
    template_per_message = count_segments(message_template or "")

    if not recipients:
        return {
            "exact": False,
            "recipients": 0,
            "template_segments_per_message": template_per_message,
            "template_total_segments": 0,
            "total_segments": 0,
            "max_segments_per_message": template_per_message,
            "min_segments_per_message": template_per_message,
            "over_template_count": 0,
        }

    per_recipient = [count_segments(render(message_template, c)) for c in recipients]
    return {
        "exact": True,
        "recipients": len(per_recipient),
        "template_segments_per_message": template_per_message,
        "template_total_segments": template_per_message * len(per_recipient),
        "total_segments": sum(per_recipient),
        "max_segments_per_message": max(per_recipient),
        "min_segments_per_message": min(per_recipient),
        # The number that matters: how many people the template's own count
        # under-quotes. One is enough to make the quote wrong.
        "over_template_count": sum(1 for n in per_recipient if n > template_per_message),
    }


# ─── Cost, at the client's rate ─────────────────────────────────────────────

def marginal_cost(db: Session, added_segments: int) -> float:
    """What this send adds to the open cycle's bill, in dollars.

    Not `segments * rate`. The plan includes 10,000 segments a month, so the
    honest answer to "what does this campaign cost" depends on where the cycle
    already stands: the first campaign of the month usually costs nothing and
    the one that crosses the allowance costs only the part above it. Quoting the
    flat rate would over-state the early sends and under-state the crossing one.

    Both terms come from `billing_service.cost_for_segments()`, which is exact
    Decimal — the subtraction happens before any rounding, so the half-cent
    boundary is still intact when `to_money()` sees it. The monthly fee is in
    both terms and cancels, which is correct: a campaign does not re-charge it.
    """
    # `db=db` rather than letting it open its own session: this runs on the
    # composer's polled quote path, inside an async route, and a second
    # connection per poll is 5i's event-loop lesson in miniature.
    cycle_start, cycle_end, _, _ = billing_service.get_billing_cycle(db=db)
    _, used = billing_service.compute_usage(db, cycle_start, cycle_end)

    before: Decimal = billing_service.cost_for_segments(used)
    after: Decimal = billing_service.cost_for_segments(used + max(0, added_segments))
    return billing_service.to_money(after - before)


def cost_estimates(db: Session, message_template: str, recipients: int) -> dict:
    """Cost now, and cost if the message were plain GSM-7, both at his rate.

    The pair is the point. One emoji cuts a segment from 160 characters to 70,
    and the only wording of that fact anyone acts on is the difference between
    two dollar figures at tonight's recipient count.
    """
    breakdown = describe(message_template or "")
    total = breakdown["segments"] * recipients
    gsm7_total = breakdown["gsm7_segments_if_stripped"] * recipients
    return {
        "estimated_cost": marginal_cost(db, total),
        "estimated_cost_if_gsm7": marginal_cost(db, gsm7_total),
        "price_per_segment": settings.BILLING_PRICE_PER_SEGMENT,
    }
