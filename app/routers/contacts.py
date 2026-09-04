"""Contacts and lists API.

CSV import is NOT here any more. `routers/imports.py` is the only supported
path — see the note on the retired endpoints below.

**The category endpoints below are retained and have no caller in the UI.**
Session 5i took every category surface off the client's screens without taking
anything out of the schema: `/api/contacts/categories` and the two bulk routes
still work, still do what they say, and nothing in `contacts.html` calls them.
Deleting them is schema-adjacent and 5i deliberately did not do it — the
taxonomy they write into is what the prospecting pipeline is keyed on.
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
from app.models.category import Category
from app.models.contact_list import ContactList, ContactListMember
from app.services import (
    blocklist_service, category_service, contact_service, contact_query_service,
)
from app.sms.phone import is_valid, normalize
import logging

logger = logging.getLogger("contacts")
router = APIRouter(prefix="/api/contacts", tags=["contacts"])

# What a client of the retired import endpoints is told. It names the
# replacement rather than 404ing, because "gone" without "go here instead" is
# how an integration gets rebuilt against the wrong flow a second time.
IMPORT_RETIRED = (
    "This import endpoint has been withdrawn. Every import now lands in a named "
    "list: POST /api/imports/preview then /api/imports/commit, with a list_name."
)


class CreateContactRequest(BaseModel):
    phone: str
    full_name: Optional[str] = None
    email: Optional[str] = None
    company: Optional[str] = None
    list_name: Optional[str] = None
    category_id: Optional[int] = None


# What someone adding a blocklisted number by hand is told. Adding a contact and
# unblocking a number are different acts, and only one of them is on this form:
# an opt-out is a legal record, and a form that silently overrode it would make
# every STOP on the box provisional.
BLOCKED_CONTACT_ERROR = (
    "That number is on your opt-out list, so it was not added. Somebody using it "
    "asked not to be texted, or a carrier reported it as undeliverable. If they "
    "have asked to be added back, remove them from Opt-outs first — that keeps "
    "the record of what changed and when."
)


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
    """Add one contact by hand — somebody phoned the auction house and asked.

    This endpoint existed with no form in front of it, so the answer to that
    phone call was "build a one-row CSV". 5e A3 gives it a form and, with it, the
    two guards an import has always had:

    **Normalisation.** `upsert_contact()` normalises to E.164 and refuses a
    number that is not one, which is what makes `contacts.phone` unique mean
    anything — "(954) 600-0777" typed here and `+19546000777` in tomorrow's CSV
    are one person, not two.

    **The blocklist.** An import skips an opted-out number outright: not created,
    not tagged, not added to the list. Typing it by hand must not be the way
    round that. It is refused rather than silently skipped, because unlike a
    6,000-row file this is one number somebody deliberately entered and the
    honest answer is why it did not go in.

    Both are checked *before* anything is written. A contact created and then
    found to be blocked would leave a row the client can see on the Contacts
    screen and that no campaign will ever text — which looks like a bug in the
    send path rather than an opt-out being honoured.
    """
    phone = normalize(payload.phone)
    if not phone or not is_valid(phone):
        raise HTTPException(status_code=400, detail="Invalid phone number")
    if blocklist_service.is_blocked(db, phone):
        raise HTTPException(status_code=409, detail=BLOCKED_CONTACT_ERROR)

    # The category is resolved here, before the contact is written, for the same
    # reason `import_service._resolve_category()` does it: `tag_contact()`
    # validates the tag's *source* and never the category, and SQLite does not
    # enforce the foreign key, so an id naming nothing writes a dangling
    # `contact_categories` row and reports success. That row then keeps the
    # contact alive forever through `_still_referenced()` on an undo. A typo must
    # not turn into a quietly mis-tagged contact.
    if payload.category_id is not None and db.get(Category, payload.category_id) is None:
        raise HTTPException(
            status_code=404,
            detail=f"No category with id {payload.category_id}.")

    # Company rides in `attributes` rather than in a column of its own: that is
    # where the CSV import puts it and where the Contacts search already looks
    # for it, so a hand-added contact is findable the same way an imported one is.
    attributes = {"company": payload.company.strip()} if (payload.company or "").strip() else None

    contact = contact_service.upsert_contact(
        db, phone=phone, full_name=payload.full_name,
        email=payload.email, source="manual", attributes=attributes,
    )
    if not contact:
        raise HTTPException(status_code=400, detail="Invalid phone number")

    if payload.list_name:
        target = contact_service.get_or_create_list(db, payload.list_name, source="manual")
        contact_service.add_to_list(db, target.id, contact.id)

    tagged = False
    if payload.category_id is not None:
        try:
            tagged = category_service.tag_contact(
                db, contact.id, payload.category_id, source="manual")
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    return {"success": True, "contact_id": contact.id, "phone": contact.phone,
            "tagged": tagged}


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


# ─── Retired: the skeleton's import ─────────────────────────────────────────
#
# These were the skeleton's original flow and they produced a block of contacts
# belonging to nothing — no tag under module 2's model, and no named list under
# 5i's. Either way it is a block nobody can safely text, and it is invisible
# until the day a memorabilia collector is sent an ad for a walk-in cooler. They
# answer 400 rather than being deleted outright so an older client or a
# bookmarked script is told where the flow went.

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
