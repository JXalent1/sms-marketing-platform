"""Decision 006: what the client is told when a hold stops a send.

006 ruled option 1 for both cases it was asked about — a capped campaign
releases nobody, and a campaign the window held entirely stays `aborted` — and
made a wording fix mandatory in the same breath, because the defect it found was
in the sentence rather than in the state:

> "This campaign was stopped before it sent and cannot be topped up. Create a
> new campaign for these contacts."
>
> That is true, and it is useless. It does not say *why* nothing sent, and it
> does not say *when* the client could try again. He is left thinking the tool
> broke — which is exactly what happened to Jordan for two consecutive campaigns
> before anyone ran SQL against it.

So the two sentences are the deliverable, and they are tested as such:

  - the abort reason names the cause, the window, and the clearing time, and the
    clearing time is `max(last_messaged_at)` across the held set plus the window
    — the *latest*, not the earliest, which is A6's own rule and the difference
    between "you can send at 10am" and "most of them are still held at 10am".
  - it says the same thing about the same moment as the composer's checklist row
    one screen earlier. Two sentences about one rule that disagree are worse than
    one sentence, and 006 asks for A6's phrasing by name.
  - `capped_campaign_hold()` names the cap and the remedy.

The rules this file inherits are in `_campaign_flow_setup.py`: leave no rows
behind, and give back the rate-limit budget you spend.
"""

import asyncio
from datetime import datetime, timedelta

import pytest

from app.core.database import SessionLocal
from app.models.app_setting import AppSetting
from app.models.contact import Contact
from app.models.sms_message import SMSMessage, HELD_BACK_STATUS
from app.services import (
    campaign_outcome, campaign_release, campaign_topup, contact_service,
    preflight_service, suppression_service,
)
from app.services.campaign_service import CampaignService
from tests._campaign_flow_setup import (
    MESSAGE, NAME_PREFIX, iso_days_ago, purge, rate_limit_fixture_body, take,
)


@pytest.fixture(scope="module", autouse=True)
def rate_limits():
    yield from rate_limit_fixture_body()


@pytest.fixture(scope="module")
def db():
    session = SessionLocal()

    def clean():
        purge(session)
        session.query(AppSetting).filter(
            AppSetting.key == suppression_service.SUPPRESSION_DAYS_KEY).delete(
            synchronize_session=False)
        session.commit()

    try:
        clean()
        yield session
        clean()
    finally:
        session.close()


def _clear_window(db):
    """Remove the stored window, leaving the `.env` default in force.

    Not `set_suppression_days(db, 0)` — a stored 0 and no row at all are
    different states, and leaking a stored row into the modules that run
    afterwards is what inflated every mutation verdict in 5e.
    """
    db.query(AppSetting).filter(
        AppSetting.key == suppression_service.SUPPRESSION_DAYS_KEY).delete(
        synchronize_session=False)
    db.commit()


def _fully_held_campaign(db, name, histories, *, window):
    """A campaign whose entire audience is inside the window, sent.

    `histories` is a list of `last_messaged_at` values. Every contact carries
    one, so `total_recipients` comes out 0 and the run takes
    `zero_send_reason()`'s suppressed branch — the state decision 006 is about.
    """
    suppression_service.set_suppression_days(db, window)
    contact_list = contact_service.get_or_create_list(db, f"{NAME_PREFIX}{name} list")
    phones = take(len(histories))
    for phone, last in zip(phones, histories):
        contact = contact_service.upsert_contact(
            db, phone=phone, full_name="Texted Already", source="5e-flow-test")
        contact.last_messaged_at = last
        contact_service.add_to_list(db, contact_list.id, contact.id)
    db.commit()

    campaign = CampaignService(db).create_campaign(
        name=f"{NAME_PREFIX}{name}", message_template=MESSAGE,
        audience=f"list:{contact_list.id}", cross_category_override=True)
    assert campaign.total_recipients == 0, "precondition: the window took everyone"
    assert campaign.suppressed_count == len(histories)
    return asyncio.run(CampaignService(db).send_campaign(campaign.id)), phones


def _quoted_clock(sentence):
    """The clock out of "… The hold clears at 10:11am on 3 Sep. …", or None.

    Extracted rather than reconstructed, so the two sentences are compared as
    the client reads them instead of as this test believes they are built.
    """
    marker = "The hold clears at "
    if marker not in sentence:
        return None
    return sentence.split(marker, 1)[1].split(".")[0]


# ─── The abort reason ───────────────────────────────────────────────────────

def test_the_abort_reason_names_the_window_and_when_it_clears(db):
    """006's required sentence, driven through the real send path.

    Through `send_campaign()` rather than by calling `zero_send_reason()` with
    hand-made arguments, because the wiring is half of what is being asserted:
    the days and the clearing time are read in `run_send_loop()` and a build
    that stopped passing them would still produce a perfectly good sentence with
    both clauses missing. 5e's `M18` was exactly that shape — a mutation
    invisible to a test that called the function directly.
    """
    result, _ = _fully_held_campaign(
        db, "held everyone", [iso_days_ago(2), iso_days_ago(1)], window=3)
    try:
        assert result.status == "aborted"
        assert result.sent_count == 0
        reason = result.abort_reason
        assert reason, "aborted with no reason renders as a bare badge"

        # The cause, in his terms and with his number.
        assert "All 2 contacts were texted in the last 3 days" in reason, reason
        assert "held back" in reason
        assert "Nothing was sent." in reason

        # The actionable half.
        expected = suppression_service.clears_at_clock(
            (datetime.now() - timedelta(days=1) + timedelta(days=3)).isoformat())
        assert f"The hold clears at {expected}." in reason, reason

        # The remedy that works. "Lower the window and send again" cannot: the
        # hold is frozen into rows at build time and nothing moves a campaign
        # back from `aborted`.
        assert "Create the campaign again after that" in reason
        assert "cannot be restarted" in reason
    finally:
        _clear_window(db)


def test_the_clearing_time_is_the_latest_held_contact_not_the_earliest(db):
    """A6's rule, and the one that decides whether the answer is usable.

    The question the sentence answers is "when can I send this to all of them",
    so it is the *last* hold to lift. The earliest would name a time at which
    most of the audience is still held — which reads as a promise and is a
    trap. Two contacts three days apart make the two answers different days, so
    the rendered clock carries a date and the wrong one cannot pass by accident.
    """
    result, _ = _fully_held_campaign(
        db, "latest not earliest", [iso_days_ago(5), iso_days_ago(2)], window=7)
    try:
        reason = result.abort_reason
        latest = suppression_service.clears_at_clock(
            (datetime.now() - timedelta(days=2) + timedelta(days=7)).isoformat())
        earliest = suppression_service.clears_at_clock(
            (datetime.now() - timedelta(days=5) + timedelta(days=7)).isoformat())
        assert latest != earliest, "the fixture has to make the two distinguishable"

        assert _quoted_clock(reason) == latest, reason
        assert earliest not in reason, (
            "the sentence names the first hold to lift, so most of the audience "
            "is still held at the time it promises")
    finally:
        _clear_window(db)


def test_the_abort_reason_and_the_composer_row_quote_one_moment(db):
    """006 asks for A6's phrasing by name, and this is what that has to mean.

    The checklist row is what he read before pressing send; the abort reason is
    what he reads after. They describe the same rule acting on the same people,
    and if they render the same instant differently — one rounding, one with a
    date, one an hour out — the tool looks broken in precisely the way 006 is
    trying to stop. Same computed timestamp, same renderer, asserted as the two
    strings a human compares.
    """
    result, phones = _fully_held_campaign(
        db, "one moment", [iso_days_ago(4), iso_days_ago(1)], window=5)
    try:
        contacts = db.query(Contact).filter(Contact.phone.in_(phones)).all()
        row_clears_at = suppression_service.suppression_clears_at(contacts, 5)
        row = preflight_service.check_recent_overlap(5, len(contacts), 0,
                                                     row_clears_at)

        assert row_clears_at is not None, "A6 has nothing to say, so this proves nothing"
        assert _quoted_clock(row["reason"]) is not None
        assert _quoted_clock(row["reason"]) == _quoted_clock(result.abort_reason), (
            f"the composer says {_quoted_clock(row['reason'])!r} and the abort "
            f"reason says {_quoted_clock(result.abort_reason)!r} about the same hold")

        # And the same instant underneath the two strings, not merely the same
        # rendering of two instants that happen to fall in one minute.
        assert campaign_release.hold_clears_at(db, result.id) == row_clears_at
    finally:
        _clear_window(db)


def test_an_unknown_clearing_time_is_omitted_rather_than_invented(db):
    """A wrong clearing time is worse than none — he plans an auction on it.

    Driven with a `last_messaged_at` that will not parse, which is what a
    hand-edited row or a half-finished import leaves behind. It still sorts
    after the cutoff, so the contact is still held; `suppression_clears_at()`
    returns None, and the sentence has to lose one clause and change its remedy
    rather than print `None` or guess a time.
    """
    result, _ = _fully_held_campaign(db, "unparseable", ["not-a-timestamp"],
                                     window=3)
    try:
        reason = result.abort_reason
        assert "The hold clears at" not in reason, reason
        assert "None" not in reason
        # The cause is still named, and the remedy still works.
        assert "The one contact in this audience was texted in the last 3 days" \
            in reason, reason
        assert "Create the campaign again once the hold clears" in reason
        assert "Nothing was sent." in reason
    finally:
        _clear_window(db)


# ─── When the hold has already lifted ───────────────────────────────────────

def test_a_hold_that_has_already_lifted_does_not_tell_him_to_wait(db):
    """Found by review, reproduced here: the two clocks are not the same clock.

    The held rows are frozen when the *draft* is built and the run can happen
    days later — a draft saved on Monday and sent on Friday, or a scheduled
    send. The campaign still has nothing pending, so it still aborts, but the
    people it held are now perfectly reachable. The first version of this
    sentence printed a clearing time three days in the **past**, in the present
    tense, and told the client to wait for it.

    Driven by building the draft with the window in force and moving the
    contacts' `last_messaged_at` back before the send, which is the same
    arithmetic as the clock moving forward and does not need a fake clock.
    """
    suppression_service.set_suppression_days(db, 3)
    try:
        contact_list = contact_service.get_or_create_list(
            db, f"{NAME_PREFIX}stale hold list")
        phone = take(1)[0]
        contact = contact_service.upsert_contact(
            db, phone=phone, full_name="Texted Already", source="5e-flow-test")
        contact.last_messaged_at = iso_days_ago(1)
        contact_service.add_to_list(db, contact_list.id, contact.id)
        db.commit()

        campaign = CampaignService(db).create_campaign(
            name=f"{NAME_PREFIX}stale hold", message_template=MESSAGE,
            audience=f"list:{contact_list.id}", cross_category_override=True)
        assert campaign.total_recipients == 0, "precondition: the hold applied"

        # Four days pass. The row is still `held_back` — nothing re-adjudicates
        # it — but the contact is now outside the window.
        contact.last_messaged_at = iso_days_ago(5)
        db.commit()

        result = asyncio.run(CampaignService(db).send_campaign(campaign.id))
        reason = result.abort_reason

        assert "That hold has since cleared." in reason, reason
        assert "Create the campaign again now" in reason
        assert "The hold clears at" not in reason, (
            "a clearing time in the past, in the present tense — he waits for a "
            "moment that has gone")
    finally:
        _clear_window(db)


def test_a_window_switched_off_after_the_build_says_the_hold_is_over(db):
    """The same defect reached from the Settings screen instead of the calendar.

    He builds with the window at 3, sees "everyone held back", and turns the
    window off — which is production's own value and the obvious reaction. The
    campaign still cannot send, because the rows were frozen. Telling him to
    wait "once the hold clears" names a hold that no longer exists and can never
    clear again.

    This is also the test whose earlier version asserted the branch was
    *unreachable* through the send path. It is reachable; the premise was
    checked against the mechanism only after review, which is the failure
    CLAUDE.md names by name.
    """
    result, _ = _fully_held_campaign(db, "window off", [iso_days_ago(1)],
                                     window=3)
    assert "The hold clears at" in result.abort_reason, "precondition"

    # A second campaign on the same shape, sent with the window at 0.
    suppression_service.set_suppression_days(db, 3)
    try:
        contact_list = contact_service.get_or_create_list(
            db, f"{NAME_PREFIX}window off 2 list")
        contact = contact_service.upsert_contact(
            db, phone=take(1)[0], full_name="Texted Already",
            source="5e-flow-test")
        contact.last_messaged_at = iso_days_ago(1)
        contact_service.add_to_list(db, contact_list.id, contact.id)
        db.commit()
        campaign = CampaignService(db).create_campaign(
            name=f"{NAME_PREFIX}window off 2", message_template=MESSAGE,
            audience=f"list:{contact_list.id}", cross_category_override=True)
        assert campaign.total_recipients == 0

        suppression_service.set_suppression_days(db, 0)
        second = asyncio.run(CampaignService(db).send_campaign(campaign.id))
    finally:
        _clear_window(db)

    assert "0 day" not in second.abort_reason, second.abort_reason
    assert "texted recently" in second.abort_reason
    assert "That hold has since cleared." in second.abort_reason
    assert "Create the campaign again now" in second.abort_reason
    assert "once the hold clears" not in second.abort_reason, (
        "the window is off, so there is no hold left to clear")


def test_the_reason_never_quotes_a_window_of_zero_days():
    """"texted in the last 0 days" is a fact about the audience, not the rule.

    A6 made the same call on the checklist row and for the same reason. Asserted
    on the function as well as through the send path above, because `None` — a
    caller that did not pass the window at all — is a different input from 0 and
    has a different remedy: unknown means "wait", off means "go".
    """
    unknown = campaign_outcome.zero_send_reason(queued=0, suppressed=4,
                                                suppression_days=None)
    assert "0 day" not in unknown and "texted recently" in unknown
    assert "once the hold clears" in unknown

    off = campaign_outcome.zero_send_reason(queued=0, suppressed=4,
                                            suppression_days=0)
    assert "0 day" not in off and "texted recently" in off
    assert "Create the campaign again now" in off
    for reason in (unknown, off):
        assert "Nothing was sent." in reason


def test_one_held_contact_is_a_grammatical_sentence():
    """Both halves of the count, which is what the first version got wrong.

    It read "all 1 contacts were texted in the last 1 day" — the day
    singularised and the contact not — and the test named for plurals asserted
    the broken half, which is worse than not testing it. A 6,857-person list can
    genuinely hold one person back.
    """
    one = campaign_outcome.zero_send_reason(queued=0, suppressed=1,
                                            suppression_days=1)
    assert "The one contact in this audience was texted in the last 1 day" in one, one
    assert "1 contacts" not in one and "1 days" not in one

    many = campaign_outcome.zero_send_reason(queued=0, suppressed=2140,
                                             suppression_days=3)
    assert "All 2,140 contacts were texted in the last 3 days" in many, many


# ─── The lookups must not be able to strand the campaign ────────────────────

@pytest.mark.parametrize("target", ["hold_clears_at", "suppression_days"])
def test_a_failed_hold_lookup_loses_a_clause_not_the_campaign(db, target):
    """Decision 006 put two queries where there had been pure arithmetic.

    They run between the send loop finishing and the campaign's final status
    being written. An exception there leaves the campaign **`running`** for ever
    — the one state the rail cannot explain, and nothing in this codebase moves
    a campaign out of it. That is strictly worse than a sentence missing a
    clause, which is why `hold_facts()` answers `{}` instead of raising.

    Both halves are driven, and the second is the one that matters:
    `suppression_days()` reads a settings row of its own and sits *outside*
    `hold_clears_at()`'s own guard, so guarding only the half that looked
    expensive would have left the cheaper half able to strand a campaign. Found
    by probing the guard rather than by reading it.
    """
    suppression_service.set_suppression_days(db, 3)
    try:
        contact_list = contact_service.get_or_create_list(
            db, f"{NAME_PREFIX}strand {target} list")
        contact = contact_service.upsert_contact(
            db, phone=take(1)[0], full_name="Texted Already", source="5e-flow-test")
        contact.last_messaged_at = iso_days_ago(1)
        contact_service.add_to_list(db, contact_list.id, contact.id)
        db.commit()
        campaign = CampaignService(db).create_campaign(
            name=f"{NAME_PREFIX}strand {target}", message_template=MESSAGE,
            audience=f"list:{contact_list.id}", cross_category_override=True)

        module = (campaign_release if target == "hold_clears_at"
                  else suppression_service)
        original = getattr(module, target)

        def boom(*args, **kwargs):
            raise RuntimeError("the database went away mid-adjudication")

        setattr(module, target, boom)
        try:
            result = asyncio.run(CampaignService(db).send_campaign(campaign.id))
        finally:
            setattr(module, target, original)
    finally:
        _clear_window(db)

    assert result.status == "aborted", (
        "the campaign is stuck reporting itself as still sending")
    assert result.completed_at, "and it never finished"
    assert result.abort_reason, "a bare badge is the silence A7 exists to remove"
    # Degraded, not silent: the cause and the remedy survive, the two clauses
    # that needed the database do not.
    assert "held back" in result.abort_reason
    assert "Nothing was sent." in result.abort_reason
    assert "The hold clears at" not in result.abort_reason


# ─── The capped-campaign refusal ────────────────────────────────────────────

def test_the_capped_refusal_names_the_cap_the_count_and_a_remedy_that_works():
    """006: "refusing without explaining is how a correct guard reads as a
    broken tool".

    Four facts, and the fourth was a review finding. The cap, because it is the
    thing he typed and the reason this is happening; how many are still held,
    because 6,806 and 6 call for different next moves; what to do; and **when
    that will work**, because "create a new campaign to reach them" reaches
    nobody at all while the window is still holding them — the new campaign
    holds them too. That is the same shape of unusable remedy 006 rejected for
    the abort reason next door.
    """
    from datetime import datetime, timedelta
    ahead = (datetime.now() + timedelta(days=2)).isoformat()

    many = campaign_topup.capped_campaign_hold(50, 6806, ahead)
    assert "capped at 50" in many
    assert "6,806 contacts" in many and "are still on it" in many
    assert "Create a new campaign without a cap" in many
    assert "holds them again" in many, (
        "he rebuilds it tonight and the window holds the same 6,806 people")
    assert f"clears at {suppression_service.clears_at_clock(ahead)}" in many

    one = campaign_topup.capped_campaign_hold(1, 1)
    assert "capped at 1" in one
    assert "1 contact the hold-back window held back is still on it" in one
    assert "1 contacts" not in one

    # No clearing time when there is none to give — never invented, and the
    # remedy still stands on its own.
    assert "clears at" not in one
    assert "Create a new campaign without a cap" in one


def test_the_capped_refusal_carries_the_clearing_time_end_to_end(db):
    """The wiring, not just the sentence.

    `capped_campaign_hold()` will happily produce a correct paragraph with the
    time omitted, so a build that stopped passing it would look fine here and
    read as a broken tool on the box. Driven through `assess()`.
    """
    suppression_service.set_suppression_days(db, 3)
    try:
        contact_list = contact_service.get_or_create_list(
            db, f"{NAME_PREFIX}capped wording list")
        for phone, last in zip(take(3), [None, iso_days_ago(1), iso_days_ago(1)]):
            contact = contact_service.upsert_contact(
                db, phone=phone, full_name="Buyer", source="5e-flow-test")
            contact.last_messaged_at = last
            contact_service.add_to_list(db, contact_list.id, contact.id)
        db.commit()

        campaign = CampaignService(db).create_campaign(
            name=f"{NAME_PREFIX}capped wording", message_template=MESSAGE,
            audience=f"list:{contact_list.id}", cross_category_override=True,
            batch_size=1)
        sent = asyncio.run(CampaignService(db).send_campaign(campaign.id))
        assert sent.sent_count == 1 and sent.suppressed_count == 2

        refusal = asyncio.run(campaign_topup.assess(db, sent))["refusal"]
        expected = suppression_service.clears_at_clock(
            campaign_release.hold_clears_at(db, campaign.id))

        assert "capped at 1" in refusal
        assert "2 contacts" in refusal
        assert f"clears at {expected}" in refusal, refusal
        assert "holds them again" in refusal
    finally:
        _clear_window(db)
