"""Campaign history and message history, both paginated.

Deferred at launch as module 8 and needed now: without them the client can see
what he is about to send and nothing about what he has sent.

Two screens, one rule each.

**Campaign history** is a list with the numbers that matter and a route into the
per-campaign report. The counts come from one grouped query over the whole page,
never one per row — the contacts screen learned that in 3b and the campaign rail
learned it again in 5e with `top_up_history()`.

**Message history** is per contact: what this person was sent, when, whether it
arrived, whether they clicked. That view is the payoff for keeping the contacts
table underneath the per-campaign uploads, and it is what makes a phone call
possible — "you looked at the flooring sale on Tuesday".

Both paginate for the same reason: 3,000-plus message rows per campaign and a
1vCPU droplet. A page is a COUNT plus a LIMIT/OFFSET, never a full read sliced
in Python. The reference build paged in the browser and stopped being usable at
about 8,000 rows, which nobody noticed until the client's list crossed it.

Error text is scrubbed on the way out, like every other surface that reads
`sms_messages.error_message` back to the client. The column is written from
carrier free text and the write side is not consistently scrubbed — see the
long-standing note in `status.md` — so a new reader that forgot would be a new
leak of exactly the kind `scrub_provider_text()` exists to prevent.
"""

import logging
from typing import Optional

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.category import Category
from app.models.contact import Contact
from app.models.sms_message import HELD_BACK_STATUS, SENT_STATUSES, SMSMessage
from app.services import link_service
from app.sms.phone import scrub_provider_text

logger = logging.getLogger("reports")

PAGE_SIZE = 25
MAX_PAGE_SIZE = 100


def _paging(page: int, per_page: Optional[int], total: int) -> dict:
    size = min(max(1, per_page or PAGE_SIZE), MAX_PAGE_SIZE)
    page = max(1, page or 1)
    return {"page": page, "per_page": size, "total": total,
            "pages": max(1, -(-total // size)), "offset": (page - 1) * size}


def _outcomes_for(db: Session, campaign_ids: list) -> dict:
    """{campaign_id: {sent, delivered, failed, held_back}} for a page of rows.

    One grouped query for the page. A lookup per campaign would leave every
    assertion in the suite passing while a fifty-row page became a hundred
    round-trips — the shape 3b's bounded-query test was written to catch.
    """
    if not campaign_ids:
        return {}
    rows = (db.query(SMSMessage.campaign_id, SMSMessage.status,
                     func.count(SMSMessage.id))
            .filter(SMSMessage.campaign_id.in_(campaign_ids))
            .group_by(SMSMessage.campaign_id, SMSMessage.status).all())

    out = {cid: {"sent": 0, "failed": 0, "held_back": 0} for cid in campaign_ids}
    for campaign_id, status, count in rows:
        bucket = out.setdefault(campaign_id,
                                {"sent": 0, "failed": 0, "held_back": 0})
        if status in SENT_STATUSES:
            bucket["sent"] += count
        elif status in ("failed", "undelivered"):
            bucket["failed"] += count
        elif status == HELD_BACK_STATUS:
            bucket["held_back"] += count
    return out


def _clicks_for(db: Session, campaign_ids: list) -> dict:
    """{campaign_id: {clicks, clickers}} for a page. One grouped query."""
    if not campaign_ids:
        return {}
    from app.models.short_link import ShortLink

    rows = (db.query(ShortLink.campaign_id,
                     func.coalesce(func.sum(ShortLink.click_count), 0),
                     func.sum(case((ShortLink.click_count > 0, 1), else_=0)))
            .filter(ShortLink.campaign_id.in_(campaign_ids))
            .group_by(ShortLink.campaign_id).all())
    return {campaign_id: {"clicks": int(clicks or 0), "clickers": int(clickers or 0)}
            for campaign_id, clicks, clickers in rows}


def campaign_history(db: Session, page: int = 1,
                     per_page: Optional[int] = None) -> dict:
    """One page of campaigns, newest first, with the numbers that matter."""
    total = db.query(func.count(Campaign.id)).scalar() or 0
    paging = _paging(page, per_page, total)

    campaigns = (db.query(Campaign).order_by(Campaign.id.desc())
                 .offset(paging["offset"]).limit(paging["per_page"]).all())
    ids = [c.id for c in campaigns]
    outcomes = _outcomes_for(db, ids)
    clicks = _clicks_for(db, ids)
    labels = {cid: label for cid, label in
              db.query(Category.id, Category.label).all()}

    return {
        **{k: v for k, v in paging.items() if k != "offset"},
        "campaigns": [{
            "id": c.id,
            "name": c.name,
            "status": c.status,
            "category_label": labels.get(c.category_id),
            "audience_label": c.audience_label,
            "recipients": c.total_recipients or 0,
            "created_at": c.created_at,
            "started_at": c.started_at,
            "completed_at": c.completed_at,
            "scheduled_at": c.scheduled_at,
            "abort_reason": c.abort_reason,
            **outcomes.get(c.id, {"sent": 0, "failed": 0, "held_back": 0}),
            **clicks.get(c.id, {"clicks": 0, "clickers": 0}),
        } for c in campaigns],
    }


def _message_rows(db: Session, query, paging: dict) -> list:
    """A page of message rows, with click data fetched for the page in one query."""
    messages = (query.order_by(SMSMessage.id.desc())
                .offset(paging["offset"]).limit(paging["per_page"]).all())
    clicks = link_service.clicks_for_messages(db, [m.id for m in messages])
    return [{
        "id": m.id,
        "campaign_id": m.campaign_id,
        "contact_id": m.contact_id,
        "phone": m.phone,
        "message": m.message,
        "status": m.status,
        "segments": m.segments,
        "sent_at": m.sent_at,
        "delivered_at": m.delivered_at,
        "top_up_at": m.top_up_at,
        # Scrubbed on the way out — see the module docstring.
        "error_message": scrub_provider_text(m.error_message),
        **{k: v for k, v in (clicks.get(m.id)
                             or {"clicks": 0, "bot_clicks": 0,
                                 "last_clicked_at": None}).items()},
    } for m in messages]


def campaign_messages(db: Session, campaign_id: int, page: int = 1,
                      per_page: Optional[int] = None,
                      status: Optional[str] = None) -> dict:
    """One page of a campaign's recipients."""
    query = db.query(SMSMessage).filter(SMSMessage.campaign_id == campaign_id)
    if status:
        query = query.filter(SMSMessage.status == status)
    total = query.with_entities(func.count(SMSMessage.id)).scalar() or 0
    paging = _paging(page, per_page, total)
    return {**{k: v for k, v in paging.items() if k != "offset"},
            "messages": _message_rows(db, query, paging)}


def contact_history(db: Session, contact_id: int, page: int = 1,
                    per_page: Optional[int] = None) -> Optional[dict]:
    """Everything one person has been sent, newest first. None if no such contact.

    The campaign name travels with each row. "Campaign 47" is not what makes a
    phone call possible; "the flooring sale on Tuesday" is, and that is the
    whole reason this screen exists.
    """
    contact = db.get(Contact, contact_id)
    if not contact:
        return None

    query = db.query(SMSMessage).filter(SMSMessage.contact_id == contact_id)
    total = query.with_entities(func.count(SMSMessage.id)).scalar() or 0
    paging = _paging(page, per_page, total)
    rows = _message_rows(db, query, paging)

    names = {cid: name for cid, name in
             db.query(Campaign.id, Campaign.name)
             .filter(Campaign.id.in_({r["campaign_id"] for r in rows if r["campaign_id"]}))
             .all()} if rows else {}
    for row in rows:
        row["campaign_name"] = names.get(row["campaign_id"])

    return {
        **{k: v for k, v in paging.items() if k != "offset"},
        "contact": {
            "id": contact.id,
            "name": contact.display_name(),
            "phone": contact.phone,
            "last_messaged_at": contact.last_messaged_at,
        },
        "messages": rows,
    }
