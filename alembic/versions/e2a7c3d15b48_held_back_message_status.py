"""held_back message status — and the backfill that must never be written

Revision ID: e2a7c3d15b48
Revises: c4f1a80b6e37
Create Date: 2026-08-30

**This revision changes no schema, and it is deliberately empty.**
`sms_messages.status` is a plain VARCHAR with no CHECK constraint and no enum,
so session 5h's new `held_back` status needs no DDL. The revision exists for one
reason: to put the decision *not* to backfill in the place a future reader looks
for it. A reader who discovers that half this table's `skipped` rows mean "held
back by the suppression window" will go looking for the migration that sorted
them out, and finding nothing is indistinguishable from finding an oversight.

## What changed above this file

Before 5h, `campaign_builder.create_campaign()` wrote a contact held back by the
recent-contact suppression window as `status="skipped"` — the status
`app/models/sms_message.py` has always documented as *"filtered before send
(wrong region)"*. One column, two meanings: one that clears on its own and one
that never does. Nothing downstream could tell them apart, so a buyer the window
merely deferred could not be reached inside that campaign after the hold
expired, and the client's only remedy was to rebuild the campaign and lose the
first send's numbers. Campaigns built from now on write `held_back` instead, and
a top-up re-adjudicates those rows against today's window and flips them to
`pending`. See `decisions/005-topping-up-a-contact-the-window-held-back.md`.

## Why there is no backfill, and why there must not be one

An existing `skipped` row may be either meaning and **cannot be classified after
the fact**. The two writers left no discriminator: same status, same campaign,
same absence of `sent_at`. `error_message` is suggestive on the suppression side
and absent on plenty of rows, and a region skip on a campaign that also held
people back is indistinguishable from the hold once the window has moved.

Guessing would not be a tidy-up. Every row guessed *held back* becomes a text a
top-up may send to somebody the region filter excluded — a message to a number
this system decided was undeliverable, paid for, and possibly to a country the
account is not enabled for. Every row guessed *skipped* stays exactly as broken
as it is today. One defect would become an unauditable set of them, which is the
same argument session 5g made for not unblocking anything on the blocklist: a
rule change governs future events, and re-adjudicating history on an inference
is not a fix.

So this migration does nothing on purpose. Decision 005 rider 2 rules it, and
the ruling is binding: **the change affects campaigns built after it lands.**
If you are reading this because you were about to write the backfill — that is
the thing being asked for, and it is the thing not to do.

Nothing here touches `ix_contacts_phone`, any index on `sms_messages`, or any
column. `downgrade()` is empty for the same reason `upgrade()` is: there is
nothing to undo, and a downgrade that "restored" `held_back` rows to `skipped`
would destroy the one distinction this session exists to create.
"""

from alembic import op  # noqa: F401  (kept so the file's shape matches its siblings)
import sqlalchemy as sa  # noqa: F401

revision = 'e2a7c3d15b48'
down_revision = 'c4f1a80b6e37'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """No schema change, and no data change. See the module docstring."""
    pass


def downgrade() -> None:
    """No schema change to reverse, and existing rows are never re-adjudicated."""
    pass
