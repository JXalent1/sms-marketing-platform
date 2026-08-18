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
async def balance(user: str = Depends(require_auth)):
    """Provider account balance — operator-facing, not for the client's eyes.

    Check this before every large blast. A campaign that outruns the balance
    fails from that point onward and cannot be cleanly resumed.
    """
    amount = await get_provider().get_balance()
    return {
        "balance": amount,
        "threshold": settings.BALANCE_ALERT_THRESHOLD,
        "low": amount is not None and amount < settings.BALANCE_ALERT_THRESHOLD,
    }
