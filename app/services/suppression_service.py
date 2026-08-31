"""The recent-contact suppression window: the rule, and where its number lives.

Its own module for two reasons, and the 500-line rule is only the second.

The first is that this is the one rule in the codebase that decides whether a
real person gets a text they did not ask for, and it is on the escalation list by
name. It shipped at 3 days and withheld 6,856 of 6,857 recipients across two
consecutive campaigns before anybody could see what was doing it — the diagnosis
took a SQL query. A rule that can silently swallow an entire audience should be
one file that a reader can hold in their head, not four functions scattered
through the pre-flight checks.

**The rule itself is unchanged and is not tuned here.** A contact texted inside
the window is held back; the comparison, the blindness to category and the
lexicographic-on-ISO trick are exactly as they were. What 5e A5 changed is where
the *number* comes from: `.env` remains the default, and a value stored through
Settings overrides it, read fresh on every call so a change takes effect on the
next send rather than the next restart.

Every function takes a Session, and none of them may default it. A caller that
forgot would silently fall back to the `.env` value and the Settings field would
stop applying on that one path — the same shape of defect as a guard wired into
only one of its call sites, which is what `should_auto_block()` was until 5d.
"""

import logging
from datetime import datetime, timedelta
from typing import List, Optional, Sequence, Tuple

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.app_setting import get_setting, set_setting

logger = logging.getLogger("suppression")


SUPPRESSION_DAYS_KEY = "recent_contact_suppression_days"

# An upper bound, not a policy. Nothing about the rule breaks at 91 days, but a
# fat-fingered "365" would withhold a year of buyers and read on screen as a
# working campaign with no recipients, so the write path refuses it rather than
# discovering it on send night.
SUPPRESSION_DAYS_MAX = 90


def suppression_days(db: Session) -> int:
    """How many days after a text a contact is held back from the next one.

    Read on every call rather than cached, which is what makes a change on the
    Settings screen take effect on the next send instead of the next restart.
    It is one indexed lookup on a table with a handful of rows.

    A stored value that is missing or unusable falls back to the configured
    default rather than to a hardcoded number: the write path validates, so a
    junk row means something edited the database directly, and the honest answer
    is then "what this box was deployed with".
    """
    raw = get_setting(db, SUPPRESSION_DAYS_KEY)
    if raw is None:
        return settings.RECENT_CONTACT_SUPPRESSION_DAYS
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        logger.warning("suppression window %r is not a number; using the configured "
                       "default of %s days", raw, settings.RECENT_CONTACT_SUPPRESSION_DAYS)
        return settings.RECENT_CONTACT_SUPPRESSION_DAYS
    if not 0 <= value <= SUPPRESSION_DAYS_MAX:
        logger.warning("suppression window %s is outside 0–%s; using the configured "
                       "default of %s days", value, SUPPRESSION_DAYS_MAX,
                       settings.RECENT_CONTACT_SUPPRESSION_DAYS)
        return settings.RECENT_CONTACT_SUPPRESSION_DAYS
    return value


def set_suppression_days(db: Session, days) -> int:
    """Store the window. Raises ValueError on anything that is not 0–90 days.

    0 is a legitimate setting and the one production currently runs: it means
    "hold nobody back". It is not the same as switching the rule off, because the
    comparison still runs — it simply never matches.
    """
    try:
        value = int(str(days).strip())
    except (TypeError, ValueError):
        raise ValueError("The suppression window has to be a whole number of days.")
    if not 0 <= value <= SUPPRESSION_DAYS_MAX:
        raise ValueError(
            f"The suppression window has to be between 0 and {SUPPRESSION_DAYS_MAX} days. "
            f"0 holds nobody back; {SUPPRESSION_DAYS_MAX} is as far as this goes."
        )
    set_setting(db, SUPPRESSION_DAYS_KEY, str(value),
                description="Days a contact is held back after being texted")
    return value


def suppression_cutoff(db: Session, now: Optional[datetime] = None) -> str:
    """ISO timestamp before which a contact is considered "not texted recently".

    Returned as a string because `contacts.last_messaged_at` is an ISO string
    and both are produced by `datetime.isoformat()` — same format, so a
    lexicographic comparison is a chronological one. Parsing every contact's
    timestamp to compare it would be the same answer, slower, and would throw on
    the one malformed row.
    """
    now = now or datetime.now()
    return (now - timedelta(days=suppression_days(db))).isoformat()


def partition_recent(db: Session, recipients: Sequence,
                     cutoff: Optional[str] = None) -> Tuple[List, List]:
    """Split recipients into (sendable, recently texted).

    Deliberately blind to category. A buyer tagged Food Service, Equipment and
    Estates is one person with one phone, and three correct campaigns in a week
    is still three texts in a week to him. The reference system suppressed
    within a list and re-texted the overlap; the overlap is where the opt-outs
    came from.
    """
    cutoff = cutoff or suppression_cutoff(db)
    sendable, suppressed = [], []
    for contact in recipients:
        last = getattr(contact, "last_messaged_at", None)
        (suppressed if last and last > cutoff else sendable).append(contact)
    return sendable, suppressed


def suppression_reason(db: Session) -> str:
    days = suppression_days(db)
    return (f"Texted within the last {days} day{'s' if days != 1 else ''} — held back "
            f"so nobody gets two messages in a row")


def suppression_clears_at(suppressed: Sequence, days: int) -> Optional[str]:
    """When the *last* held-back contact in this set becomes sendable again.

    The latest `last_messaged_at` in the set, plus the window. Deliberately the
    latest and not the earliest: the question the composer is answering is "when
    can I send this to all of them", and the earliest would give a time at which
    most of the hold is still in force.

    Returns None when there is nothing to say — an empty set, a window of 0, or
    timestamps we cannot parse. A caller must render nothing rather than guess:
    an explanation of a rule that is not running is noise, and a wrong clearing
    time is worse than none.
    """
    if days <= 0:
        return None
    stamps = [s for s in (getattr(c, "last_messaged_at", None) for c in suppressed) if s]
    if not stamps:
        return None
    try:
        latest = datetime.fromisoformat(max(stamps))
    except (TypeError, ValueError):
        logger.warning("suppressed set carries an unparseable last_messaged_at; "
                       "no clearing time will be shown")
        return None
    return (latest + timedelta(days=days)).isoformat()


def clears_at_clock(stamp: Optional[str]) -> str:
    """A clearing time as "10:11am", or "10:11am on 3 Sep" if it is not today.

    Lives beside the function that computes the timestamp, because two surfaces
    now render it and they have to agree: the composer's pre-flight checklist
    row before the send (5e A6) and the abort reason after it (decision 006).
    Those two sentences are about the same rule and the same moment, and a
    second copy of this formatting is how they end up disagreeing by an hour.

    The only function in this module that does not take a Session, and it is
    not an exception to that rule so much as outside it: it reads no window and
    no setting, it converts a string this module produced into the words a
    client reads.

    Server-local, like every other time this product prints. **Never raises** —
    it is decoration on a sentence whose job is to explain a failure, and a
    malformed timestamp must not take that sentence down with it.
    """
    try:
        when = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return str(stamp)
    hour = when.hour % 12 or 12
    stamped = f"{hour}:{when.minute:02d}{'am' if when.hour < 12 else 'pm'}"
    if when.date() == datetime.now().date():
        return stamped
    return f"{stamped} on {when.day} {when.strftime('%b')}"


