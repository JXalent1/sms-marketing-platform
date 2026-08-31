"""The campaign-first flow: upload as step one, and topping up afterwards.

Same `/api/campaigns` prefix as `campaigns.py` and included beside it. A separate
module because `campaigns.py` is at the 500-line rule and because these three
endpoints are one story — a campaign built around the list you just uploaded,
and that list growing afterwards — while everything in `campaigns.py` predates it.

`_campaign_dict` is imported from there rather than re-implemented. It is the
client's view of a campaign and its docstring exists to keep one leak (our
wholesale cost estimate) out of it; a second serializer would be a second place
for that to come back.

These are multipart endpoints, so they take Form fields rather than a JSON body.
That is not a style choice: a file and the campaign it is for have to arrive in
one request, or a rejected campaign leaves an orphan list behind and the next
attempt collides with the name it just orphaned.
"""

import logging
from typing import Optional

from fastapi import (
    APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request,
    UploadFile,
)
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

from app.core.auth import get_client_ip, require_auth
from app.core.database import get_db
from app.models.campaign import Campaign
from app.services import campaign_builder, campaign_topup, import_service
from app.services.campaign_service import CampaignError, CampaignService
from app.routers.campaigns import _campaign_dict  # one serializer, one leak to keep out

logger = logging.getLogger("campaign")
router = APIRouter(prefix="/api/campaigns", tags=["campaigns"])
limiter = Limiter(key_func=get_remote_address)


@router.post("/upload-preview")
@limiter.limit("20/minute")
async def upload_preview(request: Request, file: UploadFile = File(...),
                         category_id: Optional[int] = Form(None),
                         db: Session = Depends(get_db),
                         user: str = Depends(require_auth)):
    """What this file would do, before it does any of it. Writes nothing.

    The same `import_service.preview()` the Contacts screen calls, and that is
    the point: its counts are the whole reason the upload flow is trustworthy,
    and a second set computed for the composer would eventually disagree with the
    set the commit actually produces.

    A category is optional here (5e A2) and required on `/api/imports/preview`.
    The difference is what the upload is *for* — see `routers/imports.py`.
    """
    try:
        return import_service.preview(db, await file.read(), category_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/from-upload")
@limiter.limit("5/minute")
async def create_campaign_from_upload(request: Request,
                                      file: UploadFile = File(...),
                                      name: str = Form(...),
                                      message_template: str = Form(...),
                                      category_id: Optional[int] = Form(None),
                                      batch_size: Optional[int] = Form(None),
                                      scheduled_at: Optional[str] = Form(None),
                                      link_target_url: Optional[str] = Form(None),
                                      db: Session = Depends(get_db),
                                      user: str = Depends(require_auth)):
    """Import a CSV and create a draft campaign whose audience is that import.

    Nothing is sent. This produces a draft exactly as `POST /api/campaigns`
    does — the send is still `POST /{id}/send`, with the same refusals.

    Rate-limited at the same 5/minute as `POST /api/campaigns`, and for the same
    reason: this is the other way a campaign gets created, so leaving it
    unlimited would be a way around the limit rather than a second one.
    """
    try:
        campaign, imported = campaign_builder.create_campaign_from_upload(
            db, CampaignService(db).render,
            name=name,
            message_template=message_template,
            content=await file.read(),
            category_id=category_id,
            batch_size=batch_size,
            scheduled_at=scheduled_at or None,
            link_target_url=link_target_url or None,
        )
    except CampaignError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        db.rollback()
        logger.error(f"Campaign from upload failed: {e}")
        raise HTTPException(status_code=500, detail="Could not create campaign")

    logger.info(f"Campaign #{campaign.id} created from upload by "
                f"{get_client_ip(request)} | list {imported['list_id']} "
                f"'{imported['list_name']}'")
    return {
        "success": True,
        "campaign": _campaign_dict(db, campaign),
        "import": imported,
    }


@router.post("/{campaign_id}/top-up")
@limiter.limit("5/minute")
async def top_up_campaign(request: Request, campaign_id: int,
                          background_tasks: BackgroundTasks,
                          db: Session = Depends(get_db),
                          user: str = Depends(require_auth)):
    """Send this campaign's message to everyone it has not reached yet.

    Two sets, gathered under different rules and counted separately in the
    answer: contacts added to the campaign's list since it went out, and
    contacts the hold-back window held back whose hold has since cleared (5h A1,
    decisions/005). Calling the second lot "new recipients" would send the client
    looking for an upload he never made, so `top_up_summary()` owns the sentence
    and this endpoint renders it verbatim.

    **Refused synchronously**, before anything is queued — 5d's lesson from
    `POST /{id}/send`, which used to answer "sending started" and let the refusal
    surface seconds later on a poll. Here it matters twice over: a refusal after
    the rows were written would leave a completed campaign carrying orphan
    `pending` messages.

    So the pre-flight refusals, the state check and the "is there anybody new"
    check all run in this request, and only a top-up that has passed all three
    reaches the background task. `campaign_topup.top_up()` runs them again there,
    because the check has to live on the path that does the work — this is an
    additional layer, not a replacement.
    """
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    try:
        verdict = await campaign_topup.assess(db, campaign)
    except ValueError as e:
        # A campaign whose audience selector no longer resolves — a deleted list,
        # a renamed category slug. Its own sentence rather than a 500.
        raise HTTPException(status_code=400, detail=str(e))

    if verdict["refusal"]:
        logger.error(f"Campaign #{campaign_id} top-up REFUSED: {verdict['refusal']}")
        raise HTTPException(status_code=verdict["code"], detail=verdict["refusal"])

    added = len(verdict["sendable"])
    released = len(verdict["released"])
    logger.info(f"Campaign #{campaign_id} top-up triggered by {get_client_ip(request)} "
                f"| {added} new recipient(s), {released} released from the hold-back "
                f"window")
    background_tasks.add_task(campaign_topup.top_up_background, campaign_id)
    return {
        "success": True,
        "message": campaign_topup.top_up_summary(added, released),
        "recipients": added + released,
        "added": added,
        "released": released,
        # Everybody this run is holding back: the newcomers texted recently, and
        # the rows whose hold has not cleared yet. One number, because "held
        # back" is one fact about this top-up however the row got there.
        "suppressed": len(verdict["suppressed"]) + len(verdict["still_held"]),
    }
