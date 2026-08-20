"""Contacts and lists API.

CSV import is NOT here any more. The category-first flow in routers/imports.py
is the only supported path — see the note on the retired endpoints below.
"""

import csv
import io

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
from app.core.database import get_db
from app.core.auth import require_auth
from app.models.contact_list import ContactList, ContactListMember
from app.services import contact_service, contact_query_service
import logging

logger = logging.getLogger("contacts")
router = APIRouter(prefix="/api/contacts", tags=["contacts"])

# What a client of the retired import endpoints is told. It names the
# replacement rather than 404ing, because "gone" without "go here instead" is
# how an integration gets rebuilt against the wrong flow a second time.
IMPORT_RETIRED = (
    "Uncategorised import has been withdrawn. Every import is tagged with a "
    "category at upload time: POST /api/imports/preview then "
    "/api/imports/commit, both with a category_id."
)


class CreateContactRequest(BaseModel):
    phone: str
    full_name: Optional[str] = None
    email: Optional[str] = None
    list_name: Optional[str] = None


class BulkCategoryRequest(BaseModel):
    contact_ids: List[int]
    category_id: int


@router.get("")
async def list_contacts(q: str = None, category_id: int = None,
                        page: int = 1, per_page: int = contact_query_service.PAGE_SIZE,
                        db: Session = Depends(get_db), user: str = Depends(require_auth)):
    """One page of contacts. Server-side paging, always — see the service."""
    return contact_query_service.search_contacts(
        db, q=q, category_id=category_id, page=page, per_page=per_page)


@router.get("/categories")
async def contact_category_tabs(db: Session = Depends(get_db),
                                user: str = Depends(require_auth)):
    """The tab row with live counts. Re-read after a bulk action."""
    return {"tabs": contact_query_service.category_tabs(db)}


@router.get("/export.csv")
async def export_contacts(q: str = None, category_id: int = None,
                          ids: str = Query(None, description="comma-separated contact ids"),
                          db: Session = Depends(get_db), user: str = Depends(require_auth)):
    """Export the selection, or the whole current filter when nothing is ticked.

    Streamed in chunks: a 50,000-row export built in memory is a minute of
    silence followed by a request that may not survive the proxy.
    """
    selected = [int(i) for i in (ids or "").split(",") if i.strip().isdigit()]

    def rows():
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=contact_query_service.EXPORT_COLUMNS)
        writer.writeheader()
        yield buffer.getvalue()
        for row in contact_query_service.iter_export(
                db, q=q, category_id=category_id, contact_ids=selected):
            buffer.seek(0)
            buffer.truncate(0)
            writer.writerow(row)
            yield buffer.getvalue()

    return StreamingResponse(
        rows(), media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="contacts.csv"'},
    )


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


@router.post("/bulk/add-category")
async def bulk_add_category(payload: BulkCategoryRequest, db: Session = Depends(get_db),
                            user: str = Depends(require_auth)):
    try:
        return contact_query_service.bulk_add_category(
            db, payload.contact_ids, payload.category_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/bulk/remove-category")
async def bulk_remove_category(payload: BulkCategoryRequest, db: Session = Depends(get_db),
                               user: str = Depends(require_auth)):
    """Remove a tag from a selection. Contacts are never deleted here."""
    try:
        return contact_query_service.bulk_remove_category(
            db, payload.contact_ids, payload.category_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ─── Retired: uncategorised import ──────────────────────────────────────────
#
# These were the skeleton's original flow and they accepted an upload with no
# category. That is precisely the mistake the category work exists to make
# impossible: an untagged block of contacts is one nobody can safely text, and
# it is invisible until the day a Memorabilia collector is sent an ad for a
# walk-in cooler. They answer 400 rather than being deleted outright so an
# older client or a bookmarked script is told where the flow went.

@router.post("/import/preview")
async def preview_import_retired(user: str = Depends(require_auth)):
    raise HTTPException(status_code=400, detail=IMPORT_RETIRED)


@router.post("/import")
async def import_csv_retired(user: str = Depends(require_auth)):
    raise HTTPException(status_code=400, detail=IMPORT_RETIRED)


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
