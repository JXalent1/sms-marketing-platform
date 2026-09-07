"""Session P1b A1: the carrier lookup provider, and what it is not allowed to spend.

**Nothing here calls the real API.** Three separate things make that true rather
than one:

  1. Every provider under test is built with a visibly fake key and has its SDK
     client replaced with a recorder before any test touches it.
  2. The recorded responses in `tests/fixtures/number_lookup_responses.json` are
     replayed through the SDK's **own** response model, so a pin that no longer
     matches the code fails here instead of on the morning of a sale — the
     defect `tests/test_provider_status.py` exists for, one API over.
  3. `PROSPECT_LOOKUP_PROVIDER` is restored after every test that changes it,
     and the one test that resolves the carrier class never calls `lookup()`.

The money rules are the point of the rest of the file. A lookup and a send draw
on the same carrier balance, so an unbounded screening run is a silent transfer
out of the pot the campaign pre-flight check measures — and the operator would
have no way to connect an overnight scrape to the next morning's refusal.
"""

import json
import logging
import pathlib
from contextlib import contextmanager
from decimal import Decimal

import pytest

from app.core.config import settings
from app.models.prospect import ProspectRejection
from app.models.scrape import PhoneLookup
from app.services import (
    blocklist_service, lookup_service, prospect_ingest, prospect_service,
)
from app.sms import lookup as lookup_module
from app.sms.providers import telnyx_lookup

from tests import _prospect_setup as setup

FIXTURES = json.loads(
    (pathlib.Path(__file__).parent / "fixtures" /
     "number_lookup_responses.json").read_text())

FAKE_API_KEY = "KEY_NOT_A_REAL_CREDENTIAL_test_only"

# What the recorded responses are expected to mean once normalised. Written out
# rather than derived from the map under test, because a table that reads itself
# proves nothing.
EXPECTED = {
    "mobile": ("mobile", True),
    "landline": ("landline", True),
    "voip": ("voip", True),
    "toll_free": ("toll_free", True),
    # Answered, and the answer is one we may not act on. Cached forever all the
    # same: asking again next month cannot make it less ambiguous.
    "ambiguous": ("unknown", True),
    # NOT an answer. A shape we do not understand is our bug or schema drift,
    # and freezing it into the cache is the permanent hole the module docstring
    # exists to prevent.
    "no_classification": ("unknown", False),
}


@pytest.fixture(scope="module")
def db():
    yield from setup.purged_db_fixture_body()


# ─── Harness ────────────────────────────────────────────────────────────────

class RecordedLookups:
    """Stands in for `client.number_lookup`. Replays one recorded response."""

    def __init__(self, response=None, raises=None):
        self.response = response
        self.raises = raises
        self.calls = []

    def retrieve(self, phone_number, *, type=None, **kwargs):
        self.calls.append((phone_number, type))
        if self.raises is not None:
            raise self.raises
        return self.response


class SDKError(Exception):
    """An SDK API error, in the shape `describe_send_error()` reads by name.

    Reproduced rather than imported so the assertion below is about *our*
    handling: the body carries the carrier's name and a destination number, and
    neither may reach the column this ends up in.
    """

    def __init__(self):
        super().__init__("Error code: 400 - {'errors': [{'code': '10002'}]}")
        self.body = {"errors": [{"code": "10002", "title": "TelnyxError",
                                 "detail": "Lookup failed for +19545550123"}]}


@contextmanager
def carrier_lookup_provider(response=None, raises=None):
    """A real `TelnyxLineTypeProvider` whose client cannot reach the network.

    The class is constructed for the reason `_provider_setup.carrier_provider()`
    does it on the send side: a stub that agrees with the code under test is how
    a suite goes green over a provider nobody can build. Construction makes no
    call (verified against telnyx 4.175.0); the client is replaced immediately
    afterwards so nothing later in the test can make one either.
    """
    previous = settings.TELNYX_API_KEY
    settings.TELNYX_API_KEY = FAKE_API_KEY
    try:
        provider = telnyx_lookup.TelnyxLineTypeProvider()
        recorder = RecordedLookups(response=response, raises=raises)
        provider.client = type("FakeClient", (), {"number_lookup": recorder})()
        yield provider, recorder
    finally:
        settings.TELNYX_API_KEY = previous


@contextmanager
def configured(provider_name: str, api_key: str = FAKE_API_KEY):
    """Point `PROSPECT_LOOKUP_PROVIDER` somewhere, then put it back.

    The cached instance is rebuilt on the way out as well as on the way in —
    `get_lookup_provider()` caches one per process and the suite shares it, the
    same rule `_provider_setup` follows for the send factory.
    """
    previous = (settings.PROSPECT_LOOKUP_PROVIDER, settings.TELNYX_API_KEY)
    settings.PROSPECT_LOOKUP_PROVIDER = provider_name
    settings.TELNYX_API_KEY = api_key
    try:
        yield lookup_module.get_lookup_provider(force_reload=True)
    finally:
        (settings.PROSPECT_LOOKUP_PROVIDER, settings.TELNYX_API_KEY) = previous
        lookup_module.get_lookup_provider(force_reload=True)


@contextmanager
def monthly_cap(value):
    previous = settings.PROSPECT_LOOKUP_MONTHLY_CAP
    settings.PROSPECT_LOOKUP_MONTHLY_CAP = value
    try:
        yield
    finally:
        settings.PROSPECT_LOOKUP_MONTHLY_CAP = previous


@contextmanager
def room_for(db, calls: int):
    """A cap with room for exactly `calls` more lookups than are already spent.

    Seeded from what the table already holds rather than from zero, because the
    cap is monthly and every other module in this suite screens numbers too. A
    fixed dollar figure here would pass or fail depending on which tests ran
    first, which is the shape of flake this session exists to remove.
    """
    already = lookup_service.spend_this_month(db)
    with monthly_cap(float(already + lookup_service.cost_per_lookup() * calls)):
        yield


def _response(key):
    """The recorded body, parsed by the SDK's own model."""
    from telnyx.types import NumberLookupRetrieveResponse
    return NumberLookupRetrieveResponse.model_validate(FIXTURES[key])


# ─── The runtime contract with the SDK ──────────────────────────────────────

def test_the_sdk_still_exposes_the_call_this_provider_drives():
    """A pinned SDK is a runtime contract, not a version number.

    `requirements.txt` once said 2.1.2 while the provider was written against
    the 4.x client class: nothing failed at import, at boot or in the suite, and
    the box served every page while being unable to send. This asserts the shape
    the lookup provider drives, so a bad pin fails at `pip install` instead.
    """
    import inspect
    import telnyx

    client = telnyx.Telnyx(api_key=FAKE_API_KEY)      # no network call
    assert hasattr(client, "number_lookup"), (
        "the SDK no longer exposes number_lookup — the pin moved under us")

    parameters = inspect.signature(client.number_lookup.retrieve).parameters
    assert "phone_number" in parameters and "type" in parameters, (
        f"retrieve() takes {list(parameters)}, which is not what the provider "
        f"calls it with")


@pytest.mark.parametrize("key", sorted(FIXTURES.keys() - {"_README"}))
def test_every_recorded_response_still_parses_as_the_sdk_declares_it(key):
    """The fixtures are the contract, so they are validated, not trusted.

    A fixture the SDK refuses to parse means the recorded shape and the shipped
    client have drifted apart, and every assertion built on it below would be
    testing our own JSON rather than the carrier's.
    """
    parsed = _response(key)
    assert parsed.data is not None


# ─── A1: the provider answers, against a recorded fixture ───────────────────

@pytest.mark.parametrize("key", sorted(EXPECTED))
def test_the_provider_returns_a_line_type_from_a_recorded_response(key):
    expected_type, expected_ok = EXPECTED[key]
    with carrier_lookup_provider(response=_response(key)) as (provider, recorder):
        result = provider.lookup("+19545550123")

    assert recorder.calls == [("+19545550123", "carrier")], (
        "the provider must ask for the carrier lookup — the caller-name query "
        "answers a different question and is priced differently")
    assert result.line_type == expected_type
    assert result.ok is expected_ok


def test_every_line_type_the_sdk_declares_is_mapped_and_the_rest_are_unknown():
    """The carrier's vocabulary is a contract too, and it is wider than ours.

    Read off the SDK's own `Literal` rather than from a list in the test: a
    value the API can return and this map has never heard of falls through to
    the default, and the default has to be `unknown`. If it were `mobile`, one
    new classification upstream would put landlines on the list — which is the
    same failure as the gate being switched off, arriving by a different door.
    """
    import typing
    from telnyx.types.number_lookup_retrieve_response import DataCarrier

    declared = typing.get_args(
        typing.get_args(DataCarrier.model_fields["type"].annotation)[0])
    assert declared, "the SDK stopped declaring the line types it can return"

    unmapped = [value for value in declared
                if value not in telnyx_lookup.CARRIER_LINE_TYPES]
    assert not unmapped, (
        f"the API can answer {unmapped} and this provider has never heard of "
        f"those — each needs a row in CARRIER_LINE_TYPES, decided deliberately")

    for value in declared:
        assert telnyx_lookup.normalize_carrier_type(value) in (
            "mobile", "voip", "landline", "toll_free", "unknown")

    assert telnyx_lookup.normalize_carrier_type("some new classification") == "unknown"
    assert telnyx_lookup.normalize_carrier_type(None) == "unknown"
    # Separator spellings. The API says "fixed line"; its documentation and
    # several clients say "fixed_line", and a spelling difference must not
    # silently become a line type we refuse to promote.
    assert telnyx_lookup.normalize_carrier_type("Fixed_Line") == "landline"
    assert telnyx_lookup.normalize_carrier_type("toll-free") == "toll_free"


def test_an_ambiguous_answer_is_never_treated_as_a_mobile():
    """`fixed line or mobile` is the asymmetry, spelled out.

    A mobile wrongly held back waits in a review queue. A landline wrongly
    promoted is paid for on every send from now on, which is how 2,526 dead
    numbers came to sit on this client's list. Make the cheap error.
    """
    with carrier_lookup_provider(response=_response("ambiguous")) as (p, _):
        result = p.lookup("+19545550166")

    assert result.line_type == "unknown"
    assert result.line_type not in lookup_service.PROMOTABLE_LINE_TYPES


def test_a_billed_call_we_cannot_read_records_the_spend_and_stays_retryable():
    """The one case that is a charge and not an answer.

    A response with no line type in it was still a completed lookup, so the
    money is gone and the row has to say so — but caching it as `unknown`
    forever would file the number as permanently unscreenable over what is
    probably our own parsing bug.
    """
    with carrier_lookup_provider(response=_response("no_classification")) as (p, _):
        result = p.lookup("+19545550199")

    assert result.ok is False
    assert Decimal(result.cost) == lookup_service.cost_per_lookup()


def test_a_failed_call_is_not_charged_for_and_names_no_carrier(db):
    """Two properties of one path, and the second is the white-label one.

    The SDK's own error string is the response body's dict repr, and the body
    carries both the carrier's name and a destination number. Asserted on the
    stored row rather than on the return value, because the column is where it
    would end up and `blocked_numbers.notes` is what taught this codebase that
    stored carrier free text eventually reaches a screen.
    """
    phone = setup.take(1)[0]
    with carrier_lookup_provider(raises=SDKError()) as (provider, recorder):
        result = provider.lookup(phone)
        assert result.ok is False and result.cost == "", (
            "a call that never completed was recorded as a charge")
        # Asserted on what the *provider* returned, before the scrubber sees
        # it. `scrub_provider_text()` strips a payload as well as a carrier
        # name, so a provider that passed `str(exc)` straight through would
        # leave the stored row clean and the defect invisible — the mutation
        # harness proved exactly that. The provider owes its own guarantee:
        # fields read by name, never the response body.
        assert result.error == "TelnyxError: Lookup failed for +19545550123", (
            f"the error was not assembled from named fields: {result.error!r}")
        lookup_service.line_type_for(db, phone, provider=provider)

    assert recorder.calls, "precondition: the SDK call was actually attempted"
    row = db.query(PhoneLookup).filter(PhoneLookup.phone == phone).one()
    assert row.status == "error" and row.cost is None
    assert "telnyx" not in (row.error or "").lower()
    assert "{'errors'" not in (row.error or ""), row.error


# ─── The registry: reachable only when it is configured ─────────────────────

def test_the_default_provider_is_still_the_one_that_makes_no_call(db):
    """Criterion 2's second half. The carrier class exists now; the default
    did not move, because switching screening on spends real money and that is
    one line of `.env` on a live box rather than a consequence of deploying."""
    assert settings.PROSPECT_LOOKUP_PROVIDER == "none"
    provider = lookup_module.get_lookup_provider(force_reload=True)
    assert provider.name == "disabled"

    result = provider.lookup(setup.POOL[0])
    assert result.line_type == "unknown" and result.ok is False
    assert result.line_type not in lookup_service.PROMOTABLE_LINE_TYPES, (
        "a box with no screening credential can promote unscreened numbers")


def test_the_carrier_provider_is_reachable_once_it_is_configured():
    """The registry line, exercised. Nothing calls `lookup()` on it."""
    with configured("telnyx") as provider:
        assert isinstance(provider, telnyx_lookup.TelnyxLineTypeProvider)
        assert provider.name == "telnyx"

    assert lookup_module.get_lookup_provider().name == "disabled", (
        "the cached instance was left pointing at the carrier")


@pytest.mark.parametrize("name,key", [
    ("telnix", FAKE_API_KEY),          # a typo in .env
    ("telnyx", ""),                    # configured, no credential
])
def test_a_misconfigured_screening_provider_degrades_instead_of_raising(name, key):
    """A typo should cost screening, not the ability to log in.

    Safe here in a way it is not on the send path: the fallback answers
    `unknown`, `unknown` promotes nobody, and the queue's wording — "this number
    has not been screened yet" — is true whether screening is off or broken. It
    is logged at ERROR so the two are still tellable apart by whoever has to fix
    it.
    """
    with configured(name, api_key=key) as provider:
        assert provider.name == "disabled"


# ─── The spend cap ──────────────────────────────────────────────────────────

def test_the_cap_refuses_before_the_call_and_logs_what_went_unscreened(db, caplog):
    """Criterion 3. Asserted on the call count, because that is the money.

    "Refuses before calling" is the whole requirement: a cap enforced after the
    fact is an audit, not a cap, and the balance it is protecting is the one the
    next campaign sends on.
    """
    phones = setup.take(5)
    provider = setup.CountingLookupProvider()

    with room_for(db, 2):
        cap = lookup_service.monthly_cap()
        with caplog.at_level(logging.ERROR, logger="lookup"):
            outcome = lookup_service.screen(db, phones, provider=provider)
        spent_after = lookup_service.spend_this_month(db)

    assert len(provider.calls) == 2, (
        f"the cap allowed {len(provider.calls)} calls when it had room for 2")
    assert outcome["performed"] == 2
    assert outcome["skipped"] == {"spend_cap": 3, "not_usable": 0}
    assert spent_after <= cap, (
        f"screening spent {spent_after} against a cap of {cap} — the account "
        f"was drained past the ceiling it was given")

    # Nothing was written for the three, so they are screenable next month
    # rather than filed as unscreenable forever.
    assert db.query(PhoneLookup).filter(
        PhoneLookup.phone.in_(phones[2:])).count() == 0

    logged = " ".join(record.getMessage() for record in caplog.records)
    assert "3 number(s) went unscreened" in logged, logged
    assert "PROSPECT_LOOKUP_MONTHLY_CAP" in logged, (
        "the refusal names no remedy, which is what makes an operator conclude "
        "the tool is broken")


def test_the_single_number_path_enforces_the_cap_too(db):
    """The entry point the mutation is on, not the one that is convenient.

    `screen()` works out the budget once and hands it down, so a cap guard that
    only lived there would be reverted with the suite green — which is exactly
    how P1's two cache guards survived their own criterion's test.
    """
    phone = setup.take(1)[0]
    provider = setup.CountingLookupProvider()

    with room_for(db, 0):
        outcome = lookup_service.line_type_for(db, phone, provider=provider)

    assert provider.calls == [], "the single-number path spent past the cap"
    assert outcome["skipped"] == "spend_cap" and outcome["ok"] is False
    assert db.query(PhoneLookup).filter(PhoneLookup.phone == phone).count() == 0


def test_a_cap_of_zero_switches_screening_off_rather_than_making_it_unlimited(db):
    """The reading that would be a disaster, written down as a test.

    Zero is the value somebody sets to mean "stop", and a `cap <= 0` that fell
    through to "no limit" would turn the off switch into the on switch.

    **The second half is the half that tests the guard**, and finding that out
    took the mutation harness twice. At the configured price `cost <= remaining`
    already refuses — on an exhausted cap and on a fresh zero one alike — so the
    first case passes whether or not the `cap <= 0` branch exists. The branch
    bites in exactly one arrangement: a lookup priced at **zero**, against a
    budget that has spent nothing. The first is what somebody sets while wiring
    up a provider they believe is free; the second is a box at the start of a
    month. Both have to be arranged deliberately here, because every other test
    in this module has already spent something.
    """
    phone, free = setup.take(1)[0], setup.take(1)[0]
    provider = setup.CountingLookupProvider()

    with monthly_cap(0.0):
        outcome = lookup_service.line_type_for(db, phone, provider=provider)
        assert provider.calls == []
        assert outcome["skipped"] == "spend_cap"

        untouched = lookup_service.LookupBudget(Decimal("0"), Decimal("0"))
        assert untouched.allows(Decimal("0")) is False, (
            "a cap of zero permitted a free lookup — off became on")

        previous = settings.PROSPECT_LOOKUP_COST_PER_NUMBER
        settings.PROSPECT_LOOKUP_COST_PER_NUMBER = 0.0
        try:
            assert lookup_service.cost_per_lookup() == Decimal("0")
            free_outcome = lookup_service.line_type_for(
                db, free, provider=provider, budget=untouched)
        finally:
            settings.PROSPECT_LOOKUP_COST_PER_NUMBER = previous

    assert provider.calls == [], (
        "a cap of zero permitted unlimited lookups because each one was priced "
        "at nothing")
    assert free_outcome["skipped"] == "spend_cap"


def test_the_budget_counts_what_earlier_runs_already_spent(db):
    """A per-run cap is no cap at all the moment anything runs twice."""
    first, second = setup.take(1)[0], setup.take(1)[0]
    provider = setup.CountingLookupProvider()

    with room_for(db, 1):
        lookup_service.screen(db, [first], provider=provider)
        outcome = lookup_service.screen(db, [second], provider=provider)

    assert provider.calls == [first], (
        "the second pass started from zero and spent past the monthly ceiling")
    assert outcome["skipped"]["spend_cap"] == 1


def test_a_second_billed_attempt_in_the_same_month_is_counted(db):
    """The cap's arithmetic, on the one path that can bill a number twice.

    A row that is retried keeps one `cost` column, so overwriting it would let
    a number billed twice in a month count once — and a ceiling that under-counts
    permits more than it says.
    """
    phone = setup.take(1)[0]
    unreadable = _UnreadableProvider()

    before = lookup_service.spend_this_month(db)
    lookup_service.line_type_for(db, phone, provider=unreadable)
    lookup_service.line_type_for(db, phone, provider=unreadable)

    row = db.query(PhoneLookup).filter(PhoneLookup.phone == phone).one()
    assert row.attempts == 2 and row.status == "error"
    assert Decimal(row.cost) == lookup_service.cost_per_lookup() * 2
    assert lookup_service.spend_this_month(db) - before == (
        lookup_service.cost_per_lookup() * 2)


class _UnreadableProvider(setup.CountingLookupProvider):
    """Bills for the call and answers nothing usable — the `no_classification`
    case, reproduced without the SDK so the cost arithmetic can be asserted."""

    name = "fake-unreadable"

    def lookup(self, phone):
        from app.sms.lookup import LineTypeResult
        self.calls.append(phone)
        return LineTypeResult(line_type="unknown", ok=False,
                              error=telnyx_lookup.NO_CLASSIFICATION,
                              cost=str(lookup_service.cost_per_lookup()))


def test_last_months_spend_does_not_count_against_this_month(db):
    """The cap is monthly, and a ceiling that never resets is a ceiling that
    eventually stops all screening for good.

    Written as a row stamped in a previous month rather than by waiting for one,
    which is the only way to test a calendar boundary in a suite that runs in
    thirty seconds.
    """
    phone = setup.take(1)[0]
    db.add(PhoneLookup(phone=phone, line_type="mobile", status="ok",
                       provider="fake-lookup", attempts=1,
                       looked_up_at="2019-01-15T09:00:00", cost="7.50"))
    db.commit()

    assert lookup_service.spend_this_month(db) < Decimal("7.50"), (
        "a lookup from a previous month is being charged against this month's "
        "cap, which never resets")
    assert lookup_service.spend_this_month(db, month="2019-01") >= Decimal("7.50")


def test_a_pass_reports_what_it_was_charged_and_not_what_it_attempted(db):
    """A timeout is a lookup performed and not a charge.

    `screen()` used to report `rate x performed`, which is the same number
    whenever every call succeeds and wrong on the day they do not. The pass now
    reports the ledger's own delta, so the figure that reaches
    `scrape_jobs.cost` and the figure the monthly ceiling is measured against
    are the same figure.
    """
    phone = setup.take(1)[0]
    before = lookup_service.spend_this_month(db)

    outcome = lookup_service.screen(db, [phone],
                                    provider=setup.FailingLookupProvider())

    assert outcome["performed"] == 1, "precondition: the call was attempted"
    assert Decimal(outcome["cost"]) == Decimal("0"), (
        f"a failed lookup was reported as costing {outcome['cost']}")
    assert lookup_service.spend_this_month(db) == before


def test_a_cost_the_provider_reports_unreadably_does_not_poison_the_ledger(db):
    """A provider is a third party's SDK behind one of our classes.

    Two things must survive a cost string that is not a number: the answer we
    have already paid for, and every later cap check. Raising would discard the
    first — after the money was spent and before the row was written — and
    storing the string raw would break the sum the ceiling is read from, for
    every screening pass from then on.
    """
    phone = setup.take(1)[0]

    class _Chatty(setup.CountingLookupProvider):
        name = "fake-chatty"

        def lookup(self, phone):
            from app.sms.lookup import LineTypeResult
            self.calls.append(phone)
            return LineTypeResult(line_type="mobile", ok=True, cost="$0.0025 USD")

    outcome = lookup_service.line_type_for(db, phone, provider=_Chatty())
    assert outcome["line_type"] == "mobile" and outcome["ok"] is True, (
        "the answer was thrown away over the cost field")

    row = db.query(PhoneLookup).filter(PhoneLookup.phone == phone).one()
    assert row.cost is None, f"{row.cost!r} went into the column the cap sums"
    # And the ledger still reads, which it would not if the raw string were in
    # there — one unreadable row would take every later pass with it.
    assert isinstance(lookup_service.spend_this_month(db), Decimal)


def test_the_month_the_cap_counts_is_the_month_the_writer_stamps(db):
    """One writer owns `looked_up_at`, and the cap's query depends on it.

    `contact_list_members.added_at` is the cautionary tale: a server default put
    a second writer on the column keeping a different clock in a different ISO
    spelling, and every defaulted row read up to five hours newer than it was.
    A monthly sum built on a prefix comparison is exactly what that breaks.
    """
    from datetime import datetime

    assert PhoneLookup.__table__.columns["looked_up_at"].server_default is None, (
        "a server default puts a second writer on this column, and the cap's "
        "monthly filter compares the string the Python writer produces")

    phone = setup.take(1)[0]
    lookup_service.line_type_for(db, phone,
                                 provider=setup.CountingLookupProvider())
    row = db.query(PhoneLookup).filter(PhoneLookup.phone == phone).one()
    assert row.looked_up_at.startswith(datetime.now().strftime("%Y-%m")), (
        f"{row.looked_up_at!r} does not begin with the month key the cap "
        f"filters on, so this row would be invisible to the ceiling")


# ─── Never spend on a number nobody will use ────────────────────────────────

def test_a_rejected_number_is_never_looked_up_by_either_entry_point(db):
    """A human said no. The carrier's answer changes nothing about that, and a
    nightly re-run of the search that keeps finding it would buy the same
    useless answer every night."""
    phone = setup.take(1)[0]
    setup.FakeSource([setup.record(phone)]).ingest(db)
    prospect = db.query(prospect_service.Prospect).filter_by(phone=phone).one()
    prospect_service.reject(db, [prospect.id], reason="seller_or_consignor")

    provider = setup.CountingLookupProvider()
    batch = lookup_service.screen(db, [phone], provider=provider)
    single = lookup_service.line_type_for(db, phone, provider=provider)

    assert provider.calls == [], "a rejected number was paid for"
    assert batch["skipped"]["not_usable"] == 1
    assert single["skipped"] == "not_usable"
    assert db.query(PhoneLookup).filter(PhoneLookup.phone == phone).count() == 0


def test_a_blocklisted_number_is_never_looked_up_by_either_entry_point(db):
    """He can never be texted, so his line type is worth nothing to us."""
    phone = setup.take(1)[0]
    blocklist_service.block_number(db, phone, reason="stop_keyword")

    provider = setup.CountingLookupProvider()
    batch = lookup_service.screen(db, [phone], provider=provider)
    single = lookup_service.line_type_for(db, phone, provider=provider)

    assert provider.calls == [], "a blocklisted number was paid for"
    assert batch["skipped"]["not_usable"] == 1
    assert single["skipped"] == "not_usable"


def test_a_number_whose_line_type_is_already_known_is_never_looked_up_again(db):
    """The cache, asserted from the "we will not use this call" direction.

    A contact or a prospect the table has already answered about is the third
    population we must not pay for, and it is the one that recurs every night on
    an overlapping scrape rather than once.
    """
    known, fresh = setup.take(1)[0], setup.take(1)[0]
    provider = setup.CountingLookupProvider()
    lookup_service.screen(db, [known], provider=provider)
    assert provider.calls == [known]

    outcome = lookup_service.screen(db, [known, fresh], provider=provider)
    assert provider.calls == [known, fresh], "a known number was bought twice"
    assert outcome["cached"] == 1 and outcome["performed"] == 1


def test_the_unusable_set_agrees_with_the_two_services_that_own_the_rules(db):
    """Two statements of one rule, so the test asserts they agree.

    `unusable_numbers()` batches over the tables because it runs on a whole
    scrape, while `prospect_ingest.is_suppressed()` and
    `blocklist_service.is_blocked()` answer about one number — and
    `prospect_ingest` reaches `lookup_service` through `prospect_service`, so
    the reverse import is a cycle rather than a choice. Pinning either list would prove the list; this
    proves the property the second copy exists to preserve.
    """
    rejected, blocked, clean = setup.take(1)[0], setup.take(1)[0], setup.take(1)[0]
    setup.FakeSource([setup.record(rejected)]).ingest(db)
    prospect = db.query(prospect_service.Prospect).filter_by(phone=rejected).one()
    prospect_service.reject(db, [prospect.id], reason="competitor")
    blocklist_service.block_number(db, blocked, reason="stop_keyword")

    everything = [rejected, blocked, clean]
    batched = lookup_service.unusable_numbers(db, everything)
    one_at_a_time = {
        phone for phone in everything
        if prospect_ingest.is_suppressed(db, phone)
        or blocklist_service.is_blocked(db, phone)
    }

    assert batched == one_at_a_time == {rejected, blocked}
    assert db.query(ProspectRejection).filter(
        ProspectRejection.phone == rejected).count() == 1
