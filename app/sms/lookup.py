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

## Why the default provider makes no call

`DisabledLineTypeProvider` is the default and answers `unknown` for everything
without touching a network. Two reasons, and neither is timidity:

  - **A carrier lookup is money.** Wiring a paid API into the product is a
    budget decision, not an implementation one — `RULES.md` escalation item 7.
    The carrier implementation lands with the first real source (P2), when
    there is a search to spend it on.
  - **A provider class nobody can run is a runtime contract nobody checked.**
    This codebase has already shipped a pinned SDK major version the provider
    was written against and did not match; nothing failed until the morning of a
    sale. An unexercised second one would be the same bet.

`unknown` is not promote-eligible, so a box with screening switched off does not
quietly promote landlines — it holds everything in the review queue and says
why. A gate that is off refuses; it does not wave things through.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
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


PROVIDERS = {
    "none": DisabledLineTypeProvider,
}

_instance: LineTypeProvider | None = None


def get_lookup_provider(force_reload: bool = False) -> LineTypeProvider:
    """The configured line-type provider (cached).

    Degrades to the disabled provider on an unrecognised name rather than
    raising, for the reason `get_provider()` does: this is read from `.env` on a
    live box, and a typo should cost screening, not the ability to log in. The
    degraded state is safe here in a way it is not on the send path — `unknown`
    promotes nobody — but it is still logged at ERROR so it is not silent.
    """
    global _instance
    if _instance is not None and not force_reload:
        return _instance

    name = (settings.PROSPECT_LOOKUP_PROVIDER or "none").lower()
    if name not in PROVIDERS:
        logger.error(
            "Unknown PROSPECT_LOOKUP_PROVIDER %r. Options: %s. Falling back to "
            "no screening — every prospect will read as unscreened and none can "
            "be promoted.", name, ", ".join(PROVIDERS),
        )
        name = "none"

    _instance = PROVIDERS[name]()
    logger.info("Line-type provider active: %s", _instance.name)
    return _instance
