"""Category-first CSV import API: preview → commit → undo.

Separate from `/api/contacts/import`, which is the skeleton's uncategorised
import and stays as it is for now. This is the flow the client actually uses:
he picks tonight's niche first, sees what the file will do, then commits.

`category_id` is a required form field on both preview and commit. Making it
optional here would put the "which category?" decision in the UI's hands, and a
missing category is exactly the mistake that silently produces an untagged
audience nobody can safely text.

That is still true *here* after 5e A2 made the category optional in the service.
This is the standalone Contacts-screen import: it produces a pile of contacts and
no campaign, so an untagged one is a pile nobody can safely text. The campaign
upload flow (`POST /api/campaigns/from-upload`) is the case A2 opened up, because
there the list it creates is the campaign's entire audience — the targeting is the
list, not a category. Both routes call the same importer; they differ only in
whether a category is required of the caller, and each says why.

`require_category()` is called explicitly rather than left to FastAPI's required
form field. A 422 with a validation blob is not the sentence anyone should read
for this, and the service is where the rule has to hold for a caller that is not
HTTP.
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
        import_service.require_category(db, category_id)
        return import_service.preview(db, await file.read(), category_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/commit")
async def commit_import(file: UploadFile = File(...),
                        category_id: Optional[int] = Form(None),
                        db: Session = Depends(get_db),
                        user: str = Depends(require_auth)):
    try:
        import_service.require_category(db, category_id)
        result = import_service.commit(db, await file.read(), category_id)
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
