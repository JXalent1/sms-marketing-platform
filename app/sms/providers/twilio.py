"""Twilio provider.

Kept as a working alternative and as a migration path. Twilio's `num_segments`
is only populated after the message is queued, so it may come back None on the
send call — the campaign engine falls back to the local segment estimate when
that happens.
"""

from typing import Optional
from app.core.config import settings
from app.sms.base import SMSProvider, SendResult

try:
    from twilio.rest import Client
    TWILIO_AVAILABLE = True
except ImportError:
    TWILIO_AVAILABLE = False


class TwilioProvider(SMSProvider):
    name = "twilio"

    def __init__(self):
        if not TWILIO_AVAILABLE:
            raise ImportError("Twilio SDK not installed. Run: pip install twilio")
        if not settings.TWILIO_ACCOUNT_SID:
            raise ValueError("TWILIO_ACCOUNT_SID is not set")

        self.client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
        self.from_number = settings.TWILIO_PHONE_NUMBER

    async def send(self, to: str, text: str) -> SendResult:
        try:
            if not to.startswith("+"):
                to = f"+{to}"

            msg = self.client.messages.create(body=text, from_=self.from_number, to=to)
            parts = getattr(msg, "num_segments", None)

            return SendResult(
                success=True,
                message_id=msg.sid,
                parts=int(parts) if parts else None,
                raw={"status": msg.status, "to": to, "from": self.from_number},
            )
        except Exception as e:
            return SendResult(success=False, error=str(e))

    async def get_balance(self) -> Optional[float]:
        try:
            balance = self.client.api.v2010.balance.fetch()
            return float(balance.balance)
        except Exception:
            return None

    async def get_message_status(self, message_id: str) -> Optional[str]:
        try:
            return self.client.messages(message_id).fetch().status
        except Exception:
            return None
