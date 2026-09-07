"""scrape_jobs: the second spend meter, and two more kinds of nothing

Revision ID: e7c05b3a1d94
Revises: b52d9c1f4e08
Create Date: 2026-09-04

Five additive columns on `scrape_jobs`, all nullable in the DDL, all backfilled,
none indexed. Nothing existing is dropped, renamed or re-typed.

## Why five columns rather than reusing the ones that are there

**The two spend meters are two bills.** `cost`, `lookups_performed` and
`lookups_cached` are the carrier line-type spend: charged per *number*, capped
by `PROSPECT_LOOKUP_MONTHLY_CAP`, and paid out of the same balance campaigns
send from. `api_requests`, `api_requests_skipped` and `api_cost` are the
discovery API's: charged per *request*, where one request returns up to twenty
businesses, capped by a different setting and drawn on a different account.

They have different units and different remedies when one runs out, so adding
them into one column would produce a figure that answers no question anybody
asks. This file's own model docstring opens with what happens when a domain
concept lives as one overloaded value.

**`records_excluded` and `records_known` are two kinds of nothing.** A search
term that mostly returns other auction houses should be retired. A search term
that mostly returns businesses the client already has is *working* — it is
covering a niche that is already covered. Both would have been folded into
`records_invalid`, which means "the source produced a broken record", and a job
row is how a term gets judged.

## The monthly request cap reads `api_requests`

`api_budget.spend_this_month()` sums this column over jobs whose `started_at`
falls in the calendar month, which works for the reason `phone_lookups.cost`
does: **`scrape_jobs.started_at` has one writer** — `scrape_runner.run_job()`,
using `datetime.now()` — and no server default, so there is no second clock and
no second ISO spelling. `contact_list_members.added_at` is what that looks like
when it goes wrong, and `tests/test_google_places.py` asserts the single-writer
property rather than trusting this paragraph.

No column below carries a server default, for the same reason: 5i spent a whole
migration undoing `DEFAULT (CURRENT_TIMESTAMP)` on `contact_lists.created_at`,
and removing a default in SQLite means rebuilding the table.

## downgrade()

Drops all five. Trivially reversible: no index is built on any of them, no
existing column changes, and a job row that loses its meters is a job row from
before there was a second meter to record.
"""

from alembic import op
import sqlalchemy as sa

revision = 'e7c05b3a1d94'
down_revision = 'b52d9c1f4e08'
branch_labels = None
depends_on = None

# name, sql type, the value existing rows get
COLUMNS = (
    ("records_excluded", sa.Integer(), "0"),
    ("records_known", sa.Integer(), "0"),
    ("api_requests", sa.Integer(), "0"),
    ("api_requests_skipped", sa.Integer(), "0"),
    ("api_cost", sa.String(length=20), None),
)


def upgrade():
    for name, type_, backfill in COLUMNS:
        op.add_column("scrape_jobs", sa.Column(name, type_, nullable=True))
        if backfill is not None:
            # Backfilled rather than left NULL. A counter where "none" is
            # written two ways is one a future `SUM()` or `= 0` gets wrong, and
            # these are counters a cap is measured from.
            op.execute(f"UPDATE scrape_jobs SET {name} = {backfill} "
                       f"WHERE {name} IS NULL")


def downgrade():
    for name, _type, _backfill in reversed(COLUMNS):
        op.drop_column("scrape_jobs", name)
