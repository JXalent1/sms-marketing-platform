"""Is this click a person, or a machine that opened the link before them?

SMS links are fetched before any human sees them. Carrier scanners follow every
URL in an A2P message, handset link previews fetch the page to draw a card, and
corporate security appliances rewrite and pre-open links. Unfiltered, a campaign
reports clicks it did not earn — and this number goes to a client who will
decide what to auction next with it.

**Which way this matcher should fail, and why it is the opposite of the
auto-block list.** `app/sms/compliance.py` carries both shapes and says so:
`AUTO_BLOCK_ERROR_FRAGMENTS` triggers an irreversible action (a buyer is deleted
from the list), so it is word-anchored and narrow. This one triggers a
*presentation* choice — which of two numbers is the headline — and both numbers
are on screen, both are stored, and nothing is discarded. So it is allowed to be
wider than it needs to be. Over-matching moves a real click into the filtered
count, where the client can still see it; under-matching inflates a headline. The
cheaper error is the first, and the row survives either way, which is the whole
reason clicks are labelled rather than dropped.

That licence is not unlimited, and the ambiguous-fragment lesson applies here
too. `"bot"` as a bare substring matches `CUBOT_X20` — a real Android handset
that appears in real user agents — so the generic words are word-anchored and
the named crawlers are not, because `Googlebot/2.1` has no boundary before its
`bot` and never will.

Nothing here is presented as precise. `report_service` reports the filtered
count alongside the human one and the templates say "filtered as automated",
because a bare number that quietly excludes things is the defect, not the
filtering.
"""

import re
from typing import Optional, Tuple

from app.core.config import settings

# Named agents. No boundaries: an SDK or a crawler glues its name to whatever it
# likes, and every one of these strings is unambiguous on its own.
KNOWN_AGENTS = (
    "googlebot", "bingbot", "yandexbot", "duckduckbot", "baiduspider",
    "applebot", "facebookexternalhit", "facebot", "twitterbot", "linkedinbot",
    "slackbot", "discordbot", "telegrambot", "whatsapp", "skypeuripreview",
    "pinterest", "redditbot", "embedly", "quora link preview", "outbrain",
    "vkshare", "w3c_validator", "flipboard", "tumblr", "bitlybot",
    # Security appliances and link-rewriters. These are the ones that actually
    # fire on this client's traffic: they open every URL in a message before the
    # recipient's handset has finished ringing.
    "proofpoint", "urldefense", "mimecast", "barracuda", "symantec",
    "forcepoint", "zscaler", "microsoft office", "safelinks",
    # Scripted fetchers.
    "curl/", "wget/", "python-requests", "python-urllib", "httpx/", "go-http-client",
    "java/", "okhttp", "libwww-perl", "axios/", "node-fetch", "headlesschrome",
    "phantomjs", "puppeteer", "playwright",
)

# Generic words, word-anchored. `bot` is in here rather than above because
# `CUBOT_X20` is a phone, not a crawler, and an unanchored `bot` files its owner
# under "automated" forever.
GENERIC_AGENT_WORDS = re.compile(
    r"(?<![\w-])(?:bot|bots|crawler|crawl|spider|scraper|scanner|preview|monitor|"
    r"validator|fetcher|archiver)(?![\w-])",
    re.IGNORECASE,
)

# What a click with no user agent at all is called. Every browser sends one;
# something that does not is a script.
NO_AGENT = "ua:absent"


def classify(user_agent: Optional[str],
             seconds_after_send: Optional[int] = None) -> Tuple[bool, Optional[str]]:
    """(is_bot, reason). `reason` is None only when the click looks human.

    Two independent signals, and the user agent is checked first because it is
    the one that names itself. Timing is the backstop for a scanner that copies
    a browser's user agent, which the better ones do.

    `seconds_after_send` is None whenever we cannot date the click against a
    send — a link on a draft, a message with no `sent_at`. That is not evidence
    of anything, so the timing rule simply does not run.
    """
    agent = (user_agent or "").strip()
    if not agent:
        return True, NO_AGENT

    lowered = agent.lower()
    for name in KNOWN_AGENTS:
        if name in lowered:
            return True, f"ua:{name.strip('/ ')}"[:40]

    match = GENERIC_AGENT_WORDS.search(lowered)
    if match:
        return True, f"ua:{match.group(0).lower()}"[:40]

    # Nobody reads a text, unlocks a handset and taps a link inside a couple of
    # seconds of the carrier accepting the message. Something that did is the
    # scanner in the delivery path. The threshold is config rather than a
    # constant: it is a judgement about how fast this client's audience is, and
    # setting it to 0 turns the rule off without touching code.
    threshold = settings.CLICK_MIN_HUMAN_SECONDS
    if (threshold > 0 and seconds_after_send is not None
            and 0 <= seconds_after_send < threshold):
        return True, "timing:too_soon"

    return False, None
