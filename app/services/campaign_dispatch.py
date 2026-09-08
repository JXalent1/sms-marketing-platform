"""The two ways a campaign starts that are not a direct call.

Split out of `campaign_service.py` when session 5d pushed that file past the
500-line rule. The boundary is *when a send begins*, not *how it runs*: nothing
here decides anything about a campaign, and both entry points hand the work to
the same `CampaignService.send_campaign()` a button press reaches. That is
deliberate and load-bearing — a scheduled blast gets the send-path refusal, the
capacity pre-flight, the blocklist, the region filter and recent-contact
suppression exactly as an on-demand one does. Scheduling decides *when*, never
*whether*, and nothing in this file may become a second, thinner send path.

Both functions own their own DB session. A request-scoped session is closed the
moment the HTTP response is returned, and an APScheduler job has no request to
borrow one from.

**Nothing here meters usage, and nothing here may.** Session B1 reported a
campaign's segments to the billing meter as the last thing each entry point
did, when every row was still `sent`; the delivery webhook then moved rows out
of the billable set and the figure could never be corrected (`decisions/011`).
B1b moved metering to a scheduled pass in `app/services/stripe_meter.py` that
reports rows once they have *settled* and marks them in the database. The rows
this file's sends write are unmarked, so the next pass takes them — there is no
hook to call, and adding one back would make the send path a second place a
segment can be metered, which is the defect `tests/test_metering_pass.py`
counts to exactly one.
"""

from datetime import datetime
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.services.campaign_service import CampaignService
import logging

logger = logging.getLogger("campaign")


async def send_campaign_background(campaign_id: int):
    """Entry point for BackgroundTasks — owns its own DB session."""
    from app.core.database import SessionLocal
    db = SessionLocal()
    try:
        await CampaignService(db).send_campaign(campaign_id)
    except Exception as e:
        logger.error(f"Campaign {campaign_id} background send failed: {e}")
    finally:
        db.close()


# ─── Scheduled send ─────────────────────────────────────────────────────────

def due_campaign_ids(db: Session, now: Optional[datetime] = None) -> List[int]:
    """Drafts whose scheduled time has arrived.

    Drafts only. A campaign that is already running, completed or aborted is not
    due however old its timestamp is, and filtering on status is what stops the
    minute-by-minute tick from dispatching the same campaign twice.

    Comparison is lexicographic on ISO strings, which is chronological for this
    format — the same basis `billing_service` uses on `sent_at`.
    """
    cutoff = (now or datetime.now()).isoformat()
    rows = (db.query(Campaign.id)
            .filter(Campaign.status == "draft",
                    Campaign.scheduled_at.isnot(None),
                    Campaign.scheduled_at <= cutoff)
            .order_by(Campaign.scheduled_at)
            .all())
    return [row_id for (row_id,) in rows]


async def run_due_campaigns() -> List[int]:
    """Send every campaign whose time has come. Returns the ids dispatched."""
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        campaign_ids = due_campaign_ids(db)
        if not campaign_ids:
            return []
        logger.info(f"Scheduler: {len(campaign_ids)} campaign(s) due — {campaign_ids}")
        service = CampaignService(db)
        for campaign_id in campaign_ids:
            try:
                await service.send_campaign(campaign_id)
            except Exception as e:
                # One bad campaign must not stop the rest of tonight's schedule.
                logger.error(f"Scheduled campaign {campaign_id} failed: {e}")
        return campaign_ids
    finally:
        db.close()
