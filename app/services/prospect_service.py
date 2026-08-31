"""Everything that writes to the prospect tables.

Three operations, and each one is a guard the sources are not allowed to hold:
`record_prospect()` persists what a source found, `reject()` suppresses a number
permanently, and `promote()` turns a prospect into a contact.

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
from app.models.prospect import (
    Prospect, ProspectRejection, ProspectSighting, REJECT_REASONS,
)
from app.services import blocklist_service, category_service, contact_service
from app.services import lookup_service, prospect_scoring
from app.sms.phone import normalize, is_valid
import logging

logger = logging.getLogger("prospects")

# What `record_prospect()` can answer. `prospect_base.ProspectSource.ingest()`
# counts these by name and refuses anything else, so adding an outcome here
# without adding a counter there fails loudly rather than reporting zero.
RECORD_OUTCOMES = ("created", "corroborated", "suppressed", "invalid")

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


# ─── Ingestion ──────────────────────────────────────────────────────────────

def _category_id_for_slug(db: Session, slug: Optional[str]) -> Optional[int]:
    """Resolve a source's category slug, or None.

    An unrecognised slug is logged and dropped rather than refused. The taxonomy
    in the plan of record is wider than the categories currently seeded — Marine
    has no row yet — and a source that finds a real buyer under a category this
    box has not created should still land in the queue, where a human picks the
    category at promote time anyway.
    """
    if not slug:
        return None
    row = db.query(Category).filter(Category.slug == slug).first()
    if row is None:
        logger.info("Prospect source named category %r, which does not exist "
                    "on this box; leaving the prospect uncategorised", slug)
        return None
    return row.id


def _slug_for_category_id(db: Session, category_id: Optional[int]) -> Optional[str]:
    if category_id is None:
        return None
    row = db.get(Category, category_id)
    return row.slug if row else None


def is_suppressed(db: Session, phone: str) -> bool:
    """Has a human already said no to this number, from any source, ever?"""
    normalized = normalize(phone)
    if not normalized:
        return False
    return (db.query(ProspectRejection)
            .filter(ProspectRejection.phone == normalized).first() is not None)


def record_prospect(db: Session, record, source_name: str, job=None) -> str:
    """Persist one record from a source. Returns a member of RECORD_OUTCOMES.

    The order of the checks is the order of their cost: a malformed number and a
    missing rationale are decided without a query, and the suppression check
    comes before anything is written so a rejected number never gets a row at
    all.
    """
    phone = normalize(getattr(record, "phone", "") or "")
    rationale = (getattr(record, "buyer_rationale", "") or "").strip()
    term = (getattr(record, "search_term", "") or "").strip()
    source_url = (getattr(record, "source_url", "") or "").strip()

    if not phone or not is_valid(phone):
        return _count(job, "records_invalid", "invalid")

    # The first of the three places "buyers, never sellers" is enforced. A term
    # with no written claim about why these people would bid cannot reach a
    # reviewer, because a reviewer cannot agree or disagree with a blank.
    if not rationale or not term or not source_url:
        logger.info("[%s] record for %s dropped: %s", source_name, phone,
                    "no buyer rationale" if not rationale else
                    "no search term" if not term else "no source url")
        return _count(job, "records_invalid", "invalid")

    if is_suppressed(db, phone):
        return _count(job, "records_suppressed", "suppressed")

    now = datetime.now().isoformat()
    existing = db.query(Prospect).filter(Prospect.phone == phone).first()

    if existing is not None:
        _add_sighting(db, existing, record, source_name, term, rationale,
                      source_url, now, job)
        rescore(db, existing, commit=False)
        existing.updated_at = now
        db.commit()
        return _count(job, "prospects_corroborated", "corroborated")

    prospect = Prospect(
        phone=phone,
        business_name=(getattr(record, "business_name", None) or None),
        address=(getattr(record, "address", None) or None),
        category_id=_category_id_for_slug(db, getattr(record, "category_slug", None)),
        category_confidence=getattr(record, "category_confidence", None),
        distance_miles=getattr(record, "distance_miles", None),
        source=source_name,
        source_url=source_url,
        scraped_at=now,
        raw_payload=dict(getattr(record, "raw_payload", None) or {}),
        search_term=term,
        buyer_rationale=rationale,
        status="pending",
        score=0,
        source_count=1,
        created_at=now,
    )
    db.add(prospect)
    db.flush()                      # id, for the sighting row

    _add_sighting(db, prospect, record, source_name, term, rationale,
                  source_url, now, job, count_source=False)
    rescore(db, prospect, commit=False)
    db.commit()
    return _count(job, "prospects_created", "created")


def _add_sighting(db: Session, prospect: Prospect, record, source_name: str,
                  term: str, rationale: str, source_url: str, now: str,
                  job=None, count_source: bool = True) -> bool:
    """Record that this source and this term found this prospect.

    The (prospect, source, term) uniqueness is checked here rather than left to
    the constraint, because a re-run of the same nightly search is the normal
    case and an IntegrityError per row is not an error path worth having.

    `source_count` is maintained here, next to the row it counts, and nowhere
    else. It is the denormalised `COUNT(DISTINCT source)` the scorer reads on
    every rescore; two writers for it would put the queue's ordering and its
    "found by 2 searches" column out of step with each other.
    """
    exists = (db.query(ProspectSighting)
              .filter(ProspectSighting.prospect_id == prospect.id,
                      ProspectSighting.source == source_name,
                      ProspectSighting.search_term == term)
              .first())
    if exists is not None:
        return False

    # Read the distinct sources *before* the new row is added. SQLAlchemy
    # autoflushes on query, so asking afterwards would flush the sighting being
    # inserted, find its own source in the answer, and conclude the source was
    # already known — leaving `source_count` at 1 forever and the corroboration
    # term of the score permanently dead.
    already = {row[0] for row in
               db.query(ProspectSighting.source)
               .filter(ProspectSighting.prospect_id == prospect.id)
               .distinct().all()} if count_source else set()

    db.add(ProspectSighting(
        prospect_id=prospect.id,
        source=source_name,
        search_term=term,
        buyer_rationale=rationale,
        source_url=source_url,
        scraped_at=now,
        raw_payload=dict(getattr(record, "raw_payload", None) or {}),
        job_id=getattr(job, "id", None),
    ))

    if count_source and source_name not in already:
        prospect.source_count = (prospect.source_count or 1) + 1
    return True


def _count(job, field: str, outcome: str) -> str:
    """Bump a job counter and answer with the outcome name.

    Folded together because every return in `record_prospect()` does both, and
    the one that forgot the counter would report a run that found nothing. The
    outcome is passed rather than derived from the field name: two names for one
    thing is how they come to disagree.
    """
    if job is not None:
        setattr(job, field, (getattr(job, field, 0) or 0) + 1)
    return outcome


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
