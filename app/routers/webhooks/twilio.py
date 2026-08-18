"""Twilio webhook.

Twilio posts form-encoded data and expects TwiML back, so the reply is returned
inline rather than sent as a second API call.
"""

from fastapi import APIRouter, Request, Response, Depends
from sqlalchemy.orm import Session
from xml.sax.saxutils import escape
from app.core.database import get_db
from app.routers.webhooks.common import handle_inbound, record_delivery_status
import logging

logger = logging.getLogger("webhooks.twilio")
router = APIRouter(prefix="/webhooks/twilio", tags=["webhooks"])

EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'


def _twiml(message: str) -> str:
    # escape() matters: an unescaped & or < in the auto-reply produces invalid
    # TwiML and Twilio silently sends nothing.
    return (f'<?xml version="1.0" encoding="UTF-8"?>'
            f'<Response><Message>{escape(message)}</Message></Response>')


@router.post("/sms")
async def incoming_sms(request: Request, db: Session = Depends(get_db)):
    try:
        form = await request.form()
        reply = handle_inbound(db, form.get("From", ""), form.get("Body", ""))
        return Response(content=_twiml(reply), media_type="application/xml")
    except Exception as e:
        logger.error(f"Twilio inbound error: {e}")
        return Response(content=EMPTY_TWIML, media_type="application/xml")


@router.post("/status")
async def status_callback(request: Request, db: Session = Depends(get_db)):
    """Delivery status callback.

    Configure this as the StatusCallback URL on the messaging service, otherwise
    every message stays 'sent' forever and undelivered traffic is invisible.
    """
    try:
        form = await request.form()
        record_delivery_status(
            db,
            form.get("MessageSid", ""),
            form.get("MessageStatus", ""),
            form.get("ErrorMessage") or form.get("ErrorCode"),
        )
    except Exception as e:
        logger.error(f"Twilio status error: {e}")
    return Response(content=EMPTY_TWIML, media_type="application/xml")


@router.get("/sms")
async def twilio_webhook_healthcheck():
    return {"status": "active", "provider": "twilio"}
