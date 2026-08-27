"""Opt-out handling and TCPA-adjacent copy.

The carrier handles STOP at the network level, but you must ALSO persist it
yourself: carrier-side opt-out lists do not travel when you switch providers,
and the day you migrate you will re-text everyone who ever opted out. Store
every STOP in your own blocklist table and treat that as the source of truth.

Keywords below are the standard CTIA set. Do not narrow them.
"""

import re
from dataclasses import dataclass
from typing import Optional

from app.core.config import settings

STOP_KEYWORDS = {"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT", "OPTOUT", "OPT-OUT"}
START_KEYWORDS = {"START", "UNSTOP", "YES", "SUBSCRIBE", "OPTIN", "OPT-IN"}
HELP_KEYWORDS = {"HELP", "INFO"}


def classify(body: str) -> str:
    """Return 'stop' | 'start' | 'help' | 'other' for an inbound message."""
    normalized = (body or "").strip().upper()
    if normalized in STOP_KEYWORDS:
        return "stop"
    if normalized in START_KEYWORDS:
        return "start"
    if normalized in HELP_KEYWORDS:
        return "help"
    return "other"


def stop_confirmation() -> str:
    return (f"You have been unsubscribed from {settings.BRAND_NAME} messages. "
            f"Reply START to resubscribe.")


def start_confirmation() -> str:
    return (f"You have been resubscribed to {settings.BRAND_NAME} messages. "
            f"Reply STOP to unsubscribe anytime.")


def help_reply() -> str:
    contact = settings.BRAND_SUPPORT_PHONE or settings.BRAND_SUPPORT_EMAIL or "our website"
    return (f"{settings.BRAND_NAME} marketing alerts. For help contact {contact}. "
            f"Reply STOP to unsubscribe. Msg & data rates may apply.")


def default_auto_reply() -> str:
    """Reply sent to any inbound message that isn't a keyword.

    Editable per client at runtime from the Settings page; this is the seed value.
    """
    lines = [
        f"Thank you for your message.",
        "",
        f"This number is used to send marketing updates from {settings.BRAND_NAME} "
        f"and is not monitored for replies.",
        "",
    ]
    if settings.BRAND_SUPPORT_PHONE or settings.BRAND_SUPPORT_EMAIL:
        contact_bits = " or ".join(
            b for b in (settings.BRAND_SUPPORT_PHONE, settings.BRAND_SUPPORT_EMAIL) if b
        )
        lines += [f"For inquiries please contact us at {contact_bits}.", ""]
    lines += [
        "To unsubscribe from future messages, reply STOP.",
        "",
        f"- {settings.BRAND_NAME}",
    ]
    return "\n".join(lines)


# ─── Classifying a carrier failure ──────────────────────────────────────────
#
# Blocking a number is permanent and silent. A wrongly blocked buyer does not
# complain — he simply stops appearing in campaigns, and the only trace is one
# row among thousands of correct ones. Everything below is arranged around that
# asymmetry.
#
# Session 5d put this classifier on the delivery-webhook path, taking it from a
# handful of events per campaign to 3,037. The rules were written for the narrow
# path, where a carrier refuses a send outright; the wide path carries the entire
# transient-failure vocabulary as well. See
# decisions/003-auto-block-fragments-on-the-webhook-path.md.

# 1. TRANSIENT — these win over every other rule.
#
# Deliberately unanchored substrings, and deliberately wider than they need to
# be. This is the inverse of the trade AUTO_BLOCK_ERROR_FRAGMENTS makes below:
# a transient marker causes a *refusal to act*, so over-matching costs half a
# cent for one extra send attempt, while under-matching deletes a live buyer.
# "temporar" as a stem rather than "temporary" is that reasoning in miniature —
# it catches "temporarily" too.
#
# What this catches is a carrier describing a *dead-number* wording as temporary:
# "destination not routable via this route, will retry", "invalid phone number
# returned upstream, temporary lookup failure". The fragment says never again and
# the carrier says try later; the carrier wins, because it is the one that knows.
#
# If a carrier ever words a genuinely permanent failure with "retry", that number
# survives one extra campaign at a cost of half a cent. That is the correct
# direction to be wrong in.
#
# This guard was originally justified by "unreachable" — see the note on the
# fragment list below for why that justification did not survive contact with
# Twilio 30003's actual wording, and why the fragment is gone rather than the
# guard.
TRANSIENT_FAILURE_MARKERS = ("temporar", "retry", "congestion", "try again")

# 2. OUR OWN MISCONFIGURATION — never the recipient's fault.
#
# A geo permission on the *sending* account (Twilio 21408). The destination was
# never the problem: enabling the region fixes it, and until session 5g the
# number stayed blocked forever for a setting on our side. It raises an operator
# alert instead — see record_delivery_status(), and CONFIGURATION_ALERT_DETAIL
# for the one place its wording lives.
CONFIGURATION_ERROR_MARKERS = ("has not been enabled for the region",)
CONFIGURATION_ERROR_CODES = frozenset({"21408"})

REGION_NOT_ENABLED = "region_not_enabled"
CONFIGURATION_ALERT_DETAIL = {
    REGION_NOT_ENABLED: (
        "A destination was refused because this messaging account is not "
        "enabled for its region. Nothing is wrong with the recipient — enable "
        "the region on the messaging account."
    ),
}

# 3. CARRIER OPT-OUT — a block, but not the same evidence as an inbound STOP.
#
# `stop_keyword` is documented in blocked_number.py as legally binding, and a
# carrier's own opt-out record is not that. Kept distinct in the compliance
# record and combined in the metric — see blocklist_service.OPT_OUT_REASONS.
CARRIER_OPT_OUT_FRAGMENTS = ("unsubscribed", "opted out", "opt-out")
CARRIER_OPT_OUT_CODES = frozenset({"21610"})     # Twilio: send to unsubscribed recipient

# 4. DEAD NUMBER — "never text this number again". Matching one of these on a
# failure auto-adds the number to the blocklist, which is what keeps a list from
# degrading into thousands of guaranteed failures per campaign.
#
# "Never again" is the bar, and it is the reason temporary failures are absent.
# A carrier's "Blocked as spam - temporary" is a rate-limit, not a dead number:
# 47 of them in one campaign, all still perfectly reachable an hour later.
# Before adding a fragment, check it cannot appear in a transient message.
#
# "deemed invalid" was added in session 5d. A live campaign produced 100
# failures reading "the destination phone number was deemed invalid by the
# carrier", and neither "is not a valid" nor "invalid phone number" occurs in
# that sentence — the fragments matched the wording of a different carrier
# response than the one being sent.
#
# The numeric codes that used to sit in this tuple — "21610", "21612", "40300" —
# are gone, and are NOT `\b`-anchored replacements. They were plain `in` tests
# against carrier-controlled prose, and +1 321-610-xxxx and 321-612-xxxx are
# assignable Brevard County numbers in this client's own market: an error string
# quoting a recipient's number blocked a different, innocent one. `\b21610\b`
# still matches a bare code sitting in prose, and prose is not where codes
# belong. They now match AUTO_BLOCK_ERROR_CODES against a structured column.
#
# ── "unreachable" is deliberately absent, and a dead line can survive because
#    of it. That is the accepted cost, not an oversight. ──────────────────────
#
# It was here until decision 004. Every other fragment means one thing;
# "unreachable" means three, and two of them are transient:
#
#     a dead line                                   permanent
#     a switched-off handset (Twilio 30003)         transient
#     an SMSC or upstream outage                    transient, and not about
#                                                   the recipient at all
#
# The transient guard above was supposed to separate them and cannot: Twilio
# 30003's own `ErrorMessage` is `Unreachable destination handset`, which carries
# no marker at all. Nor does `SMPP bind failed - SMSC unreachable`, which is a
# carrier-side outage condemning the *recipient*. The guard only ever caught the
# wordings a carrier happened to decorate with an adjective.
#
# So the residual: **a line the carrier describes only as "unreachable" stays on
# the list and is paid for again on every campaign.** About half a cent a blast —
# under two dollars a year at one blast a day. A wrongly deleted buyer costs the
# auction house a bidder, permanently and invisibly, and the client never learns
# why his list is shrinking. When a signal is ambiguous, make the cheap error.
#
# The real fix is not a better heuristic. Permanence is a property of the line,
# and line-type lookup answers it authoritatively for about $0.004 — logged in
# `modules.md` under "Found in live use". An error string is a lossy proxy for a
# question we can simply ask. Do not rebuild a two-tier rule here to compensate;
# decision 004 considered and rejected exactly that.
AUTO_BLOCK_ERROR_FRAGMENTS = (
    "blacklisted", "is not a valid", "landline", "not a mobile",
    "not routable", "invalid phone number", "deemed invalid",
)
AUTO_BLOCK_ERROR_CODES = frozenset({"21612", "40300"})

# Reason strings for the blocklist. Duplicated as literals rather than imported
# from app.models.blocked_number because app/sms/ must not import the DB layer —
# tests/test_blocklist_reasons.py asserts they are members of BLOCK_REASONS, so
# the duplication cannot drift silently.
DELIVERY_FAILURE = "delivery_failure"
CARRIER_OPT_OUT = "carrier_opt_out"


def _anchored(fragments) -> "re.Pattern":
    """Word-bounded alternation over `fragments`.

    Anchoring here is safe in a way it was not in `scrub_provider_text()`. That
    regex reads text an SDK assembled, where the carrier's name gets glued to a
    word character (`TelnyxError`) and a trailing `\\b` silently fails open.
    These fragments read a carrier's English prose, where "landline" is a word;
    the boundary only stops it matching inside a longer one. Defence in depth,
    and cheap — not the rule that does the work.
    """
    return re.compile(r"\b(?:%s)\b" % "|".join(re.escape(f) for f in fragments),
                      re.IGNORECASE)


_OPT_OUT_RE = _anchored(CARRIER_OPT_OUT_FRAGMENTS)
_DEAD_NUMBER_RE = _anchored(AUTO_BLOCK_ERROR_FRAGMENTS)


@dataclass(frozen=True)
class FailureVerdict:
    """What to do about one carrier failure.

    `block`/`reason` and `alert_key` are independent: a misconfiguration alert
    is about our account, not about the recipient, so it is raised whether or
    not the number is also blocked for some other stated reason.
    """
    block: bool = False
    reason: Optional[str] = None          # a BLOCK_REASONS value when block is True
    alert_key: Optional[str] = None       # operator alert — our problem, not theirs
    alert_detail: Optional[str] = None


def _normalized_code(error_code) -> str:
    """Carrier codes arrive as ints, as strings, and as strings with padding."""
    if error_code is None:
        return ""
    return str(error_code).strip()


def classify_failure(error_message: str, error_code=None) -> FailureVerdict:
    """Decide what a delivery or submission failure means.

    Numeric codes are matched against `error_code` — a structured field the
    carrier populated — and never against `error_message`, which is prose that
    routinely quotes phone numbers, message ids and timestamps.
    """
    text = error_message or ""
    code = _normalized_code(error_code)

    alert_key = None
    if code in CONFIGURATION_ERROR_CODES or any(
            marker in text.lower() for marker in CONFIGURATION_ERROR_MARKERS):
        alert_key = REGION_NOT_ENABLED

    def verdict(block=False, reason=None) -> FailureVerdict:
        return FailureVerdict(
            block=block, reason=reason, alert_key=alert_key,
            alert_detail=CONFIGURATION_ALERT_DETAIL.get(alert_key),
        )

    # The transient guard is first and beats everything, including a structured
    # code. A carrier that reports a permanent code inside a message about
    # retrying is a carrier contradicting itself, and the safe reading of a
    # contradiction is the one that keeps a buyer on the list.
    lowered = text.lower()
    if any(marker in lowered for marker in TRANSIENT_FAILURE_MARKERS):
        return verdict()

    if not text and not code:
        return verdict()

    if code in CARRIER_OPT_OUT_CODES:
        return verdict(True, CARRIER_OPT_OUT)
    if code in AUTO_BLOCK_ERROR_CODES:
        return verdict(True, DELIVERY_FAILURE)

    # Opt-out wordings before dead-number wordings: "unsubscribed" would
    # otherwise be filed as a data-quality fact rather than the compliance
    # event it is.
    if _OPT_OUT_RE.search(text):
        return verdict(True, CARRIER_OPT_OUT)
    if _DEAD_NUMBER_RE.search(text):
        return verdict(True, DELIVERY_FAILURE)

    return verdict()


def should_auto_block(error_message: str, error_code=None) -> bool:
    """Whether this failure means "never text this number again"."""
    return classify_failure(error_message, error_code).block
