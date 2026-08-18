"""One outbound message.

Status lifecycle — the distinction between the first three matters for billing:

  pending      queued, not yet handed to the carrier
  sent         carrier ACCEPTED it (HTTP 200). NOT the same as delivered.
  delivered    carrier confirmed handset delivery (via webhook)
  undelivered  carrier accepted then dropped it — spam filters land here
  failed       carrier rejected the send outright
  blocked      on our blocklist, never attempted
  skipped      filtered before send (wrong region) — non-billable, not a failure

Bill on ('sent', 'delivered'). Counting only 'sent' silently drops every campaign
the moment its delivery webhooks land — that bug made a live client's usage meter
appear to freeze for days.
"""

from sqlalchemy import Column, Integer, String, Text, ForeignKey, Index
from app.core.database import Base

MESSAGE_STATUSES = (
    "pending", "sent", "delivered", "undelivered", "failed", "blocked", "skipped",
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

    sent_at = Column(String(50), nullable=True)
    delivered_at = Column(String(50), nullable=True)

    __table_args__ = (
        Index("idx_sms_campaign", "campaign_id"),
        Index("idx_sms_status", "status"),
        Index("idx_sms_sent_at", "sent_at"),
    )
