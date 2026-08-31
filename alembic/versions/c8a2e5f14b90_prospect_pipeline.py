"""prospect pipeline — the holding pen, the line-type cache and the job record

Revision ID: c8a2e5f14b90
Revises: d3b6f04c9a12
Create Date: 2026-08-31

Additive only. Five new tables, no column added to an existing one, no index on
`contacts` or `sms_messages` touched, and nothing backfilled — there is nothing
to backfill, because no prospect has ever existed.

`ix_contacts_phone` is deliberately untouched. Prospects carry their own unique
index on `prospects.phone` and become contacts only through
`prospect_service.promote()`, which goes through `contact_service.upsert_contact()`
like every other ingestion path. Nothing here can insert a duplicate contact,
because nothing here inserts a contact at all.

## Why five tables and not the three the session named

`prospects`, `scrape_jobs` and `phone_lookups` are the three. The other two
exist because the alternative to each is an overloaded column, which is the
mistake this project's lessons file opens with.

**`prospect_sightings`.** Scoring ranks a prospect partly on multi-source
corroboration — a business two different searches found is more likely real. The
column version of that is a counter plus a JSON list of terms on `prospects`,
which is a domain concept living as a string. A row per (prospect, source,
term), uniquely constrained, makes the count a `COUNT(DISTINCT)` and makes
re-running the same search idempotent instead of score-inflating.

**`prospect_rejections`.** "Every rejection suppresses permanently, and
re-ingesting the same record from any source must not resurface it" is a claim
about a *number*, not about a row somebody might later tidy up. It is the same
separation `blocked_numbers` has from `contacts.is_active`, for the same reason:
the record has to outlive whatever carried it.

## Order matters here

`prospect_sightings.job_id` points at `scrape_jobs`, and both `prospects`
columns and `prospect_rejections.prospect_id` point at `prospects`, so
`scrape_jobs` and `prospects` are created first and the downgrade drops in the
reverse order. `prospects.promoted_contact_id` and `prospects.category_id` point
at tables that already exist.

`downgrade()` drops all five. That loses the review queue, the rejection record
and the line-type cache, which is the honest consequence of removing the feature
— and the cache in particular is money: rebuilding it is $0.0025 a number.
"""

from alembic import op
import sqlalchemy as sa

revision = 'c8a2e5f14b90'
down_revision = 'd3b6f04c9a12'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'scrape_jobs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('source', sa.String(length=50), nullable=False),
        sa.Column('search_term', sa.String(length=255), nullable=True),
        sa.Column('parameters', sa.JSON(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='running'),
        sa.Column('started_at', sa.String(length=50), nullable=False),
        sa.Column('finished_at', sa.String(length=50), nullable=True),
        sa.Column('records_yielded', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('prospects_created', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('prospects_corroborated', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('records_suppressed', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('records_invalid', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('lookups_performed', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('lookups_cached', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('cost', sa.String(length=20), nullable=True),
        sa.Column('cleanup_ran', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('error', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_scrape_jobs_id', 'scrape_jobs', ['id'])
    op.create_index('idx_scrape_jobs_started', 'scrape_jobs', ['started_at'])
    op.create_index('idx_scrape_jobs_status', 'scrape_jobs', ['status'])

    op.create_table(
        'prospects',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('phone', sa.String(length=20), nullable=False),
        sa.Column('business_name', sa.String(length=255), nullable=True),
        sa.Column('address', sa.String(length=255), nullable=True),
        sa.Column('category_id', sa.Integer(), nullable=True),
        sa.Column('category_confidence', sa.Float(), nullable=True),
        sa.Column('distance_miles', sa.Float(), nullable=True),
        # Provenance. Non-nullable so a prospect cannot exist without a trail
        # back to the search that produced it.
        sa.Column('source', sa.String(length=50), nullable=False),
        sa.Column('source_url', sa.Text(), nullable=False),
        sa.Column('scraped_at', sa.String(length=50), nullable=False),
        sa.Column('raw_payload', sa.JSON(), nullable=False),
        sa.Column('search_term', sa.String(length=255), nullable=False),
        sa.Column('buyer_rationale', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='pending'),
        sa.Column('score', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('source_count', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('promoted_contact_id', sa.Integer(), nullable=True),
        sa.Column('promoted_at', sa.String(length=50), nullable=True),
        sa.Column('rejected_at', sa.String(length=50), nullable=True),
        sa.Column('created_at', sa.String(length=50), nullable=False),
        sa.Column('updated_at', sa.String(length=50), nullable=True),
        sa.ForeignKeyConstraint(['category_id'], ['categories.id']),
        sa.ForeignKeyConstraint(['promoted_contact_id'], ['contacts.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_prospects_id', 'prospects', ['id'])
    # Unique: the number is the identity, exactly as it is for a contact. This
    # is what stops one business becoming five prospects from five searches.
    op.create_index('ix_prospects_phone', 'prospects', ['phone'], unique=True)
    op.create_index('idx_prospects_status_score', 'prospects', ['status', 'score'])
    op.create_index('idx_prospects_category', 'prospects', ['category_id'])
    op.create_index('idx_prospects_search_term', 'prospects', ['search_term'])

    op.create_table(
        'prospect_sightings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('prospect_id', sa.Integer(), nullable=False),
        sa.Column('source', sa.String(length=50), nullable=False),
        sa.Column('search_term', sa.String(length=255), nullable=False),
        sa.Column('buyer_rationale', sa.Text(), nullable=False),
        sa.Column('source_url', sa.Text(), nullable=False),
        sa.Column('scraped_at', sa.String(length=50), nullable=False),
        sa.Column('raw_payload', sa.JSON(), nullable=True),
        sa.Column('job_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['prospect_id'], ['prospects.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['job_id'], ['scrape_jobs.id']),
        sa.PrimaryKeyConstraint('id'),
        # The dedup guarantee for corroboration — see the model.
        sa.UniqueConstraint('prospect_id', 'source', 'search_term',
                            name='uq_prospect_sighting'),
    )
    op.create_index('ix_prospect_sightings_id', 'prospect_sightings', ['id'])
    op.create_index('idx_prospect_sightings_prospect', 'prospect_sightings', ['prospect_id'])

    op.create_table(
        'prospect_rejections',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('phone', sa.String(length=20), nullable=False),
        sa.Column('prospect_id', sa.Integer(), nullable=False),
        sa.Column('reason', sa.String(length=40), nullable=False),
        sa.Column('rejected_at', sa.String(length=50), nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['prospect_id'], ['prospects.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_prospect_rejections_id', 'prospect_rejections', ['id'])
    # Unique on the number: this is the suppression key, and a second row for
    # the same phone would mean the second one could be missed.
    op.create_index('ix_prospect_rejections_phone', 'prospect_rejections', ['phone'],
                    unique=True)
    op.create_index('idx_prospect_rejections_reason', 'prospect_rejections', ['reason'])

    op.create_table(
        'phone_lookups',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('phone', sa.String(length=20), nullable=False),
        sa.Column('line_type', sa.String(length=20), nullable=False),
        sa.Column('status', sa.String(length=10), nullable=False),
        sa.Column('provider', sa.String(length=50), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('looked_up_at', sa.String(length=50), nullable=False),
        sa.Column('cost', sa.String(length=20), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_phone_lookups_id', 'phone_lookups', ['id'])
    # Unique: this constraint IS the "never look a number up twice" guarantee.
    op.create_index('ix_phone_lookups_phone', 'phone_lookups', ['phone'], unique=True)
    op.create_index('idx_phone_lookups_line_type', 'phone_lookups', ['line_type'])


def downgrade() -> None:
    op.drop_table('phone_lookups')
    op.drop_table('prospect_rejections')
    op.drop_table('prospect_sightings')
    op.drop_table('prospects')
    op.drop_table('scrape_jobs')
