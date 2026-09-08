"""The application's clock: one zone, one meaning, written down.

**Every naive timestamp this application stores is wall-clock time in
`APP_TIMEZONE`**, which is `America/New_York` for this client and is a setting
rather than a constant, on `BILLING_CYCLE_DAY`'s precedent. That sentence is the
whole of session 5m's A2 and it was not true before it.

What was wrong. `campaigns.scheduled_at` arrives from an `<input
type="datetime-local">`, which submits the wall clock the operator typed with no
zone attached — `2026-09-09T18:00` for six in the evening. The scheduler compared
it against `datetime.now()`, and the droplet's clock is UTC, so a campaign
scheduled for 6:00 PM Eastern was dispatched at 18:00 UTC: **2:00 PM Eastern,
four hours early in EDT and five in EST.** For an auction house whose entire
product is "the sale is tonight", a blast that lands mid-afternoon is worse than
one that does not land at all.

The ruling, and why it is this one. `scheduled_at` stays **wall clock in the
client's zone** rather than being converted to UTC, and the reader is fixed
instead of the data:

  * it is what he typed, it is what the input submits, and it is what every
    screen shows him — a round trip with nothing to get wrong in it;
  * `sent_at`, `created_at` and `added_at` are all naive local strings and are
    explicitly out of 5m's scope, so making `scheduled_at` alone an instant
    would leave one table with two rules. `CLAUDE.md` opens on what an
    overloaded column costs, and "the next session assumes one rule for all of
    them" is exactly the failure to design against;
  * the comparison in `due_campaign_ids()` therefore stays lexicographic on ISO
    strings, which is chronological while every value carries one format and one
    zone — and now they do, because this module is the only thing that produces
    "now" for it;
  * no migration converts a live row. The values already in the database were
    typed as Eastern wall clock and were being *read* as UTC; reading them
    correctly is the fix, and a conversion would have been a second wrong.

`now()` here does not depend on the box being configured in the right zone — it
asks `zoneinfo` — and `config.apply_process_timezone()` then sets the process TZ
so that the sixty-odd ambient `datetime.now()` calls elsewhere in the
application agree with it. Two mechanisms with two jobs: this module is the
declaration, the process TZ is what makes the rest of the code obey it on a box
whose own clock is UTC. Neither is redundant — the scheduler must be right even
if `tzset()` never ran, and `sent_at` must be right without sixty edits.

**`zoneinfo`, never a fixed offset.** September is EDT (UTC−4) and January is
EST (UTC−5); a `-4` constant is wrong for four months of the year, and one of
the two changeover dates is a Sunday morning in November when a scheduled
campaign would be an hour out.

What happens in the hour that occurs twice, 1:00–2:00 AM on the first Sunday in
November: a campaign scheduled for 1:30 AM becomes due at the first 1:30 AM
(EDT), is dispatched, and leaves `draft`. When the wall clock reads 1:30 AM
again an hour later the campaign is no longer a draft, and `due_campaign_ids()`
filters on `status == "draft"` — so it is not sent twice. In the hour that does
not exist, 2:00–3:00 AM on the second Sunday in March, a campaign scheduled for
2:30 AM becomes due the moment the clock jumps to 3:00 AM: half an hour late in
real time, at the first instant its wall clock has passed.
"""

from datetime import date, datetime
from typing import Optional

from app.core.config import APP_ZONE, APP_ZONE_NAME

# The zone, resolved once in `app.core.config` — see the note there for why it
# is read and validated in that module rather than in this one. Re-exported here
# because this is where the rule lives, and a reader looking for "what timezone
# is this application in" should find it in the file named for the clock.
ZONE = APP_ZONE
ZONE_NAME = APP_ZONE_NAME


def now() -> datetime:
    """Now, as wall clock in the client's zone, naive — what columns store.

    Naive on purpose. Every timestamp column in this application holds a naive
    local string, and handing an aware datetime to a caller that will
    `.isoformat()` it into one of them would put an offset on some rows and not
    others — which is the two-spellings half of the `contact_lists.created_at`
    defect, arriving from the other direction.
    """
    return datetime.now(ZONE).replace(tzinfo=None)


def now_iso() -> str:
    """Now, in the one spelling `scheduled_at` is compared against."""
    return now().isoformat()


def today() -> date:
    return now().date()


def wall_clock(moment: Optional[datetime]) -> datetime:
    """Any moment as this application's wall clock.

    An aware datetime is converted through `zoneinfo`; a naive one is already
    local by the rule at the top of this file and is returned unchanged. Callers
    pass an aware instant when they want to be independent of the box's own
    clock — the scheduler's tests do exactly that, which is what makes them
    prove the zone rather than the machine they run on.
    """
    if moment is None:
        return now()
    if moment.tzinfo is None:
        return moment
    return moment.astimezone(ZONE).replace(tzinfo=None)


def normalise_stored(value: Optional[str]) -> Optional[str]:
    """A `scheduled_at` as it must be stored: the client's wall clock, naive.

    The column's meaning is written down in this module and migration
    `b7d43f0c9a15` adjudicated the rows already in it — and neither of those
    stops the next offset-bearing value being written the day after the migration
    runs. `campaigns.scheduled_at` is a free `str` on the API, and an
    offset-bearing value never compares `<=` a naive cutoff until the wall clock
    passes the *offset's* hour: a campaign booked for `18:00:00+00:00` goes out
    at 6:00 PM Eastern, four hours late. Requirement 1 of session 5m is "one
    writer, one meaning" — the meaning was written down and the writer accepted
    anything, so this is the other half.

    An offset-bearing value is converted, exactly as the migration converts one:
    it names an instant, and that instant's wall clock here is determined. A
    naive value passes through untouched — it is already what the column means.
    Anything else raises `ValueError`, and the caller turns that into a refusal:
    a campaign whose time nobody can read never comes due, and refusing at
    creation is the difference between a message that is late and a message that
    is never sent.
    """
    text = (value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        raise ValueError(
            f"{value!r} is not a time this can schedule. Use the date-and-time "
            "picker, which sends your own local clock."
        ) from None
    if parsed.tzinfo is None:
        # Re-spelled rather than passed through. `fromisoformat` accepts
        # "2026-09-09T18" and "2026-09-09 18:00", and the comparison in
        # `due_campaign_ids()` is lexicographic — which is chronological only
        # while every value carries **one format** as well as one zone. That is
        # the half `contact_lists.created_at` cost a migration over, and one
        # canonical spelling at the one writer is what stops it recurring here.
        return parsed.isoformat()
    return parsed.astimezone(ZONE).replace(tzinfo=None).isoformat()


def clock_time(stamp) -> str:
    """A stored timestamp as "10:11am", or "10:11am on 3 Sep" when not today.

    The one server-side renderer of a client-facing time. Its browser-side twin
    is `fmtClock()` in `base.html`, which renders the same timestamps in the same
    words, from their components; between them nothing in this product renders a
    time through the viewer's own zone. (They differ on an input neither is ever
    given: `fmtClock()` answers null for a date with no time, this answers
    "12:00am". `suppression_clears_at()` is the only producer of what either
    receives and it always carries a time.)

    **Never raises** — it is decoration on sentences whose job is to explain a
    refusal, and a malformed timestamp must not take one of those down.
    """
    try:
        when = datetime.fromisoformat(str(stamp))
    except (TypeError, ValueError):
        return str(stamp)
    hour = when.hour % 12 or 12
    stamped = f"{hour}:{when.minute:02d}{'am' if when.hour < 12 else 'pm'}"
    if when.date() == today():
        return stamped
    return f"{stamped} on {when.day} {when.strftime('%b')}"
