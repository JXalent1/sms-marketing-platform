"""Runtime settings API (auto-reply text, system info)."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from app.core.database import get_db
from app.core.auth import require_auth
from app.core.config import settings as app_settings
from app.models.app_setting import get_setting, set_setting, AUTO_REPLY_KEY
from app.services import suppression_service
from app.sms import compliance
from app.sms.factory import send_mode, active_sender_number
from app.sms.segments import describe

router = APIRouter(prefix="/api/settings", tags=["settings"])


class AutoReplyRequest(BaseModel):
    message: str


class SuppressionRequest(BaseModel):
    days: int


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


@router.get("/suppression")
async def get_suppression(db: Session = Depends(get_db), user: str = Depends(require_auth)):
    """The recent-contact hold-back window, and what it is currently doing.

    Surfaced because it can silently withhold an entire audience and did: at 3
    days it held back 6,856 of 6,857 recipients across two consecutive campaigns,
    and finding out why took a SQL query against the live database. A rule with
    that much reach belongs on a screen the person sending can read.

    `max_days` comes from the service so the field's own limit and the value the
    service will accept cannot drift apart.
    """
    return {
        "days": suppression_service.suppression_days(db),
        "max_days": suppression_service.SUPPRESSION_DAYS_MAX,
        "is_default": get_setting(db, suppression_service.SUPPRESSION_DAYS_KEY) is None,
    }


@router.put("/suppression")
async def update_suppression(payload: SuppressionRequest, db: Session = Depends(get_db),
                             user: str = Depends(require_auth)):
    """Set the window. Takes effect on the next send — no restart.

    The value is read fresh out of `app_settings` by every caller on the send
    path, which is what "without a restart" means here: there is no cached copy
    to invalidate and no worker holding an old number.
    """
    try:
        days = suppression_service.set_suppression_days(db, payload.days)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"success": True, "days": days,
            "max_days": suppression_service.SUPPRESSION_DAYS_MAX,
            "is_default": False}


@router.get("/system")
async def system_info(user: str = Depends(require_auth)):
    """Operational status. Deliberately does not leak API keys or the carrier
    name into anything the client sees.

    `webhook_url` used to be returned here and rendered on the Settings page.
    It is built as PUBLIC_BASE_URL + "/webhooks/" + provider.name, so it printed
    the carrier's name onto a client-facing screen — the one leak a grep for the
    carrier name could never find, because the name is assembled at runtime.
    It is also a setup value only we ever use: the client has no login to the
    carrier portal. It belongs in the deployment notes, not in his dashboard.
    Use `settings.webhook_url(provider_name)` directly when configuring.

    `dry_run` means a dry run was *chosen*. It used to mean "the console
    provider is active", which is also true when a carrier credential is wrong
    and get_provider() has fallen back — so a broken live box answered this
    endpoint exactly as a healthy dry-run one did. The third state is
    `sending_unavailable`, and it is the one that needs a human. The exception
    behind it stays in the log: it is SDK text and routinely names the carrier.
    """
    mode = send_mode()
    return {
        "provider_configured": mode.key == "live",
        "dry_run": mode.key == "dry_run",
        "sending_unavailable": mode.key == "unavailable",
        "send_mode": mode.key,
        "send_mode_label": mode.label,
        "send_mode_detail": mode.detail,
        "sender_number": active_sender_number(),
        "environment": app_settings.ENVIRONMENT,
        "skip_non_us": app_settings.SKIP_NON_US_NUMBERS,
        "preflight_balance_check": app_settings.PREFLIGHT_BALANCE_CHECK,
    }
