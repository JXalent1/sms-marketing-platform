"""Campaign API.

Rate limits are not decoration. The one attack this codebase's ancestor suffered
was two campaigns created back-to-back through an unauthenticated endpoint,
9,360 messages, eleven minutes. Auth is the real fix; the limiter caps the blast
radius of a stolen session.
"""

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Request
from sqlalchemy.orm import Session
from slowapi import Limiter
from slowapi.util import get_remote_address
from pydantic import BaseModel
from typing import Optional
from app.core.database import get_db
from app.core.auth import require_auth, get_client_ip
from app.models.campaign import Campaign
from app.models.sms_message import SMSMessage
from app.services.campaign_service import (
    CampaignService, CampaignError, send_campaign_background,
)
from app.services import contact_service
from app.sms.factory import get_provider
from app.sms.segments import describe
from app.sms.phone import normalize, scrub_provider_text, find_risky_links
import logging

logger = logging.getLogger("campaign")
router = APIRouter(prefix="/api/campaigns", tags=["campaigns"])
limiter = Limiter(key_func=get_remote_address)


class CreateCampaignRequest(BaseModel):
    name: str
    message_template: str
    audience: str                       # "all" | "list:<id>" | "source:<name>"
    batch_size: Optional[int] = None


class TestSMSRequest(BaseModel):
    phone: str
    message: str


class PreviewRequest(BaseModel):
    message_template: str
    audience: Optional[str] = None


@router.get("/audiences")
async def audiences(db: Session = Depends(get_db), user: str = Depends(require_auth)):
    """Selectable audiences with live counts, for the composer dropdown."""
    return {"audiences": contact_service.list_summaries(db)}


@router.post("/preview")
async def preview(payload: PreviewRequest, db: Session = Depends(get_db),
                  user: str = Depends(require_auth)):
    """Cost and deliverability preview — call this before every send.

    Shows the real segment count (so an emoji's 2.4x cost is visible before the
    blast, not on the invoice) and flags shortener links carriers will drop.
    """
    breakdown = describe(payload.message_template)
    recipients = 0
    if payload.audience:
        try:
            recipients = len(contact_service.resolve_audience(db, payload.audience))
        except ValueError:
            recipients = 0

    return {
        **breakdown,
        "recipients": recipients,
        "total_segments": breakdown["segments"] * recipients,
        "risky_links": find_risky_links(payload.message_template),
    }


@router.post("")
@limiter.limit("5/minute")
async def create_campaign(request: Request, payload: CreateCampaignRequest,
                          db: Session = Depends(get_db), user: str = Depends(require_auth)):
    """Create a draft campaign. Nothing is sent until POST /{id}/send."""
    try:
        campaign = CampaignService(db).create_campaign(
            name=payload.name,
            message_template=payload.message_template,
            audience=payload.audience,
            batch_size=payload.batch_size,
        )
    except CampaignError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Campaign create failed: {e}")
        raise HTTPException(status_code=500, detail="Could not create campaign")

    logger.info(f"Campaign #{campaign.id} created by {get_client_ip(request)}")
    return {"success": True, "campaign": _campaign_dict(campaign)}


@router.post("/{campaign_id}/send")
@limiter.limit("5/minute")
async def send_campaign(request: Request, campaign_id: int, background_tasks: BackgroundTasks,
                        db: Session = Depends(get_db), user: str = Depends(require_auth)):
    """Start sending a draft campaign in the background."""
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if campaign.status != "draft":
        raise HTTPException(status_code=400, detail=f"Campaign is {campaign.status}, not draft")

    logger.info(f"Campaign #{campaign_id} send triggered by {get_client_ip(request)}")
    background_tasks.add_task(send_campaign_background, campaign_id)
    return {"success": True, "message": "Campaign sending started"}


@router.get("")
async def list_campaigns(skip: int = 0, limit: int = 50,
                         db: Session = Depends(get_db), user: str = Depends(require_auth)):
    campaigns = (db.query(Campaign).order_by(Campaign.id.desc())
                 .offset(skip).limit(limit).all())
    return {
        "campaigns": [_campaign_dict(c) for c in campaigns],
        "total": db.query(Campaign).count(),
    }


@router.get("/{campaign_id}")
async def get_campaign(campaign_id: int, db: Session = Depends(get_db),
                       user: str = Depends(require_auth)):
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    # Delivery counts come from webhooks and lag the send by minutes.
    delivered = db.query(SMSMessage).filter(
        SMSMessage.campaign_id == campaign_id,
        SMSMessage.delivered_at.isnot(None),
    ).count()
    undelivered = db.query(SMSMessage).filter(
        SMSMessage.campaign_id == campaign_id,
        SMSMessage.status == "undelivered",
    ).count()

    messages = db.query(SMSMessage).filter(
        SMSMessage.campaign_id == campaign_id
    ).limit(200).all()

    return {
        "campaign": {**_campaign_dict(campaign),
                     "delivered_count": delivered,
                     "undelivered_count": undelivered},
        "messages": [{
            "id": m.id,
            "phone": m.phone,
            "message": m.message,
            "status": m.status,
            "segments": m.segments,
            "sent_at": m.sent_at,
            "delivered_at": m.delivered_at,
            "error_message": scrub_provider_text(m.error_message),
        } for m in messages],
    }


@router.get("/{campaign_id}/failures")
async def campaign_failures(campaign_id: int, db: Session = Depends(get_db),
                            user: str = Depends(require_auth)):
    """Failure reasons grouped by message.

    The first question after any disappointing campaign is "why did these fail",
    and the answer is almost never one cause. Grouping separates a funding
    outage from bad phone data at a glance.
    """
    from collections import Counter

    rows = db.query(SMSMessage.error_message).filter(
        SMSMessage.campaign_id == campaign_id,
        SMSMessage.status.in_(("failed", "undelivered")),
    ).all()

    counter = Counter(scrub_provider_text(e or "")[:120] or "Unknown" for (e,) in rows)
    return {
        "total": sum(counter.values()),
        "reasons": [{"reason": reason, "count": count}
                    for reason, count in counter.most_common(20)],
    }


@router.post("/test-sms")
@limiter.limit("5/minute")
async def send_test_sms(request: Request, payload: TestSMSRequest,
                        user: str = Depends(require_auth)):
    """Send one message to a real handset.

    Always do this before a blast, to a phone on the carrier your audience uses.
    A message can be accepted by the provider and still be dropped by the carrier;
    only a real handset tells you the truth.
    """
    phone = normalize(payload.phone)
    if not phone:
        raise HTTPException(status_code=400, detail="Invalid phone number")

    result = await get_provider().send(phone, payload.message)
    if result.success:
        logger.info(f"Test SMS to {phone} by {get_client_ip(request)}")
        return {"success": True, "message": f"Test SMS sent to {phone}",
                "segments": result.parts}
    return {"success": False, "error": scrub_provider_text(result.error)}


def _campaign_dict(c: Campaign) -> dict:
    """The client's view of a campaign.

    The campaign's cost estimate is deliberately absent. It is priced at our
    wholesale carrier rate, so returning it did three wrong things at once: it
    named the carrier relationship, it disclosed our margin, and — worst — it
    showed him a figure about 40% below what he is actually invoiced, which is a
    number he would plan against. He is metered in segments, and
    `estimated_segments` is the honest field; any money figure comes from
    billing_service, at his rate. The column stays on the model, for the
    pre-flight check and our own logs.

    Adding it back is caught by tests/test_whitelabel.py, not by the gate — the
    gate greps for the carrier's name and this leak never contained one.
    """
    return {
        "id": c.id,
        "name": c.name,
        "message_template": c.message_template,
        "audience": c.audience,
        "audience_label": c.audience_label,
        "status": c.status,
        "total_recipients": c.total_recipients,
        "sent_count": c.sent_count,
        "failed_count": c.failed_count,
        "skipped_count": c.skipped_count,
        "estimated_segments": c.estimated_segments,
        "abort_reason": c.abort_reason,
        "created_at": c.created_at,
        "started_at": c.started_at,
        "completed_at": c.completed_at,
    }
