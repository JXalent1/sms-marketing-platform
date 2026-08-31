"""White-label enforcement that runs the code instead of reading it.

`agent/gate.sh` greps templates and routers for the carrier's name. That catches
a literal, and every leak found in this codebase so far was not a literal:

  - an f-string built from `provider.name`
  - a URL assembled as PUBLIC_BASE_URL + "/webhooks/" + provider.name
  - `str(e)` from a carrier SDK exception, whose class name carries the brand
  - a JS template literal rendering a money field the API should not have sent

A grep is structurally incapable of seeing any of those. This module makes the
requests and scans what actually comes back.

The route list is discovered from the app, not typed out here, so a new
client-facing GET route is scanned the moment it is added. A route with a path
parameter this file has no sample value for fails the coverage test rather than
being quietly skipped — pass by omission is exactly the failure mode being
designed out.
"""

import asyncio
import json
import os
import re

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.database import SessionLocal
from app.main import app
from app.models.campaign import Campaign
from app.services import contact_service
from app.services.campaign_service import CampaignService
from app.sms import factory
from tests._provider_setup import degraded_provider

PASSWORD = os.environ["ADMIN_PASSWORD"]

# Every brand we might ever put behind the provider abstraction, not just the
# one currently configured — a leak introduced while SMS_PROVIDER=console is
# still a leak.
CARRIER_RE = re.compile(r"telnyx|twilio", re.IGNORECASE)

# Same fictional numbers the smoke test uses (555-01xx is reserved for fiction).
# Reusing them means this module adds no contacts: `contacts.phone` is unique, so
# these upserts hit existing rows and no other test's counts move.
DEMO_PHONES = ["+15555550100", "+15555550101"]

# Routes the scan deliberately does not walk.
#   /static    file mount, not a response this app composes
#   /logout    destroys the session mid-scan; its body is an empty redirect
#   /webhooks/ the carrier posts to these. The path IS the carrier's name, by
#              design — it is our integration surface, and the client has no
#              login to it. gate.sh excludes it for the same reason.
EXEMPT_PATHS = {"/static", "/logout"}
EXEMPT_PREFIXES = ("/webhooks",)

# Sample values for path parameters, filled in by the fixture below. A route
# whose parameter is missing from here fails test_every_client_route_is_scanned.
PATH_VALUES: dict = {}


@pytest.fixture(scope="module")
def client():
    db = SessionLocal()
    try:
        for phone in DEMO_PHONES:
            contact_service.upsert_contact(db, phone=phone, full_name="Scan Target",
                                           source="test")
    finally:
        db.close()

    c = TestClient(app)
    login = c.post("/login", data={"username": "admin", "password": PASSWORD})
    # Asserted, not assumed. POST /login is capped at 10/minute per IP and every
    # test module logs in from the same address inside one window; when this
    # module's login came back 429, every scan below ran against an
    # unauthenticated 401 body and passed by containing nothing. A white-label
    # test that passes because it never got in is worse than no test. If this
    # ever fires, give the offending module a module-scoped login fixture.
    assert login.status_code in (200, 302), (
        f"login failed with {login.status_code} — the scan would have run "
        f"against 401 bodies and passed vacuously"
    )

    # A real draft, so the detail routes render a populated payload rather than
    # a 404 body that would pass every assertion by having nothing in it.
    # Never sent: this must not add billable messages to the shared cycle.
    categories = c.get("/api/categories").json()["categories"]
    PATH_VALUES["category_id"] = categories[0]["id"]

    created = c.post("/api/campaigns", json={
        "name": "white-label scan",
        "message_template": "Hi {first_name}, scanning.",
        "audience": "all",
        "category_id": categories[0]["id"],
    })
    assert created.status_code == 200, created.text
    PATH_VALUES["campaign_id"] = created.json()["campaign"]["id"]

    lists = c.get("/api/lists").json()
    PATH_VALUES["list_id"] = (lists.get("lists") or [{"id": 999999}])[0].get("id", 999999)

    return c


def _get_routes():
    """Every client-facing GET route the app exposes, discovered at runtime."""
    out = []
    for route in app.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", None) or set()
        if "GET" not in methods:
            continue
        if path in EXEMPT_PATHS or path.startswith(EXEMPT_PREFIXES):
            continue
        out.append(path)
    return sorted(out)


def _fill(path: str) -> str:
    for name, value in PATH_VALUES.items():
        path = path.replace("{" + name + "}", str(value))
    return path


# ─── Coverage ───────────────────────────────────────────────────────────────

def test_every_client_route_is_scanned(client):
    """A new route with an unknown path parameter fails here.

    Without this, adding `/api/categories/{category_id}` in module 2 would sail
    through the scan below on a 404 body and look like a pass.
    """
    unresolved = [p for p in _get_routes() if "{" in _fill(p)]
    assert not unresolved, (
        f"No sample value for the path parameter(s) in {unresolved}. Add one to "
        f"PATH_VALUES in this file so the route is actually exercised."
    )
    assert len(_get_routes()) >= 15, "route discovery returned suspiciously little"


# ─── The carrier is invisible ───────────────────────────────────────────────

def test_no_response_names_the_carrier(client):
    """Pages and JSON alike. This is the assertion the gate cannot make."""
    offenders = []
    for path in _get_routes():
        response = client.get(_fill(path))
        if CARRIER_RE.search(response.text):
            offenders.append(f"{path} -> {CARRIER_RE.search(response.text).group(0)}")
    assert not offenders, f"carrier name reached a client response: {offenders}"


def test_degraded_send_path_names_no_carrier(client):
    """Every route again, this time with the carrier failing to start.

    The scan above runs on the console provider, where nothing in the process
    has ever held the carrier's name. The degraded state is the opposite case:
    the app is holding a raw SDK exception that says "module 'telnyx' has no
    attribute 'Telnyx'", and it is describing that failure on six screens and in
    a JSON payload. Those strings are new as of session 5c, they are the ones a
    future reader is most tempted to make "more helpful" by pasting the
    exception into, and no grep of the templates could tell.
    """
    with degraded_provider():
        assert CARRIER_RE.search(factory.provider_fallback().error), (
            "the harness must reproduce an error that names the carrier, or "
            "this test proves nothing"
        )
        # Two positive controls before the sweep. The routes below return a mix
        # of 200s and 404s by design, so a blanket status assertion inside the
        # loop would be wrong — but "no carrier name" is equally true of a 302
        # to /login, so prove first that this client is authenticated and that
        # the degraded state is actually in effect while the scan runs.
        settings_page = client.get("/settings")
        assert settings_page.status_code == 200, settings_page.status_code
        assert 'data-mode="unavailable"' in settings_page.text, (
            "the sweep below would have scanned pages in the ordinary dry-run state"
        )
        offenders = []
        for path in _get_routes():
            response = client.get(_fill(path))
            if CARRIER_RE.search(response.text):
                offenders.append(f"{path} -> {CARRIER_RE.search(response.text).group(0)}")
    assert not offenders, f"carrier name reached a client response while degraded: {offenders}"


def test_post_responses_do_not_name_the_carrier(client):
    """The write paths that return provider-derived text.

    test-sms and campaign creation both surface an error string that originates
    in the carrier SDK; scrub_provider_text() is what keeps them clean, and this
    is what proves it still runs.
    """
    responses = [
        client.post("/api/campaigns/test-sms",
                    json={"phone": "+15555550100", "message": "scan"}),
        client.post("/api/campaigns/preview",
                    json={"message_template": "scan", "audience": "all"}),
        client.post("/api/settings/auto-reply/reset"),
    ]
    for response in responses:
        assert not CARRIER_RE.search(response.text), response.text[:300]


def test_carrier_branded_text_written_by_a_webhook_is_scrubbed_on_the_way_out(client):
    """The auto-block writes carrier free text into a column the client reads.

    Session 5d wired `should_auto_block()` into the delivery webhook, which made
    `blocked_numbers.notes` the first client-rendered string in this codebase
    assembled verbatim from text a carrier chose — 2,673 rows in one campaign.
    Its only defence is `scrub_provider_text()`, and that regex was anchored
    `\\b(?:telnyx|...)\\b`: the trailing boundary fails the moment an SDK glues
    the name to a word, which is how SDKs write things. "TelnyxError 40300" went
    through untouched.

    The sweep above cannot catch this. It walks /api/blocklist, but only over
    whatever rows happen to exist, and no other module writes carrier-branded
    notes. This one writes them and reads them back.
    """
    from app.models.blocked_number import BlockedNumber
    from app.models.sms_message import SMSMessage
    from app.routers.webhooks.common import record_delivery_status

    phone = "+15555550970"
    external_id = "whitelabel-notes-probe"

    # The wordings that defeated the anchored regex, plus one it always caught.
    carrier_error = (
        "TelnyxError 40300: telnyx_api reports the destination is not routable. "
        "Raised by twilio.rest.exceptions.TwilioRestException. "
        "See https://developers.telnyx.com/docs/errors"
    )

    db = SessionLocal()
    try:
        db.query(BlockedNumber).filter(BlockedNumber.phone == phone).delete(
            synchronize_session=False)
        db.query(SMSMessage).filter(SMSMessage.external_id == external_id).delete(
            synchronize_session=False)
        db.add(SMSMessage(phone=phone, message="probe", status="sent",
                          external_id=external_id, sent_at="2026-08-26T09:00:00"))
        db.commit()
        record_delivery_status(db, external_id, "delivery_failed",
                               carrier_error, source="telnyx")
        db.commit()
        stored = db.query(BlockedNumber).filter(BlockedNumber.phone == phone).first()
        assert stored is not None, (
            "the probe did not produce a blocklist row, so it scans nothing"
        )
        assert CARRIER_RE.search(carrier_error), (
            "the probe text must name the carrier or this proves nothing"
        )

        body = client.get("/api/blocklist").text
        page = client.get("/blocklist").text
    finally:
        db.query(BlockedNumber).filter(BlockedNumber.phone == phone).delete(
            synchronize_session=False)
        db.query(SMSMessage).filter(SMSMessage.external_id == external_id).delete(
            synchronize_session=False)
        db.commit()
        db.close()

    assert not CARRIER_RE.search(body), (
        f"the carrier's name reached /api/blocklist through an auto-block note: "
        f"{CARRIER_RE.search(body).group(0)!r}"
    )
    assert not CARRIER_RE.search(page)


def test_system_info_exposes_no_webhook_url(client):
    """The webhook URL is PUBLIC_BASE_URL + "/webhooks/" + provider.name.

    It printed the carrier's name onto the Settings page without the name ever
    appearing in a template, and it is a setup value only we use.
    """
    body = client.get("/api/settings/system").json()
    assert "webhook" not in json.dumps(body).lower(), body


# ─── Our cost is invisible ──────────────────────────────────────────────────

def test_campaign_payloads_carry_no_estimated_cost(client):
    """estimated_cost is priced at our wholesale rate. It is not his number:
    it discloses our margin and reads ~40% below what he is invoiced."""
    campaign_id = PATH_VALUES["campaign_id"]
    for path in ("/api/campaigns", f"/api/campaigns/{campaign_id}"):
        assert "estimated_cost" not in client.get(path).text, path

    # The cross-category override path, scanned too: it is the one create that
    # produces a campaign with no category, and its payload must be as free of
    # our wholesale figure as any other.
    created = client.post("/api/campaigns", json={
        "name": "white-label scan 2",
        "message_template": "Hi {first_name}, scanning again.",
        "audience": "all",
        "cross_category_override": True,
    })
    assert "estimated_cost" not in created.text


def test_no_response_quotes_our_wholesale_rate(client):
    """The rate itself, in case a future payload helpfully includes it."""
    rate = str(settings.WHOLESALE_COST_PER_SEGMENT)
    for path in _get_routes():
        assert rate not in client.get(_fill(path)).text, path


# ─── The pre-flight refusal is worded in his units ──────────────────────────

class _StubProvider:
    """A carrier account with almost no money in it."""

    name = "console"

    def __init__(self, balance: float):
        self._balance = balance

    async def get_balance(self):
        return self._balance


def test_preflight_refusal_names_no_carrier_and_quotes_no_money():
    """The abort reason is stored on the campaign and rendered in his list.

    It used to quote our carrier account's dollar balance. He is metered in
    segments and should read the refusal in segments; the money view goes to
    our log, which he never sees.
    """
    db = SessionLocal()
    try:
        service = CampaignService(db)
        service.provider = _StubProvider(balance=1.00)
        # Not persisted: pre-flight only reads these, and a stray aborted
        # campaign would be debris in a suite that shares one database.
        campaign = Campaign(id=0, estimated_cost=250.0, estimated_segments=27_000)
        ok, detail = asyncio.run(service.preflight(campaign))
    finally:
        db.close()

    assert ok is False, "pre-flight must refuse a campaign the balance cannot fund"
    assert "$" not in detail, detail
    assert not CARRIER_RE.search(detail), detail
    assert "segments" in detail


# ─── 5e's new client-facing surfaces ────────────────────────────────────────
#
# The lesson this file exists for: every white-label leak found so far was
# assembled at runtime — an f-string, a URL built from the provider name, a
# `str(e)` off an SDK exception. The gate greps for literals and structurally
# cannot see any of them, so a new surface gets a case here, which runs the code
# rather than reading it.
#
# 5e adds three: a campaign created from an upload, a top-up refusal, and the
# reason a campaign that reached nobody now carries. The last two are the risky
# ones — both are sentences built at runtime and stored on a row the client reads
# back off the campaign rail.

def test_a_top_up_refusal_on_a_degraded_box_names_no_carrier():
    """The refusal is `send_path_assessment()`'s wording, and has to stay so.

    `provider_fallback()` carries raw SDK text — `module 'telnyx' has no
    attribute 'Telnyx'` — and this refusal travels straight into an HTTP response
    body. A future edit that "helps support" by appending the cause is the exact
    leak session 5c removed from the pill.
    """
    from app.services import campaign_topup
    from app.models.campaign import Campaign as _Campaign
    from app.sms.factory import send_path_assessment
    from tests._provider_setup import degraded_provider

    from app.models.contact import Contact
    from app.models.contact_list import ContactList, ContactListMember
    from app.services import contact_service

    db = SessionLocal()
    try:
        # Real state, because the degraded refusal is the *last* thing `assess()`
        # checks: the state check and the list-audience check come first, both
        # deliberately, and a campaign that trips either never reaches the branch
        # under test. An earlier version of this test used `audience="all"` and
        # was silently exercising the wrong refusal — its own identity assertion
        # is what caught that, which is the argument for pinning identity rather
        # than pattern-matching for a carrier name.
        contact = contact_service.upsert_contact(
            db, phone="+15555559901", full_name="WL", source="whitelabel-test")
        listing = contact_service.get_or_create_list(db, "whitelabel top-up list")
        campaign = _Campaign(id=0, status="completed",
                             audience=f"list:{listing.id}",
                             name="wl", message_template="hi",
                             created_at="2000-01-01T00:00:00")
        contact_service.add_to_list(db, listing.id, contact.id)
        db.commit()

        with degraded_provider():
            verdict = asyncio.run(campaign_topup.assess(db, campaign))
            expected = send_path_assessment()["abort_detail"]
    finally:
        # The campaign is never persisted; the list and contact are, so they go.
        db.query(ContactListMember).filter(
            ContactListMember.list_id == listing.id).delete(synchronize_session=False)
        db.query(ContactList).filter(ContactList.id == listing.id).delete(
            synchronize_session=False)
        db.query(Contact).filter(Contact.phone == "+15555559901").delete(
            synchronize_session=False)
        db.commit()
        db.close()

    # The *degraded* refusal specifically. `assess()` has three other ways to
    # refuse — wrong state, nobody new, no capacity — and every one of them is
    # carrier-free by construction, so a test that accepted any of them would go
    # green while never exercising the one built from SDK-adjacent state.
    assert verdict["refusal"] == expected, (
        f"expected the degraded-send-path refusal, got {verdict['refusal']!r}")
    assert not CARRIER_RE.search(verdict["refusal"]), verdict["refusal"]
    assert "$" not in verdict["refusal"], verdict["refusal"]


def test_the_zero_send_reasons_name_no_carrier_and_quote_no_money():
    """Stored as `abort_reason` and rendered verbatim on the campaign rail.

    Every branch, not the one a campaign in this suite happens to reach: these
    are the entire user-visible output of A7 and there is no detail screen behind
    them. A reason that quoted our wholesale rate would put our margin on the one
    screen he reads after every campaign.
    """
    from app.services import campaign_outcome

    rate = str(settings.WHOLESALE_COST_PER_SEGMENT)
    reasons = [
        campaign_outcome.zero_send_reason(queued=0, suppressed=6857),
        campaign_outcome.zero_send_reason(queued=0),
        campaign_outcome.zero_send_reason(queued=4, blocked=4),
        campaign_outcome.zero_send_reason(queued=4, region_skipped=4),
        campaign_outcome.zero_send_reason(queued=4, failed=4),
        campaign_outcome.zero_send_reason(queued=9, blocked=2, failed=1),
    ]
    for reason in reasons:
        assert not CARRIER_RE.search(reason), reason
        assert "$" not in reason, reason
        assert rate not in reason, reason
