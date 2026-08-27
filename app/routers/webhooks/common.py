"""Shared webhook logic, provider-independent.

Both inbound handling and delivery-status recording live here so a second
provider is a thin translation layer, not a copy of the business rules.
"""

from sqlalchemy.orm import Session
from app.models.sms_message import SMSMessage
from app.models.campaign import Campaign
from app.models.app_setting import get_setting, AUTO_REPLY_KEY
from app.services.blocklist_service import block_number, unblock_number
from app.services.monitoring_service import record_config_alert
from app.sms import compliance
from app.sms.compliance import classify_failure
from app.sms.phone import scrub_provider_text
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


def record_delivery_status(db: Session, message_id: str, status: str,
                           error_detail: str = None, source: str = "webhook",
                           error_code: str = None):
    """Persist a carrier's final delivery outcome.

    A message is marked 'sent' the instant the provider accepts it (HTTP 200),
    long before any carrier decides whether to deliver it. That decision arrives
    here, asynchronously. Without recording it, carrier spam-blocks stay
    invisible and the 'sent' count on the dashboard is simply untrue — a client
    sees 5,000 sent while the messages were dropped on the way to the handset.

    Idempotent: only the first terminal event per message moves counters.
    Carriers retry webhooks, sometimes for days.

    `source` is recorded on any auto-block this raises and is the provider that
    reported the failure. The client never sees it — routers/blocklist.py maps
    anything but "manual" to "Automatic" — but a two-carrier box needs to know
    which one condemned a number.

    `error_code` is the carrier's structured code, kept apart from
    `error_detail`. Every numeric auto-block rule reads this and none reads the
    prose: the prose quotes the destination number, and "21610" as a substring
    test fires on +1 321-610-xxxx.
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
        msg.error_code = error_code or None
        db.commit()
        logger.info(f"Message {message_id} undelivered: {msg.error_message}")

        # A number the carrier calls unreachable is unreachable on every future
        # campaign too. `should_auto_block()` existed for exactly this and was
        # wired to exactly one call site — campaign_service's *submission* path,
        # where the provider rejects a send outright. The larger share of dead
        # numbers never goes down that path: they are accepted at submission
        # (HTTP 200) and fail here, minutes later. One live campaign: 6,857
        # recipients, 2,673 undelivered, of which 2,526 were "not routable:
        # either a landline or a non-routable wireless number". Two failure
        # paths, one guard, and the guard was on the smaller one — so the same
        # dead numbers were re-sent to, and re-paid for, on every campaign.
        #
        # Inside the status guard above on purpose: the branch only runs on the
        # first terminal event for a message, so a carrier retrying this webhook
        # for three days blocks the number once. block_number() also refuses a
        # duplicate, which is the second layer rather than the first.
        #
        # `delivered_at is None` is the third condition and the one that matters
        # most. The guard above admits `msg.status == "delivered"`, so the
        # sequence delivered-then-failed — a carrier retry, a duplicate, or a
        # race between two of its workers — reaches here for a message that
        # provably arrived on a handset. Before this session that cost a wrong
        # counter; with an auto-block on the same path it would permanently
        # delete a buyer who received the text. A handset receipt is not
        # revocable by a later failure event.
        #
        # Session 5g replaced the boolean with a verdict. The same failure now
        # answers three questions rather than one — block or not, under which
        # reason, and whether the thing that failed was our own account setting
        # rather than the recipient — because on this path all three arrive
        # together and answering only the first is what filed carrier opt-outs
        # as unreachable numbers and blocked buyers for our region permissions.
        verdict = classify_failure(msg.error_message, msg.error_code)

        # Raised whether or not the number is blocked, and never over SMS: this
        # is a fault on the sending account, and 5d ruled that an alert must not
        # travel over the thing it is warning about. /health reports it.
        if verdict.alert_key:
            record_config_alert(db, verdict.alert_key, verdict.alert_detail)

        if msg.delivered_at is None and verdict.block:
            block_number(
                db, msg.phone,
                reason=verdict.reason,
                source=source,
                notes=f"Auto-blocked: {scrub_provider_text(msg.error_message)[:200]}",
            )
