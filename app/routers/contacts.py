"""Contacts and lists API, including CSV import."""

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session
from sqlalchemy import or_
from pydantic import BaseModel
from typing import Optional
from app.core.database import get_db
from app.core.auth import require_auth
from app.models.contact import Contact
from app.models.contact_list import ContactList, ContactListMember
from app.services import contact_service
from app.sources.csv_source import CSVContactSource
import logging

logger = logging.getLogger("contacts")
router = APIRouter(prefix="/api/contacts", tags=["contacts"])


class CreateContactRequest(BaseModel):
    phone: str
    full_name: Optional[str] = None
    email: Optional[str] = None
    list_name: Optional[str] = None


@router.get("")
async def list_contacts(q: str = None, list_id: int = None, skip: int = 0, limit: int = 200,
                        db: Session = Depends(get_db), user: str = Depends(require_auth)):
    query = db.query(Contact)

    if list_id:
        query = query.join(
            ContactListMember, ContactListMember.contact_id == Contact.id
        ).filter(ContactListMember.list_id == list_id)

    if q:
        pattern = f"%{q}%"
        query = query.filter(or_(Contact.full_name.ilike(pattern),
                                 Contact.phone.ilike(pattern),
                                 Contact.email.ilike(pattern)))

    total = query.count()
    rows = query.order_by(Contact.id.desc()).offset(skip).limit(limit).all()

    return {
        "total": total,
        "contacts": [{
            "id": c.id,
            "phone": c.phone,
            "full_name": c.full_name,
            "email": c.email,
            "source": c.source,
            "attributes": c.attributes or {},
            "last_messaged_at": c.last_messaged_at,
            "created_at": c.created_at,
        } for c in rows],
    }


@router.post("")
async def create_contact(payload: CreateContactRequest, db: Session = Depends(get_db),
                         user: str = Depends(require_auth)):
    contact = contact_service.upsert_contact(
        db, phone=payload.phone, full_name=payload.full_name,
        email=payload.email, source="manual",
    )
    if not contact:
        raise HTTPException(status_code=400, detail="Invalid phone number")

    if payload.list_name:
        target = contact_service.get_or_create_list(db, payload.list_name, source="manual")
        contact_service.add_to_list(db, target.id, contact.id)

    return {"success": True, "contact_id": contact.id, "phone": contact.phone}


@router.post("/import/preview")
async def preview_import(file: UploadFile = File(...), user: str = Depends(require_auth)):
    """Show the detected column mapping before committing an import.

    Worth the extra click: a CSV whose phone column wasn't recognized imports
    zero rows and looks identical to a successful import of an empty file.
    """
    try:
        return CSVContactSource.preview(await file.read())
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/import")
async def import_csv(file: UploadFile = File(...), list_name: str = Form(None),
                     db: Session = Depends(get_db), user: str = Depends(require_auth)):
    """Import a CSV, optionally into a named list."""
    try:
        result = CSVContactSource().ingest(db, list_name=list_name, content=await file.read())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        db.rollback()
        logger.error(f"CSV import failed: {e}")
        raise HTTPException(status_code=500, detail="Import failed")

    return {"success": True, "list_name": list_name, **result.as_dict()}


# ─── Lists ──────────────────────────────────────────────────────────────────

lists_router = APIRouter(prefix="/api/lists", tags=["lists"])


@lists_router.get("")
async def get_lists(db: Session = Depends(get_db), user: str = Depends(require_auth)):
    return {"lists": contact_service.list_summaries(db)}


@lists_router.delete("/{list_id}")
async def delete_list(list_id: int, db: Session = Depends(get_db),
                      user: str = Depends(require_auth)):
    """Delete a list. Contacts themselves are never deleted — only membership.

    A list is a view onto the audience; dropping one must not destroy contact
    history, opt-out state, or message records.
    """
    row = db.get(ContactList, list_id)
    if not row:
        raise HTTPException(status_code=404, detail="List not found")

    removed = db.query(ContactListMember).filter(
        ContactListMember.list_id == list_id
    ).delete()
    db.delete(row)
    db.commit()
    return {"success": True, "memberships_removed": removed}
