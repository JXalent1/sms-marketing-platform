"""Module 4 — the pre-flight checklist and the money it quotes.

Split out of `test_campaign_guardrails.py` when the two together crossed the
500-line rule; the fixtures they share live in `_guardrail_setup.py`, and the
two rules both files keep — leave no rows behind, leave no rate-limit debt — are
documented at the top of that one.

Nothing here creates a campaign, so this module spends none of the 5/minute
budget: `/preflight` and `/preview` are read-only and unlimited.
"""

import asyncio
import os
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.database import SessionLocal
from app.main import app
from app.models.campaign import Campaign
from app.services import billing_service, preflight_service
from app.services.campaign_service import CampaignService

from tests import _guardrail_setup as setup

PASSWORD = os.environ["ADMIN_PASSWORD"]

EXPECTED_CHECKS = {"capacity", "opt_out_language", "brand_identified",
                   "segment_count", "recent_overlap", "link_shortener",
                   "category_match"}


@pytest.fixture(scope="module")
def seeded():
    yield from setup.seeded_fixture_body()


@pytest.fixture(scope="module")
def client(seeded):
    c = TestClient(app)
    login = c.post("/login", data={"username": "admin", "password": PASSWORD})
    assert login.status_code in (200, 302), (
        f"login failed with {login.status_code} — every assertion below would "
        f"have run against a 401 body"
    )
    return c


@pytest.fixture(scope="module")
def categories(client):
    return {c["slug"]: c for c in client.get("/api/categories").json()["categories"]}


def _preflight(client, seeded, body, category_id=None):
    return client.post("/api/campaigns/preflight", json={
        "message_template": body,
        "audience": seeded["audience"],
        "category_id": category_id or seeded["category_id"],
    }).json()


# ─── The checklist ──────────────────────────────────────────────────────────

def test_preflight_returns_every_check_with_a_status_and_a_reason(client, seeded):
    report = _preflight(
        client, seeded,
        f"{settings.BRAND_NAME}: fryer sale Thursday. Reply STOP to opt out.")

    assert {c["key"] for c in report["checks"]} == EXPECTED_CHECKS
    for check in report["checks"]:
        assert check["status"] in ("pass", "warn", "fail"), check
        assert check["reason"], f"{check['key']} has no reason"
        assert check["label"], f"{check['key']} has no label"

    assert report["counts"]["recipients"] == 2
    assert report["counts"]["suppressed"] == 1
    assert "opted_out" in report["counts"]


def test_preflight_fails_a_message_with_no_stop_and_no_brand(client, seeded):
    report = _preflight(client, seeded, "Big sale on Thursday, come early.")
    by_key = {c["key"]: c for c in report["checks"]}

    assert by_key["opt_out_language"]["status"] == "fail"
    assert "STOP" in by_key["opt_out_language"]["reason"]
    assert by_key["brand_identified"]["status"] == "fail"
    assert settings.BRAND_NAME in by_key["brand_identified"]["reason"]
    assert report["ok"] is False


def test_preflight_warns_on_a_shortener_and_a_long_message(client, seeded):
    long_body = (f"{settings.BRAND_NAME}: restaurant auction Thursday. " * 20
                 + "https://bit.ly/a4a Reply STOP to opt out.")
    by_key = {c["key"]: c for c in _preflight(client, seeded, long_body)["checks"]}

    assert by_key["link_shortener"]["status"] == "warn"
    assert "bit.ly" in by_key["link_shortener"]["reason"]
    assert by_key["segment_count"]["status"] == "warn", by_key["segment_count"]
    assert str(settings.PREFLIGHT_SEGMENT_CEILING) in by_key["segment_count"]["reason"]


def test_preflight_warns_when_someone_was_texted_recently(client, seeded):
    report = _preflight(
        client, seeded,
        f"{settings.BRAND_NAME}: sale Thursday. Reply STOP to opt out.")
    overlap = next(c for c in report["checks"] if c["key"] == "recent_overlap")

    assert overlap["status"] == "warn"
    assert overlap["suppressed_count"] == 1
    assert "1" in overlap["reason"]


# ─── The check that earns its keep ──────────────────────────────────────────

def test_category_mismatch_warns_and_names_both_categories(client, seeded, categories):
    """The copy-paste mistake: last night's equipment text, tonight's food list."""
    report = _preflight(
        client, seeded,
        f"{settings.BRAND_NAME}: drill press and lathe going under the hammer "
        f"Thursday. Reply STOP to opt out.",
        category_id=categories["food_service"]["id"])
    match = next(c for c in report["checks"] if c["key"] == "category_match")

    assert match["status"] == "warn", match
    assert categories["food_service"]["label"] in match["reason"]
    assert categories["equipment"]["label"] in match["reason"]
    assert "drill press" in match["reason"]
    assert categories["equipment"]["label"] in match["foreign_categories"]


def test_category_match_passes_on_its_own_vocabulary(client, seeded, categories):
    report = _preflight(
        client, seeded,
        f"{settings.BRAND_NAME}: fryer, griddle and walk-in cooler Thursday. "
        f"Reply STOP to opt out.",
        category_id=categories["food_service"]["id"])
    match = next(c for c in report["checks"] if c["key"] == "category_match")
    assert match["status"] == "pass", match


def test_keyword_matching_respects_word_boundaries():
    """"range" must not fire on "arrangements"."""
    assert preflight_service._keyword_hits("flexible arrangements available") == []
    assert ("food_service", "range") in preflight_service._keyword_hits(
        "six-burner range included")


# ─── Capacity: restated, not re-decided ─────────────────────────────────────

def test_preflight_capacity_row_matches_the_send_paths_own_verdict():
    """The endpoint re-states the send path's check; it does not re-decide it."""
    class _BrokeProvider:
        name = "console"

        async def get_balance(self):
            return 0.01

    db = SessionLocal()
    try:
        service = CampaignService(db)
        service.provider = _BrokeProvider()

        assessment = asyncio.run(service.capacity_assessment(27_000, 250.0))
        # Not persisted: pre-flight only reads these, and a stray campaign would
        # be debris in a suite that shares one database.
        campaign = Campaign(id=0, estimated_cost=250.0, estimated_segments=27_000)
        ok, detail = asyncio.run(service.preflight(campaign))

        assert assessment["ok"] is False and ok is False
        assert assessment["detail"] == detail

        row = preflight_service.check_capacity(assessment)
        assert row["status"] == "fail"
        assert row["reason"] == detail
    finally:
        db.close()


def test_preflight_response_never_quotes_our_wholesale_rate(client, seeded):
    """The capacity assessment carries a balance and our rate. Neither ships."""
    body = client.post("/api/campaigns/preflight", json={
        "message_template": f"{settings.BRAND_NAME}: sale. Reply STOP to opt out.",
        "audience": seeded["audience"],
        "category_id": seeded["category_id"],
    }).text
    assert str(settings.WHOLESALE_COST_PER_SEGMENT) not in body
    assert "balance" not in body.lower()
    assert "wholesale" not in body.lower()


# ─── Cost, at his rate ──────────────────────────────────────────────────────

def test_emoji_changes_segments_and_the_estimate_at_the_client_rate(client, seeded):
    """One emoji: fewer characters per segment, more segments, a bigger bill."""
    plain_body = (f"{settings.BRAND_NAME}: restaurant equipment auction Thursday at "
                  f"9am, preview from 8. Reply STOP to opt out.")
    emoji_body = "\U0001F525 " + plain_body

    def preview(body):
        return client.post("/api/campaigns/preview", json={
            "message_template": body, "audience": seeded["audience"],
        }).json()

    plain, emoji = preview(plain_body), preview(emoji_body)

    assert plain["encoding"] == "GSM-7" and emoji["encoding"] == "UCS-2"
    assert emoji["segments"] > plain["segments"], "UCS-2 must cost more segments"
    assert emoji["total_segments"] > plain["total_segments"]

    # The dollar figure is his, not ours: BILLING_PRICE_PER_SEGMENT, net of the
    # month's included allowance. That is the number on his invoice, and it is
    # roughly 40% above the wholesale figure the capacity check runs on.
    assert emoji["price_per_segment"] == settings.BILLING_PRICE_PER_SEGMENT
    assert emoji["price_per_segment"] != settings.WHOLESALE_COST_PER_SEGMENT
    assert emoji["estimated_cost"] >= emoji["estimated_cost_if_gsm7"]
    assert emoji["estimated_cost_if_gsm7"] == plain["estimated_cost"]


def test_the_estimate_prices_segments_at_the_client_rate_beyond_the_allowance():
    """Priced against a cycle that has already used its included segments.

    Inside the allowance the honest estimate is $0.00, so a test that only
    looked at a fresh cycle would pass on a function that returned nothing at
    all. This one asks what the segments after the 10,000th cost.
    """
    included = settings.BILLING_SEGMENTS_INCLUDED
    rate = Decimal(str(settings.BILLING_PRICE_PER_SEGMENT))

    before = billing_service.cost_for_segments(included)
    after = billing_service.cost_for_segments(included + 1_000)
    assert billing_service.to_money(after - before) == float(1_000 * rate)

    wholesale = float(1_000 * Decimal(str(settings.WHOLESALE_COST_PER_SEGMENT)))
    assert billing_service.to_money(after - before) != wholesale


def test_marginal_cost_is_free_inside_the_allowance():
    """The first campaign of the month usually costs nothing, and says so."""
    db = SessionLocal()
    try:
        cycle_start, cycle_end, _, _ = billing_service.get_billing_cycle()
        _, used = billing_service.compute_usage(db, cycle_start, cycle_end)
        if used + 10 >= settings.BILLING_SEGMENTS_INCLUDED:
            pytest.skip("this cycle has already crossed the allowance")
        assert preflight_service.marginal_cost(db, 10) == 0.0
    finally:
        db.close()
