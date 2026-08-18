"""Contacts, lists and audience resolution.

Audience selectors are strings so they can round-trip through a form field and
be stored on the campaign:

    "all"        every active contact
    "list:<id>"  members of one list
    "source:csv" everything a given ContactSource produced
"""

from sqlalchemy.orm import Session
from sqlalchemy import func
from app.models.contact import Contact
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
    """Every list plus the synthetic 'all' audience, for audience dropdowns."""
    rows = (db.query(ContactList.id, ContactList.name, func.count(ContactListMember.id))
            .outerjoin(ContactListMember, ContactListMember.list_id == ContactList.id)
            .group_by(ContactList.id, ContactList.name)
            .order_by(ContactList.name)
            .all())

    total_active = db.query(Contact).filter(Contact.is_active == 1).count()

    audiences = [{
        "selector": "all",
        "label": "All contacts",
        "count": total_active,
    }]
    audiences += [{
        "selector": f"list:{list_id}",
        "label": name,
        "count": count,
    } for list_id, name, count in rows]
    return audiences


# ─── Audience resolution ────────────────────────────────────────────────────

def resolve_audience(db: Session, selector: str) -> List[Contact]:
    """Turn an audience selector into contacts.

    Deduplication is guaranteed by Contact.phone being unique, so a contact on
    three lists is still texted once.
    """
    selector = (selector or "").strip()

    if selector == "all":
        return db.query(Contact).filter(Contact.is_active == 1).all()

    if selector.startswith("list:"):
        list_id = int(selector.split(":", 1)[1])
        return (db.query(Contact)
                .join(ContactListMember, ContactListMember.contact_id == Contact.id)
                .filter(ContactListMember.list_id == list_id, Contact.is_active == 1)
                .all())

    if selector.startswith("source:"):
        source = selector.split(":", 1)[1]
        return db.query(Contact).filter(
            Contact.source == source, Contact.is_active == 1
        ).all()

    raise ValueError(f"Unknown audience selector: {selector!r}")


def audience_label(db: Session, selector: str) -> str:
    if selector == "all":
        return "All contacts"
    if selector.startswith("list:"):
        row = db.get(ContactList, int(selector.split(":", 1)[1]))
        return row.name if row else selector
    if selector.startswith("source:"):
        return f"Source: {selector.split(':', 1)[1]}"
    return selector
