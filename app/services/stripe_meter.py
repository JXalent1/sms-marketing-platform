"""What Stripe is told to bill, and the check that the tier still agrees with us.

Split from `stripe_billing.py` on the 500-line rule, along the boundary the two
halves already had: that module owns *this account's link to Stripe* — checkout,
the ownership guard, the webhook, the cycle anchor — and this one owns *what
Stripe is told about usage*. It imports from there and nothing imports back.
`stripe_tiers.py` came out of this file the same way, and owns the separate
question of whether Stripe is *configured* to price what it is told correctly.

## The allowance is applied exactly once, and it is applied in Stripe

`billing_service.billable_segments()` is `max(0, segments - 10,000)`. That is the
same arithmetic a graduated tiered price performs:

    tier 1    up to BILLING_SEGMENTS_INCLUDED     $0
    tier 2    thereafter                          BILLING_PRICE_PER_SEGMENT

So **the meter receives the raw count of segments in `BILLABLE_STATUSES`** and
the tier subtracts the allowance. Reporting `billable_segments()` would subtract
it twice — once here and once in the tier — and a 15,000-segment month would
invoice $0 instead of $75 with nothing on any screen looking wrong, because
`/usage` would still show 15,000 used and $75 due. It is the quietest way this
session could lose money, which is why it is acceptance criterion 2 and a
mutation.

`BILLABLE_STATUSES` is imported, never restated. Opt-outs, carrier rejections,
region skips, `held_back` and failures are already outside it, and a local copy
of the tuple is how a commercial change to that set silently stops reaching the
invoice.

## Reporting must never be able to stop a send

`report_campaign()` is called after the messages have gone out and cannot raise:
a Stripe outage that aborted a campaign would turn a billing problem into an
empty saleroom, and `decisions/002` already settled which of those two costs
more. The meter event's `identifier` is deterministic — `campaign_<id>` — so a
retry, a redeploy and a backfill each count once. Stripe dedupes on it.
"""

import logging
from datetime import date
from typing import Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.app_setting import get_setting, set_setting
from app.models import sms_message as message_model
from app.models.sms_message import SMSMessage
from app.services import billing_service, stripe_billing

logger = logging.getLogger("billing.stripe")

# One row per campaign already reported, so `backfill_unreported()` knows what
# it is replaying. Stripe's own dedupe makes a double report harmless; this
# makes it unnecessary, which is what keeps the backfill readable.
REPORTED_KEY_PREFIX = "stripe_meter_campaign:"



def reported_key(campaign_id: int) -> str:
    return f"{REPORTED_KEY_PREFIX}{campaign_id}"


# ─── A6: what a campaign owes ───────────────────────────────────────────────


def campaign_segments(db: Session, campaign_id: int) -> int:
    """The raw billable-status segment count for one campaign.

    Raw: the allowance is **not** applied here. See this module's docstring —
    the tier applies it, and applying it twice is the defect this session most
    easily ships.

    A row with no `segments` is priced by the **same** function
    `billing_service.compute_usage()` uses, not by a rule that resembles it.
    The first version of this said "counted as one" and was wrong for every
    legacy row over 160 characters: a 480-character message metered as 1 and
    appeared on `/usage` as 3. The docstring claimed the two agreed, which is
    how it survived review — a comment asserting an equivalence is not an
    equivalence. `legacy_segment_count()` is now the one implementation and both
    call it.

    `BILLABLE_STATUSES` is read **through the model module** rather than bound
    at import, and that is not a style choice. A local restatement of the tuple
    is behaviourally identical today, so no assertion about its *contents* could
    ever tell the two apart — 5i learned this the hard way when
    `SENT_STATUSES is not BILLABLE_STATUSES` failed for a reason that had
    nothing to do with this codebase. Late binding is what lets a test change
    what the constant *means* and require this figure to follow, which is the
    property that actually matters: a commercial change to the billable set has
    to reach the invoice.
    """
    rows = (db.query(SMSMessage.segments, SMSMessage.message)
            .filter(SMSMessage.campaign_id == campaign_id,
                    SMSMessage.status.in_(message_model.BILLABLE_STATUSES))
            .all())
    return sum(int(segments) if segments
               else billing_service.legacy_segment_count(message)
               for segments, message in rows)


def report_segments(segments: int, campaign_id: int, db: Session) -> dict:
    """Post one meter event. Never raises, never double-bills.

    The identifier is `campaign_<id>` and that is the entire idempotency story:
    Stripe records the first event under an identifier and ignores the rest, so
    a retried background task, a redeploy mid-run and a backfill all leave the
    invoice unchanged.

    Skips — rather than fails — when there is nothing to meter against: no
    Stripe configured, no customer stored yet (he has not subscribed), or zero
    segments. Each returns a named reason so the caller's log line says which,
    because "nothing happened" is three different situations and only one of
    them is worth waking up for.
    """
    if segments <= 0:
        return {"reported": False, "reason": "no billable segments", "segments": 0}
    if not stripe_billing.configured():
        return {"reported": False, "reason": "stripe not configured",
                "segments": segments}
    customer = stripe_billing.customer_id(db)
    if not customer:
        return {"reported": False, "reason": "no subscription yet",
                "segments": segments}

    identifier = f"campaign_{campaign_id}"
    try:
        stripe_billing.api().create_meter_event(
            event_name=settings.STRIPE_METER_EVENT_NAME,
            identifier=identifier,
            payload={"stripe_customer_id": customer, "value": str(segments)},
        )
    except Exception as exc:
        # Logged and swallowed. The caller is on the send path and a carrier
        # that worked must not be undone by a payment processor that did not;
        # `backfill_unreported()` is the recovery, and it is safe because of the
        # identifier above.
        logger.error("Meter event %s (%s segments) failed: %s",
                     identifier, segments, exc)
        return {"reported": False, "reason": "stripe call failed",
                "segments": segments, "error": str(exc)}

    set_setting(db, reported_key(campaign_id),
                stripe_billing.dumps({"segments": segments,
                                      "at": date.today().isoformat()}),
                "Segments already metered for this campaign")
    logger.info("Metered %s segment(s) for campaign #%s as %s",
                segments, campaign_id, identifier)
    return {"reported": True, "reason": None, "segments": segments,
            "identifier": identifier}


def report_campaign(db: Session, campaign_id: int) -> dict:
    """Meter one finished campaign. The entry point the send path calls."""
    return report_segments(campaign_segments(db, campaign_id), campaign_id, db)


def already_reported(db: Session, campaign_id: int) -> Optional[dict]:
    return stripe_billing.loads(get_setting(db, reported_key(campaign_id)))


def backfill_unreported(db: Session, dry_run: bool = True,
                        since: Optional[date] = None) -> dict:
    """Replay campaigns that were never metered. Dry run by default.

    Safe to run twice, and safe to run over campaigns that *were* metered: the
    identifier is derived from the campaign id, so Stripe counts each once
    however many times it is offered. The ledger row is only an optimisation —
    it keeps the output honest about what this run actually changed.

    Dry run is the default because the alternative is a tool whose first
    invocation is its irreversible one.

    **A subtraction needs a time bound, and this one is load-bearing.** The
    identifier makes a *repeat* free; it does nothing about history. Every
    campaign this client has ever sent predates the subscription, and August's
    28,002 segments are already settled by the one-time price on the first
    invoice — so an unbounded replay would meter them a second time, into the
    current period, on top of a charge he has paid. The default bound is the day
    the subscription started, stored at checkout. Passing `since` overrides it,
    deliberately and by a human.

    With no subscription stored there is nothing to meter onto and nothing to
    bound the replay with, so it replays nothing and says so rather than
    silently ranging over the whole table.
    """
    bound = since or subscription_start(db)
    if bound is None:
        return {"dry_run": dry_run, "replayed": [], "skipped": 0, "failed": [],
                "segments": 0,
                "refused": "no subscription is stored, so there is no period to "
                           "replay into and no bound to replay from"}

    # Bounded on when the segments were **sent**, not on when the campaign row
    # was created. A draft written in August and sent in September — a campaign
    # left in the composer, or one with `scheduled_at` — is billable in
    # September, and a bound on `created_at` would put it outside the window
    # forever. That is a silent under-bill on the one path this function exists
    # to recover. `sent_at` is also the column `compute_usage()` filters on, so
    # the backfill and the dashboard agree about which period a campaign is in.
    query = (db.query(SMSMessage.campaign_id)
             .filter(SMSMessage.campaign_id.isnot(None),
                     SMSMessage.status.in_(message_model.BILLABLE_STATUSES),
                     SMSMessage.sent_at >= bound.isoformat())
             .distinct()
             .order_by(SMSMessage.campaign_id))

    replayed, skipped, failed = [], 0, []
    for (campaign_id,) in query.all():
        if already_reported(db, campaign_id):
            skipped += 1
            continue
        segments = campaign_segments(db, campaign_id)
        if segments <= 0:
            skipped += 1
            continue
        if dry_run:
            replayed.append({"campaign_id": campaign_id, "segments": segments})
            continue
        outcome = report_segments(segments, campaign_id, db)
        if outcome["reported"]:
            replayed.append({"campaign_id": campaign_id, "segments": segments})
        else:
            failed.append({"campaign_id": campaign_id, "segments": segments,
                           "reason": outcome["reason"]})

    return {"dry_run": dry_run, "replayed": replayed, "skipped": skipped,
            "failed": failed, "since": bound.isoformat(),
            "segments": sum(row["segments"] for row in replayed)}


def subscription_start(db: Session) -> Optional[date]:
    """The day the subscription began, as stored at checkout. None before that.

    It is the bound `backfill_unreported()` uses, and it is read rather than
    derived: `BILLING_CYCLE_DAY` would give a day of the month with no year in
    it, and "the earliest campaign" would give exactly the answer that
    double-bills.
    """
    stored = get_setting(db, stripe_billing.CYCLE_ANCHOR_AT_KEY)
    try:
        return date.fromisoformat(stored) if stored else None
    except ValueError:
        logger.error("Stored subscription start %r is not a date", stored)
        return None


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
