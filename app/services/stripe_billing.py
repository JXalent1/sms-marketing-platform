"""The link between this client's account and one Stripe subscription.

One checkout does two things: it settles the outstanding balance on the first
invoice and it attaches the card every month afterwards is billed against. The
client does it once and is never invoiced by hand again.

## Stripe is not the carrier

The white-label rule covers the **SMS carrier**, whose name must never reach a
client-facing surface. Stripe is the payment processor: it appears on his card
statement, it renders the page he types his card number into, and hiding it
would break checkout. It is not scrubbed anywhere. `WHOLESALE_COST_PER_SEGMENT`
is a different question and the answer is unchanged — it reaches nothing here.

## Every call goes through one object, and that is deliberate

`StripeAPI` below lists every Stripe call this codebase makes. Nothing else in
the app touches the SDK. Three things follow:

  1. A test replaces one object instead of monkeypatching a package, so
     "no test reaches the network" is a property of the design rather than of
     everyone's discipline. `agent/accept-B1.sh` check 1 proves it by running
     the suite with `socket.connect` disabled.
  2. `stripe.api_key` is never set globally. A stray import in another module
     therefore cannot inherit a live key.
  3. When the API version moves, the diff is in one class.

## What is verified rather than remembered

`sessions/session-B1.md` A3 offers two mechanisms for the one-time balance —
an extra line item, or `subscription_data.add_invoice_items` — and says to
verify which this API version accepts rather than choosing from memory. The
answer is the line item: `add_invoice_items` is a **Subscription** and
**SubscriptionSchedule** parameter and is not, and never was, part of
`checkout.Session.create`'s `subscription_data`. `tests/test_stripe_contract.py`
asserts that against the installed SDK, so a pin that moves the shape fails at
`pip install` rather than on the morning the client tries to pay — the rule
`tests/test_provider_status.py` exists for, one vendor along.
"""

import json
import logging
from datetime import date, datetime
from typing import Optional, Tuple

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.app_setting import get_setting, set_setting
from app.services import billing_service
from app.services.billing_service import CYCLE_ANCHOR_DAY_KEY

logger = logging.getLogger("billing.stripe")

# Rows in `app_settings`. One writer each, and the reader imports the name —
# see the note on CYCLE_ANCHOR_DAY_KEY in billing_service.
CUSTOMER_ID_KEY = "stripe_customer_id"
SUBSCRIPTION_ID_KEY = "stripe_subscription_id"
CYCLE_ANCHOR_AT_KEY = "billing_cycle_anchor_at"

# Wording the client sees when Stripe is not set up on this box. Blank keys are
# a supported state — the page ships before the Stripe account exists — and a
# supported state gets a sentence, not a stack trace.
NOT_CONNECTED = (
    "Card payments are not switched on for this account yet. Nothing is owed "
    "through this page in the meantime and your usage is still being counted."
)
CHECKOUT_FAILED = (
    "The payment page could not be opened just now. Nothing has been charged. "
    "Please try again in a few minutes."
)


class StripeNotConfigured(RuntimeError):
    """No secret key or no metered price. Callers turn this into a 503."""


# ─── The one place that talks to Stripe ─────────────────────────────────────


class StripeAPI:
    """Every Stripe call this codebase makes.

    Thin on purpose: no decisions live here, only the SDK spelling. A fake in
    the suite implements the same handful of methods and nothing has to know
    which one it is holding.

    The key is passed per call rather than assigned to `stripe.api_key`. A
    module-level key is process-wide state that any other import inherits, and
    "no test can reach a live account" is much easier to guarantee when there is
    no global to leak into.
    """

    def __init__(self, api_key: str):
        self._key = api_key

    def _stripe(self):
        # Imported inside the method so importing this module costs nothing on a
        # box with no Stripe, and so a missing package degrades at the call site
        # with a clear error rather than taking the whole app down at boot.
        import stripe
        return stripe

    def create_checkout_session(self, **params):
        return self._stripe().checkout.Session.create(api_key=self._key, **params)

    def retrieve_checkout_session(self, session_id: str):
        return self._stripe().checkout.Session.retrieve(session_id, api_key=self._key)

    def list_session_line_items(self, session_id: str, limit: int = 100):
        return self._stripe().checkout.Session.list_line_items(
            session_id, limit=limit, api_key=self._key)

    def retrieve_subscription(self, subscription_id: str):
        return self._stripe().Subscription.retrieve(subscription_id, api_key=self._key)

    def retrieve_price(self, price_id: str):
        # `tiers` is not returned unless it is expanded, and a graduated price
        # with no `tiers` in the response reads exactly like a price with no
        # tiers configured. The drift check would then be comparing against
        # nothing and reporting agreement.
        return self._stripe().Price.retrieve(price_id, expand=["tiers"],
                                             api_key=self._key)

    def create_meter_event(self, **params):
        return self._stripe().billing.MeterEvent.create(api_key=self._key, **params)

    def create_invoice_item(self, **params):
        return self._stripe().InvoiceItem.create(api_key=self._key, **params)

    def create_invoice(self, **params):
        return self._stripe().Invoice.create(api_key=self._key, **params)

    def finalize_invoice(self, invoice_id: str, **params):
        return self._stripe().Invoice.finalize_invoice(invoice_id, api_key=self._key,
                                                       **params)

    def construct_webhook_event(self, payload, signature: str, secret: str):
        return self._stripe().Webhook.construct_event(payload, signature, secret)


# Tests install a factory here. Production never touches it, and the default of
# None means the only way to get a real client is through `api()` below, which
# refuses without a configured key.
_API_FACTORY = None


def api() -> StripeAPI:
    if _API_FACTORY is not None:
        return _API_FACTORY()
    if not configured():
        raise StripeNotConfigured(
            "STRIPE_SECRET_KEY and STRIPE_PRICE_METERED must both be set")
    return StripeAPI(settings.STRIPE_SECRET_KEY)


def configured() -> bool:
    """Both halves, because either alone is useless.

    A key with no price cannot open a checkout; a price with no key cannot be
    read. There is no half-configured state worth serving, so the page says so
    and the endpoint answers 503 rather than failing later with an SDK error the
    client cannot act on.
    """
    return bool(settings.STRIPE_SECRET_KEY and settings.STRIPE_PRICE_METERED)


# ─── Checkout ───────────────────────────────────────────────────────────────


def checkout_line_items() -> list:
    """The metered price, plus the outstanding balance when one is configured.

    **No `quantity` on the metered price.** Stripe rejects a quantity on a
    metered line item — only licensed prices take one — and the request fails
    outright, so this is a mistake that costs the client a checkout rather than
    a wrong number. The usage arrives as meter events; there is nothing to
    quantify at subscribe time.

    The balance is a separate one-time price with `quantity=1`, which is the
    mechanism verified against the SDK's own declared parameters rather than
    chosen from memory. See this module's docstring.
    """
    items = [{"price": settings.STRIPE_PRICE_METERED}]
    if settings.STRIPE_PRICE_BALANCE:
        items.append({"price": settings.STRIPE_PRICE_BALANCE, "quantity": 1})
    return items


def create_checkout_session(success_url: str, cancel_url: str):
    """One `mode=subscription` session that settles the balance and takes a card.

    `payment_method_collection="always"` is not decoration. A subscription whose
    *recurring* total is $0 can complete without capturing a card, and every
    month under the allowance is a $0 invoice — so without this, a quiet month
    would leave us with a subscription and no way to charge the first busy one.
    The August balance happens to force a card today; the setting is what
    guarantees it tomorrow, and the balance is a one-off.
    """
    return api().create_checkout_session(
        mode="subscription",
        line_items=checkout_line_items(),
        payment_method_collection="always",
        success_url=success_url,
        cancel_url=cancel_url,
    )


# ─── A5: the shared-account guard ───────────────────────────────────────────


def session_is_ours(session_id: str) -> bool:
    """Does this Checkout Session bill against *our* metered price?

    Jordan runs more than one client through one Stripe account. A handler that
    stored the customer id from any completed checkout would let another
    client's unrelated payment overwrite this client's customer id — and then
    A4A's segments would be metered onto that client's card. The failure is
    silent on both sides and the money moves.

    The metered price id is the fingerprint: nothing else on the account bills
    against this meter. It is checked by fetching the session's line items,
    because the id a caller hands us is the one thing we must not trust — the
    success URL is a plain GET that anybody can hit with any session id.

    Fails closed on everything: an unconfigured price, a session Stripe will not
    return, a network error. The cost of a false negative is a client who has to
    click again; the cost of a false positive is another client's card.
    """
    if not session_id or not settings.STRIPE_PRICE_METERED:
        return False
    try:
        items = api().list_session_line_items(session_id)
    except Exception as exc:
        logger.error("Could not read line items for checkout session %s: %s",
                     session_id, exc)
        return False
    for item in getattr(items, "data", []) or []:
        price = getattr(item, "price", None)
        if price is not None and getattr(price, "id", None) == settings.STRIPE_PRICE_METERED:
            return True
    return False


# ─── A4: the cycle anchor ───────────────────────────────────────────────────


def _local_date(timestamp: int) -> date:
    """A Stripe epoch as a calendar date, in the clock the rest of the app keeps.

    Every timestamp this application writes comes from `datetime.now()`, which
    is local — `sms_messages.sent_at` above all, and that column is what
    `compute_usage()` filters the cycle on. Converting Stripe's UTC epoch to a
    UTC date here would give the cycle two clocks, which is the defect
    `contact_list_members.added_at` cost a migration to remove.

    One converter, used by the writer that stores the anchor and by the reader
    that describes the subscription's period, so the two cannot drift.
    """
    return datetime.fromtimestamp(int(timestamp)).date()


def subscription_cycle(subscription) -> Optional[Tuple[date, date]]:
    """(first day, last day) of the period this subscription is currently billing.

    Returns `None` when the object carries no period, which is what a trimmed
    payload looks like — a caller comparing two windows has to be able to tell
    "they disagree" from "there was nothing to compare".

    **The period lives on the subscription's items, not on the subscription.**
    `current_period_start` and `current_period_end` were moved off the
    Subscription object in the API version this SDK pins. `sessions/session-B1.md`
    A4 names the field on the subscription; reading the SDK rather than
    remembering is what caught that, and it is the same discipline that settled
    A3's two mechanisms.

    The end is inclusive — one day before Stripe's `current_period_end`, which
    is the instant the *next* period begins. `billing_service.get_billing_cycle()`
    returns the last day *in* the cycle for the same reason, so the two windows
    are comparable by construction rather than by two authors agreeing about an
    off-by-one.
    """
    item = _first_item(subscription)
    start_ts = getattr(item, "current_period_start", None) if item else None
    end_ts = getattr(item, "current_period_end", None) if item else None
    if start_ts is None or end_ts is None:
        return None
    return (_local_date(start_ts),
            date.fromordinal(_local_date(end_ts).toordinal() - 1))


def anchor_day(subscription) -> Optional[int]:
    """The day of the month this subscription turns over on.

    `billing_cycle_anchor` first, because it is the field that *defines* the
    day and it never moves. The current period's start is the fallback and is
    right in every ordinary month — but a subscription anchored on the 31st has
    a February period starting on the 28th, and storing 28 would quietly move
    the client's cycle for good. Reading the anchor costs nothing and cannot
    drift.
    """
    ts = getattr(subscription, "billing_cycle_anchor", None)
    if not ts:
        item = _first_item(subscription)
        ts = getattr(item, "current_period_start", None) if item else None
    return _local_date(ts).day if ts else None


def _first_item(subscription):
    """The subscription's first item, or None.

    An earlier version also handled `subscription.items` arriving as a *callable*
    — the dict method of that name, which `StripeObject` inherits. Driven
    against the pinned SDK, `Subscription.items` is a `ListObject` and
    `callable()` is False; no arrangement either caller can produce reaches the
    branch, and nothing would have noticed its removal. A guard no arrangement
    can reach is dead code, not a guard, and a comment with an `if` in front of
    it is worse than a comment. Deleted rather than left to be defended.
    """
    data = getattr(getattr(subscription, "items", None), "data", None)
    return data[0] if data else None


def store_subscription(db: Session, customer_id: str, subscription_id: str,
                       subscription=None) -> dict:
    """Record the customer, the subscription and the day the cycle turns over.

    Called only after `session_is_ours()` has said yes. Everything written here
    decides whose card this client's usage lands on, so nothing may reach it on
    the strength of an id somebody posted at a URL.
    """
    stored = {"customer_id": customer_id, "subscription_id": subscription_id,
              "anchor_day": None}
    set_setting(db, CUSTOMER_ID_KEY, customer_id,
                "Stripe customer this account's usage is metered onto")
    if subscription_id:
        set_setting(db, SUBSCRIPTION_ID_KEY, subscription_id,
                    "Stripe subscription carrying the metered price")

    day = anchor_day(subscription) if subscription is not None else None
    window = subscription_cycle(subscription) if subscription is not None else None
    if day:
        set_setting(db, CYCLE_ANCHOR_DAY_KEY, str(day),
                    "Day of the month Stripe bills on — /usage reports the same window")
        if window:
            set_setting(db, CYCLE_ANCHOR_AT_KEY, window[0].isoformat(),
                        "First day of the Stripe billing period in force")
        stored["anchor_day"] = day
        logger.info("Stripe subscription %s stored | cycle anchored on day %s",
                    subscription_id, day)
    else:
        # Not an error worth refusing over: the customer id is the part that
        # decides where the money goes, and the cycle keeps working on
        # BILLING_CYCLE_DAY. But it is a disagreement waiting to happen, so it
        # is loud in the log rather than absent from it.
        logger.error("Stripe subscription %s carried no billing period; /usage "
                     "will keep reporting the configured cycle day and may "
                     "disagree with the invoice", subscription_id)
    return stored


def customer_id(db: Session) -> Optional[str]:
    return get_setting(db, CUSTOMER_ID_KEY)


def subscription_id(db: Session) -> Optional[str]:
    return get_setting(db, SUBSCRIPTION_ID_KEY)


# ─── A5: the webhook ────────────────────────────────────────────────────────


def verify_webhook(payload: bytes, signature: Optional[str]):
    """The event Stripe signed, or None.

    **Fails closed with no signing secret configured.** The tempting reading is
    "we have not set it up yet, so accept the payload" — and what an accepted
    payload writes is the customer id every segment this client sends is billed
    to. Anybody on the internet can post to this URL. A guard that is switched
    off refuses; it does not wave things through, which is the same rule that
    keeps an unscreened number unpromotable.
    """
    if not settings.STRIPE_WEBHOOK_SECRET:
        logger.error("Stripe webhook received with no STRIPE_WEBHOOK_SECRET "
                     "configured — payload ignored, nothing stored")
        return None
    if not signature:
        logger.error("Stripe webhook received with no signature header — ignored")
        return None
    try:
        return api().construct_webhook_event(payload, signature,
                                             settings.STRIPE_WEBHOOK_SECRET)
    except Exception as exc:
        logger.error("Stripe webhook signature rejected: %s", exc)
        return None


def _event_field(event, *path):
    """Read `event.data.object.x` off either a StripeObject or a plain dict."""
    node = event
    for key in path:
        if node is None:
            return None
        node = node.get(key) if isinstance(node, dict) else getattr(node, key, None)
    return node


def handle_checkout_completed(db: Session, event) -> dict:
    """Store the customer — but only for a session that carries *our* price.

    Returns a small verdict rather than raising, so the route can answer 200 to
    a legitimately-unrelated event without pretending it acted on it.
    """
    session = _event_field(event, "data", "object")
    if session is None:
        return {"stored": False, "reason": "no session on the event"}

    session_id = session.get("id") if isinstance(session, dict) else getattr(session, "id", None)
    if not session_is_ours(session_id):
        logger.info("Checkout session %s does not carry this product's metered "
                    "price — ignored", session_id)
        return {"stored": False, "reason": "not this product"}

    customer = (session.get("customer") if isinstance(session, dict)
                else getattr(session, "customer", None))
    subscription_ref = (session.get("subscription") if isinstance(session, dict)
                        else getattr(session, "subscription", None))
    customer = _id_of(customer)
    subscription_ref = _id_of(subscription_ref)
    if not customer:
        logger.error("Checkout session %s completed with no customer", session_id)
        return {"stored": False, "reason": "no customer on the session"}

    subscription = None
    if subscription_ref:
        try:
            subscription = api().retrieve_subscription(subscription_ref)
        except Exception as exc:
            logger.error("Could not read subscription %s: %s", subscription_ref, exc)

    stored = store_subscription(db, customer, subscription_ref, subscription)
    stored["stored"] = True
    return stored


def _id_of(value):
    """Stripe expands a reference into an object or leaves it as a string id."""
    if value is None or isinstance(value, str):
        return value
    return value.get("id") if isinstance(value, dict) else getattr(value, "id", None)


# ─── The subscribe page's own state ─────────────────────────────────────────


def account_status(db: Session) -> dict:
    """What `/subscribe` renders. No figure here that is not the client's.

    `pricing_table()` is the one source of the plan; this adds only whether the
    account is connected and to what. The rate and the allowance are never
    written into the template — the reason `usage.html` has no number in it.
    """
    return {
        "available": configured(),
        "subscribed": bool(customer_id(db)),
        "unavailable_notice": None if configured() else NOT_CONNECTED,
        "cycle_anchor": get_setting(db, CYCLE_ANCHOR_AT_KEY),
        # The day on its own, and its suffix. "Your month runs from the
        # 2026-09-09 onwards" is a timestamp read aloud; "starts on the 9th" is
        # the sentence a person would write. Same rule as `clears_at_clock()` —
        # one function computes the moment, one renders it.
        "cycle_anchor_day": billing_service.cycle_day(db),
        "cycle_anchor_ordinal": ordinal_suffix(billing_service.cycle_day(db)),
        "plan": billing_service.pricing_table(db),
    }


def ordinal_suffix(day: int) -> str:
    """"th" for 11-13 and everything else, "st"/"nd"/"rd" where they belong."""
    if 11 <= day % 100 <= 13:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")


def dumps(value) -> str:
    """One JSON spelling for the settings rows this module and the meter write."""
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def loads(raw: Optional[str]):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None
