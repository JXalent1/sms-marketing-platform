"""Session P1 A1-A5: provenance, the source seam, the runner and the gate.

Nothing here calls a paid API. The line-type provider is always passed in
explicitly as a fake that counts its calls, and `PROSPECT_LOOKUP_PROVIDER` is
never touched — so there is no path from this file to a real lookup even if the
default changed.

Nothing here sends a message either; the send path is not reached at all.
"""

import time

import pytest

from app.core.config import settings
from app.models.prospect import Prospect, ProspectSighting
from app.models.scrape import PhoneLookup
from app.services import lookup_service, prospect_scoring, prospect_service
from app.services import scrape_runner
from app.sources.prospect_base import ProspectRecord

from tests import _prospect_setup as setup


@pytest.fixture(scope="module")
def db():
    yield from setup.purged_db_fixture_body()


# ─── A1: what a prospect keeps ──────────────────────────────────────────────

def test_every_prospect_retains_its_provenance(db):
    """Criterion 8, and it is a claim about the *columns*, not about a payload.

    A bad record has to be traceable to the search that produced it rather than
    argued about. Each of these is asserted separately: a single "the row exists"
    assertion would pass with four of the five empty, and the one that is empty
    is always the one somebody disputes.
    """
    phone = setup.take(1)[0]
    source = setup.FakeSource([setup.record(phone, name="Taco Truck")])
    source.ingest(db)

    row = db.query(Prospect).filter(Prospect.phone == phone).one()
    assert row.source_url == setup.SOURCE_URL
    assert row.scraped_at                       # a timestamp, written by us
    assert row.raw_payload == {"place_id": f"p-{phone[-4:]}"}
    assert row.search_term == setup.TERM
    assert row.buyer_rationale == setup.RATIONALE
    assert row.source == "fake-a"

    # And non-nullable, so the property survives a future writer that forgets.
    for column in ("source_url", "scraped_at", "raw_payload", "search_term",
                   "buyer_rationale"):
        assert Prospect.__table__.columns[column].nullable is False, (
            f"{column} became nullable — provenance that can be NULL is "
            f"provenance that is missing on exactly the disputed rows")


def test_a_record_with_no_buyer_rationale_never_reaches_the_queue(db):
    """The first of the three places "buyers, never sellers" is enforced.

    A term with no written claim about why these people would bid cannot reach a
    reviewer, because a reviewer cannot agree or disagree with a blank.
    """
    phone = setup.take(1)[0]
    nameless = ProspectRecord(phone=phone, source_url=setup.SOURCE_URL,
                              search_term="estate liquidators near me",
                              buyer_rationale="   ")
    source = setup.FakeSource([nameless])
    result = source.ingest(db)

    assert result.invalid == 1 and result.created == 0
    assert db.query(Prospect).filter(Prospect.phone == phone).first() is None


def test_a_record_with_no_search_term_or_source_url_is_refused_too(db):
    phones = setup.take(2)
    records = [
        ProspectRecord(phone=phones[0], source_url=setup.SOURCE_URL,
                       search_term="", buyer_rationale=setup.RATIONALE),
        ProspectRecord(phone=phones[1], source_url="",
                       search_term=setup.TERM, buyer_rationale=setup.RATIONALE),
    ]
    result = setup.FakeSource(records).ingest(db)
    assert result.invalid == 2
    assert db.query(Prospect).filter(Prospect.phone.in_(phones)).count() == 0


def test_the_same_business_found_twice_is_one_prospect_with_two_sightings(db):
    """Multi-source corroboration, and the dedup that makes it mean something.

    The number is the identity, exactly as it is for a contact. A second search
    finding the same business writes a sighting and raises the score; it does not
    write a second prospect, and running the *same* search again does neither.
    """
    phone = setup.take(1)[0]
    setup.FakeSource([setup.record(phone)], name="fake-a").ingest(db)
    before = db.query(Prospect).filter(Prospect.phone == phone).one().score

    setup.FakeSource([setup.record(phone, term="mobile caterers broward")],
                     name="fake-b").ingest(db)
    row = db.query(Prospect).filter(Prospect.phone == phone).one()

    assert db.query(Prospect).filter(Prospect.phone == phone).count() == 1
    assert row.source_count == 2
    assert row.score > before, "a second source must raise the score"
    assert db.query(ProspectSighting).filter(
        ProspectSighting.prospect_id == row.id).count() == 2

    # The same source and the same term again: idempotent. Without the unique
    # constraint a nightly re-run would inflate every score it touched and the
    # queue would end up ordered by how often a search happened to run.
    setup.FakeSource([setup.record(phone)], name="fake-a").ingest(db)
    db.refresh(row)
    assert row.source_count == 2
    assert db.query(ProspectSighting).filter(
        ProspectSighting.prospect_id == row.id).count() == 2


# ─── A4: the gate, and the cache that pays for it ───────────────────────────

def test_a_repeat_lookup_hits_the_cache_and_makes_no_call(db):
    """Criterion 6, asserted on the call count rather than on elapsed time.

    Elapsed time passes whether or not a cache exists. The call count is the
    only thing that says a number was not paid for twice — and at $0.0025 a
    number over 10,000 businesses, "twice" is the difference between $25 and
    however many times anybody re-runs a search.
    """
    phones = setup.take(3)
    provider = setup.CountingLookupProvider()

    first = lookup_service.screen(db, phones, provider=provider)
    assert sorted(provider.calls) == sorted(phones)
    assert first["performed"] == 3 and first["cached"] == 0

    second = lookup_service.screen(db, phones, provider=provider)
    assert len(provider.calls) == 3, (
        f"the cache made {len(provider.calls) - 3} extra call(s) — every one of "
        f"them is money, and the whole economic case for scraping is that this "
        f"number stays at 3")
    assert second["performed"] == 0 and second["cached"] == 3
    assert float(second["cost"]) == 0.0


def test_the_single_number_path_reads_the_cache_too(db):
    """`screen()` short-circuits on a fully cached set and never reaches
    `line_type_for()`, so the criterion-6 test above proves nothing about the
    single-number entry point — which is public API and the one a future caller
    will reach for. The mutation harness found this: reverting the cache read
    inside `line_type_for()` broke nothing.
    """
    phone = setup.take(1)[0]
    provider = setup.CountingLookupProvider(answers={phone: "mobile"})

    first = lookup_service.line_type_for(db, phone, provider=provider)
    assert first == {"line_type": "mobile", "cached": False, "ok": True}

    second = lookup_service.line_type_for(db, phone, provider=provider)
    assert second["cached"] is True
    assert provider.calls == [phone], (
        "the single-number path looked the same number up twice")


def test_a_partly_cached_batch_pays_only_for_the_misses(db):
    """The realistic case, and the one neither test above covers.

    A fully cached batch never enters the lookup loop at all, so the per-number
    "skip what we already know" check is unexercised by it — also found by the
    mutation harness. A nightly job over an overlapping set is exactly this
    shape: some numbers seen last night, some new.
    """
    known, fresh = setup.take(2), setup.take(2)
    provider = setup.CountingLookupProvider()
    lookup_service.screen(db, known, provider=provider)
    assert len(provider.calls) == 2

    outcome = lookup_service.screen(db, known + fresh, provider=provider)
    assert outcome["performed"] == 2 and outcome["cached"] == 2, (
        f"paid for {outcome['performed']} of 4 numbers when 2 were already known")
    assert sorted(provider.calls) == sorted(known + fresh)
    assert set(outcome["results"]) == set(known + fresh)


def test_a_failed_lookup_is_recorded_but_not_cached_as_an_answer(db):
    """An outage must not become a permanent hole in the list.

    A carrier that times out has not answered. Freezing that into the cache
    would file every number screened during the outage as `unknown` forever, and
    `unknown` cannot be promoted — so those businesses would sit in the queue
    with nothing on screen explaining why.
    """
    phone = setup.take(1)[0]
    down = setup.FailingLookupProvider()
    lookup_service.screen(db, [phone], provider=down)

    row = db.query(PhoneLookup).filter(PhoneLookup.phone == phone).one()
    assert row.status == "error" and row.line_type == "unknown"
    assert lookup_service.cached_line_types(db, [phone]) == {}, (
        "a failure is being served from the cache as though it were an answer")

    working = setup.CountingLookupProvider(answers={phone: "mobile"})
    lookup_service.screen(db, [phone], provider=working)
    assert working.calls == [phone], "a failed lookup must stay retryable"

    db.refresh(row)
    assert row.status == "ok" and row.line_type == "mobile"
    assert row.attempts == 2, "the retry count is how a hopeless number shows up"


def test_the_gate_lets_through_only_what_it_can_prove_is_reachable(db):
    """`unknown` and `landline` are both refused, and for different reasons.

    Asserted through `refusal_for()` rather than against a hardcoded set,
    because that function is what `promote()` calls — testing the membership
    tuple would prove the tuple, not the guard.
    """
    assert lookup_service.refusal_for("mobile") is None
    assert lookup_service.refusal_for("landline") == lookup_service.LANDLINE_REFUSAL
    assert lookup_service.refusal_for("unknown") == lookup_service.UNSCREENED_REFUSAL
    # A line type nobody has written a refusal for falls through to the safe
    # answer, not to None.
    assert lookup_service.refusal_for("something_new") == lookup_service.UNSCREENED_REFUSAL


# ─── A3: the runner ─────────────────────────────────────────────────────────

def test_a_hung_job_is_stopped_at_its_deadline_and_its_cleanup_runs(db):
    """Criterion 4, and the cleanup is *asserted*, not assumed.

    Three separate claims, because passing one of them while failing another is
    exactly the reference system's failure — that scrape finished every night
    and leaked a browser driver every night.

      1. The job stops at its deadline rather than at the source's convenience.
      2. `cleanup()` actually ran, on the source object, once.
      3. The resource it holds is genuinely released — proved by the worker
         thread ending, which it only does because `cleanup()` released it.

    The third is the one that matters. `cleanup_ran = 1` says a method was
    called; a thread that is gone says the method did something.
    """
    phone = setup.take(1)[0]
    source = setup.HangingSource([setup.record(phone)])

    before = {t.name for t in _threads()}
    started = time.monotonic()
    job = scrape_runner.run_job(db, source, search_term=setup.TERM,
                                timeout_seconds=1, screen=False)
    elapsed = time.monotonic() - started

    assert job.status == "timed_out"
    assert elapsed < 10, (
        f"the deadline was 1s and the source waits 30s; this took {elapsed:.1f}s, "
        f"so the runner waited for the source rather than stopping it")
    assert source.cleaned == 1, "cleanup() did not run on a timed-out job"
    assert job.cleanup_ran == 1, "the job row does not record that cleanup ran"

    # The worker only exits because cleanup() released what fetch() was blocked
    # on. Give it a moment — it was abandoned, not joined.
    for _ in range(50):
        if f"scrape:{source.name}" not in {t.name for t in _threads()} - before:
            break
        time.sleep(0.02)
    assert f"scrape:{source.name}" not in {t.name for t in _threads()} - before, (
        "the worker is still blocked — cleanup() was called but released nothing")


def test_a_timed_out_job_keeps_what_it_had_already_produced(db):
    """A timeout is not a rollback.

    Throwing the partial result away would mean a source slower than its budget
    produces nothing at all rather than producing less, which is the worse of
    the two failures by a distance.
    """
    phone = setup.take(1)[0]
    source = setup.HangingSource([setup.record(phone, name="Slow Kitchen")])
    job = scrape_runner.run_job(db, source, timeout_seconds=1, screen=False)

    assert job.status == "timed_out"
    assert job.records_yielded == 1 and job.prospects_created == 1
    assert db.query(Prospect).filter(Prospect.phone == phone).one().business_name \
        == "Slow Kitchen"


def test_a_source_that_raises_still_has_its_cleanup_run(db):
    """The exception path is a path, and the reference leak was one missing branch."""
    source = setup.ExplodingSource()
    job = scrape_runner.run_job(db, source, timeout_seconds=5, screen=False)

    assert job.status == "failed"
    assert "the search page changed shape" in (job.error or "")
    assert source.cleaned == 1 and job.cleanup_ran == 1


def test_a_job_whose_persistence_blows_up_is_not_reported_completed(db):
    """A partial failure must not report success.

    `_collect()` catches everything the *source* raises, so `failure` is set on
    that path — but a bug in ingestion or in the screening pass raises out of the
    try block with `failure` still None, and the `finally` would then fall
    straight through to `status = "completed"` on a job that blew up. Same
    defect as a blast that reached nobody reporting "completed", one level down.
    """
    from app.models.scrape import ScrapeJob

    phone = setup.take(1)[0]
    source = setup.FakeSource([setup.record(phone)], name="fake-ingest-boom")

    def boom(*args, **kwargs):
        raise RuntimeError("record_prospect returned something new")

    source.ingest = boom

    with pytest.raises(RuntimeError):
        scrape_runner.run_job(db, source, timeout_seconds=5, screen=False)

    job = (db.query(ScrapeJob).filter(ScrapeJob.source == "fake-ingest-boom")
           .order_by(ScrapeJob.id.desc()).first())
    assert job.status == "failed", (
        "a job that raised out of its persistence step is being reported as "
        "completed, with no record of what happened")
    assert "record_prospect returned something new" in (job.error or "")
    assert source.cleaned == 1 and job.cleanup_ran == 1


def test_a_job_records_what_it_attempted_what_it_produced_and_what_it_cost(db):
    """All three, because a run that returns nothing is indistinguishable from
    one that never ran unless the attempt is written down."""
    phones = setup.take(3)
    records = [setup.record(phones[0]), setup.record(phones[1]),
               ProspectRecord(phone=phones[2], source_url=setup.SOURCE_URL,
                              search_term=setup.TERM, buyer_rationale="")]
    provider = setup.CountingLookupProvider()
    job = scrape_runner.run_job(
        db, setup.FakeSource(records), search_term=setup.TERM,
        parameters={"radius": 150}, timeout_seconds=10, provider=provider)

    assert job.status == "completed"
    assert job.search_term == setup.TERM and job.parameters == {"radius": 150}
    assert job.records_yielded == 3
    assert job.prospects_created == 2 and job.records_invalid == 1
    assert job.lookups_performed == 2 and job.lookups_cached == 0
    assert float(job.cost) == pytest.approx(
        2 * settings.PROSPECT_LOOKUP_COST_PER_NUMBER)
    assert job.finished_at and job.cleanup_ran == 1


def test_a_second_job_over_the_same_numbers_pays_nothing(db):
    """The cache is what makes a nightly re-run free rather than $25 a night."""
    phones = setup.take(2)
    provider = setup.CountingLookupProvider()

    first = scrape_runner.run_job(
        db, setup.FakeSource([setup.record(p) for p in phones]),
        timeout_seconds=10, provider=provider)
    second = scrape_runner.run_job(
        db, setup.FakeSource([setup.record(p) for p in phones], name="fake-b"),
        timeout_seconds=10, provider=provider)

    assert first.lookups_performed == 2
    assert second.lookups_performed == 0 and second.lookups_cached == 2
    assert float(second.cost) == 0.0
    assert len(provider.calls) == 2


def test_a_rejected_number_is_never_paid_to_screen(db):
    """A rejection is permanent, so an answer about that number is worthless.

    The number is suppressed on arrival and therefore never enters the cache —
    so a screening pass that looked up "every number this run produced" would
    spend a fresh $0.0025 on it every night the search keeps finding it, for an
    answer nobody will ever act on. Asserted on the call count, because that is
    the only thing that distinguishes a cache hit from a purchase.
    """
    from app.services import prospect_service as service

    phone = setup.take(1)[0]
    setup.FakeSource([setup.record(phone)], name="fake-a").ingest(db)
    prospect = db.query(Prospect).filter(Prospect.phone == phone).one()
    service.reject(db, [prospect.id], "seller_or_consignor")

    provider = setup.CountingLookupProvider()
    job = scrape_runner.run_job(
        db, setup.FakeSource([setup.record(phone, name="Same Business Again")],
                             name="fake-d"),
        timeout_seconds=10, provider=provider)

    assert job.records_suppressed == 1
    assert provider.calls == [], (
        f"paid to screen {len(provider.calls)} number(s) a human already "
        f"rejected — that is a recurring charge for an answer nobody wants")
    assert job.lookups_performed == 0
    assert db.query(PhoneLookup).filter(PhoneLookup.phone == phone).first() is None


def test_the_runner_persists_through_the_sources_own_ingest(db):
    """One path from a source into these tables, not two.

    The runner drains `fetch()` on another thread and persists afterwards, so it
    would be easy for it to loop over `record_prospect()` itself — and that
    second call site is where the rationale check, the suppression check and the
    provenance requirement would stop applying. Asserted by watching `ingest()`
    get called rather than by reading the runner.
    """
    phone = setup.take(1)[0]
    source = setup.FakeSource([setup.record(phone)])
    seen = {}
    original = source.ingest

    def spy(db_, records=None, job=None, **kwargs):
        seen["records"] = list(records or [])
        seen["job"] = job
        return original(db_, records=seen["records"], job=job, **kwargs)

    source.ingest = spy
    job = scrape_runner.run_job(db, source, timeout_seconds=10, screen=False)

    assert len(seen["records"]) == 1, "the runner did not go through ingest()"
    assert seen["job"] is job
    assert job.prospects_created == 1


# ─── A5: scoring ────────────────────────────────────────────────────────────

def test_line_type_is_the_dominant_term(db):
    """A perfect landline must rank below an unscreened mobile.

    That is the finding, not a hedge: 39% of one live send was landlines, and no
    amount of category confidence makes a landline worth texting.
    """
    perfect_landline = prospect_scoring.score(
        line_type="landline", category_confidence=1.0, distance_miles=0.0,
        category_slug="food_service", source_count=5)
    bare_mobile = prospect_scoring.score(
        line_type="mobile", category_confidence=None, distance_miles=None,
        category_slug="food_service", source_count=1)

    assert bare_mobile > perfect_landline


def test_distance_is_a_cliff_at_the_radius_and_moot_for_a_national_category():
    near = prospect_scoring.score("mobile", 0.5, 10.0, "food_service", 1)
    far = prospect_scoring.score("mobile", 0.5, 400.0, "food_service", 1)
    assert near > far, "inside the radius must beat outside it"

    # `memorabilia` is configured national, so a dealer in Oregon is not taxed
    # for being in Oregon.
    assert (prospect_scoring.score("mobile", 0.5, 400.0, "memorabilia", 1)
            == prospect_scoring.score("mobile", 0.5, 1.0, "memorabilia", 1))


def test_an_unconfigured_category_falls_back_to_a_radius_not_to_national():
    """The permissive direction fails silently: a food-service search would
    start ranking Seattle and nothing on screen would say why."""
    assert prospect_scoring.radius_for("no_such_category") == \
        settings.PROSPECT_DEFAULT_RADIUS_MILES
    assert prospect_scoring.radius_for("memorabilia") is None


def test_corroboration_is_capped():
    """Beyond two extra sources it stops being evidence and starts measuring how
    many searches were run."""
    two = prospect_scoring.score("mobile", 0.5, 10.0, "food_service", 3)
    ten = prospect_scoring.score("mobile", 0.5, 10.0, "food_service", 11)
    assert two == ten


def test_a_lookup_landing_rescores_the_prospect(db):
    """The score's dominant term changes when screening runs, so the stored
    score has to change with it — otherwise the queue is ordered on line types
    from before anyone looked them up."""
    phone = setup.take(1)[0]
    setup.FakeSource([setup.record(phone)]).ingest(db)
    row = db.query(Prospect).filter(Prospect.phone == phone).one()
    unscreened = row.score

    # The SAME source and the SAME term, so no sighting is written and
    # `source_count` does not move. If this used a second source the score would
    # rise from corroboration and the test would pass whether or not screening
    # rescored anything — which is the shape of a test that proves nothing.
    provider = setup.CountingLookupProvider(answers={phone: "mobile"})
    scrape_runner.run_job(db, setup.FakeSource([setup.record(phone)], name="fake-a"),
                          timeout_seconds=10, provider=provider)
    db.refresh(row)
    assert row.source_count == 1, "precondition: corroboration did not move"
    assert provider.calls == [phone], "the re-run did not screen the prospect"
    assert row.score > unscreened


def test_the_two_constraints_that_are_the_guarantees_survived_the_migration(db):
    """`prospects.phone` and `phone_lookups.phone` are unique in the *schema*.

    Both are load-bearing rather than tidy. The first is why one business found
    by five searches is one prospect; the second is why a number is looked up
    once, ever, at $0.0025 a call. The scratch database this suite runs against
    was built by `alembic upgrade head`, so these read what the migration chain
    actually left behind rather than what the models describe.
    """
    from sqlalchemy import inspect
    from app.core.database import engine

    inspector = inspect(engine)
    for table, column in (("prospects", "phone"), ("phone_lookups", "phone"),
                          ("prospect_rejections", "phone")):
        unique = {tuple(i["column_names"]) for i in inspector.get_indexes(table)
                  if i["unique"]}
        assert (column,) in unique, (
            f"{table}.{column} is no longer uniquely indexed: {unique}")

    # A table-level UniqueConstraint rather than a unique index, so it comes
    # back from a different inspector call — asking the wrong one returns an
    # empty set, which reads exactly like a constraint that is not there.
    sighting = {tuple(sorted(c["column_names"]))
                for c in inspector.get_unique_constraints("prospect_sightings")}
    assert ("prospect_id", "search_term", "source") in sighting, (
        "the sighting constraint is gone, so a nightly re-run of one search "
        "would inflate corroboration and re-order the whole queue")


def _threads():
    import threading
    return threading.enumerate()
