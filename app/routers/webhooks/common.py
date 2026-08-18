"""Shared webhook logic, provider-independent.

Both inbound handling and delivery-status recording live here so a second
provider is a thin translation layer, not a copy of the business rules.
"""

from sqlalchemy.orm import Session
from app.models.sms_message import SMSMessage
from app.models.campaign import Campaign
from app.models.app_setting import get_setting, AUTO_REPLY_KEY
from app.services.blocklist_service import block_number, unblock_number
from app.sms import compliance
from datetime import datetime
import logging

logger = logging.getLogger("webhooks")


def handle_inbound(db: Session, from_number: str, body: str) -> str:
    """Process an inbound message and return the reply to send.

    STOP is persisted to our own blocklist, not just acknowledged. The carrier
    also keeps its own opt-out list, but that list does not come with you when
    you switch carriers — and the day you migrate, you would re-text every
    person who ever opted out.
    """
    kind = compliance.classify(body)
    logger.info(f"Inbound from {from_number}: {kind} | {body[:60]!r}")

    if kind == "stop":
        block_number(db, from_number, reason="stop_keyword", source="webhook",
                     notes=f"Keyword: {(body or '').strip().upper()[:40]}")
        return compliance.stop_confirmation()

    if kind == "start":
        unblock_number(db, from_number)
        return compliance.start_confirmation()

    if kind == "help":
        return compliance.help_reply()

    return get_setting(db, AUTO_REPLY_KEY) or compliance.default_auto_reply()


def record_delivery_status(db: Session, message_id: str, status: str, error_detail: str = None):
    """Persist a carrier's final delivery outcome.

    A message is marked 'sent' the instant the provider accepts it (HTTP 200),
    long before any carrier decides whether to deliver it. That decision arrives
    here, asynchronously. Without recording it, carrier spam-blocks stay
    invisible and the 'sent' count on the dashboard is simply untrue — a client
    sees 5,000 sent while the messages were dropped on the way to the handset.

    Idempotent: only the first terminal event per message moves counters.
    Carriers retry webhooks, sometimes for days.
    """
    if not message_id:
        return

    msg = db.query(SMSMessage).filter(SMSMessage.external_id == message_id).first()
    if not msg:
        return                      # test sends and pre-tracking messages have no row

    status = (status or "").lower()
    delivered = status == "delivered"
    failed = "fail" in status or status in ("undelivered", "rejected", "expired")

    if delivered:
        if msg.delivered_at is None:
            msg.delivered_at = datetime.now().isoformat()
            if msg.status in ("sent", "pending"):
                msg.status = "delivered"
            db.commit()
        return

    if failed and msg.status in ("sent", "pending", "delivered"):
        if msg.campaign_id:
            campaign = db.get(Campaign, msg.campaign_id)
            if campaign:
                # It was counted as sent; the carrier says otherwise.
                if msg.status in ("sent", "delivered") and (campaign.sent_count or 0) > 0:
                    campaign.sent_count -= 1
                campaign.failed_count = (campaign.failed_count or 0) + 1

        msg.status = "undelivered"
        msg.error_message = error_detail or "Carrier did not deliver the message"
        db.commit()
        logger.info(f"Message {message_id} undelivered: {msg.error_message}")
