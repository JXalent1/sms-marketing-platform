"""carrier error code on sms_messages

One additive nullable column: `sms_messages.error_code`.

The auto-block rules used to look for carrier codes inside `error_message`,
which is carrier-controlled prose. That prose quotes the destination number, so
a plain substring test for "21610" — Twilio's "sent to an unsubscribed
recipient" — also matches +1 321-610-xxxx, an assignable Brevard County number
in this client's own market. Blocking is permanent and silent: the wrongly
blocked buyer simply stops appearing in campaigns.

Giving the code its own column is what lets `app/sms/compliance.py` match codes
against a field the carrier populated and never against prose. See
decisions/003-auto-block-fragments-on-the-webhook-path.md.

Nothing is backfilled. Rows written before this migration have no code because
the webhook discarded it, and inventing one from the prose is the exact defect
this column exists to end.

Trivially reversible: the downgrade drops the column it added. Nothing here
touches `ix_contacts_phone`, any index on `sms_messages`, or any existing
column.

**Deliberately not `batch_alter_table`, unlike its neighbours in this
directory.** Batch mode recreates the whole table and rebuilds every index on
it, which is exactly what CLAUDE.md's escalation item 8 names as needing a human
— and it is unnecessary here. SQLite supports `ALTER TABLE ... ADD COLUMN` in
place for a nullable column with no default; it is a metadata-only change, and
`idx_sms_campaign`, `idx_sms_status`, `idx_sms_sent_at` and
`ix_sms_messages_external_id` are never dropped. The neighbouring migration uses
batch mode because it adds a REFERENCES constraint, which SQLite genuinely
cannot do in place.

The downgrade *does* need batch mode: SQLite gained `DROP COLUMN` only in 3.35
and Alembic routes it through a table rebuild regardless. That is the reversal
path, run by hand, not the forward one every deploy takes.

Revision ID: b7e3c9a1d024
Revises: 8c1d4a2f70b3
Create Date: 2026-08-26 12:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7e3c9a1d024'
down_revision: Union[str, None] = '8c1d4a2f70b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # In place. See the note above on why this is not batch mode: `sms_messages`
    # is the largest table on the box and its indexes must not be rebuilt for an
    # additive nullable column.
    op.add_column("sms_messages",
                  sa.Column("error_code", sa.String(length=20), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("sms_messages", schema=None) as batch_op:
        batch_op.drop_column("error_code")
