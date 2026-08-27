"""Phone normalization and destination-region filtering.

Every number entering the system is normalized to E.164 once, at the edge, so
the blocklist, dedup and send path all compare the same string. Half the
"duplicate contact" bugs in this class of app come from storing
"(555) 123-4567" and "+15551234567" as different people.
"""

import re

# NANP area codes belonging to Canada and the Caribbean NANP members.
# A US-only 10DLC messaging profile cannot deliver to these — the carrier rejects
# them with a region error after you have already paid for the attempt.
#
# This set is deliberately US-safe: it contains no US mainland area codes, so it
# can never filter out a real US recipient. If your client's profile is approved
# for Canada, set SKIP_NON_US_NUMBERS=false rather than editing this list.
NON_US_NANP_AREA_CODES = {
    # Canada
    "204", "226", "236", "249", "250", "263", "289", "306", "343", "354", "365",
    "367", "368", "382", "403", "416", "418", "428", "431", "437", "438", "450",
    "468", "474", "506", "514", "519", "548", "579", "581", "584", "587", "604",
    "613", "639", "647", "672", "683", "705", "709", "742", "753", "778", "780",
    "782", "807", "819", "825", "867", "873", "879", "902", "905",
    # Caribbean / other NANP countries + Puerto Rico
    "242", "246", "264", "268", "284", "345", "441", "473", "649", "658", "664",
    "670", "721", "758", "767", "784", "787", "809", "829", "849", "868", "869",
    "876", "939",
}


def normalize(phone: str) -> str:
    """Return an E.164-ish string, assuming US when no country code is present."""
    if not phone:
        return ""
    digits = "".join(filter(str.isdigit, phone))
    if not digits:
        return ""
    if len(digits) == 10:                 # bare US number
        digits = "1" + digits
    return "+" + digits


def is_valid(phone: str) -> bool:
    """Loose sanity check: country code plus at least 10 digits."""
    return len(normalize(phone)) >= 12


def is_non_us_region(phone: str) -> bool:
    """True if the number is outside the US sending region.

    Two cases, both US-safe:
      - not NANP at all (digits don't start with country code 1)
      - NANP with a known Canadian / Caribbean area code
    Malformed US-format numbers are intentionally left alone — filtering those
    is a separate, riskier decision that should be made per client.
    """
    if not phone:
        return False
    digits = "".join(filter(str.isdigit, phone))
    if not digits:
        return False
    if not digits.startswith("1"):
        return True
    if len(digits) == 11:
        return digits[1:4] in NON_US_NANP_AREA_CODES
    return False


# ─── Message hygiene ────────────────────────────────────────────────────────

# Public URL shorteners. Carriers aggressively spam-filter these on A2P traffic:
# in the reference system every message carrying a short.gy link was accepted by
# the provider (200 OK, dashboard said "sent") and then silently dropped by the
# carrier with error 40002 "Blocked as spam". Proven by A/B test — the identical
# message with a plain first-party domain delivered fine. Always use a link on a
# domain the client owns.
SHARED_SHORTENER_DOMAINS = (
    "bit.ly", "tinyurl.com", "short.gy", "t.co", "goo.gl", "ow.ly",
    "buff.ly", "is.gd", "rebrand.ly", "cutt.ly", "shorturl.at",
)

_URL_RE = re.compile(r"https?://([^/\s]+)", re.IGNORECASE)


def find_risky_links(message: str) -> list:
    """Return shortener domains found in the message.

    Surface these in the composer as a blocking-level warning. A campaign that
    is 100% carrier-spam-blocked still costs full price and looks like a
    delivery mystery for days.
    """
    if not message:
        return []
    found = []
    for host in _URL_RE.findall(message):
        host = host.lower().lstrip("www.")
        if any(host == d or host.endswith("." + d) for d in SHARED_SHORTENER_DOMAINS):
            found.append(host)
    return sorted(set(found))


# No word boundaries, deliberately. This was `\b(?:...)\b`, and the trailing \b
# fails the moment a carrier glues its name to a word character — which is
# exactly how SDKs write things:
#
#   'TelnyxError: bad'                            -> unscrubbed
#   'telnyx_api failure'                          -> unscrubbed
#   'twilio.rest.exceptions.TwilioRestException'  -> half scrubbed
#
# Found by the session 5d review, after the delivery-webhook auto-block became
# the first client-rendered string assembled verbatim from carrier free text at
# volume — 2,673 rows in one campaign, each carrying whatever the carrier chose
# to call itself. A boundary-anchored scrubber is a scrubber that works on the
# wordings we happened to test with.
#
# The cost is that "bandwidth" is also an ordinary English word and now matches
# inside longer ones ("bandwidths"). It already matched standalone, so this
# widens a trade the module had already made, and reading "SMS carrier" where a
# client expected "bandwidth" is a cosmetic bug. Leaking the carrier's name is not.
PROVIDER_WORDS = re.compile(r"(?:twilio|telnyx|eightbyeight|8x8|bandwidth|vonage)", re.IGNORECASE)
PROVIDER_URLS = re.compile(r"https?://\S*(?:twilio|telnyx|8x8|bandwidth|vonage)\S*", re.IGNORECASE)


# A serialized response body embedded in an error string. SDK exceptions render
# as `"Error code: 400 - {'errors': [{'code': '10002', 'title': ..., 'detail':
# ...}]}"` — the whole payload, dict-repr'd into the message. Two of those are
# in `sms_messages.error_message` on the live box, and since session 5d that
# column feeds `blocked_numbers.notes`, which the client reads.
#
# Remove the serialized payload and keep everything else. What is removed is
# carrier-internal structure of unknown shape: today its visible prefix happens
# to carry no brand and no personal data, but `detail` on a destination error is
# exactly where a recipient's phone number appears, and nothing makes today's
# shape true of tomorrow's.
#
# `describe_send_error()` in the Telnyx provider parses those fields properly,
# which is the actual fix. This is the backstop for every other provider, every
# other SDK version, and every path that reaches a client-facing string without
# going through one of ours.
#
# Two design points, both found by review rather than by reasoning:
#
#   1. It removes a *span*, not a suffix. The first version cut at the opening
#      brace and discarded the rest of the string, which ate the half of the
#      message the client needs — "...is not a valid phone number [{'code':
#      21211}] - see the error reference for how to fix this" lost the fix
#      instructions. The payload is not always last.
#   2. It takes TWO structural characters in a row — an opening bracket followed
#      by another bracket or a quoted key. A lone brace is not enough of a signal
#      to cut an error message on, and this runs on every client-facing error
#      string in the app: "Message body {first_name} was rejected" survives it
#      intact.
#
# Both directions are asserted in tests/test_carrier_error_surfaces.py.

_OPENERS = "[{"
_CLOSERS = "]}"
_QUOTES = "\"'"

# What a client sees when the error string was nothing but payload. Without it,
# `notes=f"Auto-blocked: {scrub_provider_text(...)}"` renders "Auto-blocked: "
# and stops — a blocked number on the Opt-outs page with no reason at all.
NO_READABLE_DETAIL = "Carrier error, no readable detail"


def _payload_span(text: str, start_at: int = 0):
    """(start, end) of the first serialized payload at or after `start_at`."""
    index = start_at
    while index < len(text):
        if text[index] not in _OPENERS:
            index += 1
            continue

        after = index + 1
        while after < len(text) and text[after].isspace():
            after += 1
        if after >= len(text) or text[after] not in _OPENERS + _QUOTES:
            index += 1
            continue

        depth = 0
        quote = None
        for cursor in range(index, len(text)):
            character = text[cursor]
            if quote:
                if character == quote:
                    quote = None
            elif character in _QUOTES:
                quote = character
            elif character in _OPENERS:
                depth += 1
            elif character in _CLOSERS:
                depth -= 1
                if depth == 0:
                    return index, cursor + 1
        # Unterminated — a payload the column length cut in half, which is
        # exactly how row 4 is stored on the live box. The rest is all payload.
        return index, len(text)
    return None


def strip_payload(text: str) -> str:
    """Remove any serialized provider payloads from an error string.

    Returns "" if the string was nothing but payload; callers decide what to
    show instead. `scrub_provider_text()` substitutes NO_READABLE_DETAIL.
    """
    if not text:
        return text

    cleaned = text
    searched_from = 0
    while True:
        span = _payload_span(cleaned, searched_from)
        if not span:
            break
        start, end = span
        cleaned = cleaned[:start] + " " + cleaned[end:]
        searched_from = start

    if cleaned == text:
        return text
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    # Trailing joiners left dangling by the removal: "Failed - [{...}]" would
    # otherwise render as "Failed -".
    return cleaned.rstrip("-:,;( ").strip()


def scrub_provider_text(text: str) -> str:
    """Strip carrier branding from text that will be shown to the client.

    Error strings come back full of provider names and doc links. Clients should
    not learn which carrier you resell, and you should be able to switch
    carriers without the UI contradicting itself.

    Since session 5g it also removes an embedded response body — see
    `strip_payload()`. Same defect class as the branding it was written for, one
    level up: there the carrier's *name* survived, here its entire JSON does.

    An error that was *nothing but* payload becomes NO_READABLE_DETAIL rather
    than the empty string. `blocked_numbers.notes` is built as
    `f"Auto-blocked: {scrub_provider_text(...)}"`, so returning "" put a blocked
    number on the client's Opt-outs page under a reason that stopped at the
    colon — which reads as a bug in the product rather than as a carrier that
    said nothing useful.
    """
    if not text:
        return text
    text = strip_payload(text) or NO_READABLE_DETAIL
    text = PROVIDER_URLS.sub("", text)
    text = PROVIDER_WORDS.sub("SMS carrier", text)
    return re.sub(r"[ \t]{2,}", " ", text).strip()
