"""Shared setup for the session-P2 test modules.

Not a test module — the leading underscore keeps pytest from collecting it. Same
two rules as `_prospect_setup.py`, plus one this session adds.

**Leave no rows behind.** The suite shares one database and is deliberately one
end-to-end story. `purge()` runs before and after every module here and removes
prospects, sightings, rejections, lookups, promoted contacts **and the
`scrape_jobs` rows** — the last of those matters more here than it did in P1,
because P2's two dedup mechanisms both read that table: the search ledger asks
"has this been searched recently" and the request meter asks "how much has this
month cost". A leftover job from another module would make either answer wrong,
and it would make it wrong in the direction where the test passes.

**A permanent rejection is permanent across modules**, so `purge()` clears
`prospect_rejections` too.

**Nothing here calls a paid API — either of them.** The line-type provider is a
fake that counts its calls, `PROSPECT_LOOKUP_PROVIDER` is never changed from its
default, and the Places client is a replay of the recorded-shape fixture file
that also counts its calls. A call count is the only thing that proves a dedup
worked; elapsed time would pass whether or not it existed.
"""

from contextlib import contextmanager
import json
import pathlib

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.blocked_number import BlockedNumber
from app.models.category import ContactCategory
from app.models.contact import Contact
from app.models.prospect import Prospect, ProspectRejection, ProspectSighting
from app.models.scrape import PhoneLookup, ScrapeJob
from app.sources import taxonomy
from app.sources.google_places import GooglePlacesSource

FIXTURES = json.loads(
    (pathlib.Path(__file__).parent / "fixtures" /
     "google_places_responses.json").read_text())

# +1 555-555-12xx and 13xx. Distinct from `_prospect_setup`'s 10xx/11xx block
# and from every other module's, so no module's counts can move another's.
POOL = [f"+1555555{n:04d}" for n in range(1200, 1400)]

SOURCE_NAME = GooglePlacesSource.name


def fixture(name: str) -> dict:
    """One recorded-shape response, copied so a test cannot mutate the file."""
    return json.loads(json.dumps(FIXTURES[name]))


def search_for(group_slug: str, term: str = None, sweep_name: str = None):
    """One `Search` out of the real plan, picked by group.

    Built from `taxonomy.searches()` rather than by constructing a `Search` by
    hand: a test that assembles its own plan is testing its own assembly, and
    the radius attached to a search is exactly the thing several of these
    modules are about.
    """
    for search in taxonomy.searches(group_slugs=[group_slug]):
        if term is not None and search.term != term:
            continue
        if sweep_name is not None and search.sweep.name != sweep_name:
            continue
        return search
    raise LookupError(f"no search in {group_slug} for {term!r}/{sweep_name!r}")


class ReplayPlacesClient:
    """Answers from the fixture file and records every request it was asked for.

    `calls` is what the dedup and cap criteria are asserted on. It holds one
    entry per HTTP request the source would have made, in order, with the query
    and the page token — so "a second run made no paid call" is a claim about a
    list rather than about a stopwatch.
    """

    def __init__(self, pages=None, error=None, calls=None):
        # {page_token or None: fixture name}. The first request of a search
        # carries no token, which is how the source's paging is exercised
        # rather than simulated.
        self.pages = dict(pages or {})
        self.error = error
        # Shared across the clients one `run_plan` builds, because the source
        # closes whatever client it holds and the plan makes a fresh source per
        # search. The claim being asserted is about the whole plan's spending.
        self.calls = [] if calls is None else calls
        self.closed = 0

    def search_text(self, query, *, page_size, page_token=None, bias=None):
        self.calls.append({"query": query, "page_token": page_token,
                           "page_size": page_size, "bias": bias})
        if self.error is not None:
            raise self.error
        name = self.pages.get(page_token)
        if name is None:
            return {"places": []}
        return fixture(name)

    def close(self):
        self.closed += 1


def source(pages=None, error=None, calls=None) -> GooglePlacesSource:
    """A `GooglePlacesSource` wired to a replay client. Never touches a network."""
    return GooglePlacesSource(
        client=ReplayPlacesClient(pages=pages, error=error, calls=calls))


def factory(pages=None, error=None):
    """A `source_factory` for `run_plan`, sharing one call log across its jobs.

    Returns `(make_source, calls)`. Every job in the plan gets its own source
    and its own client — which is what production does — while `calls` records
    every request all of them made, so "the second run made no paid call" is
    asserted on the plan rather than on one job of it.
    """
    calls = []

    def make():
        return source(pages=pages, error=error, calls=calls)

    return make, calls


@contextmanager
def caps(*, requests: int = None, repeat_days: int = None,
         max_pages: int = None, lookup_cap: float = None):
    """Override the spend and repeat settings for one test, and put them back."""
    previous = (settings.GOOGLE_PLACES_MONTHLY_REQUEST_CAP,
                settings.GOOGLE_PLACES_QUERY_REPEAT_DAYS,
                settings.GOOGLE_PLACES_MAX_PAGES,
                settings.PROSPECT_LOOKUP_MONTHLY_CAP)
    if requests is not None:
        settings.GOOGLE_PLACES_MONTHLY_REQUEST_CAP = requests
    if repeat_days is not None:
        settings.GOOGLE_PLACES_QUERY_REPEAT_DAYS = repeat_days
    if max_pages is not None:
        settings.GOOGLE_PLACES_MAX_PAGES = max_pages
    if lookup_cap is not None:
        settings.PROSPECT_LOOKUP_MONTHLY_CAP = lookup_cap
    try:
        yield
    finally:
        (settings.GOOGLE_PLACES_MONTHLY_REQUEST_CAP,
         settings.GOOGLE_PLACES_QUERY_REPEAT_DAYS,
         settings.GOOGLE_PLACES_MAX_PAGES,
         settings.PROSPECT_LOOKUP_MONTHLY_CAP) = previous


def purge(db) -> None:
    """Remove every row these modules create. Runs before and after each."""
    prospect_ids = [p.id for p in db.query(Prospect).filter(Prospect.phone.in_(POOL))]

    for model, column in ((ProspectSighting, ProspectSighting.prospect_id),
                          (ProspectRejection, ProspectRejection.prospect_id)):
        if prospect_ids:
            db.query(model).filter(column.in_(prospect_ids)).delete(
                synchronize_session=False)
    db.query(ProspectRejection).filter(ProspectRejection.phone.in_(POOL)).delete(
        synchronize_session=False)

    if prospect_ids:
        db.query(Prospect).filter(Prospect.id.in_(prospect_ids)).delete(
            synchronize_session=False)

    db.query(PhoneLookup).filter(PhoneLookup.phone.in_(POOL)).delete(
        synchronize_session=False)
    db.query(BlockedNumber).filter(BlockedNumber.phone.in_(POOL)).delete(
        synchronize_session=False)

    contact_ids = [c.id for c in db.query(Contact).filter(Contact.phone.in_(POOL))]
    if contact_ids:
        db.query(ContactCategory).filter(
            ContactCategory.contact_id.in_(contact_ids)).delete(
            synchronize_session=False)
        db.query(Contact).filter(Contact.id.in_(contact_ids)).delete(
            synchronize_session=False)

    # **The jobs, and this is the one P1's purge did not have to care about.**
    # `searched_recently()` and `spend_this_month()` both read this table, so a
    # job left behind by an earlier module would tell a later one that a search
    # had already run or that the month's budget was spent — and both of those
    # make a test pass.
    db.query(ScrapeJob).filter(ScrapeJob.source == SOURCE_NAME).delete(
        synchronize_session=False)
    db.commit()


def purged_db_fixture_body():
    db = SessionLocal()
    try:
        purge(db)
        yield db
        purge(db)
    finally:
        db.close()
