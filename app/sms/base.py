"""The SMS provider contract.

Every provider returns the same shape, so the campaign engine never knows or
cares which carrier is behind it. Swapping Twilio for Telnyx in the reference
system was a one-line .env change because of this boundary — keep it intact.

Two fields matter more than they look:

  `parts`   The carrier's own segment count for the message. This is what the
            carrier bills you, so it is what you bill on. Never infer it from
            len(text)//160 when the provider will tell you the truth.

  `error`   The raw provider error string. Store it. Delivery post-mortems are
            impossible without it, and the auto-block rules key off it.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, Dict, Any


@dataclass
class SendResult:
    success: bool
    message_id: Optional[str] = None      # provider's ID, used to match delivery webhooks
    parts: Optional[int] = None           # carrier-reported segment count
    error: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)


class SMSProvider(ABC):
    """Base class for SMS providers.

    To add a provider: subclass this, implement send(), register it in
    app/sms/factory.py, and add its credentials to app/core/config.py.
    """

    name: str = "base"

    @abstractmethod
    async def send(self, to: str, text: str) -> SendResult:
        """Send one message. Must never raise — return SendResult(success=False)."""

    async def get_balance(self) -> Optional[float]:
        """Account balance in USD, or None if the provider has no balance API.

        Used by the pre-flight check that stops a campaign from starting when
        the account cannot fund it. Returning None disables that check.
        """
        return None

    async def get_message_status(self, message_id: str) -> Optional[str]:
        """Terminal delivery status for a message, if the provider exposes it."""
        return None
