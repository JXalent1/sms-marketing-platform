"""A bidder's behaviour on an auction platform, beside the contact it belongs to.

## Why this is its own table

`contacts` is who you can text. This is what somebody has *done* at his sales —
auctions attended, items won, average hammer, card on file, disputes. The
reference system put all of it on its `Bidder` row, which made every query
auction-shaped; `app/models/contact.py`'s own docstring records that. Putting it
back on `contacts`, or into `contacts.attributes` as JSON, is the overloaded
column `CLAUDE.md` opens with, and a JSON blob cannot be filtered on.

## Why this is the most valuable thing session L1 lands

These columns make **"everyone who has won at least three items"** and
**"average hammer over $250"** an audience: a comparison on an integer, joined
to a contact by id. That is the segmentation the category model was reaching
for — categories say what a sale *is*; this says how a person *bids*. Building
those audiences is not L1. Landing the data in a shape that allows them is, and
that is why every field is a real column with a real type and never the page's
string: `avg_hammer_cents` is an integer of cents (money in integers, never a
float), `member_since` is a date, `card_on_file` is a boolean.

NULL always means "the platform did not say". A bidder with no analytics block
has not won zero items, and an audience of "won fewer than two" must not
include them by accident — so nothing here defaults a count to 0.

## One row per contact per platform

Unique on `(contact_id, platform)`. The daily read overwrites the figures with
the platform's current ones and stamps `scraped_at`; `first_seen_at` is written
once. Keyed on the contact, whose phone is the identity — the reference
`profile_hash` of phone + auction date was a second identity, and two identity
keys on one path is how a duplicate appears.

A contact with no profile row is an ordinary contact: every CSV-imported bidder
is one. Nothing reads this table on the send path.

## `bidder_scrape_runs`

What a daily read attempted, produced and whether its browser was closed. Its
own table rather than `scrape_jobs`, whose columns are the prospect pipeline's
(`prospects_created`, `records_suppressed`): writing "contacts created" into
`prospects_created` would be two meanings in one column again. `cleanup_ran` is
a column for `scrape_jobs`' reason — a leak you can query for, before it is
seventeen processes.
"""

from sqlalchemy import (Boolean, Column, Date, Float, ForeignKey, Index, Integer,
                        String, Text, UniqueConstraint)
from app.core.database import Base

# `incomplete` is a run that finished without error and read noticeably less
# than the page held — pages it never reached, panels that never opened. Its
# own status because "completed" is what a client-facing summary would trust,
# and a partial run reporting success is the defect `campaign_outcome` exists
# to prevent one level up.
RUN_STATUSES = ("running", "completed", "incomplete", "failed", "timed_out")


class BidderProfile(Base):
    __tablename__ = "bidder_profiles"

    id = Column(Integer, primary_key=True, index=True)
    contact_id = Column(Integer, ForeignKey("contacts.id"), nullable=False)
    platform = Column(String(50), nullable=False)      # app/sources/platforms.py

    platform_username = Column(String(100), nullable=True)
    address = Column(String(255), nullable=True)
    location = Column(String(255), nullable=True)
    member_since = Column(Date, nullable=True)

    card_on_file = Column(Boolean, nullable=True)
    tax_exempt = Column(Boolean, nullable=True)
    auctions_attended = Column(Integer, nullable=True)
    bids_placed = Column(Integer, nullable=True)
    items_won = Column(Integer, nullable=True)
    # A percentage, 0-100 — not money, so a float is honest here.
    payment_rate_pct = Column(Float, nullable=True)
    avg_hammer_cents = Column(Integer, nullable=True)
    # True when the platform printed "Less than $X": the figure is a ceiling.
    avg_hammer_is_ceiling = Column(Boolean, nullable=True)
    disputes_open = Column(Integer, nullable=True)
    disputes_closed = Column(Integer, nullable=True)

    first_seen_at = Column(String(50), nullable=False)
    scraped_at = Column(String(50), nullable=False)

    __table_args__ = (
        UniqueConstraint("contact_id", "platform", name="uq_bidder_profile_contact_platform"),
        # The audience queries this table exists for filter on these.
        Index("idx_bidder_profiles_items_won", "items_won"),
        Index("idx_bidder_profiles_avg_hammer", "avg_hammer_cents"),
    )


class BidderScrapeRun(Base):
    __tablename__ = "bidder_scrape_runs"

    id = Column(Integer, primary_key=True, index=True)
    platform = Column(String(50), nullable=False)
    status = Column(String(20), nullable=False, default="running")
    started_at = Column(String(50), nullable=False)
    finished_at = Column(String(50), nullable=True)

    # What the page held, and what became of each row. These add up:
    # bidders_read = no_phone + invalid + repeats + phone_conflicts + opted_out
    #                + screened_out + contacts_created + contacts_updated
    # `repeats` is one person listed twice (registered for two upcoming sales);
    # `phone_conflicts` is one number read for two different names, which is a
    # misread until proved otherwise, so only the first is kept.
    rows_seen = Column(Integer, nullable=False, default=0)
    # The platform's own "of N" total, when the page states one.
    rows_expected = Column(Integer, nullable=True)
    bidders_read = Column(Integer, nullable=False, default=0)
    no_phone = Column(Integer, nullable=False, default=0)
    repeats = Column(Integer, nullable=False, default=0)
    phone_conflicts = Column(Integer, nullable=False, default=0)
    opted_out = Column(Integer, nullable=False, default=0)
    screened_out = Column(Integer, nullable=False, default=0)
    invalid = Column(Integer, nullable=False, default=0)
    contacts_created = Column(Integer, nullable=False, default=0)
    contacts_updated = Column(Integer, nullable=False, default=0)
    profiles_written = Column(Integer, nullable=False, default=0)
    # A contact whose profile already belongs to a different platform username:
    # one number, two bidders, across runs. Not overwritten — whoever came first
    # keeps the row, so the contact's name and the behaviour beside it stay the
    # same person's. Outside the sum above; the contact itself was still landed.
    profile_conflicts = Column(Integer, nullable=False, default=0)

    cleanup_ran = Column(Integer, nullable=False, default=0)
    # Our diagnostic, not the client's: an exception type and a short reason.
    # There is no screen for this table; if one is built, this column is not on it.
    error = Column(Text, nullable=True)

    __table_args__ = (
        Index("idx_bidder_runs_started", "started_at"),
        Index("idx_bidder_runs_status", "status"),
    )
