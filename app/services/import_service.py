"""Category-first CSV import: preview, commit, undo.

`csv_source.py` still owns parsing. This owns the flow, which is where the
client's risk actually is: an import that tags 4,000 people with the wrong niche
is not undone by deleting a list, and an import he cannot see the shape of
before committing is one he will not trust enough to run.

**The category is chosen before the file is parsed and is required at every
step.** A preview without one cannot answer "how many of these do I already
have?", which is the only question the preview exists to answer.

The counts
──────────
`rows` is what he sees in Excel. The rest partition the distinct usable numbers:

    rows = valid_phones + unusable + duplicates
    valid_phones = opted_out + already_in_category + existing_contacts + new_contacts

`duplicates` and `existing_contacts` are here because without them the report
does not add up and the client is left to guess which bucket absorbed the
difference. A row repeated in the file is not "unusable" — the number is fine,
it just imports once. A number we already hold but have not tagged with *this*
category is neither "already in category" nor a new contact — it gets the tag
without being created.

`valid_phones`, never "valid mobiles". We have no line-type data. Most scraped
business numbers are landlines and texting them fails and costs money, but the
honest fix is to buy line-type lookup, not to relabel a column we did not check.
"""

import logging
from datetime import date, datetime
from typing import Iterable, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.category import Category, ContactCategory
from app.models.contact import Contact
from app.models.contact_list import ContactList, ContactListMember
from app.models.sms_message import SMSMessage
from app.services import blocklist_service, category_service, contact_service
from app.sms.phone import is_valid, normalize
from app.sources.csv_source import CSVContactSource

logger = logging.getLogger("imports")

# SQLite's bound-parameter ceiling is the reason this exists. A 20,000-row file
# in one IN clause is not a slow query, it is an OperationalError.
_CHUNK = 500

# Which bucket a usable number falls in. Ordered by precedence: a blocklisted
# number is reported as opted out even if it is also already in the category,
# because "will be skipped" is the fact that changes what happens next.
BUCKETS = ("opted_out", "already_in_category", "existing_contacts", "new_contacts")


def _chunked(items: List, size: int = _CHUNK) -> Iterable[List]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _require_category(db: Session, category_id: Optional[int]) -> Category:
    if category_id is None:
        raise ValueError(
            "Choose a category before importing. An untagged upload is a list of "
            "people nobody can safely text about anything."
        )
    row = db.get(Category, int(category_id))
    if row is None:
        raise LookupError(f"No category with id {category_id}")
    return row


def _analyze(db: Session, content: bytes, category: Category) -> Tuple[dict, list]:
    """Parse and bucket, touching nothing.

    Returns (counts, plan). The plan is one entry per distinct usable number,
    in file order: (phone, record, bucket). commit() applies the same plan it
    reports, so the "actuals" it returns cannot drift from what it did.
    """
    total_rows = CSVContactSource.count_data_rows(content)

    seen = set()
    plan_records = []       # (normalized_phone, record)
    unusable = duplicates = 0
    yielded = 0

    for record in CSVContactSource().fetch(content=content):
        yielded += 1
        phone = normalize(record.phone)
        if not phone or not is_valid(phone):
            unusable += 1
            continue
        if phone in seen:
            duplicates += 1
            continue
        seen.add(phone)
        plan_records.append((phone, record))

    # fetch() skips a row with no phone cell at all, so those never reach the
    # loop above. They are still rows he can see in the file, and still rows we
    # cannot get a number from.
    unusable += total_rows - yielded

    phones = [p for p, _ in plan_records]
    existing = {}
    for chunk in _chunked(phones):
        for cid, phone in db.query(Contact.id, Contact.phone).filter(
            Contact.phone.in_(chunk)
        ).all():
            existing[phone] = cid

    tagged = set()
    existing_ids = list(existing.values())
    for chunk in _chunked(existing_ids):
        tagged.update(row[0] for row in db.query(ContactCategory.contact_id).filter(
            ContactCategory.category_id == category.id,
            ContactCategory.contact_id.in_(chunk),
        ).all())

    blocked = blocklist_service.load_blocked_set(db)

    counts = {b: 0 for b in BUCKETS}
    plan = []
    for phone, record in plan_records:
        if phone in blocked:
            bucket = "opted_out"
        elif phone not in existing:
            bucket = "new_contacts"
        elif existing[phone] in tagged:
            bucket = "already_in_category"
        else:
            bucket = "existing_contacts"
        counts[bucket] += 1
        plan.append((phone, record, bucket))

    counts.update({
        "category_id": category.id,
        "category_label": category.label,
        "rows": total_rows,
        "valid_phones": len(plan_records),
        "unusable": unusable,
        "duplicates": duplicates,
    })
    return counts, plan


def preview(db: Session, content: bytes, category_id: int) -> dict:
    """Counts and the detected column mapping. Writes nothing.

    Worth the extra click every time: a CSV whose phone column was not
    recognized imports zero rows and looks identical to a successful import of
    an empty file.
    """
    category = _require_category(db, category_id)
    counts, _ = _analyze(db, content, category)
    return {**counts, **CSVContactSource.preview(content)}


# ─── Commit ─────────────────────────────────────────────────────────────────

def _batch_list_name(db: Session, category: Category, on: date = None) -> str:
    """"{Label} — {YYYY-MM-DD} upload", with a counter if he uploads twice."""
    base = f"{category.label} — {(on or date.today()).isoformat()} upload"
    name, n = base, 1
    while db.query(ContactList).filter(ContactList.name == name).first():
        n += 1
        name = f"{base} ({n})"
    return name


def commit(db: Session, content: bytes, category_id: int) -> dict:
    """Import the file into one category. Returns the same counts as actuals.

    Opted-out numbers are skipped outright — not created, not tagged, not added
    to the batch list. An opt-out is not a filter applied at send time; it means
    we should not be building an audience around that person at all.
    """
    category = _require_category(db, category_id)
    counts, plan = _analyze(db, content, category)

    batch = ContactList(
        name=_batch_list_name(db, category),
        description=f"CSV import into {category.label}",
        source=CSVContactSource.name,
        category_id=category.id,
        created_at=datetime.now().isoformat(),
    )
    db.add(batch)
    db.flush()

    for phone, record, bucket in plan:
        if bucket == "opted_out":
            continue

        contact = contact_service.upsert_contact(
            db, phone=phone, full_name=record.full_name, email=record.email,
            source=CSVContactSource.name, external_ref=record.external_ref,
            attributes=record.attributes, commit=False,
        )
        db.flush()      # id is None until flush; the membership row needs it

        created_tag = category_service.tag_contact(
            db, contact.id, category.id, source="upload", commit=False,
        )
        db.add(ContactListMember(
            list_id=batch.id,
            contact_id=contact.id,
            created_contact=1 if bucket == "new_contacts" else 0,
            created_tag=1 if created_tag else 0,
            added_at=datetime.now().isoformat(),
        ))

    db.commit()
    logger.info(
        "import committed: list=%s category=%s new=%s tagged=%s skipped_opt_out=%s",
        batch.id, category.slug, counts["new_contacts"],
        counts["new_contacts"] + counts["existing_contacts"], counts["opted_out"],
    )
    return {**counts, "list_id": batch.id, "list_name": batch.name}


# ─── Undo ───────────────────────────────────────────────────────────────────

def undo(db: Session, list_id: int) -> dict:
    """Reverse exactly one import batch, identified by its list id.

    Subtractive, not destructive. Three things it deliberately will not do:

      - Remove a tag it did not add. Only rows this batch created
        (`created_tag`) *and* sourced from an upload are removed, so a tag a
        human added by hand survives, and so does an earlier import's.
      - Delete a contact with anything left. A contact goes only if this batch
        created it, it now belongs to no category and no other list, and no
        message was ever sent to it. An orphan contact is recoverable; a
        deleted one with message history is not.
      - Touch the blocklist. An opt-out outlives any import, including the
        import that first surfaced the number.
    """
    batch = db.get(ContactList, list_id)
    if batch is None:
        raise LookupError(f"No list with id {list_id}")
    if batch.category_id is None:
        raise ValueError(
            f"{batch.name!r} is not an import batch — undo only reverses an import. "
            "Delete the list instead; that removes membership and nothing else."
        )

    members = db.query(ContactListMember).filter(
        ContactListMember.list_id == list_id
    ).all()
    created_ids = sorted({m.contact_id for m in members if m.created_contact})
    tagged_ids = sorted({m.contact_id for m in members if m.created_tag})

    tags_removed = 0
    for chunk in _chunked(tagged_ids):
        tags_removed += db.query(ContactCategory).filter(
            ContactCategory.category_id == batch.category_id,
            ContactCategory.contact_id.in_(chunk),
            ContactCategory.source == "upload",
        ).delete(synchronize_session=False)

    memberships_removed = db.query(ContactListMember).filter(
        ContactListMember.list_id == list_id
    ).delete(synchronize_session=False)
    db.delete(batch)
    db.flush()

    deleted = kept = 0
    for contact_id in created_ids:
        if _still_referenced(db, contact_id):
            kept += 1
            continue
        contact = db.get(Contact, contact_id)
        if contact is not None:
            db.delete(contact)
            deleted += 1

    db.commit()
    result = {
        "list_id": list_id,
        "tags_removed": tags_removed,
        "memberships_removed": memberships_removed,
        "contacts_deleted": deleted,
        "contacts_kept": kept,
    }
    logger.info("import undone: %s", result)
    return result


def _still_referenced(db: Session, contact_id: int) -> bool:
    """True if anything would be lost by deleting this contact."""
    if db.query(ContactCategory).filter(
        ContactCategory.contact_id == contact_id
    ).first() is not None:
        return True
    if db.query(ContactListMember).filter(
        ContactListMember.contact_id == contact_id
    ).first() is not None:
        return True
    return db.query(SMSMessage).filter(
        SMSMessage.contact_id == contact_id
    ).first() is not None
