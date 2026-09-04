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
from app.models.sms_message import SMSMessage, BILLABLE_STATUSES, HELD_BACK_STATUS
from app.services import preflight_service, suppression_service
from app.services.campaign_service import CampaignError, CampaignService
# Scheduling moved to campaign_dispatch in session 5d, when campaign_service
# crossed the 500-line rule. Same functions, same behaviour — the split is along
# "when a send begins" and nothing about *whether* it may moved with it.
from app.services.campaign_dispatch import due_campaign_ids, run_due_campaigns

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

def test_a_list_audience_needs_no_category(client, seeded):
    """5i criterion 5, first half. This assertion used to be its inverse.

    Until 5i this exact request was a 400 naming the category and the override,
    and that was right while the composer had a category picker in front of it.
    The picker is gone, so a campaign pointed at one of his lists has nothing to
    put in that field and no way to type an override — the request the composer
    now sends is exactly this one, and refusing it would refuse every campaign.

    Through the endpoint rather than through `resolve_category()`, deliberately:
    the requirement is about what the client can create, and a property proved of
    the helper is not proved of the route that calls it.
    """
    response = client.post("/api/campaigns", json={
        "name": f"{CAMPAIGN_PREFIX}list audience",
        "message_template": "Sale Thursday. Reply STOP to opt out.",
        "audience": seeded["audience"],
    })
    assert response.status_code == 200, response.text
    created = response.json()["campaign"]

    db = SessionLocal()
    try:
        row = db.get(Campaign, created["id"])
        assert row.category_id is None
        # Not recorded as an override either. An override is evidence that a
        # human decided something, and nobody decided anything here.
        assert not row.cross_category_override
    finally:
        db.close()


def test_the_all_audience_needs_no_category(client, seeded):
    """5i criterion 5, second half — the pinned entry in the picker."""
    response = client.post("/api/campaigns", json={
        "name": f"{CAMPAIGN_PREFIX}all audience",
        "message_template": "Sale Thursday. Reply STOP to opt out.",
        "audience": "all",
    })
    assert response.status_code == 200, response.text

    db = SessionLocal()
    try:
        row = db.get(Campaign, response.json()["campaign"]["id"])
        assert row.category_id is None
        assert not row.cross_category_override
    finally:
        db.close()


def test_a_malformed_list_selector_is_refused_with_its_own_sentence(client, seeded):
    """What else arrives on the path 5i widened.

    Until 5i a hand-written `list:abc` never reached the resolver: the category
    rule refused it first, with the wrong message but the right status. Relaxing
    that rule for list audiences moved a bad selector one step further down, onto
    `_int_arg()`'s ValueError — which the router does not map, so it became a 500
    reading "Could not create campaign" with the real reason left in a log the
    client cannot read. A refusal that names no cause reads as a broken tool.
    """
    response = client.post("/api/campaigns", json={
        "name": f"{CAMPAIGN_PREFIX}malformed selector",
        "message_template": "Sale Thursday. Reply STOP to opt out.",
        "audience": "list:not-a-number",
    })
    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert "numeric id" in detail, detail
    assert "list:not-a-number" in detail, detail


def test_a_category_selector_with_no_category_is_still_rejected(client, seeded):
    """5i criterion 5, third half — the case the rule still covers.

    No screen can produce a `category:` selector, so a caller writing one is
    doing something deliberate, and for that selector the audience genuinely does
    not say which auction the message is about. The refusal still has to say what
    to do about it rather than only that something is wrong.
    """
    response = client.post("/api/campaigns", json={
        "name": f"{CAMPAIGN_PREFIX}no category",
        "message_template": "Sale Thursday. Reply STOP to opt out.",
        "audience": "category:food_service",
    })
    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert "category" in detail.lower(), detail
    assert "override" in detail.lower(), detail


def test_the_category_columns_still_record_what_a_caller_supplies(client, seeded):
    """The columns still work; it is the payload that stopped carrying them.

    5i took `category_id`, `category_label`, `category_color_token` and
    `cross_category_override` out of the campaign payload — the category is not a
    client-facing concept any more. Nothing came out of the schema, and this is
    what says so: both values are still stored, and the override still leaves the
    mark it existed to leave. It is unreachable from a screen after 5i — the
    checkbox is gone, and `tests/test_audience_surfaces.py` proves no rendered
    body carries it — so a deliberate API call is the only caller left, which is
    the only kind the override was ever evidence about.

    Through the service rather than the endpoint, and that is the file's own
    budget rule rather than a preference: `POST /api/campaigns` is capped at
    5/minute per IP and the whole suite shares one window from one address. The
    three creates that must go over HTTP are the criterion-5 ones above, because
    what they assert is what the client can create.
    """
    db = SessionLocal()
    try:
        service = CampaignService(db)
        tagged = service.create_campaign(
            name=f"{CAMPAIGN_PREFIX}categorised",
            message_template="Fryer sale Thursday. Reply STOP to opt out.",
            audience=seeded["audience"], category_id=seeded["category_id"])
        assert tagged.category_id == seeded["category_id"]
        assert not tagged.cross_category_override

        overridden = service.create_campaign(
            name=f"{CAMPAIGN_PREFIX}override",
            message_template="We are closed Monday. Reply STOP to opt out.",
            audience=seeded["audience"], cross_category_override=True)
        assert overridden.category_id is None
        assert overridden.cross_category_override == 1
    finally:
        db.close()

    # And the payload the composer's rail reads carries none of it.
    listed = client.get("/api/campaigns?limit=8").json()["campaigns"]
    assert listed, "no campaigns came back, so the assertion below scans nothing"
    for row in listed:
        for field in ("category_id", "category_label", "category_color_token",
                      "cross_category_override"):
            assert field not in row, f"{field} is back in the campaign payload"


def test_unknown_category_is_rejected(client, seeded):
    response = client.post("/api/campaigns", json={
        "name": f"{CAMPAIGN_PREFIX}ghost category",
        "message_template": "Hello. Reply STOP to opt out.",
        "audience": seeded["audience"],
        "category_id": 999999,
    })
    assert response.status_code == 400, response.text


def test_the_rule_lives_in_the_service_not_the_router():
    """A script or a future screen cannot route around it.

    The audience argument is passed through the service wrapper as well as the
    builder function, because a wrapper whose signature is a subset of the rule
    it delegates to is a second, quieter version of that rule — which one you get
    would then depend on which name you called.
    """
    db = SessionLocal()
    try:
        service = CampaignService(db)
        with pytest.raises(CampaignError):
            service.resolve_category(None, cross_category_override=False)
        assert service.resolve_category(None, cross_category_override=True) is None
        # 5i, through the wrapper: the audience can satisfy the rule on its own.
        assert service.resolve_category(
            None, cross_category_override=False, audience="all") is None
        with pytest.raises(CampaignError):
            service.resolve_category(None, cross_category_override=False,
                                     audience="category:food_service")
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
        # `held_back` since 5h, not `skipped`. The status this row carries is the
        # whole of decision 005: `skipped` also means "wrong region", which is
        # permanent, so a row under that word could never be released once the
        # hold expired. Asserting it is not `skipped` as well as that it is
        # `held_back` because the two halves fail differently — a build that
        # wrote neither would satisfy an `!=` on its own.
        assert rows[RECENT_PHONE].status == HELD_BACK_STATUS
        assert rows[RECENT_PHONE].status != "skipped"
        assert rows[FRESH_PHONE].status == "pending"
        assert rows[OLD_PHONE].status == "pending"
    finally:
        db.close()


def test_a_suppressed_message_is_never_billed(seeded):
    """`held_back` is outside the billable set, which is what "unbilled" means.

    The status changed in 5h and the reason it is unbilled did not: a message
    that was never handed to a carrier is not a segment. Same shape as 5d's
    `not_sent`.

    It builds its own campaign. It used to read the row the test above happens
    to leave behind, which passed in a full run and failed the moment
    `agent/accept-5h.sh` ran criterion 4's tests on their own — the acceptance
    script running each criterion in isolation is precisely what that discipline
    is for, and this is the third time it has caught a test leaning on a
    neighbour.
    """
    assert HELD_BACK_STATUS not in BILLABLE_STATUSES

    db = SessionLocal()
    try:
        campaign = _create(db, seeded, "billing suppression",
                           "Sale Thursday. Reply STOP to opt out.",
                           category_id=seeded["category_id"])
        held = (db.query(SMSMessage)
                .filter(SMSMessage.campaign_id == campaign.id,
                        SMSMessage.phone == RECENT_PHONE,
                        SMSMessage.status == HELD_BACK_STATUS)
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

    db = SessionLocal()
    try:
        # A Session is required, not optional: since 5e A5 the window comes from
        # app_settings with `.env` as the default, and a caller that could omit
        # the session would silently get the `.env` value on that one path.
        sendable, suppressed = suppression_service.partition_recent(
            db, [_Contact(None), _Contact(iso_days_ago(2)), _Contact(iso_days_ago(5))]
        )
    finally:
        db.close()
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
