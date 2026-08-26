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

    # Built as locals and published together. Assigning the globals as we went
    # meant a reload that raised on its way out — the console fallback itself
    # failing to import — left the previous provider cached with the new run's
    # fallback state on top of it, and send_mode() then described one while
    # callers used the other. Nothing calls force_reload in production today;
    # the first "reload the provider" admin button would make that a real
    # answer given to a real client.
    #
    # An unrecognised name is raised *inside* the try rather than above it, so
    # a typo degrades exactly as a carrier that failed to construct does
    # (decision 002). It used to raise out of here, which 500'd every page on
    # the box — the dashboard, the contact list, the usage meter — over a
    # setting that only affects sending. .env is hand-edited over ssh on a live
    # client box, and `telnix` should cost him the ability to send, not the
    # ability to log in. Degrading is only safe because session 5c made the
    # state visible and A1 makes it refuse to send; before that it would have
    # been a silent dry run.
    #
    # There is no programmer-error case to keep the raise for: the only input
    # is settings.SMS_PROVIDER, which comes from .env. A misconfiguration is
    # the only way to get here.
    try:
        if name not in PROVIDERS:
            raise LookupError(
                f"Unknown SMS_PROVIDER '{name}'. Options: {', '.join(PROVIDERS)}"
            )
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


# The refusal, appended to send_mode().detail rather than written from scratch,
# and here rather than in campaign_service for the reason this whole module
# exists: the layer that knows the carrier's name is the layer responsible for
# every client-safe sentence about it. Two callers composing their own "we can't
# send" wording is how a failed provider ended up described as a deliberate dry
# run on one screen and a live one on another.
#
# Two of them, because tense is not decoration. One string served both surfaces
# and the composer therefore told him "Nothing was sent." about a draft he had
# not sent — which reads as a past campaign having silently failed, on the one
# screen whose job is to stop him before he starts.
#
# "Nothing will be sent" rather than "this campaign will not be started": the
# same refusal answers the test-send button, and a single test message is not a
# campaign. Will/was is the whole difference between the two.
DEGRADED_REFUSAL_BEFORE = "Nothing will be sent."
DEGRADED_REFUSAL_AFTER = "Nothing was sent."


def send_path_assessment() -> dict:
    """Can this box reach a carrier at all? The question before capacity.

    On a box whose carrier refused to start, get_provider() falls back to
    console — correct, a dashboard must not die over a credential — and console
    then answers the capacity question with a bottomless balance, reports every
    send successful, and every row is written `sent` and invoiced. The client
    could send to all 1,223 contacts, watch every row go green, and find out at
    the saleroom. Decision 002 is why the callers refuse rather than warn: the
    operator is the client, and a warning row is one more thing between him and
    tonight's auction.

    `dry_run` is explicitly excluded. A *chosen* console run is a deliberate
    demo and behaves exactly as it did before this existed; `unavailable` means
    the box tried to reach a carrier and could not. Telling those apart is the
    whole of session 5c and is not re-derived here — send_mode() decides.

    Returns the same shape as `CampaignService.capacity_assessment()` so the
    composer's checklist can render either without knowing which it has.
    `detail` is for surfaces shown *before* a send — the composer row, the
    test-send refusal, the send button's 409 — and `abort_detail` is what gets
    recorded *after* one was refused. Same fault, two tenses; keeping both here
    is what stops a future surface from inventing a third.
    """
    mode = send_mode()
    if mode.key == "unavailable":
        return {"ok": False, "checked": True, "mode": mode.key,
                "detail": f"{mode.detail} {DEGRADED_REFUSAL_BEFORE}",
                "abort_detail": f"{mode.detail} {DEGRADED_REFUSAL_AFTER}"}
    return {"ok": True, "checked": True, "mode": mode.key,
            "detail": mode.detail, "abort_detail": mode.detail}


def active_sender_number() -> str:
    """The number messages go out from — shown in Settings, used in auto-replies.

    "(dry run)" is reserved for the console provider being *chosen*. An
    unrecognised SMS_PROVIDER now degrades rather than raising (A3), and
    answering "(dry run)" for it would have the Settings page call the box a dry
    run in the same breath as the banner above it says sending is unavailable.
    There is no number in that case, so it says so and the templates render
    their own empty state.
    """
    name = (settings.SMS_PROVIDER or "console").lower()
    if name == "telnyx":
        return settings.TELNYX_PHONE_NUMBER
    if name == "twilio":
        return settings.TWILIO_PHONE_NUMBER
    return "(dry run)" if name == "console" else ""
