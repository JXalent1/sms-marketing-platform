"""Which carrier failures may delete a buyer from the list, and under what reason.

Session 5d put the auto-block on a path it was not written for. `should_auto_block()`
was built for the *submission* path, where a carrier refuses a send outright and
the vocabulary is small. 5d A7 pointed it at the delivery webhook, where it went
from a handful of events to 3,037 and started seeing the entire transient-failure
vocabulary — as a list of unanchored substrings with no transient exclusion and
three bare numeric codes in it.

The failure this file prevents is invisible by construction. A wrongly blocked
buyer does not complain; he stops appearing in campaigns, and the only trace is
one `delivery_failure` row among thousands of correct ones. Nobody audits a list
for people who are missing from it.

Every hazard asserted below is real in shape and absent from this account's
traffic today — the whole live corpus is four strings, and none of them contains
"unreachable", a bare code, or the region wording. That makes these guards cheap
insurance rather than an emergency, and it is why the four real strings are
asserted too: a guard that breaks the traffic it runs on every day is worse than
the hazard it was built for.

Its sibling `test_carrier_error_surfaces.py` covers what a failure *shows* — the
raw provider payload, and the operator signal. This file covers what it *does*.

Nothing here sends. See decisions/003-auto-block-fragments-on-the-webhook-path.md.
"""

import os
import re
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.main import app
from app.models.blocked_number import BLOCK_REASONS, BlockedNumber
from app.models.sms_message import SMSMessage
from app.routers.webhooks.common import record_delivery_status
from app.services import blocklist_service, dashboard_service
from app.sms import compliance
from app.sms.compliance import classify_failure, should_auto_block
from tests import _carrier_failure_setup as setup
from tests._carrier_failure_setup import (
    LIVE_DEEMED_INVALID, LIVE_NOT_ROUTABLE, LIVE_RAW_PAYLOAD, LIVE_TEMPORARY_SPAM,
    REGION_ERROR, blocked,
)

PASSWORD = os.environ["ADMIN_PASSWORD"]

NOT_ROUTABLE = setup.DECISION_PHONES["not_routable"]
DEEMED_INVALID = setup.DECISION_PHONES["deemed_invalid"]
TEMPORARY_SPAM = setup.DECISION_PHONES["temporary_spam"]
RAW_PAYLOAD = setup.DECISION_PHONES["raw_payload"]
SWITCHED_OFF = setup.DECISION_PHONES["switched_off"]
QUOTED_NUMBER = setup.DECISION_PHONES["quoted_number"]
CODED_OPT_OUT = setup.DECISION_PHONES["coded_opt_out"]
WEBHOOK_PHONES = tuple(setup.DECISION_PHONES.values())

(ROUTE_PHONE, TWILIO_ROUTE_PHONE, AGREE_CARRIER_OPT_OUT, AGREE_OUR_STOP,
 AGREE_UNREACHABLE, API_PHONE) = setup.DECISION_EXTRA

PHONES = (*WEBHOOK_PHONES, *setup.DECISION_EXTRA)


@pytest.fixture(scope="module", autouse=True)
def login_budget():
    yield from setup.login_budget_fixture_body()


@pytest.fixture(scope="module")
def db():
    yield from setup.db_fixture_body(PHONES)


@pytest.fixture
def sent_message(db):
    yield from setup.sent_message_fixture_body(db, WEBHOOK_PHONES, "5g-decision")


# ─── 1. The traffic that actually exists ────────────────────────────────────

def test_the_live_failure_corpus_classifies_the_way_it_did_before(db, sent_message):
    """All four real strings, through the webhook, in one test.

    The guards below are built against hazards this account has never seen. The
    cost of getting one wrong is not hypothetical at all: 2,847 of these arrive
    per campaign, and a fragment list that stops matching them puts 2,847 dead
    numbers back on the list at half a cent each, every blast.
    """
    for phone, error in ((NOT_ROUTABLE, LIVE_NOT_ROUTABLE),
                         (DEEMED_INVALID, LIVE_DEEMED_INVALID),
                         (TEMPORARY_SPAM, LIVE_TEMPORARY_SPAM),
                         (RAW_PAYLOAD, LIVE_RAW_PAYLOAD)):
        record_delivery_status(db, sent_message[phone].external_id, "delivery_failed",
                               error, source="telnyx")

    assert blocked(db, NOT_ROUTABLE) is not None, "2,847 landlines a campaign stopped blocking"
    assert blocked(db, DEEMED_INVALID) is not None, "the 112 deemed-invalid failures"
    assert blocked(db, RAW_PAYLOAD) is not None, (
        "the raw-payload row no longer blocks — parsing the error must not "
        "change what it means"
    )
    assert blocked(db, TEMPORARY_SPAM) is None, (
        "76 temporary spam-filter blocks a campaign, every one of them still "
        "reachable, deleted from the list permanently"
    )


# ─── 2. Never block on a transient failure (A1) ─────────────────────────────

# Every wording here carries a live block fragment as well as a transient
# marker, so the guard is the only thing standing between it and a deleted
# buyer. That property is asserted below rather than asserted by me — see
# `test_every_transient_wording_would_block_without_the_guard`.
#
# It used to be three "unreachable" wordings plus two that carried no fragment
# at all and passed whether or not the guard existed. The mutation harness
# caught the latter two, and decision 004 then removed "unreachable" from the
# fragment list entirely, which would have left all five proving nothing. Rider
# 1 of that decision: do not preserve a harmful rule to keep a test meaningful —
# re-point the test.
TRANSIENT_WORDINGS = [
    "Temporary routing failure: destination not routable via this route, will retry",
    "Network congestion at the landline gateway, try again later",
    "Invalid phone number returned by the upstream lookup; temporary failure, retrying",
    "Destination deemed invalid by the carrier - temporary validation outage, retry later",
]


def _without_transient_markers(text: str) -> str:
    """The same string with every transient marker (and its suffix) removed."""
    for marker in compliance.TRANSIENT_FAILURE_MARKERS:
        text = re.sub(re.escape(marker) + r"[a-z]*", " ", text, flags=re.IGNORECASE)
    return text


@pytest.mark.parametrize("error", TRANSIENT_WORDINGS)
def test_a_transient_failure_never_blocks_however_it_is_worded(error):
    """A carrier describing a dead-number wording as temporary.

    The fragment says never again and the carrier says try later. The carrier
    wins, because it is the one that knows — and the cost of being wrong that way
    is half a cent for one more attempt, against a deleted buyer the other way.
    """
    assert not should_auto_block(error), f"a transient failure blocked a buyer: {error!r}"


@pytest.mark.parametrize("error", TRANSIENT_WORDINGS)
def test_every_transient_wording_would_block_without_the_guard(error):
    """The test above is only worth running if the guard is what stops it.

    Strip the transient markers out and the same string must block. If it does
    not, that row is passing because it matches nothing, and a green tick is
    standing in for a proof — which is exactly the defect decision 004's rider 1
    was written about, and exactly what the old "unreachable" wordings became the
    moment the fragment was removed.

    Expressed through the public API on purpose: it asserts the *property* rather
    than reaching into the module's regexes to re-check my own arithmetic.
    """
    assert should_auto_block(_without_transient_markers(error)), (
        f"{error!r} carries no block fragment, so the transient guard is not "
        f"what stops it and this row proves nothing"
    )


def test_the_transient_guard_beats_a_structured_code(db, sent_message):
    """A carrier reporting a permanent code inside a message about retrying is
    contradicting itself, and the safe reading of a contradiction is the one
    that keeps a buyer on the list. Costs half a cent for one more attempt."""
    record_delivery_status(db, sent_message[SWITCHED_OFF].external_id,
                           "delivery_failed",
                           "Temporary routing failure, will retry",
                           source="telnyx", error_code="21612")

    assert blocked(db, SWITCHED_OFF) is None
    assert not should_auto_block("Delivery deferred, will retry", error_code="21610")


def test_being_wrong_in_the_safe_direction_is_the_documented_trade():
    """If a carrier words a permanent failure with a transient marker, that
    number survives one extra campaign. State it, so nobody later "fixes" the
    guard by narrowing it."""
    assert not should_auto_block("This landline will never accept SMS, do not retry")


# ─── 2b. "unreachable" is not a block fragment (decision 004) ───────────────
#
# Separate from the guard tests above, and that separation is the point. These
# strings stop blocking because the *fragment is gone*, not because the guard
# caught them — three of them carry no transient marker at all, so the guard
# never saw them. Filing them under the guard is what made the old suite read
# stronger than it was.

UNREACHABLE_WORDINGS = [
    "Unreachable destination handset",                  # Twilio 30003, verbatim
    "SMPP bind failed - SMSC unreachable",              # a carrier-side outage
    "Destination unreachable",
    "Handset temporarily unreachable, will retry",      # this one the guard also catches
]


@pytest.mark.parametrize("error", UNREACHABLE_WORDINGS)
def test_an_unreachable_wording_does_not_block(error):
    """Decision 004, option 2.

    Every other fragment means one thing; "unreachable" meant three, two of them
    transient — a switched-off handset (Twilio 30003) and an upstream outage that
    is not about the recipient at all. The transient guard was supposed to
    separate them and could not: 30003's own `ErrorMessage` carries no marker.
    """
    assert not should_auto_block(error), (
        f"{error!r} still deletes a buyer whose phone may simply have been off"
    )


def test_the_unreachable_fragment_left_the_block_list():
    """Asserted on the list itself as well as on behaviour.

    A future session adding it back would make the four wordings above block
    again, and the reason not to is a cost argument rather than an obvious one —
    it is written out at the fragment list in `compliance.py`, per rider 2.
    """
    assert "unreachable" not in compliance.AUTO_BLOCK_ERROR_FRAGMENTS
    assert not any("unreachable" in f for f in compliance.AUTO_BLOCK_ERROR_FRAGMENTS)


def test_the_accepted_residual_is_a_dead_line_that_survives():
    """The cost of decision 004, asserted so it stays a known quantity.

    A line the carrier describes *only* as "unreachable" stays on the list and is
    paid for again on every campaign. That is the deliberate trade, not a gap:
    half a cent a blast against a bidder deleted permanently and invisibly.
    """
    assert not should_auto_block("The destination handset is unreachable")
    # But a carrier that says why still blocks — the fragment beside it does the
    # work, which is the whole reason "unreachable" was redundant as well as
    # ambiguous.
    assert should_auto_block("Unreachable: the destination is a landline")


def test_nothing_at_all_is_not_a_reason_to_block():
    assert not should_auto_block(None)
    assert not should_auto_block("")
    assert not should_auto_block("", error_code="")
    assert not should_auto_block("Carrier did not deliver the message")


# ─── 3. Codes come from a column, prose does not (A2) ───────────────────────

def test_a_quoted_phone_number_is_not_a_carrier_code(db, sent_message):
    """+1 321-610-xxxx is an assignable Brevard County number — this client's
    own market — and "21610" was a plain substring test against carrier prose
    that quotes destination numbers."""
    error = "Delivery to +13216105555 failed at the carrier"
    assert not should_auto_block(error), (
        "an innocent Brevard County number was blocked because a *different* "
        "number appeared in the error text"
    )

    record_delivery_status(db, sent_message[QUOTED_NUMBER].external_id,
                           "delivery_failed", error, source="telnyx")
    assert blocked(db, QUOTED_NUMBER) is None


@pytest.mark.parametrize("error", [
    "Delivery report ref 9f21610a-4c21-4f0e-9d10-4d21612bfa02 pending",
    "Charged 21610 units against the messaging profile",
    "Failed after 40300 ms",
])
def test_a_bare_number_in_prose_is_not_a_carrier_code(error):
    assert not should_auto_block(error), f"a number in prose read as a code: {error!r}"


def test_the_removed_codes_were_not_anchored_back_into_the_fragment_list():
    """`\\b21610\\b` still matches a bare code sitting in prose, and prose is
    not where codes belong — anchoring is what failed in `scrub_provider_text()`
    during 5d. The codes had to leave the text list, not gain boundaries."""
    for fragment in compliance.AUTO_BLOCK_ERROR_FRAGMENTS + compliance.CARRIER_OPT_OUT_FRAGMENTS:
        assert not any(character.isdigit() for character in fragment), (
            f"{fragment!r} matches carrier prose for a numeric code"
        )


def test_a_structured_opt_out_code_blocks_as_a_carrier_opt_out(db, sent_message):
    """Criterion 5. The same digits that must be ignored in prose must still
    work when the carrier puts them in the field built for them."""
    record_delivery_status(db, sent_message[CODED_OPT_OUT].external_id,
                           "delivery_failed", "Message could not be delivered",
                           source="twilio", error_code="21610")

    row = blocked(db, CODED_OPT_OUT)
    assert row is not None, "a genuine carrier opt-out was ignored"
    assert row.reason == "carrier_opt_out"


def test_a_code_arriving_as_an_integer_is_still_matched():
    """Carriers send codes as ints, as strings, and as strings with padding."""
    assert should_auto_block("Message could not be delivered", error_code=21610)
    assert should_auto_block("Message could not be delivered", error_code=" 21610 ")


def test_the_telnyx_webhook_stores_the_code_in_its_own_column():
    """The route builds `f"{title}: {detail}"` and used to drop `code` entirely,
    which is what forced the numeric rules to look for it in the prose."""
    setup_session = SessionLocal()
    try:
        setup_session.add(SMSMessage(phone=ROUTE_PHONE, message="body", status="sent",
                                     external_id="5g-route",
                                     sent_at="2026-08-26T09:00:00"))
        setup_session.commit()
    finally:
        setup_session.close()

    TestClient(app).post("/webhooks/telnyx", json={"data": {
        "event_type": "message.finalized",
        "payload": {
            "id": "5g-route",
            "to": [{"status": "delivery_failed"}],
            "errors": [{"code": "21610", "title": "Unsubscribed recipient",
                        "detail": "The destination has opted out."}],
        },
    }})

    check = SessionLocal()
    try:
        row = check.query(SMSMessage).filter(SMSMessage.external_id == "5g-route").first()
        assert row.error_code == "21610", (
            "the carrier's code is still being discarded on the way into a string"
        )
        assert blocked(check, ROUTE_PHONE).reason == "carrier_opt_out"
    finally:
        check.close()


def test_the_twilio_status_callback_stores_its_error_code():
    """Twilio posts ErrorCode as its own form field. It used to arrive only as
    a fallback for the *prose*, where no rule can safely read it."""
    setup_session = SessionLocal()
    try:
        setup_session.add(SMSMessage(phone=TWILIO_ROUTE_PHONE, message="body",
                                     status="sent", external_id="5g-twilio",
                                     sent_at="2026-08-26T09:00:00"))
        setup_session.commit()
    finally:
        setup_session.close()

    TestClient(app).post("/webhooks/twilio/status", data={
        "MessageSid": "5g-twilio", "MessageStatus": "undelivered",
        "ErrorCode": "21612", "ErrorMessage": "The number is not reachable",
    })

    check = SessionLocal()
    try:
        row = check.query(SMSMessage).filter(
            SMSMessage.external_id == "5g-twilio").first()
        assert row.error_code == "21612"
        assert blocked(check, TWILIO_ROUTE_PHONE) is not None
    finally:
        check.close()


# ─── 4. Word boundaries (A4) ────────────────────────────────────────────────

@pytest.mark.parametrize("error", [
    "policy: landlines-are-fine for this profile",
    "the destination is not routablewireless",
    "the destination was deemed invalidated by the carrier",
])
def test_a_fragment_inside_a_longer_word_does_not_block(error):
    """Cheap defence in depth. Anchoring is safe here in a way it was not in
    `scrub_provider_text()`: that regex reads text an SDK assembled, where the
    carrier's name gets glued to a word and a trailing `\\b` fails open. These
    read a carrier's English prose, where "landline" is a word."""
    assert not should_auto_block(error)


def test_the_boundaries_did_not_break_the_wordings_that_matter():
    for error in (LIVE_NOT_ROUTABLE, LIVE_DEEMED_INVALID, LIVE_RAW_PAYLOAD):
        assert should_auto_block(error), f"boundaries broke a live wording: {error!r}"


# ─── 5. The region wording is gone from the block list (A5) ─────────────────
#
# What it raises instead is `test_carrier_error_surfaces.py`'s subject. That it
# no longer condemns the recipient is this file's.

def test_the_region_wording_left_the_block_list():
    assert "has not been enabled for the region" not in compliance.AUTO_BLOCK_ERROR_FRAGMENTS
    assert not should_auto_block(REGION_ERROR)
    assert not should_auto_block("Message failed", error_code="21408")


# ─── 6. Carrier opt-outs get their own reason (A6) ──────────────────────────

def test_carrier_opt_out_is_a_real_block_reason():
    """`app/sms/` cannot import the model layer, so compliance.py holds these
    strings as literals. This is what stops the two copies drifting."""
    assert compliance.CARRIER_OPT_OUT in BLOCK_REASONS
    assert compliance.DELIVERY_FAILURE in BLOCK_REASONS


@pytest.mark.parametrize("error", [
    "The recipient has unsubscribed from this sender",
    "Destination has opted out of messages",
    "Carrier reports an opt-out on this number",
])
def test_an_opt_out_wording_is_an_opt_out_not_a_dead_number(error):
    """These were filed as `delivery_failure`, which put a person who asked not
    to be texted in the *unreachable* bucket — the same defect the 5d split was
    built to fix, arriving from the other direction."""
    verdict = classify_failure(error)
    assert verdict.block
    assert verdict.reason == "carrier_opt_out"


def test_a_carrier_opt_out_is_not_filed_as_our_own_inbound_stop():
    """`stop_keyword` is documented as legally binding: a message this system
    received and can produce on demand. A carrier's assertion is not that
    evidence, and collapsing them weakens a record that exists to be audited."""
    assert classify_failure("recipient unsubscribed").reason != "stop_keyword"
    assert "carrier_opt_out" in blocklist_service.OPT_OUT_REASONS
    assert "stop_keyword" in blocklist_service.OPT_OUT_REASONS


def _tile_opt_outs(session) -> int:
    """The count the dashboard renders, read back off the tile it renders it on.

    Parsed from the rendered sub-label rather than re-running the query, so this
    fails if the tile stops using `OPT_OUT_REASONS` — which is the whole claim.
    """
    tile = next(t for t in dashboard_service.stat_tiles(session) if t["key"] == "opt_outs")
    return int(tile["sub"].split()[0].replace(",", ""))


def test_the_blocklist_headline_and_the_dashboard_tile_agree(db):
    """Criterion 7, and the invariant claimed at blocklist_service.py:88-91.

    Both figures are recomputed over rows that include `carrier_opt_out`. A
    literal filter in either place — which is what `dashboard_service` had —
    leaves the two screens reporting different opt-out counts from one table,
    and this is the single number a client judges his list by.
    """
    now = datetime.now().isoformat()

    before_counts = blocklist_service.blocked_counts(db)
    before_tile = _tile_opt_outs(db)

    db.add_all([
        BlockedNumber(phone=AGREE_CARRIER_OPT_OUT, reason="carrier_opt_out",
                      source="telnyx", blocked_at=now),
        BlockedNumber(phone=AGREE_OUR_STOP, reason="stop_keyword", source="webhook",
                      blocked_at=now),
        BlockedNumber(phone=AGREE_UNREACHABLE, reason="delivery_failure",
                      source="telnyx", blocked_at=now),
    ])
    db.commit()

    after_counts = blocklist_service.blocked_counts(db)
    assert after_counts["opt_outs"] - before_counts["opt_outs"] == 2, (
        "a carrier opt-out is being reported as an unreachable number"
    )
    assert after_counts["unreachable"] - before_counts["unreachable"] == 1
    assert _tile_opt_outs(db) - before_tile == 2, (
        "the dashboard tile and the Opt-outs headline disagree about the same rows"
    )


def test_the_opt_outs_api_counts_a_carrier_opt_out_as_an_opt_out(db):
    """Counted server-side over the whole table: `numbers` is capped at 5,000
    rows, so a client-side tally under-reports as *fewer opt-outs* the day the
    list overflows — the direction nobody checks."""
    client = TestClient(app)
    client.post("/login", data={"username": "admin", "password": PASSWORD})

    before = client.get("/api/blocklist").json()["counts"]
    db.query(BlockedNumber).filter(BlockedNumber.phone == API_PHONE).delete(
        synchronize_session=False)
    db.add(BlockedNumber(phone=API_PHONE, reason="carrier_opt_out", source="telnyx",
                         blocked_at=datetime.now().isoformat()))
    db.commit()
    try:
        after = client.get("/api/blocklist").json()["counts"]
        assert after["opt_outs"] - before["opt_outs"] == 1
        assert after["unreachable"] == before["unreachable"]
        assert after["other"] == before["other"], (
            "carrier_opt_out fell into the catch-all bucket instead of the "
            "figure it belongs to"
        )
    finally:
        db.query(BlockedNumber).filter(BlockedNumber.phone == API_PHONE).delete(
            synchronize_session=False)
        db.commit()
