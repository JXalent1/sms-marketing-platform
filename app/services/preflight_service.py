"""Pre-flight checks and recent-contact suppression.

Everything in here answers one question: *is this the right message, to the
right people, at a price he agreed to?* The client runs a different-niche
auction almost every day, and the failure this module exists to prevent is a
memorabilia collector getting a text about a walk-in cooler.

Two design rules worth stating.

**The checks compute; the UI renders.** Every check returns the same shape —
key, label, status, reason — so `campaigns.html` can draw a checklist without
knowing what any individual check means. A check whose wording lives in the
template is a check that says something different on the API than on the screen.

**Money here is the client's money.** Every dollar figure in this module comes
from `billing_service` at `BILLING_PRICE_PER_SEGMENT`.
`WHOLESALE_COST_PER_SEGMENT` is what we pay the carrier; it funds the capacity
check in `campaign_service` and our own logs, and it must not appear in anything
built here. Session 1b removed a leak of exactly that kind.

The capacity check itself is *not* implemented here. It lives in
`campaign_service.CampaignService.capacity_assessment()`, where the send path
already calls it; this module only re-states its verdict as a checklist row.
Surfacing it is additive — nothing in this file can weaken it, and nothing here
is on the path between a send and that check.
"""

import re
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Callable, List, Optional, Sequence, Tuple

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.category import Category
from app.services import billing_service
from app.sms.compliance import STOP_KEYWORDS
from app.sms.phone import find_risky_links
from app.sms.segments import count_segments, describe

PASS, WARN, FAIL = "pass", "warn", "fail"

# How much of the message counts as "the opening" for the brand check. A carrier
# trust-and-safety review reads the first line; so does a recipient deciding
# whether this is a scam. Naming yourself in the footer is not identifying
# yourself.
OPENING_CHARS = 64


def _check(key: str, label: str, status: str, reason: str, **extra) -> dict:
    return {"key": key, "label": label, "status": status, "reason": reason, **extra}


# ─── Recent-contact suppression ─────────────────────────────────────────────

def suppression_cutoff(now: Optional[datetime] = None) -> str:
    """ISO timestamp before which a contact is considered "not texted recently".

    Returned as a string because `contacts.last_messaged_at` is an ISO string
    and both are produced by `datetime.isoformat()` — same format, so a
    lexicographic comparison is a chronological one. Parsing every contact's
    timestamp to compare it would be the same answer, slower, and would throw on
    the one malformed row.
    """
    now = now or datetime.now()
    return (now - timedelta(days=settings.RECENT_CONTACT_SUPPRESSION_DAYS)).isoformat()


def partition_recent(recipients: Sequence, cutoff: Optional[str] = None) -> Tuple[List, List]:
    """Split recipients into (sendable, recently texted).

    Deliberately blind to category. A buyer tagged Food Service, Equipment and
    Estates is one person with one phone, and three correct campaigns in a week
    is still three texts in a week to him. The reference system suppressed
    within a list and re-texted the overlap; the overlap is where the opt-outs
    came from.
    """
    cutoff = cutoff or suppression_cutoff()
    sendable, suppressed = [], []
    for contact in recipients:
        last = getattr(contact, "last_messaged_at", None)
        (suppressed if last and last > cutoff else sendable).append(contact)
    return sendable, suppressed


def suppression_reason() -> str:
    days = settings.RECENT_CONTACT_SUPPRESSION_DAYS
    return (f"Texted within the last {days} day{'s' if days != 1 else ''} — held back "
            f"so nobody gets two messages in a row")


# ─── Segments, measured on what actually reaches a handset ──────────────────

def exact_segment_totals(message_template: str, recipients: Sequence,
                         render: Callable[[str, Any], str]) -> dict:
    """Segments this send really costs: the template rendered per recipient.

    The composer's live counter measures the raw template, where `{first_name}`
    is twelve literal characters. Nobody is called `{first_name}`. Usually that
    over-counts and the quote is merely pessimistic, but the reverse happens
    too: `{name}` is six characters, so a 158-character template counts as one
    segment and renders to 163 for a Christopher — two segments, at double the
    quoted rate, discovered on the invoice.

    So the keystroke counter stays an estimate and this is the exact figure.
    Pre-flight is a deliberate action against a resolved audience: rendering the
    template `len(recipients)` times is affordable exactly here and nowhere on
    the typing path.

    `render` is passed in rather than imported. `campaign_service` already
    imports this module, and the send path's own `render()` is the one whose
    output is billed — measuring with a second copy of that logic would
    eventually measure something the send path does not produce.

    With no recipients there is nothing to render, so the template's own count
    is returned and `exact` is False. Callers must not present that as measured.
    """
    template_per_message = count_segments(message_template or "")

    if not recipients:
        return {
            "exact": False,
            "recipients": 0,
            "template_segments_per_message": template_per_message,
            "template_total_segments": 0,
            "total_segments": 0,
            "max_segments_per_message": template_per_message,
            "min_segments_per_message": template_per_message,
            "over_template_count": 0,
        }

    per_recipient = [count_segments(render(message_template, c)) for c in recipients]
    return {
        "exact": True,
        "recipients": len(per_recipient),
        "template_segments_per_message": template_per_message,
        "template_total_segments": template_per_message * len(per_recipient),
        "total_segments": sum(per_recipient),
        "max_segments_per_message": max(per_recipient),
        "min_segments_per_message": min(per_recipient),
        # The number that matters: how many people the template's own count
        # under-quotes. One is enough to make the quote wrong.
        "over_template_count": sum(1 for n in per_recipient if n > template_per_message),
    }


# ─── Cost, at the client's rate ─────────────────────────────────────────────

def marginal_cost(db: Session, added_segments: int) -> float:
    """What this send adds to the open cycle's bill, in dollars.

    Not `segments * rate`. The plan includes 10,000 segments a month, so the
    honest answer to "what does this campaign cost" depends on where the cycle
    already stands: the first campaign of the month usually costs nothing and
    the one that crosses the allowance costs only the part above it. Quoting the
    flat rate would over-state the early sends and under-state the crossing one.

    Both terms come from `billing_service.cost_for_segments()`, which is exact
    Decimal — the subtraction happens before any rounding, so the half-cent
    boundary is still intact when `to_money()` sees it. The monthly fee is in
    both terms and cancels, which is correct: a campaign does not re-charge it.
    """
    cycle_start, cycle_end, _, _ = billing_service.get_billing_cycle()
    _, used = billing_service.compute_usage(db, cycle_start, cycle_end)

    before: Decimal = billing_service.cost_for_segments(used)
    after: Decimal = billing_service.cost_for_segments(used + max(0, added_segments))
    return billing_service.to_money(after - before)


# ─── The checks ─────────────────────────────────────────────────────────────

def check_capacity(assessment: dict) -> dict:
    """Re-state the send path's own capacity verdict as a checklist row.

    `assessment` comes from `CampaignService.capacity_assessment()` — the same
    call, with the same threshold, that refuses to start a campaign the account
    cannot fund. Only `ok` and `detail` are read: the assessment also carries the
    carrier balance and our wholesale rate, and neither may travel any further
    than this function.
    """
    return _check(
        "capacity", "Sending capacity",
        PASS if assessment.get("ok") else FAIL,
        assessment.get("detail") or "",
    )


def check_opt_out_language(body: str) -> dict:
    """A STOP instruction has to be in the body, not just honoured by the code.

    We honour STOP whether or not the message says so, but a message that does
    not offer the opt-out is the one that gets reported as spam, and a carrier
    that sees enough reports filters the sender rather than the message.
    """
    words = set(re.findall(r"[A-Za-z-]+", (body or "").upper()))
    if words & STOP_KEYWORDS:
        return _check("opt_out_language", "Opt-out instruction", PASS,
                      "The message tells recipients how to stop.")
    return _check("opt_out_language", "Opt-out instruction", FAIL,
                  "No STOP instruction in the message. Add something like "
                  "\"Reply STOP to opt out.\"")


def check_brand_identified(body: str) -> dict:
    """The business has to name itself up front."""
    opening = (body or "")[:OPENING_CHARS].lower()
    names = [n for n in (settings.BRAND_NAME, settings.BRAND_SHORT_NAME) if n]
    if any(name.lower() in opening for name in names):
        return _check("brand_identified", "Business identified", PASS,
                      f"{settings.BRAND_NAME} is named in the opening.")
    return _check(
        "brand_identified", "Business identified", FAIL,
        f"{settings.BRAND_NAME} is not named in the first {OPENING_CHARS} characters. "
        f"An unidentified sender reads as a scam and gets reported as one.",
    )


def check_segment_count(segments_per_message: int) -> dict:
    """A long message is a choice; it should not be an accident."""
    ceiling = settings.PREFLIGHT_SEGMENT_CEILING
    if segments_per_message <= ceiling:
        return _check("segment_count", "Message length", PASS,
                      f"{segments_per_message} segment"
                      f"{'s' if segments_per_message != 1 else ''} per message.")
    return _check(
        "segment_count", "Message length", WARN,
        f"{segments_per_message} segments per message, above the {ceiling}-segment "
        f"guideline. Every recipient is metered {segments_per_message} times, "
        f"not once.",
    )


def check_merge_expansion(totals: dict) -> dict:
    """Does rendering push anyone past a boundary the template didn't predict?

    Only ever a warning. Going over is a real cost, not a mistake — a long name
    is not something he can fix — so this tells him the true number and lets him
    decide, rather than blocking a correct send because one buyer is called
    Christopher.
    """
    predicted = totals["template_segments_per_message"]

    if not totals["exact"]:
        return _check("merge_expansion", "Merge tags", WARN,
                      "No recipients resolved yet, so the total below is the "
                      "template's own estimate rather than a measured figure.")

    over = totals["over_template_count"]
    if not over:
        return _check(
            "merge_expansion", "Merge tags", PASS,
            f"Measured on every recipient's rendered message: "
            f"{totals['total_segments']:,} segments in total. Merge tags do not "
            f"push anyone past {predicted} segment{'s' if predicted != 1 else ''}.",
        )

    return _check(
        "merge_expansion", "Merge tags", WARN,
        f"{over:,} of {totals['recipients']:,} recipients render to "
        f"{totals['max_segments_per_message']} segments, not the {predicted} the "
        f"template predicts — their names are longer than the merge tag they "
        f"replace. The real total is {totals['total_segments']:,} segments, not "
        f"{totals['template_total_segments']:,}, and the cost below is the real one.",
        over_template_count=over,
    )


def check_recent_overlap(suppressed_count: int, sendable_count: int) -> dict:
    days = settings.RECENT_CONTACT_SUPPRESSION_DAYS
    if not suppressed_count:
        return _check("recent_overlap", "Recently texted", PASS,
                      f"Nobody in this audience was texted in the last {days} days.")
    return _check(
        "recent_overlap", "Recently texted", WARN,
        f"{suppressed_count:,} of {suppressed_count + sendable_count:,} contacts were "
        f"texted in the last {days} days and will be held back. "
        f"{sendable_count:,} will receive this message.",
        suppressed_count=suppressed_count,
    )


def check_link_shortener(body: str) -> dict:
    """Carriers filter shortened domains far harder than full ones."""
    risky = find_risky_links(body or "")
    if not risky:
        return _check("link_shortener", "Links", PASS, "No shortened links.")
    return _check(
        "link_shortener", "Links", WARN,
        f"{', '.join(risky)} is a shared link shortener. Carriers spam-filter these "
        f"hard — the send is accepted and then quietly dropped, at full price. Use a "
        f"link on a domain you own.",
        domains=risky,
    )


def _category_labels(db: Session) -> dict:
    return {slug: label for slug, label in db.query(Category.slug, Category.label).all()}


def _keyword_hits(body: str) -> List[Tuple[str, str]]:
    """(category_slug, keyword) for every configured keyword present in the body.

    Bounded on both sides by a non-word, non-hyphen character so "range" does
    not fire on "arrangements" and "walk-in" is matched as the two-word phrase
    the client actually writes.
    """
    hits = []
    for slug, keywords in (settings.CATEGORY_KEYWORDS or {}).items():
        for keyword in keywords:
            pattern = rf"(?<![\w-]){re.escape(keyword)}(?![\w-])"
            if re.search(pattern, body or "", re.IGNORECASE):
                hits.append((slug, keyword))
    return hits


def check_category_match(db: Session, category_slug: Optional[str], body: str) -> dict:
    """Does the copy sound like the category it is going to?

    This is the cheapest check in the file and the one most likely to earn its
    keep. The realistic mistake is not a mis-tagged contact — it is last night's
    fryer text, edited in a hurry, sent to tonight's memorabilia list. A keyword
    table catches that for nothing.

    It only ever warns. The keyword list is a heuristic, and a heuristic that
    can block a send will eventually block a correct one at 6pm on sale day.
    """
    if not category_slug:
        return _check("category_match", "Category match", WARN,
                      "No category on this campaign, so the copy cannot be checked "
                      "against one.")

    foreign = [(slug, kw) for slug, kw in _keyword_hits(body) if slug != category_slug]
    if not foreign:
        return _check("category_match", "Category match", PASS,
                      "Nothing in the copy belongs to another category.")

    labels = _category_labels(db)
    this_label = labels.get(category_slug, category_slug)
    # Name both sides. "Off-category keyword detected" tells him something is
    # wrong; "'drill press' is Equipment & Machinery wording and this is going to
    # Food Service" tells him which of the two is the mistake.
    parts = [f"“{kw}” ({labels.get(slug, slug)})" for slug, kw in foreign[:5]]
    return _check(
        "category_match", "Category match", WARN,
        f"This campaign is going to {this_label}, but the message uses "
        f"{', '.join(parts)} wording. Check you are sending the right message to "
        f"the right list.",
        campaign_category=this_label,
        foreign_categories=sorted({labels.get(slug, slug) for slug, _ in foreign}),
        keywords=[kw for _, kw in foreign],
    )


# ─── The report ─────────────────────────────────────────────────────────────

def build_report(db: Session, *, category_slug: Optional[str], message_template: str,
                 totals: dict, sendable_count: int, suppressed_count: int,
                 capacity_assessment: dict) -> dict:
    """Every check, plus the numbers the composer's summary panel renders.

    Checks come back in a fixed order — capacity first, because it is the one
    that stops a send — so the checklist does not reshuffle itself between
    keystrokes.

    `totals` comes from `exact_segment_totals()` and is the measured cost of
    this exact audience. Everything downstream of it — the segment total, the
    length check and the quote — is denominated in what will actually be sent,
    not in what the raw template happens to measure.
    """
    breakdown = describe(message_template or "")
    template_per_message = breakdown["segments"]
    # Length is judged on the longest rendered message, not the template: a
    # 3-segment guideline is about what lands on a handset.
    per_message = totals["max_segments_per_message"]
    total_segments = totals["total_segments"]

    checks = [
        check_capacity(capacity_assessment),
        check_opt_out_language(message_template),
        check_brand_identified(message_template),
        check_segment_count(per_message),
        check_merge_expansion(totals),
        check_recent_overlap(suppressed_count, sendable_count),
        check_link_shortener(message_template),
        check_category_match(db, category_slug, message_template),
    ]

    return {
        "ok": not any(c["status"] == FAIL for c in checks),
        "checks": checks,
        "counts": {
            "recipients": sendable_count,
            "suppressed": suppressed_count,
            # What the live counter shows, kept so the two panels can be
            # compared rather than silently disagreeing.
            "segments_per_message": template_per_message,
            "max_segments_per_message": per_message,
            "template_total_segments": totals["template_total_segments"],
            "total_segments": total_segments,
            "segments_measured": totals["exact"],
        },
        "encoding": breakdown["encoding"],
        # His rate, from billing_service. Never the wholesale figure the
        # capacity check above is denominated in.
        "estimated_cost": marginal_cost(db, total_segments),
        "price_per_segment": settings.BILLING_PRICE_PER_SEGMENT,
    }


def cost_estimates(db: Session, message_template: str, recipients: int) -> dict:
    """Cost now, and cost if the message were plain GSM-7, both at his rate.

    The pair is the point. One emoji cuts a segment from 160 characters to 70,
    and the only wording of that fact anyone acts on is the difference between
    two dollar figures at tonight's recipient count.
    """
    breakdown = describe(message_template or "")
    total = breakdown["segments"] * recipients
    gsm7_total = breakdown["gsm7_segments_if_stripped"] * recipients
    return {
        "estimated_cost": marginal_cost(db, total),
        "estimated_cost_if_gsm7": marginal_cost(db, gsm7_total),
        "price_per_segment": settings.BILLING_PRICE_PER_SEGMENT,
    }
