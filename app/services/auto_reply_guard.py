"""The one gate every reply to an inbound text passes through.

Incident 2026-10-09 to 10-12. Our number met two businesses' auto-responders.
They texted "We do not monitor text messages sent to this number", we answered
every one with the Settings auto-reply, each answer drew another, and nothing
stopped it for three days: ~114k outbound, ~56k inbound in 24 hours, $2,818.
`handle_inbound()` returned a reply for every inbound message there is, and the
webhook sent whatever it returned. There was no dedupe, no rate limit, no check
for a machine on the other end and no blocklist check — and because those
replies were never written to `sms_messages`, no screen in the platform showed
the loop either.

So the reply decision lives here, once, for both carriers' webhooks and for
every kind of reply (the auto-reply and the STOP / START / HELP confirmations).
The rules, in the order they are asked; the first that refuses names the row:

  automated_sender  The inbound reads like an auto-responder
                    (`compliance.AUTOMATED_SENDER_PHRASES`). No reply of any
                    kind — this is the rule that would have stopped the
                    incident on its first message.
  no_sender         There is no number to answer.
  invalid_sender    Not a full phone number: a short code or an alphanumeric
                    sender ID, which is a machine by construction.
  non_us_sender     Outside the US sending region, under the same
                    `SKIP_NON_US_NUMBERS` setting the campaign send loop obeys.
                    An inbound text is a request we did not originate, so it
                    must not reach a destination a campaign would refuse —
                    international replies are the expensive ones.
  blocked_number    Never reply to a blocked number — except the first STOP
                    confirmation, which is the one reply an opt-out is owed.
  per_number_cap    At most one reply per number per
                    `AUTO_REPLY_PER_NUMBER_HOURS`, across every kind. This is
                    the rule that bounds a loop the phrase list does not
                    recognise: a responder worded some other way gets one
                    answer a day, not one per message.
  circuit_breaker   At most `AUTO_REPLY_HOURLY_CAP` replies in a rolling hour
                    across all numbers. The backstop for a loop spread across
                    many numbers. Logged at ERROR and raised on /health; it
                    clears itself when the hour does.

Which way to be wrong. A wrongly suppressed reply costs one person a courtesy
text they can get by calling. A wrongly sent one is how 114k messages happened.
Every rule here therefore fails towards silence, including the ones that fail
by accident: a crash after the claim leaves a `sent` row that was never sent,
which suppresses later replies rather than permitting them.

Claim before send. The decision and its `sent` row are committed BEFORE the
provider is called, inside one write transaction that begins with the INSERT.
SQLite admits one writer at a time and the INSERT takes that lock, so a second
webhook for the same number cannot start deciding until the first has
committed — and then it sees the first's row. Two concurrent webhooks cannot
both pass. (This leans on SQLite's single writer, which is the deployment. On
Postgres the INSERT does not serialise readers and this would need an advisory
lock — noted in status.md, not built.)

Delivery-status webhooks never reach this module, and must not: they carry no
inbound text and there is nothing on that path to reply to.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional
import logging

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core import clock
from app.core.config import settings
from app.models.auto_reply_log import AutoReplyLog
from app.models.blocked_number import BlockedNumber
from app.services.monitoring_service import record_config_alert
from app.sms import compliance
from app.sms.phone import is_non_us_region, is_valid, normalize, scrub_provider_text

logger = logging.getLogger("auto_reply_guard")

AUTOMATED_SENDER = "automated_sender"
NO_SENDER = "no_sender"
INVALID_SENDER = "invalid_sender"
NON_US_SENDER = "non_us_sender"
BLOCKED_NUMBER = "blocked_number"
PER_NUMBER_CAP = "per_number_cap"
CIRCUIT_BREAKER = "circuit_breaker"

EXCERPT_CHARS = 160
BREAKER_WINDOW = timedelta(hours=1)      # "rolling hour" is the rule, not a setting

# compliance.classify() answers stop | start | help | other; the log names the
# reply, and the reply to "other" is the auto-reply.
_KIND = {"stop": "stop", "start": "start", "help": "help", "other": "auto_reply"}


@dataclass(frozen=True)
class ReplyDecision:
    """What the webhook may do. `reply` is None when it must send nothing."""
    log_id: Optional[int]
    reply: Optional[str]
    reason: Optional[str] = None

    @property
    def allowed(self) -> bool:
        return self.reply is not None


def claim(db: Session, phone: str, body: str, inbound_kind: str, reply: str,
          now: Optional[datetime] = None) -> ReplyDecision:
    """Decide whether `reply` may go to `phone`, and record the decision.

    Commits before it returns, whatever it decides. When it allows, the `sent`
    row already exists, so the caller sends knowing the claim is durable.
    `now` is for tests; production passes nothing and gets the application clock.
    """
    kind = _KIND.get(inbound_kind, "auto_reply")
    key = normalize(phone) or (phone or "").strip()
    row = AutoReplyLog(phone=key[:32], kind=kind, decision="sent",
                       inbound_excerpt=(body or "")[:EXCERPT_CHARS],
                       created_at=now or clock.now())

    # Whatever the caller left open is not ours to decide inside. Ending it here
    # means the INSERT below begins the transaction, and so takes the write lock
    # before anything this decision reads.
    db.commit()

    reason = None
    if compliance.is_automated_sender(body):
        reason = AUTOMATED_SENDER
    elif not key:
        reason = NO_SENDER
    elif not is_valid(key):
        reason = INVALID_SENDER
    elif settings.SKIP_NON_US_NUMBERS and is_non_us_region(key):
        reason = NON_US_SENDER
    else:
        db.add(row)
        db.flush()                      # INSERT: the write lock is ours until commit
        reason = _refusal(db, row, kind)

    if reason:
        row.decision = "suppressed"
        row.reason = reason
        db.add(row)
    db.commit()

    if reason == CIRCUIT_BREAKER:
        _trip(db, row)
    elif reason:
        logger.info(f"Auto-reply suppressed for {key or '(no sender)'}: {reason} "
                    f"[{kind}]")
    else:
        logger.info(f"Auto-reply claimed for {key} [{kind}] (log {row.id})")
    return ReplyDecision(log_id=row.id, reply=None if reason else reply, reason=reason)


def record_send_failure(db: Session, log_id: Optional[int], detail: str) -> None:
    """A claimed send failed. Say so on the claim; never remove it.

    The row stays `sent`. A failure reported by the carrier is not proof the
    handset got nothing, and the cap must keep counting the attempt — a send
    that fails and is retried on the next inbound is the loop again, slower.
    Never raises: the webhook must still answer 200.
    """
    if not log_id:
        return
    try:
        row = db.get(AutoReplyLog, log_id)
        if row is not None:
            row.reason = f"send_failed: {scrub_provider_text(detail or 'unknown')}"[:200]
            db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"could not record auto-reply send failure on log {log_id}: {e}")


def _refusal(db: Session, row: AutoReplyLog, kind: str) -> Optional[str]:
    """The first rule that refuses this reply, or None. Reads only other rows."""
    now = row.created_at
    earlier = db.query(AutoReplyLog).filter(AutoReplyLog.id != row.id)

    if db.query(BlockedNumber.id).filter(BlockedNumber.phone == row.phone).first():
        if kind != "stop" or _stop_already_confirmed(earlier, row.phone):
            return BLOCKED_NUMBER

    # Lower bounds only. An upper bound of `now` would let a concurrent claim
    # whose clock read a millisecond later slip past this one, and on the night
    # the clocks go back it would hide an hour of replies.
    #
    # These are wall-clock windows (the application's one clock, see
    # app/core/clock.py), so once a year they are an hour short of real time:
    # in the hour after the spring-forward jump the breaker can admit up to
    # twice its cap, and the per-number window is 23 real hours that day.
    # Bounded, and accepted rather than giving this one table a second clock.
    # At fall-back the error runs the other way — more is held, not less.
    window = timedelta(hours=settings.AUTO_REPLY_PER_NUMBER_HOURS)
    if earlier.filter(AutoReplyLog.phone == row.phone,
                      AutoReplyLog.decision == "sent",
                      AutoReplyLog.created_at > now - window).first():
        return PER_NUMBER_CAP

    sent_this_hour = earlier.filter(
        AutoReplyLog.decision == "sent",
        AutoReplyLog.created_at > now - BREAKER_WINDOW,
    ).with_entities(func.count(AutoReplyLog.id)).scalar() or 0
    if sent_this_hour >= settings.AUTO_REPLY_HOURLY_CAP:
        return CIRCUIT_BREAKER
    return None


def _stop_already_confirmed(earlier, phone: str) -> bool:
    """Whether this opt-out has already had its confirmation.

    "Since the number's last START", not "ever": someone who resubscribes and
    opts out again is owed a confirmation for the second opt-out. Read from this
    table rather than from `blocked_numbers.blocked_at` so the comparison is
    between two values one writer wrote on one clock. A number blocked for some
    other reason (a delivery failure, an operator) that texts STOP has had no
    confirmation, and gets its first.
    """
    last_start = earlier.filter(AutoReplyLog.phone == phone,
                                AutoReplyLog.kind == "start") \
        .with_entities(func.max(AutoReplyLog.id)).scalar()
    confirmed = earlier.filter(AutoReplyLog.phone == phone,
                               AutoReplyLog.kind == "stop",
                               AutoReplyLog.decision == "sent")
    if last_start:
        confirmed = confirmed.filter(AutoReplyLog.id > last_start)
    return confirmed.first() is not None


def _trip(db: Session, row: AutoReplyLog) -> None:
    # ERROR, and a row /health reads — never a text. An alert about runaway
    # sending must not be sent over the thing that is running away.
    logger.error(
        f"AUTO-REPLY CIRCUIT BREAKER: {settings.AUTO_REPLY_HOURLY_CAP} replies "
        f"in the last hour; suppressed reply to {row.phone} (log {row.id}). "
        f"Every reply is paused until the hour clears.")
    record_config_alert(db, compliance.AUTO_REPLY_BREAKER,
                        compliance.CONFIGURATION_ALERT_DETAIL[compliance.AUTO_REPLY_BREAKER])
