"""Category CRUD and tagging.

Two rules worth stating up front, because both are easy to get wrong and
expensive to notice late:

  1. `color_token` is validated against COLOR_TOKENS on every write. The four
     hues passed a colorblind-separation and contrast validator as a set; a
     fifth value would render as an unstyled chip at best and as two
     indistinguishable categories at worst.

  2. A category with members is never hard-deleted. The FK cascades, so the
     delete would silently take the tagging history with it — the one thing in
     this table that cannot be reconstructed from a CSV. Deactivation is the
     supported way to retire a category and it loses nothing.
"""

import re
from datetime import datetime
from typing import List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.category import Category, ContactCategory, COLOR_TOKENS, TAG_SOURCES
from app.models.contact import Contact

SLUG_RE = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*$")


class CategoryInUse(Exception):
    """Raised when a destructive operation would drop tagging history."""


def validate_color_token(token: str) -> str:
    if token not in COLOR_TOKENS:
        raise ValueError(
            f"Unknown color token {token!r}. Allowed: {', '.join(COLOR_TOKENS)}. "
            "The four hues are validated as a set — a new category takes 'neutral'."
        )
    return token


def validate_slug(slug: str) -> str:
    slug = (slug or "").strip().lower()
    if not SLUG_RE.match(slug):
        raise ValueError(
            f"Invalid category slug {slug!r} — use lowercase words joined by "
            "underscores, e.g. 'food_service'."
        )
    return slug


# ─── Reads ──────────────────────────────────────────────────────────────────

def list_categories(db: Session, include_inactive: bool = False) -> List[Category]:
    query = db.query(Category)
    if not include_inactive:
        query = query.filter(Category.is_active == 1)
    return query.order_by(Category.sort_order, Category.label).all()


def get_by_slug(db: Session, slug: str) -> Optional[Category]:
    return db.query(Category).filter(Category.slug == slug).first()


def member_count(db: Session, category_id: int) -> int:
    """Active contacts carrying this category.

    Active-only, because that is the number a campaign would actually reach and
    the number the picker shows. `has_members()` below deliberately does not
    filter, since a suppressed contact's tag is still history worth protecting.
    """
    return (db.query(func.count(ContactCategory.id))
            .join(Contact, Contact.id == ContactCategory.contact_id)
            .filter(ContactCategory.category_id == category_id, Contact.is_active == 1)
            .scalar()) or 0


def has_members(db: Session, category_id: int) -> bool:
    return db.query(ContactCategory).filter(
        ContactCategory.category_id == category_id
    ).first() is not None


# ─── Writes ─────────────────────────────────────────────────────────────────

def create_category(db: Session, slug: str, label: str, color_token: str = "neutral",
                    sort_order: int = 0) -> Category:
    slug = validate_slug(slug)
    validate_color_token(color_token)
    label = (label or "").strip()
    if not label:
        raise ValueError("Category label is required")

    if get_by_slug(db, slug):
        raise ValueError(f"Category slug {slug!r} already exists")

    row = Category(
        slug=slug,
        label=label,
        color_token=color_token,
        sort_order=sort_order,
        is_active=1,
        created_at=datetime.now().isoformat(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update_category(db: Session, category_id: int, label: str = None,
                    color_token: str = None, sort_order: int = None,
                    is_active: int = None) -> Category:
    """Update the mutable fields. `slug` is not one of them.

    A slug is what a saved campaign audience ("category:food_service") points
    at. Renaming it would silently repoint or break every stored selector, so
    the label is what changes when the client wants different wording.
    """
    row = db.get(Category, category_id)
    if row is None:
        raise LookupError(f"No category with id {category_id}")

    if label is not None:
        label = label.strip()
        if not label:
            raise ValueError("Category label cannot be blank")
        row.label = label
    if color_token is not None:
        row.color_token = validate_color_token(color_token)
    if sort_order is not None:
        row.sort_order = int(sort_order)
    if is_active is not None:
        row.is_active = 1 if int(is_active) else 0

    db.commit()
    db.refresh(row)
    return row


def deactivate_category(db: Session, category_id: int) -> Category:
    """Soft delete. Hides it from pickers; keeps every tag."""
    return update_category(db, category_id, is_active=0)


def delete_category(db: Session, category_id: int) -> None:
    """Hard delete, permitted only while the category has no members.

    The FK is ON DELETE CASCADE, so this would otherwise drop every
    contact_categories row without a word — the client would see a category
    disappear and have no way to know how many people went with it.
    """
    row = db.get(Category, category_id)
    if row is None:
        raise LookupError(f"No category with id {category_id}")

    if has_members(db, category_id):
        raise CategoryInUse(
            f"{row.label!r} still has contacts tagged with it. Deleting it would "
            "discard that tagging history. Deactivate it instead — it disappears "
            "from the pickers and nothing is lost."
        )

    db.delete(row)
    db.commit()


def tag_contact(db: Session, contact_id: int, category_id: int,
                source: str = "manual", confidence: float = None,
                commit: bool = True) -> bool:
    """Tag a contact. Returns True only if the tag is new.

    The return value is load-bearing for imports: it is what an undo uses to
    tell "this batch put the tag there" from "the tag was already there".
    """
    if source not in TAG_SOURCES:
        raise ValueError(f"Unknown tag source {source!r}. Allowed: {', '.join(TAG_SOURCES)}")

    existing = db.query(ContactCategory).filter(
        ContactCategory.contact_id == contact_id,
        ContactCategory.category_id == category_id,
    ).first()
    if existing:
        return False

    db.add(ContactCategory(
        contact_id=contact_id,
        category_id=category_id,
        source=source,
        confidence=confidence,
        added_at=datetime.now().isoformat(),
    ))
    if commit:
        db.commit()
    else:
        db.flush()
    return True


def as_dict(db: Session, row: Category) -> dict:
    return {
        "id": row.id,
        "slug": row.slug,
        "label": row.label,
        "color_token": row.color_token,
        "sort_order": row.sort_order,
        "is_active": bool(row.is_active),
        "selector": f"category:{row.slug}",
        "count": member_count(db, row.id),
    }
