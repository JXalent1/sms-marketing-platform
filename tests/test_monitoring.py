"""Module 5b — the two unattended jobs.

What these assert, in order of how much they matter:

1. A low balance pages someone, and does it **without sending a message**. The
   alert is the guard against the failure that cost the reference client 19,375
   messages; an alert that quietly went out over the empty carrier account would
   be the same bug wearing a hat.
2. The digest groups. A list of 4,623 phone numbers is not a digest.
3. Neither job names the carrier in anything a client could read.

`agent/notify.sh` is stubbed here rather than executed. Running it for real
would either print to stderr (no credential configured) or, on a machine that
happens to have one exported, text a person. The stub proves the message we
*would* send, which is the thing under test.
"""

import asyncio
from datetime import datetime, timedelta

import pytest

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.sms_message import SMSMessage
from app.services import monitoring_service


@pytest.fixture
def captured(monkeypatch, tmp_path):
    """Capture notifications instead of running the pager; isolate the state file."""
    sent = []
    monkeypatch.setattr(monitoring_service, "notify",
                        lambda message: sent.append(message) or True)
    monkeypatch.setattr(monitoring_service, "STATE_FILE",
                        str(tmp_path / "monitoring_state.json"))
    return sent


class _Provider:
    """Stands in for the carrier. `send` exists only so the test can prove that
    nothing calls it."""

    name = "console"

    def __init__(self, balance):
        self.balance = balance

    async def get_balance(self):
        return self.balance

    async def send(self, *args, **kwargs):
        raise AssertionError("monitoring must never send a message")


# ─── Low balance ────────────────────────────────────────────────────────────

def test_a_low_balance_alerts_and_sends_no_message(captured, monkeypatch):
    """The simulated dip: below threshold, paged, nothing sent."""
    below = settings.BALANCE_ALERT_THRESHOLD / 2
    monkeypatch.setattr(monitoring_service, "get_provider", lambda: _Provider(below))

    result = asyncio.run(monitoring_service.check_low_balance())

    assert result["low"] is True and result["alerted"] is True
    assert len(captured) == 1
    assert "low" in captured[0].lower()


def test_a_healthy_balance_says_nothing(captured, monkeypatch):
    above = settings.BALANCE_ALERT_THRESHOLD + 100
    monkeypatch.setattr(monitoring_service, "get_provider", lambda: _Provider(above))

    result = asyncio.run(monitoring_service.check_low_balance())

    assert result["low"] is False and result["alerted"] is False
    assert captured == []


def test_a_provider_with_no_balance_is_not_an_error(captured, monkeypatch):
    """Dry run reports no balance. That is normal, not a reason to page anyone."""
    monkeypatch.setattr(monitoring_service, "get_provider", lambda: _Provider(None))

    assert asyncio.run(monitoring_service.check_low_balance())["checked"] is False
    assert captured == []


def test_the_alert_does_not_repeat_every_hour(captured, monkeypatch):
    """Hourly job, 12-hour re-alert window: one page, not twelve overnight."""
    below = settings.BALANCE_ALERT_THRESHOLD / 2
    monkeypatch.setattr(monitoring_service, "get_provider", lambda: _Provider(below))
    now = datetime.now()

    asyncio.run(monitoring_service.check_low_balance(now))
    again = asyncio.run(monitoring_service.check_low_balance(now + timedelta(hours=1)))
    later = asyncio.run(monitoring_service.check_low_balance(
        now + timedelta(hours=monitoring_service.REALERT_HOURS + 1)))

    assert again["alerted"] is False and again["reason"] == "already alerted"
    assert later["alerted"] is True
    assert len(captured) == 2


def test_recovering_re_arms_the_alert(captured, monkeypatch):
    """Topped up then drained again pages a second time, inside the window."""
    now = datetime.now()
    low, high = settings.BALANCE_ALERT_THRESHOLD / 2, settings.BALANCE_ALERT_THRESHOLD + 1

    monkeypatch.setattr(monitoring_service, "get_provider", lambda: _Provider(low))
    asyncio.run(monitoring_service.check_low_balance(now))
    monkeypatch.setattr(monitoring_service, "get_provider", lambda: _Provider(high))
    asyncio.run(monitoring_service.check_low_balance(now + timedelta(hours=1)))
    monkeypatch.setattr(monitoring_service, "get_provider", lambda: _Provider(low))
    asyncio.run(monitoring_service.check_low_balance(now + timedelta(hours=2)))

    assert len(captured) == 2


def test_the_alert_never_quotes_our_wholesale_rate(captured, monkeypatch):
    """It is denominated in segments. The dollar figure is our cost, not his."""
    monkeypatch.setattr(monitoring_service, "get_provider",
                        lambda: _Provider(settings.BALANCE_ALERT_THRESHOLD / 2))
    asyncio.run(monitoring_service.check_low_balance())

    body = captured[0].lower()
    assert str(settings.WHOLESALE_COST_PER_SEGMENT) not in body
    assert "telnyx" not in body and "twilio" not in body


# ─── Failure digest ─────────────────────────────────────────────────────────

DIGEST_PHONE_PREFIX = "+1555555050"


@pytest.fixture
def yesterdays_failures():
    """Seed yesterday with one systemic failure and a scatter of dead numbers."""
    yesterday = datetime.now() - timedelta(days=1)
    stamp = yesterday.replace(hour=13, minute=0, second=0, microsecond=0).isoformat()

    db = SessionLocal()
    try:
        db.query(SMSMessage).filter(
            SMSMessage.phone.like(f"{DIGEST_PHONE_PREFIX}%")).delete(
                synchronize_session=False)
        db.commit()

        rows = ([("Account inactive: out of funds", 4)] +
                [("Not routable: landline", 2)] +
                [("Telnyx rejected the message, see https://developers.telnyx.com", 1)])
        n = 0
        for reason, count in rows:
            for _ in range(count):
                db.add(SMSMessage(phone=f"{DIGEST_PHONE_PREFIX}{n % 10}",
                                  message="x", status="failed",
                                  error_message=reason, sent_at=stamp))
                n += 1
        db.commit()
        yield yesterday
        db.query(SMSMessage).filter(
            SMSMessage.phone.like(f"{DIGEST_PHONE_PREFIX}%")).delete(
                synchronize_session=False)
        db.commit()
    finally:
        db.close()


def test_the_digest_groups_by_reason_rather_than_listing_numbers(yesterdays_failures):
    digest = monitoring_service.failure_digest(yesterdays_failures)

    assert digest["total"] == 7
    top = digest["reasons"][0]
    assert top["reason"] == "Account inactive: out of funds"
    assert top["count"] == 4
    assert len(digest["reasons"]) == 3
    # No phone number reaches the digest. That is the difference between a
    # finding and a wall of text.
    assert DIGEST_PHONE_PREFIX not in str(digest)


def test_the_digest_scrubs_the_carrier_out_of_error_text(yesterdays_failures):
    """Carrier error strings arrive full of the carrier's name and doc links."""
    body = str(monitoring_service.failure_digest(yesterdays_failures))

    assert "telnyx" not in body.lower()
    assert "developers." not in body.lower()


def test_a_quiet_day_pages_nobody(captured):
    """No "0 failures" mail every morning — that is how a digest gets ignored."""
    # A date far enough back that no fixture in this suite seeded it.
    result = monitoring_service.send_failure_digest(datetime.now() - timedelta(days=400))

    assert result["total"] == 0 and result["alerted"] is False
    assert captured == []


def test_a_bad_day_pages_with_the_grouped_counts(captured, yesterdays_failures):
    result = monitoring_service.send_failure_digest(yesterdays_failures)

    assert result["alerted"] is True
    assert "4 x Account inactive: out of funds" in captured[0]
    assert "telnyx" not in captured[0].lower()


# ─── Registration ───────────────────────────────────────────────────────────

def test_both_jobs_register_on_startup():
    """The lifespan wires them; a job that fails to register looks like a quiet
    night, so assert on the ids rather than on the log."""
    from fastapi.testclient import TestClient

    from app.main import app, scheduler

    with TestClient(app):
        ids = {job.id for job in scheduler.get_jobs()}

    assert {"low_balance_alert", "daily_failure_digest"} <= ids
    assert "scheduled_campaigns" in ids
