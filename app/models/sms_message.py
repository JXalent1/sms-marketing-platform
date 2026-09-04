"""One outbound message.

Status lifecycle — the distinction between the first three matters for billing:

  pending      queued, not yet handed to the carrier
  sent         carrier ACCEPTED it (HTTP 200). NOT the same as delivered.
  delivered    carrier confirmed handset delivery (via webhook)
  undelivered  carrier accepted then dropped it — spam filters land here
  failed       carrier rejected the send outright
  blocked      on our blocklist, never attempted
  skipped      filtered before send (wrong region) — non-billable, not a failure,
               and PERMANENT: nothing clears it
  held_back    inside the recent-contact suppression window when the draft was
               built — non-billable, not a failure, and TEMPORARY: the hold
               expires, and a top-up re-adjudicates the row against today's
               window and flips it to 'pending'
  not_sent     the send path was degraded — the message never reached a carrier

Bill on ('sent', 'delivered'). Counting only 'sent' silently drops every campaign
the moment its delivery webhooks land — that bug made a live client's usage meter
appear to freeze for days.

'not_sent' is the backstop under the pre-flight refusal in campaign_service: a
box whose carrier failed to start falls back to the console provider, which
reports every send successful, so before session 5d those rows were written
'sent' and invoiced at $0.015 for messages nobody received. Keeping the status
out of BILLABLE_STATUSES is not a pricing concession — a segment that never
reached a carrier is not a segment. See decisions/002-degraded-box-still-bills.md.

'held_back' is session 5h, and it is the same shape one level along. Until it
existed, `campaign_builder` wrote a suppressed contact as 'skipped' — the status
whose contract, three lines up, has always said "wrong region". So one column
carried a hold that clears with time and an exclusion that never does, nothing
downstream could tell them apart, and a buyer the window merely deferred was
unreachable inside that campaign for good. That is the overloaded-column mistake
CLAUDE.md opens with, caught with two consumers rather than five.
See decisions/005-topping-up-a-contact-the-window-held-back.md.

**Rows written 'skipped' before 5h are not re-adjudicated and never will be.**
They may mean either thing and cannot be classified after the fact; guessing
would turn one defect into an unauditable set of them. The distinction begins
with campaigns built after the change — see
alembic/versions/e2a7c3d15b48_held_back_message_status.py.
"""

from sqlalchemy import Column, Integer, String, Text, ForeignKey, Index
from app.core.database import Base

MESSAGE_STATUSES = (
    "pending", "sent", "delivered", "undelivered", "failed", "blocked", "skipped",
    "held_back", "not_sent",
)
BILLABLE_STATUSES = ("sent", "delivered")

# What counts as "this reached a handset" on every screen that reports freshness
# or delivery: the dashboard's days-since-last-send, the campaign report's
# delivered figure, and the per-list recency the composer's picker is sorted by.
#
# Deliberately NOT `BILLABLE_STATUSES`, even though the two sets are identical
# today. "Did this reach a handset?" and "do we invoice for this?" are separate
# questions that happen to share an answer; binding them together means a
# commercial change to the billable set would silently rewrite the freshness
# figures the client schedules his auctions against.
#
# It lives here, beside the set it must not become, because it was defined
# independently in `dashboard_service` and again in `report_service` — and
# session 5i would have made it three. Two definitions of one rule is how two
# screens come to disagree about what "texted" means. Do not assert its contents
# in a test; assert that the screens reading it agree.
SENT_STATUSES = ("sent", "delivered")

# The one spelling of the held-back status. Two services write it and a third
# reads it back to release it; a literal in each is how the top-up ends up
# looking for rows nothing writes. Deliberately outside BILLABLE_STATUSES —
# a message that was never handed to a carrier is not a segment.
HELD_BACK_STATUS = "held_back"


class SMSMessage(Base):
    __tablename__ = "sms_messages"

    id = Column(Integer, primary_key=True, index=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id"), nullable=True)
    contact_id = Column(Integer, ForeignKey("contacts.id"), nullable=True)

    phone = Column(String(20), nullable=False)
    message = Column(Text, nullable=False)
    status = Column(String(20), default="pending")

    # Carrier's own segment count, captured at send time. This is the billing
    # basis; falling back to len//160 undercounts every emoji message by ~2.4x.
    segments = Column(Integer, nullable=True)

    external_id = Column(String(255), nullable=True, index=True)   # provider message ID
    error_message = Column(Text, nullable=True)

    # The carrier's own error code, kept apart from the prose in error_message.
    # Auto-block rules match codes against THIS column and never against the
    # message: error prose routinely quotes the destination number, and a plain
    # substring test for "21610" fires on +1 321-610-xxxx — an assignable
    # Brevard County number in this client's market. See app/sms/compliance.py
    # and decisions/003-auto-block-fragments-on-the-webhook-path.md.
    error_code = Column(String(20), nullable=True)

    sent_at = Column(String(50), nullable=True)
    delivered_at = Column(String(50), nullable=True)

    # When this row was added to a campaign that had already sent. NULL is the
    # original send — the honest value, not "unknown".
    #
    # A top-up folds into the campaign's totals by design (modules.md, 2026-08-24),
    # so without this the campaign's recipient count simply changes and a report
    # three weeks later cannot say why. One `GROUP BY top_up_at` turns that back
    # into "1,200 + 5 added 26 Aug". See app/services/campaign_topup.py.
    #
    # A 'held_back' row released by a top-up is stamped too, even though it was
    # written at the original send. The column funds one report — how the
    # campaign's recipient count reached the number on screen — and a released
    # row joins that count on the day it is released, not on the day it was
    # queued. Leaving it NULL would show "1,200 + 0 added" over a total that grew
    # by two. The row's own history is intact either way: it is one row, it
    # carries the body it was rendered with, and it was never sent before.
    top_up_at = Column(String(50), nullable=True)

    # ─── What the carrier actually charged ──────────────────────────────────
    #
    # `campaigns.estimated_cost` has carried a comment since the skeleton saying
    # it exists to "reconcile against the invoice afterwards", and until 5f
    # there was nothing to reconcile it against: the provider returns a cost and
    # a rate/carrier-fee breakdown on every message and both were dropped on the
    # floor. Per-carrier pass-through differs, so the true blended figure is
    # per-campaign rather than a constant, and `WHOLESALE_COST_PER_SEGMENT` at
    # 0.009 is a deliberate over-reserve for the capacity guard rather than a
    # measurement of anything.
    #
    # **These are OUR cost, and they are subject to the same rule as
    # `WHOLESALE_COST_PER_SEGMENT`: they must never reach a response body, a
    # template or an export.** The client's money comes from `billing_service`
    # at `BILLING_PRICE_PER_SEGMENT`. Reconciliation is an operator job and
    # lives in `app/services/cost_reconciliation.py` and `scripts/cost_report.py`.
    #
    # Stored as strings, exactly as the carrier reported them, and summed in
    # Decimal. A Float column would put a binary expansion between the carrier's
    # figure and ours, which is the defect session 1b fixed one layer up: money
    # is Decimal end to end, not only at the rounding step.
    carrier_cost = Column(String(24), nullable=True)          # total, e.g. "0.0045"
    carrier_cost_rate = Column(String(24), nullable=True)     # the carrier's rate
    carrier_cost_fee = Column(String(24), nullable=True)      # per-carrier pass-through
    carrier_cost_currency = Column(String(8), nullable=True)  # "USD"

    __table_args__ = (
        Index("idx_sms_campaign", "campaign_id"),
        Index("idx_sms_status", "status"),
        Index("idx_sms_sent_at", "sent_at"),
        # The freshness join's key. `contact_service._last_sent_by_list()` joins
        # this table to `contact_list_members` on `contact_id`, and until
        # session 5j neither side was indexed on it: 30,000 messages against
        # 15,500 memberships took **10 minutes 2 seconds** on production and
        # under a millisecond on the suite's twelve rows.
        #
        # Declared here as well as in migration `a3f1e08c5d47` because the
        # models are the schema's other half — `test_migrations_match_the_models`
        # compares the two, and an index that exists in only one of them is a
        # drift that surfaces the next time somebody autogenerates a revision.
        Index("idx_sms_contact", "contact_id"),
    )
