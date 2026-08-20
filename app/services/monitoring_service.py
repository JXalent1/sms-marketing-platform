"""The two things nobody is watching at 3am.

Both jobs are registered on the scheduler in `app/main.py` and both notify
through `agent/notify.sh`. That script is ours, not the client's: it is the
operator's pager, it never touches the campaign send path, and it is the one
place in the repo allowed to name the carrier.

**Why not `provider.send()`.** The obvious way to text a low-balance warning is
over the carrier account — and that account is the thing that is out of money.
`scripts/balance_alert.py` did exactly that, and its own docstring admitted the
hole: at a true zero balance the warning cannot be sent either, so the one
moment the alert matters is the one moment it is silently dropped. Routing
through `notify.sh` puts the alert on a credential independent of the one being
warned about.

**Nothing here can send a campaign message.** Neither job touches
`campaign_service`, neither reads the audience, and the only provider call is
`get_balance()`, which is a read.
"""

import asyncio
import json
import logging
import os
import subprocess
from collections import Counter
from datetime import datetime, timedelta
from typing import Optional

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.sms_message import SMSMessage
from app.sms.factory import get_provider
from app.sms.phone import scrub_provider_text

logger = logging.getLogger("monitoring")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
NOTIFY_SCRIPT = os.path.join(PROJECT_ROOT, "agent", "notify.sh")

# State lives on disk, not in memory: a restart during a long low-balance
# stretch would otherwise re-alert immediately, and a deploy at 9am on sale day
# is exactly when that happens.
STATE_FILE = os.path.join(PROJECT_ROOT, "data", ".monitoring_state.json")

# How long to stay quiet after alerting about the same condition. Twelve hours
# is two working days' worth of chances to act without becoming noise someone
# learns to ignore.
REALERT_HOURS = 12

NOTIFY_TIMEOUT_SECONDS = 20


# ─── State ──────────────────────────────────────────────────────────────────

def _load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except OSError as e:
        # A monitoring job that crashes the scheduler is worse than one that
        # re-alerts. Log and carry on.
        logger.warning(f"could not write monitoring state: {e}")


def _alerted_recently(state: dict, key: str, now: datetime) -> bool:
    last = state.get(key)
    if not last:
        return False
    try:
        return now - datetime.fromisoformat(last) < timedelta(hours=REALERT_HOURS)
    except ValueError:
        return False


# ─── Transport ──────────────────────────────────────────────────────────────

def notify(message: str) -> bool:
    """Hand one line to `agent/notify.sh`. Returns whether it exited clean.

    With no notification credential configured the script prints the message and
    exits 0 — which is the correct behaviour for a dry run and for CI, and is
    why nothing here needs a `if settings.SMS_PROVIDER == 'console'` guard.

    Never raises. A failed alert must not take the scheduler down with it.
    """
    if not os.path.exists(NOTIFY_SCRIPT):
        logger.error(f"notify script missing at {NOTIFY_SCRIPT}; alert dropped: {message}")
        return False
    try:
        result = subprocess.run(
            ["bash", NOTIFY_SCRIPT, message],
            capture_output=True, text=True, timeout=NOTIFY_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as e:
        logger.error(f"notify failed: {e}")
        return False

    if result.returncode != 0:
        logger.error(f"notify exited {result.returncode}: {result.stderr.strip()}")
        return False

    # With no pager credential configured the script prints the message to
    # stderr and exits 0. Discarding that captured output meant the alert
    # vanished on exactly the boxes least likely to be watched — a dry run, a
    # staging box, a deploy where NOTIFY_TO was never filled in. Put it in our
    # log, where the rest of the story already is.
    if result.stderr.strip():
        logger.info(f"notify: {result.stderr.strip()}")
    return True


# ─── Job 1: low credit ──────────────────────────────────────────────────────

async def check_low_balance(now: Optional[datetime] = None) -> dict:
    """Warn while there is still time to top up.

    This is the guard against the failure that cost the reference client 19,375
    messages across eight campaigns: the account hit zero mid-blast, every
    remaining message failed with "Account inactive", and nobody knew until the
    delivery report. The threshold is deliberately well above zero — an alert
    that fires at $0 fires too late to be acted on.

    Returns a dict describing what it decided, so the caller (and the test) can
    assert on it rather than on a log line.
    """
    now = now or datetime.now()
    threshold = settings.BALANCE_ALERT_THRESHOLD

    provider = get_provider()
    balance = await provider.get_balance()
    if balance is None:
        # console/dry-run and some carriers report nothing. Not an error.
        return {"checked": False, "reason": "no balance reported", "alerted": False}

    state = _load_state()

    if balance >= threshold:
        if state.pop("low_balance_alert", None):
            _save_state(state)          # recovered — re-arm for the next dip
        return {"checked": True, "low": False, "alerted": False, "balance": balance}

    if _alerted_recently(state, "low_balance_alert", now):
        return {"checked": True, "low": True, "alerted": False,
                "reason": "already alerted", "balance": balance}

    # Segments, not dollars. The operator reads this, but the number that
    # answers "will tonight's blast finish" is how many messages are left, and
    # the dollar figure is our wholesale cost — see CLAUDE.md.
    rate = settings.WHOLESALE_COST_PER_SEGMENT
    remaining = int(balance / rate) if rate > 0 else 0
    sent = notify(
        f"{settings.BRAND_APP_NAME}: sending capacity is low — about "
        f"{remaining:,} segments left, below the configured alert threshold. "
        f"Top up before the next campaign or messages will start failing."
    )

    if sent:
        state["low_balance_alert"] = now.isoformat()
        _save_state(state)
    return {"checked": True, "low": True, "alerted": sent, "balance": balance,
            "remaining_segments": remaining}


# ─── Job 2: daily failure digest ────────────────────────────────────────────

def failure_digest(day: Optional[datetime] = None) -> dict:
    """Yesterday's failures, grouped by reason.

    Grouped, not listed. "4,623 x Account inactive" is a finding; 4,623 phone
    numbers is a wall of text nobody reads to the bottom of, and the pattern —
    one systemic cause versus a scatter of dead numbers — is the entire point.

    Reasons are scrubbed before they leave this function. They come from carrier
    error strings and arrive full of the carrier's name and doc links.
    """
    day = day or (datetime.now() - timedelta(days=1))
    start = day.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)

    db = SessionLocal()
    try:
        # sent_at is an ISO string, and so are these bounds — same format, so a
        # lexicographic range is a chronological one. Same reasoning as
        # preflight_service.suppression_cutoff().
        rows = db.query(SMSMessage.error_message).filter(
            SMSMessage.status.in_(("failed", "undelivered")),
            SMSMessage.sent_at >= start.isoformat(),
            SMSMessage.sent_at < end.isoformat(),
        ).all()
    finally:
        db.close()

    counter = Counter(scrub_provider_text(r[0]) or "Unknown" for r in rows)
    return {
        "date": start.date().isoformat(),
        "total": sum(counter.values()),
        "reasons": [{"reason": reason, "count": count}
                    for reason, count in counter.most_common(10)],
    }


def send_failure_digest(day: Optional[datetime] = None) -> dict:
    """Build the digest and page it out — unless there is nothing to say.

    A digest that arrives every morning saying "0 failures" trains the reader to
    delete it unopened, which is precisely the message that must not be ignored
    on the morning it says 4,623.
    """
    digest = failure_digest(day)
    if not digest["total"]:
        logger.info(f"failure digest {digest['date']}: no failures, nothing sent")
        return {**digest, "alerted": False}

    lines = [f"{settings.BRAND_APP_NAME}: {digest['total']:,} failed or undelivered "
             f"messages on {digest['date']}."]
    lines += [f"  {row['count']:,} x {row['reason']}" for row in digest["reasons"]]
    return {**digest, "alerted": notify("\n".join(lines))}


# ─── Scheduler entry points ─────────────────────────────────────────────────
#
# APScheduler calls these. They swallow their own exceptions on purpose: a job
# that raises is logged by APScheduler and then silently keeps its schedule, but
# a job that raises *inside* the send-adjacent code path is the kind of thing
# that gets noticed a week later. Log it where our own logs are.

async def low_balance_job() -> None:
    try:
        result = await check_low_balance()
        logger.info(f"low-balance check: {result}")
    except Exception as e:
        logger.exception(f"low-balance check failed: {e}")


async def failure_digest_job() -> None:
    try:
        # Synchronous DB work off the event loop: the digest runs a query over
        # sms_messages, and blocking the loop for it would stall every request
        # in flight.
        result = await asyncio.to_thread(send_failure_digest)
        logger.info(f"failure digest: {result['total']} failures, "
                    f"alerted={result['alerted']}")
    except Exception as e:
        logger.exception(f"failure digest failed: {e}")
