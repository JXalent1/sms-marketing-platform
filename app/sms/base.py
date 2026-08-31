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

  `cost`    What the carrier says this message cost, and the rate/carrier-fee
            split behind it. Strings, exactly as reported. Providers returned
            these from the first day and this contract discarded them, so
            `campaigns.estimated_cost` — which exists to be reconciled against
            an invoice — had nothing to reconcile against for the whole of the
            build. OUR cost, never the client's: see app/models/sms_message.py.
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

    # Money, as strings. Not floats: these are summed across thousands of rows
    # to reconcile against an invoice, and a float puts a binary expansion
    # between the carrier's figure and ours before the addition even starts.
    # None where the provider did not say — which for most carriers is the
    # ordinary case on the send call, because the final cost is known at
    # delivery. None is the honest value; 0 would read as "free".
    cost: Optional[str] = None            # total for this message
    cost_rate: Optional[str] = None       # the carrier's own rate component
    cost_carrier_fee: Optional[str] = None   # per-carrier pass-through
    cost_currency: Optional[str] = None   # "USD"


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
