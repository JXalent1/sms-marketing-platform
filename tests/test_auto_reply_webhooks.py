"""Session I1, criteria 2-6: the guard as the carriers' webhooks reach it.

Incident 2026-10-09 to 10-12: +19547382462 looped with two auto-responders for
three days, ~114k outbound. These tests replay that traffic through the routes
the carrier actually posts to, and count what would have gone back out.
"""

import re

import pytest
from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.main import app
from app.models.auto_reply_log import AutoReplyLog
from app.models.blocked_number import BlockedNumber
from app.models.sms_message import SMSMessage
from app.services import auto_reply_guard
from app.sms import compliance
from app.sms.providers.console import ConsoleProvider
from tests._auto_reply_setup import (RESPONDER_TEXT, RESPONDERS,  # noqa: F401
                                     CountingProvider, isolated_db, routed_to,
                                     telnyx_inbound, use_provider)

SIMULATED = 1000
_TWIML_MESSAGE = re.compile(r"<Message>")


def _log_rows(factory, phones):
    db = factory()
    try:
        return db.query(AutoReplyLog).filter(AutoReplyLog.phone.in_(phones)).all()
    finally:
        db.close()


# ─── 2. delivery events never reach the guard or the provider ───────────────

DELIVERY_EVENTS = [
    ("message.sent", {"to": [{"status": "sent"}]}),
    ("message.delivered", {"to": [{"status": "delivered"}]}),
    ("message.failed", {"to": [{"status": "delivery_failed"}],
                        "errors": [{"code": "40300", "title": "Blocked",
                                    "detail": "not routable"}]}),
    ("message.finalized", {"to": [{"status": "delivery_failed"}],
                           "errors": [{"code": "21408", "title": "Region",
                                       "detail": "has not been enabled for the region"}]}),
]


@pytest.fixture
def no_send_path(monkeypatch):
    """Count every way a reply could leave: the route's provider, the console
    provider the box falls back to, and the guard that would have to approve it."""
    calls = {"provider": 0, "console": 0, "guard": 0}
    provider = use_provider(monkeypatch, CountingProvider())

    async def console_send(self, to, text):
        calls["console"] += 1

    real_claim = auto_reply_guard.claim

    def counting_claim(*a, **k):
        calls["guard"] += 1
        return real_claim(*a, **k)

    monkeypatch.setattr(ConsoleProvider, "send", console_send)
    monkeypatch.setattr(auto_reply_guard, "claim", counting_claim)
    yield calls, provider


def _seed_message(factory, external_id, phone):
    db = factory()
    try:
        db.add(SMSMessage(phone=phone, message="body", status="sent",
                          external_id=external_id, sent_at="2026-10-09T09:00:00"))
        db.commit()
    finally:
        db.close()


@pytest.mark.parametrize("event_type,extra", DELIVERY_EVENTS, ids=[e for e, _ in DELIVERY_EVENTS])
def test_telnyx_delivery_events_never_send(isolated_db, no_send_path, event_type, extra):
    calls, provider = no_send_path
    _seed_message(isolated_db, f"ext-{event_type}", "+15550005001")
    with routed_to(app, isolated_db):
        for _ in range(5):               # a carrier retrying the same event
            r = TestClient(app).post("/webhooks/telnyx", json={"data": {
                "event_type": event_type, "payload": {"id": f"ext-{event_type}", **extra}}})
            assert r.status_code == 200
    assert (len(provider.sent), calls["console"], calls["guard"]) == (0, 0, 0)
    assert _log_rows(isolated_db, ["+15550005001"]) == []


@pytest.mark.parametrize("status", ["sent", "delivered", "undelivered", "failed"])
def test_twilio_status_callback_never_sends(isolated_db, no_send_path, status):
    calls, provider = no_send_path
    _seed_message(isolated_db, f"SM-{status}", "+15550005002")
    with routed_to(app, isolated_db):
        for _ in range(5):
            r = TestClient(app).post("/webhooks/twilio/status", data={
                "MessageSid": f"SM-{status}", "MessageStatus": status,
                "ErrorCode": "30005" if status != "delivered" else ""})
            assert r.status_code == 200
            assert not _TWIML_MESSAGE.search(r.text)
    assert (len(provider.sent), calls["console"], calls["guard"]) == (0, 0, 0)


def test_the_no_send_fixture_does_count_a_send(isolated_db, no_send_path):
    """Known-answer check on the fixture above: an inbound message *does* reach
    the guard and the provider, so a 0 from it means something."""
    calls, provider = no_send_path
    with routed_to(app, isolated_db):
        TestClient(app).post("/webhooks/telnyx",
                             json=telnyx_inbound("+15550005003", "hello there"))
    assert (len(provider.sent), calls["guard"]) == (1, 1)


# ─── 3 & 4. the incident, replayed ──────────────────────────────────────────

def _replay_telnyx(client):
    for i in range(SIMULATED):
        client.post("/webhooks/telnyx", json=telnyx_inbound(RESPONDERS[i % 2], RESPONDER_TEXT))


def _replay_twilio(client):
    empty = 0
    replies = 0
    for i in range(SIMULATED):
        r = client.post("/webhooks/twilio/sms",
                        data={"From": RESPONDERS[i % 2], "Body": RESPONDER_TEXT})
        if _TWIML_MESSAGE.search(r.text):
            replies += 1
        elif r.text == '<?xml version="1.0" encoding="UTF-8"?><Response></Response>':
            empty += 1
    return replies, empty


def test_telnyx_replay_with_the_phrase_filter_sends_nothing(isolated_db, monkeypatch):
    provider = use_provider(monkeypatch, CountingProvider())
    with routed_to(app, isolated_db):
        _replay_telnyx(TestClient(app))
    rows = _log_rows(isolated_db, RESPONDERS)
    assert len(provider.sent) == 0
    assert len(rows) == SIMULATED
    assert {(r.decision, r.reason) for r in rows} == {("suppressed", "automated_sender")}


def test_telnyx_replay_without_the_phrase_filter_sends_one_per_number(isolated_db, monkeypatch):
    monkeypatch.setattr(compliance, "AUTOMATED_SENDER_PHRASES", ())
    assert not compliance.is_automated_sender(RESPONDER_TEXT)
    provider = use_provider(monkeypatch, CountingProvider())
    with routed_to(app, isolated_db):
        _replay_telnyx(TestClient(app))
    rows = _log_rows(isolated_db, RESPONDERS)
    assert len(provider.sent) <= 2
    assert sorted(to for to, _ in provider.sent) == sorted(RESPONDERS)
    assert len(rows) == SIMULATED
    assert sum(r.decision == "sent" for r in rows) == 2
    assert {r.reason for r in rows if r.decision == "suppressed"} == {"per_number_cap"}


def test_twilio_replay_with_the_phrase_filter_sends_nothing(isolated_db):
    with routed_to(app, isolated_db):
        replies, empty = _replay_twilio(TestClient(app))
    assert (replies, empty) == (0, SIMULATED)
    assert len(_log_rows(isolated_db, RESPONDERS)) == SIMULATED


def test_twilio_replay_without_the_phrase_filter_sends_one_per_number(isolated_db, monkeypatch):
    monkeypatch.setattr(compliance, "AUTOMATED_SENDER_PHRASES", ())
    with routed_to(app, isolated_db):
        replies, empty = _replay_twilio(TestClient(app))
    rows = _log_rows(isolated_db, RESPONDERS)
    assert replies <= 2 and (replies, empty) == (2, SIMULATED - 2)
    assert {r.phone for r in rows if r.decision == "sent"} == set(RESPONDERS)
    assert len(rows) == SIMULATED


# ─── 5 & 6. the paths a real person takes — nothing injected ────────────────
#
# The suite's own database, the application clock, the real get_db. Only the
# provider is replaced, because it is the thing being counted.

def test_a_first_time_question_gets_one_reply_and_a_second_within_a_day_none(monkeypatch):
    phone = "+15550006001"
    provider = use_provider(monkeypatch, CountingProvider())
    client = TestClient(app)
    client.post("/webhooks/telnyx", json=telnyx_inbound(phone, "hi, when is the next auction?"))
    assert len(provider.sent) == 1
    client.post("/webhooks/telnyx", json=telnyx_inbound(phone, "hi, when is the next auction?"))
    assert len(provider.sent) == 1
    assert [r.decision for r in _log_rows(SessionLocal, [phone])] == ["sent", "suppressed"]


def test_stop_twenty_times_blocks_the_number_and_confirms_once(monkeypatch):
    phone = "+15550006002"
    provider = use_provider(monkeypatch, CountingProvider())
    client = TestClient(app)
    for _ in range(20):
        client.post("/webhooks/telnyx", json=telnyx_inbound(phone, "STOP"))
    assert provider.sent == [(phone, compliance.stop_confirmation())]
    db = SessionLocal()
    try:
        blocked = db.query(BlockedNumber).filter(BlockedNumber.phone == phone).one()
        assert blocked.reason == "stop_keyword"
    finally:
        db.close()
    rows = _log_rows(SessionLocal, [phone])
    assert len(rows) == 20 and sum(r.decision == "sent" for r in rows) == 1


def test_the_opt_out_word_2_twenty_times_through_twilio_confirms_once():
    phone = "+15550006003"
    client = TestClient(app)
    bodies = [client.post("/webhooks/twilio/sms", data={"From": phone, "Body": "2"}).text
              for _ in range(20)]
    assert sum(bool(_TWIML_MESSAGE.search(b)) for b in bodies) == 1
    db = SessionLocal()
    try:
        assert db.query(BlockedNumber).filter(BlockedNumber.phone == phone).count() == 1
    finally:
        db.close()
