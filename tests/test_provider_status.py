"""The send path says what it is actually doing.

Session 5c exists because production was switched live, could not send, and
reported itself healthy for it. Two defects, one visible failure:

  1. `requirements.txt` pinned a carrier SDK major version the provider was not
     written against, so `TelnyxProvider.__init__` raised on every start.
  2. `get_provider()` caught that, fell back to the console provider — correct,
     a dashboard must not die over a credential — and then the whole product
     described the result as a *chosen* dry run. Same amber pill, same wording,
     same reassuring "Dry run" badge on the Settings page. The only record of
     the real failure was an ERROR line in a journal the service account could
     not read.

Every test here fails against the code as it stood on the morning of the launch.
That is the point of the file: a suite that goes green while the product cannot
send a message is not testing the thing that matters.

Nothing here sends. `carrier_provider()` builds the real provider object with a
visibly fake key — construction only — and `degraded_provider()` never gets that
far. `conftest.py` forces SMS_PROVIDER=console for the suite and every context
manager below restores it.
"""

import os
import re

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.sms import factory
from tests._provider_setup import (
    CARRIER_SDK_ERROR, broken_credential, carrier_provider, degraded_provider,
)

PASSWORD = os.environ["ADMIN_PASSWORD"]

SHELL_PAGES = ("/dashboard", "/campaigns", "/contacts", "/blocklist", "/usage", "/settings")

_PILL_MODE = re.compile(r'id="sendModePill"[^>]*data-mode="([a-z_]+)"')


@pytest.fixture(scope="module", autouse=True)
def login_budget():
    """Hand back the login this module spends.

    POST /login is capped at 10/minute per IP and the whole suite logs in from
    one address inside one window. Before this file there were nine logins in
    it; a tenth is the cap, and a suite that starts failing on 429s reads like
    an auth bug rather than a budget one. Same discipline as the campaign-create
    limiter in `_guardrail_setup.rate_limit_fixture_body()`.
    """
    from app.routers import pages as pages_router

    pages_router.limiter.reset()
    yield
    pages_router.limiter.reset()


@pytest.fixture(scope="module")
def client():
    c = TestClient(app)
    login = c.post("/login", data={"username": "admin", "password": PASSWORD})
    assert login.status_code in (200, 302), (
        f"login failed with {login.status_code} — every assertion below would "
        f"have run against a 401 body and passed by containing nothing"
    )
    return c


def _system(client) -> dict:
    return client.get("/api/settings/system").json()


def _pill_mode(html: str) -> str | None:
    found = _PILL_MODE.search(html)
    return found.group(1) if found else None


# ─── The provider that gets used is the one that was configured ─────────────

def test_configured_carrier_is_the_provider_that_is_used():
    """The pin bug, in one assertion.

    With telnyx pinned at 2.1.2 this comes back as the console provider: the
    4.x client class the provider constructs does not exist in 2.x, __init__
    raises AttributeError, and the fallback swallows it. Nothing else in the
    suite noticed, because everything else runs on console by design.
    """
    with carrier_provider():
        provider = factory.get_provider()
        assert provider.name == "telnyx", (
            "SMS_PROVIDER named a carrier and a key was set, but the console "
            "provider is active — the carrier failed to construct"
        )
        assert type(provider).__name__ == "TelnyxProvider"
        assert factory.provider_fallback() is None
        assert factory.send_mode().key == "live"


def test_carrier_sdk_matches_the_provider_it_is_used_through():
    """Guards the pin itself rather than its symptom.

    `requirements.txt` is the thing that regressed, and it regresses silently:
    the app imports, boots and serves. Asserting the client class exists is the
    cheapest way to fail on `pip install -r requirements.txt` having installed
    an SDK the provider cannot drive.
    """
    telnyx = pytest.importorskip(
        "telnyx", reason="the carrier SDK is pinned in requirements.txt and must be installed"
    )
    assert hasattr(telnyx, "Telnyx"), (
        "the installed carrier SDK has no client class — app/sms/providers/"
        "telnyx.py is written against 4.x; check the pin in requirements.txt"
    )


# ─── A failed provider is not a chosen one ──────────────────────────────────

def test_fallback_is_recorded_and_not_only_logged():
    with degraded_provider():
        fallback = factory.provider_fallback()
        assert fallback is not None, "the fallback left no trace outside the log"
        assert fallback.error_type == "AttributeError"
        assert CARRIER_SDK_ERROR in fallback.error
        # Which provider was asked for, so a two-carrier box can say which one
        # died. Diagnosis only — `requested` is a carrier name and the leak test
        # below is what keeps it off the screens.
        assert fallback.requested == "telnyx"
        # Still serving. The fallback is correct behaviour; invisibility was the bug.
        assert factory.get_provider().name == "console"
        assert factory.send_mode().key == "unavailable"


def test_a_real_broken_credential_degrades_the_same_way(client):
    """The stub above agrees with the factory by construction. This does not.

    `TelnyxProvider.__init__` refuses an empty key, which is the plainest form of
    the "deliberately broken credential" the spec asks for, and it exercises the
    provider class this app actually ships. If a future refactor makes that
    constructor swallow a bad credential and return a half-built client, the
    stub-driven tests stay green and this one goes red.
    """
    with broken_credential():
        fallback = factory.provider_fallback()
        assert fallback is not None, "an empty carrier credential started cleanly"
        assert fallback.error_type == "ValueError"
        assert factory.send_mode().key == "unavailable"

        payload = _system(client)
        assert payload["send_mode"] == "unavailable"
        assert payload["dry_run"] is False
        assert _pill_mode(client.get("/settings").text) == "unavailable"


def test_chosen_dry_run_and_failed_carrier_are_different_states():
    chosen = factory.send_mode()
    with degraded_provider():
        broken = factory.send_mode()
    assert chosen.key == "dry_run" and broken.key == "unavailable"
    assert chosen.label != broken.label, (
        "a box that cannot send reads identically to one deliberately in dry run"
    )


def test_a_reload_that_dies_leaves_the_state_self_consistent():
    """The cached provider and the reported mode describe the same moment.

    A reload that raises on its way out used to leave the previous provider
    cached with the new run's fallback state on top: `send_mode()` said
    "unavailable" while every caller went on sending happily through a provider
    that worked. Either answer is defensible; the two of them at once is not.
    """
    before_provider = factory.get_provider()
    before_mode = factory.send_mode().key

    original_load = factory._load

    def everything_fails(path: str):
        raise ImportError("neither the carrier nor the console provider will load")

    factory._load = everything_fails
    try:
        with pytest.raises(ImportError):
            factory.get_provider(force_reload=True)
        assert factory.get_provider() is before_provider
        assert factory.send_mode().key == before_mode
    finally:
        factory._load = original_load
        factory.get_provider(force_reload=True)


def test_recovery_clears_the_degraded_state():
    """A stale flag is its own outage: it would have the client chasing a fault
    that has already been fixed."""
    with degraded_provider():
        assert factory.provider_fallback() is not None
    assert factory.provider_fallback() is None
    assert factory.send_mode().key == "dry_run"


# ─── It reaches the client's screens ────────────────────────────────────────

def test_settings_payload_separates_dry_run_from_unavailable(client):
    chosen = _system(client)
    assert chosen["send_mode"] == "dry_run"
    assert chosen["dry_run"] is True
    assert chosen["sending_unavailable"] is False

    with degraded_provider():
        broken = _system(client)

    assert broken["send_mode"] == "unavailable"
    assert broken["sending_unavailable"] is True
    assert broken["provider_configured"] is False
    assert broken["dry_run"] is False, (
        "the payload still calls a broken carrier a dry run — this is the field "
        "the Settings page renders a calm amber badge from"
    )
    assert broken["send_mode_label"] != chosen["send_mode_label"]
    assert broken["send_mode_detail"] != chosen["send_mode_detail"]


def test_settings_page_shows_a_degraded_banner(client):
    calm = client.get("/settings").text
    assert "dryRunBanner" in calm
    assert "sendingUnavailableBanner" not in calm

    with degraded_provider():
        broken = client.get("/settings").text

    assert "sendingUnavailableBanner" in broken
    assert "Sending unavailable" in broken
    assert "dryRunBanner" not in broken, "both banners at once tells him two things"


def test_the_pill_changes_on_every_page(client):
    """The pill is in the shell, so it is on all six screens or none.

    A degraded state visible only on Settings is a degraded state nobody sees:
    the screen he is looking at on the morning of a sale is Today or Compose.
    """
    for path in SHELL_PAGES:
        assert _pill_mode(client.get(path).text) == "dry_run", path

    with degraded_provider():
        for path in SHELL_PAGES:
            html = client.get(path).text
            assert _pill_mode(html) == "unavailable", path
            assert "Sending unavailable" in html, path


def test_degraded_surfaces_never_name_the_carrier(client):
    """The failure's only source of words is a carrier SDK exception.

    The recorded error says "module 'telnyx' has no attribute 'Telnyx'" — a
    leak waiting for someone to render it "so the client can tell support what
    broke". It stays in the log; the screens get our wording.
    """
    carrier = re.compile(r"telnyx|twilio", re.IGNORECASE)

    with degraded_provider():
        assert carrier.search(factory.provider_fallback().error), (
            "the harness is meant to reproduce an error that names the carrier"
        )
        for path in (*SHELL_PAGES, "/api/settings/system"):
            response = client.get(path)
            # Asserted before the scan: "contains no carrier name" is also true
            # of a 302 to /login and of a 500, and a leak test that passes
            # because it never rendered the page is worse than no test.
            assert response.status_code == 200, f"{path} returned {response.status_code}"
            assert not carrier.search(response.text), (
                f"{path} leaked the carrier name while reporting the failure"
            )
