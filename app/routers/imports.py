"""Category-first CSV import API: preview → commit → undo.

Separate from `/api/contacts/import`, which is the skeleton's uncategorised
import and stays as it is for now. This is the flow the client actually uses:
he picks tonight's niche first, sees what the file will do, then commits.

`category_id` is a required form field on both preview and commit. Making it
optional here would put the "which category?" decision in the UI's hands, and a
missing category is exactly the mistake that silently produces an untagged
audience nobody can safely text.
"""

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session
import logging

from app.core.auth import require_auth
from app.core.database import get_db
from app.services import import_service

logger = logging.getLogger("imports")
router = APIRouter(prefix="/api/imports", tags=["imports"])


@router.post("/preview")
async def preview_import(file: UploadFile = File(...),
                         category_id: int = Form(...),
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
                        category_id: int = Form(...),
                        db: Session = Depends(get_db),
                        user: str = Depends(require_auth)):
    try:
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
