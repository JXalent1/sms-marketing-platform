"""sms_messages.metered_at — the usage meter's ledger

One additive nullable column: `sms_messages.metered_at`. Nothing existing is
dropped, renamed, re-typed or re-indexed.

## What it is for

Session B1 metered a campaign the instant its send loop returned and trusted
Stripe's meter-event `identifier` to make every later report of the same
campaign free. `decisions/011` measured what that gets wrong: the figure could
never be corrected once the delivery webhook moved rows out of
`BILLABLE_STATUSES` (12,000 metered, 8,000 used), a top-up's rows were never
metered at all, and — the largest — Stripe enforces identifier uniqueness only
over a rolling 24 hours, so `backfill_unreported()` billed any period older
than a day a second time.

The fix is to keep the ledger **here**, in the one database that also renders
`/usage`. A metering pass selects rows that are billable, settled and unmarked,
reports them, and stamps `metered_at` in the transaction that follows the
accepted report — the report is an HTTP call and cannot share a transaction
with anything, which is why the batch is also written down *before* the call
and cleared in the same commit as the mark. A row carrying `metered_at` is
never reported again by any path. Stripe's identifier stays as a second line
of defence for the same-minute retry it is designed for, and stops being
load-bearing.

## NULL means "never metered", and every existing row is NULL

Every row in this table at the moment this migration runs has **never been
metered**. Not "unknown": nothing has been metered, by any code path, because
no Stripe customer has ever been stored on this account — B1's Part B (the
dashboard setup and the keys) was deliberately held until this session landed.
So NULL here is the honest value for history, not a gap to backfill, and the
pass reads it as "not yet". Whether a NULL row is *in scope* for the meter is a
separate question the pass answers from the stored subscription start: rows
sent before it are settled by the one-time balance on the first invoice and
stay NULL forever, which `tools/bill_period.py --unmetered` labels as such.

## Deliberately not `batch_alter_table`

For the reason `c4f1a80b6e37` (`top_up_at`) states at length: batch mode
recreates `sms_messages` and rebuilds every index on it, and SQLite adds a
nullable column with no default in place as a metadata-only change. No index
on `sms_messages` is dropped or rebuilt — `tests/test_migrations.py` asserts
the four it names survive, rather than trusting this paragraph, and
`test_index_convergence.py` covers `idx_sms_contact`. No index is added on the new
column either: an index on `sms_messages` is escalation item 8, and the pass's
selection ranges on `sent_at`, which is already indexed. The downgrade *does*
need batch mode, because Alembic routes SQLite's `DROP COLUMN` through a table
rebuild regardless; that is the reversal path, run by hand.

Revision ID: d7e2a91c4f36
Revises: e7c05b3a1d94
Create Date: 2026-09-08
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'd7e2a91c4f36'
down_revision: Union[str, None] = 'e7c05b3a1d94'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # In place, no server default: one writer (the metering pass), one clock
    # (`datetime.now()`), one ISO spelling — the `contact_list_members.added_at`
    # lesson, applied before the column has a second writer rather than after.
    op.add_column("sms_messages",
                  sa.Column("metered_at", sa.String(length=50), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("sms_messages", schema=None) as batch_op:
        batch_op.drop_column("metered_at")
