"""Telnyx provider.

Notes from running ~200k messages through this:

  - `data.parts` is the carrier's own segment count. Store it; it is the billing
    truth and it is the only way to catch emoji/UCS-2 cost blowouts.
  - A 200 from the send API means Telnyx ACCEPTED the message, not that a carrier
    delivered it. Delivery arrives asynchronously on the webhook. Treating
    acceptance as delivery hides carrier spam-blocks completely.
  - Error 20012 "Account inactive" means the balance hit zero mid-campaign. It is
    not a code bug and no retry fixes it. See get_balance() and the pre-flight
    check in campaign_service.
"""

from typing import Optional
from app.core.config import settings
from app.sms.base import SMSProvider, SendResult
from app.sms.phone import strip_payload

try:
    import telnyx
    TELNYX_AVAILABLE = True
except ImportError:                     # keep the app importable without the SDK
    TELNYX_AVAILABLE = False


def describe_send_error(exc: Exception) -> str:
    """One line describing a failed send, assembled from named fields.

    This used to be `str(exc)`, and the SDK's `__str__` for an API error is
    `"Error code: 400 - {'errors': [{'code': '10002', 'title': ..., 'detail':
    ...}]}"` — the response body's dict repr. Two of those are sitting in
    `sms_messages.error_message` on the live box, and since session 5d that
    column is the source of `blocked_numbers.notes`, which the client reads on
    the Opt-outs page. `detail` on a destination error is precisely where a
    recipient's phone number would appear.

    So: read the fields by name, and never let the payload itself through.
    Duck-typed on `.body` rather than caught by SDK exception class, because
    this module has to stay importable when the SDK is absent and because the
    attribute has outlived two of the SDK's class hierarchies.

    Scrubbing is not done here. The caller writes through
    `scrub_provider_text()` like every other client-visible string, and doing it
    twice would put a second definition of "client-safe" in the codebase.
    """
    body = getattr(exc, "body", None)
    errors = body.get("errors") if isinstance(body, dict) else None
    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        first = errors[0]
        parts = [str(first.get(field)).strip()
                 for field in ("title", "detail")
                 if first.get(field)]
        if parts:
            return ": ".join(parts)
        code = first.get("code")
        if code:
            return f"Carrier error {code}"

    # No structured body — a timeout, a DNS failure, a bug in our own call.
    # `strip_payload()` is the backstop for an SDK that puts JSON in the message
    # and nowhere else.
    return strip_payload(str(exc)) or exc.__class__.__name__


def _money(node) -> tuple:
    """(amount, currency) from a `{amount, currency}` cost node, as strings.

    Duck-typed and defensive for `describe_send_error()`'s reason: the SDK has
    changed its class hierarchy twice inside this project's lifetime, and this
    runs inside the send loop where an AttributeError costs a message. A cost we
    could not read is None, which reads as "the carrier did not say" rather than
    as "free".

    Both dict and attribute access are handled because the SDK returns model
    objects on the send call and plain dicts on the webhook payload, and both
    carry the same two keys.
    """
    if node is None:
        return None, None
    if isinstance(node, dict):
        amount, currency = node.get("amount"), node.get("currency")
    else:
        amount, currency = getattr(node, "amount", None), getattr(node, "currency", None)
    amount = str(amount).strip() if amount is not None else None
    currency = str(currency).strip() if currency else None
    return (amount or None), currency


class TelnyxProvider(SMSProvider):
    name = "telnyx"

    def __init__(self):
        if not TELNYX_AVAILABLE:
            raise ImportError("Telnyx SDK not installed. Run: pip install telnyx")
        if not settings.TELNYX_API_KEY:
            raise ValueError("TELNYX_API_KEY is not set")

        self.client = telnyx.Telnyx(api_key=settings.TELNYX_API_KEY)
        self.from_number = settings.TELNYX_PHONE_NUMBER
        self.messaging_profile_id = settings.TELNYX_MESSAGING_PROFILE_ID

    async def send(self, to: str, text: str) -> SendResult:
        try:
            if not to.startswith("+"):
                to = f"+{to}"

            kwargs = {"from_": self.from_number, "to": to, "text": text}
            if self.messaging_profile_id:
                kwargs["messaging_profile_id"] = self.messaging_profile_id

            response = self.client.messages.send(**kwargs)
            data = response.data if hasattr(response, "data") else response

            cost = _money(getattr(data, "cost", None))
            breakdown = getattr(data, "cost_breakdown", None)
            return SendResult(
                success=True,
                message_id=str(getattr(data, "id", "")) or None,
                parts=getattr(data, "parts", None),
                cost=cost[0],
                cost_currency=cost[1],
                cost_rate=_money(getattr(breakdown, "rate", None))[0],
                cost_carrier_fee=_money(getattr(breakdown, "carrier_fee", None))[0],
                raw={"to": to, "from": self.from_number},
            )
        except Exception as e:
            return SendResult(success=False, error=describe_send_error(e))

    async def get_balance(self) -> Optional[float]:
        """Current account balance in USD.

        Telnyx has no credit buffer by default (credit_limit $0), so a balance of
        zero deactivates the account instantly and every remaining send in a
        campaign fails with 20012.
        """
        import urllib.request
        import json
        try:
            req = urllib.request.Request(
                "https://api.telnyx.com/v2/balance",
                headers={"Authorization": f"Bearer {settings.TELNYX_API_KEY}"},
            )
            with urllib.request.urlopen(req, timeout=30) as r:
                return float(json.load(r)["data"]["balance"])
        except Exception:
            return None

    async def get_message_status(self, message_id: str) -> Optional[str]:
        """Authoritative per-message status — this is what reveals carrier
        spam-blocks that the send call reported as success."""
        try:
            response = self.client.messages.retrieve(id=message_id)
            to = response.data.to
            return to[0].status if to else None
        except Exception:
            return None
