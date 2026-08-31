"""top-up stamp on sms_messages

One additive nullable column: `sms_messages.top_up_at`.

5e A4 lets contacts be added to a campaign that has already sent, receive the
same message, and fold into that campaign's totals. The spec's requirement is
that the addition stays distinguishable — a report should read
"1,200 + 5 added 26 Aug", not a silently different number three weeks later.

A column rather than a second table, and a timestamp rather than a batch
counter. The question a report asks is "what was added, and when", which is one
`GROUP BY top_up_at` over rows that already exist. A `campaign_top_ups` table
would carry counts that have to be kept in step with the message rows they
describe, and the lesson this codebase keeps relearning is to count in the
database rather than in a second place that can disagree with it.

NULL means the original send. That is the honest default for every row written
before this migration and for every row the first send of a campaign writes; it
is not "unknown".

Trivially reversible: the downgrade drops the column it added. Nothing here
touches `ix_contacts_phone`, any index on `sms_messages`, or any existing
column.

**Deliberately not `batch_alter_table`**, for the same reason
`b7e3c9a1d024` states at length: batch mode recreates `sms_messages` and rebuilds
every index on it, and SQLite adds a nullable column with no default in place as
a metadata-only change. `idx_sms_campaign`, `idx_sms_status`, `idx_sms_sent_at`
and `ix_sms_messages_external_id` are never dropped —
`tests/test_migrations.py` asserts it rather than trusting this paragraph.

The downgrade *does* need batch mode: Alembic routes SQLite's `DROP COLUMN`
through a table rebuild regardless. That is the reversal path, run by hand, not
the forward one every deploy takes.

Revision ID: c4f1a80b6e37
Revises: b7e3c9a1d024
Create Date: 2026-08-27 09:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4f1a80b6e37'
down_revision: Union[str, None] = 'b7e3c9a1d024'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # In place. See the note above: `sms_messages` is the largest table on the
    # box and its indexes must not be rebuilt for an additive nullable column.
    op.add_column("sms_messages",
                  sa.Column("top_up_at", sa.String(length=50), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("sms_messages", schema=None) as batch_op:
        batch_op.drop_column("top_up_at")
