"""Usage and billing API."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.auth import require_auth
from app.core.config import settings
from app.services import billing_service
from app.sms.factory import get_provider

router = APIRouter(prefix="/api/usage", tags=["usage"])


@router.get("/current")
async def current(db: Session = Depends(get_db), user: str = Depends(require_auth)):
    return billing_service.current_usage(db)


@router.get("/history")
async def history(cycles: int = 6, db: Session = Depends(get_db),
                  user: str = Depends(require_auth)):
    return {"months": billing_service.usage_history(db, cycles=cycles)}


@router.get("/pricing")
async def pricing(user: str = Depends(require_auth)):
    """The plan as configured. The UI renders this rather than hardcoding rates,
    so the page can never drift from what the code actually charges."""
    return billing_service.pricing_table()


@router.get("/balance")
async def capacity(user: str = Depends(require_auth)):
    """Remaining sending capacity, denominated in segments.

    The carrier account and its dollar balance are our implementation detail —
    the only person logging in here is the client, and what he needs to know is
    "how many more messages can I send", not what we pay per message or which
    carrier holds the funds. A campaign that outruns the capacity fails from
    that point onward and cannot be cleanly resumed, so the number still has to
    be visible; it just gets shown in his units.
    """
    amount = await get_provider().get_balance()
    # Wholesale rate, used only as the divisor that turns our carrier balance
    # into his segment capacity. Neither it nor `amount` is returned.
    rate = settings.WHOLESALE_COST_PER_SEGMENT

    if amount is None or rate <= 0:
        # Console (dry run) and some carriers expose no balance at all.
        return {"segments_remaining": None, "threshold_segments": None, "low": False}

    return {
        "segments_remaining": int(amount / rate),
        "threshold_segments": int(settings.BALANCE_ALERT_THRESHOLD / rate),
        "low": amount < settings.BALANCE_ALERT_THRESHOLD,
    }
