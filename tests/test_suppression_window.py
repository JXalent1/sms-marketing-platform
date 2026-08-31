"""5e A5 and A6: the hold-back window on a screen, and its effect before you queue.

  A5  the window is settable in Settings and takes effect without a restart,
      defaulting to whatever this installation was deployed with
  A6  the composer names the held-back count and when it clears, at the moment of
      decision — and says nothing at all when the window is 0

**The rule itself is not tested here as though it were new.** Who gets held back,
and the fact that suppression is blind to category, are module 4's and are pinned
in `test_campaign_guardrails.py`. What 5e changed is where the *number* comes
from, so that is what these assert. A test here that re-checked the partitioning
would be a second definition of a rule on the escalation list.

The rules this file inherits are in `_campaign_flow_setup.py`.
"""

import pytest
from datetime import datetime, timedelta
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.models.app_setting import AppSetting
from app.models.contact import Contact
from app.services import contact_service, preflight_service, suppression_service
from app.services.campaign_service import CampaignService
from tests._campaign_flow_setup import (
    MESSAGE, NAME_PREFIX, iso_days_ago, purged_db_fixture_body,
    rate_limit_fixture_body, take,
)

PASSWORD = "devpassword123"


@pytest.fixture(scope="module", autouse=True)
def rate_limits():
    yield from rate_limit_fixture_body()


@pytest.fixture(scope="module")
def db():
    yield from purged_db_fixture_body()


@pytest.fixture(autouse=True)
def restore_window(db):
    """Put the window back after every test in this module.

    Module scope would not be enough: these tests move a setting the whole suite
    reads, and a failure partway through one of them would leave a 30-day window
    behind for every module that runs afterwards.
    """
    yield
    db.query(AppSetting).filter(
        AppSetting.key == suppression_service.SUPPRESSION_DAYS_KEY).delete(
        synchronize_session=False)
    db.commit()


@pytest.fixture(scope="module")
def client():
    c = TestClient(app)
    login = c.post("/login", data={"username": "admin", "password": PASSWORD})
    assert login.status_code in (200, 302), (
        f"login failed with {login.status_code} — every assertion below would "
        f"have run against a 401 body and passed by containing nothing"
    )
    return c


# ─── A5: the window is a stored setting ─────────────────────────────────────

def test_the_default_is_what_this_installation_was_deployed_with(db):
    """"Read the current value as the default, do not assume 3."

    Production runs 0 at the client's instruction and this machine's config says
    3. Hardcoding either would be wrong on the other box, so the default is
    whatever `.env` set — asserted against the config object rather than against
    a literal, which is the difference between pinning the behaviour and pinning
    this machine.
    """
    assert suppression_service.suppression_days(db) == \
        settings.RECENT_CONTACT_SUPPRESSION_DAYS


def test_setting_it_takes_effect_without_a_restart(db):
    """Criterion 7. Read fresh on every call, so there is no cache to invalidate.

    The proof is not that the setter returns the new value — it is that the
    *cutoff the send path filters on* moves, in the same process, with nothing
    reloaded. Asserted as a span in days rather than an exact timestamp: the two
    calls are microseconds apart and an equality test on ISO strings would be
    flaky for a reason that has nothing to do with the code.
    """
    suppression_service.set_suppression_days(db, 7)
    assert suppression_service.suppression_days(db) == 7

    cutoff = datetime.fromisoformat(suppression_service.suppression_cutoff(db))
    assert abs((datetime.now() - cutoff) - timedelta(days=7)) < timedelta(seconds=5)

    suppression_service.set_suppression_days(db, 0)
    cutoff = datetime.fromisoformat(suppression_service.suppression_cutoff(db))
    assert abs(datetime.now() - cutoff) < timedelta(seconds=5)


def test_the_window_actually_changes_who_is_held_back(db):
    """The setting is only real if the partition moves with it.

    One contact, texted five days ago, run through the same call the send path
    makes — held back at 30, sendable at 3. A setting that stored a number the
    filter did not read would pass every test above this one.
    """
    phone = take(1)[0]
    contact = contact_service.upsert_contact(
        db, phone=phone, full_name="Five Days Ago", source="5e-flow-test")
    contact.last_messaged_at = iso_days_ago(5)
    db.commit()

    suppression_service.set_suppression_days(db, 30)
    sendable, held = suppression_service.partition_recent(db, [contact])
    assert (len(sendable), len(held)) == (0, 1)

    suppression_service.set_suppression_days(db, 3)
    sendable, held = suppression_service.partition_recent(db, [contact])
    assert (len(sendable), len(held)) == (1, 0)


@pytest.mark.parametrize("bad", [-1, 91, "seven", None, 3.5])
def test_a_value_outside_the_range_is_refused_rather_than_stored(db, bad):
    """The write path validates so the read path never has to guess.

    0 and 90 are both legitimate and are asserted as such below; what is refused
    is a negative window (which would mean "texted in the future"), one past the
    ceiling, and anything that is not a whole number. `3.5` is in here because
    `int("3.5")` raises while `int(3.5)` truncates — a float that silently became
    3 would be a setting that disagreed with the field he typed it into.
    """
    with pytest.raises(ValueError):
        suppression_service.set_suppression_days(db, bad)
    assert suppression_service.suppression_days(db) == \
        settings.RECENT_CONTACT_SUPPRESSION_DAYS, "a refused value was stored anyway"


@pytest.mark.parametrize("good", [0, 1, 90])
def test_the_edges_of_the_range_are_accepted(db, good):
    """0 is the production setting and 90 is the ceiling. Both are real values."""
    assert suppression_service.set_suppression_days(db, good) == good
    assert suppression_service.suppression_days(db) == good


def test_a_junk_row_falls_back_to_the_configured_default(db):
    """A hand-edited database must not decide who gets texted.

    The write path validates, so a value this bad means somebody went round it.
    Falling back to what the box was deployed with is the only answer that is not
    a guess — and it must not raise, because this is read on the send path.
    """
    from app.models.app_setting import set_setting
    set_setting(db, suppression_service.SUPPRESSION_DAYS_KEY, "not a number")
    assert suppression_service.suppression_days(db) == \
        settings.RECENT_CONTACT_SUPPRESSION_DAYS

    set_setting(db, suppression_service.SUPPRESSION_DAYS_KEY, "9999")
    assert suppression_service.suppression_days(db) == \
        settings.RECENT_CONTACT_SUPPRESSION_DAYS


def test_the_settings_endpoint_round_trips_the_window(db, client):
    """Criterion 7 over HTTP, including the "is this a default?" flag.

    `is_default` is not decoration: "currently 0" leaves him unable to tell a
    value he set from one the box was deployed with, and those need different
    actions when the number is wrong.
    """
    first = client.get("/api/settings/suppression").json()
    assert first["is_default"] is True
    assert first["max_days"] == suppression_service.SUPPRESSION_DAYS_MAX

    saved = client.put("/api/settings/suppression", json={"days": 5})
    assert saved.status_code == 200, saved.text
    assert saved.json()["days"] == 5

    again = client.get("/api/settings/suppression").json()
    assert again["days"] == 5
    assert again["is_default"] is False


def test_the_settings_endpoint_refuses_a_bad_window_with_a_sentence(db, client):
    """A 400 he can read, not a validation blob."""
    response = client.put("/api/settings/suppression", json={"days": 400})
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "between 0 and 90" in detail
    assert "0 holds nobody back" in detail


def test_the_settings_page_carries_the_field(client):
    """The whole point of A5 is that it is on a screen, not in `.env`."""
    html = client.get("/settings").text
    assert 'id="suppressionDays"' in html
    assert "Hold-back window" in html
    # The explanation has to state the consequence in both directions — a rule he
    # can change needs to say what changing it costs, or it becomes a number
    # somebody nudges.
    assert "hold nobody back" in html
    assert "held back" in html


# ─── A6: what it is doing, before the campaign is queued ────────────────────

def test_the_preview_names_the_held_back_count_and_when_it_clears(db, client):
    """Criterion 8, first half.

    The count already existed on `/preview` and was shown as a bare number. The
    clearing time is the half that decides whether he waits or re-cuts the
    audience, and it comes from the server because a figure computed in the page
    would eventually quote a window the send path was not filtering with.
    """
    suppression_service.set_suppression_days(db, 10)
    phones = take(2)
    contact_list = contact_service.get_or_create_list(db, f"{NAME_PREFIX}A6 list")
    for phone, days in zip(phones, (1, 4)):
        contact = contact_service.upsert_contact(
            db, phone=phone, full_name="Texted Recently", source="5e-flow-test")
        contact.last_messaged_at = iso_days_ago(days)
        contact_service.add_to_list(db, contact_list.id, contact.id)
    db.commit()

    payload = client.post("/api/campaigns/preview", json={
        "message_template": MESSAGE, "audience": f"list:{contact_list.id}"}).json()

    assert payload["suppressed"] == 2
    assert payload["recipients"] == 0
    assert payload["suppression_days"] == 10
    assert payload["suppression_clears_at"], "the count without the time is A6 unfixed"

    # The *latest* of the held-back set plus the window, not the earliest: the
    # question is "when can I send this to all of them", and the earliest gives a
    # time at which most of the hold is still in force.
    clears = datetime.fromisoformat(payload["suppression_clears_at"])
    expected = datetime.fromisoformat(iso_days_ago(1)) + timedelta(days=10)
    assert abs(clears - expected) < timedelta(seconds=5)


def test_at_a_window_of_zero_the_composer_is_told_nothing(db, client):
    """Criterion 8, second half. "If the window is 0, say nothing."

    Two separate facts and both are asserted: nobody is held back, *and* there is
    no clearing time to render. A payload that reported 0 held back alongside a
    timestamp would have the page draw an explanation of a rule that is not
    running.
    """
    suppression_service.set_suppression_days(db, 0)
    contact_list = contact_service.get_or_create_list(db, f"{NAME_PREFIX}A6 list")

    payload = client.post("/api/campaigns/preview", json={
        "message_template": MESSAGE, "audience": f"list:{contact_list.id}"}).json()

    assert payload["suppressed"] == 0
    assert payload["suppression_days"] == 0
    assert payload["suppression_clears_at"] is None


def test_the_clearing_time_is_never_computed_from_an_unparseable_stamp():
    """Return nothing rather than something wrong.

    A wrong clearing time is worse than none: he plans around it. This is a pure
    function so the bad input can be fed to it directly, which is the only honest
    way to test a branch that a well-behaved database will never reach.
    """
    class _Contact:
        def __init__(self, last):
            self.last_messaged_at = last

    assert suppression_service.suppression_clears_at([_Contact("garbage")], 3) is None
    assert suppression_service.suppression_clears_at([_Contact(None)], 3) is None
    assert suppression_service.suppression_clears_at([], 3) is None
    # And a window of 0 short-circuits before any of that.
    assert suppression_service.suppression_clears_at(
        [_Contact(iso_days_ago(1))], 0) is None


def test_the_preflight_row_quotes_the_stored_window_not_the_env_one(db, client):
    """The row and the send path have to describe the same window.

    `check_recent_overlap` takes the window as an argument rather than looking it
    up a second way, and this is what that buys: a checklist row saying "texted
    in the last 3 days" under a campaign filtered on 12 is exactly the kind of
    quiet disagreement between two readers this codebase keeps removing.

    **Driven through the real endpoint, not by calling the check directly.** The
    check has always taken the number it is given; what could regress is the
    *call site* going back to reading config. A test that called
    `check_recent_overlap(suppression_days(db), …)` itself would pass happily
    while `build_report` quoted `.env` — which is precisely what the mutation run
    found when this test was written that way.
    """
    stored = 12
    assert stored != settings.RECENT_CONTACT_SUPPRESSION_DAYS, (
        "this test cannot distinguish the two readers if they agree — pick a "
        "window that differs from the configured default")
    suppression_service.set_suppression_days(db, stored)

    phones = take(2)
    contact_list = contact_service.get_or_create_list(db, f"{NAME_PREFIX}row list")
    for phone in phones:
        contact = contact_service.upsert_contact(
            db, phone=phone, full_name="Texted Recently", source="5e-flow-test")
        contact.last_messaged_at = iso_days_ago(1)
        contact_service.add_to_list(db, contact_list.id, contact.id)
    db.commit()

    report = client.post("/api/campaigns/preflight", json={
        "message_template": MESSAGE, "audience": f"list:{contact_list.id}"}).json()
    row = next(c for c in report["checks"] if c["key"] == "recent_overlap")

    assert row["status"] == "warn"
    assert f"last {stored} days" in row["reason"], row["reason"]
    assert "clears" in row["reason"], row["reason"]
    assert row["suppression_days"] == stored
    assert report["counts"]["suppression_days"] == stored


def test_the_checklist_row_and_the_summary_panel_quote_one_window(db, client):
    """Two surfaces, one number — asserted by comparing them, not by reading both.

    `/preview` fills the composer's summary panel and `/preflight` fills its
    checklist, and they are separate endpoints. The lesson from 5g's opt-out
    definition applies: assert the property the shared reader exists for (the two
    screens agree), not the value each happens to return.
    """
    suppression_service.set_suppression_days(db, 9)

    # Seeds its own contacts. Reading the list a previous test happened to fill
    # made two of the three assertions below compare 0 == 0 and None == None when
    # the test ran alone — and `accept-5e.sh` runs each criterion's tests alone,
    # so it reported green while proving nothing. Found by review.
    contact_list = contact_service.get_or_create_list(db, f"{NAME_PREFIX}agree list")
    for phone in take(2):
        contact = contact_service.upsert_contact(
            db, phone=phone, full_name="Texted Recently", source="5e-flow-test")
        contact.last_messaged_at = iso_days_ago(2)
        contact_service.add_to_list(db, contact_list.id, contact.id)
    db.commit()
    audience = f"list:{contact_list.id}"

    preview = client.post("/api/campaigns/preview", json={
        "message_template": MESSAGE, "audience": audience}).json()
    report = client.post("/api/campaigns/preflight", json={
        "message_template": MESSAGE, "audience": audience}).json()

    # The comparison is only meaningful over a non-empty hold. Asserted rather
    # than assumed, because "both endpoints reported nothing" is the shape this
    # test used to pass with.
    assert preview["suppressed"] == 2, "nothing was held back, so nothing is compared"
    assert preview["suppression_clears_at"] is not None

    assert preview["suppression_days"] == report["counts"]["suppression_days"]
    assert preview["suppressed"] == report["counts"]["suppressed"]
    assert preview["suppression_clears_at"] == report["counts"]["suppression_clears_at"]


def test_the_preflight_row_says_the_rule_is_off_rather_than_reporting_zero_days(db):
    """"Nobody was texted in the last 0 days" reads as a fact about the audience.

    It is a fact about the rule. At 0 the row has to say the window is off, or
    the client reads a green tick as evidence his list is fresh.
    """
    row = preflight_service.check_recent_overlap(0, 0, 100)
    assert row["status"] == "pass"
    assert "off" in row["reason"]
    assert "last 0 days" not in row["reason"]
