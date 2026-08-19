"""End-to-end smoke test.

Run this after every change and before every deploy, inside the project venv:

    pip install -r requirements.txt -r requirements-dev.txt
    python -m pytest tests/ -v

It exercises the whole stack against the console (dry-run) provider, so it never
sends a message or spends money. What it protects:

  - auth actually covers every API route and page (the one failure that has
    already cost a client real money)
  - opt-outs are honored by the send loop
  - segment counting reflects encoding, so emoji cost is visible
  - billing counts what was actually sent

If you strip features for a new client, delete the matching test rather than
leaving it failing — a red suite that everyone ignores protects nothing.
"""

import os
import time
import pytest

# Environment (DATABASE_URL, dry-run provider, admin credentials) is set in
# tests/conftest.py, which pytest imports before this module. Setting it here
# was a race waiting to happen: any future test module importing app.main
# first would have pinned the settings before this file ran.

from fastapi.testclient import TestClient      # noqa: E402
from app.main import app                       # noqa: E402
from app.core.database import SessionLocal     # noqa: E402
from app.services import contact_service       # noqa: E402

PASSWORD = os.environ["ADMIN_PASSWORD"]

# 555-01xx is reserved for fiction — these can never reach a real subscriber.
DEMO_CONTACTS = [
    ("+15555550100", "Jane Doe"),
    ("+15555550101", "John Smith"),
    ("+15555550102", "Maria Garcia"),
    ("+15555550103", "Sam Patel"),
    ("+15555550104", "Alex Chen"),
]


@pytest.fixture(scope="module")
def seeded():
    db = SessionLocal()
    try:
        for phone, name in DEMO_CONTACTS:
            contact_service.upsert_contact(db, phone=phone, full_name=name, source="test")
    finally:
        db.close()


@pytest.fixture(scope="module")
def client(seeded):
    """One logged-in session for the module, not one per test.

    Function-scoped, this logged in eight times in the few seconds the suite
    takes, and POST /login is capped at 10/minute per IP — the suite sat one
    test away from a 429 that would have looked like an auth bug. The session
    is read-only state here; nothing below depends on a fresh one.
    """
    c = TestClient(app)
    c.post("/login", data={"username": "admin", "password": PASSWORD})
    return c


@pytest.fixture
def anon():
    return TestClient(app)


# ─── Auth ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", [
    "/api/contacts", "/api/campaigns", "/api/blocklist",
    "/api/usage/current", "/api/settings/system",
])
def test_api_requires_auth(anon, path):
    assert anon.get(path).status_code == 401


@pytest.mark.parametrize("path", [
    "/dashboard", "/campaigns", "/contacts", "/usage", "/blocklist", "/settings",
])
def test_pages_redirect_when_anonymous(anon, path):
    assert anon.get(path, follow_redirects=False).status_code == 302


def test_health_is_public(anon):
    assert anon.get("/health").status_code == 200


def test_wrong_password_rejected(anon):
    r = anon.post("/login", data={"username": "admin", "password": "wrong"},
                  follow_redirects=False)
    assert r.status_code == 401


def test_login_succeeds(anon):
    r = anon.post("/login", data={"username": "admin", "password": PASSWORD},
                  follow_redirects=False)
    assert r.status_code == 302


# ─── Segment counting ───────────────────────────────────────────────────────

def test_plain_text_is_gsm7(client):
    r = client.post("/api/campaigns/preview", json={
        "message_template": "Hi {name}, our sale starts Friday. Reply STOP to opt out.",
        "audience": "all",
    }).json()
    assert r["encoding"] == "GSM-7"
    assert r["segments"] == 1


def test_emoji_forces_unicode_and_costs_more(client):
    """The margin-killer. One emoji re-encodes the whole message to UCS-2."""
    r = client.post("/api/campaigns/preview", json={
        "message_template": "\U0001F6A8 Hi {name}, our sale starts Friday! " * 4,
        "audience": "all",
    }).json()
    assert r["encoding"] == "UCS-2"
    assert r["segments"] > r["gsm7_segments_if_stripped"]
    assert r["forced_unicode_by"]


def test_shortener_links_are_flagged(client):
    """Carriers spam-block public shorteners; the send still costs full price."""
    r = client.post("/api/campaigns/preview", json={
        "message_template": "Sale today https://bit.ly/abc123",
        "audience": "all",
    }).json()
    assert "bit.ly" in r["risky_links"]


# ─── Campaign send ──────────────────────────────────────────────────────────

def test_campaign_honors_blocklist_and_bills_correctly(client):
    client.post("/api/blocklist/block",
                json={"phone": "+15555550101", "reason": "stop_keyword"})

    created = client.post("/api/campaigns", json={
        "name": "smoke test",
        "message_template": "Hi {first_name}, testing.",
        "audience": "all",
    }).json()
    assert created["success"]
    campaign_id = created["campaign"]["id"]

    assert client.post(f"/api/campaigns/{campaign_id}/send").json()["success"]

    # Sending runs as a background task.
    for _ in range(40):
        detail = client.get(f"/api/campaigns/{campaign_id}").json()
        if detail["campaign"]["status"] == "completed":
            break
        time.sleep(0.25)

    campaign = detail["campaign"]
    assert campaign["status"] == "completed"
    assert campaign["skipped_count"] == 1, "blocked number should be skipped, not sent"
    assert campaign["sent_count"] == len(DEMO_CONTACTS) - 1

    blocked = [m for m in detail["messages"] if m["phone"] == "+15555550101"][0]
    assert blocked["status"] == "blocked"
    assert blocked["sent_at"] is None


def test_usage_counts_sent_segments(client):
    usage = client.get("/api/usage/current").json()
    assert usage["used_segments"] >= 1
    assert usage["total_due"] >= usage["monthly_fee"]


def test_pricing_comes_from_config(client):
    """The plan is config. The arithmetic lives in tests/test_billing.py."""
    pricing = client.get("/api/usage/pricing").json()
    assert pricing["included_segments"] > 0
    assert pricing["price_per_segment"] > 0


# ─── Compliance ─────────────────────────────────────────────────────────────

def test_inbound_stop_is_persisted(client):
    before = client.get("/api/blocklist/count").json()["count"]
    client.post("/webhooks/telnyx", json={"data": {
        "event_type": "message.received",
        "payload": {"from": {"phone_number": "+15555550102"}, "text": "STOP"},
    }})
    after = client.get("/api/blocklist/count").json()["count"]
    assert after == before + 1, "STOP must be recorded in our own blocklist"


def test_delivery_webhook_marks_undelivered(client):
    """A carrier spam-block arrives here, minutes after the send reported success."""
    from app.core.database import SessionLocal
    from app.models.sms_message import SMSMessage

    db = SessionLocal()
    try:
        msg = db.query(SMSMessage).filter(SMSMessage.status == "sent").first()
        if msg is None:
            pytest.skip("no sent message to test against")
        external_id = msg.external_id
    finally:
        db.close()

    client.post("/webhooks/telnyx", json={"data": {
        "event_type": "message.failed",
        "payload": {
            "id": external_id,
            "to": [{"status": "delivery_failed"}],
            "errors": [{"title": "Blocked as spam", "detail": "temporary"}],
        },
    }})

    db = SessionLocal()
    try:
        refreshed = db.query(SMSMessage).filter(
            SMSMessage.external_id == external_id
        ).first()
        assert refreshed.status == "undelivered"
    finally:
        db.close()
