"""The Contacts screen's query layer: search, paging, bulk actions, export.

Split out of contact_service rather than appended to it. That module is the
audience-resolution engine the send path depends on; this one is a read model
for one screen, it changes when the screen changes, and keeping the two apart
keeps either file readable in one sitting.

Everything here is written to survive 50,000 contacts. Two rules:

  1. A page is a COUNT plus a LIMIT/OFFSET, never a full read sliced in Python.
     The reference build paged in the browser: it shipped the entire table on
     every keystroke and stopped being usable at about 8,000 rows, which nobody
     noticed until the client's list crossed it.
  2. Per-row extras — category chips, send counts — are fetched for the whole
     page in one query each. A lookup per row is an N+1 that looks fine against
     50 seeded contacts and is 100 round-trips on a real page.
"""

from sqlalchemy.orm import Session
from sqlalchemy import func, or_
from app.models.contact import Contact
from app.models.category import Category, ContactCategory
from app.models.sms_message import SMSMessage
from datetime import datetime
from typing import Iterator, List
import logging
import re

logger = logging.getLogger("contacts")

PAGE_SIZE = 50
MAX_PAGE_SIZE = 200

# Sends, not billable sends. "How many texts has this person had from us" is a
# different question from what we invoice for, and they must not share a
# constant — a commercial change to the billable set is not a change to this.
COUNTED_STATUSES = ("sent", "delivered")

_DIGITS = re.compile(r"\D")


def _contacts_query(db: Session, q: str = None, category_id: int = None):
    """Base query for the contacts table — active contacts, filtered.

    Returned unordered and unpaged so the count and the page share exactly one
    definition of "matching". Two queries that drift apart is how a list shows
    "1,204 contacts" above 50 rows of something else.
    """
    query = db.query(Contact).filter(Contact.is_active == 1)

    if category_id:
        # Sub-select rather than a join: a contact tagged twice must not appear
        # twice in the table or be counted twice above it.
        query = query.filter(Contact.id.in_(
            db.query(ContactCategory.contact_id)
            .filter(ContactCategory.category_id == category_id)
            .scalar_subquery()
        ))

    q = (q or "").strip()
    if q:
        pattern = f"%{q}%"
        clauses = [
            Contact.full_name.ilike(pattern),
            Contact.phone.ilike(pattern),
            # attributes->company, via SQLAlchemy's JSON path so this reads the
            # same on SQLite (json_extract) and Postgres (->>). Company is where
            # the CSV importer parks a business name, and "Sysco" is how he
            # looks someone up far more often than by their first name.
            Contact.attributes["company"].as_string().ilike(pattern),
        ]
        digits = _DIGITS.sub("", q)
        if digits:
            # "(954) 555-1001" and "954 555" have to find a number stored as
            # +19545551001. Without this, searching by the number as it appears
            # on his own screen returns nothing.
            clauses.append(Contact.phone.ilike(f"%{digits}%"))
        query = query.filter(or_(*clauses))

    return query


def _chips_for(db: Session, contact_ids: List[int]) -> dict:
    """contact_id -> its category chips. One query for the whole page."""
    if not contact_ids:
        return {}
    rows = (db.query(ContactCategory.contact_id, Category.slug, Category.label,
                     Category.color_token)
            .join(Category, Category.id == ContactCategory.category_id)
            .filter(ContactCategory.contact_id.in_(contact_ids))
            .order_by(Category.sort_order, Category.label)
            .all())
    chips = {}
    for contact_id, slug, label, color_token in rows:
        chips.setdefault(contact_id, []).append(
            {"slug": slug, "label": label, "color_token": color_token})
    return chips


def _send_counts_for(db: Session, contact_ids: List[int]) -> dict:
    """contact_id -> messages actually sent to them. One query for the page."""
    if not contact_ids:
        return {}
    rows = (db.query(SMSMessage.contact_id, func.count(SMSMessage.id))
            .filter(SMSMessage.contact_id.in_(contact_ids),
                    SMSMessage.status.in_(COUNTED_STATUSES))
            .group_by(SMSMessage.contact_id)
            .all())
    return dict(rows)


def _row(contact: Contact, chips: dict, sends: dict) -> dict:
    return {
        "id": contact.id,
        "full_name": contact.full_name,
        "company": (contact.attributes or {}).get("company"),
        "phone": contact.phone,
        "source": contact.source,
        "last_texted": contact.last_messaged_at,
        "send_count": sends.get(contact.id, 0),
        "categories": chips.get(contact.id, []),
    }


def search_contacts(db: Session, q: str = None, category_id: int = None,
                    page: int = 1, per_page: int = PAGE_SIZE) -> dict:
    """One page of the contacts table, with everything a row renders."""
    per_page = max(1, min(int(per_page or PAGE_SIZE), MAX_PAGE_SIZE))
    page = max(1, int(page or 1))

    query = _contacts_query(db, q=q, category_id=category_id)
    total = query.order_by(None).with_entities(func.count(Contact.id)).scalar() or 0

    pages = max(1, -(-total // per_page))
    page = min(page, pages)

    rows = (query.order_by(Contact.id.desc())
            .offset((page - 1) * per_page).limit(per_page).all())
    ids = [c.id for c in rows]
    chips = _chips_for(db, ids)
    sends = _send_counts_for(db, ids)

    return {
        "total": total,
        "page": page,
        "pages": pages,
        "per_page": per_page,
        "contacts": [_row(c, chips, sends) for c in rows],
    }


def category_tabs(db: Session) -> List[dict]:
    """"All" plus every active category with a live count. One grouped query."""
    counts = dict(
        db.query(ContactCategory.category_id, func.count(Contact.id))
        .join(Contact, Contact.id == ContactCategory.contact_id)
        .filter(Contact.is_active == 1)
        .group_by(ContactCategory.category_id)
        .all()
    )
    total = db.query(func.count(Contact.id)).filter(Contact.is_active == 1).scalar() or 0

    tabs = [{"id": None, "slug": "all", "label": "All",
             "color_token": None, "count": total}]
    tabs += [{"id": row.id, "slug": row.slug, "label": row.label,
              "color_token": row.color_token, "count": counts.get(row.id, 0)}
             for row in (db.query(Category).filter(Category.is_active == 1)
                         .order_by(Category.sort_order, Category.label).all())]
    return tabs


# ─── Bulk actions ───────────────────────────────────────────────────────────

def bulk_add_category(db: Session, contact_ids: List[int], category_id: int) -> dict:
    """Tag a selection. Returns how many tags were actually new.

    "added" is not len(contact_ids): re-tagging someone already in the category
    is a no-op, and reporting it as a change would tell him an import did
    something it did not.
    """
    if db.get(Category, category_id) is None:
        raise LookupError(f"No category with id {category_id}")
    ids = [int(i) for i in (contact_ids or [])]
    if not ids:
        return {"added": 0, "already_tagged": 0}

    existing = {row.contact_id for row in
                db.query(ContactCategory.contact_id).filter(
                    ContactCategory.contact_id.in_(ids),
                    ContactCategory.category_id == category_id).all()}
    fresh = [i for i in dict.fromkeys(ids) if i not in existing]

    now = datetime.now().isoformat()
    for contact_id in fresh:
        db.add(ContactCategory(contact_id=contact_id, category_id=category_id,
                               source="manual", confidence=None, added_at=now))
    db.commit()
    return {"added": len(fresh), "already_tagged": len(ids) - len(fresh)}


def bulk_remove_category(db: Session, contact_ids: List[int], category_id: int) -> dict:
    """Untag a selection. Removes memberships only — never a contact."""
    if db.get(Category, category_id) is None:
        raise LookupError(f"No category with id {category_id}")
    ids = [int(i) for i in (contact_ids or [])]
    if not ids:
        return {"removed": 0}

    removed = (db.query(ContactCategory)
               .filter(ContactCategory.contact_id.in_(ids),
                       ContactCategory.category_id == category_id)
               .delete(synchronize_session=False))
    db.commit()
    return {"removed": removed}


# ─── Export ─────────────────────────────────────────────────────────────────

EXPORT_COLUMNS = ("name", "company", "phone", "categories", "source",
                  "last_texted", "send_count")


def iter_export(db: Session, q: str = None, category_id: int = None,
                contact_ids: List[int] = None, chunk: int = 500) -> Iterator[dict]:
    """Yield export rows in chunks, so an export never holds the list in memory.

    Nothing here names the carrier: the export is a client deliverable and the
    provider is our implementation detail. The columns are his data only.
    """
    if contact_ids:
        query = (db.query(Contact)
                 .filter(Contact.id.in_([int(i) for i in contact_ids])))
    else:
        query = _contacts_query(db, q=q, category_id=category_id)
    query = query.order_by(Contact.id.desc())

    offset = 0
    while True:
        rows = query.offset(offset).limit(chunk).all()
        if not rows:
            return
        ids = [c.id for c in rows]
        chips = _chips_for(db, ids)
        sends = _send_counts_for(db, ids)
        for contact in rows:
            row = _row(contact, chips, sends)
            yield {
                "name": row["full_name"] or "",
                "company": row["company"] or "",
                "phone": row["phone"],
                "categories": "; ".join(c["label"] for c in row["categories"]),
                "source": row["source"] or "",
                "last_texted": row["last_texted"] or "",
                "send_count": row["send_count"],
            }
        offset += chunk
