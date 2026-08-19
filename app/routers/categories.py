"""Category CRUD API.

HTTP only: validate, delegate, serialize. Every rule that matters — the color
token set, the refusal to hard-delete a category with members — lives in
`category_service`, so a future screen or script cannot route around it.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional

from app.core.auth import require_auth
from app.core.database import get_db
from app.models.category import COLOR_TOKENS
from app.services import category_service
from app.services.category_service import CategoryInUse

router = APIRouter(prefix="/api/categories", tags=["categories"])


class CreateCategoryRequest(BaseModel):
    slug: str
    label: str
    color_token: str = "neutral"
    sort_order: int = 0


class UpdateCategoryRequest(BaseModel):
    label: Optional[str] = None
    color_token: Optional[str] = None
    sort_order: Optional[int] = None
    is_active: Optional[bool] = None


@router.get("")
async def get_categories(include_inactive: bool = False,
                         db: Session = Depends(get_db),
                         user: str = Depends(require_auth)):
    rows = category_service.list_categories(db, include_inactive=include_inactive)
    return {
        "categories": [category_service.as_dict(db, row) for row in rows],
        "color_tokens": list(COLOR_TOKENS),
    }


@router.post("")
async def create_category(payload: CreateCategoryRequest,
                          db: Session = Depends(get_db),
                          user: str = Depends(require_auth)):
    try:
        row = category_service.create_category(
            db, slug=payload.slug, label=payload.label,
            color_token=payload.color_token, sort_order=payload.sort_order,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"success": True, "category": category_service.as_dict(db, row)}


@router.patch("/{category_id}")
async def update_category(category_id: int, payload: UpdateCategoryRequest,
                          db: Session = Depends(get_db),
                          user: str = Depends(require_auth)):
    try:
        row = category_service.update_category(
            db, category_id,
            label=payload.label,
            color_token=payload.color_token,
            sort_order=payload.sort_order,
            is_active=None if payload.is_active is None else int(payload.is_active),
        )
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"success": True, "category": category_service.as_dict(db, row)}


@router.delete("/{category_id}")
async def delete_category(category_id: int, hard: bool = False,
                          db: Session = Depends(get_db),
                          user: str = Depends(require_auth)):
    """Deactivate by default; `?hard=true` only for a category nobody is in.

    The default is the soft delete because that is what "delete this category"
    almost always means, and it is the one that cannot lose data. A genuine
    hard delete has to be asked for, and is refused while anyone is tagged.
    """
    try:
        if hard:
            category_service.delete_category(db, category_id)
            return {"success": True, "deleted": True, "category_id": category_id}
        row = category_service.deactivate_category(db, category_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except CategoryInUse as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"success": True, "deleted": False,
            "category": category_service.as_dict(db, row)}
