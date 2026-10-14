"""Session I1, criterion 1: one test per rule in app/services/auto_reply_guard.py.

Each test gets a database of its own (`isolated_db`) and a frozen clock, so the
rule it names is the only thing that can decide its outcome. Incident
2026-10-09: our number answered two auto-responders for three days.
"""

import logging
import threading
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.models.auto_reply_log import AutoReplyLog
from app.services import auto_reply_guard as guard
from app.services.blocklist_service import block_number, unblock_number
from app.services.monitoring_service import active_config_alerts
from app.sms import compliance
from app.sms.base import SendResult
from tests._auto_reply_setup import (RESPONDER_TEXT, T0, CountingProvider,  # noqa: F401
                                     isolated_db, routed_to, telnyx_inbound,
                                     use_provider)

HUMAN = "hi, when is the next auction?"


def _claim(db, phone, body=HUMAN, kind="other", at=T0):
    return guard.claim(db, phone, body, kind, "reply text", now=at)


def _rows(db, phone=None):
    q = db.query(AutoReplyLog)
    if phone:
        q = q.filter(AutoReplyLog.phone == phone)
    return q.order_by(AutoReplyLog.id).all()


# ─── per-number cap ─────────────────────────────────────────────────────────

def test_per_number_cap_allows_one_reply_per_window_across_every_kind(isolated_db):
    db = isolated_db()
    phone = "+15550001001"
    assert _claim(db, phone).allowed
    # Inside the window: refused, whatever kind of reply it would have been.
    for kind, body in (("other", HUMAN), ("help", "HELP"), ("start", "START")):
        d = _claim(db, phone, body, kind, at=T0 + timedelta(hours=23, minutes=59))
        assert not d.allowed and d.reason == guard.PER_NUMBER_CAP
    # Once the window has passed, one more.
    assert _claim(db, phone, at=T0 + timedelta(hours=24, seconds=1)).allowed
    # Another number is unaffected throughout.
    assert _claim(db, "+15550001002", at=T0 + timedelta(minutes=1)).allowed


def test_per_number_window_is_read_from_config(isolated_db, monkeypatch):
    monkeypatch.setattr(settings, "AUTO_REPLY_PER_NUMBER_HOURS", 2)
    db = isolated_db()
    phone = "+15550001003"
    assert _claim(db, phone).allowed
    assert not _claim(db, phone, at=T0 + timedelta(hours=1)).allowed
    assert _claim(db, phone, at=T0 + timedelta(hours=2, seconds=1)).allowed


def test_a_suppressed_reply_does_not_extend_the_window(isolated_db):
    """Only `sent` rows count. A responder texting every minute must not keep
    pushing its window forward with suppressions and so starve a real reply
    forever — nor, more to the point, be able to reset it."""
    db = isolated_db()
    phone = "+15550001004"
    assert _claim(db, phone).allowed
    assert not _claim(db, phone, at=T0 + timedelta(hours=23)).allowed
    assert _claim(db, phone, at=T0 + timedelta(hours=24, seconds=1)).allowed


# ─── automated-sender phrase filter ─────────────────────────────────────────

SPEC_PHRASES = ("do not monitor", "not monitored", "do not reply", "no-reply", "noreply",
                "automated message", "unable to receive", "this number does not accept",
                "cannot receive", "auto-reply", "autoreply")


def test_the_phrase_list_covers_at_least_the_specified_phrases():
    missing = [p for p in SPEC_PHRASES if p not in compliance.AUTOMATED_SENDER_PHRASES]
    assert not missing


@pytest.mark.parametrize("phrase", SPEC_PHRASES)
@pytest.mark.parametrize("spell", [str.lower, str.upper, str.title,
                                   lambda p: f"Hello. {p.capitalize()}, thanks!"])
def test_each_automated_phrase_gets_no_reply_and_is_logged(isolated_db, phrase, spell):
    db = isolated_db()
    phone = "+15550002001"
    body = spell(phrase)
    assert compliance.is_automated_sender(body)
    d = _claim(db, phone, body)
    assert not d.allowed and d.reason == guard.AUTOMATED_SENDER
    [row] = _rows(db, phone)
    assert (row.decision, row.reason, row.inbound_excerpt) == ("suppressed", "automated_sender", body)


def test_the_incident_text_is_an_automated_sender_and_a_human_is_not():
    assert compliance.is_automated_sender(RESPONDER_TEXT)
    assert not compliance.is_automated_sender(HUMAN)
    assert not compliance.is_automated_sender("")


def test_an_automated_sender_is_refused_before_the_per_number_window_is_spent(isolated_db):
    """The phrase rule must not claim a slot: a responder's message should not
    use up the one reply its number's human is owed today."""
    db = isolated_db()
    phone = "+15550002002"
    assert not _claim(db, phone, RESPONDER_TEXT).allowed
    assert _claim(db, phone, at=T0 + timedelta(minutes=5)).allowed


# ─── blocked numbers ────────────────────────────────────────────────────────

def test_a_blocked_number_gets_no_reply_except_its_first_stop_confirmation(isolated_db):
    db = isolated_db()
    phone = "+15550003001"
    block_number(db, phone, reason="stop_keyword", source="webhook")

    # The confirmation the opt-out is owed.
    assert _claim(db, phone, "STOP", "stop").allowed
    # Nothing else, even once the per-number window has long passed.
    later = T0 + timedelta(days=3)
    for kind, body in (("stop", "STOP"), ("stop", "2"), ("help", "HELP"), ("other", HUMAN)):
        d = _claim(db, phone, body, kind, at=later)
        assert not d.allowed and d.reason == guard.BLOCKED_NUMBER, (kind, body)


def test_a_number_blocked_for_another_reason_still_gets_one_stop_confirmation(isolated_db):
    db = isolated_db()
    phone = "+15550003002"
    block_number(db, phone, reason="delivery_failure", source="telnyx")
    assert not _claim(db, phone).allowed
    assert _claim(db, phone, "STOP", "stop", at=T0 + timedelta(days=2)).allowed
    assert not _claim(db, phone, "STOP", "stop", at=T0 + timedelta(days=4)).allowed


def test_an_opt_out_after_resubscribing_is_confirmed_again(isolated_db):
    db = isolated_db()
    phone = "+15550003003"
    block_number(db, phone, reason="stop_keyword", source="webhook")
    assert _claim(db, phone, "STOP", "stop").allowed
    unblock_number(db, phone)
    assert _claim(db, phone, "START", "start", at=T0 + timedelta(days=2)).allowed
    block_number(db, phone, reason="stop_keyword", source="webhook")
    assert _claim(db, phone, "STOP", "stop", at=T0 + timedelta(days=4)).allowed


# ─── circuit breaker ────────────────────────────────────────────────────────

def test_circuit_breaker_trips_at_the_hourly_cap_and_recovers_after_the_hour(
        isolated_db, caplog):
    db = isolated_db()
    cap = settings.AUTO_REPLY_HOURLY_CAP
    assert cap == 50
    for i in range(cap):
        assert _claim(db, f"+1555100{i:04d}", at=T0 + timedelta(seconds=i)).allowed

    with caplog.at_level(logging.ERROR, logger="auto_reply_guard"):
        d = _claim(db, "+15551009999", at=T0 + timedelta(minutes=30))
    assert not d.allowed and d.reason == guard.CIRCUIT_BREAKER
    assert any("CIRCUIT BREAKER" in r.message for r in caplog.records
               if r.levelno == logging.ERROR)
    alerts = active_config_alerts(db, now=T0 + timedelta(minutes=31))
    assert [a["key"] for a in alerts] == [compliance.AUTO_REPLY_BREAKER]
    assert str(cap) not in alerts[0]["detail"], "/health is unauthenticated: no figures"

    # Still tripped one second before the oldest of the fifty leaves the hour...
    assert not _claim(db, "+15551009998", at=T0 + timedelta(minutes=59, seconds=59)).allowed
    # ...and clear on its own once every one of them has.
    assert _claim(db, "+15551009997", at=T0 + timedelta(hours=1, seconds=cap)).allowed


def test_circuit_breaker_cap_is_read_from_config(isolated_db, monkeypatch):
    monkeypatch.setattr(settings, "AUTO_REPLY_HOURLY_CAP", 3)
    db = isolated_db()
    for i in range(3):
        assert _claim(db, f"+1555200{i:04d}").allowed
    assert _claim(db, "+15552009999").reason == guard.CIRCUIT_BREAKER


# ─── claim before send ──────────────────────────────────────────────────────

@pytest.mark.parametrize("failure", ["raises", "reports_failure"])
def test_the_claim_is_committed_before_the_send_and_survives_its_failure(
        isolated_db, monkeypatch, failure):
    phone = "+15550004001"
    seen_during_send = []

    def look(to, text):
        # A different session: only a committed row is visible from here.
        other = isolated_db()
        try:
            seen_during_send.extend((r.decision, r.reason) for r in _rows(other, phone))
        finally:
            other.close()

    if failure == "raises":
        provider = CountingProvider(raises=RuntimeError("carrier down"), on_send=look)
    else:
        provider = CountingProvider(result=SendResult(success=False, error="TelnyxError 40001"),
                                    on_send=look)
    use_provider(monkeypatch, provider)

    with routed_to(app, isolated_db):
        TestClient(app).post("/webhooks/telnyx", json=telnyx_inbound(phone, HUMAN))

    assert len(provider.sent) == 1
    assert seen_during_send == [("sent", None)], "the claim must be durable before the send"
    db = isolated_db()
    [row] = _rows(db, phone)
    assert row.decision == "sent"
    assert row.reason.startswith("send_failed")
    assert "telnyx" not in row.reason.lower()
    # And the failed attempt still counts: the next inbound is refused.
    with routed_to(app, isolated_db):
        TestClient(app).post("/webhooks/telnyx", json=telnyx_inbound(phone, HUMAN))
    assert len(provider.sent) == 1


def test_concurrent_claims_for_one_number_let_exactly_one_through(isolated_db):
    phone = "+15550004002"
    results, barrier = [], threading.Barrier(8)

    def worker():
        db = isolated_db()
        try:
            barrier.wait()
            results.append(guard.claim(db, phone, HUMAN, "other", "reply").allowed)
        finally:
            db.close()

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(results) == [False] * 7 + [True]
    db = isolated_db()
    assert [r.decision for r in _rows(db, phone)].count("sent") == 1
    assert len(_rows(db, phone)) == 8


# ─── sender validity and region (session I1 review, F2) ─────────────────────
#
# The reviewer posted forged inbounds from +882 (a premium satellite range) and
# was answered fifty times an hour. An inbound is a request we did not
# originate; it must not reach a destination a campaign would refuse.

@pytest.mark.parametrize("sender,reason", [
    ("+882160000001", "non_us_sender"),          # premium international range
    ("+447700900123", "non_us_sender"),
    ("001 904 694 3673", "non_us_sender"),       # a 00-prefixed spelling of a responder
    ("+14165550123", "non_us_sender"),           # Toronto: NANP, outside the US
    ("12345", "invalid_sender"),                 # short code
    ("AUCTIONS", "invalid_sender"),              # alphanumeric sender ID
    ("", "no_sender"),
])
def test_senders_a_campaign_would_not_text_get_no_reply(isolated_db, sender, reason):
    db = isolated_db()
    d = _claim(db, sender)
    assert not d.allowed and d.reason == reason
    [row] = _rows(db)
    assert (row.decision, row.reason) == ("suppressed", reason)


def test_the_region_rule_follows_the_campaign_setting(isolated_db, monkeypatch):
    monkeypatch.setattr(settings, "SKIP_NON_US_NUMBERS", False)
    db = isolated_db()
    assert _claim(db, "+447700900124").allowed
    assert not _claim(db, "12345").allowed       # a short code is never a person


# ─── config bounds (session I1 review, F4) ──────────────────────────────────

def test_a_per_number_window_below_an_hour_refuses_to_boot():
    """0 hours makes the window `created_at > now`, which matches nothing — the
    reviewer measured 30 replies to 30 inbounds from one number."""
    from pydantic import ValidationError
    from app.core.config import Settings

    for hours in (0, -24):
        with pytest.raises(ValidationError):
            Settings(AUTO_REPLY_PER_NUMBER_HOURS=hours)
    with pytest.raises(ValidationError):
        Settings(AUTO_REPLY_HOURLY_CAP=-1)
    assert Settings(AUTO_REPLY_PER_NUMBER_HOURS=1).AUTO_REPLY_PER_NUMBER_HOURS == 1


def test_an_hourly_cap_of_zero_means_no_auto_replies(isolated_db, monkeypatch):
    monkeypatch.setattr(settings, "AUTO_REPLY_HOURLY_CAP", 0)
    assert _claim(isolated_db(), "+15552100001").reason == guard.CIRCUIT_BREAKER
