"""short links, clicks, and the carrier cost we never captured

Revision ID: d3b6f04c9a12
Revises: a91d5f2c6b70
Create Date: 2026-08-31

Three additive changes, none of them touching an existing index or column.

## `short_links` and `link_clicks`

New tables. One link per recipient per campaign — see
`app/models/short_link.py` for why per-recipient is the whole point — and one
row per arrival, including the arrivals we believe are scanners rather than
people. Nothing is backfilled and nothing could be: a campaign that has already
gone out carries no links, and a report on it correctly shows no click data
rather than a zero that looks like nobody clicked.

`short_links.slug` is uniquely indexed. That constraint is the mint's collision
guarantee, not a nicety — `link_service._fresh_slugs()` checks a batch against
the table before inserting, and this is what makes a race between two campaigns
being created at once fail loudly rather than hand two recipients the same link.

## `campaigns.link_target_url`

Where a campaign's `{link}` tag points. Nullable and un-backfilled: a campaign
built before this revision has no link, and NULL is the honest value.

## The four cost columns on `sms_messages`

`carrier_cost`, `carrier_cost_rate`, `carrier_cost_fee`,
`carrier_cost_currency`. The provider has always returned a cost and a
rate/carrier-fee split on every message and the provider class discarded both,
so `campaigns.estimated_cost` — whose own comment says it exists "to reconcile
against the invoice afterwards" — has never had anything to reconcile against.

Strings, holding what the carrier reported, summed in Decimal. A Float column
would insert a binary expansion between the carrier's figure and ours, which is
the defect session 1b fixed one layer up.

**They are our cost, not the client's.** Same rule as
`WHOLESALE_COST_PER_SEGMENT`: they must not reach a response body, a template or
an export. `tests/test_whitelabel.py` runs the routes and asserts it.

`downgrade()` reverses all three, dropping the two tables last so the foreign
keys go before what they point at. It loses click history, which is the honest
consequence of removing the feature rather than something to work around.
"""

from alembic import op
import sqlalchemy as sa

revision = 'd3b6f04c9a12'
down_revision = 'a91d5f2c6b70'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'short_links',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('slug', sa.String(length=32), nullable=False),
        sa.Column('campaign_id', sa.Integer(), nullable=True),
        sa.Column('contact_id', sa.Integer(), nullable=True),
        sa.Column('message_id', sa.Integer(), nullable=True),
        sa.Column('target_url', sa.Text(), nullable=False),
        sa.Column('created_at', sa.String(length=50), nullable=True),
        sa.Column('click_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('bot_click_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('first_clicked_at', sa.String(length=50), nullable=True),
        sa.Column('last_clicked_at', sa.String(length=50), nullable=True),
        sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id']),
        sa.ForeignKeyConstraint(['contact_id'], ['contacts.id']),
        sa.ForeignKeyConstraint(['message_id'], ['sms_messages.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_short_links_id', 'short_links', ['id'])
    # Unique: this is the mint's collision guarantee. See the docstring.
    op.create_index('ix_short_links_slug', 'short_links', ['slug'], unique=True)
    op.create_index('idx_short_links_campaign', 'short_links', ['campaign_id'])
    op.create_index('idx_short_links_contact', 'short_links', ['contact_id'])
    op.create_index('idx_short_links_message', 'short_links', ['message_id'])

    op.create_table(
        'link_clicks',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('short_link_id', sa.Integer(), nullable=False),
        sa.Column('clicked_at', sa.String(length=50), nullable=False),
        sa.Column('user_agent', sa.Text(), nullable=True),
        sa.Column('seconds_after_send', sa.Integer(), nullable=True),
        sa.Column('is_bot', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('bot_reason', sa.String(length=40), nullable=True),
        sa.ForeignKeyConstraint(['short_link_id'], ['short_links.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_link_clicks_id', 'link_clicks', ['id'])
    op.create_index('ix_link_clicks_short_link_id', 'link_clicks', ['short_link_id'])
    op.create_index('idx_link_clicks_clicked_at', 'link_clicks', ['clicked_at'])

    # Plain ADD COLUMN on both tables, never batch_alter_table. The batch form
    # rebuilds the whole table and every index on it — 5g's review caught that
    # happening to `sms_messages` under a docstring claiming it touched no
    # index, and `ix_contacts_phone`-class constraints are exactly what a
    # silent rebuild puts at risk.
    op.add_column('campaigns', sa.Column('link_target_url', sa.Text(), nullable=True))

    op.add_column('sms_messages',
                  sa.Column('carrier_cost', sa.String(length=24), nullable=True))
    op.add_column('sms_messages',
                  sa.Column('carrier_cost_rate', sa.String(length=24), nullable=True))
    op.add_column('sms_messages',
                  sa.Column('carrier_cost_fee', sa.String(length=24), nullable=True))
    op.add_column('sms_messages',
                  sa.Column('carrier_cost_currency', sa.String(length=8), nullable=True))


def downgrade() -> None:
    op.drop_column('sms_messages', 'carrier_cost_currency')
    op.drop_column('sms_messages', 'carrier_cost_fee')
    op.drop_column('sms_messages', 'carrier_cost_rate')
    op.drop_column('sms_messages', 'carrier_cost')
    op.drop_column('campaigns', 'link_target_url')

    op.drop_index('idx_link_clicks_clicked_at', table_name='link_clicks')
    op.drop_index('ix_link_clicks_short_link_id', table_name='link_clicks')
    op.drop_index('ix_link_clicks_id', table_name='link_clicks')
    op.drop_table('link_clicks')

    op.drop_index('idx_short_links_message', table_name='short_links')
    op.drop_index('idx_short_links_contact', table_name='short_links')
    op.drop_index('idx_short_links_campaign', table_name='short_links')
    op.drop_index('ix_short_links_slug', table_name='short_links')
    op.drop_index('ix_short_links_id', table_name='short_links')
    op.drop_table('short_links')
