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
from app.models.category import Category
from app.models.sms_message import SMSMessage
from app.services.campaign_service import (
    CampaignService, CampaignError, send_campaign_background, wholesale_estimate,
)
from app.services import contact_service, preflight_service
from app.services.blocklist_service import load_blocked_set
from app.sms.factory import get_provider
from app.sms.segments import describe, count_segments
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

    # Required in practice: one of these two has to be supplied. category_id
    # names the auction; cross_category_override is the deliberate escape, and
    # it defaults to False so it can only ever be a decision someone made.
    category_id: Optional[int] = None
    cross_category_override: bool = False

    # ISO timestamp. A campaign carrying one waits for the scheduler and then
    # goes through the identical send path, pre-flight included.
    scheduled_at: Optional[str] = None


class TestSMSRequest(BaseModel):
    phone: str
    message: str


class PreviewRequest(BaseModel):
    message_template: str
    audience: Optional[str] = None


class PreflightRequest(BaseModel):
    message_template: str
    audience: str
    category_id: Optional[int] = None
    batch_size: Optional[int] = None


@router.get("/audiences")
async def audiences(db: Session = Depends(get_db), user: str = Depends(require_auth)):
    """Selectable audiences with live counts, for the composer dropdown."""
    return {"audiences": contact_service.list_summaries(db)}


def _audience_split(db: Session, audience: Optional[str],
                    batch_size: Optional[int] = None) -> dict:
    """What an audience selector actually resolves to, before anything is sent.

    Returns the counts the composer's summary panel shows and one sample contact
    for the phone preview. The partition is the same call `create_campaign()`
    makes, so the composer's numbers and the draft's numbers cannot disagree.

    A bad selector reports zeros rather than raising: this runs on every
    keystroke and a half-typed selector is not an error worth a 500.
    """
    empty = {"recipients": 0, "suppressed": 0, "opted_out": 0, "sample": None}
    if not audience:
        return empty
    try:
        resolved = contact_service.resolve_audience(db, audience)
    except ValueError:
        return empty

    sendable, suppressed = preflight_service.partition_recent(resolved)
    if batch_size and batch_size > 0:
        sendable = sendable[:batch_size]

    # Opted-out numbers are counted, not removed: the send loop is what refuses
    # them, and the count belongs on screen beforehand rather than in the
    # post-mortem. One query for the whole set, not one per contact.
    blocked = load_blocked_set(db)
    return {
        "recipients": len(sendable),
        "suppressed": len(suppressed),
        "opted_out": sum(1 for c in resolved if c.phone in blocked),
        "sample": sendable[0] if sendable else None,
    }


@router.post("/preview")
async def preview(payload: PreviewRequest, db: Session = Depends(get_db),
                  user: str = Depends(require_auth)):
    """Cost and deliverability preview — call this before every send.

    Shows the real segment count (so an emoji's 2.4x cost is visible before the
    blast, not on the invoice) and flags shortener links carriers will drop.

    `estimated_cost` here is HIS cost, from billing_service at
    BILLING_PRICE_PER_SEGMENT and net of the month's included allowance. It has
    nothing to do with `campaigns.estimated_cost`, which is priced at our
    wholesale rate and deliberately never leaves the server — see
    `_campaign_dict()` below.
    """
    breakdown = describe(payload.message_template)
    split = _audience_split(db, payload.audience)
    recipients = split["recipients"]

    # Rendered against a real contact, not a made-up "Jane Doe". A merge tag
    # that is empty for half the list — the contact with no name, the attribute
    # only some rows carry — shows up here and nowhere else before the send.
    sample = split["sample"]
    preview_text = (CampaignService(db).render(payload.message_template, sample)
                    if sample else payload.message_template)

    return {
        **breakdown,
        "recipients": recipients,
        "suppressed": split["suppressed"],
        "opted_out": split["opted_out"],
        "preview_text": preview_text,
        "sample_name": sample.display_name() if sample else None,
        "total_segments": breakdown["segments"] * recipients,
        "risky_links": find_risky_links(payload.message_template),
        **preflight_service.cost_estimates(db, payload.message_template, recipients),
    }


@router.post("/preflight")
async def preflight(payload: PreflightRequest, db: Session = Depends(get_db),
                    user: str = Depends(require_auth)):
    """The composer's step-3 checklist, computed here and merely drawn there.

    Every check comes back as key / label / status / reason. The UI renders
    whatever it is given, so a new check needs no template change and a check's
    wording can never say one thing on screen and another over the API.

    This is additive to, and does not replace, the capacity check the send path
    runs in `campaign_service`. Passing here is not permission to send; failing
    the capacity row here is the same verdict the send path will reach on its
    own a moment later.
    """
    category = db.get(Category, payload.category_id) if payload.category_id else None
    if payload.category_id and category is None:
        raise HTTPException(status_code=404, detail="Category not found")

    split = _audience_split(db, payload.audience, payload.batch_size)

    # The capacity check is denominated in our wholesale cost, so it needs the
    # segment total the campaign would actually queue.
    per_message = count_segments(payload.message_template or "")
    total_segments = per_message * split["recipients"]
    assessment = await CampaignService(db).capacity_assessment(
        total_segments, wholesale_estimate(total_segments)
    )

    report = preflight_service.build_report(
        db,
        category_slug=category.slug if category else None,
        message_template=payload.message_template,
        sendable_count=split["recipients"],
        suppressed_count=split["suppressed"],
        capacity_assessment=assessment,
    )
    report["counts"]["opted_out"] = split["opted_out"]
    return report


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
            category_id=payload.category_id,
            cross_category_override=payload.cross_category_override,
            scheduled_at=payload.scheduled_at,
        )
    except CampaignError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Campaign create failed: {e}")
        raise HTTPException(status_code=500, detail="Could not create campaign")

    logger.info(f"Campaign #{campaign.id} created by {get_client_ip(request)}")
    return {"success": True, "campaign": _campaign_dict(db, campaign)}


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
        "campaigns": [_campaign_dict(db, c) for c in campaigns],
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
        "campaign": {**_campaign_dict(db, campaign),
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


def _campaign_dict(db: Session, c: Campaign) -> dict:
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

    A campaign predating module 4 has no category and gets nulls here. The UI
    shows "—" for those; it does not guess, because a guessed category is
    indistinguishable from one a human chose and the column exists to record the
    choice.
    """
    category = db.get(Category, c.category_id) if c.category_id else None
    return {
        "id": c.id,
        "name": c.name,
        "message_template": c.message_template,
        "audience": c.audience,
        "audience_label": c.audience_label,
        "category_id": c.category_id,
        "category_label": category.label if category else None,
        "category_color_token": category.color_token if category else None,
        "cross_category_override": bool(c.cross_category_override),
        "status": c.status,
        "total_recipients": c.total_recipients,
        "sent_count": c.sent_count,
        "failed_count": c.failed_count,
        "skipped_count": c.skipped_count,
        "suppressed_count": c.suppressed_count,
        "estimated_segments": c.estimated_segments,
        "abort_reason": c.abort_reason,
        "scheduled_at": c.scheduled_at,
        "created_at": c.created_at,
        "started_at": c.started_at,
        "completed_at": c.completed_at,
    }
