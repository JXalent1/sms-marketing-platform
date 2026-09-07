"""Runs a prospect source under a deadline, and cleans up whatever it leaves.

## The failure this exists to prevent

The reference system ran one Playwright scrape a night and never closed the
browser context. Seventeen orphaned driver processes and 1.6 GB of RSS on a
3.9 GB box, discovered when something unrelated was OOM-killed. **This box has
2 GB.** Every exit path below — success, exception, timeout, a source that
raised on its first line — goes through the same `finally`, and the job row
records that it did.

`cleanup_ran` is a column rather than a log line because "the cleanup ran" has
to be a thing you can query for. A timed-out job with `cleanup_ran = 0` is the
leak, visible, before it is seventeen processes.

## Why the timeout works the way it does

`fetch()` is a synchronous generator, so the deadline is enforced in two layers:

  1. **Cooperatively.** The runner sets `source.stop_requested`; a well-behaved
     `fetch()` checks `should_stop()` in its paging loop and returns. This is
     the path that actually stops most work, and it keeps everything the source
     had already produced.
  2. **By abandonment.** A source blocked inside a third-party call never
     reaches its loop, so the runner stops waiting on it. The worker is a daemon
     thread: Python cannot kill a thread, and pretending otherwise is how a
     runner ends up believing it has cleaned up something still running.

That second layer is exactly why `cleanup()` is the source's job and not the
runner's. Only the source holds the handle to the browser context, the socket or
the driver process, and `cleanup()` is called on the runner's thread the moment
the deadline passes — it does not wait for the worker it can no longer stop.

**A source's `cleanup()` must therefore be safe to call while `fetch()` is still
running.** That is stated in `ProspectSource.cleanup()` too, because it is the
one thing a new source has to get right.

## Records already produced are kept

A timeout is not a rollback. Everything the source yielded before the deadline
is persisted after the run stops, through the source's own `ingest()`; throwing
it away would mean a source slower than its budget produces nothing at all
rather than producing less. The job says `timed_out`, and the counts say how
much it managed.

## Screening happens here, not in the source

A source produces records; it never decides whether something is textable. The
line-type pass runs after ingestion, over the numbers this job touched, through
`lookup_service` and its cache — so a number a previous job screened costs
nothing, and the job records how many calls it actually paid for.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from queue import Empty, Queue
from threading import Thread
from typing import Iterable, Optional
import logging
import time

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.prospect import Prospect
from app.models.scrape import ScrapeJob
from app.services import api_budget, lookup_service, prospect_service
from app.sms.phone import normalize
from app.sources import taxonomy
from app.sources.prospect_base import ProspectSource

logger = logging.getLogger("prospects")

# What the runner puts on the queue to say the source finished on its own.
_DONE = object()


def run_job(db: Session, source: ProspectSource, *,
            search_term: str = None, parameters: dict = None,
            timeout_seconds: int = None, screen: bool = True,
            provider=None, request_budget=None, **fetch_kwargs) -> ScrapeJob:
    """Run one source to completion, a deadline, or a failure. Always cleans up.

    Returns the `ScrapeJob` row, committed. The row exists before the first
    record is fetched, so a process killed mid-run leaves a `running` job behind
    rather than leaving nothing — an interrupted scrape you can see beats a
    scrape you have to infer from a gap in the prospects.
    """
    timeout = timeout_seconds or settings.PROSPECT_JOB_TIMEOUT_SECONDS

    job = ScrapeJob(
        source=source.name,
        search_term=search_term,
        parameters=dict(parameters or {}),
        status="running",
        started_at=datetime.now().isoformat(),
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    # A source that pays per request gets its monthly meter read from the
    # database here and handed in, because a source may not query — the same
    # seam `line_type_for()` has, one layer up. A source that costs nothing per
    # request gets no budget rather than an unlimited one.
    if request_budget is None:
        request_budget = api_budget.RequestBudget.for_month(db, source.name)
    if request_budget is not None:
        fetch_kwargs["request_budget"] = request_budget

    produced = []
    failure: Optional[BaseException] = None
    timed_out = False

    try:
        produced, failure, timed_out = _collect(source, timeout, fetch_kwargs)

        # Through the source's own `ingest()`, not through a loop of our own.
        # That method is the single path from a source into these tables, and a
        # second one here would be a second place the rationale check, the
        # suppression check and the provenance requirement have to be
        # remembered — the shape of every "guard with one call site" defect in
        # this codebase's history.
        result = source.ingest(db, records=produced, job=job)
        job.records_yielded += result.total
        db.commit()

        if screen:
            _screen(db, job, produced, provider=provider)

    except BaseException as e:                       # noqa: BLE001
        # Anything the ingest or the screening pass raised. Without this the
        # `finally` below falls through to `status = "completed"` on a job that
        # blew up — a partial failure reporting success, which is the same
        # defect `campaign_outcome` exists to prevent one level up. Recorded and
        # re-raised: a bug in ingestion is not something to swallow, and the job
        # row is still committed by the `finally` on the way out.
        failure = failure or e
        raise

    finally:
        # Every way out of this function passes here, including the one where
        # `_collect` raised before producing anything. The reference system's
        # leak was one missing branch, not a missing concept.
        try:
            source.cleanup()
            job.cleanup_ran = 1
        except Exception as e:                       # noqa: BLE001
            # A cleanup that throws is worse than one that does not run, because
            # it hides the original failure. Record it and leave cleanup_ran at
            # 0, which is the state somebody should be able to query for.
            logger.error("[%s] cleanup raised: %s", source.name, e)
            job.error = _join_errors(job.error, f"cleanup failed: {e}")

        # The second meter, recorded whatever happened — including on the paths
        # where the source was abandoned mid-request. A run whose spend is only
        # knowable on the success path is a run whose expensive failures are
        # invisible.
        #
        # On the abandonment path this is read while the worker may still be
        # alive, so it can under-report by the request in flight. Bounded rather
        # than open-ended: `request_stop()` was set before we stopped waiting,
        # and the source checks it at the top of every page, so an abandoned
        # worker makes at most one more request. Under-reporting a request means
        # the month's meter is one low, which is the direction that costs three
        # and a half cents rather than the direction that hides a leak.
        if request_budget is not None:
            job.api_requests = request_budget.requests_made
            job.api_requests_skipped = request_budget.skipped
            charged = request_budget.charged
            job.api_cost = str(charged) if charged else None
            request_budget.log_refusals()

        job.finished_at = datetime.now().isoformat()
        if timed_out:
            job.status = "timed_out"
            job.error = _join_errors(
                job.error, f"stopped after {timeout}s without finishing")
        elif failure is not None:
            job.status = "failed"
            job.error = _join_errors(job.error, f"{type(failure).__name__}: {failure}")
        else:
            job.status = "completed"
        db.commit()
        db.refresh(job)

    logger.info("[%s] job %s %s | yielded=%d created=%d corroborated=%d "
                "suppressed=%d excluded=%d known=%d invalid=%d | "
                "requests=%d skipped=%d lookups=%d cached=%d",
                source.name, job.id, job.status, job.records_yielded,
                job.prospects_created, job.prospects_corroborated,
                job.records_suppressed, job.records_excluded or 0,
                job.records_known or 0, job.records_invalid,
                job.api_requests or 0, job.api_requests_skipped or 0,
                job.lookups_performed, job.lookups_cached)
    return job


def searched_recently(db: Session, source_name: str,
                      repeat_days: int = None) -> set:
    """The ledger keys this source has already searched, recently and fully.

    **This is the Google meter's dedup, and it is the half that is easy to
    miss.** The carrier meter dedups per *number* — the cache, the rejection
    list and the contact check all stop a second $0.0025. Nothing there stops a
    second $0.035, because a request is charged for asking, not for what comes
    back. A nightly re-run of the same 89 searches would cost the same money
    every night to be told the same sixty businesses we already hold.

    Three conditions, and each one earns its place:

    - **`completed` only.** A `failed` or `timed_out` search did not finish, and
      re-running it costs $0.035 against a niche that is otherwise permanently
      incomplete. Cheap error, expensive error.
    - **`api_requests_skipped = 0`.** A job the request cap stopped is
      `completed` and *not* finished — its remaining pages were refused. Without
      this clause the cap would silently retire the searches it interrupted.
    - **Inside the window.** `GOOGLE_PLACES_QUERY_REPEAT_DAYS`.

    The window is filtered in SQL as a string and then confirmed by parsing,
    which is belt and braces rather than superstition: `started_at` has one
    writer (`run_job`, `datetime.now()`) and no server default, so the string
    compare is sound today — and `contact_list_members.added_at` is what it
    looks like when a second writer appears and nobody re-reads the comparison.
    A prefilter that is wrong drops a row from the ledger and costs one repeat
    request; it cannot suppress a search that should run.
    """
    days = settings.GOOGLE_PLACES_QUERY_REPEAT_DAYS if repeat_days is None \
        else repeat_days
    if days <= 0:
        return set()
    cutoff = datetime.now() - timedelta(days=days)
    # The "was it interrupted" clause is part of *this* query rather than a
    # second query subtracted from the result. Subtracting was the first version
    # and it was wrong in time: a search interrupted in March and completed
    # cleanly in April would have stayed permanently out of the ledger, so it
    # re-ran, and paid, every night forever.
    #
    # `is_(None)` is the other half. Jobs written before this column existed
    # hold NULL, and `api_requests_skipped = 0` is NULL — not true — in SQL, so
    # a bare equality test would quietly drop every pre-P2 job.
    rows = (db.query(ScrapeJob.search_term, ScrapeJob.started_at)
            .filter(ScrapeJob.source == source_name,
                    ScrapeJob.status == "completed",
                    ScrapeJob.search_term.isnot(None),
                    ScrapeJob.started_at >= cutoff.isoformat(),
                    or_(ScrapeJob.api_requests_skipped == 0,
                        ScrapeJob.api_requests_skipped.is_(None)))
            .all())
    fresh = set()
    for term, started_at in rows:
        try:
            if datetime.fromisoformat(started_at) >= cutoff:
                fresh.add(term)
        except (TypeError, ValueError):
            continue
    return fresh


def run_plan(db: Session, *, source_factory=None, group_slugs=None,
             category_slug: str = None, repeat_days: int = None,
             **job_kwargs) -> dict:
    """Run every search in the taxonomy's plan, one job each.

    One job per search rather than one job per run, and that is the design
    rather than a convenience. `scrape_jobs.search_term` becomes the ledger of
    what has been searched; `api_requests` and `cost` on that row are what *that
    search* cost; and the monthly meter is exact across runs because every job
    re-reads it from the table instead of carrying state between searches.

    Stops the whole plan the moment the request cap refuses. Everything already
    produced is persisted and every job already run is `completed` — a cap is a
    ceiling on spending, not a rollback.
    """
    factory = source_factory or _google_places
    name = factory().name
    plan = list(taxonomy.searches(group_slugs=group_slugs,
                                  category_slug=category_slug))
    already = searched_recently(db, name, repeat_days)

    summary = {"planned": len(plan), "ran": 0, "skipped_recent": 0,
               "skipped_cap": 0, "jobs": [], "created": 0, "corroborated": 0,
               "excluded": 0, "known": 0, "requests": 0}

    for index, search in enumerate(plan):
        if search.ledger_key in already:
            summary["skipped_recent"] += 1
            continue

        # Checked here as well as inside the source. The source's check stops a
        # run mid-search; this one stops the plan without opening a job row for
        # a search that cannot make its first request — an empty `completed`
        # job would land in the ledger above and retire the search unrun.
        budget = api_budget.RequestBudget.for_month(db, name)
        if budget is not None and not budget.allows():
            # Counted by walking what is left, not by arithmetic on the index.
            # The first version was `len(plan) - index - skipped_recent`, which
            # subtracts the already-searched twice — they are all at indices
            # below `index` and are therefore already outside the slice. It read
            # right and under-reported the moment anything had been skipped as
            # recent, which is every run after the first.
            summary["skipped_cap"] = sum(
                1 for later in plan[index:] if later.ledger_key not in already)
            budget.refuse(summary["skipped_cap"])
            budget.log_refusals()
            break

        job = run_job(db, factory(), search_term=search.ledger_key,
                      parameters={"group": search.group.slug,
                                  "sweep": search.sweep.name,
                                  "term": search.term,
                                  "radius_miles": search.radius_miles},
                      search=search, request_budget=budget, **job_kwargs)
        summary["ran"] += 1
        summary["jobs"].append(job.id)
        summary["created"] += job.prospects_created
        summary["corroborated"] += job.prospects_corroborated
        summary["excluded"] += job.records_excluded or 0
        summary["known"] += job.records_known or 0
        summary["requests"] += job.api_requests or 0

    logger.info("[%s] plan: %d searches, %d run, %d already searched, %d "
                "stopped by the request cap | %d created, %d requests",
                name, summary["planned"], summary["ran"],
                summary["skipped_recent"], summary["skipped_cap"],
                summary["created"], summary["requests"])
    return summary


def _google_places():
    """Imported lazily: `google_places` imports httpx only when it builds a
    client, and nothing else in this module needs the source to exist."""
    from app.sources.google_places import GooglePlacesSource
    return GooglePlacesSource()


def _collect(source: ProspectSource, timeout: int, fetch_kwargs: dict):
    """Drain `fetch()` under a deadline. Returns (records, failure, timed_out).

    The worker thread pushes each record onto a queue and the caller reads with
    the remaining budget, so a source that yields steadily is never interrupted
    for being long — only for being late. The thread is a daemon so an abandoned
    one cannot hold the process open at shutdown.
    """
    handoff: Queue = Queue()
    state = {"failure": None}

    def pump():
        try:
            for record in source.fetch(**fetch_kwargs):
                handoff.put(record)
                if source.should_stop():
                    break
        except BaseException as e:                   # noqa: BLE001
            state["failure"] = e
        finally:
            handoff.put(_DONE)

    worker = Thread(target=pump, name=f"scrape:{source.name}", daemon=True)
    worker.start()

    deadline = time.monotonic() + timeout
    produced = []
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            # Ask first, abandon second. A source that checks `should_stop()`
            # gets to close its own resources on its own thread, which is always
            # tidier than having them closed underneath it.
            source.request_stop()
            try:
                item = handoff.get(timeout=1.0)
            except Empty:
                logger.warning("[%s] did not stop when asked; abandoning the "
                               "worker and cleaning up", source.name)
                return produced, state["failure"], True
            if item is _DONE:
                break
            produced.append(item)
            continue

        try:
            item = handoff.get(timeout=min(remaining, 0.5))
        except Empty:
            continue
        if item is _DONE:
            break
        produced.append(item)

    worker.join(timeout=1.0)
    return produced, state["failure"], False


def _screen(db: Session, job: ScrapeJob, produced: list, provider=None) -> None:
    """Line-type the numbers this job touched, then rescore them.

    Scoped to the numbers this run produced, so a job that found 40 businesses
    does not re-walk 10,000. Cached numbers cost nothing and are counted
    separately, which is what makes the job's cost line a measurement rather
    than an estimate.

    **Scoped to what the run produced, not to the sightings it wrote**, and the
    difference is a real hole rather than a style point. A re-run of the same
    search on the same source writes no new sighting — that idempotence is what
    stops corroboration inflating — so scoping on `job_id` would mean a prospect
    that was never screened the first time could never be screened by a repeat
    of the search that found it. It would sit in the queue as `unknown` forever,
    unpromotable, with nothing on screen explaining why.

    **Only prospects still awaiting a decision are screened, and that is money.**
    A number a human rejected is never looked up: `record_prospect()` suppresses
    it on arrival, so it is never in the cache, so every re-run of the search
    that keeps finding it would be a fresh $0.0025 for an answer nobody will ever
    act on. Filtering on `pending` here rather than merely on "is a prospect" is
    the difference between paying once per business and paying every night for
    the ones already turned down. Promoted prospects are excluded for the weaker
    reason that they were screened to get promoted, so they would be cache hits.
    """
    wanted = {normalize(getattr(record, "phone", "") or "") for record in produced}
    wanted.discard("")
    if not wanted:
        return

    phones = [row[0] for row in
              db.query(Prospect.phone)
              .filter(Prospect.phone.in_(wanted), Prospect.status == "pending")
              .all()]
    if not phones:
        return

    outcome = lookup_service.screen(db, phones, provider=provider)
    job.lookups_performed += outcome["performed"]
    job.lookups_cached += outcome["cached"]
    job.cost = str(Decimal(job.cost or "0") + Decimal(outcome["cost"]))

    # The score's dominant term just changed for every one of these, so it is
    # recomputed here rather than left until somebody notices the queue is
    # ordered on stale line types.
    rows = db.query(Prospect).filter(Prospect.phone.in_(phones)).all()
    for prospect in rows:
        prospect_service.rescore(
            db, prospect,
            line_type=outcome["results"].get(prospect.phone, "unknown"),
            commit=False)
    db.commit()


def _join_errors(existing: Optional[str], addition: str) -> str:
    """Keep both. A cleanup failure must not overwrite the reason for the run's."""
    return f"{existing}; {addition}" if existing else addition
