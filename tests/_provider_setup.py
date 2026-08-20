"""Drives the provider factory into each of its three send modes.

Not a test module — the leading underscore keeps pytest from collecting it.
`test_provider_status.py` asserts what each state does; `test_whitelabel.py`
re-renders every client-facing route while degraded, because that is the state
whose strings are newest and whose only source of text is a carrier SDK.

Two rules for anything that uses this:

  1. **Always restore.** `factory` caches one provider instance per process and
     the suite shares it. A context left open points every later test — and
     `CampaignService`, which resolves the provider when it is constructed — at
     a carrier instead of the console dry run that `conftest.py` forces.
  2. **Never send.** `carrier_provider()` builds the real provider object with a
     visibly fake key. Construction alone makes no network call (verified
     against telnyx 4.175.0); nothing inside these contexts calls `send()`.

The fake credentials use 555-01xx and a key that could not be mistaken for a
real one, so a leak into a log or a fixture is obvious on sight.
"""

from contextlib import contextmanager

from app.core.config import settings
from app.sms import factory

FAKE_API_KEY = "KEY_NOT_A_REAL_CREDENTIAL_test_only"
FAKE_SENDER = "+15555550140"

# Word for word what production raised on 2026-08-20: requirements.txt pinned a
# 2.x SDK, the provider was written against the 4.x client class, and __init__
# died on the attribute lookup. Reproducing the real string matters — it names
# the carrier, which is exactly what must not reach the client while the app
# reports the failure.
CARRIER_SDK_ERROR = "module 'telnyx' has no attribute 'Telnyx'"


class BrokenCarrierProvider:
    """A carrier provider whose constructor fails, as the real one did."""

    name = "telnyx"

    def __init__(self):
        raise AttributeError(CARRIER_SDK_ERROR)


@contextmanager
def _carrier_settings(requested: str):
    """Point settings at a carrier, then put everything back."""
    previous = (settings.SMS_PROVIDER, settings.TELNYX_API_KEY, settings.TELNYX_PHONE_NUMBER)
    settings.SMS_PROVIDER = requested
    settings.TELNYX_API_KEY = FAKE_API_KEY
    settings.TELNYX_PHONE_NUMBER = FAKE_SENDER
    try:
        yield
    finally:
        (settings.SMS_PROVIDER,
         settings.TELNYX_API_KEY,
         settings.TELNYX_PHONE_NUMBER) = previous
        # Rebuild from the restored settings so the cached instance and the
        # recorded fallback both go back to what conftest.py established.
        factory.get_provider(force_reload=True)


@contextmanager
def degraded_provider(requested: str = "telnyx"):
    """A carrier is configured and refuses to start — the launch-day state.

    The app keeps serving on the console provider. What this proves is that it
    stops *describing* itself as a chosen dry run while it does.
    """
    original_load = factory._load

    def broken_load(path: str):
        if path == factory.PROVIDERS[requested]:
            return BrokenCarrierProvider
        return original_load(path)

    with _carrier_settings(requested):
        factory._load = broken_load
        try:
            factory.get_provider(force_reload=True)
            yield
        finally:
            factory._load = original_load


@contextmanager
def broken_credential(requested: str = "telnyx"):
    """A carrier is configured with no usable credential.

    The same degraded state as above, reached through the *real* provider class
    rather than a stub. `degraded_provider()` proves the factory handles a
    failing constructor; this proves the constructor this app actually ships
    fails the way the factory expects. A stub that agrees with the code under
    test is how a suite goes green over a provider nobody can build.
    """
    with _carrier_settings(requested):
        settings.TELNYX_API_KEY = ""
        factory.get_provider(force_reload=True)
        yield


@contextmanager
def carrier_provider(requested: str = "telnyx"):
    """A carrier is configured and starts cleanly.

    Unreachable for the whole of module 5b: the pinned SDK had no client class,
    so this path always ended in the fallback below it. Building the real
    provider object here is the point — a stub would pass against either pin.
    """
    with _carrier_settings(requested):
        factory.get_provider(force_reload=True)
        yield
