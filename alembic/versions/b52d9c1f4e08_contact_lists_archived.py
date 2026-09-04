"""contact_lists.archived — hide a list from the picker without losing it

Revision ID: b52d9c1f4e08
Revises: a3f1e08c5d47
Create Date: 2026-09-04

Additive, nullable, backfilled to 0. Deliberately a separate revision from
`a3f1e08c5d47`: the index convergence and this column have nothing to do with
each other beyond arriving in the same session, and a reviewer must be able to
revert either one without the other.

## What the column means

Nothing in the product could rename or hide a list, so every test upload became
a permanent entry in the audience dropdown — ten pieces of debris from one
evening were deleted by hand on the live box on 2026-09-04, which is something
the client has no way to do himself.

`archived` is the same ruling the categories got, for the same reason:
**hidden from the picker, still resolving for history.** `list_summaries()`
excludes an archived list, so it leaves the composer's dropdown, `/api/lists`
and the dashboard cards. `resolve_audience()`, `_term_ids_query()`,
`_term_label()` and `audience_count()` all keep resolving it, because a campaign
that targeted list 20 must still render that list's *name* in history and in its
report. Archiving a list he has already sent to must never turn a report label
into the raw string `list:20`.

## No server default, on purpose

`contact_lists.created_at` was created with `DEFAULT (CURRENT_TIMESTAMP)` in
`f69dc078ee13`, and session 5i spent a whole migration undoing what that second
writer had done to the column. Removing a column default in SQLite means
rebuilding the table, which is escalation item 8, so that DDL is still there and
5i had to close the hole at the ORM layer instead.

This column does not repeat it. The model carries a Python-side `default=0` and
the table carries no default at all, so there is exactly one writer. Existing
rows are backfilled here rather than left NULL, so the column reads the same way
whether a row predates this revision or not.

`is_archived()` in `app/services/contact_service.py` still treats NULL as "not
archived" — a raw `INSERT` naming no `archived` can still produce one, which is
the same reason `parse_created_at()` still understands the old spelling of
`created_at`.

## downgrade()

Drops the column. Trivially reversible in both directions: nothing else reads
it, no index is built on it, and an archived list downgraded is simply a visible
list again. SQLite 3.35+ drops a column in place, and `archived` carries no
index, so this does not rebuild the table or its unique index on `name`.
"""

from alembic import op
import sqlalchemy as sa

revision = 'b52d9c1f4e08'
down_revision = 'a3f1e08c5d47'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("contact_lists", sa.Column("archived", sa.Integer(), nullable=True))
    # Backfilled rather than left NULL. Both spellings mean "not archived" to
    # every reader, but a column where "no" is written two ways is one a future
    # query will eventually get wrong with `archived = 0`.
    op.execute("UPDATE contact_lists SET archived = 0 WHERE archived IS NULL")


def downgrade():
    op.drop_column("contact_lists", "archived")
