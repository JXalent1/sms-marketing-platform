"""Prospects API — the review queue and its two verbs.

HTTP only: validate, delegate, serialize. Every rule this screen enforces lives
in `prospect_service` (the writes), `prospect_queue` (the reads) and
`lookup_service` (the gate), because the same rules have to hold for a scheduled
job that never comes through a router.

## What must not cross this boundary

**`raw_payload`.** It is retained on every prospect so a disputed record can be
traced to the search that produced it, and it is whatever a third party returned
— unvetted, unbounded, and the one field on these tables nobody has read. It is
not in the queue serialization and it is not in the export.

**The job cost.** Screening a number costs us about a quarter of a cent. The
client is billed per segment and for nothing else, so `scrape_jobs.cost`,
`phone_lookups.cost` and `PROSPECT_LOOKUP_COST_PER_NUMBER` are ours on exactly
the footing `WHOLESALE_COST_PER_SEGMENT` is. None of them is read in this file,
and `agent/accept-P1.sh` asserts that by AST rather than by trusting this
paragraph.

**Any carrier's name.** Line types come back as `mobile`/`landline`/`unknown`
from `app/sms/lookup.py`, which is where the carrier vocabulary stops.
"""

import csv
import io

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import List, Optional

from app.core.auth import require_auth
from app.core.database import get_db
from app.models.prospect import REJECT_REASONS
from app.services import prospect_queue, prospect_service
import logging

logger = logging.getLogger("prospects")
router = APIRouter(prefix="/api/prospects", tags=["prospects"])

# Wording only. The *set* and the *order* come from `REJECT_REASONS` in the
# model, which already lists the two that mean "wrong side of the room" first —
# a reviewer scanning a dropdown should meet them before "Other". Keeping a
# second ordered list here is how the screen comes to offer a reason `reject()`
# refuses, or to hide one it accepts; a reason with no label falls back to its
# slug rather than vanishing from the dropdown.
REJECT_REASON_LABELS = {
    "seller_or_consignor": "Seller or consignor — sells to us",
    "competitor": "Competitor — auction house, liquidator, appraiser",
    "not_a_buyer": "Not a buyer for this kind of lot",
    "wrong_category": "Wrong category",
    "bad_number": "Bad or unusable number",
    "other": "Other",
}


def reject_reason_options() -> list:
    return [{"value": reason, "label": REJECT_REASON_LABELS.get(reason, reason)}
            for reason in REJECT_REASONS]

EXPORT_COLUMNS = ["phone", "business_name", "category", "line_type", "score",
                  "distance_miles", "status", "search_term", "buyer_rationale",
                  "source", "source_url", "scraped_at"]


class ReviewRequest(BaseModel):
    prospect_ids: List[int]


class PromoteRequest(ReviewRequest):
    category_id: int


class RejectRequest(ReviewRequest):
    reason: str
    notes: Optional[str] = None


def _eligibility(raw: Optional[str]) -> Optional[bool]:
    """"yes"/"no"/absent — a tri-state filter, kept out of the handler.

    A bare `bool` query parameter cannot express "don't filter", and FastAPI
    would coerce an absent one to False, which silently turns "show me
    everything" into "show me only what the gate stopped".
    """
    if raw in (None, "", "all"):
        return None
    return raw.lower() in ("1", "true", "yes", "eligible")


@router.get("")
async def list_prospects(status: str = "pending", category_id: int = None,
                         line_type: str = None, eligible: str = None,
                         search_term: str = None, q: str = None,
                         sort: str = prospect_queue.DEFAULT_SORT,
                         direction: str = "desc", page: int = 1,
                         per_page: int = prospect_queue.PAGE_SIZE,
                         db: Session = Depends(get_db),
                         user: str = Depends(require_auth)):
    """One page of the queue. Server-side paging and sorting, always."""
    return prospect_queue.queue_page(
        db, status=status, category_id=category_id, line_type=line_type,
        promote_eligible=_eligibility(eligible), search_term=search_term, q=q,
        sort=sort, direction=direction, page=page, per_page=per_page)


@router.get("/summary")
async def prospect_summary(db: Session = Depends(get_db),
                           user: str = Depends(require_auth)):
    return {**prospect_queue.summary(db),
            "reject_reasons": reject_reason_options()}


@router.get("/terms")
async def prospect_terms(db: Session = Depends(get_db),
                         user: str = Depends(require_auth)):
    """Per-term breakdown: what each search found and what it got rejected for.

    This is how a search term that finds the wrong side of the room becomes
    visible instead of being something somebody eventually notices.
    """
    return prospect_queue.term_breakdown(db)


@router.get("/export.csv")
async def export_prospects(status: str = "pending", category_id: int = None,
                           eligible: str = None, search_term: str = None,
                           q: str = None,
                           db: Session = Depends(get_db),
                           user: str = Depends(require_auth)):
    """The current filter as CSV. Streamed, and page-free.

    Streamed in chunks for the reason the contacts export is: a 10,000-row
    export built in memory is a minute of silence followed by a request that may
    not survive the proxy. `EXPORT_COLUMNS` carries no payload and no cost.
    """
    def rows():
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=EXPORT_COLUMNS)
        writer.writeheader()
        yield buffer.getvalue()

        page = 1
        while True:
            batch = prospect_queue.queue_page(
                db, status=status, category_id=category_id,
                promote_eligible=_eligibility(eligible),
                search_term=search_term, q=q, page=page,
                per_page=prospect_queue.MAX_PAGE_SIZE)
            for row in batch["prospects"]:
                buffer.seek(0)
                buffer.truncate(0)
                writer.writerow({
                    "phone": row["phone"],
                    "business_name": row["business_name"] or "",
                    "category": row["category_label"] or "",
                    "line_type": row["line_type"],
                    "score": row["score"],
                    "distance_miles": row["distance_miles"] or "",
                    "status": row["status"],
                    "search_term": row["search_term"],
                    "buyer_rationale": row["buyer_rationale"],
                    "source": row["source"],
                    "source_url": row["source_url"],
                    "scraped_at": row["scraped_at"],
                })
                yield buffer.getvalue()
            if page >= batch["pages"]:
                break
            page += 1

    return StreamingResponse(
        rows(), media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="prospects.csv"'})


@router.post("/promote")
async def promote_prospects(payload: PromoteRequest,
                            db: Session = Depends(get_db),
                            user: str = Depends(require_auth)):
    """Turn the selection into contacts, tagged with the chosen category.

    Partial outcomes are the normal case and are reported as such: a screenful
    of prospects where three are landlines returns the ones that went in and the
    ones that did not, each with the reason. A 400 for the whole batch would
    make the client re-tick fifty rows to find out which three.
    """
    result = prospect_service.promote(db, payload.prospect_ids, payload.category_id)
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return {"success": True, **result}


@router.post("/reject")
async def reject_prospects(payload: RejectRequest, db: Session = Depends(get_db),
                           user: str = Depends(require_auth)):
    """Say no, permanently. The number never resurfaces, from any source."""
    try:
        result = prospect_service.reject(
            db, payload.prospect_ids, payload.reason, notes=payload.notes)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"success": True, **result}
