"""Pre-flight checks: is this the right message, to the right people?

Everything in here answers one question: *is this the right message, to the
right people, at a price he agreed to?* The client runs a different-niche
auction almost every day, and the failure this module exists to prevent is a
memorabilia collector getting a text about a walk-in cooler.

The suppression window moved to `suppression_service.py` in 5e. It is a rule
about who gets a text rather than a check on the message, it is on the escalation
list in its own right, and its number now comes from the database. This module
reads it — `check_recent_overlap()` is handed the window rather than looking it
up a second way, so the row and the send path cannot describe different windows.

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

The two checks that can stop a send are *not* implemented here. Both live in
`campaign_service.CampaignService` — `capacity_assessment()` and
`send_path_assessment()` — where the send path already calls them; this module
only re-states their verdicts as checklist rows. Surfacing them is additive:
nothing in this file can weaken either, and nothing here is on the path between
a send and those checks.
"""

import logging
import re
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.category import Category
from app.services import link_service
# Re-exported: the measurement half moved to its own module in 5f when this
# file crossed the 500-line rule, and every caller reaches for these three
# names at this address. Moving the definitions without keeping the names
# would be a rename dressed up as a refactor.
from app.services.preflight_totals import (   # noqa: F401  (re-export)
    cost_estimates, exact_segment_totals, marginal_cost,
)
from app.services.suppression_service import clears_at_clock, suppression_days
from app.sms.compliance import STOP_KEYWORDS
from app.sms.phone import find_risky_links
from app.sms.segments import describe

logger = logging.getLogger("preflight")

PASS, WARN, FAIL = "pass", "warn", "fail"

# How much of the message counts as "the opening" for the brand check. A carrier
# trust-and-safety review reads the first line; so does a recipient deciding
# whether this is a scam. Naming yourself in the footer is not identifying
# yourself.
OPENING_CHARS = 64


def _check(key: str, label: str, status: str, reason: str, **extra) -> dict:
    return {"key": key, "label": label, "status": status, "reason": reason, **extra}


# ─── The checks ─────────────────────────────────────────────────────────────

def check_send_path(assessment: dict) -> dict:
    """Re-state the send path's own degraded verdict as a checklist row.

    `assessment` comes from `CampaignService.send_path_assessment()` — the same
    call, reading the same `send_mode()`, that refuses to start a campaign on a
    box whose carrier failed to start. Like the capacity row below it, this
    computes nothing of its own: a second opinion here would eventually
    disagree with the one that actually stops the send.

    It is a FAIL, not a WARN, and decision 002 is explicit about why: the
    operator is the client, and a warning row is something he clicks through on
    his way to tonight's auction. A *chosen* dry run passes — that is the demo
    flow, and it is untouched.
    """
    return _check(
        "send_path", "Sending status",
        PASS if assessment.get("ok") else FAIL,
        assessment.get("detail") or "",
    )


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


def check_recent_overlap(days: int, suppressed_count: int, sendable_count: int,
                         clears_at: Optional[str] = None) -> dict:
    """What the suppression window is doing to this audience, before it is queued.

    `days` is passed in rather than read from config here: this row and the send
    path have to be describing the same window, and the send path's window now
    comes from the database. A second reader would eventually report the number
    the campaign was not actually filtered with.

    A window of 0 holds nobody back, so the row says that plainly rather than
    reporting "nobody was texted in the last 0 days", which reads like a fact
    about the audience instead of a fact about the rule.
    """
    if days <= 0:
        return _check("recent_overlap", "Recently texted", PASS,
                      "The hold-back window is off, so nobody is held back for having "
                      "been texted recently.")
    if not suppressed_count:
        return _check("recent_overlap", "Recently texted", PASS,
                      f"Nobody in this audience was texted in the last {days} days.")

    # The clearing time is the actionable half. "1,204 held back" invites "held
    # back until when?", and the answer decides whether he waits or re-cuts the
    # audience. Omitted rather than guessed when it cannot be computed.
    when = f" The hold clears at {_clock(clears_at)}." if clears_at else ""
    return _check(
        "recent_overlap", "Recently texted", WARN,
        f"{suppressed_count:,} of {suppressed_count + sendable_count:,} contacts were "
        f"texted in the last {days} days and will be held back. "
        f"{sendable_count:,} will receive this message.{when}",
        suppressed_count=suppressed_count,
        suppression_days=days,
        clears_at=clears_at,
    )


def _clock(stamp: Optional[str]) -> str:
    """An ISO timestamp as "10:11am on 29 Aug", or the raw value if unparseable.

    Delegates rather than implements. Decision 006 requires the abort reason for
    a fully-held-back campaign to quote the same clearing time this row does,
    and two copies of the formatting is how the pre-send sentence and the
    post-hoc one come to disagree about the same moment. The renderer lives with
    `suppression_clears_at()`, which produces what it renders.
    """
    return clears_at_clock(stamp)


def check_short_link(message_template: str, link_target_url: Optional[str],
                     example_url: Optional[str] = None) -> dict:
    """Is the `{link}` merge tag usable, and does it point somewhere sane?

    A FAIL here, not a WARN, and 5f A3 is explicit about why: the tag must
    refuse at *compose* time and never at send time. A campaign created with an
    unusable link would mint nothing, render the literal `{link}` into 4,200
    messages and cost full price for a message with a broken URL in it — and
    the client would find out from a buyer, not from the product.

    The refusals are `link_service`'s own sentences, rendered verbatim. That is
    the `send_mode()` pattern: the module that knows why a link cannot be minted
    owns the wording, and every surface repeats it rather than inventing a
    second explanation. `campaign_builder.resolve_link_target()` raises the same
    strings on the create path, so the checklist and the refusal agree by
    construction rather than by review.
    """
    tagged = link_service.has_link_tag(message_template)

    if not tagged:
        if link_target_url:
            return _check("short_link", "Link", WARN,
                          "A link destination was given but the message does not "
                          f"use {link_service.LINK_TAG}, so nobody will see it. Add "
                          "the tag, or clear the destination.")
        return _check("short_link", "Link", PASS, "This message carries no link.")

    if not link_service.configured():
        return _check("short_link", "Link", FAIL, link_service.NO_DOMAIN_ERROR)

    try:
        link_service.validate_target(link_target_url)
    except link_service.LinkError as e:
        return _check("short_link", "Link", FAIL, str(e))

    shown = example_url or link_service.placeholder_url()
    return _check(
        "short_link", "Link", PASS,
        f"Every recipient gets their own link — {shown} — so the report can say "
        f"which buyers opened it. It is {len(shown)} characters and the segment "
        f"count below is measured on the real thing, not on the tag.",
        example_url=shown,
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
                 capacity_assessment: dict, send_path_assessment: dict,
                 suppression_clears_at: Optional[str] = None,
                 link_target_url: Optional[str] = None) -> dict:
    """Every check, plus the numbers the composer's summary panel renders.

    Checks come back in a fixed order — the send path first and capacity second,
    the two that stop a send — so the checklist does not reshuffle itself
    between keystrokes.

    `send_path_assessment` is a required argument rather than one defaulting to
    "fine". A caller that forgets it should fail loudly here; a caller that
    silently skipped the row would draw a clean checklist on a box that cannot
    send, which is the exact screen session 5d exists to remove.

    `totals` comes from `exact_segment_totals()` and is the measured cost of
    this exact audience. Everything downstream of it — the segment total, the
    length check and the quote — is denominated in what will actually be sent,
    not in what the raw template happens to measure.
    """
    # Measured with the link at its rendered width — see
    # `link_service.for_counting()`. The *checks* below still read the raw
    # template: they are about the copy he wrote, and a slug in the body would
    # be a slug in the category-keyword scan.
    breakdown = describe(link_service.for_counting(message_template))
    template_per_message = breakdown["segments"]
    # Length is judged on the longest rendered message, not the template: a
    # 3-segment guideline is about what lands on a handset.
    per_message = totals["max_segments_per_message"]
    total_segments = totals["total_segments"]
    days = suppression_days(db)

    checks = [
        check_send_path(send_path_assessment),
        check_capacity(capacity_assessment),
        check_opt_out_language(message_template),
        check_brand_identified(message_template),
        check_segment_count(per_message),
        check_merge_expansion(totals),
        check_recent_overlap(days, suppressed_count, sendable_count,
                             suppression_clears_at),
        check_short_link(message_template, link_target_url),
        check_link_shortener(message_template),
        check_category_match(db, category_slug, message_template),
    ]

    return {
        "ok": not any(c["status"] == FAIL for c in checks),
        "checks": checks,
        "counts": {
            "recipients": sendable_count,
            "suppressed": suppressed_count,
            "suppression_days": days,
            "suppression_clears_at": suppression_clears_at,
            # What the live counter shows, kept so the two panels can be
            # compared rather than silently disagreeing.
            "segments_per_message": template_per_message,
            "max_segments_per_message": per_message,
            "template_total_segments": totals["template_total_segments"],
            "total_segments": total_segments,
            "segments_measured": totals["exact"],
        },
        "encoding": breakdown["encoding"],
        # What the composer needs to know about the link without re-deriving
        # either fact: whether the message uses the tag, and whether the box can
        # mint one. A screen that worked this out for itself would eventually
        # offer the tag on a box with no domain configured.
        "link": {
            "tag": link_service.LINK_TAG,
            "in_message": link_service.has_link_tag(message_template),
            "available": link_service.configured(),
            "example_url": (link_service.placeholder_url()
                            if link_service.configured() else None),
        },
        # His rate, from billing_service. Never the wholesale figure the
        # capacity check above is denominated in.
        "estimated_cost": marginal_cost(db, total_segments),
        "price_per_segment": settings.BILLING_PRICE_PER_SEGMENT,
    }
