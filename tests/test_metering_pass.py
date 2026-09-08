"""Session B1b: every billable segment reaches the meter exactly once.

The property, from `sessions/session-B1b.md`: **every billable segment reaches
the meter exactly once, and the figure metered is the figure `compute_usage()`
would report for the same window.** `decisions/011` is the reasoning.

Nothing here reaches Stripe. `tests/_stripe_fixtures.replaced_api()` installs a
fake for every block, `conftest.py` blanks the four Stripe settings for the
whole suite, and `agent/accept-B1b.sh` runs this module with `socket.connect`
raising so that "no network" is proved rather than promised.

Every clock is pinned. The pass takes `now`, so the settle window and the
35-day horizon are tested against fixed 2021 dates — which also keeps these rows
out of the current cycle the smoke test and the dashboard tests assert on. Two
things are asserted on **call counts**, never on the fake's own dedupe: that a
second pass reports nothing, and that a backfill of an old period reports
nothing. Stripe's identifier dedupes for 24 hours and no longer, and a test that
leaned on the fake's record would have passed on exactly the double bill this
session removes.
"""

import ast
import asyncio
import logging
import os
import pathlib
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.database import SessionLocal
from app.main import app
from app.models.app_setting import set_setting
from app.models.campaign import Campaign
from app.models.sms_message import SMSMessage, BILLABLE_STATUSES
from app.routers.webhooks.common import record_delivery_status
from app.services import (billing_service, campaign_dispatch, campaign_topup,
                          stripe_billing, stripe_meter, stripe_reconcile)
from tests import _stripe_fixtures as fx

PASSWORD = os.environ["ADMIN_PASSWORD"]
PHONE = "+15555550380"          # distinct from B1's 555-030x rows
SUBSCRIBED_ON = date(2021, 5, 1)
SENT = "2021-05-02T09:00:00"


@pytest.fixture
def db():
    session = SessionLocal()
    fx.clear_billing_rows(session)
    try:
        yield session
    finally:
        fx.clear_billing_rows(session)
        session.close()


@pytest.fixture
def subscribed(db):
    """A stored customer and a stored subscription start — both, because the
    pass refuses to run with only the first (it would have no lower bound)."""
    set_setting(db, stripe_billing.CUSTOMER_ID_KEY, fx.CUSTOMER)
    set_setting(db, stripe_billing.CYCLE_ANCHOR_AT_KEY, SUBSCRIBED_ON.isoformat())
    return fx.CUSTOMER


def make_campaign(db, *, name, rows, created_at="2021-05-01T09:00:00"):
    """A campaign with real message rows: every figure under test is a query.

    `rows` are dicts of SMSMessage fields; `status`, `segments` and `sent_at`
    default to a settled-looking `sent` row on 2 May 2021. `sent_count` is set
    so the delivery webhook's counter arithmetic has something to move.
    """
    campaign = Campaign(name=name, message_template="hi", audience="all",
                        status="completed", created_at=created_at,
                        sent_count=len(rows))
    db.add(campaign)
    db.commit()
    db.refresh(campaign)
    for fields in rows:
        db.add(SMSMessage(**{"campaign_id": campaign.id, "phone": PHONE,
                             "message": "hi", "status": "sent", "segments": 1,
                             "sent_at": SENT, **fields}))
    db.commit()
    return campaign.id


def drop_campaign(db, campaign_id):
    db.query(SMSMessage).filter(SMSMessage.campaign_id == campaign_id).delete(
        synchronize_session=False)
    db.query(Campaign).filter(Campaign.id == campaign_id).delete(
        synchronize_session=False)
    db.commit()


def rows_of(db, campaign_id):
    return db.query(SMSMessage).filter(SMSMessage.campaign_id == campaign_id).all()


def settled_after(sent_at: str) -> datetime:
    """A clock one hour past the settle window for a row sent at `sent_at`."""
    return (datetime.fromisoformat(sent_at)
            + timedelta(hours=settings.BILLING_SETTLE_HOURS + 1))


def run_pass(db, fake, now, **kwargs):
    with fx.stripe_configured(), fx.replaced_api(fake):
        return stripe_meter.meter_settled_rows(db, now=now, **kwargs)


def meter_calls(fake):
    return fake.named("create_meter_event")


# ─── Criterion 1: the over-bill is gone ─────────────────────────────────────


def test_the_meter_receives_what_the_webhook_left_billable_once(db, subscribed):
    """12,000 sent, the webhook writes 4,000 of them undelivered, the pass
    meters 8,000 — as one event, and never again.

    The flip goes through `record_delivery_status()`, the real webhook path,
    not a status edit: the defect was that B1 metered before that path ran.
    """
    campaign_id = make_campaign(db, name="B1b overbill", rows=[
        {"segments": 1000, "external_id": f"b1b-overbill-{n}"} for n in range(12)])
    fake = fx.FakeStripe(dedupe=False)
    try:
        for n in range(4):
            record_delivery_status(db, f"b1b-overbill-{n}", "undelivered",
                                   "Carrier did not deliver the message")
        db.expire_all()
        _, usage = billing_service.compute_usage(db, date(2021, 5, 2), date(2021, 5, 2))
        assert usage == 8000, "the webhook did not move the rows out of the billable set"

        verdict = run_pass(db, fake, settled_after(SENT))
        assert sum(fake.metered_values) == 8000, fake.metered_values
        assert len(meter_calls(fake)) == 1, "8,000 segments in one campaign on one day is one event"
        assert verdict["segments"] == 8000
        assert all(row.metered_at for row in rows_of(db, campaign_id)
                   if row.status in BILLABLE_STATUSES)
        assert not any(row.metered_at for row in rows_of(db, campaign_id)
                       if row.status == "undelivered")

        run_pass(db, fake, settled_after(SENT) + timedelta(hours=1))
        assert len(meter_calls(fake)) == 1, "a second pass re-reported the campaign"
    finally:
        drop_campaign(db, campaign_id)


# ─── Criterion 2: the top-up is billed ──────────────────────────────────────


def test_a_top_up_is_a_later_batch_and_the_campaign_total_matches_usage(db, subscribed):
    """The first pass meters the first send; a top-up four days later is new
    unmarked rows on the same campaign, and the next pass meters only those.
    The meter's total for the campaign is `compute_usage()`'s figure.
    """
    first_send = [{"segments": 3} for _ in range(5)]
    campaign_id = make_campaign(db, name="B1b top-up", rows=first_send)
    fake = fx.FakeStripe(dedupe=False)
    try:
        run_pass(db, fake, settled_after(SENT))
        assert fake.metered_values == [15]

        top_up_sent = "2021-05-06T12:00:00"
        for _ in range(2):
            db.add(SMSMessage(campaign_id=campaign_id, phone=PHONE, message="hi",
                              status="sent", segments=3, sent_at=top_up_sent,
                              top_up_at=top_up_sent))
        db.commit()

        run_pass(db, fake, settled_after(top_up_sent))
        assert fake.metered_values == [15, 6], "the top-up was not metered as its own batch"
        identifiers = [event["identifier"] for event in fake.meter_events]
        assert len(set(identifiers)) == 2, identifiers

        _, usage = billing_service.compute_usage(db, date(2021, 5, 1), date(2021, 5, 31))
        assert sum(fake.metered_values) == usage == 21
    finally:
        drop_campaign(db, campaign_id)


# ─── Criterion 3: a second pass reports nothing ─────────────────────────────


def test_a_second_pass_over_the_same_rows_makes_no_call_at_all(db, subscribed):
    """Asserted on the call count with a fake that bills every call. Stripe's
    identifier would have hidden a re-report for 24 hours and then billed it."""
    campaign_id = make_campaign(db, name="B1b idempotent", rows=[{"segments": 7}] * 3)
    fake = fx.FakeStripe(dedupe=False)
    try:
        first = run_pass(db, fake, settled_after(SENT))
        assert first["segments"] == 21
        for later in (1, 24, 24 * 30):
            again = run_pass(db, fake, settled_after(SENT) + timedelta(hours=later))
            assert again["reported"] == [], again
        assert len(meter_calls(fake)) == 1, "the mark did not stop a re-report"
        assert sum(fake.metered_values) == 21
        stamps = {row.metered_at for row in rows_of(db, campaign_id)}
        assert len(stamps) == 1 and None not in stamps, stamps
    finally:
        drop_campaign(db, campaign_id)


def test_a_marked_row_is_never_reported_whatever_its_status(db, subscribed):
    """The ledger is by row id. Marked, then flipped by a late receipt, then
    another pass: no call. The residue is visible in the breakdown instead."""
    campaign_id = make_campaign(db, name="B1b marked flip", rows=[
        {"segments": 5, "external_id": "b1b-late-receipt"}])
    fake = fx.FakeStripe(dedupe=False)
    try:
        run_pass(db, fake, settled_after(SENT))
        record_delivery_status(db, "b1b-late-receipt", "undelivered", "late")
        db.expire_all()
        run_pass(db, fake, settled_after(SENT) + timedelta(days=1))
        assert len(meter_calls(fake)) == 1
        breakdown = stripe_reconcile.unmetered_breakdown(
            db, date(2021, 5, 2), date(2021, 5, 2), now=settled_after(SENT))
        assert breakdown["metered_no_longer_billable"] == {"rows": 1, "segments": 5}
    finally:
        drop_campaign(db, campaign_id)


# ─── Criterion 4: a backfill of an old period double-bills nothing ──────────


def test_a_backfill_of_a_thirty_day_old_period_double_bills_nothing(db, subscribed):
    """Metered thirty days ago; replayed today by the backfill with a fake
    that bills every call, and with `since` naming the period explicitly.
    Nothing is offered — proved against the mark, not against Stripe."""
    campaign_id = make_campaign(db, name="B1b old backfill", rows=[{"segments": 400}] * 2)
    fake = fx.FakeStripe(dedupe=False)
    thirty_days_on = datetime.fromisoformat(SENT) + timedelta(days=30)
    try:
        run_pass(db, fake, settled_after(SENT))
        assert fake.metered_values == [800]

        with fx.stripe_configured(), fx.replaced_api(fake):
            plan = stripe_meter.backfill_unreported(
                db, dry_run=True, since=date(2021, 5, 1), now=thirty_days_on)
            assert plan["dry_run"] is True and plan["reported"] == [], plan
            done = stripe_meter.backfill_unreported(
                db, dry_run=False, since=date(2021, 5, 1), now=thirty_days_on)
        assert done["reported"] == [] and done["refused"] == [], done
        assert len(meter_calls(fake)) == 1, "the backfill re-offered marked rows"
        assert sum(fake.metered_values) == 800
    finally:
        drop_campaign(db, campaign_id)


def test_the_backfill_meters_what_was_missed_and_only_that(db, subscribed):
    """The positive control, or the test above proves nothing: a backfill
    over a period with one metered and one missed campaign reports the missed
    one, once, dry run first."""
    metered = make_campaign(db, name="B1b backfill done", rows=[{"segments": 20}])
    missed = make_campaign(db, name="B1b backfill missed", rows=[{"segments": 30}])
    fake = fx.FakeStripe(dedupe=False)
    try:
        run_pass(db, fake, settled_after(SENT))
        assert fake.metered_values == [20, 30]
        db.query(SMSMessage).filter(SMSMessage.campaign_id == missed).update(
            {"metered_at": None}, synchronize_session=False)
        db.commit()

        with fx.stripe_configured(), fx.replaced_api(fake):
            plan = stripe_meter.backfill_unreported(
                db, since=date(2021, 5, 1), now=settled_after(SENT) + timedelta(days=20))
            assert [b["campaign_id"] for b in plan["reported"]] == [missed]
            assert len(meter_calls(fake)) == 2, "a dry run posted"
            done = stripe_meter.backfill_unreported(
                db, dry_run=False, since=date(2021, 5, 1),
                now=settled_after(SENT) + timedelta(days=20))
        assert [b["campaign_id"] for b in done["reported"]] == [missed]
        assert fake.metered_values == [20, 30, 30]
    finally:
        drop_campaign(db, metered)
        drop_campaign(db, missed)


# ─── Criterion 5: usage older than 35 days is refused, visibly ──────────────


def test_usage_older_than_thirty_five_days_is_refused_visibly(db, subscribed, caplog):
    """Refused, not dropped: no call, `metered_at` left NULL, an ERROR naming
    the remedy, the verdict carrying the rows, and the breakdown labelling them.
    And at 34 days the same rows are metered — the horizon is the SDK's 35."""
    campaign_id = make_campaign(db, name="B1b too old", rows=[{"segments": 250}] * 2)
    fake = fx.FakeStripe(dedupe=False)
    sent = datetime.fromisoformat(SENT)
    try:
        with caplog.at_level(logging.ERROR, logger="billing.stripe"):
            verdict = run_pass(db, fake, sent + timedelta(days=36))
        assert meter_calls(fake) == [], "usage older than the horizon reached Stripe"
        assert verdict["reported"] == []
        assert [b["campaign_id"] for b in verdict["refused"]] == [campaign_id]
        assert verdict["refused"][0]["segments"] == 500
        assert all(row.metered_at is None for row in rows_of(db, campaign_id))
        errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
        assert any("USAGE NOT METERED" in m and "bill_period" in m and "500" in m
                   for m in errors), errors

        breakdown = stripe_reconcile.unmetered_breakdown(
            db, date(2021, 5, 1), date(2021, 5, 31), now=sent + timedelta(days=36))
        assert breakdown["unmetered"] == {
            stripe_reconcile.TOO_OLD: {"rows": 2, "segments": 500}}

        # Positive control: a day inside the horizon, the same rows are metered.
        verdict = run_pass(db, fake, sent + timedelta(days=34))
        assert verdict["refused"] == [] and fake.metered_values == [500]
    finally:
        drop_campaign(db, campaign_id)


# ─── Criterion 6: the event carries the send timestamp ──────────────────────


def test_the_event_carries_the_send_time_and_lands_in_the_earlier_cycle(db, subscribed):
    """Sent at 23:00 on the last day of May; the pass runs on 2 June. The
    event's `timestamp` is the send's epoch, and its date is inside May's
    cycle — not June's, which is the cycle the pass ran in."""
    late_may = "2021-05-31T23:00:00"
    campaign_id = make_campaign(db, name="B1b boundary", rows=[
        {"segments": 9, "sent_at": late_may}])
    fake = fx.FakeStripe()
    try:
        now = datetime(2021, 6, 2, 1, 0)
        assert now > settled_after(late_may)
        run_pass(db, fake, now)
        (_, params), = meter_calls(fake)
        assert params["timestamp"] == int(datetime(2021, 5, 31, 23, 0).timestamp())

        landed = date.fromtimestamp(params["timestamp"])
        may_start, may_end, _, _ = billing_service.get_billing_cycle(date(2021, 5, 31), db=db)
        june_start, _, _, _ = billing_service.get_billing_cycle(now.date(), db=db)
        assert may_start <= landed <= may_end, (landed, may_start, may_end)
        assert landed < june_start
    finally:
        drop_campaign(db, campaign_id)


def test_a_campaign_that_straddles_midnight_is_split_the_way_usage_splits_it(db, subscribed):
    """One batch per calendar day, because `get_billing_cycle()` is a date and
    a single timestamp would drag both halves into one cycle."""
    campaign_id = make_campaign(db, name="B1b midnight", rows=[
        {"segments": 4, "sent_at": "2021-05-31T23:59:30"},
        {"segments": 6, "sent_at": "2021-06-01T00:00:30"}])
    fake = fx.FakeStripe()
    try:
        run_pass(db, fake, datetime(2021, 6, 3, 0, 0))
        by_day = {date.fromtimestamp(e["timestamp"]): int(e["payload"]["value"])
                  for e in fake.meter_events}
        assert by_day == {date(2021, 5, 31): 4, date(2021, 6, 1): 6}, by_day
    finally:
        drop_campaign(db, campaign_id)


# ─── Criterion 7: nothing meters from the send path ─────────────────────────


def test_a_real_send_meters_nothing_and_the_pass_meters_it_later(db, subscribed):
    """The Send button's path, end to end on the console provider, with a
    customer stored and Stripe configured: zero Stripe calls. Then the pass,
    a settle window later, meters exactly what was sent."""
    from app.services import campaign_builder
    from app.services.campaign_service import CampaignService
    from tests._campaign_flow_setup import (MESSAGE, NAME_PREFIX, default_csv,
                                            purge, take)

    set_setting(db, stripe_billing.CYCLE_ANCHOR_AT_KEY,
                (date.today() - timedelta(days=1)).isoformat())
    fake = fx.FakeStripe(dedupe=False)
    purge(db)
    try:
        campaign, _ = campaign_builder.create_campaign_from_upload(
            db, CampaignService(db).render, name=f"{NAME_PREFIX}B1b send",
            message_template=MESSAGE, content=default_csv(take(3)))
        with fx.stripe_configured(), fx.replaced_api(fake):
            asyncio.run(campaign_dispatch.send_campaign_background(campaign.id))
        db.expire_all()
        sent_rows = [r for r in rows_of(db, campaign.id) if r.status == "sent"]
        assert len(sent_rows) == 3, [r.status for r in rows_of(db, campaign.id)]
        assert fake.calls == [], "the send path reached Stripe"
        assert all(r.metered_at is None for r in sent_rows)

        run_pass(db, fake, datetime.now())
        assert not [b for b in fake.meter_events
                    if b["identifier"].startswith(f"u_c{campaign.id}_")], (
            "an unsettled row was metered")
        verdict = run_pass(db, fake, datetime.now()
                           + timedelta(hours=settings.BILLING_SETTLE_HOURS + 1))
        # This campaign's batch only: the pass ranges over the whole database
        # since yesterday, and another module's leftover row must not fail a
        # test about this send (nor pass it).
        ours = [b for b in verdict["reported"] if b["campaign_id"] == campaign.id]
        assert [b["segments"] for b in ours] == [sum(r.segments for r in sent_rows)]
        assert sum(r.segments for r in sent_rows) > 0
    finally:
        purge(db)


def test_neither_the_scheduler_nor_the_top_up_path_meters(db, subscribed, monkeypatch):
    """The other two entry points, with their work replaced by a no-op so the
    test is about the wiring around a send rather than the send loop."""
    from app.services.campaign_service import CampaignService

    async def _no_send(self, cid):
        return None

    async def _no_top_up(session, cid):
        return None

    monkeypatch.setattr(CampaignService, "send_campaign", _no_send)
    monkeypatch.setattr(campaign_topup, "top_up", _no_top_up)
    campaign = Campaign(name="B1b scheduled", message_template="hi", audience="all",
                        status="draft", created_at="2021-05-01T09:00:00",
                        scheduled_at="2021-05-01T09:00:00")
    db.add(campaign)
    db.commit()
    db.refresh(campaign)
    fake = fx.FakeStripe()
    try:
        with fx.stripe_configured(), fx.replaced_api(fake):
            dispatched = asyncio.run(campaign_dispatch.run_due_campaigns())
            asyncio.run(campaign_topup.top_up_background(campaign.id))
        assert campaign.id in dispatched
        assert fake.calls == [], fake.calls
    finally:
        drop_campaign(db, campaign.id)


def _app_sources():
    root = pathlib.Path(__file__).resolve().parent.parent / "app"
    return {p: p.read_text() for p in root.rglob("*.py")}


def test_a_segment_can_be_metered_from_exactly_one_place():
    """Review lens 1, as a sweep. `create_meter_event` is called from one site
    in `app/`, and the three modules that write `sent` rows import neither
    Stripe module — anywhere, including inside a function body, which is
    where B1's hook lived."""
    sources = _app_sources()
    sites = {p.name: s.count(".create_meter_event(") for p, s in sources.items()
             if ".create_meter_event(" in s and p.name != "stripe_billing.py"}
    assert sites == {"stripe_meter.py": 1}, sites

    for name in ("campaign_dispatch.py", "campaign_topup.py", "campaign_service.py"):
        path = next(p for p in sources if p.name == name)
        for node in ast.walk(ast.parse(sources[path])):
            imported = []
            if isinstance(node, ast.ImportFrom):
                imported = [f"{node.module}.{a.name}" for a in node.names]
            elif isinstance(node, ast.Import):
                imported = [a.name for a in node.names]
            assert not any("stripe" in item for item in imported), (
                f"{name} imports {imported}: the send path is metering again")


# ─── Criterion 8: /usage and the metered total agree ────────────────────────


@pytest.fixture(scope="module")
def client():
    from app.routers import pages as pages_router
    pages_router.limiter.reset()
    c = TestClient(app)
    login = c.post("/login", data={"username": "admin", "password": PASSWORD})
    assert login.status_code in (200, 302), login.status_code
    yield c
    pages_router.limiter.reset()


def test_usage_and_the_metered_total_agree_with_failures_and_a_top_up(db, subscribed, client):
    """June 2021, through the real `/api/usage/history` route: six rows sent,
    two fail by webhook, a top-up of three lands a week later, two passes.
    The page's figure, the reconciliation's, and the meter's are one number."""
    june = "2021-06-02T09:00:00"
    campaign_id = make_campaign(db, name="B1b agree", rows=[
        {"segments": 100, "sent_at": june, "external_id": f"b1b-agree-{n}"}
        for n in range(6)])
    fake = fx.FakeStripe(dedupe=False)
    try:
        for n in range(2):
            record_delivery_status(db, f"b1b-agree-{n}", "undelivered", "dropped")
        db.expire_all()
        run_pass(db, fake, settled_after(june))
        top_up = "2021-06-10T09:00:00"
        for _ in range(3):
            db.add(SMSMessage(campaign_id=campaign_id, phone=PHONE, message="hi",
                              status="delivered", segments=100, sent_at=top_up,
                              top_up_at=top_up))
        db.commit()
        run_pass(db, fake, settled_after(top_up))

        months = client.get("/api/usage/history?cycles=80").json()["months"]
        page = next(m for m in months if m["billing_start"] == "2021-06-01")
        metered_in_june = sum(int(e["payload"]["value"]) for e in fake.meter_events
                              if date.fromtimestamp(e["timestamp"]).month == 6)
        breakdown = stripe_reconcile.unmetered_breakdown(
            db, date(2021, 6, 1), date(2021, 6, 30), now=settled_after(top_up))
        assert page["total_segments"] == metered_in_june == 700
        assert breakdown["metered_segments"] == 700
        assert breakdown["unmetered_segments"] == 0
        assert breakdown["metered_no_longer_billable"]["rows"] == 0
    finally:
        drop_campaign(db, campaign_id)


# ─── A2: settled, defined ───────────────────────────────────────────────────


def test_a_sent_row_waits_for_the_window_and_a_delivered_one_does_not(db, subscribed):
    campaign_id = make_campaign(db, name="B1b settle", rows=[
        {"segments": 1, "status": "sent"}, {"segments": 2, "status": "delivered"}])
    fake = fx.FakeStripe(dedupe=False)
    try:
        soon = datetime.fromisoformat(SENT) + timedelta(hours=1)
        run_pass(db, fake, soon)
        assert fake.metered_values == [2], "a sent row was metered inside the settle window"
        run_pass(db, fake, settled_after(SENT))
        assert fake.metered_values == [2, 1]
    finally:
        drop_campaign(db, campaign_id)


def test_the_settle_window_is_the_configured_number_of_hours(db, subscribed, monkeypatch):
    campaign_id = make_campaign(db, name="B1b window", rows=[{"segments": 1}])
    fake = fx.FakeStripe()
    try:
        monkeypatch.setattr(settings, "BILLING_SETTLE_HOURS", 48)
        run_pass(db, fake, datetime.fromisoformat(SENT) + timedelta(hours=30))
        assert fake.calls == []
        run_pass(db, fake, datetime.fromisoformat(SENT) + timedelta(hours=49))
        assert fake.metered_values == [1]
    finally:
        drop_campaign(db, campaign_id)


def test_the_pass_follows_the_models_definition_of_billable(db, subscribed, monkeypatch):
    """The binding, not the contents. With only `sent` billable, a delivered
    row must drop out of the selection — a local copy would keep counting it."""
    campaign_id = make_campaign(db, name="B1b billable binding", rows=[
        {"segments": 3, "status": "sent"}, {"segments": 4, "status": "delivered"}])
    try:
        now = settled_after(SENT)
        assert {r.segments for r in stripe_meter.settled_unmetered(db, now, SUBSCRIBED_ON)
                if r.campaign_id == campaign_id} == {3, 4}
        monkeypatch.setattr("app.models.sms_message.BILLABLE_STATUSES", ("sent",))
        assert {r.segments for r in stripe_meter.settled_unmetered(db, now, SUBSCRIBED_ON)
                if r.campaign_id == campaign_id} == {3}, (
            "the pass is reading its own copy of BILLABLE_STATUSES")
    finally:
        drop_campaign(db, campaign_id)


def test_the_pass_follows_the_models_definition_of_settled(db, subscribed, monkeypatch):
    campaign_id = make_campaign(db, name="B1b settled binding", rows=[
        {"segments": 4, "status": "delivered"}])
    try:
        soon = datetime.fromisoformat(SENT) + timedelta(hours=1)
        assert [r.segments for r in stripe_meter.settled_unmetered(db, soon, SUBSCRIBED_ON)
                if r.campaign_id == campaign_id] == [4]
        monkeypatch.setattr("app.models.sms_message.SETTLED_STATUSES", ())
        assert [r for r in stripe_meter.settled_unmetered(db, soon, SUBSCRIBED_ON)
                if r.campaign_id == campaign_id] == [], (
            "the pass is reading its own copy of SETTLED_STATUSES")
    finally:
        drop_campaign(db, campaign_id)


def test_only_billable_statuses_reach_the_meter(db, subscribed):
    campaign_id = make_campaign(db, name="B1b statuses", rows=[
        {"segments": 3, "status": "sent"}, {"segments": 4, "status": "delivered"},
        *[{"segments": 100, "status": s} for s in
          ("held_back", "not_sent", "skipped", "blocked", "failed", "pending", "undelivered")]])
    fake = fx.FakeStripe()
    try:
        run_pass(db, fake, settled_after(SENT))
        assert fake.metered_values == [7]
    finally:
        drop_campaign(db, campaign_id)


# ─── A1, carried forward: the raw count, and the legacy rule ────────────────


def test_the_meter_receives_the_raw_count_not_billable_segments(db, subscribed):
    """12,000 in one campaign: raw is 12,000, allowance-applied is 2,000. The
    tier subtracts the allowance; subtracting it here too bills $0 for a
    15,000-segment month while `/usage` says $75 due."""
    campaign_id = make_campaign(db, name="B1b raw", rows=[{"segments": 12000}])
    fake = fx.FakeStripe()
    try:
        run_pass(db, fake, settled_after(SENT))
    finally:
        drop_campaign(db, campaign_id)
    assert fake.metered_values == [12000]
    assert billing_service.billable_segments(12000) == 2000


def test_a_legacy_row_is_priced_as_usage_prices_it(db, subscribed):
    """Rows with no stored count, swept across the 160-character boundary,
    metered by the pass and compared with `compute_usage()` on the same rows."""
    lengths = (0, 1, 159, 160, 161, 320, 321, 480, 1000)
    campaign_id = make_campaign(db, name="B1b legacy", rows=[
        {"segments": None, "message": "x" * n} for n in lengths])
    fake = fx.FakeStripe()
    try:
        expected = sum(billing_service.legacy_segment_count("x" * n) for n in lengths)
        assert expected > len(lengths), "the sweep must contain rows that are not one segment"
        run_pass(db, fake, settled_after(SENT))
        _, via_usage = billing_service.compute_usage(db, date(2021, 5, 2), date(2021, 5, 2))
        assert fake.metered_values == [expected]
        assert via_usage == expected
    finally:
        drop_campaign(db, campaign_id)


# ─── Stripe down, and the scheduler ─────────────────────────────────────────


def test_a_stripe_failure_marks_nothing_and_stops_the_pass(db, subscribed):
    """Two campaigns, Stripe raising: one attempted call, no row marked, the
    verdict names the failure, and the next pass (Stripe back) meters both."""
    first = make_campaign(db, name="B1b outage a", rows=[{"segments": 5}])
    second = make_campaign(db, name="B1b outage b", rows=[{"segments": 6}])
    down = fx.FakeStripe(fail_with=RuntimeError("Stripe is down"))
    up = fx.FakeStripe(dedupe=False)
    try:
        verdict = run_pass(db, down, settled_after(SENT))
        assert verdict["failed"]["error"] == "Stripe is down"
        assert verdict["reported"] == []
        assert len(meter_calls(down)) == 1, "the pass kept hammering a failing Stripe"
        assert all(r.metered_at is None for cid in (first, second) for r in rows_of(db, cid))

        verdict = run_pass(db, up, settled_after(SENT) + timedelta(hours=1))
        assert sorted(up.metered_values) == [5, 6]
        assert verdict["failed"] is None
    finally:
        drop_campaign(db, first)
        drop_campaign(db, second)


def test_a_failure_after_the_first_batch_leaves_that_batch_committed(db, subscribed):
    """Mid-pass: batch one accepted and marked, batch two fails. The mark on
    batch one survives, and the next pass offers only batch two."""
    first = make_campaign(db, name="B1b partial a", rows=[{"segments": 5}])
    second = make_campaign(db, name="B1b partial b", rows=[{"segments": 6}])

    class _SecondCallFails(fx.FakeStripe):
        def create_meter_event(self, **params):
            if len(self.named("create_meter_event")) == 1:
                self.calls.append(("create_meter_event", params))
                raise RuntimeError("Stripe went away")
            return super().create_meter_event(**params)

    fake = _SecondCallFails(dedupe=False)
    try:
        verdict = run_pass(db, fake, settled_after(SENT))
        assert [b["campaign_id"] for b in verdict["reported"]] == [first]
        assert verdict["failed"]["campaign_id"] == second
        assert all(r.metered_at for r in rows_of(db, first))
        assert all(r.metered_at is None for r in rows_of(db, second))
        # The failed batch was staged, so the next pass *replays* it rather
        # than batching it afresh — and reports nothing new.
        verdict = run_pass(db, fake, settled_after(SENT) + timedelta(hours=1))
        assert verdict["replayed"]["campaign_id"] == second
        assert verdict["reported"] == []
        assert fake.metered_values == [5, 6]
    finally:
        drop_campaign(db, first)
        drop_campaign(db, second)


def test_the_scheduled_job_never_raises(db, subscribed, monkeypatch):
    """An exception out of a scheduled job is a job APScheduler stops running.
    Two shapes: Stripe raising (the pass returns), and something the pass does
    not expect (the job returns)."""
    campaign_id = make_campaign(db, name="B1b job", rows=[{"segments": 5}])
    down = fx.FakeStripe(fail_with=RuntimeError("Stripe is down"))
    try:
        with fx.stripe_configured(), fx.replaced_api(down):
            verdict = stripe_meter.metering_pass_job(now=settled_after(SENT))
        # The fake was reached: the first version of this half ran on the real
        # clock, the 2021 row was refused as too old, and "reported == []" held
        # for a reason that had nothing to do with Stripe being down.
        assert len(meter_calls(down)) == 1, "the job never reached Stripe"
        assert verdict["failed"]["error"] == "Stripe is down"
        assert verdict["reported"] == []

        def _boom(*args, **kwargs):
            raise RuntimeError("the database went away")

        monkeypatch.setattr(stripe_meter, "settled_unmetered", _boom)
        # The same clock: the batch the first half left staged is replayed
        # first (Stripe is back), then the selection raises.
        with fx.stripe_configured(), fx.replaced_api(fx.FakeStripe()):
            verdict = stripe_meter.metering_pass_job(
                now=settled_after(SENT) + timedelta(hours=1))
        assert verdict["failed"]["error"] == "the database went away"
        assert verdict["reason"] == "pass failed"
    finally:
        drop_campaign(db, campaign_id)


def test_the_metering_pass_is_registered_hourly_under_its_own_id(db):
    """Against the app's own scheduler registration, by callable, and every
    job id unique — a duplicated id under `replace_existing=True` silently
    unregisters whichever job held it first."""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.interval import IntervalTrigger

    registered = []
    scheduler = AsyncIOScheduler()

    def _record(func, trigger, *, id, **kwargs):
        registered.append((id, func, trigger, kwargs))

    scheduler.add_job = _record
    import app.main as main
    original, main.scheduler = main.scheduler, scheduler
    try:
        asyncio.run(_drain(main.lifespan(main.app)))
    finally:
        main.scheduler = original

    ours = [row for row in registered if row[1] is stripe_meter.metering_pass_job]
    assert len(ours) == 1, [row[0] for row in registered]
    job_id, _, trigger, kwargs = ours[0]
    assert isinstance(trigger, IntervalTrigger) and trigger.interval == timedelta(hours=1)
    assert kwargs.get("max_instances") == 1
    ids = [row[0] for row in registered]
    assert len(ids) == len(set(ids)), f"duplicate scheduler job id: {sorted(ids)}"
    assert not asyncio.iscoroutinefunction(stripe_meter.metering_pass_job), (
        "a coroutine job runs on the event loop the send path shares")


async def _drain(context):
    await context.__aenter__()
    await context.__aexit__(None, None, None)


# ─── The identifier: deterministic, and not load-bearing ────────────────────


def test_the_identifier_is_a_pure_function_of_the_batch(db, subscribed):
    campaign_id = make_campaign(db, name="B1b identifier", rows=[{"segments": 2}] * 3)
    try:
        rows = [r for r in stripe_meter.settled_unmetered(db, settled_after(SENT), SUBSCRIBED_ON)
                if r.campaign_id == campaign_id]
        one, two = stripe_meter.batches(rows), stripe_meter.batches(list(reversed(rows)))
        assert one[0].identifier == two[0].identifier
        assert str(campaign_id) in one[0].identifier
        assert one[0].identifier == one[0].identifier, "not deterministic"
    finally:
        drop_campaign(db, campaign_id)


def test_a_lost_commit_is_re_offered_under_the_same_identifier(db, subscribed, monkeypatch):
    """Stripe accepted; the mark's commit failed (a database lock under a
    webhook storm, or the process died). Then a row in the batch flips before
    the next pass. The batch was staged, so the next pass re-offers exactly
    the staged rows under exactly the staged identifier — not a recomputed
    one, which the flip would have changed — Stripe dedupes it, and every
    staged row is marked, the flipped one included."""
    campaign_id = make_campaign(db, name="B1b lost commit", rows=[
        {"segments": 8, "external_id": f"b1b-lost-{n}"} for n in range(3)])
    fake = fx.FakeStripe()                     # dedupes, as Stripe does within a day
    try:
        def _lock(db, staged, now):
            raise RuntimeError("database is locked")

        monkeypatch.setattr(stripe_meter, "_finish", _lock)
        verdict = run_pass(db, fake, settled_after(SENT))
        assert verdict["failed"]["error"] == "database is locked"
        assert verdict["reported"] == []
        staged = stripe_meter.pending_batch(db)
        assert staged and staged["segments"] == 24, staged
        assert all(r.metered_at is None for r in rows_of(db, campaign_id))
        monkeypatch.undo()

        record_delivery_status(db, "b1b-lost-1", "undelivered", "late receipt")
        db.expire_all()
        verdict = run_pass(db, fake, settled_after(SENT) + timedelta(hours=1))
        assert verdict["replayed"]["identifier"] == staged["identifier"]
        assert verdict["reported"] == [], "the survivors were re-batched and billed again"
        assert len(meter_calls(fake)) == 2
        assert fake.metered_values == [24], "the retry was billed a second time"
        assert all(r.metered_at for r in rows_of(db, campaign_id)), (
            "a staged row was left unmarked")
        assert stripe_meter.pending_batch(db) is None
    finally:
        drop_campaign(db, campaign_id)


def test_a_stripe_failure_leaves_the_batch_staged_and_the_next_pass_replays_it(db, subscribed):
    """Stripe down: the staged row survives with its identifier; Stripe back:
    the replay sends that identifier, marks, and clears the stage."""
    campaign_id = make_campaign(db, name="B1b staged", rows=[{"segments": 9}] * 2)
    down = fx.FakeStripe(fail_with=RuntimeError("Stripe is down"))
    up = fx.FakeStripe(dedupe=False)
    try:
        run_pass(db, down, settled_after(SENT))
        staged = stripe_meter.pending_batch(db)
        assert staged is not None, "the batch was not staged before the call"
        (_, attempted), = meter_calls(down)
        assert attempted["identifier"] == staged["identifier"]

        verdict = run_pass(db, up, settled_after(SENT) + timedelta(hours=1))
        (_, replayed), = meter_calls(up)
        assert replayed["identifier"] == staged["identifier"]
        assert replayed["timestamp"] == staged["timestamp"]
        assert verdict["replayed"]["segments"] == 18 and verdict["reported"] == []
        assert stripe_meter.pending_batch(db) is None
        assert all(r.metered_at for r in rows_of(db, campaign_id))
    finally:
        drop_campaign(db, campaign_id)


def test_a_batch_staged_longer_than_the_identifier_window_is_refused_until_resolved(
        db, subscribed, caplog):
    """Past 24 hours Stripe no longer dedupes the identifier, so a retry could
    be a second bill and a skip could be a missed one. Neither is guessed at:
    the pass refuses, loudly, and a person answers from Stripe's event summary."""
    campaign_id = make_campaign(db, name="B1b stale stage", rows=[{"segments": 5}] * 2)
    fake = fx.FakeStripe(dedupe=False)
    now = settled_after(SENT)
    try:
        rows = [r for r in stripe_meter.settled_unmetered(db, now, SUBSCRIBED_ON)
                if r.campaign_id == campaign_id]
        (batch,) = stripe_meter.batches(rows)
        set_setting(db, stripe_meter.PENDING_BATCH_KEY, stripe_billing.dumps(
            {**batch.staged(), "staged_at": (now - timedelta(hours=25)).isoformat()}))
        with caplog.at_level(logging.ERROR, logger="billing.stripe"):
            verdict = run_pass(db, fake, now)
        assert fake.calls == [], "a stale staged batch was retried"
        assert verdict["reason"] == "a staged batch is too old to retry"
        assert any("resolve_pending_batch" in r.getMessage() for r in caplog.records)
        assert all(r.metered_at is None for r in rows_of(db, campaign_id))

        # Stripe's summary shows it: mark without sending, and the pass resumes.
        done = stripe_meter.resolve_pending_batch(db, recorded=True, now=now)
        assert done["resolved"] and done["identifier"] == batch.identifier
        assert all(r.metered_at for r in rows_of(db, campaign_id))
        verdict = run_pass(db, fake, now + timedelta(hours=1))
        assert verdict["reason"] is None and fake.calls == []
    finally:
        drop_campaign(db, campaign_id)


def test_an_unrecorded_stale_batch_is_cleared_and_batched_afresh(db, subscribed):
    campaign_id = make_campaign(db, name="B1b stale unrecorded", rows=[{"segments": 6}])
    fake = fx.FakeStripe(dedupe=False)
    now = settled_after(SENT)
    try:
        (batch,) = stripe_meter.batches([
            r for r in stripe_meter.settled_unmetered(db, now, SUBSCRIBED_ON)
            if r.campaign_id == campaign_id])
        set_setting(db, stripe_meter.PENDING_BATCH_KEY, stripe_billing.dumps(
            {**batch.staged(), "staged_at": (now - timedelta(days=2)).isoformat()}))
        assert run_pass(db, fake, now)["failed"]
        stripe_meter.resolve_pending_batch(db, recorded=False)
        assert stripe_meter.pending_batch(db) is None
        verdict = run_pass(db, fake, now)
        assert [b["campaign_id"] for b in verdict["reported"]] == [campaign_id]
        assert fake.metered_values == [6]
    finally:
        drop_campaign(db, campaign_id)


# ─── Scope: the bound, and the states in which nothing is metered ───────────


def test_rows_sent_before_the_subscription_are_not_the_meters_business(db, subscribed):
    """August is settled by the one-time balance. A pass that ranged over it
    would bill it twice — and the breakdown says why the rows are unmetered."""
    campaign_id = make_campaign(db, name="B1b pre-subscription", rows=[
        {"segments": 28002, "sent_at": "2021-04-14T10:00:00"}])
    fake = fx.FakeStripe()
    try:
        verdict = run_pass(db, fake, datetime(2021, 5, 3, 10, 0))
        assert fake.calls == [] and verdict["refused"] == []
        breakdown = stripe_reconcile.unmetered_breakdown(
            db, date(2021, 4, 1), date(2021, 4, 30), now=datetime(2021, 5, 3, 10, 0))
        assert breakdown["unmetered"] == {
            stripe_reconcile.BEFORE_SUBSCRIPTION: {"rows": 1, "segments": 28002}}
    finally:
        drop_campaign(db, campaign_id)


def test_nothing_is_metered_before_the_client_has_subscribed(db):
    campaign_id = make_campaign(db, name="B1b unsubscribed", rows=[{"segments": 5}])
    fake = fx.FakeStripe()
    try:
        verdict = run_pass(db, fake, settled_after(SENT))
        assert verdict["reason"] == "no subscription yet" and fake.calls == []

        set_setting(db, stripe_billing.CUSTOMER_ID_KEY, fx.CUSTOMER)
        verdict = run_pass(db, fake, settled_after(SENT))
        assert verdict["reason"] == "no subscription start stored", (
            "a customer with no stored start ran with no lower bound")
        assert fake.calls == []
        assert all(r.metered_at is None for r in rows_of(db, campaign_id))
    finally:
        drop_campaign(db, campaign_id)


def test_a_stored_start_with_no_customer_meters_nothing(db):
    """The arrangement in which the empty-customer guard is the only thing
    deciding: a start is stored, so the bound would let the pass through, and
    no customer is. Without this the guard's mutation was caught one guard
    down, for a reason string that had nothing to do with it."""
    set_setting(db, stripe_billing.CYCLE_ANCHOR_AT_KEY, SUBSCRIBED_ON.isoformat())
    campaign_id = make_campaign(db, name="B1b no customer", rows=[{"segments": 5}])
    fake = fx.FakeStripe()
    try:
        verdict = run_pass(db, fake, settled_after(SENT))
        assert verdict["reason"] == "no subscription yet"
        assert fake.calls == [], "an event was posted with no customer to bill"
        assert all(r.metered_at is None for r in rows_of(db, campaign_id))
    finally:
        drop_campaign(db, campaign_id)


def test_a_missing_key_with_a_customer_stored_is_loud(db, subscribed, caplog):
    """A key removed after checkout. The pass would otherwise skip every hour
    with nothing on any screen saying so, until every row aged past 35 days."""
    campaign_id = make_campaign(db, name="B1b no key", rows=[{"segments": 5}])
    try:
        assert not stripe_billing.configured()
        with caplog.at_level(logging.ERROR, logger="billing.stripe"):
            verdict = stripe_meter.meter_settled_rows(db, now=settled_after(SENT))
        assert verdict["reason"] == "stripe not configured"
        assert any("STRIPE_SECRET_KEY" in r.getMessage() and r.levelno == logging.ERROR
                   for r in caplog.records), [r.getMessage() for r in caplog.records]
    finally:
        drop_campaign(db, campaign_id)


def test_an_unreadable_subscription_start_refuses_rather_than_ranging(db, subscribed):
    set_setting(db, stripe_billing.CYCLE_ANCHOR_AT_KEY, "last Tuesday")
    campaign_id = make_campaign(db, name="B1b bad start", rows=[{"segments": 5}])
    fake = fx.FakeStripe()
    try:
        verdict = run_pass(db, fake, settled_after(SENT))
        assert verdict["reason"] == "no subscription start stored"
        assert fake.calls == []
    finally:
        drop_campaign(db, campaign_id)


def test_a_billable_row_with_no_send_time_is_never_selected(db, subscribed):
    """It cannot be timestamped or placed in a window, and `compute_usage()`
    cannot see it either; both selections and the pass leave it alone."""
    campaign_id = make_campaign(db, name="B1b no send time", rows=[
        {"segments": 5, "sent_at": None, "status": "delivered"}])
    fake = fx.FakeStripe()
    try:
        now = settled_after(SENT)
        assert not [r for r in stripe_meter.settled_unmetered(db, now, SUBSCRIBED_ON)
                    if r.campaign_id == campaign_id]
        assert not [r for r in stripe_meter.too_old_unmetered(db, now, SUBSCRIBED_ON)
                    if r.campaign_id == campaign_id]
        verdict = run_pass(db, fake, now)
        assert verdict["failed"] is None
    finally:
        drop_campaign(db, campaign_id)


def test_a_batch_larger_than_one_update_chunk_is_marked_in_full(db, subscribed):
    count = stripe_meter.MARK_CHUNK + 1
    campaign_id = make_campaign(db, name="B1b chunk", rows=[{"segments": 1}] * count)
    fake = fx.FakeStripe(dedupe=False)
    try:
        run_pass(db, fake, settled_after(SENT))
        assert fake.metered_values == [count]
        assert sum(1 for r in rows_of(db, campaign_id) if r.metered_at) == count
        run_pass(db, fake, settled_after(SENT) + timedelta(hours=1))
        assert len(meter_calls(fake)) == 1
    finally:
        drop_campaign(db, campaign_id)


# ─── Reconciliation: the breakdown adds up, and the tool renders it ─────────


def test_the_breakdown_adds_up_to_usage_and_names_every_reason(db, subscribed):
    """A window with one row in every state: metered, awaiting the pass, not
    settled, too old, before the subscription, and one metered-then-flipped."""
    now = datetime(2021, 6, 12, 10, 0)
    campaign_id = make_campaign(db, name="B1b breakdown", rows=[
        {"segments": 1, "sent_at": "2021-06-10T09:00:00", "external_id": "b1b-bd-metered"},
        {"segments": 2, "sent_at": "2021-06-10T10:00:00", "external_id": "b1b-bd-flipped"},
        {"segments": 4, "sent_at": "2021-06-12T09:30:00"},              # not settled
        {"segments": 8, "sent_at": "2021-05-03T09:00:00"},              # too old
        {"segments": 16, "sent_at": "2021-04-20T09:00:00"},             # before subscription
        # Unsettled at the first pass (11.5 hours old), settled by `now`
        # (33.5 hours): the one state that reads "awaiting the next pass".
        {"segments": 32, "sent_at": "2021-06-11T00:30:00"},
    ])
    fake = fx.FakeStripe()
    try:
        run_pass(db, fake, datetime(2021, 6, 11, 12, 0))          # meters rows 1, 2
        record_delivery_status(db, "b1b-bd-flipped", "undelivered", "late receipt")
        db.expire_all()
        breakdown = stripe_reconcile.unmetered_breakdown(
            db, date(2021, 4, 1), date(2021, 6, 30), now=now)
        _, usage = billing_service.compute_usage(db, date(2021, 4, 1), date(2021, 6, 30))

        assert breakdown["metered_segments"] + breakdown["unmetered_segments"] == usage
        assert breakdown["metered_segments"] == 1
        assert breakdown["unmetered"] == {
            stripe_reconcile.NOT_SETTLED: {"rows": 1, "segments": 4},
            stripe_reconcile.TOO_OLD: {"rows": 1, "segments": 8},
            stripe_reconcile.BEFORE_SUBSCRIPTION: {"rows": 1, "segments": 16},
            stripe_reconcile.AWAITING_PASS: {"rows": 1, "segments": 32},
        }
        assert breakdown["metered_no_longer_billable"] == {"rows": 1, "segments": 2}
    finally:
        drop_campaign(db, campaign_id)


def test_the_tool_refuses_to_invoice_a_window_that_is_partly_on_the_meter(db, subscribed, capsys):
    """Review finding, measured: 12,000 metered plus 12,000 refused priced as
    24,000 and $210.00 — the metered half billed twice and the allowance
    applied twice. `--create` refuses any window with metered rows in it; the
    positive control is a window nothing has metered, which still invoices."""
    from tools import bill_period

    august = make_campaign(db, name="B1b tool metered", rows=[
        {"segments": 12000, "sent_at": "2021-08-20T09:00:00"}])
    fake = fx.FakeStripe(dedupe=False)
    try:
        run_pass(db, fake, settled_after("2021-08-20T09:00:00"))
        assert fake.metered_values == [12000]
        db.add(SMSMessage(campaign_id=august, phone=PHONE, message="hi", status="sent",
                          segments=12000, sent_at="2021-08-02T09:00:00"))
        db.commit()
        with fx.stripe_configured(), fx.replaced_api(fake):
            code = bill_period.main(["--start", "2021-08-01", "--end", "2021-08-31",
                                     "--create"])
        out = capsys.readouterr().out
        assert code == 2, out
        assert "REFUSED: THIS WINDOW IS PARTLY ON THE STRIPE METER" in out
        assert "12,000 of its 24,000 billable segments" in out
        assert "decisions/012" in out
        assert fake.invoices == [] and fake.invoice_items == [], "an invoice was drafted"

        # Positive control: nothing metered in September, the invoice is drafted.
        db.add(SMSMessage(campaign_id=august, phone=PHONE, message="hi", status="sent",
                          segments=12000, sent_at="2021-09-02T09:00:00"))
        db.commit()
        with fx.stripe_configured(), fx.replaced_api(fake):
            code = bill_period.main(["--start", "2021-09-01", "--end", "2021-09-30",
                                     "--create"])
        out = capsys.readouterr().out
        assert code == 0 and "REFUSED" not in out, out
        assert len(fake.invoices) == 1
    finally:
        drop_campaign(db, august)


def test_the_tool_renders_the_breakdown_from_the_command_line(db, subscribed, capsys):
    from tools import bill_period

    campaign_id = make_campaign(db, name="B1b tool", rows=[
        {"segments": 9, "sent_at": "2021-07-05T09:00:00"}])
    try:
        assert bill_period.main(["--start", "2021-07-01", "--end", "2021-07-31",
                                 "--unmetered"]) == 0
    finally:
        drop_campaign(db, campaign_id)
    out = capsys.readouterr().out
    assert "dry run" in out
    assert "Meter reconciliation for 2021-07-01 to 2021-07-31" in out
    assert "Billable segments 9" in out
    assert "unmetered       9" in out
    assert stripe_reconcile.TOO_OLD in out
