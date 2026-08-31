"""One outbound message.

Status lifecycle — the distinction between the first three matters for billing:

  pending      queued, not yet handed to the carrier
  sent         carrier ACCEPTED it (HTTP 200). NOT the same as delivered.
  delivered    carrier confirmed handset delivery (via webhook)
  undelivered  carrier accepted then dropped it — spam filters land here
  failed       carrier rejected the send outright
  blocked      on our blocklist, never attempted
  skipped      filtered before send (wrong region) — non-billable, not a failure
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
"""

from sqlalchemy import Column, Integer, String, Text, ForeignKey, Index
from app.core.database import Base

MESSAGE_STATUSES = (
    "pending", "sent", "delivered", "undelivered", "failed", "blocked", "skipped",
    "not_sent",
)
BILLABLE_STATUSES = ("sent", "delivered")


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
    top_up_at = Column(String(50), nullable=True)

    __table_args__ = (
        Index("idx_sms_campaign", "campaign_id"),
        Index("idx_sms_status", "status"),
        Index("idx_sms_sent_at", "sent_at"),
    )
