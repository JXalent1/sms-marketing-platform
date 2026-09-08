"""What Stripe is told to bill, and the ledger that makes it exactly once.

Split from `stripe_billing.py` on the 500-line rule, along the boundary the two
halves already had: that module owns *this account's link to Stripe* — checkout,
the ownership guard, the webhook, the cycle anchor — and this one owns *what
Stripe is told about usage*. It imports from there and nothing imports back.
`stripe_tiers.py` owns whether Stripe is *configured* to price what it is told
correctly; `stripe_reconcile.py` owns what a person reads back afterwards.

## The property

**Every billable segment reaches the meter exactly once, and the figure metered
is the figure `billing_service.compute_usage()` would report for the same
window.** Everything below serves that sentence; `decisions/011` is the reasoning
and is not repeated here.

## The mechanism, in four parts

**The ledger is `sms_messages.metered_at`, not Stripe's identifier.** Session B1
metered a campaign the instant its send loop returned and leaned on the meter
event's deterministic `identifier` to make every later report free. Stripe
enforces identifier uniqueness only over a rolling period of at least 24 hours —
it exists for the same-minute retry — so a backfill of any older period billed
twice, and a figure reported at send time could never be corrected once the
delivery webhook moved rows out of `BILLABLE_STATUSES`. Now a pass selects rows
that are **billable, settled and unmarked**, reports them, and stamps
`metered_at` in the transaction that follows the accepted report. A marked row
is never reported again, by any path, including the backfill.

**Settled means the status can no longer leave the billable set.** `delivered`
is a handset receipt and settles at once; `sent` is only the carrier accepting
the message, and settles when `BILLING_SETTLE_HOURS` have passed — the window
exists so a receipt that never arrives cannot hold billing open forever. Both
sets are read through the model module (`BILLABLE_STATUSES`,
`SETTLED_STATUSES`), never restated: a local copy is behaviourally identical
today, which is exactly why no test of its contents could catch it.

**The event is stamped with the send time.** Stripe's `timestamp` accepts
anything within the past 35 calendar days, so a pass running hours or days
later still lands the usage in the cycle the send belongs to. Usage older than
35 days cannot reach the meter at all: the pass detects it, refuses it, leaves
`metered_at` NULL and says so at ERROR — a one-off invoice, never a silent
drop. A guard that is switched off refuses; it does not wave things through.

**The pass is scheduled, and it is the only place a segment is metered.** It
runs hourly off the scheduler in its own session, never inside the send loop
and never raising into it. `campaign_dispatch` and `campaign_topup` carry no
hook: their rows are unmarked, so the next pass takes them — a top-up is
simply a later batch on the same campaign.

## The allowance is applied exactly once, and it is applied in Stripe

`billing_service.billable_segments()` is `max(0, segments - 10,000)`, the same
arithmetic a graduated tiered price performs. So **the meter receives the raw
count of segments in `BILLABLE_STATUSES`** and the tier subtracts the
allowance; reporting `billable_segments()` would subtract it twice, and a
15,000-segment month would invoice $0 while `/usage` still showed $75 due.

## What the pass does when Stripe — or the database — fails mid-batch

A batch is **staged** (written to `app_settings`, committed) before Stripe is
called, and cleared in the same commit as the mark. Stripe fails: nothing
marked, the pass stops. The mark's commit fails — a database lock under a
delivery-webhook storm — or the process dies: Stripe has the event, and the
next pass **re-offers exactly the staged rows under exactly the staged
identifier**, which Stripe dedupes. Never recomputed from the rows: one
delivery-status flip between two passes would change a recomputed identifier
and bill the survivors twice. A batch staged longer than Stripe's window is
refused, loudly, until a person resolves it.
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.app_setting import AppSetting, get_setting, set_setting
from app.models import sms_message as message_model
from app.models.sms_message import SMSMessage
from app.services import billing_service, stripe_billing

logger = logging.getLogger("billing.stripe")

# Stripe's own rules, read from the pinned SDK's declared parameters rather than
# remembered — `tests/test_stripe_contract.py` asserts both figures still stand,
# so a pin that moves either fails at `pip install` and not on an invoice.
#   timestamp:  "within the past 35 calendar days or up to 5 minutes in the future"
#   identifier: "uniqueness within a rolling period of at least 24 hours"
METER_TIMESTAMP_MAX_AGE_DAYS = 35
IDENTIFIER_WINDOW_HOURS = 24

# One UPDATE per chunk when marking a batch. SQLite's host-parameter limit was
# 999 before 3.32, and a single campaign is thousands of rows.
MARK_CHUNK = 500

# The batch in flight: offered to Stripe, not yet marked. One row, one writer.
PENDING_BATCH_KEY = "stripe_meter_pending_batch"

# ─── Settled, defined ───────────────────────────────────────────────────────


def settle_cutoff(now: datetime) -> datetime:
    """A `sent` row sent at or before this moment is settled."""
    return now - timedelta(hours=settings.BILLING_SETTLE_HOURS)


def too_old_cutoff(now: datetime) -> datetime:
    """A row sent before this moment can no longer be timestamped for Stripe."""
    return now - timedelta(days=METER_TIMESTAMP_MAX_AGE_DAYS)


def row_segments(segments: Optional[int], message: Optional[str]) -> int:
    """One row's segments, priced by the rule `compute_usage()` uses.

    A row with no stored count is priced by `legacy_segment_count()` and by
    nothing that resembles it: B1's first version said `segments or 1` and
    metered a 480-character legacy row as 1 while `/usage` showed 3, with a
    docstring between the two asserting they agreed.
    """
    return int(segments) if segments else billing_service.legacy_segment_count(message)


def subscription_start(db: Session) -> Optional[date]:
    """The day the subscription began, as stored at checkout. None before that.

    The pass's lower bound: everything this client sent before subscribing is
    settled by the one-time balance, so a pass ranging over the whole table
    would meter August twice. Read rather than derived — "the earliest
    campaign" is exactly the answer that double-bills. Unreadable is None,
    which the pass refuses on.
    """
    stored = get_setting(db, stripe_billing.CYCLE_ANCHOR_AT_KEY)
    try:
        return date.fromisoformat(stored) if stored else None
    except ValueError:
        logger.error("Stored subscription start %r is not a date", stored)
        return None


def _unmarked_billable(db: Session, since: date):
    """Billable, never metered, in scope. The base of both selections below.

    Both status sets are read through `message_model` at call time — see the
    module docstring on why a bound-at-import copy cannot be told apart. A row
    with no `sent_at` cannot be timestamped or placed in a window; SQL's NULL
    comparison already excludes it, and the explicit filter says so.
    """
    return (db.query(SMSMessage)
            .filter(SMSMessage.status.in_(message_model.BILLABLE_STATUSES),
                    SMSMessage.metered_at.is_(None),
                    SMSMessage.sent_at.isnot(None),
                    SMSMessage.sent_at >= since.isoformat()))


def settled_unmetered(db: Session, now: datetime, since: date) -> List[SMSMessage]:
    """The rows this pass may report: settled, and young enough to timestamp.

    `sent_at` is compared as an ISO string, which is chronological for this
    format and is the basis `compute_usage()` already uses on the same column
    — the meter has to agree with it about which rows are in a window, so it
    compares the way it does. The lower bound is the later of the subscription
    start and the 35-day horizon; anything older than the horizon is a
    refusal, gathered separately by `too_old_unmetered()`, never a quiet skip.
    """
    return (_unmarked_billable(db, since)
            .filter(SMSMessage.sent_at >= too_old_cutoff(now).isoformat(),
                    or_(SMSMessage.status.in_(message_model.SETTLED_STATUSES),
                        SMSMessage.sent_at <= settle_cutoff(now).isoformat()))
            .order_by(SMSMessage.campaign_id, SMSMessage.sent_at, SMSMessage.id)
            .all())


def too_old_unmetered(db: Session, now: datetime, since: date) -> List[SMSMessage]:
    """In scope, never metered, and beyond what Stripe's timestamp accepts."""
    return (_unmarked_billable(db, since)
            .filter(SMSMessage.sent_at < too_old_cutoff(now).isoformat())
            .order_by(SMSMessage.campaign_id, SMSMessage.sent_at, SMSMessage.id)
            .all())


# ─── A batch: the unit that is reported ─────────────────────────────────────


@dataclass
class Batch:
    """The settled, unmarked rows of one campaign sent on one calendar day.

    One day per batch because the event carries one `timestamp` and the cycle
    boundary this codebase keeps is a date (`get_billing_cycle()`); a campaign
    still sending at midnight on the last day of a cycle must split the way
    `compute_usage()` splits it. `campaign_id` may be None — a billable row with
    no campaign is still a billable row on `/usage`.

    Every figure is computed once, from the rows, when the batch is built, and
    the rows themselves are not kept. The mark's commit expires every loaded
    ORM object, so a property that read `row.segments` afterwards cost one
    SELECT per row — 1,204 queries for a 1,200-row batch, measured.
    """
    campaign_id: Optional[int]
    day: str
    ids: List[int]
    segments: int
    timestamp: int

    @property
    def identifier(self) -> str:
        """Deterministic from the rows. Not load-bearing — `metered_at` and the
        staged row are — but it is what makes a retry of the same batch one
        event rather than two."""
        tag = self.campaign_id if self.campaign_id is not None else "none"
        return (f"u_c{tag}_{self.day.replace('-', '')}_{min(self.ids)}_{max(self.ids)}"
                f"_{len(self.ids)}_{self.segments}")

    def staged(self) -> dict:
        return {"identifier": self.identifier, "ids": self.ids,
                "segments": self.segments, "timestamp": self.timestamp,
                "campaign_id": self.campaign_id, "day": self.day}


def batches(rows: List[SMSMessage]) -> List[Batch]:
    grouped: Dict[tuple, List[SMSMessage]] = {}
    for row in rows:
        grouped.setdefault((row.campaign_id, row.sent_at[:10]), []).append(row)
    out = []
    for (campaign_id, day), group in sorted(
            grouped.items(), key=lambda item: (item[0][0] or 0, item[0][1])):
        # `fromisoformat()` on the naive local string this app writes gives a
        # local datetime, and `.timestamp()` converts it with the local offset
        # — the same clock `_local_date()` uses on the way back from Stripe.
        latest = max(row.sent_at for row in group)
        out.append(Batch(campaign_id, day, [row.id for row in group],
                         sum(row_segments(row.segments, row.message) for row in group),
                         int(datetime.fromisoformat(latest).timestamp())))
    return out


def _summary(staged: dict) -> dict:
    return {"campaign_id": staged["campaign_id"], "day": staged["day"],
            "rows": len(staged["ids"]), "segments": staged["segments"],
            "identifier": staged["identifier"]}


# ─── The staged batch ───────────────────────────────────────────────────────


def pending_batch(db: Session) -> Optional[dict]:
    return stripe_billing.loads(get_setting(db, PENDING_BATCH_KEY))


def _stage(db: Session, batch: Batch, now: datetime) -> dict:
    """Write the batch down before offering it. Committed on its own."""
    staged = {**batch.staged(), "staged_at": now.isoformat(timespec="seconds")}
    set_setting(db, PENDING_BATCH_KEY, stripe_billing.dumps(staged),
                "The meter batch in flight: offered to Stripe, not yet marked")
    return staged


def _report(customer: str, staged: dict) -> None:
    """The one Stripe call. Everything it sends comes from the staged record."""
    stripe_billing.api().create_meter_event(
        event_name=settings.STRIPE_METER_EVENT_NAME,
        identifier=staged["identifier"],
        timestamp=staged["timestamp"],
        # The RAW count. The tier applies the allowance — see the module
        # docstring for what applying it here as well costs.
        payload={"stripe_customer_id": customer,
                 "value": str(staged["segments"])},
    )


def _finish(db: Session, staged: dict, now: datetime) -> None:
    """Stamp `metered_at` on exactly the staged ids and clear the staged row.

    One commit. After the report, never before: a mark Stripe did not accept
    is a segment the client is never billed for, silently. By row id and
    unconditionally, not `WHERE status IN billable`: the column records that
    *these rows' segments were reported*, which is true whatever the row's
    status becomes.

    **A row whose status changes after it is marked stays marked and stays
    metered.** That is the residual over-bill this session bounds rather than
    removes — it can only happen to a row that was settled, so on a receipt
    arriving after `BILLING_SETTLE_HOURS`, or on the delivered-then-failed
    sequence the webhook itself treats as a carrier race. Unmarking it would
    re-report it (Stripe cannot take a negative event), so the mark is final
    and `tools/bill_period.py --unmetered` shows the residue instead.
    """
    stamp = now.isoformat()
    ids = staged["ids"]
    for start in range(0, len(ids), MARK_CHUNK):
        (db.query(SMSMessage)
         .filter(SMSMessage.id.in_(ids[start:start + MARK_CHUNK]))
         .update({"metered_at": stamp}, synchronize_session=False))
    (db.query(AppSetting).filter(AppSetting.key == PENDING_BATCH_KEY)
     .update({"value": None}, synchronize_session=False))
    db.commit()


def _offer(db: Session, customer: str, staged: dict, now: datetime,
           verdict: dict) -> bool:
    """Report, then mark. False — with `verdict["failed"]` set — on either failing."""
    try:
        _report(customer, staged)
    except Exception as exc:
        # Nothing marked. The batch stays staged and is offered again next
        # pass under the same identifier; if Stripe is down the next batch
        # would fail the same way, so the caller stops here.
        logger.error("Meter event %s (%s segments) failed; the batch stays "
                     "staged for the next pass: %s",
                     staged["identifier"], staged["segments"], exc)
        verdict["failed"] = {**_summary(staged), "error": str(exc)}
        return False
    try:
        _finish(db, staged, now)
    except Exception as exc:
        db.rollback()
        logger.error("Stripe accepted meter event %s (%s segments) but the rows "
                     "could not be marked: %s. The batch stays staged; the next "
                     "pass re-offers the same identifier, which Stripe dedupes.",
                     staged["identifier"], staged["segments"], exc)
        verdict["failed"] = {**_summary(staged), "error": str(exc)}
        return False
    return True


def _is_stale(staged: dict, now: datetime) -> bool:
    try:
        staged_at = datetime.fromisoformat(staged["staged_at"])
    except (KeyError, TypeError, ValueError):
        return True
    return now - staged_at > timedelta(hours=IDENTIFIER_WINDOW_HOURS)


def resolve_pending_batch(db: Session, recorded: bool,
                          now: Optional[datetime] = None) -> dict:
    """A person's answer for a batch staged longer than Stripe dedupes.

    `recorded=True`: Stripe's event summary shows the identifier, so mark the
    rows and clear the stage without sending again. `recorded=False`: it does
    not, so clear the stage and let the next pass batch the rows afresh.
    """
    staged = pending_batch(db)
    if not staged:
        return {"resolved": False, "reason": "nothing is staged"}
    if recorded:
        _finish(db, staged, now or datetime.now())
    else:
        (db.query(AppSetting).filter(AppSetting.key == PENDING_BATCH_KEY)
         .update({"value": None}, synchronize_session=False))
        db.commit()
    return {"resolved": True, "recorded": recorded, **_summary(staged)}


# ─── The pass ───────────────────────────────────────────────────────────────


def meter_settled_rows(db: Session, now: Optional[datetime] = None,
                       dry_run: bool = False, since: Optional[date] = None) -> dict:
    """Report every settled, unmarked billable row, once. The metering pass.

    `now` is injectable so the settle window and the 35-day horizon can be
    tested against fixed dates; production passes nothing. `since` overrides
    the stored subscription start, deliberately and by a human — it is how the
    backfill widens the bound.

    Returns a verdict rather than raising. The ways to report nothing are each
    named: Stripe is not configured, no customer is stored, no subscription
    start is stored to bound the pass with — the last is a refusal, not a skip,
    because a pass with no lower bound is the double bill
    `backfill_unreported()` was written to avoid — or a staged batch too old to
    retry safely.
    """
    now = now or datetime.now()
    verdict = {"dry_run": dry_run, "now": now.isoformat(timespec="seconds"),
               "since": None, "reason": None, "reported": [], "replayed": None,
               "failed": None, "refused": [], "segments": 0}
    if not stripe_billing.configured():
        verdict["reason"] = "stripe not configured"
        if stripe_billing.customer_id(db):
            # A key removed after checkout. The pass would otherwise skip
            # every hour with nothing on any screen saying so, and every row
            # would age past the 35-day horizon in silence.
            logger.error("A Stripe customer is stored but STRIPE_SECRET_KEY or "
                         "STRIPE_PRICE_METERED is blank: nothing is being "
                         "metered. Restore the keys before usage ages out.")
        return verdict
    customer = stripe_billing.customer_id(db)
    if not customer:
        verdict["reason"] = "no subscription yet"
        return verdict
    bound = since or subscription_start(db)
    if bound is None:
        logger.error("A Stripe customer is stored but no subscription start is; "
                     "the metering pass has no lower bound and refuses to run")
        verdict["reason"] = "no subscription start stored"
        return verdict
    verdict["since"] = bound.isoformat()

    staged = pending_batch(db)
    if staged and not dry_run:
        if _is_stale(staged, now):
            logger.error(
                "A metering batch (%s, %s rows, %s segments) has been staged "
                "since %s — longer than the %s hours Stripe dedupes an "
                "identifier for, so it cannot be retried safely. Check the "
                "meter's event summary in Stripe for that identifier, then run "
                "stripe_meter.resolve_pending_batch(db, recorded=True|False). "
                "Nothing meters until it is resolved.",
                staged["identifier"], len(staged["ids"]), staged["segments"],
                staged.get("staged_at"), IDENTIFIER_WINDOW_HOURS)
            verdict["reason"] = "a staged batch is too old to retry"
            verdict["failed"] = {**_summary(staged),
                                 "error": "staged longer than the identifier window"}
            return verdict
        if not _offer(db, customer, staged, now, verdict):
            return verdict
        verdict["replayed"] = _summary(staged)
        logger.info("Re-offered staged meter event %s (%s segments); marked",
                    staged["identifier"], staged["segments"])

    too_old = too_old_unmetered(db, now, bound)
    if too_old:
        verdict["refused"] = [_summary(b.staged()) for b in batches(too_old)]
        # Loud, every pass, until somebody invoices it by hand. These rows
        # will never reach the meter and `metered_at` stays NULL on purpose:
        # the column says what was reported, and these were not.
        logger.error(
            "USAGE NOT METERED: %s billable segment(s) in %s row(s) are older "
            "than %s days and cannot be timestamped for Stripe. They need a "
            "one-off invoice: run tools/bill_period.py --unmetered for the "
            "window and read decisions/012 before --create. They will be "
            "refused on every pass until then. Campaigns: %s",
            sum(b["segments"] for b in verdict["refused"]), len(too_old),
            METER_TIMESTAMP_MAX_AGE_DAYS,
            sorted({b["campaign_id"] for b in verdict["refused"]}, key=str))

    for batch in batches(settled_unmetered(db, now, bound)):
        if dry_run:
            verdict["reported"].append(_summary(batch.staged()))
            continue
        staged = _stage(db, batch, now)
        if not _offer(db, customer, staged, now, verdict):
            break
        verdict["reported"].append(_summary(staged))
        logger.info("Metered %s segment(s) for campaign #%s sent %s as %s",
                    batch.segments, batch.campaign_id, batch.day, batch.identifier)

    verdict["segments"] = sum(b["segments"] for b in verdict["reported"])
    return verdict


def metering_pass_job(now: Optional[datetime] = None) -> dict:
    """The scheduler's entry point. Owns its own session and never raises.

    Its own session for `campaign_dispatch`'s reason — an APScheduler job has no
    request to borrow one from — closed in a `finally` with every return below
    it. It cannot raise: an exception out of a scheduled job is a job APScheduler
    stops running, and the failure would be a meter that quietly stopped
    metering. `meter_settled_rows()` already returns rather than raises on a
    Stripe failure; this wrapper is about *this call site*, for anything else.
    `now` is for tests; the scheduler passes nothing.
    """
    from app.core.database import SessionLocal
    db = None
    try:
        db = SessionLocal()
        verdict = meter_settled_rows(db, now=now)
        if verdict["reported"] or verdict["failed"] or verdict["refused"]:
            logger.info("Metering pass: %s batch(es), %s segment(s) reported%s",
                        len(verdict["reported"]), verdict["segments"],
                        "" if not verdict["failed"] else " — stopped on a failure")
        return verdict
    except Exception as exc:
        logger.error("Metering pass failed: %s", exc)
        return {"dry_run": False, "reason": "pass failed", "reported": [],
                "replayed": None, "failed": {"error": str(exc)}, "refused": [],
                "segments": 0}
    finally:
        if db is not None:
            db.close()


def backfill_unreported(db: Session, dry_run: bool = True,
                        since: Optional[date] = None,
                        now: Optional[datetime] = None) -> dict:
    """The pass, run by a human. Dry run by default.

    The same function the scheduler runs, so it cannot report a marked row for
    the same reason the scheduler cannot; there is no replay logic to drift.
    It adds a dry run — the alternative is a tool whose first invocation is
    its irreversible one — and `since`, which widens the bound on purpose
    (before the subscription start it re-meters rows the balance settled; the
    dry run is the only guard, deliberately). 35 days is still refused here.
    """
    return meter_settled_rows(db, now=now, dry_run=dry_run, since=since)
