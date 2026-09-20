"""The composer's keystroke quote: `POST /api/campaigns/preview`.

Same `/api/campaigns` prefix as `campaigns.py` and included beside it. Its own
module since session 5n, which gave this endpoint a second answer and pushed
`campaigns.py` past the 500-line rule — and because the endpoint is one story on
its own: it runs on every keystroke, it is the only thing allowed to fill the
composer's summary panel, and it has to stay cheap and off the event loop.

**Two answers, one measurement.** With an audience it reports the message half
and the audience half together, and the summary panel paints every row from that
one reply (5m). Without one — the upload tab, where the audience is a file that
has not been read yet — it reports the message half exactly as before and says
the audience half is *unknown*, as `None`, never as 0. "0 recipients" above a
list he is about to upload is a claim, and a false one. The message half is the
same function either way: `count_sms_segments()` is the one authority on
segments, and the browser is never asked to work one out.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.auth import require_auth
from app.core.config import settings
from app.core.database import get_db
from app.services import audience_split, link_service, preflight_service
from app.services.campaign_service import CampaignService
from app.sms.phone import find_risky_links
from app.sms.segments import describe

logger = logging.getLogger("campaign")
router = APIRouter(prefix="/api/campaigns", tags=["campaigns"])


class PreviewRequest(BaseModel):
    message_template: str
    audience: Optional[str] = None
    # Sent since 5m. The composer had a batch-size field wired to re-run this
    # preview and never sent the value, so the summary panel quoted 443
    # recipients for a send capped at 50 while the checklist beside it — which
    # does pass the cap — quoted 50. Two answers to "how many people does this
    # send reach", on one screen. `create_campaign()` applies the cap, so the
    # capped figure is the true one and both surfaces now ask the same question.
    batch_size: Optional[int] = None


# What /preview answers about an audience it was not asked about — every
# audience-derived figure is `None`, never 0. The composer's upload tab asks
# about the template alone, because the audience is a file that has not been
# read yet, and "0 recipients" above a list he is about to upload is a claim
# rather than a blank (5n A1). The template half of the same reply is measured
# exactly as it is with an audience: `count_sms_segments()` is the one authority
# on segments, and the browser is never asked to work one out.
UNKNOWN_AUDIENCE = {
    "audience": None, "audience_label": None, "recipients": None,
    "suppressed": None, "opted_out": None, "total_segments": None,
    "suppression_days": None, "suppression_clears_at": None,
    "estimated_cost": None, "estimated_cost_if_gsm7": None,
}


@router.post("/preview")
def preview(payload: PreviewRequest, db: Session = Depends(get_db),
            user: str = Depends(require_auth)):
    """Cost and deliverability preview — call this before every send.

    **`def`, not `async def`, and that is 5j's lesson rather than a style.**
    Nothing in here awaits: it is synchronous SQLAlchemy, and on an `async def`
    route it therefore runs *on the event loop*. `audience_split.resolve(db,
    "all")` materialises every active contact, partitions 10,146 of them and
    loads a 3,460-row blocklist — 237.9 ms measured, on a machine an order of
    magnitude faster than the client's one-vCPU droplet — and `run_due_campaigns`
    ticks on that same loop every minute while the rail polls every five seconds.
    5j watched a reporting query hold the loop and make the scheduler miss its
    tick by 38 seconds. Session 5m gave this endpoint one more caller (the panel
    is fetched before a message is typed), which is the wrong direction to move
    while it is on the loop. FastAPI runs a `def` path operation in a worker
    thread.

    Shows the real segment count (so an emoji's 2.4x cost is visible before the
    blast, not on the invoice) and flags shortener links carriers will drop.

    `estimated_cost` here is HIS cost, from billing_service at
    BILLING_PRICE_PER_SEGMENT and net of the month's included allowance. It has
    nothing to do with `campaigns.estimated_cost`, which is priced at our
    wholesale rate and deliberately never leaves the server — see
    `_campaign_dict()` in `campaigns.py`.
    """
    # Counted with the link at its rendered width. The keystroke counter is
    # allowed to be an estimate about *names*; it is not allowed to be wrong
    # about a merge tag whose expansion is fixed and known — that would put a
    # segment figure on screen next to a phone preview that contradicts it.
    counted = link_service.for_counting(payload.message_template)
    breakdown = describe(counted)
    # No audience, no audience half. The upload tab asks this way (5n A1), and
    # the answer says "unknown" rather than resolving nothing into zeros.
    if not payload.audience:
        return {**_template_half(db, payload, breakdown, sample=None),
                **UNKNOWN_AUDIENCE,
                "price_per_segment": settings.BILLING_PRICE_PER_SEGMENT}

    split = audience_split.resolve(db, payload.audience, payload.batch_size)
    recipients = split["recipients"]

    # A message that does not exist is not a one-segment message. `describe("")`
    # answers 1, because it answers about text, and since 5m this endpoint is
    # also how the composer fills its summary panel *before* anything is typed —
    # so that 1 would quote the client 10,146 segments and $2.19 for an empty
    # box. The audience half of the answer is the same either way; only the
    # message half is zeroed, and it is zeroed here rather than in
    # `count_sms_segments()`, which is correct and is not being asked this
    # question.
    written = bool(payload.message_template)
    priced_for = recipients if written else 0

    return {
        **_template_half(db, payload, breakdown, sample=split["sample"]),
        # Which audience this whole answer is about. The composer's panel paints
        # every row from one response, and it names the audience from this
        # rather than from the control it happens to be sitting next to.
        "audience": split["audience"],
        "audience_label": split["audience_label"],
        "recipients": recipients,
        "suppressed": split["suppressed"],
        # A6. The composer draws these; it does not decide when the hold clears
        # and it does not know the window — a screen that computed either would
        # eventually quote a window the send path was not filtering with.
        "suppression_days": split["suppression_days"],
        "suppression_clears_at": split["suppression_clears_at"],
        "opted_out": split["opted_out"],
        "total_segments": breakdown["segments"] * priced_for,
        **preflight_service.cost_estimates(db, counted, priced_for),
    }


def _template_half(db: Session, payload: PreviewRequest, breakdown: dict,
                   sample) -> dict:
    """The part of a preview that is true of the message alone, in any mode.

    Characters, encoding and segments per message are measured on the template
    and depend on no audience; the composer shows them while he types whether
    the audience is a list he has picked or a file he has not uploaded yet.
    One function builds them for both answers `preview()` can give, so the two
    cannot drift — the same figure through the same call, `count_sms_segments()`.
    """
    # Rendered against a real contact, not a made-up "Jane Doe". A merge tag
    # that is empty for half the list — the contact with no name, the attribute
    # only some rows carry — shows up here and nowhere else before the send.
    # Rendered with a placeholder link of exactly the length a real one will be
    # (`link_service.placeholder_url()`), so the phone preview shows the message
    # at its true width. The tag is never left as `{link}` on a screen whose job
    # is to show what lands on a handset.
    link = link_service.placeholder_url() if link_service.configured() else None
    preview_text = (CampaignService(db).render(payload.message_template, sample, link)
                    if sample else payload.message_template)
    return {
        **breakdown,
        "segments": breakdown["segments"] if payload.message_template else 0,
        "preview_text": preview_text,
        "sample_name": sample.display_name() if sample else None,
        "risky_links": find_risky_links(payload.message_template),
        # The composer needs both facts and must not derive either: whether the
        # message uses the tag, and whether this box can mint a link at all.
        "link_tag": link_service.LINK_TAG,
        "link_in_message": link_service.has_link_tag(payload.message_template),
        "link_available": link_service.configured(),
    }
