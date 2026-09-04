"""index contact_id on both sides of the freshness join, converging a live box
that already has them under different names

Revision ID: a3f1e08c5d47
Revises: f4a1c7d90e52
Create Date: 2026-09-04

## Why

Session 5i's per-list freshness query joins `contact_list_members` to
`sms_messages` on `contact_id`, and **neither side was indexed on that column**:
`contact_list_members` carries `idx_member_list` on `list_id` alone, and its
unique `(list_id, contact_id)` index has `list_id` leading, so it cannot serve a
lookup by `contact_id`; `sms_messages` had `campaign_id`, `status` and `sent_at`
and nothing on `contact_id`.

On production — ~30,000 messages against ~15,500 memberships on one vCPU — that
query took **10 minutes 2 seconds**, and it is synchronous inside an `async def`
route, so it held the event loop and the scheduler missed two ticks behind it.
The suite ran the same query on twelve rows in under a millisecond. See the
incident entry at the end of `status.md`.

## Why this migration is written the awkward way

The outage was ended by hand, on the live database, on 2026-09-04:

    CREATE INDEX IF NOT EXISTS ix_sms_messages_contact_id ON sms_messages(contact_id);
    CREATE INDEX IF NOT EXISTS ix_clm_contact_id          ON contact_list_members(contact_id);
    ANALYZE;

10m02s -> 0.95s. **So production's schema leads the migration history by two
indexes that exist in no migration and in no model.** A bare `op.create_index()`
raises `index ... already exists` there, and `deployment/deploy.sh` aborts the
deploy without restarting the service when a migration fails — which would make
the fix for the outage the one thing that cannot ship.

So each index is created only if it is absent, and the check is
`sa.inspect(bind).get_indexes(table)` rather than SQLite's `IF NOT EXISTS`: that
spelling is SQLite-specific and this project is Postgres-ready through
`DATABASE_URL`. The inspector is the portable question.

## The names

The hand-made names were typed under pressure and do not match this project's
convention, which is `idx_<table-ish>_<column>` — `idx_member_list`,
`idx_sms_campaign`, `idx_sms_status`, `idx_sms_sent_at`. They converge on
`idx_sms_contact` and `idx_member_contact`, and the hand-made ones are dropped.

**Create before drop, per table, and the order is the point.** Both orders leave
a window of milliseconds, but only one of them leaves a window in which the join
has no index at all — and the box is live during business hours, with a
composer that polls. Do it in the order that has no window.

Four indexes where there should be two is the failure mode this converges away
from: duplicates cost write time on every message row inserted, forever, on the
table the send path writes to.

## downgrade()

Drops the convention-named indexes if present and does **not** recreate the
hand-made ones. Those were an incident response, not a schema; recreating them
would put the database back into the undescribed state this revision exists to
end. A downgraded box is slow, which is recoverable and visible; a box carrying
two index names nothing describes is how this session happened.
"""

from alembic import op
import sqlalchemy as sa

revision = 'a3f1e08c5d47'
down_revision = 'f4a1c7d90e52'
branch_labels = None
depends_on = None

# table, the convention-named index, its column, the hand-made name it replaces.
#
# Both sides of the join key are indexed, not just the one SQLite happens to
# search today. Which side the planner picks is a statistics decision and it
# flips with row counts: at suite scale it searches `contact_list_members`, at
# production scale it searches `sms_messages`. An index on only the side today's
# statistics favour is one ANALYZE away from being the wrong one.
CONVERGENCE = (
    ("sms_messages", "idx_sms_contact", "contact_id", "ix_sms_messages_contact_id"),
    ("contact_list_members", "idx_member_contact", "contact_id", "ix_clm_contact_id"),
)


def _index_names(table: str) -> set:
    """What this database actually has on `table`, right now.

    Asked per table and immediately before it is acted on, because the answer
    differs between a fresh clone (neither index) and the live box (both, under
    the hand-made names) and this revision has to be correct on both.
    """
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade():
    for table, wanted, column, handmade in CONVERGENCE:
        present = _index_names(table)
        if wanted not in present:
            op.create_index(wanted, table, [column])
        # Read after the create, so the drop below is decided against the state
        # this migration has already produced rather than a stale snapshot.
        if handmade in _index_names(table):
            op.drop_index(handmade, table_name=table)


def downgrade():
    for table, wanted, _column, _handmade in CONVERGENCE:
        if wanted in _index_names(table):
            op.drop_index(wanted, table_name=table)
