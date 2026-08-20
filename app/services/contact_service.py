"""Contacts, lists and audience resolution.

Audience selectors are strings so they can round-trip through a form field and
be stored on the campaign:

    "all"                            every active contact
    "list:<id>"                      members of one list
    "source:csv"                     everything a given ContactSource produced
    "category:food_service"          members of one category
    "category:food_service,equipment"  UNION of two or more
    "category:equipment&list:12"     INTERSECTION — in the category AND on the list

Grammar, stated so nothing has to be guessed:

  - `&` binds looser than `,`. "category:a,b&list:12" is (a ∪ b) ∩ list12.
  - Exactly one `&` is supported. Two is a ValueError, not a best guess.
  - An unknown category slug is a ValueError naming the slug. It is never a
    silent empty audience: a typo that quietly resolves to zero recipients is
    how a campaign gets "sent" to nobody and nobody finds out for a week.

Deactivating a category does not stop it resolving. Retiring a category from the
pickers must not silently empty a campaign already pointed at it — `list_summaries()`
is what hides it, and that is the honest place for the distinction.
"""

from sqlalchemy.orm import Session
from sqlalchemy import func, and_
from app.models.contact import Contact
from app.models.category import Category, ContactCategory
from app.models.contact_list import ContactList, ContactListMember
from app.sms.phone import normalize, is_valid
from datetime import datetime
from typing import List, Optional
import logging

logger = logging.getLogger("contacts")


# ─── Upsert ─────────────────────────────────────────────────────────────────

def upsert_contact(db: Session, phone: str, full_name: str = None, email: str = None,
                   source: str = None, external_ref: str = None,
                   attributes: dict = None, commit: bool = True) -> Optional[Contact]:
    """Create or update a contact keyed on the normalized phone number.

    Returns None for unusable numbers rather than storing junk — a list full of
    malformed numbers turns into a list full of paid-for failures later.
    """
    normalized = normalize(phone)
    if not normalized or not is_valid(normalized):
        return None

    contact = db.query(Contact).filter(Contact.phone == normalized).first()
    if contact:
        # Only fill gaps; never let a sparse import blank out good data.
        if full_name and not contact.full_name:
            contact.full_name = full_name
        if email and not contact.email:
            contact.email = email
        if attributes:
            merged = dict(contact.attributes or {})
            merged.update(attributes)
            contact.attributes = merged
        contact.updated_at = datetime.now().isoformat()
    else:
        contact = Contact(
            phone=normalized,
            full_name=full_name,
            email=email,
            source=source,
            external_ref=external_ref,
            attributes=attributes or {},
            created_at=datetime.now().isoformat(),
        )
        db.add(contact)

    if commit:
        db.commit()
        db.refresh(contact)
    return contact


# ─── Lists ──────────────────────────────────────────────────────────────────

def get_or_create_list(db: Session, name: str, description: str = None,
                       source: str = None) -> ContactList:
    row = db.query(ContactList).filter(ContactList.name == name).first()
    if row:
        return row
    row = ContactList(name=name, description=description, source=source)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def add_to_list(db: Session, list_id: int, contact_id: int, commit: bool = True) -> bool:
    exists = db.query(ContactListMember).filter(
        ContactListMember.list_id == list_id,
        ContactListMember.contact_id == contact_id,
    ).first()
    if exists:
        return False
    db.add(ContactListMember(list_id=list_id, contact_id=contact_id))
    if commit:
        db.commit()
    return True


def list_summaries(db: Session) -> List[dict]:
    """Every audience a picker can offer: 'all', each active category, each list.

    Categories come before lists because that is the client's primary axis — he
    picks tonight's niche, not a historical upload. Each carries a live count so
    a dropdown never shows a stale number.
    """
    rows = (db.query(ContactList.id, ContactList.name, func.count(ContactListMember.id))
            .outerjoin(ContactListMember, ContactListMember.list_id == ContactList.id)
            .group_by(ContactList.id, ContactList.name)
            .order_by(ContactList.name)
            .all())

    # Active contacts only, so the count matches what a send would actually
    # reach. The outer join keeps an empty category in the list rather than
    # dropping it — "Estates: 0" is information; a missing row is confusing.
    category_rows = (
        db.query(Category.slug, Category.label, func.count(Contact.id))
        .outerjoin(ContactCategory, ContactCategory.category_id == Category.id)
        .outerjoin(Contact, and_(Contact.id == ContactCategory.contact_id,
                                 Contact.is_active == 1))
        .filter(Category.is_active == 1)
        .group_by(Category.id, Category.slug, Category.label)
        .order_by(Category.sort_order, Category.label)
        .all()
    )

    total_active = db.query(Contact).filter(Contact.is_active == 1).count()

    audiences = [{
        "selector": "all",
        "label": "All contacts",
        "count": total_active,
        "kind": "all",
    }]
    audiences += [{
        "selector": f"category:{slug}",
        "label": label,
        "count": count,
        "kind": "category",
    } for slug, label, count in category_rows]
    audiences += [{
        "selector": f"list:{list_id}",
        "label": name,
        "count": count,
        "kind": "list",
    } for list_id, name, count in rows]
    return audiences


# ─── Audience resolution ────────────────────────────────────────────────────

def _category_ids_for_slugs(db: Session, slugs: List[str]) -> List[int]:
    """Slugs to ids, raising on the first one that does not exist."""
    found = {slug: cid for cid, slug in
             db.query(Category.id, Category.slug).filter(Category.slug.in_(slugs)).all()}
    missing = [s for s in slugs if s not in found]
    if missing:
        raise ValueError(
            f"Unknown category slug: {', '.join(missing)}. "
            "A typo must not resolve to an empty audience."
        )
    return [found[s] for s in slugs]


def _term_ids_query(db: Session, term: str):
    """A query over Contact.id for one selector term, active contacts only.

    Returned as a query rather than a Python set so an intersection stays in
    SQL. Materializing 50k ids to feed them back through an IN clause is both
    slower and, on SQLite, past the bound-parameter limit.
    """
    term = term.strip()

    if term == "all":
        return db.query(Contact.id).filter(Contact.is_active == 1)

    if term.startswith("list:"):
        return (db.query(Contact.id)
                .join(ContactListMember, ContactListMember.contact_id == Contact.id)
                .filter(ContactListMember.list_id == _int_arg(term),
                        Contact.is_active == 1))

    if term.startswith("source:"):
        return db.query(Contact.id).filter(
            Contact.source == term.split(":", 1)[1], Contact.is_active == 1
        )

    if term.startswith("category:"):
        slugs = [s.strip() for s in term.split(":", 1)[1].split(",") if s.strip()]
        if not slugs:
            raise ValueError(f"Audience selector {term!r} names no category")
        ids = _category_ids_for_slugs(db, slugs)
        # IN across several categories is the union; the outer query never
        # joins, so a contact in two of them still comes back once.
        return (db.query(Contact.id)
                .join(ContactCategory, ContactCategory.contact_id == Contact.id)
                .filter(ContactCategory.category_id.in_(ids), Contact.is_active == 1))

    raise ValueError(f"Unknown audience selector: {term!r}")


def _int_arg(term: str) -> int:
    raw = term.split(":", 1)[1]
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"Audience selector {term!r} needs a numeric id, got {raw!r}")


def _split_terms(selector: str) -> List[str]:
    terms = [t for t in (selector or "").split("&")]
    if len(terms) > 2:
        raise ValueError(
            f"Audience selector {selector!r} has more than one '&'. Only one "
            "intersection is supported — write the union on the left, e.g. "
            "'category:food_service,equipment&list:12'."
        )
    return terms


def resolve_audience(db: Session, selector: str) -> List[Contact]:
    """Turn an audience selector into contacts.

    Deduplication is guaranteed by Contact.phone being unique, and reinforced
    here: each term contributes an IN sub-select rather than a join, so a
    contact in two categories and on three lists is still a single row.
    """
    terms = _split_terms((selector or "").strip())

    query = db.query(Contact).filter(Contact.is_active == 1)
    for term in terms:
        query = query.filter(Contact.id.in_(_term_ids_query(db, term).scalar_subquery()))
    return query.order_by(Contact.id).all()


def _term_label(db: Session, term: str) -> str:
    term = term.strip()
    if term == "all":
        return "All contacts"
    if term.startswith("list:"):
        try:
            row = db.get(ContactList, _int_arg(term))
        except ValueError:
            return term
        return row.name if row else term
    if term.startswith("source:"):
        return f"Source: {term.split(':', 1)[1]}"
    if term.startswith("category:"):
        slugs = [s.strip() for s in term.split(":", 1)[1].split(",") if s.strip()]
        rows = {r.slug: r.label for r in
                db.query(Category).filter(Category.slug.in_(slugs)).all()}
        return " + ".join(rows.get(s, s) for s in slugs)
    return term


def audience_count(db: Session, selector: str) -> int:
    """How many contacts a selector resolves to, without materializing them.

    resolve_audience() returns objects because the send loop needs them. A
    screen that only wants the number must not pay for 50,000 hydrated rows to
    call len() on them — that is the difference between a dashboard that opens
    instantly and one that appears to hang.
    """
    terms = _split_terms((selector or "").strip())
    query = db.query(func.count(Contact.id)).filter(Contact.is_active == 1)
    for term in terms:
        query = query.filter(Contact.id.in_(_term_ids_query(db, term).scalar_subquery()))
    return query.scalar() or 0


def audience_label(db: Session, selector: str) -> str:
    """Human wording for a selector: "Food Service + Equipment ∩ Aug 22 preview".

    Never raises. This renders in page headers and campaign rows, and a label
    helper that throws turns a typo in a saved selector into a 500 on a screen
    that was only trying to describe it.
    """
    selector = (selector or "").strip()
    try:
        terms = _split_terms(selector)
    except ValueError:
        return selector
    return " ∩ ".join(_term_label(db, t) for t in terms)

