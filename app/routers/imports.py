"""CSV import API: preview → commit → undo.

Separate from `/api/contacts/import`, which is the skeleton's flow and answers
400 with a pointer here. This is the standalone Contacts-screen import: it
produces a named list and no campaign, and the campaign-first upload
(`POST /api/campaigns/from-upload`) is the other way in.

**A category is no longer required, and the reason is a product change rather
than a loosening.** Until 5i this route called `import_service.require_category()`
and refused an untagged upload, on the grounds that a pile of contacts nobody
had tagged was a pile nobody could safely text. 5i replaced the tag with the
thing that was actually carrying the meaning: the import lands in a **named
list**, the client names it, and that list is what a campaign points at. An
import with a list name is not untagged — it is named, which is strictly more
than a category ever told anyone.

`list_name` is optional on the wire and the importer falls back to a dated name
(`import_service.batch_list_name()`), because a name that collides or is left
blank must not lose the file. The Contacts screen requires one of the client
before it posts, which is a different question from what this endpoint accepts.

`category_id` is still accepted. Nothing in the UI sends one, and the taxonomy
it writes into is retained for prospecting — see `sessions/session-5i.md` A8.
"""

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session
from typing import Optional
import logging

from app.core.auth import require_auth
from app.core.database import get_db
from app.services import import_service

logger = logging.getLogger("imports")
router = APIRouter(prefix="/api/imports", tags=["imports"])


@router.post("/preview")
async def preview_import(file: UploadFile = File(...),
                         category_id: Optional[int] = Form(None),
                         db: Session = Depends(get_db),
                         user: str = Depends(require_auth)):
    try:
        return import_service.preview(db, await file.read(), category_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/commit")
async def commit_import(file: UploadFile = File(...),
                        category_id: Optional[int] = Form(None),
                        list_name: Optional[str] = Form(None),
                        db: Session = Depends(get_db),
                        user: str = Depends(require_auth)):
    try:
        result = import_service.commit(db, await file.read(), category_id,
                                       list_name=list_name)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        # Partial imports are the worst outcome: half the file tagged, no batch
        # to undo. Roll back and let him retry the whole file.
        db.rollback()
        logger.error(f"import commit failed: {e}")
        raise HTTPException(status_code=500, detail="Import failed")
    return {"success": True, **result}


@router.post("/{list_id}/undo")
async def undo_import(list_id: int, db: Session = Depends(get_db),
                      user: str = Depends(require_auth)):
    try:
        result = import_service.undo(db, list_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"success": True, **result}
