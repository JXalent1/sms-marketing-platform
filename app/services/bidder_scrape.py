"""The daily read of his registered bidders: one at a time, under a deadline.

`app/sources/liveauctioneers.py` produces `BidderRecord`s and never touches the
database. Everything that decides what a record *becomes* is here, in the layer
that is allowed to touch both — `CLAUDE.md`'s layering rule, and the reason the
reference scraper's `save_profile()` could not be ported as it was.

A new service rather than an addition to `contact_service` (494 lines) or
`scrape_runner` (451): both are named in L1's file list and neither can take a
runner and a persistence pass under the 500-line rule. `status.md` records it.

## What a record goes through, in order

1. **No phone — not a contact.** `contacts.phone` is the identity and is NOT
   NULL; a phoneless bidder has nothing to key a contact or a profile on, and a
   second table keyed on name + sale would be the reference system's second
   identity, which is what the dedup exists to prevent. Counted, not kept: he
   still has them on the platform, and `no_phone` says how many.
2. **One number, two names — keep the first, count it.** A misread until proved
   otherwise (see the stale-panel note in `liveauctioneers.py`), and a phone-
   keyed upsert would otherwise merge two people's behaviour into one row.
3. **The blocklist, without exception.** Skipped outright, as
   `import_service.commit()` skips an opted-out CSV row: not created, not
   updated, no profile written. Somebody who texted STOP and then registered for
   another sale is still somebody who texted STOP.
4. **Line-type screening, when it is on** (a lookup provider is configured and
   the monthly cap is positive). The same gate a prospect passes:
   `PROMOTABLE_LINE_TYPES` only, and `unknown` does not pass. A held bidder is
   re-read and re-screened tomorrow — a failed lookup is never cached — and is
   not deactivated if he is already a contact from a CSV; this path simply does
   not touch him. Off, bidders land as a CSV row does, unscreened.
5. **`ContactSource.ingest()`** — the one path every source takes into
   `contacts`, through `contact_service.upsert_contact()` and the unique phone
   index. Onto one list per platform, `platforms.list_name()`.
6. **The profile row**, one per contact per platform, beside the contact.

## One at a time

A process-wide lock taken without blocking, plus a check for a `running` row
younger than the deadline, so a second run — a manual one while the scheduler's
is going, or a second process — **refuses** rather than queueing a second
Chromium behind the first on a 2 GB box. APScheduler's `max_instances=1` covers
the schedule; the lock covers everything else that can call this.
"""

from datetime import datetime, timedelta
from typing import List, Optional
import asyncio
import logging
import os
import threading

from sqlalchemy.orm import Session

from app.core import clock
from app.core.config import settings
from app.models.bidder_profile import BidderProfile, BidderScrapeRun
from app.models.contact import Contact
from app.services import blocklist_service, lookup_service
from app.sms.lookup import get_lookup_provider
from app.sms.phone import is_valid, normalize
from app.sources import platforms
from app.sources.auction_scraper_base import (AuctionScraperBase, BidderRecord,
                                              profile_dir_problem)

logger = logging.getLogger("bidders")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
JOB_ID = "daily_bidder_scrape"

_RUN_LOCK = threading.Lock()

# A run that read less than this share of the rows it saw is `incomplete`. A
# panel that fails to open now and then is normal; a tenth of the list is the
# page misbehaving, and saying "completed" over it is how a shrinking list goes
# unnoticed.
READ_SHARE_FLOOR = 0.9
COUNT_COLUMNS = ("rows_seen", "rows_expected", "bidders_read", "no_phone", "repeats",
                 "phone_conflicts", "opted_out", "screened_out", "invalid",
                 "contacts_created", "contacts_updated", "profiles_written",
                 "profile_conflicts")

# Fields copied from a record onto its profile row, record name -> column name.
_PROFILE_FIELDS = {
    "username": "platform_username", "address": "address", "location": "location",
    "member_since": "member_since", "card_on_file": "card_on_file",
    "tax_exempt": "tax_exempt", "auctions_attended": "auctions_attended",
    "bids_placed": "bids_placed", "items_won": "items_won",
    "payment_rate_pct": "payment_rate_pct", "avg_hammer_cents": "avg_hammer_cents",
    "avg_hammer_is_ceiling": "avg_hammer_is_ceiling",
    "disputes_open": "disputes_open", "disputes_closed": "disputes_closed",
}


class ScrapeRefused(RuntimeError):
    """A run was asked for while one is already going. Nothing was started."""


def configured() -> bool:
    """All three credentials present. Blank is the off switch (decisions/014)."""
    return bool(settings.LA_USERNAME and settings.LA_PASSWORD and settings.LA_HOUSE_ID)


def trigger():
    """The daily trigger, in the application's zone and nobody else's.

    The reference app ran its own APScheduler with its own `timezone=eastern`.
    Two definitions of the client's zone is how the next drift starts, so the
    zone is read from `clock.ZONE` — `APP_TIMEZONE`, resolved once — at the
    moment the trigger is built, and this function takes no zone argument.
    """
    from apscheduler.triggers.cron import CronTrigger
    return CronTrigger(hour=settings.BIDDER_SCRAPE_HOUR,
                       minute=settings.BIDDER_SCRAPE_MINUTE, timezone=clock.ZONE)


def screening_enabled() -> bool:
    """On when a lookup provider is configured and the monthly cap is positive."""
    return (get_lookup_provider().name != "disabled"
            and lookup_service.monthly_cap() > 0)


def _running_elsewhere(db: Session, platform: str, timeout: int) -> bool:
    """A `running` row young enough to still be alive. A row older than the
    deadline is a process that died mid-run and must not block tomorrow's."""
    cutoff = datetime.now() - timedelta(seconds=timeout + 600)
    for (started,) in (db.query(BidderScrapeRun.started_at)
                       .filter(BidderScrapeRun.platform == platform,
                               BidderScrapeRun.status == "running").all()):
        try:
            if datetime.fromisoformat(started) >= cutoff:
                return True
        except (TypeError, ValueError):
            continue
    return False


def run_scrape(db: Session, source: AuctionScraperBase = None, *,
               timeout_seconds: int = None, provider=None,
               platform: str = platforms.LIVEAUCTIONEERS,
               **collect_kwargs) -> BidderScrapeRun:
    """Read one platform's registered bidders and land them. Raises
    `ScrapeRefused` if a run is already going; otherwise returns the run row."""
    if not _RUN_LOCK.acquire(blocking=False):
        logger.warning("[%s] a bidder read is already running; refused", platform)
        raise ScrapeRefused("A bidder read is already running")
    try:
        timeout = timeout_seconds or settings.BIDDER_SCRAPE_TIMEOUT_SECONDS
        if _running_elsewhere(db, platform, timeout):
            logger.warning("[%s] another process has a bidder read running; refused",
                           platform)
            raise ScrapeRefused("A bidder read is already running")
        source = source or platforms.scraper_class(platform)()
        return _run(db, source, timeout, provider, collect_kwargs)
    finally:
        _RUN_LOCK.release()


def _run(db: Session, source: AuctionScraperBase, timeout: int, provider,
         collect_kwargs: dict) -> BidderScrapeRun:
    run = BidderScrapeRun(platform=source.platform, status="running",
                          started_at=datetime.now().isoformat())
    db.add(run)
    db.commit()

    failure: Optional[BaseException] = None
    timed_out = False
    problem = profile_dir_problem(source.profile_dir(), PROJECT_ROOT)
    try:
        if problem:
            raise RuntimeError(problem)
        _check_writable(source.profile_dir())
        # `run()` spins its own event loop. This function is called from the
        # scheduler's worker thread, so the application's loop — the one the
        # send path runs on — never carries an hour of browser clicks.
        source.run(timeout, **collect_kwargs)
    except asyncio.TimeoutError:
        timed_out = True
    except Exception as e:                            # noqa: BLE001
        failure = e
    finally:
        # Asked of the source's state, not of whether the teardown said it ran:
        # a missing `finally` sets nothing, and "nothing said otherwise" is how
        # the reference box reached seventeen orphans. A browser never started
        # is clean; one still held, or one whose teardown failed, is not.
        run.cleanup_ran = 0 if (source.browser_open()
                                or source.shutdown_clean is False) else 1

    try:
        # Whatever was read before a failure or the deadline is landed: a
        # timeout is not a rollback, and page 1 of a run that died on page 3 is
        # still his bidders.
        run.rows_seen = source.rows_seen
        run.rows_expected = source.rows_expected
        _persist(db, source, list(source.records), run, provider)
    except Exception as e:                            # noqa: BLE001
        # `ingest()` has already committed the contacts, so the counts are
        # true even though what came after them failed; a rollback would reset
        # them to zero over contacts that exist.
        counts = {c: getattr(run, c) for c in COUNT_COLUMNS}
        db.rollback()
        for column, value in counts.items():
            setattr(run, column, value)
        failure = failure or e
    finally:
        run.finished_at = datetime.now().isoformat()
        if timed_out:
            run.status = "timed_out"
            run.error = f"stopped after {timeout}s; the browser was closed"
        elif failure is not None:
            run.status = "failed"
            run.error = f"{type(failure).__name__}: {str(failure)[:300]}"
        else:
            shortfall = _shortfall(run)
            run.status = "incomplete" if shortfall else "completed"
            run.error = shortfall
        db.add(run)
        db.commit()

    logger.info("[%s] run %s %s | rows=%d read=%d created=%d updated=%d "
                "no_phone=%d opted_out=%d screened_out=%d conflicts=%d cleanup=%d",
                run.platform, run.id, run.status, run.rows_seen, run.bidders_read,
                run.contacts_created, run.contacts_updated, run.no_phone,
                run.opted_out, run.screened_out, run.phone_conflicts, run.cleanup_ran)
    return run


def _shortfall(run: BidderScrapeRun) -> Optional[str]:
    """Why a run that raised nothing still did not read the list, or None."""
    if run.rows_expected and run.rows_seen < run.rows_expected:
        return (f"saw {run.rows_seen} of the {run.rows_expected} rows the page "
                "reported; paging stopped early")
    if run.rows_seen and run.bidders_read < READ_SHARE_FLOOR * run.rows_seen:
        return (f"read {run.bidders_read} of {run.rows_seen} rows; the rest "
                "never opened a profile")
    return None


def _check_writable(path: str) -> None:
    """Fail with the fix named, not with a bare PermissionError from Chromium.

    The production unit mounts the home directory read-only (`ProtectHome=`)
    and allows writes only inside the project, where `profile_dir_problem()`
    refuses. The remedy is a directory the unit makes writable, e.g.
    `StateDirectory=` and `BROWSER_PROFILE_ROOT=/var/lib/<it>` — status.md, L1.
    """
    try:
        os.makedirs(path, mode=0o700, exist_ok=True)
        probe = os.path.join(path, ".write-probe")
        with open(probe, "w"):
            pass
        os.remove(probe)
    except OSError as e:
        raise RuntimeError(
            f"BROWSER_PROFILE_ROOT is not writable by this service "
            f"({type(e).__name__}); give it a StateDirectory outside the project") from None


def _dedupe(records: List[BidderRecord], run: BidderScrapeRun) -> List[BidderRecord]:
    """Steps 1 and 2: drop the phoneless and the unusable, one record per number."""
    kept, by_phone = [], {}
    for record in records:
        if not (record.phone or "").strip():
            run.no_phone += 1
            continue
        phone = normalize(record.phone)
        if not phone or not is_valid(phone):
            run.invalid += 1
            continue
        first = by_phone.get(phone)
        if first is not None:
            same = (first.full_name or "").casefold() == (record.full_name or "").casefold()
            if same:
                run.repeats += 1
            else:
                run.phone_conflicts += 1
                logger.warning("[%s] one number read for two bidder names; kept the "
                               "first", run.platform)
            continue
        record.phone = phone
        by_phone[phone] = record
        kept.append(record)
    return kept


def _persist(db: Session, source: AuctionScraperBase, records: List[BidderRecord],
             run: BidderScrapeRun, provider) -> None:
    run.bidders_read = len(records)
    kept = _dedupe(records, run)

    blocked = blocklist_service.load_blocked_set(db)
    run.opted_out = sum(1 for r in kept if r.phone in blocked)
    kept = [r for r in kept if r.phone not in blocked]

    if kept and screening_enabled():
        outcome = lookup_service.screen(db, [r.phone for r in kept], provider=provider)
        passed = [r for r in kept if outcome["results"].get(r.phone)
                  in lookup_service.PROMOTABLE_LINE_TYPES]
        run.screened_out = len(kept) - len(passed)
        kept = passed

    if not kept:
        db.commit()
        return

    result = source.ingest(db, list_name=platforms.list_name(source.platform),
                           records=kept)
    run.contacts_created = result.created
    run.contacts_updated = result.updated
    run.invalid += result.invalid
    run.profiles_written, run.profile_conflicts = _write_profiles(
        db, source.platform, kept)
    db.commit()


def _write_profiles(db: Session, platform: str, records: List[BidderRecord]) -> tuple:
    """One row per contact per platform: created once, refreshed every read."""
    phones = [r.phone for r in records]
    ids = {}
    for start in range(0, len(phones), 500):
        ids.update({phone: cid for cid, phone in db.query(Contact.id, Contact.phone)
                    .filter(Contact.phone.in_(phones[start:start + 500])).all()})
    existing = {p.contact_id: p for p in db.query(BidderProfile).filter(
        BidderProfile.platform == platform,
        BidderProfile.contact_id.in_(list(ids.values()) or [-1])).all()}

    now = datetime.now().isoformat()
    written = profile_conflicts = 0
    for record in records:
        contact_id = ids.get(record.phone)
        if contact_id is None:
            continue
        row = existing.get(contact_id)
        if (row is not None and row.platform_username and record.username
                and row.platform_username != record.username):
            # Another bidder on this platform already owns this number's
            # profile. Overwriting would make the contact one person and the
            # behaviour beside it another, and flip back whenever the page
            # order does.
            profile_conflicts += 1
            continue
        if row is None:
            row = BidderProfile(contact_id=contact_id, platform=platform,
                                first_seen_at=now, scraped_at=now)
            db.add(row)
            existing[contact_id] = row
        for field, column in _PROFILE_FIELDS.items():
            setattr(row, column, getattr(record, field))
        row.scraped_at = now
        written += 1
    return written, profile_conflicts


def daily_job() -> None:
    """The scheduled entry point. Never raises: a job that raises is logged by
    APScheduler and nobody reads that log, so the outcome is logged here."""
    from app.core.database import SessionLocal
    db = SessionLocal()
    try:
        run = run_scrape(db)
        level = logging.INFO if run.status == "completed" else logging.ERROR
        logger.log(level, "Daily bidder read %s (run %s)", run.status, run.id)
    except ScrapeRefused:
        pass
    except Exception:                                 # noqa: BLE001
        logger.exception("Daily bidder read raised")
    finally:
        db.close()
