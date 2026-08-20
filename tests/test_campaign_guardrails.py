"""Module 4 — the composer's guardrails, exercised rather than described.

The category requirement, recent-contact suppression, and scheduled send. The
pre-flight checklist and the cost arithmetic are next door in
`test_campaign_preflight.py`; both files share `_guardrail_setup.py`.

Two things both modules have to be careful about, both learned the hard way.

**They clean up after themselves.** The suite runs against one database with no
rollback between tests, `test_smoke` sends to audience "all" and asserts an
exact `sent_count`, and `test_categories` asserts exact per-category counts.
These modules sort before both, so anything left behind is somebody else's red
test. `purge()` is the same pattern `test_categories` uses.

**They give back the rate-limit budget they spend.** `POST /api/campaigns` is
capped at 5/minute per IP, and the whole suite runs inside a single window from
a single address, so four creates here silently starved test_smoke and
test_whitelabel of theirs — both failed on a 429 that read like a bug in the
code under test. The cap is not the problem and is not weakened: the fixture
below clears the counter on the way in and on the way out, so nothing inherits
or leaves rate-limit debt. Everything not specifically testing the HTTP contract
goes through the service instead.
"""

import asyncio
import os
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.database import SessionLocal
from app.main import app
from app.models.campaign import Campaign
from app.models.sms_message import SMSMessage, BILLABLE_STATUSES
from app.services import preflight_service
from app.services.campaign_service import (
    CampaignError, CampaignService, due_campaign_ids, run_due_campaigns,
)

from tests import _guardrail_setup as setup
from tests._guardrail_setup import (
    CAMPAIGN_PREFIX, FRESH_PHONE, OLD_PHONE, RECENT_PHONE, iso_days_ago,
)

PASSWORD = os.environ["ADMIN_PASSWORD"]


@pytest.fixture(scope="module")
def seeded():
    yield from setup.seeded_fixture_body()


@pytest.fixture(scope="module", autouse=True)
def rate_limit_budget():
    yield from setup.rate_limit_fixture_body()


@pytest.fixture(scope="module")
def client(seeded):
    c = TestClient(app)
    login = c.post("/login", data={"username": "admin", "password": PASSWORD})
    assert login.status_code in (200, 302), (
        f"login failed with {login.status_code} — every assertion below would "
        f"have run against a 401 body"
    )
    return c


def _create(db, seeded, name, body, **kwargs):
    """Create a campaign through the service, bypassing the HTTP rate limit."""
    return CampaignService(db).create_campaign(
        name=f"{CAMPAIGN_PREFIX}{name}",
        message_template=body,
        audience=seeded["audience"],
        **kwargs,
    )


# ─── The category requirement ───────────────────────────────────────────────

def test_campaign_without_a_category_is_rejected(client, seeded):
    response = client.post("/api/campaigns", json={
        "name": f"{CAMPAIGN_PREFIX}no category",
        "message_template": "Sale Thursday. Reply STOP to opt out.",
        "audience": seeded["audience"],
    })
    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert "category" in detail.lower(), detail
    # It has to say what to do about it, not just that something is wrong.
    assert "override" in detail.lower(), detail


def test_campaign_with_a_category_is_accepted_and_records_it(client, seeded):
    response = client.post("/api/campaigns", json={
        "name": f"{CAMPAIGN_PREFIX}categorised",
        "message_template": "Fryer sale Thursday. Reply STOP to opt out.",
        "audience": seeded["audience"],
        "category_id": seeded["category_id"],
    })
    assert response.status_code == 200, response.text
    campaign = response.json()["campaign"]
    assert campaign["category_id"] == seeded["category_id"]
    assert campaign["category_label"]
    assert campaign["cross_category_override"] is False


def test_cross_category_override_is_accepted_and_recorded(client, seeded):
    """The escape hatch works, and leaves a mark saying a human used it."""
    response = client.post("/api/campaigns", json={
        "name": f"{CAMPAIGN_PREFIX}override",
        "message_template": "We are closed Monday. Reply STOP to opt out.",
        "audience": seeded["audience"],
        "cross_category_override": True,
    })
    assert response.status_code == 200, response.text
    campaign = response.json()["campaign"]
    assert campaign["category_id"] is None
    assert campaign["cross_category_override"] is True

    # Persisted, not just echoed — the audit trail has to survive the response.
    db = SessionLocal()
    try:
        assert db.get(Campaign, campaign["id"]).cross_category_override == 1
    finally:
        db.close()


def test_unknown_category_is_rejected(client, seeded):
    response = client.post("/api/campaigns", json={
        "name": f"{CAMPAIGN_PREFIX}ghost category",
        "message_template": "Hello. Reply STOP to opt out.",
        "audience": seeded["audience"],
        "category_id": 999999,
    })
    assert response.status_code == 400, response.text


def test_the_rule_lives_in_the_service_not_the_router():
    """A script or a future screen cannot route around it."""
    db = SessionLocal()
    try:
        with pytest.raises(CampaignError):
            CampaignService(db).resolve_category(None, cross_category_override=False)
        assert CampaignService(db).resolve_category(
            None, cross_category_override=True) is None
    finally:
        db.close()


# ─── Recent-contact suppression ─────────────────────────────────────────────

def test_suppression_excludes_two_days_and_includes_five(seeded):
    """The window is 3 days: 2 days ago is held back, 5 days ago goes."""
    assert settings.RECENT_CONTACT_SUPPRESSION_DAYS == 3

    db = SessionLocal()
    try:
        campaign = _create(db, seeded, "suppression",
                           "Sale Thursday. Reply STOP to opt out.",
                           category_id=seeded["category_id"])

        assert campaign.total_recipients == 2, "never-texted and 5-days-ago should send"
        assert campaign.suppressed_count == 1, "2-days-ago should be held back"

        rows = {m.phone: m for m in db.query(SMSMessage)
                .filter(SMSMessage.campaign_id == campaign.id)}
        assert rows[RECENT_PHONE].status == "skipped"
        assert rows[FRESH_PHONE].status == "pending"
        assert rows[OLD_PHONE].status == "pending"
    finally:
        db.close()


def test_a_suppressed_message_is_never_billed(seeded):
    """`skipped` is outside the billable set, which is what "unbilled" means."""
    assert "skipped" not in BILLABLE_STATUSES

    db = SessionLocal()
    try:
        held = (db.query(SMSMessage)
                .filter(SMSMessage.phone == RECENT_PHONE,
                        SMSMessage.status == "skipped")
                .first())
        assert held is not None, "the suppressed contact should have a queued row"
        # No sent_at, so it cannot fall inside a billing window either.
        assert held.sent_at is None
        assert "held back" in (held.error_message or "").lower()
    finally:
        db.close()


def test_suppression_ignores_category():
    """A contact in three categories is still one person with one phone."""
    class _Contact:
        def __init__(self, last):
            self.last_messaged_at = last

    sendable, suppressed = preflight_service.partition_recent(
        [_Contact(None), _Contact(iso_days_ago(2)), _Contact(iso_days_ago(5))]
    )
    assert len(sendable) == 2 and len(suppressed) == 1


def test_the_cap_applies_to_people_who_will_actually_receive_it(seeded):
    """Cap after suppression, not before — "send to 1" has to mean one send."""
    db = SessionLocal()
    try:
        campaign = _create(db, seeded, "capped", "Sale. Reply STOP to opt out.",
                           category_id=seeded["category_id"], batch_size=1)
        assert campaign.total_recipients == 1
        pending = (db.query(SMSMessage)
                   .filter(SMSMessage.campaign_id == campaign.id,
                           SMSMessage.status == "pending").count())
        assert pending == 1
    finally:
        db.close()


# ─── Scheduled send ─────────────────────────────────────────────────────────

def test_a_future_campaign_is_not_due_yet(seeded):
    future = (datetime.now() + timedelta(days=1)).isoformat()
    db = SessionLocal()
    try:
        campaign = _create(db, seeded, "future", "Sale. Reply STOP to opt out.",
                           category_id=seeded["category_id"], scheduled_at=future)
        assert campaign.scheduled_at == future
        assert campaign.id not in due_campaign_ids(db)
    finally:
        db.close()


def test_a_due_campaign_fires_through_the_normal_send_path(seeded):
    """Scheduling decides when, never whether — pre-flight still runs.

    Asserted by making pre-flight *refuse*: a scheduled campaign that goes
    through the real send path against an unfundable account comes out
    `aborted`, exactly as a hand-sent one does. A scheduler that reached the
    carrier directly would come out `completed`, and `send()` below would have
    raised on the way.
    """
    past = (datetime.now() - timedelta(minutes=5)).isoformat()
    db = SessionLocal()
    try:
        campaign = _create(db, seeded, "due", "Sale. Reply STOP to opt out.",
                           category_id=seeded["category_id"], scheduled_at=past)
        campaign_id = campaign.id
        assert campaign_id in due_campaign_ids(db)
    finally:
        db.close()

    class _BrokeProvider:
        name = "console"

        async def get_balance(self):
            return 0.0

        async def send(self, to, text):        # pragma: no cover — must not run
            raise AssertionError("pre-flight should have refused before any send")

    # Patched where campaign_service *looks it up*, not where it is defined:
    # the module did `from app.sms.factory import get_provider`, so rebinding
    # the name on the factory module would leave the real provider in place and
    # the campaign would sail through on the console provider's fake balance.
    import app.services.campaign_service as campaign_module
    original = campaign_module.get_provider
    campaign_module.get_provider = lambda: _BrokeProvider()
    try:
        dispatched = asyncio.run(run_due_campaigns())
    finally:
        campaign_module.get_provider = original

    assert campaign_id in dispatched

    db = SessionLocal()
    try:
        sent = db.get(Campaign, campaign_id)
        assert sent.status == "aborted", "the scheduled send skipped pre-flight"
        assert "capacity" in (sent.abort_reason or "").lower()
        assert sent.sent_count == 0
        # And nothing was handed to the carrier.
        assert db.query(SMSMessage).filter(
            SMSMessage.campaign_id == campaign_id,
            SMSMessage.status == "sent").count() == 0

        # Already handled, so the next tick must not pick it up again.
        assert campaign_id not in due_campaign_ids(db)
    finally:
        db.close()
