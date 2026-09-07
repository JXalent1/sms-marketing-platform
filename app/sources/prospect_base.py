"""Prospect sources — the pluggable discovery layer.

The same seam as `app/sources/base.py`, one table over. Read that file's module
docstring before adding a source here: the reference system wired a 930-line
Playwright scraper straight into the models, the dashboard and the scheduler, so
reusing it for another client meant deleting a third of the app. Nothing below
is allowed to reintroduce that.

To add a source:
    1. Subclass ProspectSource, implement fetch().
    2. Implement cleanup() if it holds anything a process can leak.
    3. Register it in app/sources/__init__.py.
    4. Run it through app/services/scrape_runner.py — never directly.

## The three things a source may not do

**A source never touches the database.** `fetch()` yields records; `ingest()`
below hands them to `prospect_ingest`, which normalises, dedups, suppresses and
persists. A source that writes a row has taken the dedup guarantee, the
permanent-rejection check and the provenance requirement into itself, where the
next source will not inherit any of them.

**A source never decides who is a buyer, either.** The never-prospect list —
other auction houses, estate liquidators, appraisers — is matched in
`prospect_ingest.record_prospect()`, on the same rule: a guard with one call
site is a guard on one path, and every source written after this one has to
inherit it rather than remember it.

**A source never decides whether something is textable.** Line type is looked up
by `lookup_service` after ingestion, behind a cache, once per number. A source
that filtered on its own guess would be spending the client's send budget on an
opinion, and it would be doing it in the one place nothing else can see.

## The buyer rationale is not optional, and this is where that is enforced

Every search term carries a written claim about why those people would raise a
paddle — "food trucks buy used prep equipment" is something a human can disagree
with. A record arriving without one is counted `invalid` and is not persisted.

That is the first of the three places the plan of record enforces "buyers, never
sellers". The second is the review queue showing the rationale on every row; the
third is `seller_or_consignor` and `competitor` being first-class reject reasons
that suppress permanently. Refusing here is what makes the other two honest: a
term with no rationale cannot reach a reviewer, so a reviewer is never asked to
judge a search nobody justified.

## Cooperative cancellation

The runner sets `stop_requested` when a job passes its deadline. A well-behaved
`fetch()` checks `self.should_stop()` in its loop and returns. A source that
cannot — one blocked inside a third-party call — is abandoned by the runner and
its `cleanup()` is still called, which is why `cleanup()` and not the loop is
the thing that must actually release the browser context, the socket or the
driver process.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from threading import Event
from typing import Iterable, Optional
import logging

logger = logging.getLogger("prospects")


@dataclass
class ProspectRecord:
    """One discovered business, before normalisation.

    `search_term` and `buyer_rationale` are required arguments rather than
    defaulted ones so that forgetting them is a TypeError at the source, not an
    `invalid` count somebody reads a week later.

    **`source_url` is shown to the client and goes into the CSV export.** It is
    the *human-facing* page or search this record came from — a listing, a
    directory entry, a search results URL somebody could open. It is never the
    API endpoint that produced it, because those carry credentials in the query
    string, and a key in an export is a key on somebody's desktop. `raw_payload`
    is the opposite: retained for tracing, never serialized to a client surface.
    """

    phone: str
    source_url: str
    search_term: str
    buyer_rationale: str
    business_name: Optional[str] = None
    address: Optional[str] = None
    category_slug: Optional[str] = None
    category_confidence: Optional[float] = None
    distance_miles: Optional[float] = None
    raw_payload: dict = field(default_factory=dict)


@dataclass
class ProspectIngestResult:
    """One counter per outcome `record_prospect()` can answer.

    Six of them, and none is a spelling of another. "Nobody justified this
    term", "this business is an auction house", "a reviewer already said no"
    and "the client already has this number" are four different diagnoses with
    four different remedies, and a source that reported them as one number
    would be telling whoever reads the job row nothing they can act on.
    """

    total: int = 0
    created: int = 0
    corroborated: int = 0
    suppressed: int = 0
    excluded: int = 0
    known: int = 0
    invalid: int = 0

    def as_dict(self) -> dict:
        return {
            "total": self.total,
            "created": self.created,
            "corroborated": self.corroborated,
            "suppressed": self.suppressed,
            "excluded": self.excluded,
            "known": self.known,
            "invalid": self.invalid,
        }


class ProspectSource(ABC):
    """Base class for anything that discovers businesses."""

    name: str = "base"
    description: str = ""

    def __init__(self):
        self.stop_requested = Event()

    # ─── The contract a subclass implements ─────────────────────────────────

    @abstractmethod
    def fetch(self, **kwargs) -> Iterable[ProspectRecord]:
        """Yield ProspectRecords. A generator, for anything that pages."""

    def cleanup(self) -> None:
        """Release everything this source acquired. Called in a `finally`.

        Default is a no-op because a source with nothing to release should not
        have to say so. A source that drives a browser, holds a socket or spawns
        a process overrides this, and it must be safe to call twice and safe to
        call on a run that never started — the runner calls it on every exit
        path including the one where `fetch()` raised on its first line.
        """

    # ─── Cancellation ───────────────────────────────────────────────────────

    def should_stop(self) -> bool:
        return self.stop_requested.is_set()

    def request_stop(self) -> None:
        self.stop_requested.set()

    # ─── Persistence, which the base owns ───────────────────────────────────

    def ingest(self, db, records: Iterable[ProspectRecord] = None, job=None,
               **kwargs) -> ProspectIngestResult:
        """Persist records. Fetches them itself when it is not handed any.

        Deliberately thin, and deliberately the *only* path from a source into
        the database. Every rule that protects the contact list — E.164
        normalisation, the unique number, the permanent rejection check, the
        non-nullable provenance — lives on the other side of this call, so a new
        source inherits all of them without knowing any of them exist.

        `records` exists because `scrape_runner` has already drained `fetch()`
        by the time it persists: it runs the fetch under a deadline, on another
        thread, and keeps whatever arrived before the deadline passed. Without
        this argument the runner would need its own loop over
        `record_prospect()` — a second call site for the rules above, which is
        how a guard ends up applying on one path and not the other.
        """
        from app.services import prospect_ingest

        result = ProspectIngestResult()
        for record in (self.fetch(**kwargs) if records is None else records):
            result.total += 1
            outcome = prospect_ingest.record_prospect(
                db, record, source_name=self.name, job=job)
            # Named rather than setattr'd blind: an outcome this class has never
            # heard of would otherwise create a counter nobody reads and be
            # reported as zero of everything, which reads as a source that found
            # nothing rather than as a bug.
            if outcome not in prospect_ingest.RECORD_OUTCOMES:
                raise ValueError(
                    f"record_prospect returned {outcome!r}; the outcomes this "
                    f"result can count are {prospect_ingest.RECORD_OUTCOMES}")
            setattr(result, outcome, getattr(result, outcome) + 1)
            if self.should_stop():
                logger.info("[%s] stop requested after %d records",
                            self.name, result.total)
                break

        logger.info("[%s] ingest complete: %s", self.name, result.as_dict())
        return result
