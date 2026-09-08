"""The application's clock, and what a client-facing timestamp means.

Session 5m: `campaigns.scheduled_at` is the wall clock an operator typed into a
`datetime-local` input, with no zone on it. `due_campaign_ids()` compared it
against `datetime.now()` and the droplet's clock is UTC, so a campaign set for
**6:00 PM Eastern went out at 2:00 PM Eastern** — four hours early in EDT, five
in EST. Nothing in the codebase named a timezone at all.

Every test here passes the moment it is evaluating as an **aware UTC instant**.
That is deliberate: the machine running the suite is in the client's zone, so a
test that used the local clock would pass with the zone handling deleted. Naming
the instant makes these tests about `America/New_York` rather than about this
laptop — and it is what makes the September and January cases different.
"""

import contextlib
import os
import subprocess
import shutil
import json
import pathlib
import re
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pytest

from app.core import clock
from app.core.config import APP_ZONE_NAME, apply_process_timezone, settings
from app.core.database import SessionLocal
from app.models.campaign import Campaign
from app.services import campaign_dispatch

ROOT = pathlib.Path(__file__).resolve().parent.parent
EASTERN = ZoneInfo("America/New_York")


def _load_migration(name: str):
    """`alembic/versions/` is not a package, so load the file by path.

    Driving the migration's own functions rather than a copy of them: a test
    that reimplements the conversion checks neither the conversion nor the
    reasoning behind it.
    """
    import importlib.util

    path = ROOT / "alembic" / "versions" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

# Six in the evening, the hour this client's auctions run and the hour on the
# campaign that exposed the defect: "09/09, 6:00 PM Private Record Collection".
SIX_PM = "18:00:00"


def utc(text: str) -> datetime:
    """An aware UTC instant, so a test says nothing about the box it runs on."""
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


@contextlib.contextmanager
def box_in(zone: str):
    """Run the block on a machine whose own clock is somewhere else.

    Naming an aware instant makes a test independent of the box's *reading* of
    the clock. It does not make it independent of the box's **zone**, because
    `apply_process_timezone()` runs at import of `app.core.config` and puts the
    whole suite in the client's zone whatever machine it is on — so a
    conversion that used the process zone instead of `APP_TIMEZONE` passes on
    this laptop, which sits in America/New_York. The 5m review found exactly
    that: pointing `wall_clock()`'s aware branch at `astimezone()` — the box —
    survived the whole suite. This is what closes it.
    """
    before = os.environ.get("TZ")
    os.environ["TZ"] = zone
    time.tzset()
    try:
        yield
    finally:
        if before is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = before
        apply_process_timezone()


# Three machines: the client's own, the droplet's, and one on the other side of
# the world. Every zone assertion runs on all three, because the interesting
# mutations are invisible on the first.
BOXES = ["America/New_York", "UTC", "Australia/Sydney"]


@pytest.fixture
def scheduled():
    """A draft scheduled for a wall-clock time, cleaned up afterwards."""
    made = []

    def make(when: str, status: str = "draft") -> int:
        db = SessionLocal()
        try:
            campaign = Campaign(name=f"Zone test {when} {len(made)}",
                                message_template="The sale is tonight.",
                                audience="all", status=status, scheduled_at=when)
            db.add(campaign)
            db.commit()
            made.append(campaign.id)
            return campaign.id
        finally:
            db.close()

    yield make
    db = SessionLocal()
    try:
        db.query(Campaign).filter(Campaign.id.in_(made)).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


# ─── The rule: 6:00 PM Eastern means 6:00 PM Eastern ────────────────────────

@pytest.mark.parametrize("box", BOXES)
def test_six_pm_eastern_dispatches_at_six_pm_eastern_in_edt(scheduled, box):
    """September. Eastern is UTC−4, so six in the evening is 22:00 UTC."""
    campaign_id = scheduled(f"2026-09-09T{SIX_PM}")
    db = SessionLocal()
    try:
      with box_in(box):
        one_minute_early = utc("2026-09-09T21:59:00")
        assert campaign_id not in campaign_dispatch.due_campaign_ids(db, one_minute_early)
        # This is the assertion the defect failed: at 18:00 UTC — 2:00 PM in Fort
        # Lauderdale, four hours before the auction — the campaign went out.
        four_hours_early = utc("2026-09-09T18:00:00")
        assert campaign_id not in campaign_dispatch.due_campaign_ids(db, four_hours_early)

        on_time = utc("2026-09-09T22:00:00")
        assert campaign_id in campaign_dispatch.due_campaign_ids(db, on_time)
    finally:
        db.close()


@pytest.mark.parametrize("box", BOXES)
def test_six_pm_eastern_dispatches_at_six_pm_eastern_in_est(scheduled, box):
    """January. Eastern is UTC−5, so six in the evening is 23:00 UTC.

    The pair with the test above is the whole of "zoneinfo, not an integer". A
    fixed −4 is right in September and an hour early every day from November to
    March — and one of the two changeovers is a Sunday morning.
    """
    campaign_id = scheduled(f"2027-01-15T{SIX_PM}")
    db = SessionLocal()
    try:
      with box_in(box):
        # 22:59 UTC is 5:59 PM Eastern in January. A −4 constant reads it as
        # 6:59 PM and sends an hour early; this is where that mutation dies.
        one_minute_early = utc("2027-01-15T22:59:00")
        assert campaign_id not in campaign_dispatch.due_campaign_ids(db, one_minute_early)

        on_time = utc("2027-01-15T23:00:00")
        assert campaign_id in campaign_dispatch.due_campaign_ids(db, on_time)
    finally:
        db.close()


@pytest.mark.parametrize("box", BOXES)
def test_the_same_wall_clock_is_two_instants_in_summer_and_winter(box):
    """Stated directly, in case the two tests above ever agree for a bad reason."""
    with box_in(box):
        summer = clock.wall_clock(utc("2026-07-04T16:00:00"))
        winter = clock.wall_clock(utc("2026-01-04T16:00:00"))
    assert summer.hour == 12, "EDT is UTC−4"
    assert winter.hour == 11, "EST is UTC−5"


@pytest.mark.parametrize("box", BOXES)
def test_the_scheduler_asks_no_one_what_time_it_is(scheduled, box):
    """The path production takes: `due_campaign_ids(db)` with no `now` at all.

    Every other test here hands in an aware instant, which is how they say
    something about `America/New_York` rather than about this laptop — and it
    takes `wall_clock()`'s *other* branch. `run_due_campaigns()` calls
    `due_campaign_ids(db)` with `now=None`, which goes to `clock.now()`, and the
    5m review showed that reinstating the production defect there — `now()`
    returning UTC — left both criterion-5 tests green. Two tests naming the
    session's headline requirement, proving a branch production never executes.

    So this one uses the real clock and a relative schedule: two minutes from
    now in the client's zone must not be due, and two minutes ago must be. On a
    box four (or fourteen) hours out, a `now` taken from the box calls the
    future campaign due — which is the defect, in its own units.
    """
    with box_in(box):
        eastern_now = datetime.now(EASTERN).replace(tzinfo=None, microsecond=0)
        soon = scheduled((eastern_now + timedelta(minutes=2)).isoformat())
        just_gone = scheduled((eastern_now - timedelta(minutes=2)).isoformat())

        db = SessionLocal()
        try:
            due = campaign_dispatch.due_campaign_ids(db)
        finally:
            db.close()

    assert just_gone in due, "a campaign whose time has passed is due"
    assert soon not in due, (
        "a campaign two minutes away in the client's zone is not due, whatever "
        f"the box thinks the time is (box={box})")


# ─── The two Sundays a year ─────────────────────────────────────────────────

def test_the_repeated_hour_dispatches_once(scheduled):
    """1 November 2026: 1:30 AM Eastern happens twice, an hour apart.

    Answered rather than avoided. The campaign becomes due at the first 1:30 AM
    (EDT, 05:30 UTC) and is dispatched; by the second (EST, 06:30 UTC) it is no
    longer a draft, and `due_campaign_ids()` filters on status. The draft filter
    is what makes the repeated hour safe, which is why 5m was told not to weaken
    it while changing the comparison.
    """
    campaign_id = scheduled("2026-11-01T01:30:00")
    db = SessionLocal()
    try:
        assert campaign_id in campaign_dispatch.due_campaign_ids(db, utc("2026-11-01T05:30:00"))

        # The dispatch happened; the campaign is no longer a draft.
        db.query(Campaign).filter(Campaign.id == campaign_id).update({"status": "completed"})
        db.commit()

        second_time_round = campaign_dispatch.due_campaign_ids(db, utc("2026-11-01T06:30:00"))
        assert campaign_id not in second_time_round, (
            "the wall clock reads 1:30 AM again an hour later; only the draft "
            "filter stops the same campaign going out twice")
    finally:
        db.close()


def test_the_hour_that_does_not_exist_dispatches_late_not_early(scheduled):
    """8 March 2026: the clock jumps 1:59:59 AM to 3:00 AM, so 2:30 never comes.

    A campaign scheduled for 2:30 AM becomes due at 3:00 AM Eastern — half an
    hour late in real time, at the first instant the wall clock has passed it.
    Late is the safe direction: early is the defect this session exists for.
    """
    campaign_id = scheduled("2026-03-08T02:30:00")
    db = SessionLocal()
    try:
        # 06:59 UTC is 1:59 AM EST — the last minute before the jump.
        assert campaign_id not in campaign_dispatch.due_campaign_ids(
            db, utc("2026-03-08T06:59:00"))
        # 07:00 UTC is 3:00 AM EDT, the first instant after it.
        assert campaign_id in campaign_dispatch.due_campaign_ids(
            db, utc("2026-03-08T07:00:00"))
    finally:
        db.close()


def test_a_campaign_that_has_run_is_never_due_again(scheduled):
    """The double-send guard, asserted for every status that is not a draft."""
    db = SessionLocal()
    try:
        long_past = utc("2026-09-09T22:00:00")
        for status in ("running", "completed", "aborted", "failed"):
            campaign_id = scheduled(f"2026-09-09T{SIX_PM}", status=status)
            assert campaign_id not in campaign_dispatch.due_campaign_ids(db, long_past), status
        still_a_draft = scheduled(f"2026-09-09T{SIX_PM}")
        assert still_a_draft in campaign_dispatch.due_campaign_ids(db, long_past)
    finally:
        db.close()


# ─── One clock for the whole process ────────────────────────────────────────

def test_the_process_timezone_is_the_clients_even_on_a_utc_box():
    """The sixty ambient `datetime.now()` calls, made right in one line.

    `sent_at`, `created_at` and `last_messaged_at` are naive local strings and
    are out of 5m's scope by name. What makes them mean the client's wall clock
    on a droplet whose own clock is UTC is that the process runs in the client's
    zone. Asserted by putting the process in UTC and applying the setting.
    """
    before = os.environ.get("TZ")
    try:
        os.environ["TZ"] = "UTC"
        time.tzset()
        assert datetime.now().hour == datetime.now(timezone.utc).hour, "the box is UTC now"

        apply_process_timezone()
        eastern_now = datetime.now(EASTERN).replace(tzinfo=None)
        assert abs((datetime.now() - eastern_now).total_seconds()) < 5
    finally:
        if before is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = before
        apply_process_timezone()


def test_the_scheduler_is_right_even_if_the_process_zone_was_never_applied(scheduled):
    """`clock.now()` asks zoneinfo, so it does not lean on `tzset()` having run.

    Two mechanisms with two jobs, and neither is redundant: the process zone
    makes sixty ambient calls right without sixty edits, and this makes the one
    comparison that decides when a real person gets a text right regardless.
    """
    before = os.environ.get("TZ")
    try:
        os.environ["TZ"] = "Australia/Sydney"
        time.tzset()
        naive_local = datetime.now()
        eastern = clock.now()
        assert abs((naive_local - eastern).total_seconds()) > 3600, "the box is elsewhere"
        assert abs((datetime.now(EASTERN).replace(tzinfo=None) - eastern)
                   .total_seconds()) < 5
    finally:
        if before is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = before
        apply_process_timezone()


def test_an_unusable_zone_falls_back_loudly_rather_than_refusing_to_boot(caplog):
    """A guard keyed on configuration must not take the product down.

    5f's lesson, one setting along: there is no reading of
    `APP_TIMEZONE=Amerika/New_York` under which refusing to start is right, and
    an outage nobody can diagnose is worse than the pre-existing weakness.
    """
    from app.core.config import _resolve_zone, DEFAULT_TIMEZONE

    with caplog.at_level("ERROR"):
        zone = _resolve_zone("Amerika/New_York")
    assert str(zone) == DEFAULT_TIMEZONE
    assert any("APP_TIMEZONE" in record.message for record in caplog.records)


def test_the_default_and_the_fallback_are_one_constant(monkeypatch):
    """The binding, not the literal.

    `settings.APP_TIMEZONE` defaults to `DEFAULT_TIMEZONE` and `_resolve_zone()`
    falls back to it; pinning the string would go red the day the client moves
    and stay green over the thing the pairing exists to prevent — a second
    literal drifting away from the first. 5i's lesson, one constant along.
    """
    from app.core import config

    assert settings.APP_TIMEZONE == config.DEFAULT_TIMEZONE
    assert APP_ZONE_NAME == config.DEFAULT_TIMEZONE
    # Change what the constant means and the fallback has to follow it.
    monkeypatch.setattr(config, "DEFAULT_TIMEZONE", "America/Chicago")
    assert str(config._resolve_zone("Amerika/Nowhere")) == "America/Chicago"


def test_a_system_with_no_zone_database_refuses_to_start_rather_than_guess(monkeypatch):
    """The failure the fallback cannot absorb, said out loud instead of raised.

    A bad *setting* falls back to the default. A system with no tz database at
    all is a different condition: the fallback is the same lookup that just
    failed, so "fall back and continue" would raise out of `app.core.config` —
    which every entry point imports — a second after the ERROR line said it had
    recovered. That is 5f's shape, a guard failing closed while announcing that
    it failed open. This fails on purpose and names the fix.
    """
    from app.core import config

    def no_zones(name):
        raise ZoneInfoNotFoundError(f"No time zone found with key {name}")

    monkeypatch.setattr(config, "ZoneInfo", no_zones)
    with pytest.raises(RuntimeError) as raised:
        config._resolve_zone("America/New_York")
    assert "tzdata" in str(raised.value)


def test_the_dashboards_upcoming_list_keeps_the_same_clock(scheduled):
    """A second reader of the same rule, and a guard on one path is a guard on one.

    `dashboard_service` answers "what is scheduled next", and it compared
    `scheduled_at` against the box's clock exactly as the scheduler did. On a
    droplet four hours ahead, tonight's 6:00 PM auction dropped off the Today
    screen at 2:00 PM — the same error, on the screen the client opens first.
    """
    from app.services import dashboard_service

    before = os.environ.get("TZ")
    try:
        # A box a long way ahead of the client. Anything comparing against its
        # clock believes the evening is already over.
        os.environ["TZ"] = "Australia/Sydney"
        time.tzset()
        in_two_hours = (clock.now() + timedelta(hours=2)).replace(microsecond=0)
        campaign_id = scheduled(in_two_hours.isoformat())
        # A decoy: `next_up()` falls back to the newest unscheduled draft when it
        # finds nothing upcoming, and without this the fallback would return the
        # same campaign and the test would pass with the clock reverted.
        decoy = scheduled(None)
        assert decoy != campaign_id

        db = SessionLocal()
        try:
            hero = dashboard_service.next_up(db, [])
        finally:
            db.close()
        assert hero and hero["id"] == campaign_id, (
            "a campaign two hours away in the client's zone is what the Today "
            f"screen leads with, whatever the box thinks the time is: {hero}")
    finally:
        if before is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = before
        apply_process_timezone()


def test_the_scheduler_itself_is_told_the_zone():
    """APScheduler printed every job time as UTC, which is how this was noticed."""
    from app.main import scheduler

    assert str(scheduler.timezone) == APP_ZONE_NAME


# ─── One formatter, and it is not the viewer's ──────────────────────────────

def _app_time_script() -> str:
    """base.html's clock block, with the zone the app actually resolved."""
    source = (ROOT / "app" / "templates" / "base.html").read_text()
    block = re.search(r"/\* app-time:start \*/(.*?)/\* app-time:end \*/", source, re.S)
    assert block, "base.html no longer carries the app-time markers"
    body = block.group(1)
    assert "{{ app_timezone }}" in body, "the zone must come from the app, not a literal"
    return body.replace("{{ app_timezone }}", APP_ZONE_NAME)


def test_the_template_global_is_the_zone_the_application_resolved():
    """The substitution above is honest: this is what a real render receives."""
    from app.routers.pages import templates

    assert templates.env.globals["app_timezone"] == APP_ZONE_NAME == clock.ZONE_NAME


@pytest.mark.parametrize("viewer_zone", ["UTC", "Australia/Sydney", "America/Los_Angeles"])
def test_a_stored_time_renders_in_the_clients_zone_wherever_it_is_read(viewer_zone):
    """Run the real block in node, in a browser that is somewhere else.

    This is the half a Python test cannot reach. `new Date("2026-09-09T18:00")`
    reads those digits in the *viewer's* zone, which is what printed a 6:00 PM
    send as "10:00 PM" — and `new Date("2026-10-01")` is parsed as midnight UTC,
    which printed /usage's reset date as the day before. Both are invisible on a
    laptop that happens to sit in Fort Lauderdale, so the browser is put
    somewhere else on purpose.
    """
    if shutil.which("node") is None:                        # pragma: no cover
        pytest.fail("node is required: these formatters are JavaScript, and npm "
                    "is already a build dependency (npm run build:css).")

    probe = _app_time_script() + """
console.log(JSON.stringify({
    evening: fmtDate('2026-09-09T18:00:00'),
    winter:  fmtDate('2027-01-15T18:00:00'),
    dateOnly: fmtDate('2026-10-01'),
    day:     fmtDay('2026-09-09T18:00:00'),
    clock:   fmtClock('2026-09-09T18:00:00'),
    junk:    fmtDate('not a time'),
    empty:   fmtDate(null),
}));
"""
    result = subprocess.run(["node", "-e", probe], capture_output=True, text=True,
                            env={**os.environ, "TZ": viewer_zone}, timeout=60)
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)

    assert out["evening"] == "Sep 9, 2026, 6:00 PM"
    assert out["winter"] == "Jan 15, 2027, 6:00 PM"
    assert out["dateOnly"] == "Oct 1, 2026", "a date-only value must not shift a day"
    assert out["day"] == "9 Sep"
    assert out["clock"] == "6:00pm on 9 Sep"
    # Unreadable input falls through unchanged, as it did before.
    assert out["junk"] == "not a time"
    assert out["empty"] == "—"


OLD_FORMATTER = """
function fmtDate(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    return isNaN(d) ? iso : d.toLocaleString('en-US',
        { month: 'short', day: 'numeric', year: 'numeric',
          hour: 'numeric', minute: '2-digit' });
}
"""


def test_the_render_check_goes_red_on_the_formatter_it_exists_to_reject():
    """The rejected formatter, kept executable rather than described.

    And it is worth being exact about what was wrong with it, because most of it
    was *accidentally* right: `new Date(naive)` reads the digits in the viewer's
    zone and `toLocaleString` prints them back in the viewer's zone, so a
    datetime string round-trips its digits in any zone. The four-hour error on
    screen was the **stored value** being UTC on the droplet, and the process
    zone is what fixes that.

    Two places the round trip fails, both live:

      * a **date-only** value is parsed as UTC midnight, so west of UTC it
        renders as the day before — `/usage`'s reset date, on the client's own
        screen, every cycle;
      * a wall clock inside the viewer's own DST gap does not exist, so `Date`
        moves it: 2:30 AM on 8 March renders as 3:30 AM.

    Neither can happen to a formatter that never builds a `Date` from a stored
    value, which is the argument for the one that replaced it.
    """
    if shutil.which("node") is None:                        # pragma: no cover
        pytest.fail("node is required")

    probe = OLD_FORMATTER + """
console.log(JSON.stringify({ dateOnly: fmtDate('2026-10-01'),
                             gap: fmtDate('2026-03-08T02:30:00') }));
"""
    west = subprocess.run(["node", "-e", probe], capture_output=True, text=True,
                          env={**os.environ, "TZ": "America/Los_Angeles"}, timeout=60)
    assert west.returncode == 0, west.stderr
    rejected = json.loads(west.stdout)
    assert rejected["dateOnly"] != "Oct 1, 2026", (
        "the old formatter parsed a date-only value as UTC midnight; if it does "
        "not here, this check is not measuring what it claims to")

    eastern = subprocess.run(["node", "-e", probe], capture_output=True, text=True,
                             env={**os.environ, "TZ": "America/New_York"}, timeout=60)
    assert eastern.returncode == 0, eastern.stderr
    assert json.loads(eastern.stdout)["gap"] != "Mar 8, 2026, 2:30 AM"

    # And the formatter that shipped answers both correctly, in the same zones.
    kept = _app_time_script() + """
console.log(JSON.stringify({ dateOnly: fmtDate('2026-10-01'),
                             gap: fmtDate('2026-03-08T02:30:00') }));
"""
    for zone in ("America/Los_Angeles", "America/New_York"):
        run = subprocess.run(["node", "-e", kept], capture_output=True, text=True,
                             env={**os.environ, "TZ": zone}, timeout=60)
        assert run.returncode == 0, run.stderr
        now = json.loads(run.stdout)
        assert now["dateOnly"] == "Oct 1, 2026", zone
        assert now["gap"] == "Mar 8, 2026, 2:30 AM", zone


# ─── The stored column, and what the migration did to it ────────────────────

def test_the_migration_leaves_a_wall_clock_alone_and_converts_an_instant():
    """Driven through the migration's own functions, not a re-implementation."""
    migration = _load_migration("b7d43f0c9a15_adjudicate_campaign_scheduled_at")
    classify, to_client_wall_clock = migration.classify, migration.to_client_wall_clock

    typed_by_the_operator = "2026-09-09T18:00:00"
    assert classify(typed_by_the_operator) == "naive"
    assert to_client_wall_clock(typed_by_the_operator) is None, (
        "a value that is already the client's wall clock must not be shifted "
        "again; that is what would have sent the 6:00 PM campaign at 10:00 PM")

    # Nothing in the application writes one of these; an API caller could.
    assert classify("2026-09-09T22:00:00+00:00") == "offset"
    assert to_client_wall_clock("2026-09-09T22:00:00+00:00") == typed_by_the_operator
    assert to_client_wall_clock("2026-09-09T22:00:00Z") == typed_by_the_operator
    # January: the same conversion, an hour further along.
    assert to_client_wall_clock("2027-01-15T23:00:00+00:00") == "2027-01-15T18:00:00"

    for unreadable in ("2026-09-09", "tonight", "", None, "2026-09-09T18"):
        assert classify(unreadable) == "unreadable", unreadable
        assert to_client_wall_clock(unreadable) is None

    # Idempotent: a converted value classifies as naive and is left alone.
    once = to_client_wall_clock("2026-09-09T22:00:00+00:00")
    assert to_client_wall_clock(once) is None


# ─── Nothing else may render a time ─────────────────────────────────────────

TEMPLATES = ROOT / "app" / "templates"

# `new Date(` with an argument. `new Date()` — the browser's own clock, which is
# what "what is today" legitimately needs — is not this. The sweep is over the
# whole template directory rather than a list, so a screen added tomorrow is
# covered the day it exists.
BUILDS_A_DATE = re.compile(r"new\s+Date\s*\(\s*[^)\s]")


def _code_lines(text: str):
    """Lines outside a Jinja comment, and not a JS line comment.

    The sixth measurement script in this project was wrong before the code was,
    and the shape it keeps taking is prose describing a rule counted as a
    violation of it — `base.html`'s own comment explains what `new Date(iso)`
    did wrong, and a sweep that flagged it would be the seventh.
    """
    inside_jinja_comment = False
    for number, line in enumerate(text.splitlines(), 1):
        if "{#" in line:
            inside_jinja_comment = "#}" not in line
            continue
        if inside_jinja_comment:
            inside_jinja_comment = "#}" not in line
            continue
        if line.lstrip().startswith(("//", "*", "/*")):
            continue
        yield number, line


def test_no_template_builds_a_date_from_a_stored_value():
    """One formatter, and the sweep is what keeps it one.

    A second `new Date(iso)` anywhere is a second answer to "what time is this",
    and the two disagree exactly where it is hardest to notice: a date-only value
    west of UTC, and a wall clock inside the viewer's own DST gap.
    """
    offenders = []
    for path in sorted(TEMPLATES.glob("*.html")):
        for number, line in _code_lines(path.read_text()):
            if BUILDS_A_DATE.search(line):
                offenders.append(f"{path.name}:{number}: {line.strip()}")
    assert not offenders, (
        "render times through fmtDate/fmtDay/fmtClock in base.html, which read "
        "the client's zone rather than the viewer's:\n  " + "\n  ".join(offenders))


def test_the_sweep_fires_and_does_not_flag_the_comment_explaining_it():
    """Against cases whose answers are known, before it is quoted as evidence."""
    assert BUILDS_A_DATE.search("const d = new Date(iso);")
    assert BUILDS_A_DATE.search("return new Date( row.sent_at ).getTime();")
    # The browser's own clock, which "what is today" needs, is not a violation.
    assert not BUILDS_A_DATE.search("const today = new Date();")
    # And a comment about the defect is not the defect.
    lines = list(_code_lines("// `new Date(iso)` would read those digits\nx = 1;"))
    assert lines == [(2, "x = 1;")]
    jinja = "{# a note about new Date(iso) #}\nconst y = 2;"
    assert list(_code_lines(jinja)) == [(2, "const y = 2;")]


@pytest.mark.parametrize("template,expected", [
    ("campaigns.html", "_composer-script.html"),   # the rail lives in the partial
    ("history.html", "history.html"),
    ("campaign-report.html", "campaign-report.html"),
])
def test_the_three_surfaces_named_in_the_spec_render_through_the_shared_formatter(
        template, expected):
    """The rail, history and the per-campaign report — by name, not by sweep."""
    source = (TEMPLATES / expected).read_text()
    assert re.search(r"fmtDate\(|fmtDay\(|fmtClock\(", source), (
        f"{expected} renders a timestamp without the shared formatter")


# ─── The column has one writer now, not just one meaning ────────────────────

def test_an_offset_bearing_send_time_is_stored_as_the_clients_wall_clock():
    """The class the migration adjudicated, closed going forward.

    `campaigns.scheduled_at` is a free `str` on the API. The migration converts
    the offset-bearing rows already in the column and its docstring says nothing
    in this application writes one — which was true and was not a guarantee. An
    offset-bearing value does not compare `<=` a naive cutoff until the wall
    clock passes the offset's hour, so `18:00:00+00:00` would go out at 6:00 PM
    Eastern: four hours **late**, the mirror of the defect this session fixed.
    """
    assert clock.normalise_stored("2026-09-09T22:00:00+00:00") == "2026-09-09T18:00:00"
    assert clock.normalise_stored("2026-09-09T22:00:00Z") == "2026-09-09T18:00:00"
    assert clock.normalise_stored("2026-09-09T18:00:00") == "2026-09-09T18:00:00"
    assert clock.normalise_stored(None) is None
    assert clock.normalise_stored("  ") is None
    # January, an hour further along, through zoneinfo rather than an offset.
    assert clock.normalise_stored("2027-01-15T23:00:00+00:00") == "2027-01-15T18:00:00"

    # One spelling as well as one zone: the comparison is lexicographic, and
    # `fromisoformat` accepts shapes that would sort against a canonical value
    # in ways nobody checks.
    assert clock.normalise_stored("2026-09-09T18") == "2026-09-09T18:00:00"
    assert clock.normalise_stored("2026-09-09 18:00") == "2026-09-09T18:00:00"

    for unreadable in ("tonight", "6pm", "09/09/2026 6pm"):
        with pytest.raises(ValueError):
            clock.normalise_stored(unreadable)

    # And through the endpoint, which is where it has to hold: the API takes a
    # bare string and the browser is not the only thing that can post one.
    from fastapi.testclient import TestClient
    from app.main import app
    from app.models.sms_message import SMSMessage
    from app.routers import campaigns as campaigns_router

    from app.models.contact import Contact

    db = SessionLocal()
    try:
        # An audience to resolve. Two contacts of this module's own, removed
        # again below: the suite shares one database and a module that leaves
        # rows behind makes its neighbours prove something else.
        for i in range(2):
            db.add(Contact(phone=f"+1954999{i:04d}", full_name=f"Zone Bidder {i}",
                           is_active=1, source="csv"))
        db.commit()
    finally:
        db.close()

    campaigns_router.limiter.reset()
    client = TestClient(app)
    client.post("/login", data={"username": "admin", "password": "devpassword123"},
                follow_redirects=False)
    body = {"name": "Offset send time", "message_template": "The sale is tonight.",
            "audience": "all", "cross_category_override": True}

    created = client.post("/api/campaigns",
                          json={**body, "scheduled_at": "2026-09-09T22:00:00+00:00"})
    assert created.status_code == 200, created.text
    campaign = created.json()["campaign"]
    assert campaign["scheduled_at"] == "2026-09-09T18:00:00"

    refused = client.post("/api/campaigns",
                          json={**body, "name": "Unreadable", "scheduled_at": "tonight"})
    assert refused.status_code == 400, refused.text
    assert "schedule" in refused.json()["detail"]

    db = SessionLocal()
    try:
        db.query(SMSMessage).filter(SMSMessage.campaign_id == campaign["id"]).delete(
            synchronize_session=False)
        db.query(Campaign).filter(Campaign.id == campaign["id"]).delete(
            synchronize_session=False)
        phones = [f"+1954999{i:04d}" for i in range(2)]
        ids = [i for (i,) in db.query(Contact.id).filter(Contact.phone.in_(phones))]
        if ids:
            db.query(SMSMessage).filter(SMSMessage.contact_id.in_(ids)).delete(
                synchronize_session=False)
        db.query(Contact).filter(Contact.phone.in_(phones)).delete(
            synchronize_session=False)
        db.commit()
    finally:
        db.close()
        campaigns_router.limiter.reset()
