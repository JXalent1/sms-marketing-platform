"""Everything the Today screen shows.

One service, because the alternative is what the reference build did: every
figure was computed at the place it was displayed, so the dashboard's contact
count and the nav's contact count disagreed for months and neither was wrong on
its own terms. The router calls one function and serializes it; the template
renders what comes back and does no arithmetic of its own.

Two rules this file exists to hold:

  1. **Freshness comes from actual sends, never from a campaign's audience
     string.** A campaign row records who it *meant* to text; `sms_messages`
     records who was actually texted. The message table stays correct when a
     campaign targets a union of two lists, when a send was aborted
     half-way, and when someone edits a saved selector afterwards — and it needs
     nothing from the campaign schema, which is being extended in a parallel
     session.

  2. **No money is computed here.** Every currency figure comes from
     billing_service, which is the only module that knows the client's rate.
"""

from datetime import date, datetime, timedelta
from typing import List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.campaign import Campaign
from app.models.blocked_number import BlockedNumber
from app.models.contact_list import ContactListMember
from app.models.sms_message import SENT_STATUSES, SMSMessage
from app.services import billing_service, blocklist_service, contact_service

# `SENT_STATUSES` — what counts as "these people have been texted" — is imported
# from the model rather than defined here. It was defined independently here and
# in `report_service`, and session 5i's per-list freshness query in
# `contact_service` would have made it three. The reasoning for keeping it apart
# from `BILLABLE_STATUSES` moved with it; read it there before binding them.

# Outcome buckets for the per-list bars. 'sent' is deliberately absent:
# it means the carrier accepted the message and has not yet reported back, so
# counting it as delivered would show 100% delivered for a campaign whose
# receipts have not landed. Those are surfaced as "awaiting receipt" instead.
OUTCOME_BUCKETS = (
    ("delivered", ("delivered",)),
    ("failed", ("failed", "undelivered")),
    ("blocked", ("blocked",)),
)

CHART_DAYS = 14
TREND_WINDOW_DAYS = 30


def _today() -> date:
    return date.today()


def _days_since(iso: Optional[str], today: date = None) -> Optional[int]:
    """Whole days between an ISO timestamp and today. None when never.

    Delegates rather than implements. `contact_service.list_summaries()` reports
    the same figure for the picker, and this screen and that one must not be
    able to disagree about how old the same send is — the same argument that
    made `preflight_service._clock()` delegate to `suppression_service`.
    """
    return contact_service.days_since(iso, today or _today())


def _iso_days_ago(days: int) -> str:
    return (_today() - timedelta(days=days)).isoformat()


# ─── List cards ─────────────────────────────────────────────────────────────

# How many list cards the grid shows, after the pinned one. Five, because the
# grid was built for five and a dashboard that grows without bound stops being a
# dashboard. The picker in the composer is not capped — it is a dropdown, and
# every list he has ever used belongs in it.
RECENT_LIST_CARDS = 5


def _card(entry: dict, threshold: int) -> dict:
    """One `list_summaries()` entry as a card the template can draw.

    `days_label` is what the template prints, and the em-dash rule is the reason
    this function exists rather than the template deciding: a list that has never
    been texted shows an em dash, **not a zero**. "0" reads as "texted today",
    which is the exact opposite of the truth, and under the old category cards
    that is what kept a whole niche from ever being picked.

    No swatch and no colour token. There is no palette token on a list, and
    colour was never the identity channel here anyway — the label was.
    """
    days = entry["days_since_sent"]
    return {
        "selector": entry["selector"],
        "label": entry["label"],
        "kind": entry["kind"],
        "contacts": entry["count"],
        "last_sent_at": entry["last_sent_at"],
        "days_since_last_send": days,
        "days_label": "—" if days is None else str(days),
        "days_caption": "never texted" if days is None
                        else ("today" if days == 0
                              else ("day ago" if days == 1 else "days ago")),
        "stale": days is not None and days > threshold,
    }


def list_cards(db: Session) -> List[dict]:
    """The pinned entry, then the five most recent lists.

    Session 5i replaced the five category cards with these. Same card shape,
    same grid, same staleness threshold and the same em-dash rule — what changed
    is what a card is *about*, because the client's question is "when did I last
    text these people", and after 5i "these people" is a named list rather than
    one of five niches that never fitted a yacht auction.

    Built from `contact_service.list_summaries()` rather than from a second
    query, so the card and the dropdown entry for the same list cannot report
    different counts or different freshness. That ordering — newest first, by a
    parsed `created_at` — is the picker's, and it is the whole reason A1 had to
    land first.
    """
    threshold = settings.DASHBOARD_STALE_DAYS
    entries = contact_service.list_summaries(db)

    pinned = [e for e in entries if e["kind"] == "all"]
    lists = [e for e in entries if e["kind"] == "list"][:RECENT_LIST_CARDS]
    return [_card(e, threshold) for e in pinned + lists]


# ─── The hero ───────────────────────────────────────────────────────────────

def _list_for_campaign(db: Session, campaign: Campaign,
                       cards: List[dict]) -> Optional[dict]:
    """The card this campaign is aimed at, or None.

    Matched on the campaign's own audience selector, which is the record of what
    it was pointed at. `list:47` finds the card for list 47; `all` finds the
    pinned card. Anything else — a `category:` selector on a campaign predating
    5i, a `source:` selector, a list that has fallen off the five most recent —
    finds nothing, and the hero then prints an em dash rather than guessing. A
    guessed figure here is indistinguishable from a measured one, and this is
    the tile he schedules against.
    """
    selector = (campaign.audience or "").strip()
    return next((c for c in cards if c["selector"] == selector), None)


def next_up(db: Session, cards: List[dict]) -> Optional[dict]:
    """The campaign the hero describes, or None for the empty state.

    There is no auctions table and this does not invent one. In order:
      1. the soonest campaign scheduled to send in the future, if scheduling
         exists yet — guarded on the attribute, since it arrives in a session
         running alongside this one;
      2. otherwise the most recently created draft;
      3. otherwise nothing, and the template offers the Compose button.
    """
    campaign = None
    scheduled_at = getattr(Campaign, "scheduled_at", None)
    if scheduled_at is not None:
        campaign = (db.query(Campaign)
                    .filter(scheduled_at.isnot(None),
                            scheduled_at >= datetime.now().isoformat(),
                            Campaign.status.notin_(("completed", "aborted", "failed")))
                    .order_by(scheduled_at.asc())
                    .first())

    if campaign is None:
        campaign = (db.query(Campaign)
                    .filter(Campaign.status == "draft")
                    .order_by(Campaign.created_at.desc(), Campaign.id.desc())
                    .first())

    if campaign is None:
        return None

    card = _list_for_campaign(db, campaign, cards)
    try:
        audience = contact_service.audience_count(db, campaign.audience)
    except ValueError:
        # A saved selector with a typo in it. The hero says so rather than
        # showing a confident zero, which is indistinguishable from an empty
        # list and is how a campaign gets "sent" to nobody.
        audience = None

    return {
        "id": campaign.id,
        "name": campaign.name,
        "status": campaign.status,
        "scheduled_at": getattr(campaign, "scheduled_at", None),
        "list": card,
        "audience_selector": campaign.audience,
        "audience_label": contact_service.audience_label(db, campaign.audience),
        "audience_count": audience,
        "days_label": card["days_label"] if card else None,
        "days_caption": card["days_caption"] if card else None,
    }


# ─── Stat tiles ─────────────────────────────────────────────────────────────

def _count_in_window(db: Session, statuses, start: str, end: str) -> int:
    return (db.query(func.count(SMSMessage.id))
            .filter(SMSMessage.status.in_(statuses),
                    SMSMessage.sent_at >= start,
                    SMSMessage.sent_at < end)
            .scalar()) or 0


def stat_tiles(db: Session) -> List[dict]:
    """The four figures across the top. Every currency value via billing_service."""
    now = _today().isoformat() + "T99"
    start_30 = _iso_days_ago(TREND_WINDOW_DAYS)
    start_60 = _iso_days_ago(TREND_WINDOW_DAYS * 2)

    delivered = _count_in_window(db, ("delivered",), start_30, now)
    prior = _count_in_window(db, ("delivered",), start_60, start_30)
    if prior:
        change = round((delivered - prior) / prior * 100)
        change_label = f"{change:+d}% vs prior 30 days"
    else:
        change_label = "no prior 30-day period to compare"

    # Opt-outs against what was actually sent in the same window. A raw count of
    # STOPs is meaningless without the denominator: 40 opt-outs is healthy after
    # 20,000 messages and alarming after 300.
    #
    # Filtered on blocklist_service.OPT_OUT_REASONS rather than a literal, so
    # this tile and the Opt-outs screen's headline cannot come to disagree. It
    # was `reason == "stop_keyword"` until session 5g added `carrier_opt_out`;
    # a literal here would have left the two screens reporting different
    # opt-out counts from the same table.
    sent_30 = _count_in_window(db, SENT_STATUSES, start_30, now)
    opt_outs = (db.query(func.count(BlockedNumber.id))
                .filter(BlockedNumber.reason.in_(blocklist_service.OPT_OUT_REASONS),
                        BlockedNumber.blocked_at >= start_30)
                .scalar()) or 0
    rate = f"{(opt_outs / sent_30 * 100):.2f}%" if sent_30 else "—"

    usage = billing_service.current_usage(db)

    return [
        {"key": "delivered", "label": "Delivered", "value": f"{delivered:,}",
         "sub": change_label},
        {"key": "opt_outs", "label": "Opt-out rate", "value": rate,
         "sub": f"{opt_outs:,} in the last 30 days"},
        {"key": "segments", "label": "Segments this cycle",
         "value": f"{usage['used_segments']:,}",
         "sub": f"{usage['included_segments']:,} included · "
                f"{usage['billable_segments']:,} billable"},
        {"key": "cost", "label": "Cost this cycle",
         "value": f"${usage['total_due']:,.2f}", "sub": usage["month"]},
    ]


# ─── 14-day segment chart ───────────────────────────────────────────────────

def segment_chart(db: Session, days: int = CHART_DAYS) -> dict:
    """Segments sent per day, oldest first.

    A day with no send is a faint rule rather than a gap — an absent bar and a
    zero bar look identical, and the client reads this chart to answer "have we
    gone quiet?", where those are the two answers that matter.
    """
    today = _today()
    start = (today - timedelta(days=days - 1)).isoformat()

    rows = (db.query(func.substr(SMSMessage.sent_at, 1, 10),
                     func.sum(func.coalesce(SMSMessage.segments, 1)))
            .filter(SMSMessage.status.in_(SENT_STATUSES),
                    SMSMessage.sent_at >= start)
            .group_by(func.substr(SMSMessage.sent_at, 1, 10))
            .all())
    by_day = {day: int(total or 0) for day, total in rows if day}

    peak = max(by_day.values()) if by_day else 0
    bars = []
    for offset in range(days):
        day = today - timedelta(days=days - 1 - offset)
        segments = by_day.get(day.isoformat(), 0)
        bars.append({
            "date": day.isoformat(),
            "tick": day.strftime("%-d") if offset % 2 == 0 else "",
            "label": day.strftime("%a %-d %b"),
            "segments": segments,
            # Floored so a one-segment day is still visible; 0 keeps the rule.
            "pct": max(6, round(segments / peak * 100)) if peak and segments else 0,
        })

    return {"bars": bars, "peak": peak, "total": sum(by_day.values()), "days": days}


# ─── Per-list last-send outcomes ────────────────────────────────────────────

def last_send_outcomes(db: Session, cards: List[dict], limit: int = 3) -> List[dict]:
    """Delivered / failed / blocked for each list's most recent campaign.

    Scoped to one campaign rather than a date window because that is the
    question being asked — "how did the last blast to these people go?" — and
    because a blocked message has no `sent_at` at all, so a window would silently
    drop exactly the outcome worth seeing.

    Keyed on list membership since 5i, where it was keyed on category tags. Same
    shape, same reasoning; a campaign that reached members of two lists shows
    under both, exactly as one that targeted two categories used to.

    The pinned "all" card is deliberately absent: every campaign reaches some of
    "all bidders", so a row for it would be the most recent campaign restated
    under a heading that says "by list", three times out of three.
    """
    list_ids = [c["selector"].split(":", 1)[1] for c in cards
                if c["kind"] == "list" and ":" in c["selector"]]
    list_ids = [int(i) for i in list_ids]
    if not list_ids:
        return []

    pairs = (db.query(ContactListMember.list_id, SMSMessage.campaign_id,
                      func.max(SMSMessage.sent_at))
             .join(SMSMessage, SMSMessage.contact_id == ContactListMember.contact_id)
             .filter(ContactListMember.list_id.in_(list_ids),
                     SMSMessage.status.in_(SENT_STATUSES),
                     SMSMessage.sent_at.isnot(None),
                     SMSMessage.campaign_id.isnot(None))
             .group_by(ContactListMember.list_id, SMSMessage.campaign_id)
             .all())

    latest = {}
    for list_id, campaign_id, sent_at in pairs:
        if list_id not in latest or sent_at > latest[list_id][1]:
            latest[list_id] = (campaign_id, sent_at)

    chosen = sorted(latest.items(), key=lambda kv: kv[1][1], reverse=True)[:limit]
    if not chosen:
        return []

    by_card = {int(c["selector"].split(":", 1)[1]): c for c in cards
               if c["kind"] == "list" and ":" in c["selector"]}
    counts = (db.query(ContactListMember.list_id, SMSMessage.campaign_id,
                       SMSMessage.status, func.count(SMSMessage.id))
              .join(SMSMessage, SMSMessage.contact_id == ContactListMember.contact_id)
              .filter(ContactListMember.list_id.in_([c[0] for c in chosen]),
                      SMSMessage.campaign_id.in_([c[1][0] for c in chosen]))
              .group_by(ContactListMember.list_id, SMSMessage.campaign_id,
                        SMSMessage.status)
              .all())

    tally = {}
    for list_id, campaign_id, status, count in counts:
        tally.setdefault((list_id, campaign_id), {})[status] = count

    out = []
    for list_id, (campaign_id, sent_at) in chosen:
        card = by_card.get(list_id)
        if card is None:                       # dropped off the card grid since
            continue
        statuses = tally.get((list_id, campaign_id), {})
        total = sum(statuses.get(s, 0) for _, group in OUTCOME_BUCKETS for s in group)
        segments = []
        for name, group in OUTCOME_BUCKETS:
            count = sum(statuses.get(s, 0) for s in group)
            segments.append({
                "key": name,
                "count": count,
                # Direct-labelled beside the bar, not hidden in a tooltip: the
                # label is what keeps this readable without relying on hue.
                "pct": round(count / total * 100) if total else 0,
            })
        out.append({
            "list": card,
            "campaign_id": campaign_id,
            "sent_at": sent_at,
            "total": total,
            "awaiting_receipt": statuses.get("sent", 0),
            "segments": segments,
        })
    return out


# ─── The whole screen ───────────────────────────────────────────────────────

def dashboard(db: Session) -> dict:
    cards = list_cards(db)
    return {
        "next_up": next_up(db, cards),
        "lists": cards,
        "tiles": stat_tiles(db),
        "chart": segment_chart(db),
        "outcomes": last_send_outcomes(db, cards),
        "stale_days": settings.DASHBOARD_STALE_DAYS,
    }
