"""campaign category, suppression counter and scheduled send

Five additive columns on `campaigns`:

  category_id             which auction niche the campaign is for
  cross_category_override the recorded human decision to send without one
  suppressed_count        how many contacts were held back as recently texted
  scheduled_at            when a scheduled campaign becomes due

`category_id` is nullable here and required by the API, deliberately. Campaigns
created before this migration predate the concept and nothing may be backfilled
into them: a guessed category on an old campaign is indistinguishable from one a
human chose, and the whole point of the column is that it records a choice.

Written with `batch_alter_table` because SQLite cannot add a REFERENCES
constraint to an existing table in place. Batch mode recreates `campaigns` and
carries its definition across; nothing here goes near `ix_contacts_phone` or any
index on `sms_messages`.

Trivially reversible — the downgrade drops the four columns and the two indexes
it added, and no existing column is altered, renamed or dropped.

Revision ID: 8c1d4a2f70b3
Revises: 4119449a9937
Create Date: 2026-08-19 21:04:52.118304

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8c1d4a2f70b3'
down_revision: Union[str, None] = '4119449a9937'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("campaigns", schema=None) as batch_op:
        batch_op.add_column(sa.Column("category_id", sa.Integer(), nullable=True))
        # server_default as well as a Python-side default: the columns are NOT
        # NULL, and an ALTER on a table with rows in it has to have something to
        # write into those rows. Without it this migration fails on any database
        # that has ever held a campaign — which is every one that matters.
        batch_op.add_column(sa.Column("cross_category_override", sa.Integer(),
                                      nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("suppressed_count", sa.Integer(),
                                      nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("scheduled_at", sa.String(length=50), nullable=True))
        batch_op.create_foreign_key(
            "fk_campaigns_category_id", "categories", ["category_id"], ["id"],
        )
        batch_op.create_index("idx_campaigns_scheduled_at", ["scheduled_at"])
        batch_op.create_index("idx_campaigns_category", ["category_id"])


def downgrade() -> None:
    with op.batch_alter_table("campaigns", schema=None) as batch_op:
        batch_op.drop_index("idx_campaigns_category")
        batch_op.drop_index("idx_campaigns_scheduled_at")
        batch_op.drop_constraint("fk_campaigns_category_id", type_="foreignkey")
        batch_op.drop_column("scheduled_at")
        batch_op.drop_column("suppressed_count")
        batch_op.drop_column("cross_category_override")
        batch_op.drop_column("category_id")
