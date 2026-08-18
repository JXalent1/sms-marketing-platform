"""Opt-out handling and TCPA-adjacent copy.

The carrier handles STOP at the network level, but you must ALSO persist it
yourself: carrier-side opt-out lists do not travel when you switch providers,
and the day you migrate you will re-text everyone who ever opted out. Store
every STOP in your own blocklist table and treat that as the source of truth.

Keywords below are the standard CTIA set. Do not narrow them.
"""

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


# Error fragments that mean "never text this number again". Matching one of these
# on a send failure auto-adds the number to the blocklist, which is what keeps a
# list from degrading into thousands of guaranteed failures per campaign.
AUTO_BLOCK_ERROR_FRAGMENTS = (
    "unsubscribed", "blacklisted", "opt-out", "opted out", "is not a valid",
    "landline", "not a mobile", "unreachable", "not routable",
    "has not been enabled for the region", "invalid phone number",
    "21610", "21612", "40300",
)


def should_auto_block(error_message: str) -> bool:
    if not error_message:
        return False
    lowered = error_message.lower()
    return any(fragment in lowered for fragment in AUTO_BLOCK_ERROR_FRAGMENTS)
