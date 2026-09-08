"""Session B1: the meter, the tier check, the guard, the cycle and the webhook.

Nothing here reaches Stripe. `tests/_stripe_fixtures.replaced_api()` installs a
fake for every block, `conftest.py` blanks the four Stripe settings for the whole
suite, and `agent/accept-B1.sh` check 1 runs this module with `socket.connect`
raising so that "no network" is proved rather than promised.

The A1 tests — the meter receives the RAW count, because `billable_segments()`
subtracts the allowance and so does the Stripe tier — lived here until session
B1b replaced metering at send time with a scheduled, ledgered pass. They are in
`tests/test_metering_pass.py` now, asserted against that pass. What stays here
is the tier check, the cycle, the ownership guard, the webhook, the checkout
shape and the back-bill tool's arithmetic.
"""

import json
import os
from datetime import date, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.database import SessionLocal
from app.main import app
from app.models.app_setting import get_setting
from app.models.sms_message import SMSMessage
from app.services import (billing_service, stripe_billing, stripe_reconcile,
                          stripe_tiers)
from tests import _stripe_fixtures as fx

PASSWORD = os.environ["ADMIN_PASSWORD"]

# A closed window far in the past, so nothing here disturbs the current cycle
# the smoke test and the dashboard tests assert against.
BILL_MONTH_START = date(2021, 5, 1)
BILL_MONTH_END = date(2021, 5, 31)


@pytest.fixture
def db():
    session = SessionLocal()
    fx.clear_billing_rows(session)
    try:
        yield session
    finally:
        fx.clear_billing_rows(session)
        session.close()


# ─── A1 and A6: the meter ───────────────────────────────────────────────────
#
# Moved to `tests/test_metering_pass.py` in session B1b, which replaced
# metering at send time with a scheduled, ledgered pass (`decisions/011`). The
# A1 property — the meter receives the RAW count and reads BILLABLE_STATUSES
# through the model — is asserted there against the pass.

def test_the_back_bill_tool_says_when_its_window_is_not_a_billing_cycle(db):
    """R7. `cost_for_segments()` gives whatever window it is handed an allowance.

    Both directions, because a warning that always fires is as useless as one
    that never does — and the arithmetic it warns about is asserted here too, so
    the test says what the warning is *for* rather than only that it exists.
    """
    from tools.bill_period import window_warning

    rows = []
    try:
        for day in ("05", "20"):
            row = SMSMessage(phone="+15555550395", message="x", status="sent",
                             segments=9000, sent_at=f"2021-05-{day}T09:00:00")
            db.add(row)
            rows.append(row)
        db.commit()

        whole = stripe_reconcile.period_usage(db, date(2021, 5, 1), date(2021, 5, 31))
        first = stripe_reconcile.period_usage(db, date(2021, 5, 1), date(2021, 5, 15))
        second = stripe_reconcile.period_usage(db, date(2021, 5, 16), date(2021, 5, 31))
        # The defect the warning exists for: one cycle split in two bills nothing.
        assert whole["total_due"] > 0
        assert first["total_due"] == 0 and second["total_due"] == 0

        assert window_warning(db, date(2021, 5, 1), date(2021, 5, 31)) == "", (
            "the warning fires on a whole cycle, so it says nothing")
        partial = window_warning(db, date(2021, 5, 1), date(2021, 5, 15))
        assert "NOT A BILLING CYCLE" in partial, partial
        assert "2021-05-31" in partial, "the warning does not name the real cycle"
    finally:
        for row in rows:
            db.delete(row)
        db.commit()


def test_an_unknown_tier_state_is_refused_rather_than_asserted(db):
    """R8. `assert` is stripped under `python -O`, and this guards a public field.

    `/health` publishes `pricing_state` to anyone who asks, and `pricing_ok()`
    decides what a monitor pages on from a fixed set of states. A typo reaching
    that field would read as a state nobody handles — which is `pricing_ok`
    False forever, or True forever, depending on the typo.
    """
    with pytest.raises(ValueError):
        stripe_tiers._store_verdict(db, "agreee", [])
    # And the positive control: a real state still stores.
    assert stripe_tiers._store_verdict(db, "agree", [])["state"] == "agree"


# ─── A2: the two definitions of the allowance must prove they agree ─────────


def test_the_tier_check_reports_agreement_on_a_matching_price(db):
    fake = fx.FakeStripe(price=fx.tiered_price())
    with fx.stripe_configured(), fx.replaced_api(fake):
        verdict = stripe_tiers.check_tier_drift(db)
    assert verdict["state"] == "agree", verdict
    assert verdict["issues"] == []
    assert stripe_tiers.pricing_ok(verdict) is True


def test_the_tier_check_fails_on_a_first_tier_of_five_thousand(db):
    """Criterion 3, and criterion 3 is also the check on the check.

    Run against a price whose answer is known to be wrong *before* the check is
    quoted as evidence — five sessions here have shipped a measurement script
    that was broken before the code was.
    """
    fake = fx.FakeStripe(price=fx.tiered_price(up_to=5000))
    with fx.stripe_configured(), fx.replaced_api(fake):
        verdict = stripe_tiers.check_tier_drift(db)
    assert verdict["state"] == "disagree", verdict
    assert any("5000" in issue for issue in verdict["issues"]), verdict["issues"]
    assert stripe_tiers.pricing_ok(verdict) is False


def test_the_tier_check_fails_on_a_mispriced_second_tier(db):
    """A rate of 2 cents against a plan of 1.5. The other half of A2."""
    fake = fx.FakeStripe(price=fx.tiered_price(rate_cents="2"))
    with fx.stripe_configured(), fx.replaced_api(fake):
        verdict = stripe_tiers.check_tier_drift(db)
    assert verdict["state"] == "disagree", verdict
    assert any("rate" in issue for issue in verdict["issues"]), verdict["issues"]


def test_a_sub_cent_rate_is_compared_as_a_decimal_number_of_cents(db):
    """$0.015 is one and a half cents and is not an integer.

    A check reading `unit_amount` would compare 0.015 against 2 and call a
    correct price wrong. This pins the comparison to the field that can hold the
    value — and to `Decimal`, because `0.015 * 100` is 1.4999999999999998.
    """
    assert Decimal(str(settings.BILLING_PRICE_PER_SEGMENT)) * 100 == Decimal("1.5")
    fake = fx.FakeStripe(price=fx.tiered_price(rate_cents="1.5"))
    with fx.stripe_configured(), fx.replaced_api(fake):
        assert stripe_tiers.check_tier_drift(db)["state"] == "agree"


def test_a_stripe_outage_is_neither_agreement_nor_disagreement(db):
    """Three states, not two. A degraded answer must not read as a chosen one."""
    fake = fx.FakeStripe(fail_with=RuntimeError("connection reset"))
    with fx.stripe_configured(), fx.replaced_api(fake):
        verdict = stripe_tiers.check_tier_drift(db)
    assert verdict["state"] == "unavailable", verdict
    assert stripe_tiers.pricing_ok(verdict) is False


def test_the_tier_check_is_registered_to_run_daily(db):
    """R4. A check that only runs at checkout cannot catch a tier edited later.

    Asserted against the app's own scheduler registration rather than against a
    list in this file, and against the *callable* rather than the job id — an id
    is a string somebody can duplicate, and duplicating one is exactly how a job
    silently replaces another under `replace_existing=True`.
    """
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger

    registered = []
    scheduler = AsyncIOScheduler()

    def _record(func, trigger, *, id, **kwargs):
        # A list, not a dict keyed on id: a dict would silently collapse a
        # duplicate id, which is the exact thing the assertion below looks for.
        registered.append((id, func, trigger))

    scheduler.add_job = _record

    import app.main as main
    original, main.scheduler = main.scheduler, scheduler
    try:
        import asyncio
        asyncio.run(_drain(main.lifespan(main.app)))
    finally:
        main.scheduler = original

    daily = [row for row in registered if row[1] is stripe_tiers.daily_tier_check]
    assert len(daily) == 1, (
        f"the tier check is registered {len(daily)} time(s): "
        f"{sorted(job_id for job_id, _, _ in registered)}")
    assert isinstance(daily[0][2], CronTrigger), daily[0][2]

    # And every job still has an id of its own. `replace_existing=True` means a
    # duplicated id does not error — it silently unregisters whatever held the
    # id first, so a copy-pasted id is a job that stops running and says
    # nothing. `main.py`'s own comment has warned about this since the scheduler
    # was added; nothing asserted it until a mutation walked straight through.
    ids = [job_id for job_id, _, _ in registered]
    assert len(ids) == len(set(ids)), f"duplicate scheduler job id: {sorted(ids)}"
    assert len(registered) == 5, (
        f"a scheduled job appeared or vanished: {sorted(ids)}")


async def _drain(context):
    """Run a lifespan context manager's startup, then its shutdown."""
    await context.__aenter__()
    await context.__aexit__(None, None, None)


def test_a_configured_box_that_has_never_checked_is_not_ok(db):
    """A check nobody has run is indistinguishable from one that would fail."""
    with fx.stripe_configured():
        verdict = stripe_tiers.tier_verdict()
    assert verdict["state"] == "never_checked", verdict
    assert stripe_tiers.pricing_ok(verdict) is False


def test_a_box_with_no_stripe_reports_nothing_to_disagree_about(db):
    """There is no second definition of the allowance here, so no alarm."""
    verdict = stripe_tiers.tier_verdict()
    assert verdict["state"] == "not_configured"
    assert stripe_tiers.pricing_ok(verdict) is True


# ─── A4: one window, described twice ────────────────────────────────────────


def _epoch(when: datetime) -> int:
    return int(when.timestamp())


def test_usages_cycle_and_the_subscriptions_cycle_are_the_same_two_dates(db):
    """Criterion 4. The client reads one window and is charged against it.

    The subscription bills 9 Sept to 9 Oct; `/usage` must report 9 Sept to
    8 Oct, which is the same window under this codebase's convention that
    `cycle_end` is the last day *in* the cycle.
    """
    subscription = fx.subscription(
        anchor_ts=_epoch(datetime(2026, 9, 9, 14, 30)),
        period_start_ts=_epoch(datetime(2026, 9, 9, 14, 30)),
        period_end_ts=_epoch(datetime(2026, 10, 9, 14, 30)))

    stripe_billing.store_subscription(db, fx.CUSTOMER, fx.SUBSCRIPTION, subscription)

    ours = billing_service.get_billing_cycle(date(2026, 9, 20), db=db)[:2]
    theirs = stripe_billing.subscription_cycle(subscription)
    assert ours == theirs == (date(2026, 9, 9), date(2026, 10, 8)), (ours, theirs)


def test_the_cycle_falls_back_to_config_when_nothing_is_stored(db):
    """A box that has never subscribed keeps the behaviour it always had."""
    assert get_setting(db, billing_service.CYCLE_ANCHOR_DAY_KEY) is None
    assert billing_service.cycle_day(db) == settings.BILLING_CYCLE_DAY
    start = billing_service.get_billing_cycle(date(2026, 9, 20), db=db)[0]
    assert start.day == settings.BILLING_CYCLE_DAY


def test_the_stored_anchor_wins_over_the_configured_day(db):
    """The mutation this exists to catch is preferring config over the anchor.

    Pinned against a configured day that is deliberately *not* the anchor, so
    the two answers differ and the assertion can only pass one way.
    """
    subscription = fx.subscription(
        anchor_ts=_epoch(datetime(2026, 9, 9, 9, 0)),
        period_start_ts=_epoch(datetime(2026, 9, 9, 9, 0)),
        period_end_ts=_epoch(datetime(2026, 10, 9, 9, 0)))
    stripe_billing.store_subscription(db, fx.CUSTOMER, fx.SUBSCRIPTION, subscription)
    assert settings.BILLING_CYCLE_DAY == 1, "the fixture needs the two to differ"
    assert billing_service.cycle_day(db) == 9
    assert billing_service.pricing_table(db)["cycle_day"] == 9


def test_the_two_windows_agree_across_a_month_too_short_for_the_anchor(db):
    """An anchor of the 31st, walked through February and April.

    Two independent clampings have to produce the same dates: Stripe's, which
    moves the period to the last day of a short month, and `_clamp_day()`, which
    does the same on our side. They agree today — but nothing was making them,
    and a client anchored on the 29th, 30th or 31st is one subscribe click away.
    The disagreement this would produce is a whole day of segments landing in
    the wrong invoice, twice a year, in the month nobody double-checks.
    """
    stripe_billing.store_subscription(db, fx.CUSTOMER, fx.SUBSCRIPTION, fx.subscription(
        anchor_ts=_epoch(datetime(2026, 1, 31, 9, 0)),
        period_start_ts=_epoch(datetime(2026, 1, 31, 9, 0)),
        period_end_ts=_epoch(datetime(2026, 2, 28, 9, 0))))
    assert billing_service.cycle_day(db) == 31

    for period_start, period_end, probe in (
        (datetime(2026, 1, 31, 9, 0), datetime(2026, 2, 28, 9, 0), date(2026, 2, 10)),
        (datetime(2026, 2, 28, 9, 0), datetime(2026, 3, 31, 9, 0), date(2026, 3, 10)),
        (datetime(2026, 3, 31, 9, 0), datetime(2026, 4, 30, 9, 0), date(2026, 4, 10)),
    ):
        subscription = fx.subscription(
            anchor_ts=_epoch(datetime(2026, 1, 31, 9, 0)),
            period_start_ts=_epoch(period_start), period_end_ts=_epoch(period_end))
        ours = billing_service.get_billing_cycle(probe, db=db)[:2]
        theirs = stripe_billing.subscription_cycle(subscription)
        assert ours == theirs, (probe, ours, theirs)


def test_an_anchor_on_the_31st_is_not_flattened_by_a_short_month(db):
    """`billing_cycle_anchor` is read before the period start, and this is why.

    A subscription anchored on 31 January has a February period starting on the
    28th. Storing 28 would move the client's cycle for good, silently, in the
    direction nobody checks.
    """
    subscription = fx.subscription(
        anchor_ts=_epoch(datetime(2026, 1, 31, 9, 0)),
        period_start_ts=_epoch(datetime(2026, 2, 28, 9, 0)),
        period_end_ts=_epoch(datetime(2026, 3, 31, 9, 0)))
    assert stripe_billing.anchor_day(subscription) == 31


# ─── A5: the shared-account guard ───────────────────────────────────────────


def test_a_session_for_another_product_is_not_ours():
    """Criterion 5. Jordan runs more than one client through one Stripe account."""
    ours = fx.FakeStripe(session_prices=(fx.METERED_PRICE, fx.BALANCE_PRICE))
    theirs = fx.FakeStripe(session_prices=(fx.OTHER_PRICE,))
    with fx.stripe_configured():
        with fx.replaced_api(ours):
            assert stripe_billing.session_is_ours("cs_test_ours") is True
        with fx.replaced_api(theirs):
            assert stripe_billing.session_is_ours("cs_someone_else") is False


def test_the_guard_fails_closed_when_stripe_cannot_be_read():
    """A network error is not a yes. The cost of a false positive is a card."""
    fake = fx.FakeStripe(fail_with=RuntimeError("timeout"))
    with fx.stripe_configured(), fx.replaced_api(fake):
        assert stripe_billing.session_is_ours("cs_test_ours") is False


def test_the_webhook_stores_nothing_for_another_clients_checkout(db):
    fake = fx.FakeStripe(session_prices=(fx.OTHER_PRICE,))
    with fx.stripe_configured(), fx.replaced_api(fake):
        outcome = stripe_billing.handle_checkout_completed(db, fx.completed_event())
    assert outcome["stored"] is False
    assert outcome["reason"] == "not this product"
    assert stripe_billing.customer_id(db) is None


def test_the_webhook_stores_the_customer_for_our_own_checkout(db):
    subscription = fx.subscription(
        anchor_ts=_epoch(datetime(2026, 9, 9, 9, 0)),
        period_start_ts=_epoch(datetime(2026, 9, 9, 9, 0)),
        period_end_ts=_epoch(datetime(2026, 10, 9, 9, 0)))
    fake = fx.FakeStripe(subscription_obj=subscription)
    with fx.stripe_configured(), fx.replaced_api(fake):
        outcome = stripe_billing.handle_checkout_completed(db, fx.completed_event())
    assert outcome["stored"] is True
    assert stripe_billing.customer_id(db) == fx.CUSTOMER
    assert billing_service.cycle_day(db) == 9


# ─── A5: the webhook fails closed ───────────────────────────────────────────


def test_an_unsigned_webhook_is_ignored():
    """Criterion 6, first half."""
    fake = fx.FakeStripe(event=fx.completed_event())
    with fx.stripe_configured(), fx.replaced_api(fake):
        assert stripe_billing.verify_webhook(b"{}", None) is None
        assert stripe_billing.verify_webhook(b"{}", "t=1,v1=nonsense") is None


def test_an_unconfigured_signing_secret_ignores_the_payload_rather_than_trusting_it():
    """Criterion 6, second half — and the half that is tempting to get wrong.

    The reading that ships this bug is "we have not set the secret yet, so let
    it through". What a trusted payload writes is the customer id every segment
    this client sends is billed to, and the URL is open to the internet.
    """
    fake = fx.FakeStripe(event=fx.completed_event())
    with fx.stripe_configured(webhook_secret=""), fx.replaced_api(fake):
        assert stripe_billing.verify_webhook(b"{}", "signed:whsec_test") is None
    assert fake.named("construct_webhook_event") == []


def test_a_correctly_signed_webhook_is_accepted():
    """The positive control. A verifier that never verifies proves nothing."""
    fake = fx.FakeStripe(event=fx.completed_event())
    with fx.stripe_configured(), fx.replaced_api(fake):
        event = stripe_billing.verify_webhook(b"{}", "signed:whsec_test")
    assert event["type"] == "checkout.session.completed"


# ─── A3: the session that is actually built ─────────────────────────────────


def test_the_metered_line_item_carries_no_quantity():
    """Stripe rejects a quantity on a metered price; only licensed prices take one."""
    with fx.stripe_configured():
        items = stripe_billing.checkout_line_items()
    assert items[0] == {"price": fx.METERED_PRICE}
    assert "quantity" not in items[0]


def test_the_outstanding_balance_rides_the_same_session_as_a_second_line_item():
    with fx.stripe_configured():
        items = stripe_billing.checkout_line_items()
    assert items[1] == {"price": fx.BALANCE_PRICE, "quantity": 1}


def test_a_box_with_no_balance_price_still_subscribes():
    """Blank is a supported state: checkout settles nothing and still takes a card."""
    with fx.stripe_configured(balance=""):
        assert stripe_billing.checkout_line_items() == [{"price": fx.METERED_PRICE}]


def test_the_checkout_is_a_subscription_and_not_a_one_off_payment():
    """`mode="subscription"` is what makes anything meter afterwards.

    A `payment` session takes the money once and creates no subscription, so
    there is nothing for the meter to bill against and no monthly invoice — and
    the first invoice would still look right, which is why this needs a test of
    its own rather than riding on one named for the card.
    """
    fake = fx.FakeStripe()
    with fx.stripe_configured(), fx.replaced_api(fake):
        stripe_billing.create_checkout_session("https://x/ok", "https://x/no")
    _, params = fake.named("create_checkout_session")[0]
    assert params["mode"] == "subscription", params


def test_the_session_always_collects_a_payment_method():
    """Without this a $0 recurring total completes with no card on file."""
    fake = fx.FakeStripe()
    with fx.stripe_configured(), fx.replaced_api(fake):
        stripe_billing.create_checkout_session("https://x/ok", "https://x/no")
    _, params = fake.named("create_checkout_session")[0]
    assert params["payment_method_collection"] == "always"
    assert params["mode"] == "subscription"


# ─── A7: the back-bill tool ─────────────────────────────────────────────────


def test_the_back_bill_tool_prices_august_from_billing_services_own_functions(db):
    """Criterion 8, at the arithmetic level. The shell check runs the CLI itself.

    28,002 segments, less the 10,000 included, at $0.015 = $270.03. The figure
    comes from `compute_usage()` and `cost_for_segments()` — the two functions
    `/usage` renders from — so the invoice and the dashboard cannot disagree.
    """
    rows = []
    try:
        # 28,002 segments as three rows, not 28,002 rows: the query sums a
        # column and the row count is not what is under test.
        for segments in (20000, 8000, 2):
            row = SMSMessage(phone="+15555550301", message="x", status="sent",
                             segments=segments, sent_at="2021-05-10T09:00:00")
            db.add(row)
            rows.append(row)
        db.commit()
        usage = stripe_reconcile.period_usage(db, BILL_MONTH_START, BILL_MONTH_END)
    finally:
        for row in rows:
            db.delete(row)
        db.commit()

    assert usage["segments"] == 28002
    assert usage["billable_segments"] == 18002
    assert usage["total_due"] == 270.03
    assert usage["exact_total"] == Decimal("270.03")
    assert usage["total_due"] == billing_service.to_money(
        billing_service.cost_for_segments(28002))


def test_the_invoice_amount_is_cents_rounded_once():
    """$270.03 is 27,003 cents, from the exact Decimal and not from the float."""
    from tools.bill_period import amount_in_cents
    assert amount_in_cents(Decimal("270.03")) == 27003
    # The half-cent case, which is where a float would drift: 3 billable
    # segments at $0.015 is $0.045, and an invoice says 5 cents.
    assert amount_in_cents(billing_service.cost_for_segments(10003)) == 5


# ─── A9: safe to deploy before Stripe exists ────────────────────────────────


@pytest.fixture(scope="module")
def client():
    from app.routers import pages as pages_router
    pages_router.limiter.reset()
    c = TestClient(app)
    login = c.post("/login", data={"username": "admin", "password": PASSWORD})
    assert login.status_code in (200, 302), login.status_code
    yield c
    pages_router.limiter.reset()


def test_subscribe_renders_a_notice_and_the_endpoint_answers_503(client):
    """Criterion 9. The page ships before the Stripe account exists."""
    assert stripe_billing.configured() is False
    page = client.get("/subscribe")
    assert page.status_code == 200
    assert 'id="notAvailable"' in page.text
    assert "not switched on" in page.text
    assert 'id="subscribeBtn"' not in page.text

    refusal = client.post("/api/billing/checkout")
    assert refusal.status_code == 503
    assert refusal.json()["error"] == stripe_billing.NOT_CONNECTED


def test_health_stays_200_while_the_tier_disagrees(client, db):
    """Criterion 3's other half. A 503 here rolls back every deploy."""
    fake = fx.FakeStripe(price=fx.tiered_price(up_to=5000))
    with fx.stripe_configured(), fx.replaced_api(fake):
        stripe_tiers.check_tier_drift(db)
        response = client.get("/health")
        body = response.json()
    assert response.status_code == 200
    assert body["pricing_ok"] is False
    assert body["pricing_state"] == "disagree"
    assert body["pricing_issues"], "the disagreement is not reported at all"
    # The other two signals are untouched: a pricing disagreement is not a
    # sending failure and must not read as one.
    assert body["sending_ok"] is True
    assert body["status"] == "healthy"


def test_health_says_which_state_without_naming_the_commercial_terms(client, db):
    """`/health` has no login, no rate limit, and every scanner reaches it.

    The stored verdict quotes this account's allowance and rate; that is a
    commercial term and the endpoint beside it (`config_issues`) sets the
    precedent of fixed generic wordings with no account data in them. The
    figures still have to exist somewhere loud, so this asserts both halves at
    once: absent from the public body, present on the authenticated route.
    """
    fake = fx.FakeStripe(price=fx.tiered_price(up_to=5000))
    with fx.stripe_configured(), fx.replaced_api(fake):
        stored = stripe_tiers.check_tier_drift(db)
        public = client.get("/health").json()
        private = client.get("/api/billing/status").json()

    # The precondition: the stored verdict really does carry the figures, or
    # the assertion below is about a sentence that never had them.
    assert any("5000" in issue and "10000" in issue for issue in stored["issues"])

    body = json.dumps(public)
    for figure in ("5000", "10000", str(settings.BILLING_PRICE_PER_SEGMENT)):
        assert figure not in body, f"/health published {figure}: {body}"
    assert public["pricing_state"] == "disagree"
    assert public["pricing_issues"] == [
        stripe_tiers.PUBLIC_PRICING_DETAIL["disagree"]]

    assert any("5000" in issue for issue in private["pricing"]["issues"]), private


def test_an_agreement_nobody_has_re_checked_goes_stale(db):
    """A verdict is a claim about a price at a moment.

    Reporting September's answer in March is the "degraded state described as a
    chosen one" defect with time as the degradation. Only agreement ages —
    a disagreement is still true until somebody fixes it.
    """
    from datetime import timedelta

    from app.models.app_setting import set_setting

    def stored_at(state, when):
        set_setting(db, stripe_tiers.TIER_CHECK_KEY, stripe_billing.dumps(
            {"state": state, "issues": [], "checked_at": when.isoformat(timespec="seconds")}))
        with fx.stripe_configured():
            return stripe_tiers.tier_verdict()

    fresh = stored_at("agree", datetime.now() - timedelta(days=1))
    assert fresh["state"] == "agree"
    assert stripe_tiers.pricing_ok(fresh) is True

    old = stored_at("agree", datetime.now()
                    - timedelta(days=stripe_tiers.TIER_CHECK_MAX_AGE_DAYS + 1))
    assert old["state"] == "stale", old
    assert stripe_tiers.pricing_ok(old) is False

    unfixed = stored_at("disagree", datetime.now() - timedelta(days=365))
    assert unfixed["state"] == "disagree", "a disagreement does not expire"


def test_health_makes_no_stripe_call(client, db):
    """An uptime monitor polls this every minute. It must not become a bill."""
    fake = fx.FakeStripe()
    with fx.stripe_configured(), fx.replaced_api(fake):
        for _ in range(3):
            assert client.get("/health").status_code == 200
    assert fake.calls == [], fake.calls


def test_the_success_page_applies_the_same_guard_as_the_webhook(client, db):
    """Criterion 5's second half. Anybody can type this URL with any session id."""
    theirs = fx.FakeStripe(session_prices=(fx.OTHER_PRICE,))
    with fx.stripe_configured(), fx.replaced_api(theirs):
        response = client.get("/billing/success?session_id=cs_someone_else")
    assert response.status_code == 200
    assert stripe_billing.customer_id(db) is None
    assert "could not match that checkout" in response.text

    subscription = fx.subscription(
        anchor_ts=_epoch(datetime(2026, 9, 9, 9, 0)),
        period_start_ts=_epoch(datetime(2026, 9, 9, 9, 0)),
        period_end_ts=_epoch(datetime(2026, 10, 9, 9, 0)))
    ours = fx.FakeStripe(subscription_obj=subscription)
    with fx.stripe_configured(), fx.replaced_api(ours):
        response = client.get("/billing/success?session_id=cs_test_ours")
    assert stripe_billing.customer_id(db) == fx.CUSTOMER
    assert "card is on file" in response.text


def test_the_webhook_route_rejects_an_unsigned_payload(client, db):
    with fx.stripe_configured(), fx.replaced_api(fx.FakeStripe()):
        response = client.post("/webhooks/stripe", content=b"{}")
    assert response.status_code == 400
    assert stripe_billing.customer_id(db) is None
