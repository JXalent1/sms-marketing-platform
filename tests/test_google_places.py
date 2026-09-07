"""The Google Places source, end to end, against recorded-shape fixtures.

Session P2, acceptance criteria 2, 4, 5, 6 and 7.

**Nothing here calls a paid API.** The Places client is a replay of
`tests/fixtures/google_places_responses.json` that counts every request it was
asked to make, and the line-type provider is P1's counting fake. Every claim
about money in this module is asserted on a **call count**, because that is the
only thing that distinguishes a dedup that works from one that does not —
elapsed time would pass either way.
"""

from decimal import Decimal
import logging

import pytest

from app.core.config import settings
from app.models.prospect import Prospect
from app.models.scrape import ScrapeJob
from app.services import (
    api_budget, contact_service, prospect_ingest, prospect_queue,
    prospect_service, scrape_runner,
)
from app.sources import google_places, taxonomy
from app.sources.google_places import GooglePlacesSource, PlacesClient
from tests import _places_setup as setup
from tests._prospect_setup import CountingLookupProvider

WHOLESALE_PAGES = {None: "shell_wholesale_page_1",
                   "A4A_TEST_PAGE_TOKEN_2": "shell_wholesale_page_2"}
AGGREGATE_PAGES = {None: "shell_aggregate_page_1"}


@pytest.fixture()
def db():
    yield from setup.purged_db_fixture_body()


def _run(db, search, *, pages=WHOLESALE_PAGES, provider=None, **kwargs):
    """One job for one search, with a replay client and a counting screener.

    Returns `(job, source, client)`. The client is handed back separately
    because `cleanup()` drops the source's reference to it — which is the
    behaviour being relied on everywhere else, so the test holds its own.
    """
    source = setup.source(pages=pages)
    client = source.client
    job = scrape_runner.run_job(db, source, search_term=search.ledger_key,
                                search=search, timeout_seconds=20,
                                provider=provider, **kwargs)
    return job, source, client


# ─── Criterion 2: N results in, N prospects out ─────────────────────────────

def test_n_api_results_produce_n_prospects(db):
    """Every usable result becomes a prospect; nothing else does.

    The fixture's first page carries five businesses: three shell traders, an
    auction house and an estate liquidator. The last two are stopped by name
    before they become prospects, which is criterion 6 arriving through the
    real source rather than through `excluded_reason()` on its own.
    """
    search = setup.search_for("shell_wholesale", sweep_name="national")
    provider = CountingLookupProvider()
    with setup.caps(max_pages=3):
        job, _source, _client = _run(db, search, provider=provider)

    assert job.status == "completed"
    assert job.records_yielded == 9, "both fixture pages should have been read"
    assert job.prospects_created == 5
    assert job.records_excluded == 4, (
        "the auction house, the estate liquidator, the appraiser and the "
        "consignment gallery must never become prospects")
    assert job.records_known == 0 and job.records_invalid == 0

    rows = db.query(Prospect).filter(Prospect.phone.in_(setup.POOL)).all()
    assert len(rows) == 5
    assert {r.source for r in rows} == {"google_places"}
    assert all(r.status == "pending" for r in rows)
    # Two pages read, two requests charged, and the money says so.
    assert job.api_requests == 2
    assert Decimal(job.api_cost or "0") >= 0


def test_the_source_pages_until_the_token_runs_out(db):
    """Three pages is the ceiling; two is what this search actually has."""
    search = setup.search_for("shell_wholesale", sweep_name="national")
    source = setup.source(pages=WHOLESALE_PAGES)
    records = list(source.fetch(search=search))

    assert [call["page_token"] for call in source.client.calls] == \
        [None, "A4A_TEST_PAGE_TOKEN_2"]
    assert len(records) == 9
    assert source.client.calls[0]["page_size"] == settings.GOOGLE_PLACES_PAGE_SIZE


def test_max_pages_stops_the_paging_before_the_token_does(db):
    search = setup.search_for("shell_wholesale", sweep_name="national")
    with setup.caps(max_pages=1):
        source = setup.source(pages=WHOLESALE_PAGES)
        records = list(source.fetch(search=search))
    assert len(source.client.calls) == 1
    assert len(records) == 5


def test_a_second_identical_run_creates_nothing_and_makes_no_paid_call(db):
    """Criterion 2's second half, and criterion 7's, asserted on call counts.

    Two independent meters, and the second run must spend on neither:

    - **No Places request**, because `searched_recently()` sees the ledger key
      on a completed job inside the repeat window. A request is charged for
      asking, so nothing further down the pipeline can save it.
    - **No line-type lookup**, because every number is already in the cache and
      every prospect is already a row.
    """
    make, calls = setup.factory(pages=WHOLESALE_PAGES)
    provider = CountingLookupProvider()

    first = scrape_runner.run_plan(
        db, source_factory=make, group_slugs=["shell_wholesale"],
        timeout_seconds=20, provider=provider)
    assert first["ran"] == first["planned"] > 1
    assert first["created"] == 5
    assert calls, "the first run made no request at all"
    assert provider.calls, "the first run screened nothing"

    requests_after_first = len(calls)
    lookups_after_first = len(provider.calls)

    second = scrape_runner.run_plan(
        db, source_factory=make, group_slugs=["shell_wholesale"],
        timeout_seconds=20, provider=provider)

    assert second["ran"] == 0
    assert second["skipped_recent"] == second["planned"]
    assert second["created"] == 0
    assert len(calls) == requests_after_first, (
        f"the second run made {len(calls) - requests_after_first} Places "
        f"request(s) for businesses it already holds")
    assert len(provider.calls) == lookups_after_first, (
        "the second run paid to screen numbers already in the cache")


def test_a_search_the_cap_interrupted_is_not_treated_as_already_searched(db):
    """The ledger clause that is easy to leave out.

    A job the request cap stopped is `completed` and *not* finished — its
    remaining pages were refused. Without the `api_requests_skipped` clause the
    cap would quietly retire every search it interrupted, and the niche would be
    permanently half-searched with nothing saying so.
    """
    search = setup.search_for("shell_wholesale", sweep_name="national")
    with setup.caps(requests=1, max_pages=3):
        job, _source, _client = _run(db, search, provider=CountingLookupProvider())

    assert job.status == "completed"
    assert job.api_requests == 1 and job.api_requests_skipped == 1
    assert search.ledger_key not in scrape_runner.searched_recently(
        db, setup.SOURCE_NAME)


def test_a_failed_search_is_not_treated_as_already_searched(db):
    search = setup.search_for("shell_wholesale", sweep_name="national")
    source = setup.source(error=RuntimeError("the search page changed shape"))
    job = scrape_runner.run_job(db, source, search_term=search.ledger_key,
                                search=search, timeout_seconds=20, screen=False)
    assert job.status == "failed"
    assert search.ledger_key not in scrape_runner.searched_recently(
        db, setup.SOURCE_NAME)


def test_a_job_row_written_before_the_meter_existed_still_counts_as_searched(db):
    """`NULL = 0` is NULL in SQL, not true — 5i's lesson, one column over.

    Every job this application writes carries a 0 in `api_requests_skipped`,
    because the model has a Python-side default. Rows written by
    `e7c05b3a1d94`'s predecessors do not: they are NULL, and a freshness query
    testing `api_requests_skipped == 0` would drop every one of them out of the
    ledger and re-run — and pay for — searches that were finished months ago.
    """
    search = setup.search_for("shell_wholesale", sweep_name="national")
    _run(db, search, provider=CountingLookupProvider())
    job = db.query(ScrapeJob).filter(ScrapeJob.source == setup.SOURCE_NAME).one()
    job.api_requests_skipped = None
    db.commit()

    assert search.ledger_key in scrape_runner.searched_recently(
        db, setup.SOURCE_NAME), (
        "a job row with a NULL meter fell out of the ledger, so its search "
        "would be paid for again")


def test_a_repeat_window_of_zero_switches_the_ledger_off(db):
    """Zero means "no window", which is the only reading that is not a trap."""
    search = setup.search_for("shell_wholesale", sweep_name="national")
    _run(db, search, provider=CountingLookupProvider())
    assert scrape_runner.searched_recently(db, setup.SOURCE_NAME, repeat_days=0) == set()
    assert search.ledger_key in scrape_runner.searched_recently(
        db, setup.SOURCE_NAME, repeat_days=30)


# ─── Criterion 4: the request cap ───────────────────────────────────────────

def test_the_cap_refuses_before_the_call_and_the_job_stays_clean(db, caplog):
    """Criterion 4. Mid-run, before any spend, without partial corruption.

    A cap checked *after* the call is a ledger, not a ceiling. The proof is the
    call count: one request made under a one-request cap, with a second page
    waiting and refused.
    """
    search = setup.search_for("shell_wholesale", sweep_name="national")
    provider = CountingLookupProvider()
    with caplog.at_level(logging.ERROR, logger="prospects"):
        with setup.caps(requests=1, max_pages=3):
            job, source, client = _run(db, search, provider=provider)

    assert len(client.calls) == 1, "the cap did not stop the second page"
    assert job.api_requests == 1
    assert job.api_requests_skipped == 1

    # Nothing is half-written: the first page's businesses are prospects, the
    # job is completed and its cleanup ran.
    assert job.status == "completed" and job.cleanup_ran == 1
    assert job.prospects_created == 3
    assert db.query(Prospect).filter(Prospect.phone.in_(setup.POOL)).count() == 3

    logged = " ".join(record.getMessage() for record in caplog.records)
    assert "monthly request cap" in logged
    assert "1 search request(s) were not made" in logged
    assert "GOOGLE_PLACES_MONTHLY_REQUEST_CAP" in logged


def test_the_plan_stops_without_opening_a_job_it_cannot_run(db):
    """A cap reached between searches must not leave an empty completed job.

    An empty `completed` job would land in the ledger and retire a search that
    never ran — the cap silently deleting part of the taxonomy.
    """
    make, calls = setup.factory(pages=WHOLESALE_PAGES)
    with setup.caps(requests=2, max_pages=1):
        summary = scrape_runner.run_plan(
            db, source_factory=make, group_slugs=["shell_wholesale"],
            timeout_seconds=20, provider=CountingLookupProvider())

    assert summary["ran"] == 2
    assert summary["skipped_cap"] == summary["planned"] - 2
    assert len(calls) == 2
    assert db.query(ScrapeJob).filter(
        ScrapeJob.source == setup.SOURCE_NAME).count() == 2, (
        "the plan opened a job for a search it could not pay for")


def test_the_cap_counts_the_searches_it_stopped_and_not_the_ones_already_done(db):
    """Found by review, and the arithmetic was wrong in the quiet direction.

    `skipped_cap` was `len(plan) - index - skipped_recent`, which subtracts the
    already-searched twice — they sit at indices below the break, so the slice
    has already excluded them. It reads right, it is correct on a first run
    where nothing has been searched, and it under-reports on **every run after
    that**, which is the only kind of run this figure is read on.

    The mirror of the same mistake is over-counting: a search still to come that
    the ledger would have skipped anyway was not stopped by the cap either.
    """
    make, _calls = setup.factory(pages=WHOLESALE_PAGES)
    provider = CountingLookupProvider()

    # Run two searches of the group, then leave room for exactly one more.
    with setup.caps(requests=2, max_pages=1, repeat_days=30):
        first = scrape_runner.run_plan(
            db, source_factory=make, group_slugs=["shell_wholesale"],
            timeout_seconds=20, provider=provider)
    assert first["ran"] == 2 and first["skipped_recent"] == 0

    with setup.caps(requests=3, max_pages=1, repeat_days=30):
        second = scrape_runner.run_plan(
            db, source_factory=make, group_slugs=["shell_wholesale"],
            timeout_seconds=20, provider=provider)

    planned = second["planned"]
    assert second["skipped_recent"] == 2, "the ledger did not skip the first two"
    assert second["ran"] == 1, "the cap left room for exactly one more"
    assert second["skipped_cap"] == planned - 3, (
        f"{second['skipped_cap']} searches reported stopped by the cap, but "
        f"{planned - 3} were left unrun (planned={planned}, ran=1, "
        f"already searched=2)")
    assert (second["ran"] + second["skipped_recent"]
            + second["skipped_cap"]) == planned, (
        "every planned search must be accounted for exactly once")


def test_a_search_interrupted_once_and_completed_later_leaves_the_ledger_alone(db):
    """The second review finding: `interrupted` was unbounded in time.

    A search the cap stopped in March and that completed cleanly in April would
    have stayed permanently out of the ledger — so it re-ran, and paid $0.035,
    every night forever. The freshness query has to ask "was the job that made
    this term fresh interrupted", not "was this term ever interrupted".
    """
    search = setup.search_for("shell_wholesale", sweep_name="national")

    with setup.caps(requests=1, max_pages=3):
        interrupted, _s, _c = _run(db, search, provider=CountingLookupProvider())
    assert interrupted.api_requests_skipped == 1
    assert search.ledger_key not in scrape_runner.searched_recently(
        db, setup.SOURCE_NAME)

    # ... and now it completes, with room for every page.
    with setup.caps(requests=10, max_pages=3):
        clean, _s, _c = _run(db, search, provider=CountingLookupProvider())
    assert clean.api_requests_skipped == 0
    assert search.ledger_key in scrape_runner.searched_recently(
        db, setup.SOURCE_NAME), (
        "an old interrupted run kept a completed search out of the ledger, so "
        "it would be paid for again on every future run")


def test_a_cap_of_zero_switches_discovery_off_rather_than_making_it_unlimited(db):
    """The reading that turns an off switch into an on switch."""
    make, calls = setup.factory(pages=WHOLESALE_PAGES)
    with setup.caps(requests=0):
        summary = scrape_runner.run_plan(
            db, source_factory=make, group_slugs=["shell_wholesale"],
            timeout_seconds=20, provider=CountingLookupProvider())
    assert summary["ran"] == 0 and calls == []
    assert summary["skipped_cap"] == summary["planned"]


def test_the_budget_counts_what_earlier_jobs_this_month_already_spent(db):
    """The ceiling is monthly. A per-run ceiling is no ceiling once anything
    runs twice."""
    search = setup.search_for("shell_wholesale", sweep_name="national")
    with setup.caps(requests=3, max_pages=1):
        _run(db, search, provider=CountingLookupProvider())
        assert api_budget.spend_this_month(db, setup.SOURCE_NAME) == 1

        other = setup.search_for("shell_wholesale", sweep_name="gulf_coast")
        _run(db, other, provider=CountingLookupProvider())
        assert api_budget.spend_this_month(db, setup.SOURCE_NAME) == 2

        budget = api_budget.RequestBudget.for_month(db, setup.SOURCE_NAME)
        assert budget.opening == 2 and budget.remaining == 1


def test_last_months_requests_do_not_count_against_this_month(db):
    search = setup.search_for("shell_wholesale", sweep_name="national")
    with setup.caps(max_pages=1):
        _run(db, search, provider=CountingLookupProvider())
    job = db.query(ScrapeJob).filter(ScrapeJob.source == setup.SOURCE_NAME).one()
    job.started_at = "2020-01-15T09:00:00"
    db.commit()
    assert api_budget.spend_this_month(db, setup.SOURCE_NAME) == 0
    assert api_budget.spend_this_month(db, setup.SOURCE_NAME, month="2020-01") == 1


def test_the_free_allowance_is_a_property_of_the_month_not_of_the_run(db):
    """The first thousand requests of a month are free; the run that crosses
    the line is billed for the part that crossed it."""
    budget = api_budget.RequestBudget(
        setup.SOURCE_NAME, cap=2000, spent=998,
        price_per_1000=Decimal("35.0"), free_per_month=1000)
    budget.charge(4)                     # 998 -> 1002: two of the four are free
    assert budget.requests_made == 4
    assert budget.charged == (Decimal(2) * Decimal("35.0")) / Decimal(1000)

    inside = api_budget.RequestBudget(
        setup.SOURCE_NAME, cap=2000, spent=0,
        price_per_1000=Decimal("35.0"), free_per_month=1000)
    inside.charge(10)
    assert inside.charged == Decimal(0)


def test_the_job_records_what_it_attempted_produced_and_spent(db):
    """A3: a scrape whose cost cannot be attributed to its results is a scrape
    nobody can decide to repeat.

    Through `run_plan()` rather than `run_job()`, because the parameters that
    make a job self-describing are written by the plan — a test that passed
    them itself would be asserting on its own argument.
    """
    make, _calls = setup.factory(pages=WHOLESALE_PAGES)
    with setup.caps(requests=1, max_pages=1):
        scrape_runner.run_plan(db, source_factory=make,
                               group_slugs=["shell_wholesale"],
                               timeout_seconds=20,
                               provider=CountingLookupProvider())
    job = db.query(ScrapeJob).filter(ScrapeJob.source == setup.SOURCE_NAME).one()
    search = setup.search_for("shell_wholesale", sweep_name="national")

    assert job.search_term == search.ledger_key
    assert job.parameters["group"] == "shell_wholesale"
    assert job.parameters["sweep"] == "national"
    assert job.parameters["radius_miles"] is None
    assert job.records_yielded and job.prospects_created
    assert job.api_requests and job.lookups_performed
    assert job.started_at and job.finished_at and job.cleanup_ran == 1


def test_scrape_jobs_started_at_has_exactly_one_writer(db):
    """The monthly meter is a `LIKE` on this column, so a second clock would
    make the cap wrong — `contact_list_members.added_at` is what that looks
    like."""
    column = ScrapeJob.__table__.columns["started_at"]
    assert column.server_default is None, (
        "a server default is a second writer keeping a different clock, and "
        "SQLite's CURRENT_TIMESTAMP is UTC while run_job() writes local time")
    assert column.nullable is False


# ─── Criterion 3 / 6b, end to end: the radius rule drops rows ───────────────

def test_a_regional_group_drops_a_business_outside_its_radius(db):
    """Dropped, not scored zero. A business that can never collect a cubic yard
    of shell costs $0.0025 to screen and a slot in the queue forever."""
    search = setup.search_for("shell_aggregate")
    assert search.radius_miles == 150
    provider = CountingLookupProvider()
    job, source, client = _run(db, search, pages=AGGREGATE_PAGES, provider=provider)

    assert source.dropped_out_of_radius == 1
    assert job.prospects_created == 2
    names = {row.business_name for row in
             db.query(Prospect).filter(Prospect.phone.in_(setup.POOL))}
    assert "Bayou Shell Aggregate Supply" not in names, (
        "a Houston yard is 962 miles from the auction house; freight dominates "
        "the price of crushed shell and nobody buys a yard of it from there")
    assert len(provider.calls) == 2, "the dropped business was still screened"


def test_a_national_group_keeps_the_same_distant_business(db):
    """The same result, the same distance, the other rule.

    This is the pair that proves radius is a property of the *group*: nothing
    about the business changed between the two tests.
    """
    search = setup.search_for("shell_wholesale", sweep_name="national")
    assert search.radius_miles is None
    job, source, _client = _run(db, search, provider=CountingLookupProvider())

    assert source.dropped_out_of_radius == 0
    rows = {row.business_name: row for row in
            db.query(Prospect).filter(Prospect.phone.in_(setup.POOL))}
    houston = rows.get("Bayfront Shell Distributors")
    assert houston is not None
    assert houston.distance_miles > 900


def test_distance_is_measured_from_the_auction_house_not_the_sweep(db):
    """"Can they collect it" is a question about the auction house.

    The Gulf coast sweep is a different *search centre*, not a different place
    for the lots to be. A distance measured from Sanibel would make a Naples
    business look local to a client in Fort Lauderdale.
    """
    gulf = setup.search_for("shell_wholesale", sweep_name="gulf_coast")
    source = setup.source(pages=WHOLESALE_PAGES)
    records = {r.business_name: r for r in source.fetch(search=gulf)}
    assert records["Gulf Coral Trading Co"].distance_miles == pytest.approx(123, abs=3)
    assert source.client.calls[0]["bias"][0] == taxonomy.GULF_COAST.center[0]


# ─── Criterion 5: the term and the rationale reach the queue ────────────────

def test_every_prospect_carries_its_term_and_that_terms_rationale(db):
    search = setup.search_for("shell_wholesale", sweep_name="national")
    _run(db, search, provider=CountingLookupProvider())

    rows = db.query(Prospect).filter(Prospect.phone.in_(setup.POOL)).all()
    assert rows
    for row in rows:
        assert row.search_term == search.term, (
            "the queue groups and flags on the human term, not the ledger key")
        assert row.buyer_rationale == search.group.buyer_rationale
        assert row.source_url.startswith("https://")
        assert "places.googleapis.com" not in row.source_url
        assert "key" not in row.source_url.lower()


def test_the_review_queue_renders_both(db):
    """Criterion 5's second half, through the screen's own query."""
    search = setup.search_for("shell_wholesale", sweep_name="national")
    _run(db, search, provider=CountingLookupProvider())

    page = prospect_queue.queue_page(db, status="pending",
                                     per_page=prospect_queue.MAX_PAGE_SIZE)
    ours = [row for row in page["prospects"] if row["phone"] in setup.POOL]
    assert ours
    for row in ours:
        assert row["search_term"] == search.term
        assert row["buyer_rationale"] == search.group.buyer_rationale

    terms = {row["term"]: row
             for row in prospect_queue.term_breakdown(db)["terms"]}
    assert search.term in terms
    assert terms[search.term]["buyer_rationale"] == search.group.buyer_rationale


# ─── Criterion 7: dedup before spending ─────────────────────────────────────

def test_a_business_already_a_contact_never_becomes_a_prospect(db):
    """Criterion 7. `modules.md` put this in P2's scope and P1 left it there.

    Stopped before the insert, so it never reaches the review queue and is never
    screened. Two costs avoided, and the queue stops showing the client
    businesses he already has.
    """
    search = setup.search_for("shell_wholesale", sweep_name="national")
    source_records = list(setup.source(pages=WHOLESALE_PAGES).fetch(search=search))
    known = source_records[0].phone
    contact_service.upsert_contact(db, phone=known, full_name="Already His",
                                   source="csv")

    provider = CountingLookupProvider()
    job, _source, _client = _run(db, search, provider=provider)

    assert job.records_known == 1
    assert job.prospects_created == 4
    assert db.query(Prospect).filter(Prospect.phone == known).first() is None
    assert known not in provider.calls, "we paid to screen a number he already has"


def test_a_rejected_number_never_returns_and_is_never_paid_for(db):
    """A permanent rejection is permanent on the number, from any source."""
    search = setup.search_for("shell_wholesale", sweep_name="national")
    _run(db, search, provider=CountingLookupProvider())
    victim = db.query(Prospect).filter(Prospect.phone.in_(setup.POOL)).first()
    phone = victim.phone
    prospect_service.reject(db, [victim.id], reason="seller_or_consignor")

    provider = CountingLookupProvider()
    with setup.caps(repeat_days=0):
        job, _source, _client = _run(db, search, provider=provider)

    assert job.records_suppressed == 1
    assert phone not in provider.calls


def test_a_number_already_looked_up_is_not_looked_up_again(db):
    """The cache, asserted on the call count across two runs of one search."""
    search = setup.search_for("shell_wholesale", sweep_name="national")
    provider = CountingLookupProvider()
    _run(db, search, provider=provider)
    first = list(provider.calls)
    assert first

    with setup.caps(repeat_days=0):
        _run(db, search, provider=provider)
    assert provider.calls == first, "a cached number was bought twice"


def test_an_excluded_business_is_stopped_before_any_spend(db):
    """Criterion 6, through the source, and the money half of it.

    The exclusion runs in `record_prospect()` — before the row and therefore
    before the screening pass, which only screens `pending` prospects.
    """
    search = setup.search_for("shell_wholesale", sweep_name="national")
    provider = CountingLookupProvider()
    job, _source, _client = _run(db, search, provider=provider)

    excluded_phones = set()
    for name in ("shell_wholesale_page_1", "shell_wholesale_page_2"):
        for place in setup.fixture(name)["places"]:
            from app.sources.exclusions import excluded_reason
            if excluded_reason((place.get("displayName") or {}).get("text")):
                excluded_phones.add(
                    place["internationalPhoneNumber"].replace(" ", "").replace("-", ""))

    assert job.records_excluded == len(excluded_phones) == 4
    assert not excluded_phones & set(provider.calls), (
        "an auction house or an estate liquidator was screened at $0.0025")
    assert db.query(Prospect).filter(
        Prospect.phone.in_(excluded_phones)).count() == 0


# ─── The Enterprise tier, the key, and the failure paths ────────────────────

def test_a_key_without_enterprise_tier_says_so_instead_of_finding_nothing(db,
                                                                         caplog):
    """The most expensive way to learn a key is wrong is a silent empty run."""
    search = setup.search_for("shell_wholesale", sweep_name="national")
    with caplog.at_level(logging.ERROR, logger="prospects"):
        job, _source, _client = _run(db, search, pages={None: "enterprise_tier_missing"},
                      provider=CountingLookupProvider())

    assert job.prospects_created == 0
    logged = " ".join(record.getMessage() for record in caplog.records)
    assert "Enterprise-tier field" in logged


def test_the_source_refuses_without_a_key_rather_than_finding_nothing():
    """A search that returns nothing and a niche that contains nothing look
    identical on every screen."""
    previous = settings.GOOGLE_PLACES_API_KEY
    settings.GOOGLE_PLACES_API_KEY = ""
    try:
        with pytest.raises(ValueError) as caught:
            PlacesClient()
        assert "GOOGLE_PLACES_API_KEY" in str(caught.value)
    finally:
        settings.GOOGLE_PLACES_API_KEY = previous


def test_a_failed_request_is_described_from_named_fields_and_redacts_the_key():
    """Never `str(exc)`, never the raw body.

    `scrape_jobs.error` is stored unscrubbed by design — it is a developer's
    diagnostic — so a credential that reached it would outlive the incident.
    """
    previous = settings.GOOGLE_PLACES_API_KEY
    settings.GOOGLE_PLACES_API_KEY = "AIza-TEST-NOT-A-REAL-KEY"
    try:
        body = setup.fixture("error_403")
        body["error"]["message"] += " key=AIza-TEST-NOT-A-REAL-KEY"
        text = google_places.describe_places_error(403, body)
        assert "AIza-TEST-NOT-A-REAL-KEY" not in text
        assert "[redacted]" in text
        assert "PERMISSION_DENIED" in text or "has not been used" in text
        assert "403" in text
    finally:
        settings.GOOGLE_PLACES_API_KEY = previous


def test_a_failed_request_leaves_a_failed_job_and_keeps_what_it_found(db):
    search = setup.search_for("shell_wholesale", sweep_name="national")
    source = setup.source(error=RuntimeError("HTTP 500"))
    client = source.client
    job = scrape_runner.run_job(db, source, search_term=search.ledger_key,
                                search=search, timeout_seconds=20, screen=False)
    assert job.status == "failed"
    assert job.cleanup_ran == 1
    assert client.closed == 1, "a failed run left its HTTP client open"


def test_cleanup_closes_the_client_on_every_path(db):
    """The reference system leaked one driver process per nightly scrape."""
    search = setup.search_for("shell_wholesale", sweep_name="national")
    _job, source, client = _run(db, search, provider=CountingLookupProvider())
    assert source.client is None
    assert source._closed is True
    assert client.closed == 1

    # Safe twice, and safe on a source that never ran.
    source.cleanup()
    never_ran = GooglePlacesSource(client=setup.ReplayPlacesClient())
    never_ran.cleanup()
    never_ran.cleanup()


def test_a_closed_source_refuses_to_open_a_second_client(db):
    """Rebuilding after cleanup is how one leak becomes seventeen."""
    source = setup.source(pages=WHOLESALE_PAGES)
    source.cleanup()
    search = setup.search_for("shell_wholesale", sweep_name="national")
    with pytest.raises(RuntimeError) as caught:
        list(source.fetch(search=search))
    assert "closed" in str(caught.value)


def test_fetch_without_a_search_refuses_rather_than_guessing(db):
    source = setup.source()
    with pytest.raises(ValueError) as caught:
        list(source.fetch())
    assert "run_plan" in str(caught.value)


def test_the_source_stops_when_the_runner_asks_it_to(db):
    """Cooperative cancellation, checked in the paging loop."""
    search = setup.search_for("shell_wholesale", sweep_name="national")
    source = setup.source(pages=WHOLESALE_PAGES)
    source.request_stop()
    assert list(source.fetch(search=search)) == []
    assert source.client.calls == []


# ─── The mapping itself ─────────────────────────────────────────────────────

def test_a_place_becomes_a_record_with_everything_the_queue_needs():
    search = setup.search_for("shell_wholesale", sweep_name="national")
    places = setup.fixture("shell_wholesale_page_1")["places"]
    records = {r.business_name: r
               for r in google_places.records_from(places, search)}

    record = records["Gulf Coral Trading Co"]
    # The API's own spelling. Normalisation to E.164 is `record_prospect()`'s
    # job, and a source that normalised would be a second place that rule lives.
    assert record.phone == "+1 555-555-1200"
    assert record.address.endswith("USA")
    assert record.category_slug == "seashells"
    assert 0.5 <= record.category_confidence <= 1.0
    assert record.distance_miles == pytest.approx(123, abs=3)
    assert record.raw_payload["id"] == "A4A_TEST_0001"
    assert record.source_url.startswith("https://maps.google.com/")


def test_the_field_mask_asks_for_everything_the_parser_reads():
    """The fixture is written to the documented schema, not recorded from a
    call — so the one consistency this repo CAN check is that the mask, the
    parser and the fixture agree with each other.

    P1b's shape, one API over: `test_lookup_provider.py` asserts the SDK still
    exposes the call its provider drives, because a pin that no longer matches
    the code fails on the morning of a sale. There is no SDK here, so the
    equivalent is that no field is parsed that was never requested — a mask
    that stops asking for `nationalPhoneNumber` would produce a run in which
    every business is unreachable, and nothing else would say so.
    """
    mask = set(google_places.FIELD_MASK.split(","))
    # Every field the mapping reads, as the mask spells it.
    read = {
        "places.id", "places.displayName", "places.formattedAddress",
        "places.location", "places.types", "places.primaryType",
        "places.googleMapsUri", "places.nationalPhoneNumber",
        "places.internationalPhoneNumber", "nextPageToken",
    }
    assert read <= mask, f"parsed but never requested: {sorted(read - mask)}"
    assert mask <= read, f"requested but never read (paid for nothing): {sorted(mask - read)}"

    # And the fixture carries only keys the mask asks for, so a fixture cannot
    # quietly teach the parser a field the live API was never asked for.
    place_fields = {f.split(".", 1)[1] for f in mask if f.startswith("places.")}
    for name in ("shell_wholesale_page_1", "shell_aggregate_page_1"):
        for place in setup.fixture(name)["places"]:
            unknown = set(place) - place_fields
            assert not unknown, f"{name} carries unrequested field(s) {unknown}"


def test_a_result_with_no_phone_is_not_a_record():
    search = setup.search_for("shell_wholesale", sweep_name="national")
    places = setup.fixture("enterprise_tier_missing")["places"]
    assert google_places.records_from(places, search) == []


def test_match_confidence_separates_a_named_match_from_a_review_match():
    """Text Search matches reviews and descriptions too, so the overlap between
    the term and the business's own name is a real signal."""
    search_term = "seashell wholesaler"
    strong = {"displayName": {"text": "Seashell Wholesaler Direct"},
              "types": ["wholesaler"], "primaryType": "wholesaler"}
    weak = {"displayName": {"text": "Marisol Cafe"},
            "types": ["restaurant"], "primaryType": "restaurant"}
    assert google_places.match_confidence(search_term, strong) == 1.0
    assert google_places.match_confidence(search_term, weak) == 0.5
    assert google_places.match_confidence("", weak) == 0.5


def test_the_ingest_outcomes_and_the_job_counters_stay_in_step():
    """`ProspectSource.ingest()` refuses an outcome it cannot count.

    Adding an outcome without adding a counter would report a run that found
    nothing rather than failing.
    """
    from app.sources.prospect_base import ProspectIngestResult
    result = ProspectIngestResult()
    for outcome in prospect_ingest.RECORD_OUTCOMES:
        assert hasattr(result, outcome), outcome
        assert outcome in result.as_dict()
    for outcome in prospect_ingest.RECORD_OUTCOMES:
        field = {"created": "prospects_created",
                 "corroborated": "prospects_corroborated",
                 "suppressed": "records_suppressed",
                 "excluded": "records_excluded",
                 "known": "records_known",
                 "invalid": "records_invalid"}[outcome]
        assert field in ScrapeJob.__table__.columns, (outcome, field)


def test_no_client_facing_prospect_surface_carries_our_api_spend(db):
    """`api_cost` and `api_requests` are OUR spend, on `WHOLESALE_*`'s footing.

    The queue's own serializer is the surface: `test_prospect_review.py` sweeps
    every route at runtime, and this asserts the new columns never enter the
    payload in the first place.
    """
    search = setup.search_for("shell_wholesale", sweep_name="national")
    _run(db, search, provider=CountingLookupProvider())
    page = prospect_queue.queue_page(db, status="pending",
                                     per_page=prospect_queue.MAX_PAGE_SIZE)
    blob = repr(page).lower()
    # Column names and payload keys, not words. "wholesale" is deliberately NOT
    # in this list: the priority group's buyer rationale contains the word
    # "wholesaler", legitimately and by name, and the first version of this
    # sweep failed on it — the fourth time in this repo a check has counted
    # prose describing a rule as a violation of it. Our *figures* are swept by
    # `_wholesale_scan`, which compares parsed numbers.
    for forbidden in ("api_cost", "api_requests", "api_requests_skipped",
                      "lookups_performed", "place_id", "googleapis",
                      "raw_payload"):
        assert forbidden not in blob, forbidden
