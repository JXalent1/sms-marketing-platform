"""Provider registry.

The rest of the app calls get_provider() and never imports a provider directly.
Adding a carrier is: write the class, add one line to PROVIDERS, add its
credentials to core/config.py.

This module also owns the one client-safe description of what the send path is
actually doing — see send_mode(). It lives here, next to the fallback that
causes it, for the same reason scrub_provider_text() lives in app/sms/phone.py:
the layer that knows the carrier's name is the layer responsible for never
leaking it, and two callers computing "are we live?" separately is how a failed
provider ends up described as a deliberate dry run on one screen and a live one
on another.
"""

import logging
from dataclasses import dataclass
from app.core.config import settings
from app.sms.base import SMSProvider

logger = logging.getLogger("sms")

PROVIDERS = {
    "console": "app.sms.providers.console:ConsoleProvider",
    "telnyx": "app.sms.providers.telnyx:TelnyxProvider",
    "twilio": "app.sms.providers.twilio:TwilioProvider",
}


@dataclass(frozen=True)
class ProviderFallback:
    """What happened when the configured provider refused to start.

    Internal only. `requested` is the carrier name and `error` is raw SDK text
    that routinely contains it — neither may reach a response body, a template
    or an export. Callers that render anything use send_mode() instead.
    """

    requested: str
    error_type: str
    error: str


@dataclass(frozen=True)
class SendMode:
    """The client's view of the send path. Three states, not two.

    "Dry run" and "not working" were indistinguishable until session 5c: a live
    box whose carrier credential was wrong fell back to console and rendered the
    same reassuring amber pill as a deliberate dry run, so the platform reported
    itself healthy while sending nothing. `key` is what code branches on; the
    strings are what the client reads, and they name no carrier.
    """

    key: str
    label: str
    detail: str


SEND_MODES = {
    "live": SendMode(
        "live", "Live",
        "Campaigns are being sent.",
    ),
    "dry_run": SendMode(
        "dry_run", "Dry run",
        "Campaigns are written to the log and never sent.",
    ),
    "unavailable": SendMode(
        "unavailable", "Sending unavailable",
        "Campaigns cannot go out right now. Contact support.",
    ),
}

_instance: SMSProvider | None = None
_fallback: ProviderFallback | None = None


def _load(path: str) -> type:
    module_path, class_name = path.split(":")
    module = __import__(module_path, fromlist=[class_name])
    return getattr(module, class_name)


def get_provider(force_reload: bool = False) -> SMSProvider:
    """Return the configured provider (cached).

    Falls back to the console provider if the configured one can't start —
    a missing API key should degrade to "sends nothing", never to a crash loop
    on a box that is also serving the client's dashboard.

    The fallback is recorded rather than only logged. It logged at ERROR into a
    journal the service account could not read, which is the same as nowhere.
    """
    global _instance, _fallback
    if _instance is not None and not force_reload:
        return _instance

    name = (settings.SMS_PROVIDER or "console").lower()
    if name not in PROVIDERS:
        raise ValueError(f"Unknown SMS_PROVIDER '{name}'. Options: {', '.join(PROVIDERS)}")

    # Built as locals and published together. Assigning the globals as we went
    # meant a reload that raised on its way out — an unknown provider name, or
    # the console fallback itself failing to import — left the previous provider
    # cached with the new run's fallback state on top of it, and send_mode()
    # then described one while callers used the other. Nothing calls
    # force_reload in production today; the first "reload the provider" admin
    # button would make that a real answer given to a real client.
    try:
        instance = _load(PROVIDERS[name])()
        fallback = None
    except Exception as e:
        fallback = ProviderFallback(requested=name, error_type=type(e).__name__, error=str(e))
        logger.error(f"Could not initialize SMS provider '{name}': {e}. Falling back to console.")
        instance = _load(PROVIDERS["console"])()

    _instance, _fallback = instance, fallback
    logger.info(f"SMS provider active: {_instance.name}")
    return _instance


def provider_fallback() -> ProviderFallback | None:
    """The recorded fallback, or None. Internal — logs and tests, never a response."""
    get_provider()
    return _fallback


def send_mode() -> SendMode:
    """The client-safe answer to "are messages going out?".

    Every client-facing surface asks this and renders `label`/`detail` verbatim.
    Nothing else may decide the wording, and nothing here names the carrier.
    """
    provider = get_provider()
    if _fallback is not None:
        return SEND_MODES["unavailable"]
    return SEND_MODES["live"] if provider.name != "console" else SEND_MODES["dry_run"]


def active_sender_number() -> str:
    """The number messages go out from — shown in Settings, used in auto-replies."""
    name = (settings.SMS_PROVIDER or "console").lower()
    if name == "telnyx":
        return settings.TELNYX_PHONE_NUMBER
    if name == "twilio":
        return settings.TWILIO_PHONE_NUMBER
    return "(dry run)"
