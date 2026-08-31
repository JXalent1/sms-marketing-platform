"""Prospects — the holding pen between a scraper and the textable list.

Nothing a source produces becomes a `Contact` on its own. It lands here, gets
screened for line type, gets scored, and a human promotes it. That order is the
whole point: the reference system's scraper wrote straight into the contact
table, and the client paid to text every landline it found for the next year.

## The one rule these tables exist to enforce

**We are looking for people who BUY at his auctions, never people who sell into
them.** A consignor on the list costs money to text, dilutes the audience and
puts a competitor on the client's own marketing channel. The plan of record
enforces that in three places rather than trusting it once, and two of the three
are columns here:

  1. `search_term` and `buyer_rationale` are **non-nullable**. A term with no
     written claim about why those people would raise a paddle does not ship,
     and `prospect_base.ProspectSource.ingest()` refuses a record without one.
  2. `buyer_rationale` is carried on the row rather than looked up from a
     taxonomy, so the review queue can show the reviewer the claim they are
     agreeing or disagreeing with — "would this person bid?", not "is this a
     real business?".
  3. The third is `ProspectRejection` below: `seller_or_consignor` and
     `competitor` are first-class reasons and both suppress permanently.

## Why the provenance columns are non-nullable

`source_url`, `scraped_at` and `raw_payload` are how a bad record gets traced to
the search that produced it instead of argued about. A nullable provenance
column is a provenance column that is empty on exactly the rows somebody
disputes, so they are required at insert and never updated afterwards.

## Why `phone` is unique, and what corroboration is

Same rule as `contacts.phone`: the number is the identity, normalised to E.164
once at the edge. A second search finding the same business does not create a
second prospect — it writes a `ProspectSighting`, which is what "found by two
different searches" means in the scoring. Storing that as a counter and a JSON
list of terms on this table would be the overloaded column this codebase opens
its lessons file with.

The first sighting's provenance is *also* on `Prospect`, deliberately
duplicated. That is what lets the columns above be non-nullable — a prospect
cannot exist without provenance — and it keeps the review queue's main query off
a join. `prospect_service` is the only writer of either, and it writes both in
one place.
"""

from sqlalchemy import (
    Column, Integer, String, Text, Float, ForeignKey, Index, UniqueConstraint,
    JSON,
)
from app.core.database import Base

# Where a prospect is in its life. Three states and no more: everything else a
# screen wants to say — promote-eligible, screened, corroborated — is derived
# from the line type, the sightings and the status together, and deriving it is
# cheaper than a fourth state two writers have to agree about.
PROSPECT_STATUSES = ("pending", "promoted", "rejected")

# Why a human said no. `seller_or_consignor` and `competitor` are first-class
# and listed first because they are the reason this queue exists — see the
# module docstring. The rest are ordinary review outcomes.
#
# Every one of them suppresses permanently. There is no "reject softly": a
# reviewer who looked at a row and said no has spent the only expensive resource
# in this pipeline, and asking them the same question again next month is how a
# review queue stops being reviewed.
REJECT_REASONS = (
    "seller_or_consignor",
    "competitor",
    "not_a_buyer",
    "wrong_category",
    "bad_number",
    "other",
)

# The two that mean "this search found the wrong side of the room". Kept as a
# named subset rather than re-listed at each call site, because the per-term
# breakdown and any future auto-flag must agree about which reasons condemn a
# search term as opposed to merely condemning one business.
WRONG_SIDE_REASONS = ("seller_or_consignor", "competitor")


class Prospect(Base):
    __tablename__ = "prospects"

    id = Column(Integer, primary_key=True, index=True)

    # Identity, E.164, normalised by prospect_service before it gets here.
    phone = Column(String(20), unique=True, nullable=False, index=True)

    business_name = Column(String(255), nullable=True)
    address = Column(String(255), nullable=True)

    # The category this prospect is *proposed* for. Nullable because a source
    # may not be able to tell, and a wrong guess is worse than no guess — the
    # reviewer picks the category at promote time either way.
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True)
    # 0..1, from the source. NULL means the source did not score it, which is a
    # different thing from scoring it zero — same distinction ContactCategory
    # draws between a human's certainty and a model's confidence.
    category_confidence = Column(Float, nullable=True)

    # Miles from the saleroom, supplied by the source. NULL means unknown, which
    # scores zero rather than being treated as near — see prospect_scoring.
    distance_miles = Column(Float, nullable=True)

    # ─── Provenance. Written once at creation, never updated. ───────────────
    source = Column(String(50), nullable=False)
    source_url = Column(Text, nullable=False)
    scraped_at = Column(String(50), nullable=False)
    raw_payload = Column(JSON, nullable=False)
    search_term = Column(String(255), nullable=False)
    buyer_rationale = Column(Text, nullable=False)

    # ─── Review state ───────────────────────────────────────────────────────
    status = Column(String(20), nullable=False, default="pending")

    # 0..100, recomputed by prospect_scoring whenever one of its inputs changes:
    # a lookup landing, or a new sighting. Stored rather than computed per query
    # because the queue's whole job is to be sorted by it, and sorting 10,000
    # rows in Python to render 50 of them is the N+1 one level up.
    score = Column(Integer, nullable=False, default=0)

    # Distinct sources that have produced this prospect. Maintained in exactly
    # one place — prospect_service._add_sighting() — alongside the sighting row
    # it counts. Denormalised because scoring reads it on every rescore.
    source_count = Column(Integer, nullable=False, default=1)

    # NULL until promoted. This is the link the acceptance criterion names: it
    # is how a contact is traced back to the search that found them, months
    # later, when somebody asks where a number came from.
    promoted_contact_id = Column(Integer, ForeignKey("contacts.id"), nullable=True)
    promoted_at = Column(String(50), nullable=True)
    rejected_at = Column(String(50), nullable=True)

    created_at = Column(String(50), nullable=False)
    updated_at = Column(String(50), nullable=True)

    __table_args__ = (
        # The review queue's ordering. Status first because every queue query
        # filters on it before it sorts.
        Index("idx_prospects_status_score", "status", "score"),
        Index("idx_prospects_category", "category_id"),
        # The per-term breakdown groups on this.
        Index("idx_prospects_search_term", "search_term"),
    )


class ProspectSighting(Base):
    """One search that produced one prospect. Including the first.

    "Multi-source corroboration" in the scoring is `COUNT(DISTINCT source)` over
    these rows: a business a food-truck search and a caterer search both found is
    more likely to be real and more likely to belong in Food Service than one
    found once.

    The unique constraint is the dedup guarantee for that count, mirroring what
    `uq_contact_category` does for tagging: re-running the same search on the
    same source cannot make a prospect look twice-corroborated. Without it, a
    nightly job would inflate every score it touched and the queue's ordering
    would drift toward whatever ran most often rather than whatever is best.
    """

    __tablename__ = "prospect_sightings"

    id = Column(Integer, primary_key=True, index=True)
    prospect_id = Column(Integer, ForeignKey("prospects.id", ondelete="CASCADE"),
                         nullable=False)

    source = Column(String(50), nullable=False)
    search_term = Column(String(255), nullable=False)
    buyer_rationale = Column(Text, nullable=False)
    source_url = Column(Text, nullable=False)
    scraped_at = Column(String(50), nullable=False)
    raw_payload = Column(JSON, nullable=True)

    job_id = Column(Integer, ForeignKey("scrape_jobs.id"), nullable=True)

    __table_args__ = (
        UniqueConstraint("prospect_id", "source", "search_term",
                         name="uq_prospect_sighting"),
        Index("idx_prospect_sightings_prospect", "prospect_id"),
    )


class ProspectRejection(Base):
    """A number a human has said no to. Permanent, and keyed on the phone.

    A separate table rather than `prospects.status = 'rejected'` alone, for the
    same reason `blocked_numbers` is separate from `contacts.is_active`: this is
    the record, and the record has to outlive whatever row happened to carry it.
    Ingestion checks *this* table before it creates anything, so the same
    business arriving from a different source next month — different name,
    different payload, different search term, same number — is suppressed on
    arrival rather than resurfacing in a queue somebody already emptied.

    `prospects.status` is the queue's view of the same event and is written in
    the same transaction. Neither is inferred from the other, and
    `prospect_service.reject()` is the only writer of both.

    Never hard-deleted. Un-rejecting is not a feature: the cost of a mistaken
    rejection is one business that has to be re-found, and the cost of a
    reversible one is a reviewer's afternoon repeated every month.
    """

    __tablename__ = "prospect_rejections"

    id = Column(Integer, primary_key=True, index=True)
    phone = Column(String(20), unique=True, nullable=False, index=True)
    prospect_id = Column(Integer, ForeignKey("prospects.id"), nullable=False)
    reason = Column(String(40), nullable=False)         # one of REJECT_REASONS
    rejected_at = Column(String(50), nullable=False)
    notes = Column(Text, nullable=True)

    __table_args__ = (
        Index("idx_prospect_rejections_reason", "reason"),
    )
