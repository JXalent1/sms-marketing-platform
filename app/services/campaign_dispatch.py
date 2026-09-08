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

Session B1 adds one thing to both: after the messages have gone out, the
campaign's segments are reported to the usage meter. It is the last thing either
function does, it is wrapped, and it cannot raise — a Stripe outage that aborted
a send would turn a billing problem into an empty saleroom, and `decisions/002`
already settled which of those two costs more. The recovery is
`stripe_meter.backfill_unreported()`, which is safe to run because the meter
event's identifier is derived from the campaign id.
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
        report_usage(db, campaign_id)
    except Exception as e:
        logger.error(f"Campaign {campaign_id} background send failed: {e}")
    finally:
        db.close()


def report_usage(db: Session, campaign_id: int) -> None:
    """Meter what this campaign sent. Fire and forget, in both directions.

    Imported here rather than at module scope so a box with no Stripe package
    installed still schedules and sends campaigns — this file is on the send
    path and the send path owes nothing to a payment processor.

    `report_campaign()` already swallows its own errors; this second wrapper is
    not redundant, because "already swallows" is a property of today's
    implementation and the rule is about this call site. The rule: nothing here
    may raise into the send loop.

    **What the meter receives is the raw segment count**, not
    `billable_segments()`. The allowance is applied by the Stripe tier; applying
    it here as well would bill $0 for a 15,000-segment month while `/usage` went
    on showing $75 due. See `app/services/stripe_meter.py`.
    """
    try:
        from app.services import stripe_meter
        outcome = stripe_meter.report_campaign(db, campaign_id)
        if not outcome["reported"] and outcome["segments"]:
            logger.info(f"Campaign {campaign_id}: {outcome['segments']} segment(s) "
                        f"not metered — {outcome['reason']}")
    except Exception as e:
        logger.error(f"Campaign {campaign_id} usage reporting failed: {e}")


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
                report_usage(db, campaign_id)
            except Exception as e:
                # One bad campaign must not stop the rest of tonight's schedule.
                logger.error(f"Scheduled campaign {campaign_id} failed: {e}")
        return campaign_ids
    finally:
        db.close()
