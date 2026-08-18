"""Provider registry.

The rest of the app calls get_provider() and never imports a provider directly.
Adding a carrier is: write the class, add one line to PROVIDERS, add its
credentials to core/config.py.
"""

import logging
from app.core.config import settings
from app.sms.base import SMSProvider

logger = logging.getLogger("sms")

PROVIDERS = {
    "console": "app.sms.providers.console:ConsoleProvider",
    "telnyx": "app.sms.providers.telnyx:TelnyxProvider",
    "twilio": "app.sms.providers.twilio:TwilioProvider",
}

_instance: SMSProvider | None = None


def _load(path: str) -> type:
    module_path, class_name = path.split(":")
    module = __import__(module_path, fromlist=[class_name])
    return getattr(module, class_name)


def get_provider(force_reload: bool = False) -> SMSProvider:
    """Return the configured provider (cached).

    Falls back to the console provider if the configured one can't start —
    a missing API key should degrade to "sends nothing", never to a crash loop
    on a box that is also serving the client's dashboard.
    """
    global _instance
    if _instance is not None and not force_reload:
        return _instance

    name = (settings.SMS_PROVIDER or "console").lower()
    if name not in PROVIDERS:
        raise ValueError(f"Unknown SMS_PROVIDER '{name}'. Options: {', '.join(PROVIDERS)}")

    try:
        _instance = _load(PROVIDERS[name])()
    except Exception as e:
        logger.error(f"Could not initialize SMS provider '{name}': {e}. Falling back to console.")
        _instance = _load(PROVIDERS["console"])()

    logger.info(f"SMS provider active: {_instance.name}")
    return _instance


def active_sender_number() -> str:
    """The number messages go out from — shown in Settings, used in auto-replies."""
    name = (settings.SMS_PROVIDER or "console").lower()
    if name == "telnyx":
        return settings.TELNYX_PHONE_NUMBER
    if name == "twilio":
        return settings.TWILIO_PHONE_NUMBER
    return "(dry run)"
