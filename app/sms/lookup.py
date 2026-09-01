"""Line-type lookup — the gate that makes scraping economic.

Directory scrapers return business main lines, and a business main line is
usually a landline. One live campaign on this client's own list produced 2,526
"not routable" failures — 39% of a 6,857-message send, every one of them paid
for, every one of them still on the list the next morning. A discovery pipeline
without line-type screening in front of it manufactures that.

A carrier lookup answers "can this number receive a text" for about $0.0025.
Screening 10,000 scraped numbers costs $25 once. That is the single cheapest
thing in this whole plan, which is why it goes in front of everything.

## Why the interface is here and the cache is not

This module talks to a carrier and knows a carrier's vocabulary, so it lives in
`app/sms/` under that directory's rule: **no imports from `app.models` or
`app.services`**. The persistent cache is a database concern and lives in
`app/services/lookup_service.py`, which is the only caller of anything here.
That is the same split as `app/sms/factory.py` and the send path, and it is why
the SMS engine survived being moved between clients.

## Why the default provider still makes no call

`DisabledLineTypeProvider` is the default and answers `unknown` for everything
without touching a network. Session P1b ruled on escalation item 7 and wired the
carrier implementation in beside it, but the *default* did not move: switching
screening on is one line of `.env` on a live box and it spends real money, so it
stays a human's decision rather than a consequence of deploying.

`unknown` is not promote-eligible, so a box with screening switched off does not
quietly promote landlines — it holds everything in the review queue and says
why. A gate that is off refuses; it does not wave things through.

## Why the price lives here

The carrier charges per call, so what a call costs is part of the carrier
conversation, exactly as the vocabulary it answers in is. It is defined once,
here, and `app/services/lookup_service.py` re-exports it: the provider needs it
to report what it spent and cannot import a service, and the spend cap needs it
to decide whether one more call fits under the ceiling. Two copies of a rate is
how a cap comes to disagree with the ledger it is capping.

**It is our cost, not the client's**, on the same footing as
`WHOLESALE_COST_PER_SEGMENT`. He is billed per segment and for nothing else.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
import logging

from app.core.config import settings

logger = logging.getLogger("lookup")

# What a carrier can answer, and the vocabulary every provider normalises into.
# `unknown` is a first-class member rather than a null: "we asked and could not
# tell" and "we have not asked" are both states this pipeline holds numbers in,
# and neither is a line type we may act on.
LINE_TYPES = ("mobile", "voip", "landline", "toll_free", "unknown")


@dataclass(frozen=True)
class LineTypeResult:
    """One provider's answer about one number.

    `ok` is whether the provider *answered*, not whether the answer was good
    news. A landline is a successful lookup. A timeout is not, and the two must
    not both arrive as `unknown` with no way to tell them apart — the cache
    keeps answers forever and retries failures, so collapsing them would either
    freeze an outage into the data or re-bill every landline in the list.
    """

    line_type: str
    ok: bool
    error: str = ""
    cost: str = ""            # Decimal as text, or "" when nothing was spent

    def __post_init__(self):
        if self.line_type not in LINE_TYPES:
            raise ValueError(
                f"{self.line_type!r} is not a line type. A provider must "
                f"normalise into {', '.join(LINE_TYPES)} rather than passing a "
                f"carrier's own spelling through."
            )


class LineTypeProvider(ABC):
    """Base class for anything that can answer "what kind of number is this".

    One number per call, deliberately. Batching is the cache's job — see
    `lookup_service.screen()` — and a provider that batched internally would
    make the call count this pipeline is measured on impossible to assert.
    """

    name: str = "base"

    @abstractmethod
    def lookup(self, phone: str) -> LineTypeResult:
        """Answer for one E.164 number. Must not raise; return ok=False instead.

        A provider that raises takes the whole screening pass with it, and a
        screening pass is the thing standing between a scrape and the contact
        list. Failing one number is cheap; failing the run is not.
        """


class DisabledLineTypeProvider(LineTypeProvider):
    """The default. Answers `unknown`, spends nothing, calls nothing.

    Not a stub for tests — tests inject their own fake so they can count calls.
    This is the honest behaviour of a box that has not been given a screening
    credential, and `unknown` keeps every such number in the review queue
    instead of promoting it.
    """

    name = "disabled"

    # What the review queue tells a client whose prospects are all unscreened.
    # Named cause, named remedy, in his units — the shape decision 006 requires
    # of any refusal, and it names a setting rather than a carrier.
    DETAIL = ("Number screening is not switched on, so no prospect can be "
              "promoted yet. Until it is, every number here is unscreened.")

    def lookup(self, phone: str) -> LineTypeResult:
        return LineTypeResult(line_type="unknown", ok=False, error=self.DETAIL)


def cost_per_lookup() -> Decimal:
    """Our per-call spend, as Decimal. Never rendered — see the module docstring.

    Decimal from end to end rather than at the rounding step: session 1b's
    lesson is that float arithmetic on money drifts below half-cent boundaries,
    and both a job cost and a monthly cap are sums of several thousand of these.
    """
    return Decimal(str(settings.PROSPECT_LOOKUP_COST_PER_NUMBER))


# Import paths rather than classes, which `_load()` resolves at call time.
# `app.sms.providers.telnyx_lookup` imports `LineTypeProvider` from this module,
# so naming the class here directly would be a circular import — and the send
# factory carries the same indirection for the same shape of reason.
PROVIDERS = {
    "none": "app.sms.lookup:DisabledLineTypeProvider",
    "telnyx": "app.sms.providers.telnyx_lookup:TelnyxLineTypeProvider",
}

_instance: LineTypeProvider | None = None


def _load(path: str) -> type:
    module_path, class_name = path.split(":")
    module = __import__(module_path, fromlist=[class_name])
    return getattr(module, class_name)


def get_lookup_provider(force_reload: bool = False) -> LineTypeProvider:
    """The configured line-type provider (cached).

    Degrades to the disabled provider on an unrecognised name, a missing SDK or
    a missing credential rather than raising, for the reason `get_provider()`
    does: this is read from `.env` on a live box, and a typo should cost
    screening, not the ability to log in.

    The degraded state is safe here in a way it is not on the send path —
    `unknown` promotes nobody, so the failure mode is a queue that will not
    empty rather than a campaign that silently goes nowhere — and no client
    surface describes it either way: the queue tells him a number is unscreened,
    which is true whether screening is switched off or broken. It is logged at
    ERROR so the difference is recoverable by whoever has to fix it.
    """
    global _instance
    if _instance is not None and not force_reload:
        return _instance

    name = (settings.PROSPECT_LOOKUP_PROVIDER or "none").lower()
    try:
        if name not in PROVIDERS:
            raise LookupError(
                f"Unknown PROSPECT_LOOKUP_PROVIDER '{name}'. "
                f"Options: {', '.join(PROVIDERS)}"
            )
        instance = _load(PROVIDERS[name])()
    except Exception as exc:
        logger.error(
            "Could not start the line-type provider %r: %s. Falling back to no "
            "screening — every prospect will read as unscreened and none can be "
            "promoted.", name, exc,
        )
        instance = _load(PROVIDERS["none"])()

    _instance = instance
    logger.info("Line-type provider active: %s", _instance.name)
    return _instance
