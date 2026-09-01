"""One way to ask "did our own cost reach the client", and it parses first.

Not a test module — the leading underscore keeps pytest from collecting it. Its
own behaviour is asserted in `tests/test_wholesale_scan.py`, in both directions.

## Why this exists

`assert str(settings.WHOLESALE_COST_PER_SEGMENT) not in body` looks for the
bare string `"0.009"` in an entire serialized response, and the ISO timestamp
`...T14:23:40.009312` contains it. That is one failure in roughly six full-suite
runs, on a gate that runs `--maxfail=1` — so a sound build bounces an unattended
agent onto a defect that does not exist. A gate that flakes is worse than one
that fails loudly.

It is also the **third** appearance of one defect class in this repo, and the
first two each cost a session:

  - `"21610"`, a carrier error code, matched a Brevard County phone number that
    an error string merely quoted — and auto-blocked a different, innocent
    number.
  - `\b(?:telnyx|…)\b` let `TelnyxError` through, because an SDK glues the name
    to a word and the trailing word boundary fails.
  - `"0.009"` matches inside a microsecond timestamp.

The pattern: **a value that is meaningful in one field is noise in another, and
a substring sweep over a whole body cannot tell them apart.**

## What this does instead

Numbers are found as numbers and compared as numbers. `numbers_in()` tokenises
decimals with the digit/period boundaries a serialized body actually has, so
`40.009312` is read as one number — forty and change — rather than as a `0.009`
sitting inside it. `0.0090` and `0.009` compare equal, which a substring test
gets wrong in the other direction.

Parsing numerically is necessary and not sufficient, and finding out took a
check written for exactly this purpose. `14:23:00.009000` is the same timestamp
family, and its seconds field parses as **precisely** our rate — the boundaries
are right and the number really is 0.009. So clock times are removed before
anything is tokenised, and the tokeniser separately refuses an integer part with
a redundant leading zero. A price is `0.009`; `00.009` is a clock.

For anything that declares its fields — JSON — `assert_no_wholesale_field()`
walks the parsed structure instead, which is the assertion with the strongest
claim: no *value* in any declared field is our rate.

## What is deliberately not scanned

`PROSPECT_LOOKUP_MONTHLY_CAP` defaults to `50.0`, and a bare 50 is a message
count, a page size, a percentage and a contact id. Sweeping for it would
reproduce exactly the defect this module exists to remove, on a figure that is
never rendered as a price anyway. Round numbers are asserted by field name, not
by value.
"""

import re
from decimal import Decimal, InvalidOperation

from app.core.config import settings

# A clock time, removed before anything is tokenised. Every false positive this
# module exists to prevent has come from one: `14:23:40.009312` and, worse,
# `14:23:00.009000`, whose seconds field parses as *exactly* 0.009 once the
# redundant leading zero is dropped. That one is not a boundary problem — the
# number really is our rate — so no lookbehind can fix it and the structure has
# to go first. Anything a clock is not keeps the ordinary rule below.
CLOCK_TIME = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?")

# A decimal, bounded so that a longer number is read whole rather than sliced.
# The lookbehind keeps `40.009312` from reading as `0.009`: the digit before it
# means the token starts earlier, and the token that does start earlier parses
# as 40.009312. The integer part refuses a redundant leading zero, because a
# rendered price is `0.009` or `12.34` and never `00.009` — that spelling is a
# clock or a date component, and this is the second, independent guard against
# one arriving in a format `CLOCK_TIME` has never seen.
#
# A colon is deliberately *not* excluded: FastAPI serialises JSON compactly, so
# a real leak reads `{"rate":0.009}` with no space, and a rule that skipped
# anything after a colon would miss every one of them.
DECIMAL = re.compile(r"(?<![\d.])(?:0|[1-9]\d*)\.\d+")

# The carrier's own per-message price, as it appears in the suite's costing
# stub. Not a setting — it arrives from the carrier on a send — but it is our
# side of the trade and it is not his number either.
CARRIER_PRICE = Decimal("0.0043")


def wholesale_figures() -> dict:
    """{what it is: Decimal} for every figure that is ours and not his.

    Read from settings rather than pinned, so a rate change in `.env` moves the
    assertion with it. A test that pinned the literal would go green the day the
    figure changed — the failure mode
    `test_the_opt_out_definition_matches_the_dashboard_tile` had.
    """
    return {
        "WHOLESALE_COST_PER_SEGMENT (our per-segment cost)":
            Decimal(str(settings.WHOLESALE_COST_PER_SEGMENT)),
        "PROSPECT_LOOKUP_COST_PER_NUMBER (our per-lookup cost)":
            Decimal(str(settings.PROSPECT_LOOKUP_COST_PER_NUMBER)),
        "the carrier's own per-message price": CARRIER_PRICE,
    }


def numbers_in(text: str) -> list:
    """Every decimal number in `text`, parsed. Integers are not tokens here.

    Only decimals, because every figure this module hunts for is a fraction of
    a cent and because whole numbers are counts — the round-number problem the
    module docstring refuses to walk into. Clock times are removed first; see
    `CLOCK_TIME`.
    """
    found = []
    for token in DECIMAL.findall(CLOCK_TIME.sub(" ", text or "")):
        try:
            found.append(Decimal(token))
        except InvalidOperation:            # pragma: no cover — regex-bounded
            pass
    return found


def assert_no_wholesale_figure(text: str, where: str = "") -> None:
    """No number in `text` is one of our own costs. For HTML, CSV, plain text.

    Used where there are no declared fields to read. It is still a comparison of
    parsed numbers rather than of substrings, which is the whole point.
    """
    present = set(numbers_in(text))
    for label, figure in wholesale_figures().items():
        assert figure not in present, (
            f"{where or 'the response'} renders {figure} — {label}. That is our "
            f"cost, not his: it discloses our margin and under-states his bill.")


def _walk(node, path="$"):
    """(path, value) for every leaf in a parsed JSON structure."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _walk(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _walk(value, f"{path}[{index}]")
    else:
        yield path, node


def assert_no_wholesale_field(payload, where: str = "") -> None:
    """No *declared field* of a parsed response carries one of our costs.

    Numbers are compared numerically; strings are tokenised the same way the
    text scan tokenises a body, so a rate rendered into a string field is caught
    and a timestamp in one is not. Field names are checked separately, because
    `{"wholesale_rate": null}` is a leak waiting for a value.
    """
    figures = wholesale_figures()
    for path, value in _walk(payload):
        assert "wholesale" not in path.lower(), (
            f"{where or 'the response'} declares {path}, and our wholesale "
            f"figures are not his to see")
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, (int, float)):
            candidates = [Decimal(str(value))]
        elif isinstance(value, str):
            candidates = numbers_in(value)
        else:                                # pragma: no cover — JSON leaves only
            continue
        for label, figure in figures.items():
            assert figure not in candidates, (
                f"{where or 'the response'} carries {figure} at {path} — "
                f"{label}. That is our cost, not his.")
