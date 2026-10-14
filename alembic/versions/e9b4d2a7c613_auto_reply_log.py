"""auto_reply_log: every decision about replying to an inbound text

Revision ID: e9b4d2a7c613
Revises: c1e8f3a6b29d
Create Date: 2026-10-14

One new table and nothing else — session I1, after our number looped with two
auto-responders for three days. The reply guard
(`app/services/auto_reply_guard.py`) both writes it and reads it: the
per-number cap is a lookup on `(phone, created_at)` and the hourly circuit
breaker a range on `created_at`, so both are indexed. No existing table, column
or index is touched — in particular nothing on `contacts` or `sms_messages`.

Trivially reversible: `downgrade()` drops a table nothing references. No server
default on `created_at`: the guard is its one writer, on the application clock.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e9b4d2a7c613'
down_revision: Union[str, None] = 'c1e8f3a6b29d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('auto_reply_log',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('phone', sa.String(length=32), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('decision', sa.String(length=16), nullable=False),
    sa.Column('reason', sa.String(length=200), nullable=True),
    sa.Column('inbound_excerpt', sa.String(length=160), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('auto_reply_log', schema=None) as batch_op:
        batch_op.create_index('idx_auto_reply_created', ['created_at'], unique=False)
        batch_op.create_index('idx_auto_reply_phone_created', ['phone', 'created_at'],
                              unique=False)


def downgrade() -> None:
    with op.batch_alter_table('auto_reply_log', schema=None) as batch_op:
        batch_op.drop_index('idx_auto_reply_phone_created')
        batch_op.drop_index('idx_auto_reply_created')
    op.drop_table('auto_reply_log')
