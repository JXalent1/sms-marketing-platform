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


PROVIDER_WORDS = re.compile(r"\b(?:twilio|telnyx|eightbyeight|8x8|bandwidth|vonage)\b", re.IGNORECASE)
PROVIDER_URLS = re.compile(r"https?://\S*(?:twilio|telnyx|8x8|bandwidth|vonage)\S*", re.IGNORECASE)


def scrub_provider_text(text: str) -> str:
    """Strip carrier branding from text that will be shown to the client.

    Error strings come back full of provider names and doc links. Clients should
    not learn which carrier you resell, and you should be able to switch
    carriers without the UI contradicting itself.
    """
    if not text:
        return text
    text = PROVIDER_URLS.sub("", text)
    text = PROVIDER_WORDS.sub("SMS carrier", text)
    return re.sub(r"[ \t]{2,}", " ", text).strip()
