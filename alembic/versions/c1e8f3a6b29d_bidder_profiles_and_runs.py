"""bidder_profiles and bidder_scrape_runs: a registered bidder's behaviour, beside the contact

Revision ID: c1e8f3a6b29d
Revises: b7d43f0c9a15
Create Date: 2026-09-22

Two new tables and nothing else. **`contacts` is not touched** — no column, no
index, and in particular not the unique index on `contacts.phone`, which is the
dedup guarantee (escalation item 4). The behaviour sits beside the contact,
keyed to it by id, for the reason `app/models/bidder_profile.py` gives: a
bidder's history is not a contact attribute, and a JSON blob on `contacts`
cannot be filtered on.

Trivially reversible: `downgrade()` drops two tables that nothing else
references. No server defaults anywhere — every timestamp here has one writer,
`app/services/bidder_scrape.py`, keeping one clock (`datetime.now()` in
`APP_TIMEZONE`), which is the lesson `contact_lists.created_at` cost.

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c1e8f3a6b29d'
down_revision: Union[str, None] = 'b7d43f0c9a15'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('bidder_scrape_runs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('platform', sa.String(length=50), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('started_at', sa.String(length=50), nullable=False),
    sa.Column('finished_at', sa.String(length=50), nullable=True),
    sa.Column('rows_seen', sa.Integer(), nullable=False),
    sa.Column('rows_expected', sa.Integer(), nullable=True),
    sa.Column('bidders_read', sa.Integer(), nullable=False),
    sa.Column('no_phone', sa.Integer(), nullable=False),
    sa.Column('repeats', sa.Integer(), nullable=False),
    sa.Column('phone_conflicts', sa.Integer(), nullable=False),
    sa.Column('opted_out', sa.Integer(), nullable=False),
    sa.Column('screened_out', sa.Integer(), nullable=False),
    sa.Column('invalid', sa.Integer(), nullable=False),
    sa.Column('contacts_created', sa.Integer(), nullable=False),
    sa.Column('contacts_updated', sa.Integer(), nullable=False),
    sa.Column('profiles_written', sa.Integer(), nullable=False),
    sa.Column('profile_conflicts', sa.Integer(), nullable=False),
    sa.Column('cleanup_ran', sa.Integer(), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('bidder_scrape_runs', schema=None) as batch_op:
        batch_op.create_index('idx_bidder_runs_started', ['started_at'], unique=False)
        batch_op.create_index('idx_bidder_runs_status', ['status'], unique=False)
        batch_op.create_index(batch_op.f('ix_bidder_scrape_runs_id'), ['id'], unique=False)

    op.create_table('bidder_profiles',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('contact_id', sa.Integer(), nullable=False),
    sa.Column('platform', sa.String(length=50), nullable=False),
    sa.Column('platform_username', sa.String(length=100), nullable=True),
    sa.Column('address', sa.String(length=255), nullable=True),
    sa.Column('location', sa.String(length=255), nullable=True),
    sa.Column('member_since', sa.Date(), nullable=True),
    sa.Column('card_on_file', sa.Boolean(), nullable=True),
    sa.Column('tax_exempt', sa.Boolean(), nullable=True),
    sa.Column('auctions_attended', sa.Integer(), nullable=True),
    sa.Column('bids_placed', sa.Integer(), nullable=True),
    sa.Column('items_won', sa.Integer(), nullable=True),
    sa.Column('payment_rate_pct', sa.Float(), nullable=True),
    sa.Column('avg_hammer_cents', sa.Integer(), nullable=True),
    sa.Column('avg_hammer_is_ceiling', sa.Boolean(), nullable=True),
    sa.Column('disputes_open', sa.Integer(), nullable=True),
    sa.Column('disputes_closed', sa.Integer(), nullable=True),
    sa.Column('first_seen_at', sa.String(length=50), nullable=False),
    sa.Column('scraped_at', sa.String(length=50), nullable=False),
    sa.ForeignKeyConstraint(['contact_id'], ['contacts.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('contact_id', 'platform', name='uq_bidder_profile_contact_platform')
    )
    with op.batch_alter_table('bidder_profiles', schema=None) as batch_op:
        batch_op.create_index('idx_bidder_profiles_avg_hammer', ['avg_hammer_cents'], unique=False)
        batch_op.create_index('idx_bidder_profiles_items_won', ['items_won'], unique=False)
        batch_op.create_index(batch_op.f('ix_bidder_profiles_id'), ['id'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('bidder_profiles', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_bidder_profiles_id'))
        batch_op.drop_index('idx_bidder_profiles_items_won')
        batch_op.drop_index('idx_bidder_profiles_avg_hammer')

    op.drop_table('bidder_profiles')
    with op.batch_alter_table('bidder_scrape_runs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_bidder_scrape_runs_id'))
        batch_op.drop_index('idx_bidder_runs_status')
        batch_op.drop_index('idx_bidder_runs_started')

    op.drop_table('bidder_scrape_runs')
