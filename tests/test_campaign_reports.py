"""Session 5f A4-A6: carrier cost, the per-campaign report, and the histories.

The white-label assertions here are the ones to read first. Criterion 8 asks for
proof over the *new* surfaces rather than the ones that existed before, because
every leak this project has found was assembled at runtime — an f-string, a URL
built from a provider name, a field copied out of an SDK payload — and a grep
structurally cannot see any of them. So these run the routes and scan what comes
back, including the CSV export, which is the one surface that leaves the
building.

Nothing here sends a message. `CostingProvider` is a stub that returns a
`SendResult`; `SMS_PROVIDER` stays `console` and no carrier object is built.
"""

import asyncio
from contextlib import contextmanager
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from app.core.config import settings
from app.core.database import engine
from app.main import app
from app.models.short_link import ShortLink
from app.models.sms_message import SMSMessage
from app.services import billing_service, cost_reconciliation, history_service
from app.services import link_service, report_service
from app.services.campaign_service import CampaignService

from tests import _link_setup as setup

PASSWORD = "devpassword123"
IPHONE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
             "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148")

# Every string a client-facing surface must never carry. The carrier names, and
# our own wholesale rate in the two spellings it would appear in.
FORBIDDEN = ("telnyx", "twilio", "bandwidth", "vonage",
             "wholesale", "carrier_cost", "cost_breakdown")


@pytest.fixture(scope="module")
def db():
    yield from setup.purged_db_fixture_body()


@pytest.fixture(autouse=True)
def rate_limits():
    yield from setup.rate_limit_fixture_body()


@pytest.fixture(scope="module")
def client():
    c = TestClient(app)
    login = c.post("/login", data={"username": "admin", "password": PASSWORD})
    assert login.status_code in (200, 302), (
        f"login failed with {login.status_code} — every scan below would be "
        f"running against a 401 body and passing by containing nothing")
    return c


def _send(db, *, phones, name, template=None, provider=None, with_link=True):
    """Create a campaign and run the real send loop against a stub carrier.

    `asyncio.run` rather than an async test, matching the rest of the suite:
    pytest-asyncio is not a dependency here and adding one is an escalation.
    """
    template = template or (
        f"Auctions4America: {link_service.LINK_TAG} Reply STOP to opt out."
        if with_link else setup.MESSAGE)
    source = f"{setup.CONTACT_SOURCE}-{name}"
    setup.seed_contacts(db, phones, source=source)

    service = CampaignService(db)
    service.provider = provider or setup.CostingProvider()
    campaign = service.create_campaign(
        name=f"{setup.NAME_PREFIX}{name}",
        message_template=template,
        audience=f"source:{source}",
        cross_category_override=True,
        link_target_url=setup.TARGET_URL if with_link else None,
    )
    asyncio.run(service.send_campaign(campaign.id))
    db.refresh(campaign)
    return campaign


# ─── A4: what the carrier actually charged ──────────────────────────────────

def test_the_carrier_cost_and_its_split_are_captured_per_message(db):
    with setup.short_link_domain():
        campaign = _send(db, phones=setup.take(3), name="cost")

    rows = db.query(SMSMessage).filter(SMSMessage.campaign_id == campaign.id).all()
    assert len(rows) == 3
    for row in rows:
        assert row.status == "sent"
        # Stored as the carrier reported them, not re-derived. A Float column
        # here would put a binary expansion between the carrier's figure and
        # ours before four thousand of them are added up.
        assert row.carrier_cost == "0.0043"
        assert row.carrier_cost_rate == "0.0035"
        assert row.carrier_cost_fee == "0.0008"
        assert row.carrier_cost_currency == "USD"


def test_the_reconciliation_puts_estimate_against_actual(db):
    with setup.short_link_domain():
        campaign = _send(db, phones=setup.take(4), name="reconcile")

    summary = cost_reconciliation.reconcile(db, campaign.id)
    assert summary["priced_messages"] == 4
    assert summary["coverage"] == 1.0
    assert summary["actual"] == Decimal("0.0172")            # 4 × 0.0043
    assert summary["rate_component"] == Decimal("0.0140")    # 4 × 0.0035
    assert summary["carrier_fee_component"] == Decimal("0.0032")
    assert summary["currencies"] == ["USD"]
    # The estimate is our reserve, not a measurement: 0.009 a segment against a
    # blended rate under half that. Proving the gap is exactly what A4 is for.
    assert summary["estimated"] > summary["actual"]
    assert summary["effective_rate_per_segment"] == Decimal("0.0043")


def test_an_unpriced_message_is_not_counted_as_free(db):
    """None and $0.00 are different facts, and reporting the first as the second
    would make an unpriced campaign look free — the direction nobody checks."""
    silent = setup.CostingProvider(amount=None, rate=None, fee=None)
    with setup.short_link_domain():
        campaign = _send(db, phones=setup.take(2), name="unpriced",
                               provider=silent)

    summary = cost_reconciliation.reconcile(db, campaign.id)
    assert summary["messages"] == 2
    assert summary["priced_messages"] == 0
    assert summary["coverage"] == 0.0
    assert summary["actual"] is None
    assert summary["effective_rate_per_segment"] is None


# ─── A5: the per-campaign report ────────────────────────────────────────────

def test_the_report_counts_what_the_campaign_did(db, client):
    with setup.short_link_domain():
        campaign = _send(db, phones=setup.take(3), name="report")
        # One buyer opens the link, twice; a scanner opens another.
        links = db.query(ShortLink).filter(
            ShortLink.campaign_id == campaign.id).order_by(ShortLink.id).all()
        for row in db.query(SMSMessage).filter(
                SMSMessage.campaign_id == campaign.id).all():
            row.sent_at = setup.iso_minutes_ago(30)
        db.commit()

        for _ in range(2):
            client.get(f"/{links[0].slug}", follow_redirects=False,
                       headers={"user-agent": IPHONE_UA})
        client.get(f"/{links[1].slug}", follow_redirects=False,
                   headers={"user-agent": "Mozilla/5.0 (compatible; bingbot/2.0)"})

        report = report_service.campaign_report(db, campaign.id)

    assert report["outcome"]["recipients"] == 3
    assert report["outcome"]["sent"] == 3
    assert report["outcome"]["failed"] == 0
    assert report["clicks"]["links"] == 3
    assert report["clicks"]["clicks"] == 2, "two taps by one person"
    assert report["clicks"]["clickers"] == 1, "one person"
    assert report["clicks"]["filtered_clicks"] == 1, "the scanner, kept and named"
    assert report["clicks"]["click_through_rate"] == pytest.approx(33.3)
    # His rate, net of the month's allowance — not our carrier cost.
    assert report["cost"]["price_per_segment"] == settings.BILLING_PRICE_PER_SEGMENT
    assert report["cost"]["segments"] == 3
    assert report["top_ups"] == []


def test_the_report_repeats_the_abort_reason_rather_than_inventing_one(db):
    """Decision 006 settled what a refusal says. A report that paraphrased it
    would be a third sentence about one fact."""
    with setup.short_link_domain():
        campaign = _send(db, phones=setup.take(1), name="aborted")
    campaign.status = "aborted"
    campaign.abort_reason = "Nothing was sent. The hold clears at 10:11am."
    db.commit()

    report = report_service.campaign_report(db, campaign.id)
    assert report["campaign"]["abort_reason"] == campaign.abort_reason


def test_a_campaign_inside_the_allowance_is_not_priced_at_the_flat_rate(db):
    """`marginal_cost()`'s argument, applied to a campaign after the fact.

    The plan includes 10,000 segments a month. Quoting `segments * rate` for a
    campaign the allowance swallowed would show him a bill he never received.
    """
    with setup.short_link_domain():
        campaign = _send(db, phones=setup.take(2), name="allowance")
    report = report_service.campaign_report(db, campaign.id)

    cycle_start, cycle_end, _, _ = billing_service.get_billing_cycle()
    _, used = billing_service.compute_usage(db, cycle_start, cycle_end)
    assert used < settings.BILLING_SEGMENTS_INCLUDED, "precondition for this test"
    assert report["cost"]["cost"] == 0.0
    assert report["cost"]["segments"] == 2


# ─── A6: history, paginated ─────────────────────────────────────────────────

def test_both_history_screens_paginate_in_a_bounded_number_of_queries(db, client):
    """The query count must not grow with the rows returned.

    A per-row lookup for click data or campaign names is the N+1 that leaves
    every other assertion here passing while the page becomes fifty round-trips
    on a 1vCPU box.
    """
    with setup.short_link_domain():
        for i in range(5):
            _send(db, phones=setup.take(2), name=f"page{i}")
        big = _send(db, phones=setup.take(8), name="paging")

    # Campaign history: two rows against six, same query count.
    with _counted_queries() as small:
        page = client.get("/api/reports/campaigns?page=1&per_page=2").json()
    with _counted_queries() as large:
        page6 = client.get("/api/reports/campaigns?page=1&per_page=6").json()
    assert len(page["campaigns"]) == 2 and len(page6["campaigns"]) == 6
    print(f"\ncampaign history: {small['n']} queries for 2 rows, "
          f"{large['n']} for 6")
    assert large["n"] == small["n"], large["statements"]
    assert large["n"] <= 8, large["statements"]

    # Message history: two rows against eight, same query count. The comparison
    # is the assertion that matters — an absolute bound alone passes happily on
    # a per-row lookup as long as the fixture is small, which is how an N+1
    # ships under a green suite.
    with _counted_queries() as small:
        rows = client.get(
            f"/api/reports/campaigns/{big.id}/messages?page=1&per_page=2").json()
    with _counted_queries() as large:
        rows8 = client.get(
            f"/api/reports/campaigns/{big.id}/messages?page=1&per_page=8").json()
    assert len(rows["messages"]) == 2 and len(rows8["messages"]) == 8
    print(f"message history: {small['n']} queries for 2 rows, {large['n']} for 8")
    assert large["n"] == small["n"], large["statements"]
    assert large["n"] <= 6, large["statements"]

    # And the per-contact screen, which joins campaign names on top.
    contact_id = rows["messages"][0]["contact_id"]
    with _counted_queries() as counter:
        client.get(f"/api/reports/contacts/{contact_id}/messages")
    print(f"contact history: {counter['n']} queries")
    assert counter["n"] <= 8, counter["statements"]


def test_contact_history_names_the_campaign_and_says_whether_they_clicked(db, client):
    with setup.short_link_domain():
        campaign = _send(db, phones=setup.take(1), name="who")
        message = db.query(SMSMessage).filter(
            SMSMessage.campaign_id == campaign.id).one()
        message.sent_at = setup.iso_minutes_ago(30)
        db.commit()
        link = db.query(ShortLink).filter(
            ShortLink.campaign_id == campaign.id).one()
        client.get(f"/{link.slug}", follow_redirects=False,
                   headers={"user-agent": IPHONE_UA})

        history = history_service.contact_history(db, message.contact_id)

    assert history["contact"]["phone"] == message.phone
    row = history["messages"][0]
    assert row["campaign_name"] == campaign.name
    assert row["clicks"] == 1
    assert row["last_clicked_at"]
    assert history_service.contact_history(db, 99_999_999) is None


# ─── Criterion 8: nothing on these surfaces is ours ─────────────────────────

def test_no_new_surface_leaks_the_carrier_or_our_cost(db, client):
    """Run the routes and scan what comes back — including the export.

    Every white-label leak this project has found was assembled at runtime, so a
    grep over the source proves nothing about any of them. The export is scanned
    too because it is the one artefact that leaves the building.
    """
    with setup.short_link_domain():
        campaign = _send(db, phones=setup.take(2), name="scan")
        # A carrier's own words on a row, which is where the wording arrives
        # from in production.
        row = db.query(SMSMessage).filter(
            SMSMessage.campaign_id == campaign.id).first()
        row.status = "undelivered"
        row.error_message = "TelnyxError: the destination was deemed invalid"
        db.commit()

        paths = [
            f"/api/reports/campaigns/{campaign.id}",
            f"/api/reports/campaigns/{campaign.id}/messages",
            f"/api/reports/campaigns/{campaign.id}/export",
            f"/api/reports/contacts/{row.contact_id}/messages",
            "/api/reports/campaigns",
            "/history",
            f"/history/{campaign.id}",
            f"/contacts/{row.contact_id}/history",
        ]
        for path in paths:
            response = client.get(path)
            assert response.status_code == 200, f"{path}: {response.status_code}"
            body = response.text.lower()
            for word in FORBIDDEN:
                assert word not in body, f"{path} leaks {word!r}"
            # The exact figure, in every spelling it would appear in.
            assert str(settings.WHOLESALE_COST_PER_SEGMENT) not in body, path
            assert "0.0043" not in body, f"{path} leaks the carrier's own price"

        # And the scrubbed wording did survive, so the scan above is not
        # passing because the field is simply absent.
        detail = client.get(f"/api/reports/campaigns/{campaign.id}/messages").json()
        errors = [m["error_message"] for m in detail["messages"] if m["error_message"]]
        assert errors and "SMS carrier" in errors[0], errors


def test_the_export_carries_the_report_and_the_recipients(db, client):
    with setup.short_link_domain():
        campaign = _send(db, phones=setup.take(2), name="export")
        response = client.get(f"/api/reports/campaigns/{campaign.id}/export")

    assert response.status_code == 200
    assert "text/csv" in response.headers["content-type"]
    body = response.text
    assert settings.BRAND_NAME in body
    assert "Messages sent,2" in body
    assert "Clicks filtered as automated,0" in body
    for phone in setup.POOL:
        if phone in body:
            break
    else:
        raise AssertionError("no recipient rows in the export")


# ─── Helpers ────────────────────────────────────────────────────────────────

@contextmanager
def _counted_queries():
    """Count SQL statements executed against the app's engine.

    The same helper `test_contacts_api.py` uses. Copied rather than imported
    because importing a fixture module for one context manager drags that
    module's own seeding into this one.
    """
    counter = {"n": 0, "statements": []}

    def before(conn, cursor, statement, parameters, context, executemany):
        counter["n"] += 1
        counter["statements"].append(statement.split("\n")[0][:80])

    event.listen(engine, "before_cursor_execute", before)
    try:
        yield counter
    finally:
        event.remove(engine, "before_cursor_execute", before)
