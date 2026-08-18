"""Blocklist API."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from app.core.database import get_db
from app.core.auth import require_auth
from app.services.blocklist_service import (
    block_number, unblock_number, get_all_blocked, get_blocked_count,
)
from app.sms.phone import scrub_provider_text

router = APIRouter(prefix="/api/blocklist", tags=["blocklist"])


class BlockRequest(BaseModel):
    phone: str
    reason: str = "manual"
    notes: Optional[str] = None


class UnblockRequest(BaseModel):
    phone: str


def _neutral_source(source: str) -> str:
    """Clients see 'Automatic', not which carrier flagged the number."""
    return "auto" if source not in (None, "manual") else (source or "manual")


@router.get("")
async def list_blocked(db: Session = Depends(get_db), user: str = Depends(require_auth)):
    rows = get_all_blocked(db)
    return {
        "total": get_blocked_count(db),
        "numbers": [{
            "id": r.id,
            "phone": r.phone,
            "reason": r.reason,
            "source": _neutral_source(r.source),
            "blocked_at": r.blocked_at,
            "notes": scrub_provider_text(r.notes),
        } for r in rows],
    }


@router.post("/block")
async def block(payload: BlockRequest, db: Session = Depends(get_db),
                user: str = Depends(require_auth)):
    ok = block_number(db, payload.phone, reason=payload.reason,
                      source="manual", notes=payload.notes)
    return {"success": ok,
            "message": f"{payload.phone} blocked" if ok else "Already blocked or invalid"}


@router.post("/unblock")
async def unblock(payload: UnblockRequest, db: Session = Depends(get_db),
                  user: str = Depends(require_auth)):
    ok = unblock_number(db, payload.phone)
    return {"success": ok,
            "message": f"{payload.phone} unblocked" if ok else "Not on the blocklist"}


@router.get("/count")
async def count(db: Session = Depends(get_db), user: str = Depends(require_auth)):
    return {"count": get_blocked_count(db)}
