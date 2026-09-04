"""normalise contact_lists.created_at — one clock, one spelling

Revision ID: f4a1c7d90e52
Revises: c8a2e5f14b90
Create Date: 2026-09-04

**No schema changes. This revision rewrites data in a live table.**
Run `scripts/backup.sh` before applying it to production.

## The defect

`contact_lists.created_at` had two writers keeping two clocks:

  - `contact_service.get_or_create_list()` omitted the column, so the model's
    `server_default=func.now()` handed the write to SQLite's CURRENT_TIMESTAMP.
    That is **UTC**, and it spells itself `YYYY-MM-DD HH:MM:SS` — a space, no
    microseconds.
  - `import_service.commit()` wrote `datetime.now().isoformat()`. That is
    **local**, and it spells itself `YYYY-MM-DDTHH:MM:SS.ffffff` — a "T".

Both spellings are in the live database. Nothing compared these rows until
session 5i made recency the sort order of the audience picker, the dashboard's
list cards and the composer — at which point the error becomes visible, and it
is one-directional: every server-defaulted row reads up to five hours **newer**
than it is. The lexicographic comparison is separately wrong, because a space
(0x20) sorts before a "T" (0x54), so a server-defaulted row always loses to a
same-day isoformat row however much later it was written.

Session 5i removed the server default, so from here there is one writer, one
clock and one spelling. This revision fixes what is already stored.

## Why classifying by format is safe

The two writers are **1:1 with the two spellings, by construction**:

  - SQLite's CURRENT_TIMESTAMP cannot emit a "T". Its output format is fixed at
    `YYYY-MM-DD HH:MM:SS`.
  - `datetime.isoformat()` cannot emit a space. Its separator defaults to "T"
    and no caller in this repo passes `sep=" "`.

So a space-separated value **is** a server-default row, **is** UTC, and converts.
A "T"-separated value was written by the application clock and is left exactly
as it is. **The classification is read off the format, not guessed from the
row** — no heuristic about which rows "look like" imports, no inference from
`source`, nothing that could be wrong about a particular row while being right
on average. A value that matches neither shape is left alone and logged: this
migration converts what it can prove and touches nothing it cannot.

Part B of session 5i is the check that this holds on production: dump every
`created_at` there before trusting this migration. A third spelling means the
classification is not 1:1 and this file is wrong.

## Idempotence

Converting a UTC value writes it back in isoformat, so it carries a "T"
afterwards and a second run skips it. Re-running this migration cannot
double-shift a row.

## downgrade()

Deliberately a no-op, and not because reversing the arithmetic is hard. Once a
row is rewritten in isoformat there is nothing that distinguishes "converted
from UTC" from "always was local", so a downgrade could only re-shift rows it
cannot identify — which is how one defect becomes an unauditable set of them.
The same argument as `e2a7c3d15b48`'s refusal to backfill. Nothing here touches
a table, a column or an index, so there is no schema to restore.
"""

import logging
import re
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

revision = 'f4a1c7d90e52'
down_revision = 'c8a2e5f14b90'
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.contact_list_created_at")

# The exact shape SQLite's CURRENT_TIMESTAMP produces, and nothing wider: four
# digits, a space separator, seconds, no fractional part, end of string.
#
# `fullmatch` rather than `search`, and this rather than "does it contain a T",
# because the classification decides whether a row is **rewritten**. A test for
# the absence of something ("no T, so it must be the server default") says yes to
# a bare date, a truncated write and anything else that is neither writer's
# output. Those get left alone and logged instead — this migration converts what
# it can prove and touches nothing it cannot.
SERVER_DEFAULT_SHAPE = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")


def written_by_the_server_default(value: str) -> bool:
    """True when this value can only have come from SQLite's CURRENT_TIMESTAMP.

    Which is also to say: true when it is UTC. That is the whole of the
    classification, and it is a property of the format rather than a guess about
    the row — `datetime.isoformat()` cannot emit a space separator and
    CURRENT_TIMESTAMP cannot emit anything else, so the two writers are 1:1 with
    the two shapes.
    """
    return bool(SERVER_DEFAULT_SHAPE.fullmatch((value or "").strip()))


def utc_text_to_local_iso(value: str) -> str:
    """One server-default value, converted. None when it is not one.

    Kept as a named function so session 5i's tests can drive the exact
    conversion this migration applies rather than a re-implementation of it —
    a rationale and its mechanism have to be checked against each other, and a
    test that reimplements the arithmetic checks neither.
    """
    text = (value or "").strip()
    if not written_by_the_server_default(text):
        return None
    try:
        naive = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    return (naive.replace(tzinfo=timezone.utc)
                 .astimezone()
                 .replace(tzinfo=None)
                 .isoformat())


def upgrade():
    connection = op.get_bind()
    rows = connection.execute(
        sa.text("SELECT id, created_at FROM contact_lists")
    ).fetchall()

    converted = unchanged = 0
    for list_id, created_at in rows:
        local_iso = utc_text_to_local_iso(created_at)
        if local_iso is None:
            # Written by the application clock — already local, already
            # isoformat — or neither spelling, in which case Part B says this
            # cannot happen and the honest answer is to leave the row rather
            # than guess at it.
            if (created_at or "").strip() and "T" not in str(created_at):
                logger.warning(
                    "contact_lists.id=%s has created_at=%r, which matches "
                    "neither writer's format. Left unchanged.", list_id, created_at)
            unchanged += 1
            continue

        connection.execute(
            sa.text("UPDATE contact_lists SET created_at = :value WHERE id = :id"),
            {"value": local_iso, "id": list_id},
        )
        converted += 1

    logger.info("contact_lists.created_at normalised: %d converted from UTC, "
                "%d left alone", converted, unchanged)


def downgrade():
    """No-op — see the module docstring."""
