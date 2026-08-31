"""Reporting API: one campaign's report, campaign history, message history.

HTTP only. Every number is computed in `report_service` or `history_service` and
serialised here unchanged, for the reason the pre-flight checklist is built the
same way: a figure assembled in a router says one thing over the API and another
on the screen that renders it, and the API is what a post-mortem reads.

**Nothing here returns our cost.** `campaigns.estimated_cost`, the
`carrier_cost*` columns and `WHOLESALE_COST_PER_SEGMENT` are wholesale figures;
the client is metered in segments and billed at `BILLING_PRICE_PER_SEGMENT`, and
`report_service` is where his money comes from. `tests/test_whitelabel.py` runs
these routes and asserts it rather than trusting the review that wrote them.
"""

import csv
import io
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.auth import require_auth
from app.core.config import settings
from app.core.database import get_db
from app.services import history_service, report_service

logger = logging.getLogger("reports")
router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("/campaigns")
async def campaign_history(page: int = 1, per_page: Optional[int] = None,
                           db: Session = Depends(get_db),
                           user: str = Depends(require_auth)):
    """Campaign history, newest first. Paginated."""
    return history_service.campaign_history(db, page=page, per_page=per_page)


@router.get("/campaigns/{campaign_id}")
async def campaign_report(campaign_id: int, db: Session = Depends(get_db),
                          user: str = Depends(require_auth)):
    report = report_service.campaign_report(db, campaign_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return report


@router.get("/campaigns/{campaign_id}/messages")
async def campaign_messages(campaign_id: int, page: int = 1,
                            per_page: Optional[int] = None,
                            status: Optional[str] = Query(default=None),
                            db: Session = Depends(get_db),
                            user: str = Depends(require_auth)):
    """One page of a campaign's recipients, with each row's click data."""
    return history_service.campaign_messages(db, campaign_id, page=page,
                                             per_page=per_page, status=status)


@router.get("/contacts/{contact_id}/messages")
async def contact_messages(contact_id: int, page: int = 1,
                           per_page: Optional[int] = None,
                           db: Session = Depends(get_db),
                           user: str = Depends(require_auth)):
    """What one person has been sent, and whether they opened it."""
    history = history_service.contact_history(db, contact_id, page=page,
                                              per_page=per_page)
    if history is None:
        raise HTTPException(status_code=404, detail="Contact not found")
    return history


# ─── Export ─────────────────────────────────────────────────────────────────

# The rows of the exported summary, in order. A list rather than a dict literal
# inline so the CSV and the screen cannot drift: both read the same report.
def _summary_rows(report: dict) -> list:
    campaign = report["campaign"]
    outcome = report["outcome"]
    clicks = report["clicks"]
    cost = report["cost"]
    rate = clicks["click_through_rate"]
    rows = [
        ("Campaign", campaign["name"]),
        ("Status", campaign["status"]),
        ("Category", campaign["category_label"] or "—"),
        ("Audience", campaign["audience_label"] or "—"),
        ("Sent", campaign["started_at"] or campaign["created_at"] or "—"),
        ("Recipients", outcome["recipients"]),
        ("Messages sent", outcome["sent"]),
        ("Delivered", outcome["delivered"]),
        ("Failed", outcome["failed"]),
        ("Held back", outcome["held_back"]),
        ("Blocked (opted out already)", outcome["blocked"]),
        ("Opted out after this campaign", outcome["opted_out"]),
        ("Link clicks (people)", clicks["clickers"]),
        ("Link clicks (total)", clicks["clicks"]),
        # Named as a filtered figure rather than dropped, on every surface. A
        # bare click count that quietly excludes things is the defect; the
        # filtering is not.
        ("Clicks filtered as automated", clicks["filtered_clicks"]),
        ("Click-through rate", f"{rate}%" if rate is not None else "—"),
        ("Segments", cost["segments"]),
        # His rate, from billing_service, net of the month's allowance. Never
        # the wholesale figure the capacity check is denominated in.
        (f"Added to the {cost['billing_month']} bill", f"${cost['cost']:.2f}"),
    ]
    if campaign["abort_reason"]:
        # Rendered verbatim, in the wording 5h and decision 006 settled. A
        # campaign that reached nobody must say so on the thing he forwards.
        rows.append(("Why this campaign did not send", campaign["abort_reason"]))
    return rows


@router.get("/campaigns/{campaign_id}/export")
async def export_campaign_report(campaign_id: int, db: Session = Depends(get_db),
                                 user: str = Depends(require_auth)):
    """The report as a CSV he can send to the auction house.

    Streamed, and the recipient rows are read in pages rather than all at once:
    a campaign has 4,000 of them and this runs on a 1vCPU droplet.
    """
    report = report_service.campaign_report(db, campaign_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Campaign not found")

    def rows():
        buffer = io.StringIO()
        writer = csv.writer(buffer)

        def flush():
            value = buffer.getvalue()
            buffer.seek(0)
            buffer.truncate(0)
            return value

        writer.writerow([settings.BRAND_NAME, "campaign report"])
        for label, value in _summary_rows(report):
            writer.writerow([label, value])
        writer.writerow([])
        writer.writerow(["Phone", "Status", "Sent", "Delivered", "Clicks",
                         "Added in top-up", "Note"])
        yield flush()

        page = 1
        while True:
            batch = history_service.campaign_messages(
                db, campaign_id, page=page, per_page=history_service.MAX_PAGE_SIZE)
            for message in batch["messages"]:
                writer.writerow([
                    message["phone"], message["status"], message["sent_at"] or "",
                    message["delivered_at"] or "", message["clicks"],
                    message["top_up_at"] or "",
                    # Already scrubbed by history_service — the column is
                    # written from carrier free text and this file goes to a
                    # third party.
                    message["error_message"] or "",
                ])
            yield flush()
            if page >= batch["pages"] or not batch["messages"]:
                break
            page += 1

    name = "".join(c if c.isalnum() or c in "-_" else "-"
                   for c in (report["campaign"]["name"] or "campaign"))[:60]
    return StreamingResponse(
        rows(), media_type="text/csv",
        headers={"Content-Disposition":
                 f'attachment; filename="{name or "campaign"}-report.csv"'},
    )
