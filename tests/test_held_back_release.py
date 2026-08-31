"""5h A1: `held_back` is its own status, and a top-up releases it.

Implements `decisions/005-topping-up-a-contact-the-window-held-back.md`, option
2. The defect in one line: a contact the suppression window merely *deferred*
got a row marked `skipped` — the status whose contract says "wrong region",
which is permanent — so once the hold expired nothing could tell the two apart
and the buyer was unreachable inside that campaign for good.

What is asserted here, in the order the decision states it:

  - the builder writes `held_back`, and the send loop's region filter still
    writes `skipped`. Both, in one campaign, because the point is that they are
    now different and a test that only checked the first would pass on a build
    that renamed every skip.
  - decision 005's own reproduction, end to end, at its own numbers.
  - a released row is *flipped*, never duplicated (rider 3).
  - the window is re-run against **today's** value (rider 4).
  - `held_back` is outside the billable set, asserted against the billing query
    itself rather than against the tuple.
  - rows written `skipped` before this change are never re-adjudicated (rider
    2). This is the one that cannot be un-shipped later, so it is tested at the
    top-up rather than only in a comment.

The rules this file inherits are in `_campaign_flow_setup.py`: leave no rows
behind, and give back the rate-limit budget you spend.
"""

import asyncio

import pytest

from app.core.database import SessionLocal
from app.models.app_setting import AppSetting
from app.models.campaign import Campaign
from app.models.contact import Contact
from app.models.sms_message import (
    SMSMessage, BILLABLE_STATUSES, HELD_BACK_STATUS,
)
from app.services import (
    billing_service, campaign_topup, contact_service, suppression_service,
)
from app.services.campaign_service import CampaignError, CampaignService
from tests._campaign_flow_setup import (
    MESSAGE, NAME_PREFIX, iso_days_ago, purge, rate_limit_fixture_body, take,
)

# Toronto. `is_non_us_region()` matches on the area code, and the send loop
# skips it — the *other* meaning of `skipped`, which is what makes it the
# control in criterion 2. Outside the 5e pool, so this module purges it itself.
REGION_PHONE = "+14165550777"


@pytest.fixture(scope="module", autouse=True)
def rate_limits():
    yield from rate_limit_fixture_body()


@pytest.fixture(scope="module")
def db():
    """Purges at both ends, plus the one number outside the shared pool."""
    session = SessionLocal()

    def clean():
        purge(session)
        for contact in session.query(Contact).filter(Contact.phone == REGION_PHONE):
            session.query(SMSMessage).filter(
                SMSMessage.contact_id == contact.id).delete(synchronize_session=False)
            session.delete(contact)
        _clear_window(session)
        session.commit()

    try:
        clean()
        yield session
        clean()
    finally:
        session.close()


def _clear_window(db):
    """Remove the stored window, leaving the `.env` default in force.

    Not `set_suppression_days(db, 0)`. A stored 0 and no row at all are
    different states and `test_suppression_window.py` asserts on the
    difference; leaking a stored row into the modules that run afterwards is
    what made an unrelated test fail under almost every mutation in 5e.
    """
    db.query(AppSetting).filter(
        AppSetting.key == suppression_service.SUPPRESSION_DAYS_KEY).delete(
        synchronize_session=False)
    db.commit()


def _contact(db, phone, *, name="Buyer", last_messaged_at=None):
    contact = contact_service.upsert_contact(
        db, phone=phone, full_name=name, source="5e-flow-test")
    contact.last_messaged_at = last_messaged_at
    db.commit()
    return contact


def _list_campaign(db, name, phones_with_history, *, window):
    """A campaign on its own list, built with `window` in force, then sent.

    Returns (campaign, list_id). `phones_with_history` is [(phone, last_messaged
    _at)] so a test can decide who the window will catch.
    """
    suppression_service.set_suppression_days(db, window)
    contact_list = contact_service.get_or_create_list(db, f"{NAME_PREFIX}{name} list")
    for phone, last in phones_with_history:
        contact = _contact(db, phone, last_messaged_at=last)
        contact_service.add_to_list(db, contact_list.id, contact.id)
    db.commit()

    campaign = CampaignService(db).create_campaign(
        name=f"{NAME_PREFIX}{name}", message_template=MESSAGE,
        audience=f"list:{contact_list.id}", cross_category_override=True)
    return campaign, contact_list.id


def _rows(db, campaign_id):
    """{phone: [row, ...]} — the shape every duplicate assertion below needs."""
    out = {}
    for row in db.query(SMSMessage).filter(SMSMessage.campaign_id == campaign_id):
        out.setdefault(row.phone, []).append(row)
    return out


# ─── Criterion 2: the two meanings are now two statuses ─────────────────────

def test_the_window_writes_held_back_and_the_region_filter_still_writes_skipped(db):
    """One campaign, both outcomes, because the point is that they differ.

    Before 5h these two rows were indistinguishable: same status, same campaign,
    same empty `sent_at`. A test asserting only that the held-back row says
    `held_back` would pass just as happily on a build that had renamed every
    skip in the send loop too — which would break the release rule in the other
    direction, by offering to re-send to a number the region filter excluded.
    """
    held_phone, region_phone = take(1)[0], REGION_PHONE
    campaign, _ = _list_campaign(
        db, "two meanings",
        [(held_phone, iso_days_ago(1)), (region_phone, None)],
        window=3)
    try:
        assert campaign.suppressed_count == 1
        assert campaign.total_recipients == 1, "only the Toronto number is sendable"

        asyncio.run(CampaignService(db).send_campaign(campaign.id))
        rows = _rows(db, campaign.id)

        assert rows[held_phone][0].status == HELD_BACK_STATUS
        assert rows[region_phone][0].status == "skipped"
        assert rows[region_phone][0].status != HELD_BACK_STATUS, (
            "a region skip is permanent and must never be offered for release")
    finally:
        _clear_window(db)


# ─── Criterion 3: decision 005's reproduction, end to end ───────────────────

def test_the_hold_clears_and_a_top_up_reaches_exactly_the_people_it_held(db):
    """The scenario in decisions/005, at its own numbers.

        window = 3; a 4-person list, 2 of them texted yesterday
        create  -> total_recipients=2  suppressed=2
        send    -> completed, sent 2
        window lowered to 0
        top-up  -> reaches exactly those 2, by flipping their existing rows

    Three assertions carry it, and all three are needed. *Exactly those two* —
    not the two who already received it, which is the duplicate this whole
    module exists to prevent. *By flipping* — one row per phone afterwards, so a
    report is not reconciling two rows for one person. And the counters move
    both ways: a released contact stops being held back at the moment they are
    reached, or the rail says "4 recipients · 2 held back" about a campaign that
    reached all four.
    """
    fresh, texted = take(2), take(2)
    campaign, _ = _list_campaign(
        db, "held then released",
        [(p, None) for p in fresh] + [(p, iso_days_ago(1)) for p in texted],
        window=3)
    try:
        assert campaign.total_recipients == 2
        assert campaign.suppressed_count == 2

        sent = asyncio.run(CampaignService(db).send_campaign(campaign.id))
        assert sent.status == "completed" and sent.sent_count == 2

        rows = _rows(db, campaign.id)
        assert {p: rows[p][0].status for p in texted} == {
            p: HELD_BACK_STATUS for p in texted}
        assert {p: rows[p][0].status for p in fresh} == {p: "sent" for p in fresh}

        # The hold clears. This is production's own setting, and since 5e A5 it
        # is a field on the Settings page rather than an ssh away.
        suppression_service.set_suppression_days(db, 0)

        verdict = asyncio.run(campaign_topup.assess(db, sent))
        assert verdict["refusal"] is None, verdict["refusal"]
        assert {row.phone for row in verdict["released"]} == set(texted)
        assert verdict["sendable"] == [], "nobody was added to this list since"

        result = asyncio.run(campaign_topup.top_up(db, campaign.id))
    finally:
        _clear_window(db)

    db.expire_all()
    rows = _rows(db, campaign.id)
    for phone in texted:
        assert len(rows[phone]) == 1, (
            f"{phone} has {len(rows[phone])} rows — the release wrote a second one "
            f"instead of flipping the first")
        assert rows[phone][0].status == "sent"
        assert rows[phone][0].top_up_at is not None, (
            "a released row joins the recipient count on the day it is released, "
            "so the rail can say where the number came from")
        assert rows[phone][0].error_message is None, (
            "a sent message still explaining why it was held back is a lie in "
            "the client's own log")
    for phone in fresh:
        assert len(rows[phone]) == 1, "the original send was queued a second time"

    assert result.sent_count == 4
    assert result.total_recipients == 4
    assert result.suppressed_count == 0, (
        "the two released contacts are not held back any more")
    assert result.status == "completed"
    assert result.abort_reason is None

    history = campaign_topup.top_up_history(db, [campaign.id])[campaign.id]
    assert [h["recipients"] for h in history] == [2], (
        "the rail reads total minus this, so a released row has to be counted here")


# ─── Rider 4: today's window, not the campaign's ────────────────────────────

def test_a_hold_that_has_not_cleared_is_not_released(db):
    """The same setup with the window left alone refuses.

    Rider 4 cuts both ways and this is the expensive direction: re-running the
    rule against today's value has to be able to say *no*. A release that read
    the build-time verdict, or that simply freed every `held_back` row it found,
    would text somebody four hours after the message they were being held back
    from.
    """
    campaign, _ = _list_campaign(
        db, "still holding",
        [(take(1)[0], None), (take(1)[0], iso_days_ago(1))], window=3)
    try:
        sent = asyncio.run(CampaignService(db).send_campaign(campaign.id))
        verdict = asyncio.run(campaign_topup.assess(db, sent))

        assert verdict["released"] == []
        assert len(verdict["still_held"]) == 1
        assert verdict["refusal"] == campaign_topup.ALL_SUPPRESSED
    finally:
        _clear_window(db)


def test_a_widened_window_does_not_un_send_anybody(db):
    """Raising the window afterwards holds nobody who was already reached.

    The mirror of the test above, and the reason `held_back_rows()` filters on
    the phone's *other* rows rather than on the row in front of it: a contact
    who was sent to is finished with this campaign, and no later change to the
    rule may reopen that.

    The campaign has to have a real hold on it or this proves nothing —
    `releasable()` returns at `if not rows` and every assertion below compares
    two empty lists. The review found the first version of this test doing
    exactly that, which is 5e's own vacuous-in-isolation defect written a second
    time. So one contact is held from the start and the precondition is
    asserted before the window moves.
    """
    reached, held = take(1)[0], take(1)[0]
    campaign, _ = _list_campaign(
        db, "widened", [(reached, None), (held, iso_days_ago(1))], window=3)
    try:
        sent = asyncio.run(CampaignService(db).send_campaign(campaign.id))
        assert sent.sent_count == 1
        assert [row.phone for row in campaign_topup.held_back_rows(db, campaign.id)] \
            == [held], "precondition: there is a hold for the widening to act on"

        suppression_service.set_suppression_days(db, 30)
        verdict = asyncio.run(campaign_topup.assess(db, sent))

        assert verdict["released"] == [], "a widened window releases nobody"
        assert [row.phone for row in verdict["still_held"]] == [held]
        assert reached not in {row.phone for row in verdict["still_held"]}, (
            "somebody this campaign already reached was pulled back into the hold")
        assert _rows(db, campaign.id)[reached][0].status == "sent"
    finally:
        _clear_window(db)


# ─── Only a row with no other history is releasable ─────────────────────────

def test_a_phone_the_campaign_reached_another_way_is_never_released(db):
    """"Whose only row for this campaign is `held_back`" — decision 005.

    Driven with a second row on the same number, which is what a campaign that
    reached somebody and then held a duplicate of them back looks like. The
    exclusion is by phone for the reason the module keys everything else on it:
    a contact deleted and re-imported is a new row and the same handset.
    """
    phones = take(2)
    campaign, _ = _list_campaign(
        db, "already reached", [(phones[0], None), (phones[1], iso_days_ago(1))],
        window=3)
    try:
        sent = asyncio.run(CampaignService(db).send_campaign(campaign.id))
        held = db.query(SMSMessage).filter(
            SMSMessage.campaign_id == campaign.id,
            SMSMessage.status == HELD_BACK_STATUS).one()

        assert [row.phone for row in campaign_topup.held_back_rows(db, campaign.id)] \
            == [held.phone]

        # The same number also carries a delivered row on this campaign.
        db.add(SMSMessage(campaign_id=campaign.id, contact_id=held.contact_id,
                          phone=held.phone, message=held.message, status="sent",
                          sent_at=iso_days_ago(0)))
        db.commit()

        assert campaign_topup.held_back_rows(db, campaign.id) == []
        suppression_service.set_suppression_days(db, 0)
        verdict = asyncio.run(campaign_topup.assess(db, sent))
        assert verdict["released"] == []
    finally:
        _clear_window(db)


def test_two_held_back_rows_for_one_phone_release_as_one_message(db):
    """One handset, one message, whatever the table says.

    Nothing in the product can write a second `held_back` row for one number on
    one campaign today — `already_reached()` sees the first. The guard is there
    because "two rows for one person" is the single outcome this flow exists to
    prevent, and a rule that only holds while a *different* rule holds is one
    refactor from not holding. The state is constructed directly here for the
    same reason: a mutation of that guard has to be reachable by something, or
    the tick beside it is decoration.
    """
    campaign, _ = _list_campaign(
        db, "double hold",
        [(take(1)[0], None), (take(1)[0], iso_days_ago(1))], window=3)
    try:
        sent = asyncio.run(CampaignService(db).send_campaign(campaign.id))
        held = db.query(SMSMessage).filter(
            SMSMessage.campaign_id == campaign.id,
            SMSMessage.status == HELD_BACK_STATUS).one()
        db.add(SMSMessage(campaign_id=campaign.id, contact_id=held.contact_id,
                          phone=held.phone, message=held.message,
                          status=HELD_BACK_STATUS,
                          error_message=held.error_message))
        db.commit()
        assert db.query(SMSMessage).filter(
            SMSMessage.campaign_id == campaign.id,
            SMSMessage.phone == held.phone).count() == 2, "precondition"

        suppression_service.set_suppression_days(db, 0)
        released, _, _ = campaign_topup.releasable(db, sent)
        assert len(released) == 1, (
            f"{len(released)} rows released for one number — that is "
            f"{len(released)} copies of the same message to one handset")

        asyncio.run(campaign_topup.top_up(db, campaign.id))
    finally:
        _clear_window(db)

    db.expire_all()
    statuses = sorted(row.status for row in db.query(SMSMessage).filter(
        SMSMessage.campaign_id == campaign.id, SMSMessage.phone == held.phone))
    assert statuses == [HELD_BACK_STATUS, "sent"], (
        f"the duplicate row was queued too — {statuses}")


def test_a_top_up_that_both_releases_and_holds_back_counts_only_what_it_sent(db):
    """The rail reads `total - added` + `added`, so `added` has to be sendable.

    A top-up can do both at once — release a hold that has cleared and hold back
    a newcomer texted yesterday — and the campaign's `total_recipients` counts
    only the first. A history that counted the newcomer's `held_back` row as a
    recipient would report the *original* send as one person smaller than it
    was, which is the direction nobody sanity-checks. Before this session the
    query could not tell the two rows apart at all.

    The window moves from 30 to 10 rather than to 0, so both halves are live in
    the same run: the contact texted 20 days ago is clear at 10 and the one
    texted yesterday is not.
    """
    fresh, long_ago, recent = take(1)[0], take(1)[0], take(1)[0]
    campaign, list_id = _list_campaign(
        db, "release and hold",
        [(fresh, None), (long_ago, iso_days_ago(20))], window=30)
    try:
        sent = asyncio.run(CampaignService(db).send_campaign(campaign.id))
        assert sent.sent_count == 1 and sent.suppressed_count == 1

        newcomer = _contact(db, recent, last_messaged_at=iso_days_ago(1))
        contact_service.add_to_list(db, list_id, newcomer.id)
        db.commit()

        suppression_service.set_suppression_days(db, 10)
        result = asyncio.run(campaign_topup.top_up(db, campaign.id))
    finally:
        _clear_window(db)

    db.expire_all()
    rows = _rows(db, campaign.id)
    assert rows[long_ago][0].status == "sent", "the 20-day-old hold had cleared"
    assert rows[recent][0].status == HELD_BACK_STATUS, "texted yesterday"
    assert result.sent_count == 2
    assert result.total_recipients == 2
    assert result.suppressed_count == 1, "the newcomer, not the released contact"

    history = campaign_topup.top_up_history(db, [campaign.id])[campaign.id]
    assert [h["recipients"] for h in history] == [1], (
        "the held-back newcomer was counted as a recipient, so the rail reports "
        "the original send as smaller than it was")


# ─── A cap is a number the client chose, and a release must not exceed it ───

def test_a_capped_campaign_releases_nobody(db):
    """"Send to the first 3 as a test" must not become a send to thirteen.

    Found by this session's fresh-context review, reproduced before it was
    fixed. `create_campaign()` applies the cap to the *sendable* set and writes
    a held-back row for **every** suppressed contact, so the held-back rows were
    never capped — pre-5h they were inert, and 5h made all of them sendable on
    one click. At this client's numbers that is a campaign capped at 50 queueing
    six thousand messages.

    It is the same defect CLAUDE.md records from 5e one set along: the release
    guard is correct and the set it filters was wrong. Releasing nothing is the
    pre-5h behaviour, kept deliberately while `decisions/006` is open — not a
    ruling that a capped campaign should never release.
    """
    fresh, texted = take(2), take(4)
    suppression_service.set_suppression_days(db, 3)
    try:
        contact_list = contact_service.get_or_create_list(
            db, f"{NAME_PREFIX}capped hold list")
        for phone in fresh:
            contact_service.add_to_list(
                db, contact_list.id, _contact(db, phone).id)
        for phone in texted:
            contact_service.add_to_list(
                db, contact_list.id,
                _contact(db, phone, last_messaged_at=iso_days_ago(1)).id)
        db.commit()

        campaign = CampaignService(db).create_campaign(
            name=f"{NAME_PREFIX}capped hold", message_template=MESSAGE,
            audience=f"list:{contact_list.id}", cross_category_override=True,
            batch_size=1)
        assert campaign.total_recipients == 1, "precondition: the cap bound"
        assert campaign.suppressed_count == 4, (
            "precondition: the window held four, none of them capped")
        assert campaign.batch_size == 1, "the cap has to be recorded to be honoured"

        sent = asyncio.run(CampaignService(db).send_campaign(campaign.id))
        assert sent.sent_count == 1

        suppression_service.set_suppression_days(db, 0)
        released, _, _ = campaign_topup.releasable(db, sent)
        assert released == [], (
            f"a campaign capped at 1 offered to send to {len(released)} more")

        verdict = asyncio.run(campaign_topup.assess(db, sent))
        # Decision 006: the refusal names the cap and the remedy. Both numbers
        # are asserted from the campaign's own state rather than by matching the
        # sentence, so a refusal that quoted the wrong cap — the easy mistake,
        # since `batch_size` and `total_recipients` are equal in this fixture —
        # fails here rather than reading fine.
        # Literals, not `== capped_campaign_hold(1, 4)`. The equality form
        # compares the code against itself and passes on any sentence the
        # function happens to produce, including one quoting the wrong cap —
        # the easy mistake here, since `batch_size` and `total_recipients` are
        # both 1 in this fixture. The wording itself is `test_hold_wording.py`.
        assert "capped at 1" in verdict["refusal"]
        assert "4 contacts" in verdict["refusal"]
        assert "without a cap" in verdict["refusal"]

        with pytest.raises(CampaignError, match="capped"):
            asyncio.run(campaign_topup.top_up(db, campaign.id))
    finally:
        _clear_window(db)

    db.expire_all()
    assert db.get(Campaign, campaign.id).sent_count == 1, "the cap was defeated anyway"
    assert db.query(SMSMessage).filter(
        SMSMessage.campaign_id == campaign.id,
        SMSMessage.status == HELD_BACK_STATUS).count() == 4


def test_a_held_back_contact_who_left_the_list_is_not_called_held_back(db):
    """A row whose contact is gone is not being held by the window.

    `import_service.undo()` deletes contacts, so this is reachable rather than
    theoretical. Folding those rows into "still held" made the refusal say they
    had been "texted recently and are being held back" — a statement about a
    rule that is not running, which is the shape of lie 5c and 5d exist to
    remove. Driven with the window at 0, where nothing can possibly be held.
    """
    kept, leaving = take(1)[0], take(1)[0]
    campaign, _ = _list_campaign(
        db, "departed", [(kept, None), (leaving, iso_days_ago(1))], window=3)
    try:
        sent = asyncio.run(CampaignService(db).send_campaign(campaign.id))
        gone = db.query(Contact).filter(Contact.phone == leaving).one()
        db.query(SMSMessage).filter(SMSMessage.contact_id == gone.id).update(
            {"contact_id": None}, synchronize_session=False)
        db.delete(gone)
        db.commit()

        suppression_service.set_suppression_days(db, 0)
        released, still_held, departed = campaign_topup.releasable(db, sent)

        assert released == [], "a contact who left the list is not re-texted"
        assert still_held == [], (
            "the window is off, so nothing may be described as held by it")
        assert [row.phone for row in departed] == [leaving]

        verdict = asyncio.run(campaign_topup.assess(db, sent))
        assert verdict["refusal"] != campaign_topup.ALL_SUPPRESSED, (
            "the refusal blamed the hold-back window, which is set to 0")
        assert verdict["refusal"] == campaign_topup.NOTHING_NEW
    finally:
        _clear_window(db)


# ─── Criterion 5: no backfill, and no re-adjudication ───────────────────────

def test_a_pre_existing_skipped_row_is_never_re_adjudicated(db):
    """Rider 2, tested at the place it could be violated.

    A campaign built before 5h wrote its held-back contacts as `skipped`, and
    those rows may equally be region skips. They cannot be classified after the
    fact, so they are never released — not by widening the query "just for the
    suppression ones", not by reading `error_message`. This drives exactly that
    tree: the row is rewritten to `skipped` the way the old builder wrote it,
    hold-back wording included, and the top-up must still not see it.
    """
    campaign, _ = _list_campaign(
        db, "legacy skipped",
        [(take(1)[0], None), (take(1)[0], iso_days_ago(1))], window=3)
    try:
        sent = asyncio.run(CampaignService(db).send_campaign(campaign.id))
        legacy = db.query(SMSMessage).filter(
            SMSMessage.campaign_id == campaign.id,
            SMSMessage.status == HELD_BACK_STATUS).one()
        legacy.status = "skipped"          # exactly what the pre-5h builder wrote
        db.commit()
        legacy_id, legacy_phone = legacy.id, legacy.phone

        suppression_service.set_suppression_days(db, 0)
        assert campaign_topup.held_back_rows(db, campaign.id) == []

        verdict = asyncio.run(campaign_topup.assess(db, sent))
        assert verdict["released"] == []
        assert verdict["refusal"] == campaign_topup.NOTHING_NEW
    finally:
        _clear_window(db)

    db.expire_all()
    after = db.get(SMSMessage, legacy_id)
    assert after.status == "skipped", "a pre-5h row was re-adjudicated"
    assert after.top_up_at is None
    assert after.phone == legacy_phone


# ─── Criterion 4: never billable ────────────────────────────────────────────

def test_a_held_back_row_is_not_counted_in_the_billing_cycle(db):
    """Asserted against the billing query, with a positive control.

    `held_back not in BILLABLE_STATUSES` is a tuple test and would also pass on
    a `compute_usage()` that was broken outright, so the same row is flipped to
    `sent` afterwards and has to appear. `sent_at` is set on both, inside the
    open cycle, so the only thing keeping the first out of the count is its
    status.
    """
    assert HELD_BACK_STATUS not in BILLABLE_STATUSES

    cycle_start, cycle_end, _, _ = billing_service.get_billing_cycle()
    before_count, before_segments = billing_service.compute_usage(
        db, cycle_start, cycle_end)

    campaign, _ = _list_campaign(
        db, "billing", [(take(1)[0], None), (take(1)[0], iso_days_ago(1))],
        window=3)
    try:
        asyncio.run(CampaignService(db).send_campaign(campaign.id))
        held = db.query(SMSMessage).filter(
            SMSMessage.campaign_id == campaign.id,
            SMSMessage.status == HELD_BACK_STATUS).one()
        held.sent_at = f"{cycle_start.isoformat()}T12:00:00"
        held.segments = 7
        db.commit()

        with_held, segments_with_held = billing_service.compute_usage(
            db, cycle_start, cycle_end)
        assert segments_with_held == before_segments + 1, (
            "only the one contact this campaign actually sent to is billable")

        held.status = "sent"               # the positive control
        db.commit()
        _, segments_if_sent = billing_service.compute_usage(db, cycle_start, cycle_end)
        assert segments_if_sent == segments_with_held + 7, (
            "the row is invisible to the billing query for a reason other than "
            "its status, so the assertion above proves nothing")

        held.status = HELD_BACK_STATUS
        db.commit()
        assert billing_service.compute_usage(db, cycle_start, cycle_end)[0] \
            == with_held
    finally:
        _clear_window(db)


# ─── The release is not limited to a list audience ──────────────────────────

def test_a_campaign_with_no_list_of_its_own_can_still_release_a_hold(db):
    """The refusal is about "added since", and a held-back row is not that.

    `NOT_A_LIST_AUDIENCE` exists because "everyone who has joined that audience
    since" is a set nobody chose — last month's message to tonight's buyers. A
    held-back row is the opposite: this campaign resolved that contact, counted
    them, and showed the number on screen before the send. Refusing to release
    them because the audience was `all` would leave decision 005's defect
    standing on every campaign that was not built from an upload.
    """
    phone = take(1)[0]
    contact = _contact(db, phone, last_messaged_at=iso_days_ago(1))
    suppression_service.set_suppression_days(db, 3)
    try:
        campaign = CampaignService(db).create_campaign(
            name=f"{NAME_PREFIX}no list", message_template=MESSAGE,
            audience=f"category:{_tagged(db, contact)}", cross_category_override=True)
        asyncio.run(CampaignService(db).send_campaign(campaign.id))

        held = db.query(SMSMessage).filter(
            SMSMessage.campaign_id == campaign.id, SMSMessage.phone == phone).one()
        assert held.status == HELD_BACK_STATUS

        # Asserted on the candidate rule rather than through `assess()`, because
        # the audience type is exactly what the candidate rule branches on and
        # `assess()` would also have to be told a campaign in this state is
        # complete. Membership, not equality: this category may hold contacts
        # other modules seeded.
        assert phone in {row.phone for row in
                         campaign_topup.held_back_rows(db, campaign.id)}
        suppression_service.set_suppression_days(db, 0)
        released, _, _ = campaign_topup.releasable(db, campaign)
        assert phone in {row.phone for row in released}, (
            "a campaign with no list of its own could not release its own hold")
    finally:
        _clear_window(db)


def _tagged(db, contact) -> str:
    """Tag `contact` into a seeded category and return its slug.

    A category audience rather than `all`: `all` resolves to every contact in
    the shared database, so the campaign would hold back whoever the modules
    before it happened to text and this test would be measuring their history
    instead of its own.
    """
    from app.services import category_service
    category = category_service.get_by_slug(db, "estates")
    category_service.tag_contact(db, contact.id, category.id, source="manual")
    db.commit()
    return category.slug


# ─── The refusals, each on the state it belongs to ──────────────────────────

def test_the_not_a_list_refusal_still_stands_when_there_is_no_hold_to_release(db):
    """The 5e refusal, unchanged, on the state it was written for.

    Moved here from `test_topup_and_zero_send.py`, where the campaign's `all`
    audience swept in whatever the rest of the suite had texted and made which
    refusal comes back depend on the modules that ran first. Constructed
    directly instead: completed, no list, no rows at all.
    """
    campaign = Campaign(name=f"{NAME_PREFIX}bare all", message_template=MESSAGE,
                        audience="all", status="completed",
                        cross_category_override=1, created_at=iso_days_ago(1))
    db.add(campaign)
    db.commit()

    verdict = asyncio.run(campaign_topup.assess(db, campaign))
    assert verdict["refusal"] == campaign_topup.NOT_A_LIST_AUDIENCE
    assert verdict["released"] == [] and verdict["sendable"] == []


def test_the_summary_names_the_two_halves_separately():
    """A released contact is not a new one, and must not be described as one.

    "Top-up sending to 2 new recipients" about people who were on the list from
    the start sends the client looking for an upload he never made. Pure
    function, so the wording is asserted directly rather than through a run that
    happens to hit one branch — the same reason A7's reasons are.
    """
    both = campaign_topup.top_up_summary(3, 2)
    assert "5 contacts" in both and "3" in both and "2" in both
    assert "held back earlier" in both

    only_released = campaign_topup.top_up_summary(0, 1)
    assert "1 contact" in only_released and "held back" in only_released
    assert "added since" not in only_released

    only_added = campaign_topup.top_up_summary(4, 0)
    assert "4 contacts added since" in only_added
    assert "held back" not in only_added
