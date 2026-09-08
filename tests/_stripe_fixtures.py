"""Recorded Stripe shapes and the fake that serves them.

Not a test module — the leading underscore keeps pytest from collecting it.

## Nothing in the suite may reach Stripe

`app.services.stripe_billing.StripeAPI` is the only object in the codebase that
touches the SDK, so replacing it replaces every call. `replaced_api()` installs
a fake for the duration of a block and restores whatever was there before.

That is the design half. The proof half is `agent/accept-B1.sh` check 1, which
runs these modules with `socket.socket.connect` raising — a test that reached
the network fails the run rather than being caught by everyone remembering.

## The payloads are recorded, not invented

Every dict below is the shape the pinned SDK returns, cross-checked against
`stripe.checkout.Session`, `stripe.Price.Tier`, `stripe.Subscription` and
`stripe.SubscriptionItem`'s own declared fields — `tests/test_stripe_contract.py`
asserts that cross-check runs rather than trusting this sentence. Where a shape
differs from what a reader would expect from Stripe's docs, it differs because
the API version this SDK pins moved it:

  * `current_period_start` / `current_period_end` are on the subscription's
    **items**, not on the subscription.
  * a tier's per-unit price for a sub-cent rate is in `unit_amount_decimal`
    (cents, as a decimal string). `unit_amount` is an integer number of cents
    and cannot express $0.015 at all.
"""

from contextlib import contextmanager
from decimal import Decimal

from app.services import stripe_billing

METERED_PRICE = "price_test_metered_segments"
BALANCE_PRICE = "price_test_august_balance"
OTHER_PRICE = "price_someone_elses_product"
CUSTOMER = "cus_test_a4a"
SUBSCRIPTION = "sub_test_a4a"


class Obj:
    """Attribute access over a dict, the way the SDK's StripeObject behaves.

    The SDK's own objects support both `x.id` and `x["id"]`; the production code
    reads either, deliberately, because a webhook payload arrives as one shape
    and a `retrieve()` as the other. This fake supports both so a test cannot
    pass by accident on the shape it happened to pick.
    """

    def __init__(self, **fields):
        for key, value in fields.items():
            setattr(self, key, value)

    def get(self, key, default=None):
        return getattr(self, key, default)

    def __getitem__(self, key):
        return getattr(self, key)

    def __contains__(self, key):
        return hasattr(self, key)


def line_items(*price_ids):
    return Obj(data=[Obj(price=Obj(id=price_id)) for price_id in price_ids])


def tiered_price(up_to=None, rate_cents="1.5", price_id=METERED_PRICE):
    """A graduated price. `up_to=None` means "use the configured allowance"."""
    from app.core.config import settings
    if up_to is None:
        up_to = settings.BILLING_SEGMENTS_INCLUDED
    return Obj(
        id=price_id,
        tiers=[
            Obj(up_to=up_to, unit_amount=0, unit_amount_decimal=Decimal("0"),
                flat_amount=None, flat_amount_decimal=None),
            Obj(up_to=None, unit_amount=None,
                unit_amount_decimal=Decimal(str(rate_cents)),
                flat_amount=None, flat_amount_decimal=None),
        ],
    )


def subscription(anchor_ts, period_start_ts, period_end_ts,
                 subscription_id=SUBSCRIPTION):
    """The period lives on the item. See this module's docstring."""
    return Obj(
        id=subscription_id,
        billing_cycle_anchor=anchor_ts,
        items=Obj(data=[Obj(id="si_test",
                            current_period_start=period_start_ts,
                            current_period_end=period_end_ts)]),
    )


def completed_event(session_id="cs_test_ours", customer=CUSTOMER,
                    subscription_ref=SUBSCRIPTION):
    return {"type": "checkout.session.completed",
            "data": {"object": {"id": session_id, "customer": customer,
                                "subscription": subscription_ref}}}


class FakeStripe:
    """Records every call and answers from what it was given.

    Counting rather than just answering, because several of this session's
    assertions are about how many calls happened — a meter event posted twice
    under one identifier, a `/health` poll that must make no Stripe request at
    all. A fake that only returns values cannot answer those.
    """

    def __init__(self, *, price=None, session_prices=(METERED_PRICE,),
                 subscription_obj=None, checkout_url="https://checkout.example/x",
                 fail_with=None, event=None):
        self.price = price
        self.session_prices = tuple(session_prices)
        self.subscription_obj = subscription_obj
        self.checkout_url = checkout_url
        self.fail_with = fail_with
        self.event = event
        self.calls = []
        self.meter_events = []
        self.invoice_items = []
        self.invoices = []
        self.finalized = []

    # ── the calls production makes ──────────────────────────────────────────

    def create_checkout_session(self, **params):
        self.calls.append(("create_checkout_session", params))
        self._maybe_fail()
        return Obj(id="cs_test_ours", url=self.checkout_url)

    def retrieve_checkout_session(self, session_id):
        self.calls.append(("retrieve_checkout_session", session_id))
        self._maybe_fail()
        return Obj(id=session_id, customer=CUSTOMER, subscription=SUBSCRIPTION)

    def list_session_line_items(self, session_id, limit=100):
        self.calls.append(("list_session_line_items", session_id))
        self._maybe_fail()
        return line_items(*self.session_prices)

    def retrieve_subscription(self, subscription_id):
        self.calls.append(("retrieve_subscription", subscription_id))
        self._maybe_fail()
        return self.subscription_obj

    def retrieve_price(self, price_id):
        self.calls.append(("retrieve_price", price_id))
        self._maybe_fail()
        return self.price if self.price is not None else tiered_price()

    def create_meter_event(self, **params):
        self.calls.append(("create_meter_event", params))
        self._maybe_fail()
        # Stripe records the first event under an identifier and ignores the
        # rest. The fake models the *record*, so a test can assert that a
        # repeated identifier bills once instead of asserting on our own code
        # having chosen not to call.
        identifier = params.get("identifier")
        if identifier not in {event.get("identifier") for event in self.meter_events}:
            self.meter_events.append(params)
        return Obj(id="mbe_test", identifier=identifier)

    def create_invoice_item(self, **params):
        self.calls.append(("create_invoice_item", params))
        self._maybe_fail()
        self.invoice_items.append(params)
        return Obj(id="ii_test")

    def create_invoice(self, **params):
        self.calls.append(("create_invoice", params))
        self._maybe_fail()
        self.invoices.append(params)
        return Obj(id="in_test")

    def finalize_invoice(self, invoice_id, **params):
        self.calls.append(("finalize_invoice", invoice_id))
        self._maybe_fail()
        self.finalized.append(invoice_id)
        return Obj(id=invoice_id, status="open")

    def construct_webhook_event(self, payload, signature, secret):
        self.calls.append(("construct_webhook_event", signature))
        if signature != f"signed:{secret}":
            raise ValueError("Signature verification failed")
        return self.event

    # ── helpers ─────────────────────────────────────────────────────────────

    def _maybe_fail(self):
        if self.fail_with is not None:
            raise self.fail_with

    def named(self, name):
        return [call for call in self.calls if call[0] == name]

    @property
    def metered_values(self):
        return [int(event["payload"]["value"]) for event in self.meter_events]


@contextmanager
def replaced_api(fake):
    """Install `fake` as the only Stripe client for the duration of the block."""
    previous = stripe_billing._API_FACTORY
    stripe_billing._API_FACTORY = lambda: fake
    try:
        yield fake
    finally:
        stripe_billing._API_FACTORY = previous


@contextmanager
def stripe_configured(monkeypatch=None, *, secret="sk_test_visibly_fake",
                      metered=METERED_PRICE, balance=BALANCE_PRICE,
                      webhook_secret="whsec_test"):
    """Pretend this box has Stripe set up, and put it back afterwards.

    The key is visibly fake. `conftest.py` blanks `STRIPE_SECRET_KEY` for the
    whole suite for the same reason it forces `SMS_PROVIDER=console`: a stray
    live credential in a developer's environment must never be what a test runs
    against. Nothing here can reach Stripe anyway — `replaced_api()` owns the
    client — but the two guards are independent on purpose.
    """
    from app.core.config import settings
    before = (settings.STRIPE_SECRET_KEY, settings.STRIPE_PRICE_METERED,
              settings.STRIPE_PRICE_BALANCE, settings.STRIPE_WEBHOOK_SECRET)
    settings.STRIPE_SECRET_KEY = secret
    settings.STRIPE_PRICE_METERED = metered
    settings.STRIPE_PRICE_BALANCE = balance
    settings.STRIPE_WEBHOOK_SECRET = webhook_secret
    try:
        yield settings
    finally:
        (settings.STRIPE_SECRET_KEY, settings.STRIPE_PRICE_METERED,
         settings.STRIPE_PRICE_BALANCE, settings.STRIPE_WEBHOOK_SECRET) = before


def clear_billing_rows(db):
    """Remove every settings row this session writes, so tests do not inherit.

    The suite shares one database on purpose — it is one end-to-end story — and
    a stored customer id is exactly the kind of debris that makes a later test
    pass for a reason that has nothing to do with it.
    """
    from app.models.app_setting import AppSetting
    from app.services import stripe_meter, stripe_tiers
    from app.services.billing_service import CYCLE_ANCHOR_DAY_KEY

    keys = (stripe_billing.CUSTOMER_ID_KEY, stripe_billing.SUBSCRIPTION_ID_KEY,
            stripe_billing.CYCLE_ANCHOR_AT_KEY, CYCLE_ANCHOR_DAY_KEY,
            stripe_tiers.TIER_CHECK_KEY)
    db.query(AppSetting).filter(AppSetting.key.in_(keys)).delete(
        synchronize_session=False)
    db.query(AppSetting).filter(
        AppSetting.key.like(f"{stripe_meter.REPORTED_KEY_PREFIX}%")).delete(
        synchronize_session=False)
    db.commit()
