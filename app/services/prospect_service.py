"""What a human does to a prospect: score it, reject it, promote it.

Two operations and the scorer's single writer. `reject()` suppresses a number
permanently and `promote()` turns a prospect into a contact.

**Ingestion moved to `prospect_ingest.py`** when P2 added the exclusion check
and the already-a-contact check and this file crossed the 500-line rule. The
seam is the one P1 already drew between the writes here and the reads in
`prospect_queue.py`, one level finer: that module is what a *source* does, this
one is what a *reviewer* does.

## Promotion runs the same guards as every other way in

A prospect is not a special case. `promote()` normalises to E.164, refuses a
number on the blocklist and refuses one an opt-out covers, exactly as
`POST /api/contacts` does for a number typed by hand and as the CSV import does
for a number in a file. The one thing it adds is the line-type gate.

The order of the refusals is deliberate. The blocklist is checked before the
gate, because "somebody asked us not to text them" is a fact about a person and
"this is a landline" is a fact about a wire, and when both are true the client
should be told the one that matters.

## `promoted_contact_id` is the audit trail

Six months from now somebody will ask where a number came from. That column,
plus the non-nullable provenance on the prospect, answers it: this contact came
from this prospect, which came from this search, which was justified by this
written claim about why they would bid. Nothing else in this application can
answer that question about a contact.

## Rejection is permanent, and it is permanent on the *number*

`reject()` writes both `prospect_rejections` (the record, keyed on the phone)
and `prospects.status` (the queue's view) in one transaction, and it is the only
writer of either. A number in that table never reaches the queue again from any
source, however differently the business is spelled next time.

That is the point. A reviewer who looked at a row and said "estate liquidator,
he sells to us" has spent the only expensive resource in this pipeline, and a
system that asks them again next month is a system that stops being used.
"""

from datetime import datetime
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.blocked_number import BlockedNumber
from app.models.category import Category
from app.models.prospect import Prospect, ProspectRejection, REJECT_REASONS
from app.services import blocklist_service, category_service, contact_service
from app.services import lookup_service, prospect_scoring
from app.sms.phone import is_valid
import logging

logger = logging.getLogger("prospects")

# ─── What a refusal says ────────────────────────────────────────────────────
#
# Cause, and the remedy that works on this object. A guard that refuses without
# explaining reads as a broken tool — decision 006 — and these are read by the
# client, on his screen, about his prospects. None of them names a carrier.

OPTED_OUT_REFUSAL = (
    "Somebody using this number asked not to be texted, so it was not added to "
    "your contacts. An opt-out is a permanent record and putting them back is a "
    "deliberate act: remove the number from Opt-outs first, which keeps the "
    "record of what changed and when."
)
UNREACHABLE_REFUSAL = (
    "This number is on your Opt-outs screen as unreachable — a carrier could "
    "not deliver to it — so it was not added. Texting it would fail and still "
    "be charged for. Remove it there if you know the number is good."
)
BLOCKED_REFUSAL = (
    "This number is on your Opt-outs list, so it was not added. Remove it there "
    "first if it should be contacted."
)
ALREADY_PROMOTED_REFUSAL = (
    "This prospect is already one of your contacts. Nothing was changed."
)
REJECTED_REFUSAL = (
    "This prospect was rejected, so it cannot be promoted. Rejections are "
    "permanent — the number is suppressed from every future search too."
)
PROMOTED_CANNOT_BE_REJECTED = (
    "This prospect is already one of your contacts, so rejecting it here would "
    "change nothing. Remove them on the Contacts screen, or add the number to "
    "Opt-outs if they asked not to be texted."
)
INVALID_PHONE_REFUSAL = "That is not a usable phone number, so nothing was added."
NO_CATEGORY_REFUSAL = "Pick the auction category these buyers belong to first."


# ─── Scoring ────────────────────────────────────────────────────────────────

def _slug_for_category_id(db: Session, category_id: Optional[int]) -> Optional[str]:
    if category_id is None:
        return None
    row = db.get(Category, category_id)
    return row.slug if row else None


def rescore(db: Session, prospect: Prospect, line_type: str = None,
            commit: bool = True) -> int:
    """Recompute and store one prospect's score.

    Called on every event that changes an input: a new sighting, and a line-type
    lookup landing. Storing the score is what lets the queue sort in SQL; this
    function is the single writer, so a stale score means somebody added a
    fourth input and did not call it.
    """
    if line_type is None:
        line_type = lookup_service.cached_line_types(
            db, [prospect.phone]).get(prospect.phone, "unknown")

    prospect.score = prospect_scoring.score(
        line_type=line_type,
        category_confidence=prospect.category_confidence,
        distance_miles=prospect.distance_miles,
        category_slug=_slug_for_category_id(db, prospect.category_id),
        source_count=prospect.source_count or 1,
    )
    if commit:
        db.commit()
    return prospect.score


# ─── Review outcomes ────────────────────────────────────────────────────────

def reject(db: Session, prospect_ids: List[int], reason: str,
           notes: str = None) -> dict:
    """Say no, permanently, to one or more prospects.

    Bulk by design — the queue's whole value is working through a screen of rows
    at a time — and idempotent: rejecting an already-rejected prospect is
    counted as unchanged rather than refused, because a double-click on a bulk
    action must not read as an error.
    """
    if reason not in REJECT_REASONS:
        raise ValueError(
            f"Unknown reject reason {reason!r}. Allowed: {', '.join(REJECT_REASONS)}")

    rows = _load(db, prospect_ids)
    now = datetime.now().isoformat()
    rejected, unchanged, refused = [], [], []

    for prospect in rows.values():
        if prospect.status == "promoted":
            refused.append({"prospect_id": prospect.id, "phone": prospect.phone,
                            "reason": PROMOTED_CANNOT_BE_REJECTED})
            continue
        if prospect.status == "rejected":
            unchanged.append(prospect.id)
            continue

        prospect.status = "rejected"
        prospect.rejected_at = now
        prospect.updated_at = now
        # The record, keyed on the number. Guarded rather than left to the
        # unique index: a prospect whose row was rejected under a previous id
        # would otherwise raise instead of being counted.
        if not db.query(ProspectRejection).filter(
                ProspectRejection.phone == prospect.phone).first():
            db.add(ProspectRejection(
                phone=prospect.phone, prospect_id=prospect.id, reason=reason,
                rejected_at=now, notes=notes,
            ))
        rejected.append(prospect.id)

    db.commit()
    missing = [i for i in prospect_ids if i not in rows]
    logger.info("Rejected %d prospect(s) as %s", len(rejected), reason)
    return {"rejected": rejected, "unchanged": unchanged, "refused": refused,
            "not_found": missing}


def promote(db: Session, prospect_ids: List[int], category_id: int) -> dict:
    """Turn prospects into contacts, tagged with the category the reviewer chose.

    Every read this needs is batched — one query for the prospects, one for the
    blocklist rows covering their numbers, one for their line types — because a
    bulk promote of a screenful is the normal case and a query per row is the
    N+1 that looks fine on six seeded prospects.
    """
    if category_id is None:
        return {"promoted": [], "refused": [], "not_found": [],
                "error": NO_CATEGORY_REFUSAL}
    category = db.get(Category, category_id)
    if category is None:
        return {"promoted": [], "refused": [], "not_found": [],
                "error": f"No category with id {category_id}."}

    rows = _load(db, prospect_ids)
    phones = [p.phone for p in rows.values()]
    blocked = {b.phone: b.reason for b in
               db.query(BlockedNumber).filter(BlockedNumber.phone.in_(phones)).all()} \
        if phones else {}
    line_types = lookup_service.cached_line_types(db, phones) if phones else {}

    now = datetime.now().isoformat()
    promoted, refused = [], []

    for prospect in rows.values():
        refusal = _promote_refusal(prospect, blocked, line_types)
        if refusal:
            refused.append({"prospect_id": prospect.id, "phone": prospect.phone,
                            "business_name": prospect.business_name,
                            "reason": refusal})
            continue

        contact = contact_service.upsert_contact(
            db, phone=prospect.phone,
            full_name=prospect.business_name,
            source="prospect",
            external_ref=str(prospect.id),
            # Where it came from, on the contact itself. A contact whose company
            # is only recoverable by joining back to a prospect row is a contact
            # the Contacts search cannot find by company name — which is how the
            # CSV import already stores it.
            attributes={"company": prospect.business_name} if prospect.business_name else None,
            commit=False,
        )
        if contact is None:
            refused.append({"prospect_id": prospect.id, "phone": prospect.phone,
                            "business_name": prospect.business_name,
                            "reason": INVALID_PHONE_REFUSAL})
            continue
        db.flush()

        # `manual`, not `inferred`. A human looked at the row, read the buyer
        # rationale and chose this category — that is the same act as typing it
        # on the Contacts screen. `inferred` carries a confidence, and storing
        # one for a decision a person made would make "certain" and "the scorer
        # was confident" indistinguishable, which is the distinction
        # ContactCategory exists to keep.
        category_service.tag_contact(db, contact.id, category_id,
                                     source="manual", commit=False)

        prospect.status = "promoted"
        prospect.promoted_contact_id = contact.id
        prospect.promoted_at = now
        prospect.updated_at = now
        promoted.append({"prospect_id": prospect.id, "contact_id": contact.id,
                         "phone": prospect.phone})

    db.commit()
    missing = [i for i in prospect_ids if i not in rows]
    logger.info("Promoted %d prospect(s) into category %s; %d refused",
                len(promoted), category.slug, len(refused))
    return {"promoted": promoted, "refused": refused, "not_found": missing,
            "category": {"id": category.id, "slug": category.slug,
                         "label": category.label}}


def _promote_refusal(prospect: Prospect, blocked: dict,
                     line_types: dict) -> Optional[str]:
    """The first reason this prospect may not become a contact, or None.

    Ordered by what the client most needs to hear. An opt-out outranks a
    landline because one is a person's request and the other is a property of a
    wire, and if both are true he should be told the one with a consequence.
    """
    if prospect.status == "promoted":
        return ALREADY_PROMOTED_REFUSAL
    if prospect.status == "rejected":
        return REJECTED_REFUSAL
    if not prospect.phone or not is_valid(prospect.phone):
        return INVALID_PHONE_REFUSAL

    reason = blocked.get(prospect.phone)
    if reason is not None:
        # `OPT_OUT_REASONS` is the one definition of "opt-out" in this codebase
        # — the Opt-outs headline and the dashboard tile both read it. Reusing
        # it here is what stops this screen from inventing a second one.
        if reason in blocklist_service.OPT_OUT_REASONS:
            return OPTED_OUT_REFUSAL
        if reason in blocklist_service.UNREACHABLE_REASONS:
            return UNREACHABLE_REFUSAL
        return BLOCKED_REFUSAL

    # The gate. `lookup_service` owns which line types pass and what the refusal
    # says, so the queue's "promote-eligible" filter and this check cannot drift.
    return lookup_service.refusal_for(line_types.get(prospect.phone))


def _load(db: Session, prospect_ids: List[int]) -> dict:
    ids = [int(i) for i in (prospect_ids or [])]
    if not ids:
        return {}
    return {row.id: row for row in
            db.query(Prospect).filter(Prospect.id.in_(ids)).all()}
