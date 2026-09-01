"""Session P1b A2: the assertion that kept failing a sound build.

`tests/_wholesale_scan.py` is code, and a measurement script is code, and three
times now a verification script in this repo has been wrong before the code was.
So the scanner is tested in **both** directions, which is what acceptance
criterion 7 asks for:

  - it does not fire on the microsecond timestamp that produced the flake, and
  - it does fire when a real wholesale figure is put into a declared field.

The second half is the one that matters. A scanner that never fires passes
twenty consecutive runs beautifully and proves nothing at all.
"""

from decimal import Decimal

import pytest

from app.core.config import settings

from tests import _wholesale_scan as scan

# The exact spelling that failed once in six full-suite runs during session P1
# and could not be reproduced in five more: seconds ending in zero, microseconds
# beginning 009. `"0.009"` is inside it, and `0.009` is not.
FLAKY_TIMESTAMP = "2026-08-31T14:23:40.009312"


# ─── It does not fire on things that are not money ──────────────────────────

def test_the_timestamp_that_caused_the_flake_is_not_a_wholesale_figure():
    assert "0.009" in FLAKY_TIMESTAMP, (
        "precondition: this is the substring the old assertion matched")
    scan.assert_no_wholesale_figure(FLAKY_TIMESTAMP, where="a timestamp")
    scan.assert_no_wholesale_field({"sent_at": FLAKY_TIMESTAMP}, where="a row")


def test_the_number_inside_the_timestamp_reads_as_the_number_it_is():
    """The tokeniser, stated as the property the whole fix rests on.

    A clock is removed before tokenising, so a timestamp yields no numbers at
    all — not "forty and change". Both answers are correct about the leak; this
    one is also correct about `00.009000`, which the boundary rule alone reads
    as exactly our rate because that is exactly what it equals.
    """
    assert scan.numbers_in(FLAKY_TIMESTAMP) == []
    assert scan.numbers_in("2026-08-31") == []
    # The boundary rule still does its own job on a number that is not a clock.
    assert scan.numbers_in("id 40.009312 here") == [Decimal("40.009312")]
    assert scan.numbers_in("$0.009") == [Decimal("0.009")]
    # And the leading-zero rule on its own, with no clock around it to be
    # stripped: the second, independent guard against a time arriving in a
    # format `CLOCK_TIME` has never seen. A price is `0.009`; `00.009` is not.
    assert scan.numbers_in("00.009000") == []
    # Trailing zeros are the direction a substring test gets wrong the other
    # way: `"0.009" in "0.0090"` is true, and so is the numeric comparison.
    assert Decimal("0.0090") in scan.numbers_in("rate: 0.0090")


# The one the numeric comparison alone did NOT fix, and the reason
# `agent/accept-P1b.sh` check 7b walks every spelling instead of sampling
# twenty runs: seconds of `00` and microseconds beginning `009` parse as exactly
# 0.009. The boundaries are right and the number really is our rate.
EXACT_TIMESTAMP = "2026-08-31T14:23:00.009000"


def test_the_timestamp_whose_seconds_field_IS_the_rate_is_not_a_price():
    assert Decimal("00.009000") == Decimal("0.009"), (
        "precondition: this is a numeric equality, not a substring accident — "
        "no boundary rule can tell these apart")
    scan.assert_no_wholesale_figure(EXACT_TIMESTAMP, where="a timestamp")
    scan.assert_no_wholesale_field({"sent_at": EXACT_TIMESTAMP}, where="a row")


def test_a_rate_in_compactly_serialised_json_is_still_caught():
    """FastAPI serialises without spaces, so a real leak reads `"rate":0.009`.

    A scanner that skipped anything following a colon — the obvious way to
    dismiss a clock — would miss every leak in every JSON body this app
    returns.
    """
    with pytest.raises(AssertionError):
        scan.assert_no_wholesale_figure(
            '{"segments":12,"rate":0.009}', where="a compact payload")


@pytest.mark.parametrize("body", [
    '{"segments": 1223, "cost": 18.35, "sent_at": "2026-08-31T14:23:40.009312"}',
    '{"created_at":"2026-08-31T14:23:00.009000","completed_at":"2026-08-31T09:00:00.0043"}',
    "Campaign sent. 4,623 segments at your rate. 0.05% of the list clicked.",
    "phone,segments,cost\n+15555550100,2,0.03\n",
    # A round number that is not money, which is why the cap is excluded from
    # the value sweep by name in `_wholesale_scan`.
    '{"batch_size": 50, "included_segments": 10000}',
])
def test_ordinary_client_facing_bodies_are_clean(body):
    scan.assert_no_wholesale_figure(body, where="a synthetic body")


# ─── It does fire when the real figure is rendered ──────────────────────────

def test_a_wholesale_figure_in_a_declared_field_is_caught():
    """Injected as a number, in a field, which is how it would actually leak."""
    leaky = {"campaign": {"segments": 12,
                          "rate_per_segment": settings.WHOLESALE_COST_PER_SEGMENT}}
    with pytest.raises(AssertionError) as raised:
        scan.assert_no_wholesale_field(leaky, where="/api/reports/campaigns/1")
    assert "rate_per_segment" in str(raised.value)
    assert "/api/reports/campaigns/1" in str(raised.value)


def test_a_wholesale_figure_rendered_into_a_page_is_caught():
    page = f"<td>Cost</td><td>${settings.WHOLESALE_COST_PER_SEGMENT} / segment</td>"
    with pytest.raises(AssertionError):
        scan.assert_no_wholesale_figure(page, where="/history")


def test_a_wholesale_figure_inside_a_string_field_is_caught():
    """The leak arrives pre-formatted more often than not — an f-string in a
    template, a label assembled in JavaScript. A field-name check alone would
    miss every one of those."""
    with pytest.raises(AssertionError):
        scan.assert_no_wholesale_field(
            {"detail": f"You are charged ${settings.WHOLESALE_COST_PER_SEGMENT} "
                       f"per segment."},
            where="a refusal")


def test_our_screening_cost_is_scanned_for_as_well():
    """P1b put a second wholesale figure into the product. A scanner that only
    knew about the first one would go green over it."""
    with pytest.raises(AssertionError):
        scan.assert_no_wholesale_field(
            {"lookup": {"cost": settings.PROSPECT_LOOKUP_COST_PER_NUMBER}},
            where="/api/prospects")


def test_a_field_named_for_our_rate_is_caught_before_it_has_a_value():
    with pytest.raises(AssertionError):
        scan.assert_no_wholesale_field({"wholesale_rate": None}, where="a payload")


def test_every_figure_the_scan_knows_about_is_actually_caught():
    """The list is the point of the module, so nothing in it may be inert.

    Asserted by running each figure through the scanner rather than by reading
    the list — five parametrized wordings once stood in for three proofs in this
    repo because nobody checked that each sample actually exercised the rule.
    """
    for label, figure in scan.wholesale_figures().items():
        with pytest.raises(AssertionError):
            scan.assert_no_wholesale_figure(f"total: ${figure}", where=label)
