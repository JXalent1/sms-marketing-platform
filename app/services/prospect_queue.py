"""Everything the review queue reads.

Split from `prospect_service` on the write/read line. The writes are the guards —
suppression, the line-type gate, the blocklist — and they are worth reading on
their own; this is the screen.

## Everything here is counted in the database

The queue is paginated and the page is capped, so a figure tallied in the
browser from the rows it was given would under-report the day the list
overflows, and it would under-report it quietly. Every count below is a grouped
query. Same rule the Opt-outs headline learned the hard way.

## The buyer rationale is on every row, and that is the requirement

The reviewer is answering **"would this person bid?"**, not "is this a real
business?". Those questions have different answers for an estate liquidator with
a beautiful website. The rationale that justified the search term is carried on
the prospect row precisely so this query does not have to reach for a taxonomy
that may have changed since — the reviewer sees the claim as it was made when
the search ran.

## The per-term breakdown is how the system learns

A search term is a hypothesis: "food trucks buy used prep equipment". The
rejections are the evidence against it, and `seller_or_consignor` and
`competitor` are the two that mean the hypothesis was wrong in kind rather than
in detail — the term is finding people who sell *to* the auction house.

The flag is deliberately a claim about the *rejections*, not about the term's
whole output: a term can find 200 good buyers and 5 sellers and still be a good
term. Both denominators are returned so the screen can show the numbers under
the flag rather than asking anyone to trust it.
"""

from typing import Optional

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.category import Category
from app.models.prospect import (
    Prospect, ProspectRejection, WRONG_SIDE_REASONS,
)
from app.models.scrape import PhoneLookup
from app.services.lookup_service import PROMOTABLE_LINE_TYPES

PAGE_SIZE = 50
MAX_PAGE_SIZE = 200

# Columns a client may sort on, and the expression each maps to. A whitelist
# rather than a getattr on a query parameter: a sort key is user input reaching
# a query, and "score" is a column name while "line_type" is a joined one.
SORTS = {
    "score": Prospect.score,
    "business_name": Prospect.business_name,
    "distance": Prospect.distance_miles,
    "created": Prospect.created_at,
    "term": Prospect.search_term,
}
DEFAULT_SORT = "score"


def _screened(query):
    """Join the line-type cache, counting only rows that are an *answer*.

    `status == "ok"` is in the join condition rather than in a WHERE clause on
    purpose: a failed lookup must leave the prospect looking unscreened, not
    make it disappear from the queue. Same definition `cached_line_types()`
    uses, so the queue's idea of a line type and the promote guard's cannot
    differ.
    """
    return query.outerjoin(
        PhoneLookup, and_(PhoneLookup.phone == Prospect.phone,
                          PhoneLookup.status == "ok"))


def queue_page(db: Session, *, status: str = "pending",
               category_id: int = None, line_type: str = None,
               promote_eligible: bool = None, search_term: str = None,
               q: str = None, sort: str = DEFAULT_SORT,
               direction: str = "desc", page: int = 1,
               per_page: int = PAGE_SIZE) -> dict:
    """One page of the review queue, plus the total the page came out of."""
    per_page = max(1, min(int(per_page or PAGE_SIZE), MAX_PAGE_SIZE))
    page = max(1, int(page or 1))

    line_type_column = func.coalesce(PhoneLookup.line_type, "unknown")

    base = _screened(db.query(Prospect, line_type_column, Category.label,
                              Category.slug)) \
        .outerjoin(Category, Category.id == Prospect.category_id)

    filters = []
    if status and status != "all":
        filters.append(Prospect.status == status)
    if category_id is not None:
        filters.append(Prospect.category_id == category_id)
    if line_type:
        filters.append(line_type_column == line_type)
    if promote_eligible is True:
        filters.append(line_type_column.in_(PROMOTABLE_LINE_TYPES))
    elif promote_eligible is False:
        filters.append(line_type_column.notin_(PROMOTABLE_LINE_TYPES))
    if search_term:
        filters.append(Prospect.search_term == search_term)
    if q:
        needle = f"%{q.strip()}%"
        filters.append(or_(Prospect.business_name.ilike(needle),
                           Prospect.phone.ilike(needle),
                           Prospect.search_term.ilike(needle)))

    if filters:
        base = base.filter(*filters)

    # Counted with its own query rather than len() over the page — the page is
    # capped and the total is the number the screen puts in front of a person.
    total_query = _screened(db.query(func.count(Prospect.id)))
    if filters:
        total_query = total_query.filter(*filters)
    total = total_query.scalar() or 0

    column = SORTS.get(sort, SORTS[DEFAULT_SORT])
    ordered = column.desc() if (direction or "desc").lower() == "desc" else column.asc()
    # Tie-break on id so a page boundary is stable. Without it, two prospects on
    # the same score can swap places between page 1 and page 2 and one of them
    # is never shown.
    rows = (base.order_by(ordered, Prospect.id.asc())
            .offset((page - 1) * per_page).limit(per_page).all())

    return {
        "prospects": [_as_row(p, lt, label, slug) for p, lt, label, slug in rows],
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": max(1, (total + per_page - 1) // per_page),
    }


def _as_row(prospect: Prospect, line_type: str, category_label: Optional[str],
            category_slug: Optional[str]) -> dict:
    """One queue row.

    `raw_payload` is deliberately absent. It is retained on the row for tracing
    a disputed record, and it is whatever a third party returned — the one thing
    on this table nobody has vetted for what it contains or whom it names. It
    does not go out over the API by default.
    """
    return {
        "id": prospect.id,
        "phone": prospect.phone,
        "business_name": prospect.business_name,
        "address": prospect.address,
        "category_id": prospect.category_id,
        "category_label": category_label,
        "category_slug": category_slug,
        "category_confidence": prospect.category_confidence,
        "distance_miles": prospect.distance_miles,
        "line_type": line_type,
        "promote_eligible": line_type in PROMOTABLE_LINE_TYPES,
        "score": prospect.score,
        "status": prospect.status,
        "source": prospect.source,
        "source_url": prospect.source_url,
        "scraped_at": prospect.scraped_at,
        "source_count": prospect.source_count,
        # The two the reviewer is actually answering with.
        "search_term": prospect.search_term,
        "buyer_rationale": prospect.buyer_rationale,
        "promoted_contact_id": prospect.promoted_contact_id,
    }


def summary(db: Session) -> dict:
    """The tiles above the queue. One grouped query, plus one for the gate."""
    by_status = dict(db.query(Prospect.status, func.count(Prospect.id))
                     .group_by(Prospect.status).all())

    eligible = (_screened(db.query(func.count(Prospect.id)))
                .filter(Prospect.status == "pending",
                        func.coalesce(PhoneLookup.line_type, "unknown")
                        .in_(PROMOTABLE_LINE_TYPES))
                .scalar() or 0)
    unscreened = (_screened(db.query(func.count(Prospect.id)))
                  .filter(Prospect.status == "pending",
                          PhoneLookup.id.is_(None))
                  .scalar() or 0)

    return {
        "pending": by_status.get("pending", 0),
        "promoted": by_status.get("promoted", 0),
        "rejected": by_status.get("rejected", 0),
        "total": sum(by_status.values()),
        # Not "mobile": the gate's definition, from the one place that owns it.
        "promote_eligible": eligible,
        "unscreened": unscreened,
    }


def term_breakdown(db: Session) -> dict:
    """Per search term: what it found, and what the rejections say about it.

    Two grouped queries and no per-term loop. A breakdown that ran a query per
    term would be an N+1 keyed on the one thing this feature is designed to grow
    — the number of searches.
    """
    found = db.query(Prospect.search_term, Prospect.status,
                     func.count(Prospect.id)) \
        .group_by(Prospect.search_term, Prospect.status).all()

    rejections = (db.query(Prospect.search_term, ProspectRejection.reason,
                           func.count(ProspectRejection.id))
                  .join(Prospect, Prospect.id == ProspectRejection.prospect_id)
                  .group_by(Prospect.search_term, ProspectRejection.reason)
                  .all())

    # One rationale per term. A term ought to have exactly one — it is the claim
    # that justified writing it — so min() picks deterministically rather than
    # arbitrarily, and two spellings of a rationale for one term is a data
    # problem in the source rather than something to render twice.
    rationales = dict(db.query(Prospect.search_term,
                               func.min(Prospect.buyer_rationale))
                      .group_by(Prospect.search_term).all())

    terms = {}
    for term, status, count in found:
        entry = terms.setdefault(term, _empty_term(term, rationales.get(term)))
        entry["found"] += count
        if status in entry:
            entry[status] += count

    for term, reason, count in rejections:
        entry = terms.setdefault(term, _empty_term(term, rationales.get(term)))
        entry["reasons"][reason] = entry["reasons"].get(reason, 0) + count
        if reason in WRONG_SIDE_REASONS:
            entry["wrong_side"] += count

    minimum = settings.PROSPECT_TERM_FLAG_MIN_REJECTIONS
    threshold = settings.PROSPECT_TERM_FLAG_SHARE
    for entry in terms.values():
        rejected = entry["rejected"]
        entry["wrong_side_share"] = (entry["wrong_side"] / rejected) if rejected else 0.0
        entry["flagged"] = (rejected >= minimum
                            and entry["wrong_side_share"] >= threshold)

    ordered = sorted(terms.values(),
                     key=lambda e: (not e["flagged"], -e["wrong_side"], e["term"]))
    return {"terms": ordered,
            "flag_rule": {"min_rejections": minimum, "share": threshold}}


def _empty_term(term: str, rationale: Optional[str]) -> dict:
    return {"term": term, "buyer_rationale": rationale, "found": 0,
            "pending": 0, "promoted": 0, "rejected": 0,
            "wrong_side": 0, "reasons": {}}
