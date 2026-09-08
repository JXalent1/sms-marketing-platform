"""The Stripe shapes this codebase drives, asserted against the installed SDK.

`requirements.txt` once said `telnyx==2.1.2` while the provider was written
against the 4.x client class. Nothing failed at import, at boot or in the suite;
the app served every page and could not send a message. The rule that came out
of it — *a pinned SDK is a runtime contract, not a version number* — produced
`tests/test_provider_status.py`. This is the same test one vendor along.

**Nothing here makes a network call.** Every assertion reads the package's own
declared parameter types and resource fields. That is also what makes this the
answer to `sessions/session-B1.md` A3's "verify which mechanism this API version
accepts — do not choose from memory": the answer is in the installed package,
and it is checked on every run rather than remembered once.
"""

import pytest

stripe = pytest.importorskip("stripe")


def _create_params(module_path, class_name):
    """A `…CreateParams` TypedDict's declared keys.

    They live under `stripe.params.…` and are `TYPE_CHECKING`-only from the
    resource's point of view, so they are imported by path rather than off the
    resource class.
    """
    from importlib import import_module
    return set(getattr(import_module(module_path), class_name).__annotations__)


CHECKOUT_PARAMS_MODULE = "stripe.params.checkout._session_create_params"


# ─── A3: which mechanism carries the outstanding balance ────────────────────


def test_checkout_subscription_data_does_not_accept_add_invoice_items():
    """The mechanism chosen for the August balance, and why the other was not.

    A3 offers two: a one-time price as a second line item, or
    `subscription_data.add_invoice_items`. The second does not exist on
    `checkout.Session.create` in this API version — `add_invoice_items` is a
    **Subscription** and **SubscriptionSchedule** parameter, which is exactly
    the kind of thing a reader half-remembers as available everywhere.

    Asserted in both directions: absent where we would have used it, present
    where it really lives. One assertion alone would pass on a typo in the
    class name and prove nothing.
    """
    subscription_data = _create_params(CHECKOUT_PARAMS_MODULE,
                                       "SessionCreateParamsSubscriptionData")
    assert "add_invoice_items" not in subscription_data, (
        "This API version accepts add_invoice_items on a Checkout Session after "
        "all. Re-read sessions/session-B1.md A3 before changing the line items — "
        "the mechanism was chosen because this parameter was absent.")

    elsewhere = _create_params("stripe.params._subscription_create_params",
                               "SubscriptionCreateParams")
    assert "add_invoice_items" in elsewhere, (
        "add_invoice_items is not on Subscription.create either, so the "
        "assertion above proves nothing about where the parameter lives")


def test_a_checkout_session_takes_the_parameters_this_app_sends():
    """`mode`, `line_items`, `payment_method_collection` and the two URLs.

    `payment_method_collection` above all: without it a subscription whose
    recurring total is $0 completes without capturing a card, and every month
    under the allowance is a $0 invoice.
    """
    params = _create_params(CHECKOUT_PARAMS_MODULE, "SessionCreateParams")
    for name in ("mode", "line_items", "payment_method_collection",
                 "success_url", "cancel_url"):
        assert name in params, f"checkout.Session.create no longer accepts {name}"

    line_item = _create_params(CHECKOUT_PARAMS_MODULE, "SessionCreateParamsLineItem")
    assert {"price", "quantity"} <= line_item, line_item


def test_the_parameters_the_app_actually_sends_are_all_accepted():
    """The set this codebase builds, checked against the set the SDK declares.

    Written as a difference rather than as a list of names, so a parameter added
    to `create_checkout_session()` by a later session is covered without anybody
    remembering to extend this file. It goes through the real function with the
    client replaced, which is the entry point the client's browser reaches — a
    property proved of a helper is not proved of its only caller.
    """
    from app.services import stripe_billing
    from tests._stripe_fixtures import FakeStripe, replaced_api, stripe_configured

    fake = FakeStripe()
    with stripe_configured(), replaced_api(fake):
        stripe_billing.create_checkout_session("https://x/ok", "https://x/no")

    _, sent = fake.named("create_checkout_session")[0]
    declared = _create_params(CHECKOUT_PARAMS_MODULE, "SessionCreateParams")
    assert set(sent) <= declared, set(sent) - declared


# ─── A2: the fields the tier check reads ────────────────────────────────────


def test_a_price_tier_still_carries_up_to_and_a_decimal_unit_amount():
    """`unit_amount_decimal`, not `unit_amount`, and the reason is arithmetic.

    `unit_amount` is an **integer** number of cents. This client's rate is
    $0.015 — one and a half cents — so it is not representable in that field at
    all, and a drift check written against it would report disagreement on a
    correctly configured price. `sessions/session-B1.md` A2 names `unit_amount`;
    reading the SDK is what caught it.
    """
    tier = stripe.Price.Tier.__annotations__
    assert "up_to" in tier
    assert "unit_amount_decimal" in tier
    assert "unit_amount" in tier
    assert stripe.Price.Tier._field_encodings.get("unit_amount_decimal") == \
        "decimal_string", (
        "unit_amount_decimal is no longer decoded as a decimal string, so the "
        "tier check may be comparing a float to a Decimal")


def test_a_price_can_be_retrieved_with_its_tiers_expanded():
    """`tiers` is absent unless expanded, and an absent list reads as no tiers."""
    params = _create_params("stripe.params._price_retrieve_params",
                            "PriceRetrieveParams")
    assert "expand" in params
    assert "tiers" in stripe.Price.__annotations__


# ─── A4: where the billing period lives ─────────────────────────────────────


def test_the_billing_period_is_on_the_subscription_item_not_the_subscription():
    """A4 names `subscription.current_period_start`. This API version does not have it.

    Both halves are asserted. "It is not on the Subscription" alone would also
    pass if the SDK had dropped the concept entirely, and then
    `subscription_cycle()` would be reading a field that exists nowhere.
    """
    assert "current_period_start" not in stripe.Subscription.__annotations__, (
        "current_period_start is back on the Subscription object. "
        "app/services/stripe_billing.py reads it off the item; check which is "
        "authoritative before simplifying.")
    for field in ("current_period_start", "current_period_end"):
        assert field in stripe.SubscriptionItem.__annotations__, field
    assert "billing_cycle_anchor" in stripe.Subscription.__annotations__


# ─── A6: the meter event and its dedupe key ─────────────────────────────────


def test_a_meter_event_takes_a_deterministic_identifier():
    """`identifier` is the whole idempotency story for A6.

    Without it a retried background task, a redeploy mid-run and a backfill each
    bill the same campaign again.
    """
    params = _create_params("stripe.params.billing._meter_event_create_params",
                            "MeterEventCreateParams")
    for name in ("event_name", "identifier", "payload"):
        assert name in params, f"billing.MeterEvent.create no longer takes {name}"
    assert "identifier" in stripe.billing.MeterEvent.__annotations__


def test_the_invoice_calls_the_back_bill_tool_makes_still_exist():
    """`tools/bill_period.py` drafts an item, drafts an invoice, finalises it."""
    assert "amount" in _create_params("stripe.params._invoice_item_create_params",
                                      "InvoiceItemCreateParams")
    assert "auto_advance" in _create_params("stripe.params._invoice_create_params",
                                            "InvoiceCreateParams")
    assert hasattr(stripe.Invoice, "finalize_invoice")


def test_webhook_signature_verification_is_where_the_app_looks_for_it():
    import inspect
    signature = inspect.signature(stripe.Webhook.construct_event)
    assert list(signature.parameters)[:3] == ["payload", "sig_header", "secret"]


# ─── The adapter itself: the one object that talks to Stripe ────────────────
#
# Everything above reads the SDK's declared shapes. These drive `StripeAPI`
# against a stub module and assert what it *sends* — which is the only way to
# see a parameter the adapter adds on the caller's behalf. `expand=["tiers"]` is
# exactly that: no fake standing in for the adapter can notice its absence,
# because the adapter is what supplies it, and a price fetched without it comes
# back with no tiers at all — so the drift check would compare against nothing
# and report agreement.


class _Recorder:
    """The smallest thing that looks like the `stripe` module to `StripeAPI`."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        return _Node(self, (name,))


class _Node:
    def __init__(self, recorder, path):
        self._recorder = recorder
        self._path = path

    def __getattr__(self, name):
        return _Node(self._recorder, self._path + (name,))

    def __call__(self, *args, **kwargs):
        self._recorder.calls.append((".".join(self._path), args, kwargs))
        return {"id": "obj_stub"}


def _driven(method, *args, **kwargs):
    from app.services.stripe_billing import StripeAPI

    recorder = _Recorder()
    api = StripeAPI("sk_test_visibly_fake")
    api._stripe = lambda: recorder
    getattr(api, method)(*args, **kwargs)
    return recorder.calls[0]


def test_the_price_is_always_fetched_with_its_tiers_expanded():
    """Without `expand`, `tiers` is absent and an absent list reads as no tiers."""
    name, args, kwargs = _driven("retrieve_price", "price_x")
    assert name == "Price.retrieve"
    assert args == ("price_x",)
    assert kwargs["expand"] == ["tiers"], kwargs


def test_every_call_carries_its_key_rather_than_setting_a_global():
    """`stripe.api_key` is process-wide state any other import would inherit.

    Checked across the whole adapter rather than on one method, so a call added
    later is covered without anybody remembering to extend this test.
    """
    import stripe

    driven = [
        ("retrieve_price", ("price_x",), {}),
        ("retrieve_checkout_session", ("cs_x",), {}),
        ("list_session_line_items", ("cs_x",), {}),
        ("retrieve_subscription", ("sub_x",), {}),
        ("create_checkout_session", (), {"mode": "subscription"}),
        ("create_meter_event", (), {"identifier": "campaign_1"}),
        ("create_invoice_item", (), {"amount": 1}),
        ("create_invoice", (), {}),
        ("finalize_invoice", ("in_x",), {}),
    ]
    for method, args, kwargs in driven:
        _, _, sent = _driven(method, *args, **kwargs)
        assert sent.get("api_key") == "sk_test_visibly_fake", method
    assert stripe.api_key is None, (
        "something in this process set a module-level Stripe key")


def test_the_meter_event_payload_uses_the_meters_own_field_names():
    """`stripe_customer_id` and `value` are the meter's configured keys.

    Part B configures the meter with those names. A payload that spelled either
    differently would be accepted by the API and aggregate to nothing — a meter
    that reads zero looks exactly like a quiet month.
    """
    from app.core.config import settings
    from app.services import stripe_meter
    from tests._stripe_fixtures import FakeStripe, replaced_api, stripe_configured
    from app.core.database import SessionLocal
    from app.models.app_setting import set_setting
    from app.services import stripe_billing

    fake = FakeStripe()
    db = SessionLocal()
    try:
        set_setting(db, stripe_billing.CUSTOMER_ID_KEY, "cus_probe")
        with stripe_configured(), replaced_api(fake):
            stripe_meter.report_segments(42, 99, db)
    finally:
        from tests._stripe_fixtures import clear_billing_rows
        clear_billing_rows(db)
        db.close()

    _, params = fake.named("create_meter_event")[0]
    assert set(params["payload"]) == {"stripe_customer_id", "value"}, params
    assert params["payload"]["value"] == "42"
    # Compared against the setting, not against the literal it defaults to. A
    # pinned literal goes red on a developer who has STRIPE_METER_EVENT_NAME
    # exported, and green on the change it exists to catch — the
    # `OPT_OUT_REASONS` failure, one file along.
    assert params["event_name"] == settings.STRIPE_METER_EVENT_NAME
