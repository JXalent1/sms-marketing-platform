"""record the cap a campaign was built with

Revision ID: a91d5f2c6b70
Revises: e2a7c3d15b48
Create Date: 2026-08-30

`campaigns.batch_size` — the "send to the first 50 as a test" cap, which the
composer has always accepted and the builder has always applied and then thrown
away.

Additive, nullable, in-place `ADD COLUMN`. No index is created and none is
touched: `campaigns` carries `idx_campaigns_status` and `idx_campaigns_created`
and this migration does not go near either. Nothing is backfilled — NULL means
"no cap was recorded", which for a campaign built before this revision is the
honest answer rather than "no cap was asked for".

## Why the column exists

The cap is applied once, at build time, and the campaign then keeps no memory of
it. Every consequence of that has been a send to people the client did not ask
to reach:

  - 5e: a top-up computed "everyone the audience resolves to now, minus everyone
    with a message row", which could not tell the remainder a cap withheld from
    somebody added since. One click delivered to all of them. Fixed by changing
    what "added since" means, without needing this column.
  - 5h: releasing a hold the suppression window froze. Those rows exist for
    every held-back contact and were never subject to the cap — a campaign
    capped at 50 with 6,000 held back would release all 6,000 on one click.
    That one cannot be fixed by redefining a set: the rows are legitimately
    held-back rows on that campaign. The only way to decline is to know a cap
    was asked for.

So `campaign_release.releasable()` reads it and releases nothing on a campaign
that carries one, which is the no-change position while
`decisions/006-releasing-a-hold-on-a-campaign-that-was-capped.open.md` is open.

`downgrade()` drops the column. Reversible, and nothing else reads it.
"""

from alembic import op
import sqlalchemy as sa

revision = 'a91d5f2c6b70'
down_revision = 'e2a7c3d15b48'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Plain ADD COLUMN, not batch_alter_table. The batch form rebuilds the whole
    # table and every index on it, which 5g's review caught happening to
    # `sms_messages` under a docstring claiming it touched no index.
    op.add_column('campaigns', sa.Column('batch_size', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('campaigns', 'batch_size')
