"""Billing arithmetic for Auctions4America's plan.

The plan: no monthly fee, 10,000 segments included per cycle, $0.015 for every
segment beyond that. It is configuration, so most tests here pin the three
settings explicitly and check the arithmetic; one test checks that what ships in
config.py actually *is* the agreed plan, because arithmetic that is right about
the wrong numbers still produces a wrong invoice.
"""

import pytest

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.sms_message import SMSMessage, BILLABLE_STATUSES
from app.services import billing_service


@pytest.fixture
def a4a_plan(monkeypatch):
    """The agreed commercial terms, pinned so a stray .env cannot move them."""
    monkeypatch.setattr(settings, "BILLING_MONTHLY_FEE", 0.0)
    monkeypatch.setattr(settings, "BILLING_SEGMENTS_INCLUDED", 10000)
    monkeypatch.setattr(settings, "BILLING_PRICE_PER_SEGMENT", 0.015)


# ─── The two numbers from the spec ──────────────────────────────────────────

def test_32940_segments_costs_344_10(a4a_plan):
    """32,940 - 10,000 included = 22,940 billable x $0.015 = $344.10."""
    segments = 32940
    assert billing_service.billable_segments(segments) == 22940
    assert billing_service.to_money(billing_service.cost_for_segments(segments)) == 344.10


def test_8000_segments_costs_nothing(a4a_plan):
    """Under the allowance there is nothing to bill, and no monthly fee."""
    segments = 8000
    assert billing_service.billable_segments(segments) == 0
    assert billing_service.to_money(billing_service.cost_for_segments(segments)) == 0.00


# ─── Edges of the allowance ─────────────────────────────────────────────────

def test_exactly_the_allowance_is_free(a4a_plan):
    """10,000 is the last free segment, not the first billable one."""
    assert billing_service.billable_segments(10000) == 0
    assert billing_service.to_money(billing_service.cost_for_segments(10000)) == 0.00


def test_one_segment_over_costs_the_rate(a4a_plan):
    assert billing_service.billable_segments(10001) == 1
    assert billing_service.to_money(billing_service.cost_for_segments(10001)) == 0.02


def test_no_usage_is_free(a4a_plan):
    """No monthly fee means an idle month bills zero, not a minimum."""
    assert billing_service.to_money(billing_service.cost_for_segments(0)) == 0.00


def test_monthly_fee_is_added_when_configured(monkeypatch):
    """The fee is config, not a constant — a future client may well have one."""
    monkeypatch.setattr(settings, "BILLING_MONTHLY_FEE", 250.0)
    monkeypatch.setattr(settings, "BILLING_SEGMENTS_INCLUDED", 10000)
    monkeypatch.setattr(settings, "BILLING_PRICE_PER_SEGMENT", 0.015)
    assert billing_service.to_money(billing_service.cost_for_segments(11000)) == 265.00


# ─── Rounding ───────────────────────────────────────────────────────────────

def test_rounding_is_half_up_not_bankers():
    """round(0.125, 2) is 0.12 in Python. An invoice says 0.13."""
    assert billing_service.to_money(0.125) == 0.13
    assert billing_service.to_money(0.135) == 0.14
    assert billing_service.to_money(2.005) == 2.01


def test_rounding_happens_once_at_the_end(a4a_plan):
    """Accumulate in full precision, round for display.

    10,003 segments is 3 x $0.015 = $0.045. Rounding each segment to cents
    first gives 3 x $0.02 = $0.06 — a 33% overcharge on this cycle, and the
    error grows with volume.
    """
    assert billing_service.to_money(billing_service.cost_for_segments(10003)) == 0.05


# ─── What counts as a billable segment ──────────────────────────────────────

def test_only_sent_and_delivered_are_billable():
    """Fixed by contract, not by preference. blocked/skipped/failed/undelivered
    messages either never reached the carrier or were never delivered, and
    billing for them is billing for nothing."""
    assert BILLABLE_STATUSES == ("sent", "delivered")


def test_compute_usage_counts_only_billable_statuses():
    """Same window, seven messages, one segment each — only two are billable."""
    # A closed window well in the past, so this cannot disturb the current
    # cycle's totals the smoke test asserts against. The rows are deleted again
    # afterwards: the suite is one shared story, and a stray 'sent' message with
    # no external_id is exactly the kind of debris that breaks a later test for
    # reasons that have nothing to do with it.
    start, end = "2020-03-01", "2020-03-31"
    db = SessionLocal()
    rows = []
    try:
        for status in ("sent", "delivered", "pending", "failed",
                       "blocked", "skipped", "undelivered"):
            row = SMSMessage(phone="+15555550199", message="x", status=status,
                             segments=1, sent_at=f"{start}T12:00:00")
            db.add(row)
            rows.append(row)
        db.commit()

        from datetime import date
        count, segments = billing_service.compute_usage(
            db, date.fromisoformat(start), date.fromisoformat(end))
    finally:
        for row in rows:
            db.delete(row)
        db.commit()
        db.close()

    assert count == 2, "only 'sent' and 'delivered' should be counted"
    assert segments == 2


def test_pricing_table_exposes_the_plan(a4a_plan):
    """The UI renders this rather than hardcoding rates, so the page can never
    drift from what the code charges."""
    plan = billing_service.pricing_table()
    assert plan["monthly_fee"] == 0.00
    assert plan["included_segments"] == 10000
    assert plan["price_per_segment"] == 0.015
    assert "tiers" not in plan, "tiered overage is not this client's plan"


def test_shipped_config_matches_the_agreed_terms():
    """No monthly fee, 10,000 included, $0.015 after — from A4A's agreement.

    Deliberately asserted against the shipped configuration and not against a
    fixture: this is the check that fails if someone edits the defaults or the
    deployment's .env drifts from the contract.
    """
    assert settings.BILLING_MONTHLY_FEE == 0
    assert settings.BILLING_SEGMENTS_INCLUDED == 10000
    assert settings.BILLING_PRICE_PER_SEGMENT == 0.015
