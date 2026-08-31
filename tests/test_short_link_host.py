"""The short-link domain serves short links and nothing else.

`bida4a.com` and the admin panel are one process behind one nginx. Before the
guard in `app/main.py` every route answered on both names, so the domain printed
in every text message also served `/login` and `/dashboard` — the client's whole
contact list, on the one hostname a stranger is handed with every campaign.

Both directions are tested, and the second is the one that costs something if it
breaks: a guard that 404s the admin panel on the short host and *also* breaks it
on the primary is a worse outage than the hole it closed.

The list of admin routes is read from the app's own route table rather than
written out here. That is the whole argument the middleware makes against an
nginx path denylist, and a test carrying a hand-written list would have exactly
the drift it exists to rule out — a route added next month would not be checked,
silently, on the host nobody looks at.
"""

import pytest
from fastapi.testclient import TestClient

from app.core.database import SessionLocal, engine
from app.main import app
from app.models.short_link import ShortLink
from app.services import link_service
from app.services.campaign_service import CampaignService

from tests import _link_setup as setup

SHORT_HOST = "bida4a.test"
PRIMARY_HOST = "app.onlineauctions.test"
PASSWORD = "devpassword123"


@pytest.fixture(scope="module")
def db():
    yield from setup.purged_db_fixture_body()


@pytest.fixture(autouse=True)
def rate_limits():
    yield from setup.rate_limit_fixture_body()


@pytest.fixture(scope="module")
def slug(db):
    """A real, resolvable slug, minted through the real creation path."""
    with setup.short_link_domain(SHORT_HOST):
        source = f"{setup.CONTACT_SOURCE}-host"
        setup.seed_contacts(db, setup.take(1), source=source)
        campaign = CampaignService(db).create_campaign(
            name=f"{setup.NAME_PREFIX}host",
            message_template=f"A4A: {link_service.LINK_TAG} Reply STOP to opt out.",
            audience=f"source:{source}",
            cross_category_override=True,
            link_target_url=setup.TARGET_URL,
        )
        yield db.query(ShortLink).filter(
            ShortLink.campaign_id == campaign.id).one().slug


def _client() -> TestClient:
    return TestClient(app)


def _app_paths() -> list:
    """Every route the app registers that a browser could ask for.

    Read from the app, never hand-listed — see the module docstring. `{`-bearing
    paths are dropped because they need a value; they are covered by the slug
    cases below and by test_whitelabel's own route scan.
    """
    paths = {getattr(route, "path", "") for route in app.routes}
    return sorted(p for p in paths if p and "{" not in p)


# ─── Direction 1: the short host serves nothing but links ───────────────────

def test_every_admin_route_404s_on_the_short_host(db, slug):
    """The reason the guard exists, over the app's own route table."""
    client = _client()
    with setup.short_link_domain(SHORT_HOST):
        served = {}
        for path in _app_paths():
            response = client.get(path, headers={"host": SHORT_HOST},
                                  follow_redirects=False)
            if response.status_code != 404:
                served[path] = response.status_code

    assert served == {}, f"these paths answer on the short domain: {served}"
    # And the list was not empty, or the assertion above proves nothing — the
    # 5e vacuous-test lesson, which cost two green checks that compared 0 == 0.
    assert "/login" in _app_paths() and "/dashboard" in _app_paths()
    assert len(_app_paths()) >= 10


def test_the_short_host_404_is_the_same_one_an_expired_link_gets(db, slug):
    """A probe of /dashboard and a probe of a dead slug must be identical.

    Otherwise scanning the short domain tells an attacker there is an admin
    panel behind the same process, and which paths it has.
    """
    client = _client()
    with setup.short_link_domain(SHORT_HOST):
        admin = client.get("/dashboard", headers={"host": SHORT_HOST})
        expired = client.get("/zzzzzzzz", headers={"host": SHORT_HOST})

    assert admin.status_code == expired.status_code == 404
    assert admin.text == expired.text
    assert admin.headers.get("content-type") == expired.headers.get("content-type")
    # Nothing in the body names the product or the brand.
    assert "auctions" not in admin.text.lower()


def test_a_reserved_word_that_is_slug_shaped_does_not_reach_its_page(db, slug):
    """`settings` is eight characters of the slug alphabet.

    Found by running this by hand before the test existed: the shape test said
    "yes, that is a slug", routing then matched `/settings` — registered before
    `/{slug}` — and the short host answered 302 to the login form while every
    other admin path answered 404. One page leaking is the whole leak.
    """
    assert link_service.SLUG_RE.match("settings"), "precondition"
    assert link_service.is_slug_path("/settings") is False
    client = _client()
    with setup.short_link_domain(SHORT_HOST):
        assert client.get("/settings", headers={"host": SHORT_HOST},
                          follow_redirects=False).status_code == 404


def test_a_post_to_the_short_host_is_refused_before_it_reaches_a_router(db):
    """The guard runs before routing, so it covers verbs the route never had."""
    client = _client()
    with setup.short_link_domain(SHORT_HOST):
        for method, path in (("post", "/login"), ("post", "/api/campaigns"),
                             ("put", "/api/settings/auto-reply")):
            response = getattr(client, method)(path, json={}, data={},
                                               headers={"host": SHORT_HOST})
            assert response.status_code == 404, f"{method.upper()} {path}"


# ─── Direction 2: the primary host is untouched ─────────────────────────────

def test_the_admin_panel_still_works_on_the_primary_host(db, slug):
    """The outage this guard could cause is worse than the hole it closes."""
    client = _client()
    with setup.short_link_domain(SHORT_HOST):
        assert client.get("/login", headers={"host": PRIMARY_HOST}).status_code == 200
        login = client.post("/login", headers={"host": PRIMARY_HOST},
                            data={"username": "admin", "password": PASSWORD},
                            follow_redirects=False)
        assert login.status_code == 302, login.text
        for path in ("/dashboard", "/campaigns", "/contacts", "/history",
                     "/settings", "/usage", "/blocklist", "/health"):
            response = client.get(path, headers={"host": PRIMARY_HOST})
            assert response.status_code == 200, f"{path} -> {response.status_code}"


def test_the_redirect_still_resolves_on_both_hosts(db, slug):
    """Links already in people's phones survive a domain change.

    A guard that made the slug route short-host-only would break every message
    already sent under the old domain, and those are unrecallable.
    """
    client = _client()
    with setup.short_link_domain(SHORT_HOST):
        for host in (SHORT_HOST, PRIMARY_HOST):
            response = client.get(f"/{slug}", headers={"host": host},
                                  follow_redirects=False)
            assert response.status_code == 302, host
            assert response.headers["location"] == setup.TARGET_URL


def test_the_guard_is_a_no_op_when_no_short_domain_is_configured(db, slug):
    """Every box without a short domain, a fresh clone, and the suite."""
    client = _client()
    with setup.short_link_domain(""):
        assert link_service.is_short_link_host(SHORT_HOST) is False
        # The host header is now meaningless, so the admin panel answers on it.
        assert client.get("/login", headers={"host": SHORT_HOST}).status_code == 200
        assert client.get(f"/{slug}", headers={"host": SHORT_HOST},
                          follow_redirects=False).status_code == 302


# ─── The host comparison itself ─────────────────────────────────────────────

@pytest.mark.parametrize("header", [
    "bida4a.test", "BIDA4A.TEST", "Bida4a.Test",      # case
    "bida4a.test:443", "bida4a.test:8000",            # port
    " bida4a.test ",                                   # whitespace
])
def test_the_host_match_ignores_case_port_and_whitespace(header):
    with setup.short_link_domain(SHORT_HOST):
        assert link_service.is_short_link_host(header) is True


@pytest.mark.parametrize("header", [
    None, "", "app.onlineauctions.test", "evil.test",
    "bida4a.test.evil.test",       # suffix, not the host
    "notbida4a.test",              # substring, not the host
])
def test_a_host_that_is_not_the_short_domain_does_not_trip_the_guard(header):
    with setup.short_link_domain(SHORT_HOST):
        assert link_service.is_short_link_host(header) is False


def test_a_configured_domain_carrying_a_port_still_matches():
    """`localhost:8000` in development is the case that would silently switch
    the guard off if only one side of the comparison were port-stripped — and
    development is where nobody would notice."""
    with setup.short_link_domain("localhost:8000"):
        assert link_service.is_short_link_host("localhost:8000") is True
        assert link_service.is_short_link_host("localhost") is True


def test_an_ipv6_literal_keeps_its_brackets():
    """`[::1]:8000` must reduce to `[::1]`, not to `[`."""
    assert link_service.normalize_host("[::1]:8000") == "[::1]"
    assert link_service.normalize_host("[::1]") == "[::1]"


# ─── The 5f guarantee, on the guarded host ──────────────────────────────────

def test_an_unknown_slug_on_the_short_host_leaks_no_database_connection(db, slug):
    """5f's `finally`, re-asserted on the path the guard now fronts.

    The guard answers before the route on every admin path, so those never open
    a session at all — but an unknown *slug* still goes through the route, and
    that is the path a scanner sweeping this domain will actually take.
    """
    client = _client()
    with setup.short_link_domain(SHORT_HOST):
        before = engine.pool.checkedout()
        for _ in range(30):
            assert client.get("/zzzzzzzz", headers={"host": SHORT_HOST}).status_code == 404
        after_slugs = engine.pool.checkedout()
        for _ in range(30):
            assert client.get("/dashboard", headers={"host": SHORT_HOST}).status_code == 404
        after_admin = engine.pool.checkedout()

    assert after_slugs <= before, f"{after_slugs - before} leaked over unknown slugs"
    assert after_admin <= before, f"{after_admin - before} leaked over guarded paths"


def test_a_forwarding_header_cannot_move_a_request_onto_the_short_host(db, slug):
    """The guard reads `Host` and nothing else.

    nginx sets `Host` from `$host`, i.e. from the server block that matched.
    `X-Forwarded-Host` is client-supplied and is not evidence of anything, so a
    request that claims to be for the short domain while nginx says otherwise
    must be served exactly as the primary host serves it.
    """
    client = _client()
    with setup.short_link_domain(SHORT_HOST):
        response = client.get("/login", headers={
            "host": PRIMARY_HOST,
            "x-forwarded-host": SHORT_HOST,
        })
        assert response.status_code == 200, (
            "a spoofable header decided which host this was")


# ─── The misconfiguration that would take the whole product down ────────────

def test_pointing_the_short_domain_at_the_admin_host_disables_the_guard(db, slug):
    """A copy-paste away, and the symptom is the worst kind.

    If `SHORT_LINK_DOMAIN` is set to the host the admin panel is served on, the
    guard matches every request and every page answers 404 with "This link has
    expired or was mistyped." — no login, no dashboard, nothing on screen
    connecting it to a setting. The client reports the product as down.

    There is no configuration in which blocking is right when the two names are
    the same, because then the short domain *is* the admin domain. So it fails
    open and the startup log says so. The cheap error is the pre-existing state
    (both surfaces on one name); the expensive one is a total outage nobody can
    diagnose.
    """
    from app.core.config import settings as app_settings

    previous = app_settings.PUBLIC_BASE_URL
    app_settings.PUBLIC_BASE_URL = f"https://{SHORT_HOST}"
    try:
        with setup.short_link_domain(SHORT_HOST):
            assert link_service.short_domain_conflicts() is True
            assert link_service.is_short_link_host(SHORT_HOST) is False
            client = _client()
            assert client.get("/login", headers={"host": SHORT_HOST}).status_code == 200
            assert client.get(f"/{slug}", headers={"host": SHORT_HOST},
                              follow_redirects=False).status_code == 302
    finally:
        app_settings.PUBLIC_BASE_URL = previous

    # And with the two names distinct, the guard is on again.
    with setup.short_link_domain(SHORT_HOST):
        assert link_service.short_domain_conflicts() is False
        assert link_service.is_short_link_host(SHORT_HOST) is True


def test_the_primary_host_is_read_from_public_base_url_however_it_is_written():
    from app.core.config import settings as app_settings

    previous = app_settings.PUBLIC_BASE_URL
    try:
        for value, expected in (
            ("https://app.onlineauctions.co", "app.onlineauctions.co"),
            ("http://app.onlineauctions.co/", "app.onlineauctions.co"),
            ("https://APP.ONLINEAUCTIONS.CO:443/x", "app.onlineauctions.co"),
            ("http://localhost:8000", "localhost"),
            ("", ""),
        ):
            app_settings.PUBLIC_BASE_URL = value
            assert link_service.primary_host() == expected, value
    finally:
        app_settings.PUBLIC_BASE_URL = previous
