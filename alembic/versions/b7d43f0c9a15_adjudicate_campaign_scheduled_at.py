"""adjudicate campaigns.scheduled_at — one clock, the client's

Revision ID: b7d43f0c9a15
Revises: d7e2a91c4f36
Create Date: 2026-09-08

**No schema changes. This revision classifies, and rewrites, data in a live
table.** Run `scripts/backup.sh` before applying it to production.

## The defect

`campaigns.scheduled_at` arrives from an `<input type="datetime-local">`, which
submits the wall clock the operator typed with **no zone attached** —
`2026-09-09T18:00` for six in the evening. `due_campaign_ids()` compared it
against `datetime.now()`, and the droplet's clock is UTC. So a campaign
scheduled for **6:00 PM Eastern was dispatched at 18:00 UTC, 2:00 PM Eastern** —
four hours early in EDT, five in EST. The campaign in front of the operator when
this was found was named `09/09, 6:00 PM Private Record Collection`.

## The ruling, and why nothing is converted

Session 5m settled that `scheduled_at` **is** wall clock in `APP_TIMEZONE`, and
fixed the reader rather than the data. `app/core/clock.py` carries the reasoning;
the half that matters here is that every value already in this column was typed
as Eastern wall clock and was merely being *read* as UTC. Reading it correctly is
the fix. Converting it as well would shift each row a second time and put the
6:00 PM campaign out at 10:00 PM.

So this migration is an **adjudication**, and it runs where the rows are — the
development database has no scheduled campaign at all, and the box that does is
one nobody can inspect from here. Every value is classified and the classification
is printed, so the deploy that applies 5m says on the record what it found.

## How it classifies, and what it does with each class

Read off the format, never guessed from the row — `f4a1c7d90e52`'s rule:

  * **naive** (`YYYY-MM-DDTHH:MM[:SS[.ffffff]]`, or a space separator): the
    browser's own output, and by the ruling above it is already what this column
    means. **Left exactly as it is**, and counted.
  * **offset-bearing** (`…+00:00`, `…Z`, any offset): denotes an *instant*, not a
    wall clock. Nothing in this application writes one — only an API caller
    could — and under the new reader it would compare wrongly against a naive
    cutoff. Its wall clock in the client's zone is exactly determined, so it is
    **converted** and counted. This is not a guess: the value says which instant
    it is.
  * **anything else** — a bare date, a truncated write, junk: **left alone and
    logged with its id**, never guessed at. A campaign carrying one will simply
    never come due, which is the safe direction for a value nobody can read.

## Idempotence

A converted row is written back naive, so a second run classifies it as naive and
skips it. Re-running cannot double-shift a row.

## downgrade()

A no-op, and the same argument `f4a1c7d90e52` and `e2a7c3d15b48` make. The naive
rows were not touched, so there is nothing to restore. A converted row denotes
the identical instant before and after; the only thing lost is the spelling, and
the spelling was the defect. Reversing it would mean re-attaching an offset this
migration cannot know, which is how one defect becomes an unauditable set.
"""

import logging
import re

from alembic import op
import sqlalchemy as sa

from datetime import datetime

from app.core.config import APP_ZONE

revision = 'b7d43f0c9a15'
down_revision = 'd7e2a91c4f36'
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.campaign_scheduled_at")

# A naive local timestamp: date, a separator, hours and minutes, optionally
# seconds and a fractional part, and **nothing after it**. `fullmatch`, because
# the classification decides whether a row is rewritten and "does it contain a
# +" is a test for the absence of something — which says yes to a bare date and
# to any truncated write.
NAIVE_SHAPE = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?")


def classify(value) -> str:
    """"naive", "offset" or "unreadable" — a property of the format, not a guess."""
    text = str(value or "").strip()
    if NAIVE_SHAPE.fullmatch(text):
        return "naive"
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return "unreadable"
    return "offset" if parsed.tzinfo is not None else "unreadable"


def to_client_wall_clock(value) -> str:
    """One offset-bearing value as the client's wall clock. None if it is not one.

    A named function so 5m's tests drive the exact conversion this migration
    applies rather than a re-implementation of it: a rationale and its mechanism
    have to be checked against each other, and a test that rewrites the
    arithmetic checks neither.
    """
    if classify(value) != "offset":
        return None
    parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    return parsed.astimezone(APP_ZONE).replace(tzinfo=None).isoformat()


def upgrade():
    connection = op.get_bind()
    rows = connection.execute(sa.text(
        "SELECT id, status, scheduled_at FROM campaigns WHERE scheduled_at IS NOT NULL"
    )).fetchall()

    counts = {"naive": 0, "offset": 0, "unreadable": 0}
    pending = 0
    for campaign_id, status, scheduled_at in rows:
        kind = classify(scheduled_at)
        counts[kind] += 1
        # Readable drafts only. An unreadable row is still a draft and will
        # never come due, so counting it here would make the line below claim
        # it "will go out at the time it says" — which is the one thing this
        # migration knows is false about it.
        if status == "draft" and kind != "unreadable":
            pending += 1
        if kind == "offset":
            connection.execute(
                sa.text("UPDATE campaigns SET scheduled_at = :value WHERE id = :id"),
                {"value": to_client_wall_clock(scheduled_at), "id": campaign_id})
        elif kind == "unreadable":
            # Named, not guessed at. This campaign will never come due, which is
            # the safe direction for a timestamp nobody can read.
            logger.warning(
                "campaign %s has an unreadable scheduled_at (%r); left alone. It "
                "cannot come due until someone re-schedules it.",
                campaign_id, scheduled_at)

    logger.info(
        "scheduled_at adjudicated in %s: %s left as the client's wall clock, "
        "%s converted from an offset, %s unreadable and left alone. %s readable "
        "draft(s) will now go out at the time they say. An unreadable row cannot "
        "come due at all until someone re-schedules it; its id is logged above.",
        APP_ZONE, counts["naive"], counts["offset"], counts["unreadable"], pending)


def downgrade():
    """Nothing to restore — see the module docstring."""
    pass
