"""The single path from any source into the prospect tables.

Split out of `prospect_service.py` when session P2 added the exclusion check and
the already-a-contact check and the file crossed the 500-line rule. The seam is
the one P1 already used between `prospect_service` (writes) and `prospect_queue`
(reads), one level finer: **this module is ingestion, `prospect_service` is what
a human does to a prospect afterwards.**

## Everything a source is not allowed to hold lives here

A source yields records. It never normalises a number, never dedups, never
decides who is a buyer and never writes a row — because a source that did would
have taken the dedup guarantee, the permanent-rejection check, the exclusion
list and the provenance requirement into itself, where the next source will
inherit none of them. `ProspectSource.ingest()` calls `record_prospect()` and
nothing else does.

That is why the exclusion list is enforced *here* rather than in
`google_places.py`. `CLAUDE.md`: a guard with one call site is a guard on one
path, and `should_auto_block()` cost this project 2,526 landlines by being
wired into the submission failure path only. P3's registry sources get the
never-prospect list for free, without their author having heard of it.

## The order of the checks is the order of their cost

A malformed number, a missing rationale and an excluded business name are all
decided without touching the database. The suppression check comes before
anything is written, so a rejected number never gets a row at all. The
already-a-contact check is last of the cheap ones because it is a query, and it
comes **before** the insert because a prospect nobody will ever promote is a row
in the review queue and $0.0025 of screening.

## Six outcomes, and none of them is a spelling of another

`RECORD_OUTCOMES` grew from four to six in P2. Each has its own counter on
`scrape_jobs` on purpose: a term whose output is mostly other auction houses and
a term whose output is mostly businesses the client already has are two
different diagnoses with two different remedies, and folding either into
`invalid` is the overloaded-column mistake `CLAUDE.md` opens with. The job row
is how a search term is judged, so it has to be able to say which kind of
nothing a search produced.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.models.category import Category
from app.models.contact import Contact
from app.models.prospect import Prospect, ProspectRejection, ProspectSighting
from app.services.prospect_service import rescore
from app.sms.phone import normalize, is_valid
from app.sources.exclusions import excluded_reason
import logging

logger = logging.getLogger("prospects")

# What `record_prospect()` can answer. `prospect_base.ProspectSource.ingest()`
# counts these by name and refuses anything else, so adding an outcome here
# without adding a counter there fails loudly rather than reporting zero.
RECORD_OUTCOMES = ("created", "corroborated", "suppressed", "excluded",
                   "known", "invalid")


def _category_id_for_slug(db: Session, slug: Optional[str]) -> Optional[int]:
    """Resolve a source's category slug, or None.

    An unrecognised slug is logged and dropped rather than refused. The taxonomy
    is wider than the categories currently seeded — Marine and Seashells have no
    rows — and a source that finds a real buyer under a category this box has
    not created should still land in the queue, where a human picks the category
    at promote time anyway.
    """
    if not slug:
        return None
    row = db.query(Category).filter(Category.slug == slug).first()
    if row is None:
        logger.info("Prospect source named category %r, which does not exist "
                    "on this box; leaving the prospect uncategorised", slug)
        return None
    return row.id


def is_suppressed(db: Session, phone: str) -> bool:
    """Has a human already said no to this number, from any source, ever?"""
    normalized = normalize(phone)
    if not normalized:
        return False
    return (db.query(ProspectRejection)
            .filter(ProspectRejection.phone == normalized).first() is not None)


def is_known_contact(db: Session, phone: str) -> bool:
    """Is this number already on the client's list?

    Matched whether or not the contact is active. An inactive contact is still a
    number we hold, with a history and possibly a reason it was deactivated, and
    rediscovering it as a fresh prospect would put it back in front of a
    reviewer as though nobody had ever decided anything about it.
    """
    normalized = normalize(phone)
    if not normalized:
        return False
    return (db.query(Contact.id)
            .filter(Contact.phone == normalized).first() is not None)


def record_prospect(db: Session, record, source_name: str, job=None) -> str:
    """Persist one record from a source. Returns a member of RECORD_OUTCOMES."""
    phone = normalize(getattr(record, "phone", "") or "")
    rationale = (getattr(record, "buyer_rationale", "") or "").strip()
    term = (getattr(record, "search_term", "") or "").strip()
    source_url = (getattr(record, "source_url", "") or "").strip()
    business_name = getattr(record, "business_name", None)

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

    # The other half of the same rule, and the reason it is here rather than in
    # a source: an auction house, an estate liquidator or an appraiser is not a
    # buyer however good the search term was, and every source that ever gets
    # written has to be stopped from finding them.
    excluded = excluded_reason(business_name)
    if excluded is not None:
        logger.info("[%s] %r excluded before it became a prospect — %s",
                    source_name, business_name, excluded)
        return _count(job, "records_excluded", "excluded")

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

    # Checked here, on the create path only, and after the corroboration branch
    # above. A prospect the client has already promoted IS a contact, and the
    # sighting is still worth writing — it is the provenance of a number he
    # already has. What this stops is the other case: a business imported from
    # a CSV years ago, rediscovered by a search, put in front of a reviewer as
    # though it were new, and screened for $0.0025 to learn something the
    # contact row already implies.
    if is_known_contact(db, phone):
        return _count(job, "records_known", "known")

    prospect = Prospect(
        phone=phone,
        business_name=(business_name or None),
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
