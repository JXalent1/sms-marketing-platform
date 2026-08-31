"""Shared setup for the session-P1 test modules.

Not a test module — the leading underscore keeps pytest from collecting it. Same
two rules as `_link_setup.py`:

**Leave no rows behind.** The suite shares one database and is deliberately one
end-to-end story. `test_smoke` asserts an exact `sent_count` against audience
"all", so a contact these modules forget to remove fails a module that has never
heard of them. `purge()` runs before and after every module here, and it removes
contacts these tests *promoted* as well as the prospects they created — a
promoted prospect is a contact, which is the whole point of the feature and
exactly the way this suite gets polluted.

**A permanent rejection is permanent, including across test modules.** That is
the feature. `purge()` therefore has to clear `prospect_rejections` too, or the
second run of a module finds its own numbers suppressed from the first and half
its assertions quietly pass for the wrong reason.

One rule of its own: **nothing here calls a paid API.** The line-type provider
is a fake that counts its calls, and `PROSPECT_LOOKUP_PROVIDER` is never
changed from its default — the fake is passed in explicitly, so no code path can
reach a real one by forgetting to patch something.
"""

from contextlib import contextmanager
from threading import Event

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.blocked_number import BlockedNumber
from app.models.category import ContactCategory
from app.models.contact import Contact
from app.models.prospect import Prospect, ProspectRejection, ProspectSighting
from app.models.scrape import PhoneLookup, ScrapeJob
from app.sms.lookup import LineTypeProvider, LineTypeResult
from app.sources.prospect_base import ProspectRecord, ProspectSource

# 555-555-10xx and 11xx. Distinct from test_smoke's 555-01xx, _guardrail_setup's
# 555-04xx, _campaign_flow_setup's 555-0700–0899 and _link_setup's 555-0900–0999,
# so no module's counts can move another's.
POOL = [f"+1555555{n:04d}" for n in range(1000, 1200)]
_allocated = []

CONTACT_SOURCE = "prospect"          # what promote() stamps on a new contact
TERM = "food trucks fort lauderdale"
RATIONALE = "Food trucks buy used prep and refrigeration to fit a kitchen on a budget."
SOURCE_URL = "https://example.test/search?q=food+trucks"


def take(count: int) -> list:
    """`count` phone numbers no other test in this run has used."""
    start = len(_allocated)
    if start + count > len(POOL):
        raise RuntimeError(
            f"the P1 phone pool is exhausted ({len(POOL)} numbers). Widen POOL — "
            f"do not reuse, or a later test inherits an earlier one's rejection, "
            f"which is permanent and would suppress it silently.")
    chosen = POOL[start:start + count]
    _allocated.extend(chosen)
    return chosen


def record(phone, *, name=None, term=TERM, rationale=RATIONALE,
           url=SOURCE_URL, category_slug="food_service", confidence=0.8,
           distance=12.0, payload=None) -> ProspectRecord:
    return ProspectRecord(
        phone=phone,
        source_url=url,
        search_term=term,
        buyer_rationale=rationale,
        business_name=name or f"Truck {phone[-4:]}",
        address="1 Test Way, Fort Lauderdale FL",
        category_slug=category_slug,
        category_confidence=confidence,
        distance_miles=distance,
        raw_payload=payload if payload is not None else {"place_id": f"p-{phone[-4:]}"},
    )


# ─── Fake sources ───────────────────────────────────────────────────────────

class FakeSource(ProspectSource):
    """Yields what it was given, and counts its own cleanup."""

    description = "test double"

    def __init__(self, records, name="fake-a"):
        super().__init__()
        self.name = name
        self.records = list(records)
        self.cleaned = 0

    def fetch(self, **kwargs):
        for item in self.records:
            yield item
            if self.should_stop():
                return

    def cleanup(self):
        self.cleaned += 1


class HangingSource(ProspectSource):
    """Yields one record, then blocks on something the runner cannot signal.

    It deliberately ignores `stop_requested`: this is the source stuck inside a
    third-party call, which is the case `cleanup()` exists for and the only case
    where abandoning the worker is the honest answer.

    `cleanup()` releases the block, which is both what a real source's cleanup
    does — closing the handle the call is waiting on — and what lets a test
    prove the release actually happened by watching the thread end.
    """

    name = "fake-hang"

    def __init__(self, records):
        super().__init__()
        self.records = list(records)
        self.released = Event()
        self.cleaned = 0

    def fetch(self, **kwargs):
        for item in self.records:
            yield item
        # 30s is a backstop against a bug in the harness leaving a thread for
        # the rest of the session; the test asserts the release happens in
        # about a second, not in thirty.
        self.released.wait(timeout=30)

    def cleanup(self):
        self.cleaned += 1
        self.released.set()


class ExplodingSource(ProspectSource):
    """Raises on the first record. Its cleanup must still run."""

    name = "fake-boom"

    def __init__(self):
        super().__init__()
        self.cleaned = 0

    def fetch(self, **kwargs):
        raise RuntimeError("the search page changed shape")
        yield          # pragma: no cover — makes fetch a generator

    def cleanup(self):
        self.cleaned += 1


# ─── The fake carrier lookup ────────────────────────────────────────────────

class CountingLookupProvider(LineTypeProvider):
    """Answers from a table and records every call it was asked to make.

    `calls` is the assertion the cache is measured on. Elapsed time would pass
    whether or not a cache existed; a call count is the only thing that says a
    number was not paid for twice.
    """

    name = "fake-lookup"

    def __init__(self, answers=None, default="mobile"):
        self.answers = dict(answers or {})
        self.default = default
        self.calls = []

    def lookup(self, phone: str) -> LineTypeResult:
        self.calls.append(phone)
        return LineTypeResult(line_type=self.answers.get(phone, self.default),
                              ok=True, cost="0.0025")


class FailingLookupProvider(LineTypeProvider):
    """Answers nothing. Used to prove a failure is not cached as an answer."""

    name = "fake-lookup-down"

    def __init__(self):
        self.calls = []

    def lookup(self, phone: str) -> LineTypeResult:
        self.calls.append(phone)
        return LineTypeResult(line_type="unknown", ok=False,
                              error="lookup service unavailable")


@contextmanager
def term_flag_rule(min_rejections: int, share: float):
    """Override the "this term finds sellers" thresholds for one test."""
    previous = (settings.PROSPECT_TERM_FLAG_MIN_REJECTIONS,
                settings.PROSPECT_TERM_FLAG_SHARE)
    settings.PROSPECT_TERM_FLAG_MIN_REJECTIONS = min_rejections
    settings.PROSPECT_TERM_FLAG_SHARE = share
    try:
        yield
    finally:
        (settings.PROSPECT_TERM_FLAG_MIN_REJECTIONS,
         settings.PROSPECT_TERM_FLAG_SHARE) = previous


def purge(db) -> None:
    """Remove every row these modules create. Runs before and after each."""
    prospect_ids = [p.id for p in db.query(Prospect).filter(Prospect.phone.in_(POOL))]

    for model, column in ((ProspectSighting, ProspectSighting.prospect_id),
                          (ProspectRejection, ProspectRejection.prospect_id)):
        if prospect_ids:
            db.query(model).filter(column.in_(prospect_ids)).delete(
                synchronize_session=False)
    # Keyed on the number as well, because a rejection is deliberately allowed
    # to outlive its prospect and a leftover one suppresses the next run.
    db.query(ProspectRejection).filter(ProspectRejection.phone.in_(POOL)).delete(
        synchronize_session=False)

    if prospect_ids:
        db.query(Prospect).filter(Prospect.id.in_(prospect_ids)).delete(
            synchronize_session=False)

    db.query(PhoneLookup).filter(PhoneLookup.phone.in_(POOL)).delete(
        synchronize_session=False)
    db.query(BlockedNumber).filter(BlockedNumber.phone.in_(POOL)).delete(
        synchronize_session=False)

    # Contacts these tests promoted. `test_smoke` counts audience "all".
    contact_ids = [c.id for c in db.query(Contact).filter(Contact.phone.in_(POOL))]
    if contact_ids:
        db.query(ContactCategory).filter(
            ContactCategory.contact_id.in_(contact_ids)).delete(
            synchronize_session=False)
        db.query(Contact).filter(Contact.id.in_(contact_ids)).delete(
            synchronize_session=False)

    # Jobs these tests ran. Their sightings are already gone above; anything
    # still pointing at one would be a foreign key violation on delete.
    db.query(ScrapeJob).filter(
        ScrapeJob.source.like("fake-%")).delete(synchronize_session=False)
    db.commit()


def purged_db_fixture_body():
    db = SessionLocal()
    try:
        purge(db)
        yield db
        purge(db)
    finally:
        db.close()
