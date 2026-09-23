"""Session L1 A3/A4: the rules a scraped bidder is held to, and the box the
browser runs on. Criteria 5, 6, 7 and 9 of `sessions/session-L1.md`.
"""

import asyncio
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.models.bidder_profile import BidderProfile, BidderScrapeRun
from app.models.contact import Contact
from app.models.contact_list import ContactList, ContactListMember
from app.services import bidder_scrape, blocklist_service, contact_service
from app.sms.lookup import LineTypeProvider, LineTypeResult
from app.sources.auction_scraper_base import SelectorDrift, profile_dir_problem
from tests._bidder_setup import (LIST_NAME, PREFIX, db,  # noqa: F401
                                 fixture_contacts, scrape)
from tests._la_portal import Portal, blocking_portal, load_bidders, make_source

BIDDERS = load_bidders()
DISTINCT = len({b["phone"][-4:] for b in BIDDERS if b["phone"]})
BLOCKED = "+19547000007"


# ─── Criterion 5: a blocked bidder stays blocked ─────────────────────────────

def test_a_blocked_bidder_who_registers_again_stays_blocked(db, tmp_path):
    before = contact_service.upsert_contact(db, phone=BLOCKED, full_name="Opted Out",
                                            source="csv")
    stamp = before.updated_at
    blocklist_service.block_number(db, BLOCKED, reason="stop_keyword", source="webhook")

    run, _, _ = scrape(db, tmp_path=tmp_path)
    assert run.status == "completed", run.error
    assert run.opted_out == 1
    assert blocklist_service.is_blocked(db, BLOCKED)
    db.expire_all()
    contact = db.query(Contact).filter(Contact.phone == BLOCKED).one()
    # Not touched at all, as import_service skips an opted-out CSV row: not
    # renamed, not re-stamped, not put on the platform's list, no profile.
    assert (contact.full_name, contact.updated_at, contact.source) == ("Opted Out", stamp, "csv")
    assert db.query(BidderProfile).filter(BidderProfile.contact_id == contact.id).count() == 0
    listed = (db.query(ContactListMember).join(ContactList)
              .filter(ContactList.name == LIST_NAME,
                      ContactListMember.contact_id == contact.id).count())
    assert listed == 0
    assert run.contacts_created == DISTINCT - 1          # everyone but the blocked one
    assert BLOCKED in blocklist_service.load_blocked_set(db)


def test_a_blocked_bidder_who_was_never_a_contact_does_not_become_one(db, tmp_path):
    blocklist_service.block_number(db, BLOCKED, reason="stop_keyword", source="webhook")
    run, _, _ = scrape(db, tmp_path=tmp_path)
    assert run.opted_out == 1
    assert db.query(Contact).filter(Contact.phone == BLOCKED).count() == 0


# ─── Criterion 6: a scrape already running refuses a second ─────────────────

def test_a_running_scrape_refuses_a_second_rather_than_queueing(db, tmp_path):
    from app.core.database import SessionLocal
    portal, release = blocking_portal()
    outcome = {}

    def first():
        own = SessionLocal()
        try:
            outcome["run"] = scrape(own, portal=portal, tmp_path=tmp_path)[0].status
        finally:
            own.close()

    worker = threading.Thread(target=first)
    worker.start()
    try:
        deadline = time.monotonic() + 10
        while not portal.gotos and time.monotonic() < deadline:
            time.sleep(0.01)
        assert portal.gotos, "the first scrape never started"
        started = time.monotonic()
        second = Portal()
        with pytest.raises(bidder_scrape.ScrapeRefused):
            scrape(db, portal=second, tmp_path=tmp_path)
        # Refused, not queued: it returned at once and never opened a browser.
        assert time.monotonic() - started < 2
        assert second.launch_kwargs is None and second.gotos == []
    finally:
        release.set()
        worker.join(30)
    assert outcome["run"] == "completed"


def test_the_lock_refuses_in_the_window_the_row_check_cannot_see(db, tmp_path,
                                                                monkeypatch):
    """Two guards refuse a second run, and each must be able to do it alone.
    The row check reads a row the first run commits a moment after it starts;
    between the check and that commit, only the process lock can see the first
    run. Construct that window: the row check sees nothing."""
    monkeypatch.setattr(bidder_scrape, "_running_elsewhere", lambda *a: False)
    test_a_running_scrape_refuses_a_second_rather_than_queueing(db, tmp_path)


def test_a_running_row_from_another_process_refuses_and_a_dead_one_does_not(db, tmp_path):
    db.add(BidderScrapeRun(platform="liveauctioneers", status="running",
                           started_at=datetime.now().isoformat()))
    db.commit()
    with pytest.raises(bidder_scrape.ScrapeRefused):
        scrape(db, tmp_path=tmp_path)

    db.query(BidderScrapeRun).delete()
    db.add(BidderScrapeRun(platform="liveauctioneers", status="running",
                           started_at=(datetime.now() - timedelta(days=1)).isoformat()))
    db.commit()
    run, _, _ = scrape(db, tmp_path=tmp_path, timeout=60)
    assert run.status == "completed"


# ─── Criterion 7: the browser closes on every path ──────────────────────────

def test_the_browser_closes_after_a_successful_run(db, tmp_path):
    run, portal, source = scrape(db, tmp_path=tmp_path)
    assert run.status == "completed"
    assert portal.browser_closed and not source.browser_open()
    assert run.cleanup_ran == 1


def test_the_browser_closes_when_the_run_fails(db, tmp_path):
    run, portal, source = scrape(db, portal=Portal(logged_in=False, login_fails=True),
                                 tmp_path=tmp_path)
    assert run.status == "failed" and "Login failed" in run.error
    assert portal.browser_closed and not source.browser_open()
    assert run.cleanup_ran == 1


def test_the_deadline_kills_the_browser_not_just_the_job(db, tmp_path):
    """The path the reference system leaked on. The scrape is hung inside a
    navigation that will never return; the deadline must cancel it and the
    cancellation must close the context and stop the driver."""
    portal = Portal(hang_on="goto")
    started = time.monotonic()
    run, portal, source = scrape(db, portal=portal, tmp_path=tmp_path, timeout=1)
    assert time.monotonic() - started < 20
    assert run.status == "timed_out"
    assert portal.closes == 1 and portal.stops == 1
    assert portal.browser_closed and not source.browser_open()
    assert run.cleanup_ran == 1


def test_a_teardown_that_fails_is_recorded_not_assumed(db, tmp_path):
    """The column must be able to say no, or it proves nothing when it says yes."""
    run, portal, _ = scrape(db, portal=Portal(broken_teardown=True), tmp_path=tmp_path)
    assert portal.closes == 1 and portal.stops == 1   # the driver still got its turn
    assert run.cleanup_ran == 0


def test_a_close_that_hangs_is_bounded_and_the_driver_still_stops(db, tmp_path):
    """Each teardown step has its own bound. Without it a hung close() holds
    the deadline open indefinitely — the review measured it still hung at 12 s."""
    portal = Portal(hang_close=True)
    source = make_source(portal, str(tmp_path))
    source.SHUTDOWN_STEP_SECONDS = 0.3
    started = time.monotonic()
    run = bidder_scrape.run_scrape(db, source, timeout_seconds=30)
    assert time.monotonic() - started < 10
    assert portal.stops == 1 and not portal.driver_running
    assert run.cleanup_ran == 0


def test_a_profile_directory_the_service_cannot_write_names_the_fix(db, tmp_path):
    locked = tmp_path / "read-only"
    locked.mkdir(mode=0o500)
    portal = Portal()
    run = bidder_scrape.run_scrape(db, make_source(portal, str(locked)),
                                   timeout_seconds=30)
    locked.chmod(0o700)
    assert run.status == "failed" and "StateDirectory" in run.error
    assert portal.launch_kwargs is None


def test_a_teardown_that_never_ran_is_recorded_not_assumed(db, tmp_path):
    """A missing `finally` sets nothing at all — neither True nor False — and
    "nothing said it failed" is not "it ran"."""
    portal = Portal()
    source = make_source(portal, str(tmp_path))

    async def skipped():
        pass
    source.shutdown = skipped
    run = bidder_scrape.run_scrape(db, source, timeout_seconds=30)
    assert portal.closes == 0 and source.browser_open()
    assert run.cleanup_ran == 0


def test_a_deadline_after_page_one_keeps_page_one(db, tmp_path):
    """A timeout is not a rollback: the bidders already read are his."""
    run, portal, _ = scrape(db, portal=Portal(hang_on="next"), tmp_path=tmp_path,
                            timeout=2)
    assert run.status == "timed_out" and portal.browser_closed
    assert 100 < run.contacts_created < DISTINCT
    assert len(fixture_contacts(db)) == run.contacts_created


def test_a_changed_page_fails_loudly_instead_of_reading_zero(db, tmp_path):
    """The platform renamed the panel's labels. The reference scraper reported
    that as a successful scrape of nobody."""
    run, portal, _ = scrape(db, portal=Portal(markers=False), tmp_path=tmp_path)
    assert run.status == "failed" and SelectorDrift.__name__ in run.error
    assert fixture_contacts(db) == []
    assert portal.browser_closed
    # And it says so after the first few rows, not after an hour of misses: at
    # real timings every miss costs PANEL_WAIT_SECONDS, and ~425 of them would
    # reach the deadline and report `timed_out` instead of the cause.
    assert "first 5" in run.error and portal.page.page_no == 1


def test_a_short_list_on_a_changed_page_still_fails(db, tmp_path):
    """Fewer rows than the early check needs: only the end-of-run check can see
    it, so this is the arrangement where that check decides alone."""
    run, _, _ = scrape(db, portal=Portal(bidders=load_bidders()[:3], markers=False),
                       tmp_path=tmp_path)
    assert run.status == "failed" and "3 bidder rows" in run.error


def test_a_hidden_phone_field_fails_instead_of_creating_nobody(db, tmp_path):
    """Panels still open, every bidder reads as phoneless: the review measured
    this as `completed`, 131 read, 0 created."""
    run, _, _ = scrape(db, portal=Portal(phones_hidden=True), tmp_path=tmp_path)
    assert run.status == "failed" and "phone" in run.error
    assert fixture_contacts(db) == []


def test_paging_that_stops_early_is_incomplete_not_completed(db, tmp_path):
    run, _, _ = scrape(db, portal=Portal(hang_on="nopager"), tmp_path=tmp_path)
    assert run.status == "incomplete", (run.status, run.error)
    assert run.rows_expected == len(BIDDERS) and run.rows_seen == 120
    assert "120 of the 131" in run.error
    assert run.contacts_created > 0            # what was read is still his


def test_a_run_that_read_too_few_of_its_rows_is_incomplete():
    run = BidderScrapeRun(rows_seen=120, rows_expected=None, bidders_read=100)
    assert "100 of 120" in bidder_scrape._shortfall(run)
    run.bidders_read = 118
    assert bidder_scrape._shortfall(run) is None


def test_two_bidders_with_one_name_are_two_contacts(db, tmp_path):
    """The reference clicked the first row *with that name*, so the second
    namesake was never opened and was counted as a harmless repeat."""
    bidders = load_bidders()
    bidders[29]["name"] = bidders[9]["name"]
    run, _, _ = scrape(db, portal=Portal(bidders=bidders), tmp_path=tmp_path)
    assert run.repeats == 1
    assert db.query(Contact).filter(Contact.phone == "+19547000030").count() == 1


def test_a_shared_number_keeps_one_persons_profile_across_runs(db, tmp_path):
    """Rows 60 and 69 share a number. Swap their order between runs: the
    contact and the behaviour beside it must stay the same person."""
    scrape(db, tmp_path=tmp_path)
    swapped = load_bidders()
    swapped[60], swapped[69] = swapped[69], swapped[60]
    run, _, _ = scrape(db, portal=Portal(bidders=swapped), tmp_path=tmp_path)
    assert run.profile_conflicts == 1
    contact = db.query(Contact).filter(Contact.phone == "+19547000061").one()
    profile = db.query(BidderProfile).filter(BidderProfile.contact_id == contact.id).one()
    assert contact.full_name.endswith(" 61") and profile.platform_username == "bidder061"


def test_a_slow_panel_is_never_read_as_the_previous_bidders(db, tmp_path):
    """After a click the page still shows the last bidder's panel for a few
    reads. The reference wait accepted it — markers present — and gave bidder
    2 bidder 1's phone. Every contact here must carry its own username."""
    run, _, _ = scrape(db, portal=Portal(panel_lag=3), tmp_path=tmp_path)
    assert run.contacts_created == DISTINCT and run.phone_conflicts == 1
    rows = (db.query(Contact.phone, BidderProfile.platform_username)
            .join(BidderProfile, BidderProfile.contact_id == Contact.id).all())
    assert rows and all(user == f"bidder{phone[-3:]}" for phone, user in rows
                        if phone != "+19547000061"), rows[:5]


def test_a_profile_directory_inside_the_project_refuses_to_start(db, tmp_path):
    inside = bidder_scrape.PROJECT_ROOT + "/data/browser"
    assert profile_dir_problem(inside, bidder_scrape.PROJECT_ROOT)
    assert profile_dir_problem(str(tmp_path), bidder_scrape.PROJECT_ROOT) is None
    portal = Portal()
    source = make_source(portal, bidder_scrape.PROJECT_ROOT + "/data/browser")
    run = bidder_scrape.run_scrape(db, source, timeout_seconds=30)
    assert run.status == "failed" and "outside" in run.error
    assert portal.launch_kwargs is None


# ─── Criterion 9: the schedule reads the app's zone ─────────────────────────

def test_the_trigger_follows_the_apps_zone_whatever_it_is(monkeypatch):
    from app.core import clock
    for zone in ("America/Los_Angeles", "Pacific/Honolulu", "America/New_York"):
        monkeypatch.setattr(clock, "ZONE", ZoneInfo(zone))
        assert str(bidder_scrape.trigger().timezone) == zone


def _registered_jobs(monkeypatch, configured: bool):
    import app.main as main
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    registered = []
    fake = AsyncIOScheduler()
    fake.add_job = lambda func, trigger, *, id, **kw: registered.append((id, func, trigger, kw))
    fake.start = lambda *a, **k: None
    fake.shutdown = lambda *a, **k: None
    monkeypatch.setattr(main, "scheduler", fake)
    for key in ("LA_USERNAME", "LA_PASSWORD", "LA_HOUSE_ID"):
        monkeypatch.setattr(bidder_scrape.settings, key, "x" if configured else "")

    async def drain():
        async with main.lifespan(main.app):
            pass
    asyncio.run(drain())
    return {job_id: (func, trigger, kw) for job_id, func, trigger, kw in registered}


def test_the_daily_job_registers_in_the_apps_zone_and_one_at_a_time(monkeypatch):
    from app.core import clock
    monkeypatch.setattr(clock, "ZONE", ZoneInfo("America/Denver"))
    jobs = _registered_jobs(monkeypatch, configured=True)
    func, trigger, kw = jobs[bidder_scrape.JOB_ID]
    assert func is bidder_scrape.daily_job
    assert str(trigger.timezone) == "America/Denver"
    assert kw.get("max_instances") == 1
    assert kw.get("misfire_grace_time", 1) >= 600, (
        "a one-second grace skips the whole day when the loop stalls at 09:00")
    assert not asyncio.iscoroutinefunction(bidder_scrape.daily_job), (
        "a coroutine job runs on the event loop the send path shares")


def test_blank_credentials_register_nothing(monkeypatch):
    assert bidder_scrape.JOB_ID not in _registered_jobs(monkeypatch, configured=False)


# ─── A3: the line-type gate, when screening is on ───────────────────────────

class _Lines(LineTypeProvider):
    """Every number ending in 3 is a landline; 5 fails to answer; the rest mobile."""
    name = "fixture"

    def __init__(self):
        self.calls = []

    def lookup(self, phone):
        self.calls.append(phone)
        if phone.endswith("3"):
            return LineTypeResult(line_type="landline", ok=True, cost="0.0025")
        if phone.endswith("5"):
            return LineTypeResult(line_type="unknown", ok=False, error="timeout")
        return LineTypeResult(line_type="mobile", ok=True, cost="0.0025")


def test_screening_on_holds_landlines_and_unanswered_numbers(db, tmp_path, monkeypatch):
    monkeypatch.setattr(bidder_scrape, "screening_enabled", lambda: True)
    blocklist_service.block_number(db, BLOCKED, reason="stop_keyword", source="webhook")
    provider = _Lines()
    run, _, _ = scrape(db, tmp_path=tmp_path, provider=provider)
    phones = {c.phone for c in fixture_contacts(db)}
    assert phones and not any(p.endswith(("3", "5")) for p in phones)
    assert run.screened_out > 0
    assert run.screened_out + run.contacts_created + run.opted_out == DISTINCT
    # Blocked numbers are filtered before the lookup, so nobody pays for them.
    assert BLOCKED not in provider.calls


def test_screening_off_lands_bidders_as_a_csv_does(db, tmp_path):
    assert bidder_scrape.screening_enabled() is False
    run, _, _ = scrape(db, tmp_path=tmp_path, provider=_Lines())
    assert run.screened_out == 0 and run.contacts_created == DISTINCT
