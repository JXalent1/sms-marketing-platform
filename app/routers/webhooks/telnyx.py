"""Telnyx webhook.

Point the messaging profile's webhook_url at {PUBLIC_BASE_URL}/webhooks/telnyx.
If it is left null, STOP replies never reach you and opt-outs go unrecorded —
which is both a compliance failure and the reason a blocklist can look
suspiciously empty.

Always return 200, even on error. A non-200 makes Telnyx retry the same event
for hours, and a webhook that throws on one malformed payload will spend the day
being redelivered instead of processing the next event.
"""

from fastapi import APIRouter, Request, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.routers.webhooks.common import handle_inbound, record_delivery_status
from app.sms.factory import get_provider
import logging

logger = logging.getLogger("webhooks.telnyx")
router = APIRouter(prefix="/webhooks/telnyx", tags=["webhooks"])

DELIVERY_EVENTS = {"message.sent", "message.delivered", "message.failed", "message.finalized"}


@router.post("")
@router.post("/webhook")            # legacy path, kept so old profiles keep working
async def telnyx_webhook(request: Request, db: Session = Depends(get_db)):
    try:
        body = await request.json()
        data = body.get("data", {})
        event_type = data.get("event_type", "")
        payload = data.get("payload", {})

        if event_type == "message.received":
            from_number = payload.get("from", {}).get("phone_number", "")
            text = payload.get("text", "")

            reply = handle_inbound(db, from_number, text)
            if reply:
                try:
                    await get_provider().send(from_number, reply)
                except Exception as e:
                    logger.error(f"Auto-reply to {from_number} failed: {e}")

            return JSONResponse({"status": "ok"})

        if event_type in DELIVERY_EVENTS:
            message_id = payload.get("id", "")
            to_info = payload.get("to") or [{}]
            status = to_info[0].get("status", event_type) if to_info else event_type

            errors = payload.get("errors") or []
            detail = None
            if errors:
                first = errors[0]
                detail = f"{first.get('title', '')}: {first.get('detail', '')}".strip(": ").strip()

            record_delivery_status(db, message_id, status, detail)
            return JSONResponse({"status": "ok"})

        logger.debug(f"Unhandled Telnyx event: {event_type}")
        return JSONResponse({"status": "ok"})

    except Exception as e:
        logger.error(f"Telnyx webhook error: {e}")
        return JSONResponse({"status": "error"}, status_code=200)


@router.get("")
async def telnyx_webhook_healthcheck():
    """Confirms the URL is reachable — paste it in a browser after configuring."""
    return {"status": "active", "provider": "telnyx"}
