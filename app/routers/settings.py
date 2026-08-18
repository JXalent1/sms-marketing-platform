"""Runtime settings API (auto-reply text, system info)."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel
from app.core.database import get_db
from app.core.auth import require_auth
from app.core.config import settings as app_settings
from app.models.app_setting import get_setting, set_setting, AUTO_REPLY_KEY
from app.sms import compliance
from app.sms.factory import get_provider, active_sender_number
from app.sms.segments import describe

router = APIRouter(prefix="/api/settings", tags=["settings"])


class AutoReplyRequest(BaseModel):
    message: str


@router.get("/auto-reply")
async def get_auto_reply(db: Session = Depends(get_db), user: str = Depends(require_auth)):
    stored = get_setting(db, AUTO_REPLY_KEY)
    message = stored or compliance.default_auto_reply()
    return {"message": message, "is_default": stored is None, **describe(message)}


@router.put("/auto-reply")
async def update_auto_reply(payload: AutoReplyRequest, db: Session = Depends(get_db),
                            user: str = Depends(require_auth)):
    row = set_setting(db, AUTO_REPLY_KEY, payload.message,
                      description="Reply sent to inbound messages")
    return {"success": True, "value": row.value, **describe(row.value)}


@router.post("/auto-reply/reset")
async def reset_auto_reply(db: Session = Depends(get_db), user: str = Depends(require_auth)):
    default = compliance.default_auto_reply()
    set_setting(db, AUTO_REPLY_KEY, default)
    return {"success": True, "value": default}


@router.get("/system")
async def system_info(user: str = Depends(require_auth)):
    """Operational status. Deliberately does not leak API keys or the carrier
    name into anything the client sees."""
    provider = get_provider()
    return {
        "provider_configured": provider.name != "console",
        "dry_run": provider.name == "console",
        "sender_number": active_sender_number(),
        "environment": app_settings.ENVIRONMENT,
        "skip_non_us": app_settings.SKIP_NON_US_NUMBERS,
        "preflight_balance_check": app_settings.PREFLIGHT_BALANCE_CHECK,
        "webhook_url": app_settings.webhook_url(provider.name),
    }
