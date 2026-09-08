"""The per-campaign report: what one blast actually did.

The client can send. Until 5f he could not answer "did it work?" — not for
himself, and not for the auction house paying for it. This module is that
answer, for one campaign, in one call.

Three rules it keeps.

**His money, never ours.** Every dollar figure comes from `billing_service` at
`BILLING_PRICE_PER_SEGMENT`. `WHOLESALE_COST_PER_SEGMENT` and the
`carrier_cost*` columns on `sms_messages` are what we pay the carrier; they are
reconciled in `cost_reconciliation.py`, which no route reaches, and they must
not appear in anything built here. Session 1b removed a leak of exactly that
kind and CLAUDE.md still carries it.

**Counted in the database, not in the page.** 5d's Opt-outs headline is the
precedent: a screen that tallies a capped list under-reports the day the list
overflows, and it under-reports in the direction nobody sanity-checks. Every
number below is a grouped query.

**A campaign that reached nobody says so in the wording 5h already produces.**
`abort_reason` is rendered verbatim; there is no second sentence about the same
fact. Decision 006 settled what those sentences say and a report that
paraphrased them would be the third copy.

Clicks are reported as two numbers, always. The human count leads and the
filtered count sits beside it, because the classification is a heuristic
(`click_classifier.py`) and a bare number that quietly excludes things is the
defect rather than the filtering.
"""

import logging
from datetime import date, datetime
from typing import List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.blocked_number import BlockedNumber
from app.models.campaign import Campaign
from app.models.category import Category
from app.models.short_link import ShortLink
from app.models.sms_message import (
    BILLABLE_STATUSES, HELD_BACK_STATUS, SENT_STATUSES, SMSMessage,
)
from app.services import billing_service

logger = logging.getLogger("reports")

# `SENT_STATUSES` — what "this message arrived" means on a report — is imported
# above rather than defined here. It was defined independently here and in
# `dashboard_service`, and session 5i's per-list freshness query would have made
# it three. It lives in `app/models/sms_message.py` beside `BILLABLE_STATUSES`,
# with the reasoning for keeping the two apart. `history_service` imports it
# from the model too; it used to import this module's copy.


def top_up_history(db: Session, campaign_ids: List[int]) -> dict:
    """{campaign_id: [{added_at, recipients}, …]} — "1,200 + 5 added 26 Aug".

    Counted in the database rather than in the page. A screen that tallied this
    from a capped list of message rows would under-report the day a campaign
    outgrew the cap, and it would under-report the *original* send, which is the
    direction nobody sanity-checks.

    One grouped query for the whole page, not one per campaign: the campaign rail
    renders up to fifty rows and a per-row lookup here would leave every other
    assertion in the suite passing while the list quietly became fifty queries.
    The original send (`top_up_at IS NULL`) is excluded — it is the campaign's own
    `total_recipients` minus these, and returning it as a "top-up" would have
    every caller filter it back out.

    Rows a top-up *held back* are excluded too, and that is a 5h correction
    rather than a refinement. The rail renders this as
    `total_recipients - added` + `added`, and `total_recipients` has never
    counted a held-back contact — so counting one here reported the original
    send as smaller than it was, in the direction nobody sanity-checks. Before
    `held_back` existed there was no way to write this filter; the query could
    not tell a top-up's held-back row from a top-up's sent one.

    **Going forward only.** A pre-5h top-up wrote its suppressed newcomers as
    `skipped` with a `top_up_at` stamp, and those rows still pass this filter —
    correctly, under the no-backfill rule, since a `skipped` row cannot be
    classified after the fact. A box carrying pre-5h top-ups keeps the old
    under-report on those campaigns; nothing built after this change does.
    """
    from sqlalchemy import func

    if not campaign_ids:
        return {}

    rows = (db.query(SMSMessage.campaign_id, SMSMessage.top_up_at,
                     func.count(SMSMessage.id))
            .filter(SMSMessage.campaign_id.in_(campaign_ids),
                    SMSMessage.top_up_at.isnot(None),
                    SMSMessage.status != HELD_BACK_STATUS)
            .group_by(SMSMessage.campaign_id, SMSMessage.top_up_at)
            .order_by(SMSMessage.campaign_id, SMSMessage.top_up_at)
            .all())

    history = {campaign_id: [] for campaign_id in campaign_ids}
    for campaign_id, stamp, count in rows:
        history.setdefault(campaign_id, []).append(
            {"added_at": stamp, "recipients": count})
    return history


# ─── The per-campaign report ────────────────────────────────────────────────

def _status_counts(db: Session, campaign_id: int) -> dict:
    """{status: {messages, segments}} for one campaign. One grouped query."""
    rows = (db.query(SMSMessage.status, func.count(SMSMessage.id),
                     func.coalesce(func.sum(SMSMessage.segments), 0))
            .filter(SMSMessage.campaign_id == campaign_id)
            .group_by(SMSMessage.status).all())
    return {status: {"messages": count, "segments": int(segments or 0)}
            for status, count, segments in rows}


def _click_totals(db: Session, campaign_id: int) -> dict:
    """Human clicks, filtered clicks, links minted, and distinct human clickers.

    Two queries rather than one with a CASE, because the second asks a different
    question: "how many *people* opened it" is what an auction house acts on,
    and one buyer who taps the link four times is one buyer.
    """
    clicks, bots, minted = (
        db.query(func.coalesce(func.sum(ShortLink.click_count), 0),
                 func.coalesce(func.sum(ShortLink.bot_click_count), 0),
                 func.count(ShortLink.id))
        .filter(ShortLink.campaign_id == campaign_id).one())
    clickers = (db.query(func.count(ShortLink.id))
                .filter(ShortLink.campaign_id == campaign_id,
                        ShortLink.click_count > 0).scalar() or 0)
    return {"clicks": int(clicks or 0), "filtered_clicks": int(bots or 0),
            "links": int(minted or 0), "clickers": int(clickers)}


def _opt_outs(db: Session, campaign: Campaign) -> int:
    """People on this campaign's recipient list who opted out after it went out.

    Attribution by phone and by time, and deliberately not by anything cleverer.
    A STOP arrives on an inbound webhook that knows nothing about campaigns, so
    "who opted out because of this message" is not a fact the system holds; what
    it holds is "this number was texted by this campaign and blocked after it
    started", which is the honest approximation and is what the label on screen
    says.

    A sub-select rather than an `IN` over four thousand phone strings — the
    idiom `contact_query_service` uses, and for the same reason.

    Both timestamps are written by `datetime.now().isoformat()` — `blocked_at`
    in `blocklist_service` and `started_at` here — so the string comparison is
    between one clock and one spelling. That is not free: `contact_list_members.
    added_at` carries two of each, which is what CLAUDE.md's "a column with a
    server default has a second writer" entry is about, and this column has no
    server default. A row with a NULL `blocked_at` falls out of the comparison
    and is not counted, which is the safe direction: an under-reported opt-out
    figure is a number he checks against the Opt-outs screen, and an
    over-reported one is a campaign blamed for somebody else's STOP.
    """
    since = campaign.started_at or campaign.created_at
    if not since:
        return 0
    return (db.query(func.count(BlockedNumber.id))
            .filter(BlockedNumber.phone.in_(
                        db.query(SMSMessage.phone)
                        .filter(SMSMessage.campaign_id == campaign.id)
                        .scalar_subquery()),
                    BlockedNumber.blocked_at >= since)
            .scalar() or 0)


def _cycle_date(campaign: Campaign) -> date:
    """Which billing cycle this campaign's cost belongs to.

    Its send, not today: a report opened in September on an August campaign has
    to price it against August's allowance. Falls back to today only when the
    campaign carries no usable timestamp at all, which no campaign built by this
    codebase does.
    """
    for stamp in (campaign.started_at, campaign.created_at):
        try:
            return datetime.fromisoformat(stamp).date()
        except (TypeError, ValueError):
            continue
    return date.today()


def campaign_cost(db: Session, campaign: Campaign, own_segments: int) -> dict:
    """What this campaign added to the client's bill, at his rate.

    Not `segments * rate`, for `preflight_totals.marginal_cost()`'s reason: the
    plan includes 10,000 segments a month, so a campaign that sat entirely
    inside the allowance cost nothing and quoting the flat rate for it would be
    a bill he never received. The honest figure is the *difference* the campaign
    makes to its own cycle:

        cost = cost_for_segments(cycle_total) - cost_for_segments(cycle_total - this)

    which prices the campaign as the last thing in its cycle. Both terms are
    exact Decimals from `billing_service` and the subtraction happens before any
    rounding, so the half-cent boundary is intact when `to_money()` sees it —
    the 1b lesson, one layer along.

    The consequence worth naming: two campaigns in one cycle are each priced as
    the marginal one, so their costs do not sum to the cycle total when the
    allowance is crossed between them. That is a property of an allowance, not
    an error, and the screen says "added to this month's bill" rather than
    "cost" for exactly that reason. The cycle total is on the Usage screen and
    remains the one number that is billed.
    """
    cycle_start, cycle_end, _, label = billing_service.get_billing_cycle(
        _cycle_date(campaign), db=db)
    _, cycle_segments = billing_service.compute_usage(db, cycle_start, cycle_end)
    before = max(0, cycle_segments - max(0, own_segments))
    added = (billing_service.cost_for_segments(cycle_segments)
             - billing_service.cost_for_segments(before))
    return {
        "billing_month": label,
        "segments": own_segments,
        "cycle_segments": cycle_segments,
        "cost": billing_service.to_money(added),
        "price_per_segment": settings.BILLING_PRICE_PER_SEGMENT,
        "included_segments": settings.BILLING_SEGMENTS_INCLUDED,
    }


def campaign_report(db: Session, campaign_id: int) -> Optional[dict]:
    """Everything one campaign's report screen shows. None if it does not exist.

    Bounded work: eight queries whatever the campaign's size. Nothing here reads
    a message row individually — the recipient table is a separate, paginated
    call (`history_service.campaign_messages`), because a campaign has 4,000 of
    them and the box is a 1vCPU droplet.
    """
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        return None

    by_status = _status_counts(db, campaign_id)
    category = db.get(Category, campaign.category_id) if campaign.category_id else None

    def messages(*statuses) -> int:
        return sum(by_status.get(s, {}).get("messages", 0) for s in statuses)

    sent = messages(*SENT_STATUSES)
    # Delivery is `delivered_at`, not the status, and the two are not the same
    # set: a late failure webhook moves a delivered row to `undelivered` while
    # the handset receipt stands. `routers/campaigns.get_campaign` has counted
    # it this way since the skeleton and a second definition here is how one
    # screen comes to disagree with another.
    delivered = (db.query(func.count(SMSMessage.id))
                 .filter(SMSMessage.campaign_id == campaign_id,
                         SMSMessage.delivered_at.isnot(None)).scalar() or 0)
    billed_segments = sum(by_status.get(s, {}).get("segments", 0)
                          for s in BILLABLE_STATUSES)

    clicks = _click_totals(db, campaign_id)
    return {
        "campaign": {
            "id": campaign.id,
            "name": campaign.name,
            "status": campaign.status,
            "message_template": campaign.message_template,
            "audience_label": campaign.audience_label,
            "category_label": category.label if category else None,
            "category_color_token": category.color_token if category else None,
            "cross_category_override": bool(campaign.cross_category_override),
            "created_at": campaign.created_at,
            "started_at": campaign.started_at,
            "completed_at": campaign.completed_at,
            "scheduled_at": campaign.scheduled_at,
            # Rendered verbatim by every surface. 5h and decision 006 settled
            # what a refusal says; a report that paraphrased it would be a
            # third sentence about one fact.
            "abort_reason": campaign.abort_reason,
            "link_target_url": campaign.link_target_url,
        },
        "outcome": {
            "recipients": campaign.total_recipients or 0,
            "sent": sent,
            "delivered": delivered,
            "failed": messages("failed", "undelivered"),
            "held_back": messages(HELD_BACK_STATUS),
            "blocked": messages("blocked"),
            "skipped": messages("skipped"),
            "not_sent": messages("not_sent"),
            "pending": messages("pending"),
            "opted_out": _opt_outs(db, campaign),
        },
        # Two numbers, always. The filtered count is shown beside the human one
        # rather than folded away: SMS links are opened by carrier scanners and
        # handset previews before any person sees them, the classification is a
        # heuristic, and a bare number that quietly excludes things is not
        # honest about being one.
        "clicks": {
            **clicks,
            "click_through_rate": round(100.0 * clicks["clickers"] / sent, 1)
            if sent else None,
        },
        "cost": campaign_cost(db, campaign, billed_segments),
        # "1,200 + 5 added 27 Aug" — top-up contributions stay visible rather
        # than being merged into a total that quietly changed.
        "top_ups": top_up_history(db, [campaign_id]).get(campaign_id, []),
    }
