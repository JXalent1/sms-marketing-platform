"""A box that cannot send refuses to try, and is never billed for trying.

Session 5c made a failed carrier visible. It did not change what happened if
someone sent anyway, and that is what this file covers. On the code as it stood
at the end of 5c:

  - `get_provider()` fell back to the console provider, which answers the
    pre-flight balance question with 999,999 and reports every send successful
  - so the capacity check — the single most valuable safeguard in the codebase —
    passed, because the provider it interrogates is a stub with a bottomless
    balance
  - every row was written `sent`, and `sent` is in BILLABLE_STATUSES

A live box whose carrier failed to start could therefore send to all 1,223
contacts, show every row green, and invoice for messages nobody received. The
damage is not the $18: A4A runs a different-niche auction nearly every day, and
a blast that silently does not go out means an empty room at a sale he has
already paid to stage. See decisions/002-degraded-box-still-bills.md.

Every test here fails against the pre-5d tree. `agent/accept-5d.sh` proves that
rather than asserting it.

Nothing here sends. `conftest.py` forces SMS_PROVIDER=console for the whole
suite, `degraded_provider()` never builds a carrier object at all, and every
context manager restores the factory on the way out.
"""

import asyncio
import json
import os
import re

import pytest
from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.core.config import settings
from app.models.campaign import Campaign
from app.models.contact import Contact
from app.models.contact_list import ContactList, ContactListMember

from app.models.sms_message import SMSMessage, BILLABLE_STATUSES
from app.main import app
from app.services import billing_service, contact_service
from app.services.campaign_service import DEGRADED_STATUS, CampaignService
from app.sms import factory
# The wording lives beside send_mode() in app/sms/factory.py, not in the service:
# the layer that knows the carrier's name owns every client-safe sentence about
# it, which is the same reason scrub_provider_text() lives in app/sms/phone.py.
from app.sms.factory import send_path_assessment
from tests import _wholesale_scan as scan
from tests._provider_setup import degraded_provider

PASSWORD = os.environ["ADMIN_PASSWORD"]

# 555-05xx: this module's own range. test_smoke owns 555-01xx and asserts an
# exact recipient count over the "all" audience, so a contact left behind here
# would fail a module three files away for a reason nobody would look for.
PHONES = ("+15555550501", "+15555550502")
LIST_NAME = "5d degraded-send list"
CAMPAIGN_PREFIX = "degraded:"

SHELL_PAGES = ("/dashboard", "/campaigns", "/contacts", "/blocklist", "/usage", "/settings")
CARRIER_RE = re.compile(r"telnyx|twilio", re.IGNORECASE)


# ─── Fixtures ───────────────────────────────────────────────────────────────

def _purge(db) -> None:
    campaign_ids = [c.id for c in db.query(Campaign)
                    .filter(Campaign.name.like(f"{CAMPAIGN_PREFIX}%"))]
    if campaign_ids:
        db.query(SMSMessage).filter(SMSMessage.campaign_id.in_(campaign_ids)).delete(
            synchronize_session=False)
        db.query(Campaign).filter(Campaign.id.in_(campaign_ids)).delete(
            synchronize_session=False)

    contact_ids = [c.id for c in db.query(Contact).filter(Contact.phone.in_(PHONES))]
    if contact_ids:
        db.query(SMSMessage).filter(SMSMessage.contact_id.in_(contact_ids)).delete(
            synchronize_session=False)
        db.query(ContactListMember).filter(
            ContactListMember.contact_id.in_(contact_ids)).delete(synchronize_session=False)
        db.query(Contact).filter(Contact.id.in_(contact_ids)).delete(
            synchronize_session=False)

    for row in db.query(ContactList).filter(ContactList.name == LIST_NAME):
        db.query(ContactListMember).filter(ContactListMember.list_id == row.id).delete(
            synchronize_session=False)
        db.delete(row)
    db.commit()


@pytest.fixture(scope="module")
def seeded():
    """Two never-texted contacts on a list of their own.

    On a list rather than in a category, for the same reason
    `_guardrail_setup.seed()` is: tagging contacts into a seeded category moves
    the per-category counts test_categories asserts on.
    """
    db = SessionLocal()
    try:
        _purge(db)
        contact_list = contact_service.get_or_create_list(db, LIST_NAME)
        for phone in PHONES:
            contact = contact_service.upsert_contact(
                db, phone=phone, full_name="Degraded Test", source="5d-test")
            contact_service.add_to_list(db, contact_list.id, contact.id)
        db.commit()
        yield {"audience": f"list:{contact_list.id}"}
        _purge(db)
    finally:
        db.close()


@pytest.fixture(scope="module", autouse=True)
def rate_limit_budget():
    """Hand back the two limiter budgets this module spends.

    POST /login is 10/minute per IP and the whole suite logs in from one address
    inside one window; POST /api/campaigns/test-sms is 5/minute on a second
    limiter and this module calls it twice. Same discipline as
    test_provider_status and _guardrail_setup.rate_limit_fixture_body().
    """
    from app.routers import campaigns as campaigns_router
    from app.routers import pages as pages_router

    pages_router.limiter.reset()
    campaigns_router.limiter.reset()
    yield
    pages_router.limiter.reset()
    campaigns_router.limiter.reset()


@pytest.fixture(scope="module")
def client(seeded):
    c = TestClient(app)
    login = c.post("/login", data={"username": "admin", "password": PASSWORD})
    assert login.status_code in (200, 302), (
        f"login failed with {login.status_code} — every assertion below would "
        f"have run against a 401 body"
    )
    return c


def _create_draft(db, audience: str, name: str) -> Campaign:
    """A draft over this module's two contacts, every time.

    `last_messaged_at` is cleared first because one test below runs a real
    console dry run, which stamps both contacts as texted — and recent-contact
    suppression would then queue the *next* campaign with zero recipients, so
    the test after it would assert against an empty send loop and pass for the
    wrong reason.
    """
    db.query(Contact).filter(Contact.phone.in_(PHONES)).update(
        {"last_messaged_at": None}, synchronize_session=False)
    db.commit()
    return CampaignService(db).create_campaign(
        name=name,
        message_template=f"{settings.BRAND_NAME}: test. Reply STOP to opt out.",
        audience=audience,
        cross_category_override=True,
    )


async def _preflight_passed(campaign):
    """Stands in for a pre-flight that passed a moment before the provider died."""
    return True, "pre-flight passed before the provider degraded"


# ─── A1: the send path is assessed, and dry run is excluded ─────────────────

def test_a_chosen_dry_run_passes_the_send_path_check():
    """The demo flow is how this product gets sold. It must be untouched."""
    assessment = send_path_assessment()
    assert factory.send_mode().key == "dry_run"
    assert assessment["ok"] is True
    assert assessment["mode"] == "dry_run"


def test_a_failed_carrier_fails_the_send_path_check():
    with degraded_provider():
        assessment = send_path_assessment()
    assert assessment["ok"] is False
    assert assessment["mode"] == "unavailable"
    assert not CARRIER_RE.search(assessment["detail"]), assessment["detail"]


def test_the_refusal_is_worded_in_the_right_tense_for_each_surface():
    """One string served both surfaces, so the composer told him "Nothing was
    sent." about a draft he had not sent — which reads as a past campaign having
    silently failed, on the screen whose job is to stop him before he starts."""
    with degraded_provider():
        assessment = send_path_assessment()

    assert assessment["detail"].endswith("Nothing will be sent."), assessment["detail"]
    assert "was sent" not in assessment["detail"], (
        "the pre-send row talks about a send that already happened"
    )
    assert "Nothing was sent." in assessment["abort_detail"]
    assert assessment["detail"] != assessment["abort_detail"]


def test_the_refusal_quotes_no_money_and_names_no_carrier():
    """Same discipline as the capacity refusal: he is metered in segments and
    the carrier is our implementation detail."""
    with degraded_provider():
        detail = send_path_assessment()["detail"]
    assert "$" not in detail, detail
    # See `tests/_wholesale_scan.py`: our figures are compared as numbers
    # everywhere now, not looked for as substrings.
    scan.assert_no_wholesale_figure(detail, where="the degraded refusal")


# ─── A1: the composer shows it before the send ──────────────────────────────

def test_the_composer_shows_a_degraded_row_before_the_send(client, seeded):
    """A pre-flight row, not an error after the fact.

    The checklist is drawn from whatever the server returns, so this is the
    whole of "he sees the reason before he presses send".
    """
    body = {"message_template": f"{settings.BRAND_NAME}: hi. Reply STOP to opt out.",
            "audience": seeded["audience"]}

    calm = client.post("/api/campaigns/preflight", json=body).json()
    calm_row = {c["key"]: c for c in calm["checks"]}["send_path"]
    assert calm_row["status"] == "pass", "a chosen dry run must not fail the checklist"

    with degraded_provider():
        broken = client.post("/api/campaigns/preflight", json=body).json()

    row = {c["key"]: c for c in broken["checks"]}["send_path"]
    assert row["status"] == "fail", "the degraded row warns instead of refusing"
    assert broken["ok"] is False, (
        "the report still reads OK overall, so the composer would draw a green "
        "checklist on a box that cannot send"
    )
    assert row["reason"], "the row has no reason, so he is refused without being told why"
    assert not CARRIER_RE.search(json.dumps(broken)), broken


def test_the_degraded_row_is_first_in_the_checklist(client, seeded):
    """Order is fixed so the checklist does not reshuffle between keystrokes,
    and the check that stops the send is the one he reads first."""
    with degraded_provider():
        report = client.post("/api/campaigns/preflight", json={
            "message_template": f"{settings.BRAND_NAME}: hi. Reply STOP to opt out.",
            "audience": seeded["audience"],
        }).json()
    assert [c["key"] for c in report["checks"]][:2] == ["send_path", "capacity"]


# ─── A1: the send path refuses ──────────────────────────────────────────────

def test_a_degraded_campaign_is_refused_and_nothing_is_sent(seeded):
    """The campaign that the capacity check could never have stopped.

    Console's get_balance() returns 999,999 with the comment "never trips the
    pre-flight check", so on the pre-5d tree this campaign completed with every
    row `sent`.
    """
    db = SessionLocal()
    try:
        campaign = _create_draft(db, seeded["audience"], f"{CAMPAIGN_PREFIX}refused")
        campaign_id = campaign.id
        assert campaign.total_recipients == len(PHONES)

        with degraded_provider():
            result = asyncio.run(CampaignService(db).send_campaign(campaign_id))

        assert result.status == "aborted", (
            f"campaign is {result.status!r} — a degraded box started a send"
        )
        assert result.sent_count == 0
        assert result.abort_reason, "aborted with no reason on the campaign"
        assert not CARRIER_RE.search(result.abort_reason), result.abort_reason

        rows = db.query(SMSMessage).filter(SMSMessage.campaign_id == campaign_id).all()
        assert rows, "the draft queued no rows, so this proves nothing"
        assert all(r.status == "pending" for r in rows), (
            "a refused campaign still touched its rows"
        )
        assert all(r.sent_at is None for r in rows)
        assert not any(r.status in BILLABLE_STATUSES for r in rows)
    finally:
        db.close()


def test_the_same_campaign_sends_normally_in_a_chosen_dry_run(seeded):
    """The before/after control. Nothing about the console path changed."""
    db = SessionLocal()
    try:
        campaign = _create_draft(db, seeded["audience"], f"{CAMPAIGN_PREFIX}dry-run")
        result = asyncio.run(CampaignService(db).send_campaign(campaign.id))

        assert result.status == "completed"
        assert result.sent_count == len(PHONES)
        rows = db.query(SMSMessage).filter(SMSMessage.campaign_id == campaign.id).all()
        assert {r.status for r in rows} == {"sent"}
        assert all(r.sent_at for r in rows)
    finally:
        db.close()


def test_the_send_button_refuses_immediately_and_keeps_the_draft(client, seeded):
    """Not "Campaign sending started", and not a consumed campaign.

    The endpoint used to queue the background task and answer optimistically, so
    for the seconds until the rail next polled the product told him his blast had
    begun. And refusing before the task is queued leaves the campaign a draft:
    decision 002's own justification is that a campaign which never ran can
    simply be re-run, and nothing in this codebase moves one back from aborted.
    """
    db = SessionLocal()
    try:
        campaign = _create_draft(db, seeded["audience"], f"{CAMPAIGN_PREFIX}button")
        campaign_id = campaign.id
    finally:
        db.close()

    with degraded_provider():
        response = client.post(f"/api/campaigns/{campaign_id}/send")

    assert response.status_code == 409, (
        f"the send button answered {response.status_code} on a degraded box"
    )
    detail = response.json()["detail"]
    assert detail.endswith("Nothing will be sent."), detail
    assert not CARRIER_RE.search(detail), detail

    db = SessionLocal()
    try:
        after = db.get(Campaign, campaign_id)
        assert after.status == "draft", (
            f"a campaign that never started was consumed as {after.status!r}; he "
            f"would have to rebuild it once the carrier came back"
        )
        assert after.sent_count == 0
        rows = db.query(SMSMessage).filter(SMSMessage.campaign_id == campaign_id).all()
        assert {r.status for r in rows} == {"pending"}
    finally:
        db.close()


def test_a_test_send_is_refused_too(client):
    """The one screen a client uses to decide whether the box is working.

    On the console fallback this answered "Test SMS sent to +1..." about a
    message that reached nobody.
    """
    with degraded_provider():
        payload = client.post("/api/campaigns/test-sms", json={
            "phone": "+15555550599", "message": "probe",
        }).json()

    assert payload["success"] is False
    assert payload["error"], "refused with no explanation"
    assert not CARRIER_RE.search(payload["error"]), payload["error"]

    # And still works when the dry run was chosen.
    ok = client.post("/api/campaigns/test-sms", json={
        "phone": "+15555550599", "message": "probe",
    }).json()
    assert ok["success"] is True


# ─── A2: a degraded row is never billable ───────────────────────────────────

def test_the_degraded_status_is_outside_the_billable_set():
    assert DEGRADED_STATUS not in BILLABLE_STATUSES


def test_a_degraded_row_is_excluded_from_a_billing_cycle_count():
    """The backstop, asserted against the billing query itself.

    Written with `sent_at` set, so the only thing keeping it off the invoice is
    its status. A row with no timestamp would fall outside the cycle window and
    the test would pass without proving anything.
    """
    db = SessionLocal()
    cycle_start, cycle_end, _, _ = billing_service.get_billing_cycle()
    try:
        before_count, before_segments = billing_service.compute_usage(
            db, cycle_start, cycle_end)

        row = SMSMessage(
            phone="+15555550598", message="never left the building",
            status=DEGRADED_STATUS, segments=5,
            sent_at=cycle_start.isoformat() + "T12:00:00",
        )
        db.add(row)
        db.commit()

        count, segments = billing_service.compute_usage(db, cycle_start, cycle_end)
        assert (count, segments) == (before_count, before_segments), (
            "a message that never reached a carrier was counted for the cycle"
        )

        # Positive control: the same row, in the state the pre-5d code wrote,
        # IS billed. Without this the assertion above would also pass if
        # compute_usage were broken outright.
        row.status = "sent"
        db.commit()
        count, segments = billing_service.compute_usage(db, cycle_start, cycle_end)
        assert (count, segments) == (before_count + 1, before_segments + 5)

        db.delete(row)
        db.commit()
    finally:
        db.close()


def test_the_send_loop_writes_the_degraded_status_rather_than_sent(seeded):
    """The backstop under A1, exercised on its own.

    A1 aborts before the loop, so the only way to reach this branch is with a
    pre-flight that has already passed — which is exactly the case it exists
    for: a provider that degrades *between* the check and the blast. Standing a
    passing pre-flight in front of the loop reproduces that, and is the only way
    to test a backstop whose whole point is that nothing should reach it.
    """
    db = SessionLocal()
    try:
        campaign = _create_draft(db, seeded["audience"], f"{CAMPAIGN_PREFIX}backstop")
        campaign_id = campaign.id

        pending = db.query(SMSMessage).filter(
            SMSMessage.campaign_id == campaign_id,
            SMSMessage.status == "pending",
        ).count()
        assert pending == len(PHONES), "no pending rows to run the loop over"

        service = CampaignService(db)
        service.preflight = _preflight_passed
        with degraded_provider():
            asyncio.run(service.send_campaign(campaign_id))

        rows = db.query(SMSMessage).filter(SMSMessage.campaign_id == campaign_id).all()
        assert {r.status for r in rows} == {DEGRADED_STATUS}, (
            f"the send loop wrote {sorted({r.status for r in rows})} on a degraded box"
        )
        assert all(r.sent_at is None for r in rows)
        assert not any(r.status in BILLABLE_STATUSES for r in rows)

        # And the campaign says so. The rail is the entire UI — there is no
        # campaign-detail screen — so it renders the status badge and shows a
        # reason only when abort_reason is set. A blast that reached nobody
        # reporting "completed" with no reason is the same lie one level down.
        final = db.get(Campaign, campaign_id)
        assert final.sent_count == 0
        assert final.status == "aborted", (
            f"a campaign that reached nobody reports {final.status!r}"
        )
        assert final.abort_reason, "aborted mid-send with no reason on the campaign"
        assert "Nothing was sent." in final.abort_reason
        assert not CARRIER_RE.search(final.abort_reason)
    finally:
        db.close()


# ─── A3: an unknown provider degrades instead of 500-ing ────────────────────

def test_an_unrecognised_provider_degrades_rather_than_raising():
    previous = settings.SMS_PROVIDER
    settings.SMS_PROVIDER = "telnix"          # the realistic typo
    try:
        provider = factory.get_provider(force_reload=True)
        assert provider.name == "console", "an unknown provider did not fall back"
        fallback = factory.provider_fallback()
        assert fallback is not None, "the unknown name left no recorded fallback"
        assert fallback.requested == "telnix"
        assert factory.send_mode().key == "unavailable", (
            "a typo in SMS_PROVIDER reports itself as a chosen dry run"
        )
    finally:
        settings.SMS_PROVIDER = previous
        factory.get_provider(force_reload=True)


def test_every_page_still_renders_with_an_unrecognised_provider(client):
    """`.env` is hand-edited over ssh on a live client box. A typo must cost him
    the ability to send, not the ability to log in."""
    previous = settings.SMS_PROVIDER
    settings.SMS_PROVIDER = "telnix"
    try:
        factory.get_provider(force_reload=True)
        for path in SHELL_PAGES:
            response = client.get(path)
            assert response.status_code == 200, f"{path} returned {response.status_code}"
            assert not CARRIER_RE.search(response.text), f"{path} named the carrier"
        # The seventh screen, and the only one a stranger can reach. Fetched
        # anonymously: an authenticated GET /login redirects to /dashboard, so
        # the logged-in client above would have scanned that page twice and
        # this one not at all.
        anon = TestClient(app).get("/login")
        assert anon.status_code == 200, f"/login returned {anon.status_code}"
        assert not CARRIER_RE.search(anon.text)
        assert 'data-mode="unavailable"' in client.get("/dashboard").text
    finally:
        settings.SMS_PROVIDER = previous
        factory.get_provider(force_reload=True)


def test_an_unrecognised_provider_offers_no_sending_number():
    """"(dry run)" is reserved for the console provider being chosen. Saying it
    here would have Settings call the box a dry run directly under a banner
    saying sending is unavailable."""
    previous = settings.SMS_PROVIDER
    settings.SMS_PROVIDER = "telnix"
    try:
        factory.get_provider(force_reload=True)
        assert factory.active_sender_number() == ""
    finally:
        settings.SMS_PROVIDER = previous
        factory.get_provider(force_reload=True)


# ─── A4: /health reports it ─────────────────────────────────────────────────

def test_health_reports_degraded_state_without_naming_a_carrier():
    """The only alert channel that still works when the carrier does not.

    Unauthenticated, so this is deliberately checked without a login: a monitor
    has no session.
    """
    anon = TestClient(app)

    healthy = anon.get("/health")
    assert healthy.status_code == 200
    assert healthy.json()["status"] == "healthy"
    assert healthy.json()["sending_ok"] is True

    with degraded_provider():
        degraded = anon.get("/health")

    assert degraded.status_code == 200, (
        "/health went non-200 while degraded — deployment/deploy.sh rolls the "
        "release back on that, so every deploy to a degraded box would revert, "
        "including the one that fixes it"
    )
    body = degraded.json()
    assert body["status"] == "degraded"
    assert body["sending_ok"] is False
    assert body["reason"], "a monitor would page with no reason to give"
    assert not CARRIER_RE.search(degraded.text), degraded.text
