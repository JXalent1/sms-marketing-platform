"""5e A4 and A7: topping up a sent campaign, and a run that reaches nobody.

  A4  contacts added after the blast get the same message and fold into the
      campaign's totals — and nobody the campaign already reached is texted twice
  A7  a run that put no message on a carrier ends `aborted` with a reason naming
      the cause, instead of `completed` with `sent_count = 0`

The rules this file inherits are in `_campaign_flow_setup.py`: leave no rows
behind, and give back the rate-limit budget you spend.
"""

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.main import app
from app.models.app_setting import AppSetting
from app.models.campaign import Campaign
from app.models.contact import Contact
from app.models.contact_list import ContactListMember
from app.models.sms_message import SMSMessage, BILLABLE_STATUSES
from app.services import (
    blocklist_service, campaign_builder, campaign_outcome, campaign_topup,
    contact_service, import_service, suppression_service,
)
from app.services.campaign_service import CampaignError, CampaignService
from tests._campaign_flow_setup import (
    BLOCKED_PHONE, MESSAGE, NAME_PREFIX, csv_bytes, default_csv, iso_days_ago,
    purged_db_fixture_body, rate_limit_fixture_body, take,
)

PASSWORD = "devpassword123"


@pytest.fixture(scope="module", autouse=True)
def rate_limits():
    yield from rate_limit_fixture_body()


@pytest.fixture(scope="module")
def db():
    yield from purged_db_fixture_body()


@pytest.fixture(scope="module")
def client():
    c = TestClient(app)
    login = c.post("/login", data={"username": "admin", "password": PASSWORD})
    assert login.status_code in (200, 302), (
        f"login failed with {login.status_code} — every assertion below would "
        f"have run against a 401 body and passed by containing nothing"
    )
    return c


def _sent_campaign(db, name, phones):
    """An upload-backed campaign that has actually gone out. The A4 precondition.

    Callers pass numbers from `take()`, never a shared constant: a send stamps
    `last_messaged_at` on every recipient, so a second test reusing them would be
    testing the hold-back window it did not mean to set up.
    """
    campaign, imported = campaign_builder.create_campaign_from_upload(
        db, CampaignService(db).render, name=f"{NAME_PREFIX}{name}",
        message_template=MESSAGE, content=default_csv(phones))
    result = asyncio.run(CampaignService(db).send_campaign(campaign.id))
    assert result.status == "completed", result.abort_reason
    return result, imported


def _clear_window(db):
    """Remove the stored window, leaving the `.env` default in force.

    Not `set_suppression_days(db, 0)`. That stores a row, and a stored 0 is a
    different state from no row at all — `test_suppression_window.py` asserts on
    exactly that distinction. Setting it "back to 0" here leaked a row into every
    module that runs afterwards, and the mutation harness is what found it: an
    unrelated test was failing under almost every mutation, which inflates every
    CAUGHT verdict it appears in.
    """
    db.query(AppSetting).filter(
        AppSetting.key == suppression_service.SUPPRESSION_DAYS_KEY).delete(
        synchronize_session=False)
    db.commit()


def _add_to_list(db, list_id, phone, last_messaged_at=None):
    contact = contact_service.upsert_contact(
        db, phone=phone, full_name="Added Later", source="5e-flow-test")
    contact.last_messaged_at = last_messaged_at
    contact_service.add_to_list(db, list_id, contact.id)
    db.commit()
    return contact


# ─── A4: the top-up ─────────────────────────────────────────────────────────

def test_a_top_up_reaches_only_the_new_contacts(db):
    """Criterion 6, and the assertion the whole feature turns on.

    Two facts, and both are needed: the new contact got the message, and every
    contact from the first run still has exactly one message row. Asserting only
    that the new one was sent would pass just as happily on an implementation
    that re-blasted all four — the campaign's `sent_count` would go up either
    way, and the duplicate would be discovered by the person who received it.
    """
    original, newcomer_phone = take(3), take(1)[0]
    campaign, imported = _sent_campaign(db, "top-up base", original)
    original_sent = campaign.sent_count
    assert original_sent == 3

    newcomer = _add_to_list(db, imported["list_id"], newcomer_phone)
    result = asyncio.run(campaign_topup.top_up(db, campaign.id))

    rows = db.query(SMSMessage).filter(SMSMessage.campaign_id == campaign.id).all()
    per_phone = {}
    for row in rows:
        per_phone.setdefault(row.phone, []).append(row)

    assert len(per_phone[newcomer_phone]) == 1, "the newcomer was texted once"
    assert per_phone[newcomer_phone][0].status == "sent"
    for phone in original:
        assert len(per_phone[phone]) == 1, (
            f"{phone} was in the original send and has {len(per_phone[phone])} rows — "
            f"the top-up re-queued somebody the campaign had already reached")

    # The totals move. That is what "folds into that campaign" means.
    assert result.sent_count == original_sent + 1
    assert result.total_recipients == 4
    assert result.status == "completed"
    assert result.abort_reason is None

    # And the addition stays distinguishable, so a report can say when.
    assert per_phone[newcomer_phone][0].top_up_at is not None
    for phone in original:
        assert per_phone[phone][0].top_up_at is None, (
            "the original send is NULL, not a stamp — it is not a top-up")

    history = campaign_topup.top_up_history(db, [campaign.id])[campaign.id]
    assert [h["recipients"] for h in history] == [1]

    # Cleanup for the tests below, which count rows on this list.
    db.query(ContactListMember).filter(
        ContactListMember.contact_id == newcomer.id).delete(synchronize_session=False)
    db.commit()


def test_the_guard_is_on_the_phone_number_not_the_contact_row(db):
    """A contact deleted and re-imported is the same handset.

    `contact_id` is the obvious key and the wrong one: an undo-then-re-upload
    gives the same person a brand-new row, and a guard keyed on the id would
    happily text them a second copy. This drives exactly that sequence.
    """
    phones = take(2)
    campaign, imported = _sent_campaign(db, "identity", phones)

    # The same number comes back as a brand-new contact row, which is what an
    # undo-then-re-upload produces. The message rows still carry the number.
    stale = db.query(Contact).filter(Contact.phone == phones[0]).one()
    db.query(SMSMessage).filter(SMSMessage.contact_id == stale.id).update(
        {"contact_id": None}, synchronize_session=False)
    db.query(ContactListMember).filter(
        ContactListMember.contact_id == stale.id).delete(synchronize_session=False)
    db.delete(stale)
    db.commit()

    reborn = _add_to_list(db, imported["list_id"], phones[0])
    assert reborn.id != stale.id, "the point of the test is a different row"

    assert phones[0] in campaign_topup.already_reached(db, campaign.id)
    sendable, _ = campaign_topup.new_recipients(db, campaign)
    assert phones[0] not in {c.phone for c in sendable}, (
        "the guard is keyed on contact id, so a re-imported buyer is texted twice")


def test_a_top_up_runs_the_same_pre_flight_and_refuses_before_queueing(db):
    """No shortcuts because the campaign already ran once.

    Driven through the *degraded* provider, which is the refusal that matters:
    a box that cannot reach a carrier must not queue five more rows and mark
    them sent. Asserting the campaign is untouched afterwards is the second half
    — a refusal that had already written rows would leave a completed campaign
    carrying orphan `pending` messages.
    """
    from tests._provider_setup import degraded_provider

    phones, newcomer = take(2), take(1)[0]
    campaign, imported = _sent_campaign(db, "degraded top-up", phones)
    _add_to_list(db, imported["list_id"], newcomer)
    rows_before = db.query(SMSMessage).filter(
        SMSMessage.campaign_id == campaign.id).count()

    with degraded_provider():
        with pytest.raises(CampaignError) as raised:
            asyncio.run(campaign_topup.top_up(db, campaign.id))

    assert "not" in str(raised.value).lower()
    db.expire_all()
    campaign = db.get(Campaign, campaign.id)
    assert campaign.status == "completed", "a refused top-up must not abort the campaign"
    assert campaign.abort_reason is None
    assert db.query(SMSMessage).filter(
        SMSMessage.campaign_id == campaign.id).count() == rows_before, (
        "the refusal wrote rows — it has to happen before anything is queued")


@pytest.mark.parametrize("status", ["draft", "aborted", "running"])
def test_only_a_campaign_that_actually_sent_can_be_topped_up(db, status):
    """A draft is edited, not topped up. An aborted campaign stays aborted.

    The `aborted` case is the one with teeth: nothing in this codebase moves a
    campaign back from that state, and a top-up would be the first thing to do
    it — quietly turning a blast that was refused into one that went out.
    """
    campaign, _ = campaign_builder.create_campaign_from_upload(
        db, CampaignService(db).render, name=f"{NAME_PREFIX}state {status}",
        message_template=MESSAGE, content=default_csv(take(2)))
    campaign.status = status
    db.commit()

    with pytest.raises(CampaignError) as raised:
        asyncio.run(campaign_topup.top_up(db, campaign.id))
    # The exact sentence for this state, not a substring that would pass on a
    # generic "no". Each state has its own answer and its own next action.
    assert str(raised.value) == campaign_topup.TOP_UP_STATE_ERRORS[status]

    db.expire_all()
    assert db.get(Campaign, campaign.id).status == status, "the state was changed anyway"


def test_a_top_up_with_nobody_new_refuses_rather_than_running(db, client):
    """Criterion 6's quiet half: the endpoint answers, synchronously, with why.

    A 409 and a sentence, not a 200 and a background task that finds nothing.
    """
    campaign, _ = _sent_campaign(db, "nobody new", take(2))
    response = client.post(f"/api/campaigns/{campaign.id}/top-up")
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]

    # The sentence is about the *list*, not about the people. "Everyone here has
    # already been through this campaign" was the earlier wording and it is false
    # whenever some of them were held back and never texted at all — which is the
    # state he is most likely to be in when he reaches for this button.
    assert "Nobody has been added to this campaign's list" in detail
    assert "already been through" not in detail


def test_a_top_up_holds_back_a_recently_texted_newcomer(db):
    """Suppression applies to a top-up exactly as it does to a first send.

    The spec is explicit that a top-up runs the same filters, and this is the one
    that is easy to lose: the newcomer is new *to this campaign* and may still
    have been texted by another one yesterday.
    """
    suppression_service.set_suppression_days(db, 3)
    try:
        phones, newcomer = take(2), take(1)[0]
        campaign, imported = _sent_campaign(db, "suppressed top-up", phones)
        _add_to_list(db, imported["list_id"], newcomer,
                     last_messaged_at=iso_days_ago(1))

        with pytest.raises(CampaignError, match="held back"):
            asyncio.run(campaign_topup.top_up(db, campaign.id))
    finally:
        _clear_window(db)


# ─── A7: a run that reached nobody ──────────────────────────────────────────

def test_a_fully_suppressed_campaign_aborts_with_a_reason(db):
    """Criterion 9. Two campaigns shipped `completed` with sent_count 0.

    The campaign rail is the entire UI and shows a reason only when
    `abort_reason` is set, so a blast that reached nobody read exactly like one
    that worked. Both halves are asserted — the status *and* the reason — because
    an abort with a null reason renders as a bare badge and is the same silence
    one step along.
    """
    suppression_service.set_suppression_days(db, 30)
    try:
        # Everyone in the file was texted yesterday, so nobody is sendable.
        phones = take(2)
        for phone in phones:
            contact = contact_service.upsert_contact(
                db, phone=phone, full_name="Texted Yesterday", source="5e-flow-test")
            contact.last_messaged_at = iso_days_ago(1)
        db.commit()

        campaign, _ = campaign_builder.create_campaign_from_upload(
            db, CampaignService(db).render, name=f"{NAME_PREFIX}all suppressed",
            message_template=MESSAGE, content=default_csv(phones))
        assert campaign.total_recipients == 0
        assert campaign.suppressed_count == 2

        result = asyncio.run(CampaignService(db).send_campaign(campaign.id))
    finally:
        _clear_window(db)

    assert result.status == "aborted", "a campaign that reached nobody is not completed"
    assert result.sent_count == 0
    assert result.abort_reason, "aborted with no reason renders as a bare badge"
    assert "held back" in result.abort_reason
    assert "Nothing was sent." in result.abort_reason


def _all_blocked_campaign(db, name):
    """A campaign whose entire audience is on the blocklist, sent.

    A helper rather than a chain between two tests: the acceptance script runs
    each criterion's tests on their own, so a test that reads a campaign an
    earlier test created passes in a full run and fails in the run that matters.
    """
    phone = take(1)[0]
    blocklist_service.block_number(db, phone, reason="stop_keyword", source="manual")
    contact = contact_service.upsert_contact(
        db, phone=phone, full_name="Opted Out", source="5e-flow-test")
    contact_list = contact_service.get_or_create_list(db, f"{NAME_PREFIX}{name} list")
    contact_service.add_to_list(db, contact_list.id, contact.id)
    db.commit()

    campaign = CampaignService(db).create_campaign(
        name=f"{NAME_PREFIX}{name}", message_template=MESSAGE,
        audience=f"list:{contact_list.id}", cross_category_override=True)
    return asyncio.run(CampaignService(db).send_campaign(campaign.id))


def test_a_fully_blocklisted_campaign_aborts_naming_the_opt_out_list(db):
    """The other way to reach nobody, and it needs a different sentence.

    "Everyone was held back" and "everyone has opted out" call for different
    actions — one clears on its own, the other never does. A single generic
    reason would send him to Settings to widen a window that was not the problem.
    """
    result = _all_blocked_campaign(db, "all blocked")

    assert result.status == "aborted"
    assert result.sent_count == 0
    assert "opt-out list" in result.abort_reason
    assert "Nothing was sent." in result.abort_reason


def test_nothing_a_zero_send_campaign_wrote_is_billable(db):
    """The reason has to be true as well as loud.

    A campaign that aborts having reached nobody must not have a billable row on
    it. Asserted against the status set rather than by re-deriving it, so a
    future status added to the send loop cannot slip past this.
    """
    campaign = _all_blocked_campaign(db, "all blocked, billing")
    statuses = {m.status for m in db.query(SMSMessage).filter(
        SMSMessage.campaign_id == campaign.id)}
    assert statuses, "the campaign wrote no rows at all — the test proves nothing"
    assert not (statuses & set(BILLABLE_STATUSES))


def test_a_dry_run_that_reaches_people_is_still_a_success(db):
    """"Do not special-case a deliberate dry run into looking like a failure."

    The whole suite runs on the console provider, so every passing send above is
    already evidence — this states it as an assertion so a future A7 that keyed
    off send mode rather than off what the run did would go red here.
    """
    from app.sms.factory import send_mode

    assert send_mode().key == "dry_run", "conftest forces the console provider"
    campaign, _ = _sent_campaign(db, "dry run is fine", take(2))
    assert campaign.status == "completed"
    assert campaign.abort_reason is None


def test_a_top_up_that_reaches_nobody_does_not_relabel_the_campaign(db):
    """The one exception, and why it is not a softening of A7.

    A campaign that reached 1,200 people did complete; a later top-up finding
    nothing cannot revoke that — the same argument that stopped a late failure
    webhook from un-delivering a message in 5d. What must not happen is silence,
    so the reason is still recorded and still says which run it is about.

    Driven by blocklisting the newcomer *after* the top-up is admitted, which is
    the real race: the number was sendable when the audience was resolved.
    """
    campaign, imported = _sent_campaign(db, "top-up reaches nobody", take(2))
    _add_to_list(db, imported["list_id"], BLOCKED_PHONE)
    blocklist_service.block_number(db, BLOCKED_PHONE, reason="stop_keyword",
                                   source="manual")

    result = asyncio.run(campaign_topup.top_up(db, campaign.id))

    assert result.status == "completed", (
        "a campaign that reached 2 people must not be relabelled aborted by a "
        "top-up that reached 0")
    assert result.sent_count == 2, "the original send's count is intact"
    assert result.abort_reason, "silence is the defect A7 exists to remove"
    assert result.abort_reason.startswith("Top-up of 1 recipient:"), (
        f"the sentence has to say which run it is about, because the badge "
        f"beside it says completed — got {result.abort_reason!r}")
    assert "opt-out list" in result.abort_reason

    # ── and a later top-up that works clears it ────────────────────────────
    # The second half of the same story, kept in one test rather than chained
    # into the next: a warning left standing under a run that worked is its own
    # small lie, and a separate test reading this one's campaign would pass in a
    # full run and fail whenever it is run alone.
    _add_to_list(db, imported["list_id"], take(1)[0])
    recovered = asyncio.run(campaign_topup.top_up(db, campaign.id))

    assert recovered.sent_count == 3
    assert recovered.abort_reason is None


# ─── The wording, on its own ────────────────────────────────────────────────

def test_every_zero_send_reason_names_a_cause_and_says_nothing_was_sent():
    """A pure-function sweep over the four causes.

    The reasons are the entire user-visible output of A7 — there is no detail
    screen — so they are asserted directly rather than only through a campaign
    that happens to hit one branch. Each has to name its own cause: a reader
    seeing "Nothing was sent" and no reason is back where session 5d started.
    """
    cases = [
        (dict(queued=0, suppressed=5), "held back"),
        (dict(queued=0), "resolved to nobody"),
        (dict(queued=4, blocked=4), "opt-out list"),
        (dict(queued=4, region_skipped=4), "outside the regions"),
        (dict(queued=4, failed=4), "rejected before delivery"),
        (dict(queued=4, blocked=2, failed=1), "Nobody received this campaign"),
    ]
    for kwargs, expected in cases:
        reason = campaign_outcome.zero_send_reason(**kwargs)
        assert expected in reason, f"{kwargs} -> {reason!r}"
        assert reason.endswith("Nothing was sent.") or "Nothing was sent." in reason, (
            f"{kwargs} -> {reason!r}")


def test_a_mixed_breakdown_accounts_for_every_recipient():
    """A breakdown that does not add up invites "so the rest went out, then".

    That is the one reading this sentence exists to deny, which is why the
    remainder is named rather than dropped.
    """
    reason = campaign_outcome.zero_send_reason(queued=10, blocked=2,
                                               region_skipped=1, failed=3)
    assert "2 on the opt-out list" in reason
    assert "1 outside the sending region" in reason
    assert "3 rejected before delivery" in reason
    assert "4 unaccounted for" in reason


# ─── What the fresh-context review found ────────────────────────────────────
#
# Three defects in the top-up's *scope*. None was caught by the tests above,
# because every one of them asked "does the guard stop a re-send?" and the answer
# was yes — while the candidate set the guard was filtering had the wrong people
# in it to begin with. A guard on the wrong set is not a guard.

def test_a_top_up_does_not_defeat_the_campaigns_cap(db):
    """`batch_size` deliberately withheld the rest of the list.

    The cap is the "send to the first few as a test" control. It is applied at
    build time and not persisted, so under a rule of "everyone the audience
    resolves to now, minus everyone with a message row" the withheld remainder is
    indistinguishable from people added since — and one click on Top up delivers
    to all of them, which is the opposite of what the cap was for.

    "Added since" is read from `contact_list_members.added_at` instead, so the
    remainder is correctly not new.
    """
    phones = take(6)
    campaign, imported = campaign_builder.create_campaign_from_upload(
        db, CampaignService(db).render, name=f"{NAME_PREFIX}capped",
        message_template=MESSAGE, content=default_csv(phones), batch_size=2)
    assert campaign.total_recipients == 2, "precondition: the cap applied"

    result = asyncio.run(CampaignService(db).send_campaign(campaign.id))
    assert result.sent_count == 2

    verdict = asyncio.run(campaign_topup.assess(db, result))
    assert verdict["refusal"], (
        f"the top-up offered to send to {len(verdict['sendable'])} people the cap "
        f"held back")
    assert "Nobody has been added" in verdict["refusal"]


def test_a_top_up_refuses_an_audience_that_is_not_its_own_list(db):
    """A campaign on `all` or `category:` has no list to be added to.

    Under the old rule every contact imported afterwards — for a different
    auction, on a different day — resolved as "added since" and would have
    received last month's message. There is no list here, so there is no honest
    answer to "who was added"; the refusal says so rather than guessing one.

    **The property is asserted, not the sentence** (changed in 5h). This
    campaign's audience is `all`, so it resolves to every contact in the shared
    test database — and whichever of those the hold-back window happened to
    catch when the draft was built now has a `held_back` row on it. That is a
    genuine second reason to refuse, with its own correct sentence, and which of
    the two comes back depends on what the modules that ran earlier texted. The
    thing this test exists to prove is that nobody imported for a *different*
    auction is a candidate, and that is what it now asserts. A regression fails
    it either way: a top-up offering the other auction's contacts has no
    refusal at all.
    """
    phones = take(2)
    contact_list = contact_service.get_or_create_list(db, f"{NAME_PREFIX}all-audience")
    for phone in phones:
        contact = contact_service.upsert_contact(
            db, phone=phone, full_name="Everyone", source="5e-flow-test")
        contact_service.add_to_list(db, contact_list.id, contact.id)
    db.commit()

    campaign = CampaignService(db).create_campaign(
        name=f"{NAME_PREFIX}audience all", message_template=MESSAGE,
        audience="all", cross_category_override=True)
    result = asyncio.run(CampaignService(db).send_campaign(campaign.id))
    assert result.status == "completed" and result.sent_count > 0

    # Somebody imports tonight's completely unrelated auction list.
    other_auction = take(3)
    campaign_builder.create_campaign_from_upload(
        db, CampaignService(db).render, name=f"{NAME_PREFIX}different auction",
        message_template=MESSAGE, content=default_csv(other_auction))

    verdict = asyncio.run(campaign_topup.assess(db, result))
    assert verdict["refusal"] in (campaign_topup.NOT_A_LIST_AUDIENCE,
                                 campaign_topup.ALL_SUPPRESSED), verdict["refusal"]
    assert verdict["sendable"] == [], (
        f"a top-up on an 'all' campaign offered to text "
        f"{len(verdict['sendable'])} people imported for another auction")
    reached = {c.phone for c in verdict["sendable"]} | {
        row.phone for row in verdict["released"]}
    assert not (reached & set(other_auction)), (
        "tonight's import reached last month's campaign through the release path")


def test_an_intersection_audience_is_not_treated_as_a_list(db):
    """`category:x&list:12` went to *part* of that list.

    "Added to the list since" and "added to the list since and in that category"
    are different sets, and sending to the difference reaches people the original
    campaign's own selector filtered out.
    """
    class _Campaign:
        audience = "category:food_service&list:12"

    assert campaign_topup.campaign_list_id(_Campaign()) is None
    assert campaign_topup.campaign_list_id(type("C", (), {"audience": "list:12"})()) == 12
    assert campaign_topup.campaign_list_id(type("C", (), {"audience": "all"})()) is None


def test_added_since_reads_one_clock_and_both_timestamp_spellings(db):
    """`added_at` carries two formats, and used to carry two time zones.

    `import_service` writes `datetime.now().isoformat()`; the column's server
    default is SQLite's `CURRENT_TIMESTAMP`, which uses a space instead of a `T`
    **and is UTC**. Comparing those against a local `campaigns.created_at`
    lexicographically is wrong in one direction always, and comparing UTC against
    local makes every defaulted row look up to five hours newer than it is — so
    somebody added shortly *before* a campaign reads as added since.

    `add_to_list()` now stamps it explicitly, and the comparison parses rather
    than compares strings. This asserts the property that matters: a contact put
    on the list before the campaign is never a top-up candidate.
    """
    phones = take(3)
    contact_list = contact_service.get_or_create_list(db, f"{NAME_PREFIX}clock list")
    early = contact_service.upsert_contact(
        db, phone=phones[0], full_name="Added Before", source="5e-flow-test")
    contact_service.add_to_list(db, contact_list.id, early.id)
    db.commit()

    stamp = db.query(ContactListMember).filter(
        ContactListMember.contact_id == early.id).one().added_at
    assert stamp and "T" in stamp, (
        f"add_to_list fell back to the server default ({stamp!r}) — that is the "
        f"UTC clock, and the whole point is that this column has one")

    campaign = CampaignService(db).create_campaign(
        name=f"{NAME_PREFIX}clock", message_template=MESSAGE,
        audience=f"list:{contact_list.id}", cross_category_override=True)
    result = asyncio.run(CampaignService(db).send_campaign(campaign.id))

    later = contact_service.upsert_contact(
        db, phone=phones[1], full_name="Added After", source="5e-flow-test")
    contact_service.add_to_list(db, contact_list.id, later.id)
    db.commit()

    sendable, _ = campaign_topup.new_recipients(db, result)
    assert {c.phone for c in sendable} == {phones[1]}, (
        "only the contact added after the campaign is a top-up candidate")
