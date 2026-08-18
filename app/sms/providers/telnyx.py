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

try:
    import telnyx
    TELNYX_AVAILABLE = True
except ImportError:                     # keep the app importable without the SDK
    TELNYX_AVAILABLE = False


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

            return SendResult(
                success=True,
                message_id=str(getattr(data, "id", "")) or None,
                parts=getattr(data, "parts", None),
                raw={"to": to, "from": self.from_number},
            )
        except Exception as e:
            return SendResult(success=False, error=str(e))

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
