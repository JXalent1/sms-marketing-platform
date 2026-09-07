"""The monthly ceiling on paid discovery-API requests.

The second of two independent meters. `lookup_service` caps what the **carrier**
charges per number; this caps what the **discovery API** charges per request,
and they are separate because they are separate bills with separate units — one
request returns up to twenty businesses, so the two ceilings can never be one
number.

## The shape is `LookupBudget`'s, deliberately

Read from the database once, then decremented in memory and handed to the code
that makes the calls. That is not only an optimisation. **A source may not touch
the database**, and the paging loop that has to check the ceiling lives inside
`fetch()` — so the runner reads the meter, passes this object in, and the source
spends against it without ever holding a Session. Same seam as `unusable` and
`budget` being handed down into `line_type_for()`.

The consequence to keep in mind: **this object is mutated on the source's worker
thread and read on the runner's.** `charge()` takes a lock, and the counters are
plain integers so a torn read is not possible on the way out.

## Zero means off, not unlimited

`GOOGLE_PLACES_MONTHLY_REQUEST_CAP <= 0` refuses every request. That reading is
the same asymmetry the rest of this pipeline runs on and it is worth stating
where somebody might "fix" it: a cap misread as *off* leaves a review queue that
stops filling, and a cap misread as *unlimited* spends money nobody approved on
an API billed at $35 per thousand calls. A guard that is switched off refuses.

## What a refusal must not do

Stop the job dead, lose what it found, or report success. The source stops
asking for more pages, everything already produced is persisted, and the count
of refused requests is written to `scrape_jobs.api_requests_skipped` — a column
rather than only a log line, on `cleanup_ran`'s precedent, because "this search
is incomplete rather than exhausted" has to be something you can query for.

## The cost is ours

`api_cost` sits on the same footing as `WHOLESALE_COST_PER_SEGMENT` and
`phone_lookups.cost`: the client is billed per segment and for nothing else, and
what a discovery run costs us appears on no invoice of his and reaches no
response body, template or export.
"""

from datetime import datetime
from decimal import Decimal
from threading import Lock
from typing import Optional
import logging

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.scrape import ScrapeJob

logger = logging.getLogger("prospects")


def _places_terms() -> dict:
    return {
        "cap": int(settings.GOOGLE_PLACES_MONTHLY_REQUEST_CAP),
        "price_per_1000": Decimal(str(settings.GOOGLE_PLACES_COST_PER_1000_REQUESTS)),
        "free_per_month": int(settings.GOOGLE_PLACES_FREE_REQUESTS_PER_MONTH),
    }


# Which sources cost money per request, and what that money is. A source absent
# from this map is free to run — a CSV file or a public licence register — and
# gets no budget at all rather than an unlimited one, because a meter nobody
# reads is worse than no meter.
PAID_SOURCES = {
    "google_places": _places_terms,
}


def spend_this_month(db: Session, source_name: str,
                     month: Optional[str] = None) -> int:
    """Requests this source has already made in the calendar month.

    Attributed by `scrape_jobs.started_at`, which has exactly one writer
    (`scrape_runner.run_job()`, `datetime.now()`) and no server default — so
    there is no second clock and no second ISO spelling to make a `LIKE` on the
    month quietly wrong. A job that starts on the 31st and runs past midnight
    counts against the month it started in, which is the month somebody decided
    to run it.
    """
    month = month or datetime.now().strftime("%Y-%m")
    rows = (db.query(ScrapeJob.api_requests)
            .filter(ScrapeJob.source == source_name,
                    ScrapeJob.api_requests.isnot(None),
                    ScrapeJob.started_at.like(f"{month}%"))
            .all())
    return sum(int(count or 0) for (count,) in rows)


class RequestBudget:
    """How many paid requests are left this month, and what they have cost."""

    def __init__(self, source_name: str, cap: int, spent: int,
                 price_per_1000: Decimal, free_per_month: int):
        self.source_name = source_name
        self.cap = cap
        self.opening = spent          # what earlier jobs this month already made
        self.spent = spent            # opening + this run's
        self.price_per_1000 = price_per_1000
        self.free_per_month = free_per_month
        self.skipped = 0
        self._lock = Lock()

    @classmethod
    def for_month(cls, db: Session, source_name: str,
                  month: Optional[str] = None) -> Optional["RequestBudget"]:
        terms = PAID_SOURCES.get(source_name)
        if terms is None:
            return None
        return cls(source_name, spent=spend_this_month(db, source_name, month),
                   **terms())

    # ─── The ceiling ────────────────────────────────────────────────────────

    @property
    def remaining(self) -> int:
        return self.cap - self.spent

    def allows(self) -> bool:
        """Is there room for one more request?

        Deliberately one expression, and deliberately `<` rather than `<=`: a
        request is affordable when the spend *before* it is strictly under the
        ceiling. A cap of zero therefore refuses because no request fits under a
        ceiling of zero, not because a special case says so.

        The obvious spelling — an `if self.cap <= 0: return False` above
        `self.remaining > 0` — was here first and `agent/mutate-P2.py` caught it
        as dead code: with an integer counter and a non-negative spend there is
        no arrangement in which that `if` decides anything the comparison below
        it would not have decided the same way. It read like a guard and was a
        comment with an `if` in front of it. This is P1b's lesson about a guard
        no test can reach, arriving one column over — the fix there was to
        construct the arrangement that reaches it, and here there is none.

        The rule it stated is still the rule: **zero or less is off, not
        unlimited.** A misread cap that stops discovery leaves a review queue
        that stops filling; a misread cap that permits it spends money nobody
        approved at $35 a thousand calls.
        """
        return self.spent < self.cap

    def charge(self, requests: int = 1) -> None:
        with self._lock:
            self.spent += requests

    def refuse(self, count: int = 1) -> None:
        with self._lock:
            self.skipped += count

    # ─── What this run did ──────────────────────────────────────────────────

    @property
    def requests_made(self) -> int:
        return self.spent - self.opening

    @property
    def charged(self) -> Decimal:
        """What THIS run cost us, in dollars, as Decimal.

        The free allowance is a property of the calendar month, not of the run,
        so it is applied against the month's running total: requests below
        `free_per_month` cost nothing and everything above it is charged. A run
        that straddles the allowance is billed for the part that crossed it,
        which is what the invoice will say.

        Decimal end to end rather than at the rounding step — session 1b's
        lesson, and this is a sum of up to a thousand $0.035 items.
        """
        billable_before = max(0, self.opening - self.free_per_month)
        billable_after = max(0, self.spent - self.free_per_month)
        billable = billable_after - billable_before
        return (Decimal(billable) * self.price_per_1000) / Decimal(1000)

    def log_refusals(self) -> None:
        """One line for the whole run, naming the count and the remedy.

        Once for the pass rather than once per refused request, for the reason
        `screen()` logs once: a search plan with two hundred queries left would
        otherwise write two hundred identical lines. The operator cannot see
        this anywhere else — by design no client screen mentions our spend.
        """
        if not self.skipped:
            return
        logger.error(
            "Discovery stopped at the monthly request cap for %s: %d of %d "
            "requests used, %d search request(s) were not made and nothing was "
            "charged for them. They will run when the month rolls over, or "
            "sooner if GOOGLE_PLACES_MONTHLY_REQUEST_CAP is raised.",
            self.source_name, self.spent, self.cap, self.skipped)
