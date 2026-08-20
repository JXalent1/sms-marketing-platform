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
     campaign targets a union of two categories, when a send was aborted
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
from app.models.category import Category, ContactCategory
from app.models.contact import Contact
from app.models.sms_message import SMSMessage
from app.services import billing_service, contact_service

# What counts as "this category has been texted".
#
# Deliberately not imported from BILLABLE_STATUSES even though the two sets are
# identical today. "Did this reach a handset?" and "do we invoice for this?" are
# separate questions that happen to share an answer; binding them together means
# a commercial change to the billable set would silently rewrite the freshness
# figures the client schedules his auctions against.
SENT_STATUSES = ("sent", "delivered")

# Outcome buckets for the per-category bars. 'sent' is deliberately absent:
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

    None and 0 are opposite facts — "never texted" and "texted this morning" —
    so this never collapses one into the other.
    """
    if not iso:
        return None
    try:
        when = date.fromisoformat(str(iso)[:10])
    except ValueError:
        return None
    return max(0, ((today or _today()) - when).days)


def _iso_days_ago(days: int) -> str:
    return (_today() - timedelta(days=days)).isoformat()


# ─── Category cards ─────────────────────────────────────────────────────────

def _last_sent_by_category(db: Session) -> dict:
    """category_id -> most recent send timestamp. One grouped query.

    Joins messages to the tagging table rather than to the campaign, so a
    campaign that targeted two categories refreshes both.
    """
    rows = (db.query(ContactCategory.category_id, func.max(SMSMessage.sent_at))
            .join(SMSMessage, SMSMessage.contact_id == ContactCategory.contact_id)
            .filter(SMSMessage.status.in_(SENT_STATUSES),
                    SMSMessage.sent_at.isnot(None))
            .group_by(ContactCategory.category_id)
            .all())
    return {category_id: last for category_id, last in rows if last}


def _contacts_by_category(db: Session) -> dict:
    """category_id -> active contacts carrying it. One grouped query."""
    rows = (db.query(ContactCategory.category_id, func.count(Contact.id))
            .join(Contact, Contact.id == ContactCategory.contact_id)
            .filter(Contact.is_active == 1)
            .group_by(ContactCategory.category_id)
            .all())
    return dict(rows)


def category_cards(db: Session) -> List[dict]:
    """One card per active category, in sort_order.

    `days_label` is what the template prints. A category that has never been
    texted shows an em dash, not a zero: "0" reads as "texted today", which is
    the opposite of the truth and would keep a whole niche from ever being
    picked.
    """
    today = _today()
    last_sent = _last_sent_by_category(db)
    counts = _contacts_by_category(db)
    threshold = settings.DASHBOARD_STALE_DAYS

    cards = []
    for row in (db.query(Category)
                .filter(Category.is_active == 1)
                .order_by(Category.sort_order, Category.label).all()):
        days = _days_since(last_sent.get(row.id), today)
        cards.append({
            "id": row.id,
            "slug": row.slug,
            "label": row.label,
            "color_token": row.color_token,
            "contacts": counts.get(row.id, 0),
            "last_sent_at": last_sent.get(row.id),
            "days_since_last_send": days,
            "days_label": "—" if days is None else str(days),
            "days_caption": "never texted" if days is None
                            else ("today" if days == 0
                                  else ("day ago" if days == 1 else "days ago")),
            "stale": days is not None and days > threshold,
        })
    return cards


# ─── The hero ───────────────────────────────────────────────────────────────

def _category_for_campaign(db: Session, campaign: Campaign,
                           cards: List[dict]) -> Optional[dict]:
    """The card this campaign is aimed at, or None.

    Prefers `campaigns.category_id` when that column exists — a parallel session
    is adding it — and otherwise reads the first category out of the audience
    selector, which is how module 2 already stores the same fact.
    """
    category_id = getattr(campaign, "category_id", None)
    if category_id:
        return next((c for c in cards if c["id"] == category_id), None)

    selector = (campaign.audience or "")
    for term in selector.replace("&", ",").split(","):
        term = term.strip()
        if term.startswith("category:"):
            slug = term.split(":", 1)[1].split(",")[0].strip()
            return next((c for c in cards if c["slug"] == slug), None)
    return None


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

    card = _category_for_campaign(db, campaign, cards)
    try:
        audience = contact_service.audience_count(db, campaign.audience)
    except ValueError:
        # A saved selector with a typo in it. The hero says so rather than
        # showing a confident zero, which is indistinguishable from an empty
        # category and is how a campaign gets "sent" to nobody.
        audience = None

    return {
        "id": campaign.id,
        "name": campaign.name,
        "status": campaign.status,
        "scheduled_at": getattr(campaign, "scheduled_at", None),
        "category": card,
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
    sent_30 = _count_in_window(db, SENT_STATUSES, start_30, now)
    opt_outs = (db.query(func.count(BlockedNumber.id))
                .filter(BlockedNumber.reason == "stop_keyword",
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


# ─── Per-category last-send outcomes ────────────────────────────────────────

def last_send_outcomes(db: Session, cards: List[dict], limit: int = 3) -> List[dict]:
    """Delivered / failed / blocked for each category's most recent campaign.

    Scoped to one campaign rather than a date window because that is the
    question being asked — "how did the last blast to these people go?" — and
    because a blocked message has no sent_at at all, so a window would silently
    drop exactly the outcome worth seeing.
    """
    pairs = (db.query(ContactCategory.category_id, SMSMessage.campaign_id,
                      func.max(SMSMessage.sent_at))
             .join(SMSMessage, SMSMessage.contact_id == ContactCategory.contact_id)
             .filter(SMSMessage.status.in_(SENT_STATUSES),
                     SMSMessage.sent_at.isnot(None),
                     SMSMessage.campaign_id.isnot(None))
             .group_by(ContactCategory.category_id, SMSMessage.campaign_id)
             .all())

    latest = {}
    for category_id, campaign_id, sent_at in pairs:
        if category_id not in latest or sent_at > latest[category_id][1]:
            latest[category_id] = (campaign_id, sent_at)

    chosen = sorted(latest.items(), key=lambda kv: kv[1][1], reverse=True)[:limit]
    if not chosen:
        return []

    by_card = {c["id"]: c for c in cards}
    counts = (db.query(ContactCategory.category_id, SMSMessage.campaign_id,
                       SMSMessage.status, func.count(SMSMessage.id))
              .join(SMSMessage, SMSMessage.contact_id == ContactCategory.contact_id)
              .filter(ContactCategory.category_id.in_([c[0] for c in chosen]),
                      SMSMessage.campaign_id.in_([c[1][0] for c in chosen]))
              .group_by(ContactCategory.category_id, SMSMessage.campaign_id,
                        SMSMessage.status)
              .all())

    tally = {}
    for category_id, campaign_id, status, count in counts:
        tally.setdefault((category_id, campaign_id), {})[status] = count

    out = []
    for category_id, (campaign_id, sent_at) in chosen:
        card = by_card.get(category_id)
        if card is None:                       # deactivated since it was texted
            continue
        statuses = tally.get((category_id, campaign_id), {})
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
            "category": card,
            "campaign_id": campaign_id,
            "sent_at": sent_at,
            "total": total,
            "awaiting_receipt": statuses.get("sent", 0),
            "segments": segments,
        })
    return out


# ─── The whole screen ───────────────────────────────────────────────────────

def dashboard(db: Session) -> dict:
    cards = category_cards(db)
    return {
        "next_up": next_up(db, cards),
        "categories": cards,
        "tiles": stat_tiles(db),
        "chart": segment_chart(db),
        "outcomes": last_send_outcomes(db, cards),
        "stale_days": settings.DASHBOARD_STALE_DAYS,
    }
