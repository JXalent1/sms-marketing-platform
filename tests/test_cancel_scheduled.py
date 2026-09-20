"""Cancelling a scheduled campaign, and the race that makes it hard.

Session 5n A2. There was no cancel route, no service function and no control:
a campaign scheduled for the wrong day could only be stopped by editing the
database. `campaign_dispatch.cancel_scheduled()` clears `scheduled_at` and
leaves an editable draft — the choice and its consequence are in that
function's docstring — and refuses, with a sentence naming the state, on
anything that has started.

**The race is the whole risk.** `run_due_campaigns()` selects every due
campaign in one query and then sends them one at a time, and a campaign at the
back of that list waits behind every blast in front of it while the send loop
yields after each message. A cancel that lands in that gap clears a schedule on
a campaign the tick has already decided to send — and `send_campaign()` checks
`status`, which a cancel does not touch. Measured on the pre-fix tree, with the
cancel simulated as the raw clear it would have to be:

    selected [1]; then cancelled #1 (scheduled_at -> NULL, status still draft)
    dispatched=[1]  status=completed  sent rows=1

Two tests below assert the race directly rather than the happy path: one
injects the cancel between selection and dispatch surgically, the other runs
it concurrently on the event loop while an earlier campaign is mid-send, which
is the shape production has.

Every row this module writes carries the guardrail prefix and is purged at
both ends, and the rate-limit budget it spends is given back — see
`test_campaign_guardrails.py` for both rules.
"""

import asyncio
import threading
import time
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.core import clock
from app.core.database import SessionLocal
from app.main import app
from app.models.campaign import Campaign
from app.models.contact import Contact
from app.models.sms_message import SMSMessage
from app.services import campaign_dispatch
from app.services.campaign_claim import SendClaimLost
from app.services.campaign_dispatch import (
    CANCEL_STATE_ERRORS, NOT_SCHEDULED, cancel_scheduled, due_campaign_ids,
    run_due_campaigns, still_scheduled,
)
from app.services.campaign_service import CampaignError, CampaignService
from tests import _guardrail_setup as setup
from tests._guardrail_setup import CAMPAIGN_PREFIX, PHONES

PASSWORD = "devpassword123"
FAR_FUTURE = "2030-01-01T18:00:00"


@pytest.fixture(scope="module")
def seeded():
    yield from setup.seeded_fixture_body()


@pytest.fixture(scope="module", autouse=True)
def rate_limit_budget():
    yield from setup.rate_limit_fixture_body()


@pytest.fixture(scope="module")
def client(seeded):
    c = TestClient(app)
    login = c.post("/login", data={"username": "admin", "password": PASSWORD},
                   follow_redirects=False)
    assert login.status_code in (200, 302)
    return c


def _create(db, seeded, name, *, scheduled_at=FAR_FUTURE, status=None) -> Campaign:
    """A scheduled draft through the service, then optionally moved to a state.

    The state is written directly because it is the *precondition* under test —
    what cancel says about a completed campaign — not a thing the send loop is
    being asked to produce here.
    """
    campaign = CampaignService(db).create_campaign(
        name=f"{CAMPAIGN_PREFIX}cancel {name}",
        message_template="The sale is tonight. Reply STOP to opt out.",
        audience=seeded["audience"],
        category_id=seeded["category_id"],
        scheduled_at=scheduled_at,
    )
    if status:
        campaign.status = status
        campaign.sent_count = 2 if status in ("running", "completed") else 0
        db.commit()
    return campaign


def _past() -> str:
    return (clock.now() - timedelta(minutes=5)).isoformat()


def _fresh_contacts() -> None:
    """Nobody on the fixture list has been texted. Called by every test that sends.

    The hold-back window is real in this suite, and a campaign that *sends* to
    the three fixture contacts holds them back for the next campaign built
    here. The 5n review found the concurrent race test red on the pre-fix tree
    for that reason and not the one it names: the first test's campaign sent
    (the defect), the second test's "first" campaign then resolved to nobody,
    aborted without yielding, and the cancel never landed mid-send. A test
    that needs its neighbours to have behaved proves nothing about the
    criterion it is named for — so each one starts from an untexted list.
    """
    db = SessionLocal()
    try:
        db.query(Contact).filter(Contact.phone.in_(PHONES)).update(
            {"last_messaged_at": None}, synchronize_session=False)
        db.commit()
    finally:
        db.close()


class _Provider:
    """The console provider's shape, with a balance call whose blocking is chosen.

    `awaiting` sleeps on the loop, which is what an async HTTP client would do;
    `blocking` sleeps the thread inside an `async def`, which is what `urllib`
    and the SDK do today. Sends succeed and cost nothing, as the dry run's do.
    """
    name = "console"

    def __init__(self, balance_takes: float, awaiting: bool):
        self.balance_takes, self.awaiting = balance_takes, awaiting
        self.sent = 0

    async def get_balance(self):
        if self.awaiting:
            await asyncio.sleep(self.balance_takes)
        else:
            time.sleep(self.balance_takes)
        return 999_999.0

    async def send(self, to, text):
        from app.sms.base import SendResult
        self.sent += 1
        return SendResult(success=True, message_id=f"probe-{self.sent}", parts=1)


# ─── Criteria 5 and 8: cancelled, and still there ───────────────────────────

def test_cancel_clears_the_schedule_and_leaves_an_editable_draft(client, seeded):
    """A cancelled campaign is a draft with no time: name, audience and rows intact."""
    db = SessionLocal()
    try:
        campaign = _create(db, seeded, "wrong day")
        campaign_id = campaign.id
        before = {
            "name": campaign.name, "audience": campaign.audience,
            "audience_label": campaign.audience_label,
            "message": campaign.message_template,
            "recipients": campaign.total_recipients,
            "pending": db.query(SMSMessage).filter(
                SMSMessage.campaign_id == campaign_id,
                SMSMessage.status == "pending").count(),
        }
        assert before["pending"] > 0, "the fixture campaign must have rows to keep"
    finally:
        db.close()

    response = client.post(f"/api/campaigns/{campaign_id}/cancel")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True
    assert body["campaign"]["scheduled_at"] is None
    assert body["campaign"]["status"] == "draft"
    assert "back to a draft" in body["message"]

    db = SessionLocal()
    try:
        after = db.get(Campaign, campaign_id)
        assert after is not None, "cancelling is not deleting"
        assert after.status == "draft"
        assert after.scheduled_at is None
        assert after.name == before["name"]
        assert after.audience == before["audience"]
        assert after.audience_label == before["audience_label"]
        assert after.message_template == before["message"]
        assert after.total_recipients == before["recipients"]
        assert db.query(SMSMessage).filter(
            SMSMessage.campaign_id == campaign_id,
            SMSMessage.status == "pending").count() == before["pending"]
        assert after.abort_reason is None, "a cancel is not an abort"
    finally:
        db.close()


def test_a_cancelled_campaign_is_never_due(client, seeded):
    """Criterion 5's other half: the scheduler cannot pick it up afterwards.

    Scheduled in the past, so it *is* due — then cancelled. `due_campaign_ids()`
    is asked with a `now` a year on, which is as due as a campaign can get.
    """
    db = SessionLocal()
    try:
        campaign = _create(db, seeded, "already due", scheduled_at=_past())
        campaign_id = campaign.id
        assert campaign_id in due_campaign_ids(db)
        cancel_scheduled(db, campaign_id)
        assert campaign_id not in due_campaign_ids(db)
        long_after = clock.now() + timedelta(days=365)
        assert campaign_id not in due_campaign_ids(db, long_after)
        assert not still_scheduled(db, campaign_id)
    finally:
        db.close()


def test_the_re_check_asks_about_state_not_time(seeded):
    """`still_scheduled()` is independent of the clock, on purpose.

    Selection already judged the time, and between selection and dispatch the
    only thing that can move `scheduled_at` is a cancel, which clears it. A
    re-check that compared the wall clock again would skip a campaign selected
    in the first 1 AM hour on the first Sunday in November and reached after
    the clocks fell back — logged as cancelled, and it was not. So a scheduled
    draft that is not due yet still answers yes; a cancelled or started one no.
    """
    db = SessionLocal()
    try:
        not_yet = _create(db, seeded, "state not time")
        assert not_yet.id not in due_campaign_ids(db), "far in the future, not due"
        assert still_scheduled(db, not_yet.id) is True
        cancel_scheduled(db, not_yet.id)
        assert still_scheduled(db, not_yet.id) is False
        started = _create(db, seeded, "state not time, running", status="running")
        assert still_scheduled(db, started.id) is False
        assert still_scheduled(db, 999999) is False
    finally:
        db.close()


# ─── Criterion 6: anything that has started refuses, and says which ────────

@pytest.mark.parametrize("status", ["running", "completed", "aborted", "failed"])
def test_a_campaign_that_has_started_refuses_cancellation(client, seeded, status):
    """409, the state named, the schedule untouched — through the endpoint."""
    db = SessionLocal()
    try:
        campaign = _create(db, seeded, f"already {status}", status=status)
        campaign_id, sent, total = campaign.id, campaign.sent_count, campaign.total_recipients
    finally:
        db.close()

    response = client.post(f"/api/campaigns/{campaign_id}/cancel")
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    # The sentence is the dispatch module's own, formatted for this campaign.
    assert detail == CANCEL_STATE_ERRORS[status].format(status=status, sent=sent, total=total)
    # In his units: a word for the state, and for a send in flight, the count.
    words = {"running": "already sending", "completed": "already been sent",
             "aborted": "stopped before it sent", "failed": "failed"}
    assert words[status] in detail
    if status == "running":
        assert "2 of" in detail and "cannot be stopped" in detail

    db = SessionLocal()
    try:
        after = db.get(Campaign, campaign_id)
        assert after.status == status
        assert after.scheduled_at == FAR_FUTURE, "a refused cancel changes nothing"
    finally:
        db.close()


def test_a_draft_that_was_never_scheduled_has_nothing_to_cancel(client, seeded):
    db = SessionLocal()
    try:
        campaign = _create(db, seeded, "sent by hand", scheduled_at=None)
        campaign_id = campaign.id
    finally:
        db.close()
    response = client.post(f"/api/campaigns/{campaign_id}/cancel")
    assert response.status_code == 409
    assert response.json()["detail"] == NOT_SCHEDULED


def test_an_unknown_campaign_is_a_404(client):
    assert client.post("/api/campaigns/999999/cancel").status_code == 404


# ─── Criterion 7: the race, asserted directly ──────────────────────────────

def _sent_rows(db, campaign_id: int) -> int:
    return db.query(SMSMessage).filter(SMSMessage.campaign_id == campaign_id,
                                       SMSMessage.status == "sent").count()


def test_a_cancel_between_selection_and_dispatch_does_not_send(seeded):
    """Selected by the tick, cancelled, then handed to the dispatch path.

    The cancel is injected at the one seam the spec names — after
    `due_campaign_ids()` has returned the campaign and before `send_campaign()`
    is called for it — by wrapping the selection. On the pre-fix tree this
    sends: `send_campaign()` reads `draft` and goes. The re-check inside the
    dispatch path is what stops it.
    """
    _fresh_contacts()
    db = SessionLocal()
    try:
        campaign = _create(db, seeded, "cancelled after selection", scheduled_at=_past())
        campaign_id = campaign.id
        assert campaign_id in due_campaign_ids(db)
    finally:
        db.close()

    real_select = campaign_dispatch.due_campaign_ids

    def select_then_cancel(db, now=None):
        ids = real_select(db, now)
        assert campaign_id in ids, "the fixture campaign must be selected by the tick"
        other = SessionLocal()          # the cancel arrives on its own session
        try:
            cancel_scheduled(other, campaign_id)
        finally:
            other.close()
        return ids

    campaign_dispatch.due_campaign_ids = select_then_cancel
    try:
        dispatched = asyncio.run(run_due_campaigns())
    finally:
        campaign_dispatch.due_campaign_ids = real_select

    assert campaign_id not in dispatched, "the scheduler reported sending a cancelled campaign"

    db = SessionLocal()
    try:
        after = db.get(Campaign, campaign_id)
        assert after.status == "draft", f"a cancelled campaign was {after.status}"
        assert after.scheduled_at is None
        assert _sent_rows(db, campaign_id) == 0, "a cancelled campaign sent"
        assert db.query(SMSMessage).filter(SMSMessage.campaign_id == campaign_id,
                                           SMSMessage.status == "pending").count() > 0
    finally:
        db.close()


def test_a_cancel_during_an_earlier_campaigns_send_does_not_send_the_later_one(seeded):
    """The shape production has: two campaigns due, the second cancelled while
    the first is mid-blast.

    Both are selected in one query. The first's send loop yields after every
    message (`SEND_DELAY_SECONDS`), and the cancel runs on the same event loop
    in that gap — exactly as the `async def` cancel route would. When the tick
    reaches the second campaign it must find it cancelled and stand down.
    """
    _fresh_contacts()
    db = SessionLocal()
    try:
        first = _create(db, seeded, "first in the queue", scheduled_at=_past())
        second = _create(db, seeded, "second in the queue",
                         scheduled_at=(clock.now() - timedelta(minutes=1)).isoformat())
        first_id, second_id = first.id, second.id
        due = due_campaign_ids(db)
        assert due.index(first_id) < due.index(second_id), "the first must go first"
    finally:
        db.close()

    async def cancel_the_second_while_the_first_sends():
        await asyncio.sleep(0.05)       # inside the first campaign's send loop
        other = SessionLocal()
        try:
            live = other.get(Campaign, first_id)
            assert live.status == "running", "the cancel must land mid-send to prove anything"
            cancel_scheduled(other, second_id)
        finally:
            other.close()

    async def both():
        return await asyncio.gather(run_due_campaigns(),
                                    cancel_the_second_while_the_first_sends())

    dispatched, _ = asyncio.run(both())
    assert first_id in dispatched
    assert second_id not in dispatched, "the cancelled campaign was dispatched"

    db = SessionLocal()
    try:
        assert db.get(Campaign, first_id).status == "completed"
        assert _sent_rows(db, first_id) > 0
        second_after = db.get(Campaign, second_id)
        assert second_after.status == "draft"
        assert second_after.scheduled_at is None
        assert _sent_rows(db, second_id) == 0, "the cancelled campaign sent"
    finally:
        db.close()


def test_cancel_decides_on_the_row_as_it_stands_not_the_object_it_read(seeded):
    """The other direction of the race: the dispatch got there first.

    A session that loaded the campaign as a scheduled draft asks to cancel it
    after another session has flipped it to `running`. The identity map still
    says draft; the database says otherwise; the conditional clear matches no
    row, and the refusal is re-read from the row rather than from the object —
    so the schedule is not cleared on a blast that is going out.
    """
    stale = SessionLocal()
    other = SessionLocal()
    try:
        campaign = _create(stale, seeded, "taken before the cancel")
        campaign_id = campaign.id
        assert stale.get(Campaign, campaign_id).status == "draft"

        other.query(Campaign).filter(Campaign.id == campaign_id).update(
            {"status": "running", "sent_count": 1}, synchronize_session=False)
        other.commit()

        with pytest.raises(CampaignError) as refused:
            cancel_scheduled(stale, campaign_id)
        assert "already sending" in str(refused.value)

        other.expire_all()
        row = other.get(Campaign, campaign_id)
        assert row.status == "running"
        assert row.scheduled_at == FAR_FUTURE, "the schedule was cleared under a running send"
    finally:
        stale.close()
        other.close()


def test_run_due_campaigns_reports_only_what_it_dispatched(seeded):
    """The return value names the campaigns that went, not the ones selected."""
    _fresh_contacts()
    db = SessionLocal()
    try:
        going = _create(db, seeded, "going out", scheduled_at=_past())
        going_id = going.id
        gone = _create(db, seeded, "cancelled first", scheduled_at=_past())
        gone_id = gone.id
        cancel_scheduled(db, gone_id)
    finally:
        db.close()

    dispatched = asyncio.run(run_due_campaigns())
    assert going_id in dispatched
    assert gone_id not in dispatched
    db = SessionLocal()
    try:
        # "Dispatched" has to mean it went: a campaign that resolved to nobody
        # and aborted would also be in the list, and would prove nothing here.
        assert db.get(Campaign, going_id).status == "completed"
        assert _sent_rows(db, going_id) > 0
    finally:
        db.close()


# ─── The gap after still_scheduled(): the flip to `running` is a claim ───────────
#
# `still_scheduled()` answers before dispatch. Between it and the `running` commit
# sits the pre-flight, which awaits the provider's balance call — and whether
# a cancel can land in that gap depends on the cancel route being on the loop,
# the provider blocking it, and uvicorn running one worker. The 5n review
# measured it open in three of four arrangements. So the flip itself is a
# conditional UPDATE (`campaign_claim.take()`), and these tests put a cancel in
# that exact gap, both ways a request can arrive.

def _run_with_cancel_mid_preflight(seeded, *, awaiting: bool, from_thread: bool):
    _fresh_contacts()
    db = SessionLocal()
    try:
        campaign = _create(db, seeded, f"cancelled mid-preflight {awaiting} {from_thread}",
                           scheduled_at=_past())
        campaign_id = campaign.id
    finally:
        db.close()

    provider = _Provider(balance_takes=0.4, awaiting=awaiting)
    outcome = {}

    def do_cancel():
        other = SessionLocal()
        try:
            cancel_scheduled(other, campaign_id)
            outcome["cancel"] = "cancelled"
        except CampaignError as e:
            outcome["cancel"] = f"refused: {e}"
        finally:
            other.close()

    async def main():
        if from_thread:
            timer = threading.Timer(0.15, do_cancel)
            timer.start()
            try:
                return await run_due_campaigns()
            finally:
                timer.join()

        async def on_loop():
            await asyncio.sleep(0.15)
            do_cancel()
        dispatched, _ = await asyncio.gather(run_due_campaigns(), on_loop())
        return dispatched

    import app.services.campaign_service as campaign_module
    original = campaign_module.get_provider
    campaign_module.get_provider = lambda: provider
    try:
        dispatched = asyncio.run(main())
    finally:
        campaign_module.get_provider = original

    db = SessionLocal()
    try:
        after = db.get(Campaign, campaign_id)
        return {"dispatched": campaign_id in dispatched, "cancel": outcome.get("cancel"),
                "status": after.status, "scheduled_at": after.scheduled_at,
                "sent_rows": _sent_rows(db, campaign_id), "provider_sent": provider.sent}
    finally:
        db.close()


@pytest.mark.parametrize("awaiting,from_thread", [
    (True, False),    # an async HTTP client, cancel on the loop
    (False, True),    # today's provider (urllib blocks, releases the GIL), cancel from a `def` route
    (True, True),     # both
])
def test_a_cancel_during_the_campaigns_own_preflight_is_honoured(seeded, awaiting, from_thread):
    """The cancel says "cancelled", and nothing is sent — in the same run.

    On the tree before the claim, every one of these arrangements sent: the
    cancel cleared the schedule mid-pre-flight and was told it succeeded, then
    the unconditional flip to `running` went ahead and the blast went out under
    a rail that said cancelled. Measured by the 5n review, three of four
    arrangements open.
    """
    out = _run_with_cancel_mid_preflight(seeded, awaiting=awaiting, from_thread=from_thread)
    assert out["cancel"] == "cancelled", out
    assert out["sent_rows"] == 0 and out["provider_sent"] == 0, f"sent after a cancel: {out}"
    assert out["status"] == "draft" and out["scheduled_at"] is None, out
    assert out["dispatched"] is False, "stood down, so not reported as dispatched"


def test_two_runs_that_both_read_a_draft_cannot_both_send_it(seeded):
    """The button path's double-click, at the claim.

    Two services each load the same draft, and each is handed to the send loop
    in turn. The first claims and sends. The second's claim matches no row —
    the status is no longer `draft` — and it stands down having written
    nothing. Without the claim the second run flips the campaign to `running`
    again, finds nothing pending (the rows are already `sent`), and adjudicates
    the run as "reached nobody": a campaign that reached three people is
    relabelled `aborted`, with a reason saying nobody was sent. Measured on a
    scratch copy with the flip made unconditional; the assertions are ordered
    so that is the first thing to fail there.
    """
    _fresh_contacts()
    first, second = SessionLocal(), SessionLocal()
    try:
        created = _create(first, seeded, "double click", scheduled_at=None)
        campaign_id = created.id
        as_first = first.get(Campaign, campaign_id)
        as_second = second.get(Campaign, campaign_id)
        assert as_first.status == as_second.status == "draft"

        asyncio.run(CampaignService(first).run_send_loop(as_first))
        assert as_first.status == "completed"
        sent_once = _sent_rows(first, campaign_id)
        assert sent_once > 0

        try:
            asyncio.run(CampaignService(second).run_send_loop(as_second))
            stood_down = False
        except SendClaimLost:
            stood_down = True
        second.expire_all()
        after = second.get(Campaign, campaign_id)
        assert after.status == "completed", (
            f"the second run relabelled a completed campaign as {after.status}: "
            f"{after.abort_reason!r}")
        assert _sent_rows(second, campaign_id) == sent_once, "the second run sent again"
        assert stood_down, "the second run did not stand down"
    finally:
        first.close()
        second.close()
