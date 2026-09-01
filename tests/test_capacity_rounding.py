"""5h A2: the capacity guard compares exact money, and rounds only to display.

`wholesale_estimate()` rounded to cents, and the pre-flight capacity check
compared that rounded figure against the carrier balance. Two consequences,
which is why both are tested rather than only the headline one:

  **The guard can round itself to nothing.** Below half a cent a segment, a
  small send needs `$0.00` — and "the account must hold at least nothing" is
  satisfied by an empty account. The safeguard CLAUDE.md calls the most valuable
  in the codebase, the one that turns "we lost 4,623 messages mid-blast" into
  "the campaign refused to start", passes cleanly on a dead balance.

  **At the rate this box actually runs it is loose rather than inert.** At
  `WHOLESALE_COST_PER_SEGMENT=0.009` a one-segment send rounds *up*, to $0.01,
  and already refuses on a zero balance. Six segments is the first count that
  rounds down: $0.054 becomes $0.05, so the check asks for $0.075 against a true
  $0.081 and a campaign starts on a balance three quarters of a cent short of
  covering it. Small, and not a threshold anybody chose.

Both are the same defect and the fix is one line of arithmetic: compare
`Decimal`, round at the edge. `tests/test_billing.py` makes the identical
argument about the client's invoice, which is where this lesson was learned the
first time (session 1b, `cost_for_segments()`).

Nothing here sends anything. The provider is a stub with a fixed balance and a
`send()` that raises, so a regression that reaches the carrier fails loudly
rather than quietly costing money.
"""

import asyncio
from decimal import Decimal

import pytest

from app.core.config import settings
from app.core.database import SessionLocal
from app.services.campaign_builder import wholesale_cost, wholesale_estimate
from app.services.campaign_service import CampaignService

from tests import _wholesale_scan as scan

# The blended rate at which the guard rounds itself to zero. Not this box's
# setting — `.env`, `.env.example` and the code default all carry 0.009 — but
# `WHOLESALE_COST_PER_SEGMENT` is one line of `.env`, it is a *blended* rate
# that moves whenever the carrier mix does, and anything under half a cent puts
# the check back in the state below. The fix does not depend on knowing which
# side of $0.005 the rate is on, and that is the point of testing at both.
SUB_HALF_CENT = 0.004


class _Balance:
    """A provider with a fixed balance that must never be asked to send."""

    name = "console"

    def __init__(self, balance):
        self.balance = balance

    async def get_balance(self):
        return self.balance

    async def send(self, to, text):          # pragma: no cover — must not run
        raise AssertionError("the capacity check is a pre-flight, not a send")


def _assessment(balance, segments, *, rate=None):
    """`capacity_assessment()` against a fixed balance, at an optional rate.

    The cost argument is `wholesale_estimate(segments)` — the rounded figure,
    because that is what both real callers pass: `preflight()` hands over
    `campaigns.estimated_cost`, which is stored rounded, and a top-up computes
    the same function over its own segments.
    """
    db = SessionLocal()
    try:
        service = CampaignService(db)
        service.provider = _Balance(balance)
        if rate is None:
            return asyncio.run(service.capacity_assessment(
                segments, wholesale_estimate(segments)))
        original = settings.WHOLESALE_COST_PER_SEGMENT
        settings.WHOLESALE_COST_PER_SEGMENT = rate
        try:
            return asyncio.run(service.capacity_assessment(
                segments, wholesale_estimate(segments)))
        finally:
            settings.WHOLESALE_COST_PER_SEGMENT = original
    finally:
        db.close()


def _pre_fix_verdict(balance, segments, rate):
    """What the check answered before 5h, in one line, at `rate`.

    The arithmetic exactly as it stood before this session:
    `wholesale_estimate()` was `round(segments * rate, 2)` — Python's *banker's*
    round on a float — and `capacity_assessment()` compared `balance >= needed *
    1.5` as floats.

    **Do not "correct" this to call `wholesale_estimate()`.** That function is
    part of what changed: it rounds half-up in Decimal now, which disagrees with
    `round()` on every count that lands on a half-cent (n=5 at 0.009 is the
    first — 0.04 then, 0.05 now). Calling it here would compare the fix against
    itself and quietly weaken every "before" assertion below. This session's
    reviewer read it the other way round, which is the argument for the comment
    rather than against the code.
    """
    needed = round(segments * rate, 2)
    return balance >= needed * 1.5


# ─── Criterion 6: a one-segment send on a zero balance ──────────────────────

def test_a_one_segment_send_on_an_empty_account_is_refused():
    """The headline case, at a rate where the rounding reaches zero.

    Both halves are executed: the pre-fix arithmetic *passes* on a zero balance
    — which is the defect, stated as a running assertion rather than a claim —
    and the shipped check refuses.
    """
    assert _pre_fix_verdict(0.0, 1, SUB_HALF_CENT), (
        "the defect does not reproduce at this rate, so the test below proves "
        "nothing about the fix")
    assert round(1 * SUB_HALF_CENT, 2) == 0.0, "the requirement rounded to nothing"

    verdict = _assessment(0.0, 1, rate=SUB_HALF_CENT)
    assert verdict["checked"] is True
    assert verdict["ok"] is False, (
        "a send on an empty account passed the check that exists to stop it")


def test_the_shipped_rate_refuses_a_one_segment_send_on_an_empty_account():
    """At 0.009 this case was already refused, and the criterion should say so.

    `round(0.009, 2)` is `0.01`, so the one-segment requirement rounds *up* and
    a zero balance never satisfied it. Stated as its own test because the
    session spec describes the zero case as the live defect, and a criterion
    that quietly passed for a different reason than the one it names is how a
    fix gets credited with work it did not do. What is actually wrong at this
    rate is the test below.
    """
    assert settings.WHOLESALE_COST_PER_SEGMENT == 0.009
    assert _pre_fix_verdict(0.0, 1, 0.009) is False
    assert _assessment(0.0, 1)["ok"] is False


def test_the_shipped_rate_no_longer_starts_a_send_the_balance_cannot_cover():
    """Six segments: $0.054 rounds to $0.05, and $0.075 is not $0.081.

    The same defect at the rate this box runs. A balance of $0.078 covered the
    rounded requirement and not the real one, so the campaign started — which is
    what the guard exists to prevent, at a size nobody would ever notice on an
    invoice.
    """
    assert _pre_fix_verdict(0.078, 6, 0.009), "the defect does not reproduce"
    assert _assessment(0.078, 6)["ok"] is False
    # And one cent more genuinely covers it, so this is a tightening rather
    # than a check that now refuses everything.
    assert _assessment(0.088, 6)["ok"] is True


@pytest.mark.parametrize("segments", [1, 2, 3, 5, 6, 7, 11, 100, 6857])
def test_the_requirement_is_never_below_the_exact_cost(segments):
    """The property, swept: the guard asks for at least what the send costs.

    Asserted as a property rather than by re-deriving the arithmetic the code
    uses — that would only prove the code agrees with itself. The exact
    requirement is segments × rate × the 1.5 margin, in Decimal, and a balance
    one cent under it must refuse. The upper assertion leaves two cents of room
    because the requirement is allowed to be *higher* than the exact figure —
    see the monotonicity test below, which is why.
    """
    rate = Decimal(str(settings.WHOLESALE_COST_PER_SEGMENT))
    exact_required = segments * rate * Decimal("1.5")

    just_under = float(exact_required - Decimal("0.01"))
    assert _assessment(just_under, segments)["ok"] is False, (
        f"{segments} segments started on ${just_under}, one cent under the "
        f"${exact_required} they need")
    assert _assessment(float(exact_required + Decimal("0.02")), segments)["ok"] is True


@pytest.mark.parametrize("segments", [1, 2, 3, 4, 5, 6, 7, 11, 100, 1000, 6857])
def test_the_fix_only_ever_tightens(segments):
    """Nothing the old check refused may now start.

    The pre-flight capacity check is escalation item 3 — never weakened, never
    bypassed, never advisory — so "we made it exact" is not on its own an
    acceptable description of a change to it. Exact is not the same as stricter:
    at every count where the estimate rounded *up*, the old requirement was
    above the true cost, and a fix that simply replaced it with the true cost
    would have made the guard more permissive on roughly half of all sends.

    That is why the requirement is the larger of the exact cost and the caller's
    own stated one. This test is the proof, run against the pre-fix arithmetic
    rather than argued: at the balance the old check refused, the new one
    refuses too.
    """
    rate = settings.WHOLESALE_COST_PER_SEGMENT
    old_required = round(segments * rate, 2) * 1.5
    just_under_old = max(0.0, old_required - 0.005)

    assert _pre_fix_verdict(just_under_old, segments, rate) is False
    assert _assessment(just_under_old, segments)["ok"] is False, (
        f"{segments} segments now start on ${just_under_old}, which the check "
        f"refused before this session")


# ─── Rounding is for display, and only for display ──────────────────────────

def test_the_exact_cost_is_decimal_and_the_estimate_is_the_rounded_one():
    """Two functions, and which one a caller reaches for is the whole fix.

    `wholesale_cost()` is what a comparison uses; `wholesale_estimate()` is what
    a Float column and a log line use. At 0.009 the difference shows on the
    first segment: $0.009 exactly, $0.01 rounded.
    """
    assert isinstance(wholesale_cost(1), Decimal)
    assert wholesale_cost(1) == Decimal("0.009")
    assert wholesale_estimate(1) == 0.01
    assert wholesale_cost(0) == Decimal("0")
    # Never negative, and never a float that has been through n * rate.
    assert wholesale_cost(-5) == Decimal("0")
    assert wholesale_cost(1000) == Decimal("9.000")


def test_a_callers_higher_stated_cost_still_binds():
    """The change can only tighten, including when the two arguments disagree.

    `capacity_assessment()` takes both segments and a cost, and normally they
    agree to within a rounding step. If a future caller's cost is higher than
    the segments imply, that is the figure the guard has to honour — deriving
    the requirement from segments alone would silently discard it and make this
    fix a weakening on that path.
    """
    assert _assessment(0.5, 1)["ok"] is True
    db = SessionLocal()
    try:
        service = CampaignService(db)
        service.provider = _Balance(0.5)
        verdict = asyncio.run(service.capacity_assessment(1, 10.00))
        assert verdict["ok"] is False, "the stated cost was thrown away"
    finally:
        db.close()


# ─── The wording is unchanged, and stays in segments ────────────────────────

def test_the_refusal_is_still_denominated_in_segments_and_names_no_money():
    """A2 changes the arithmetic, not the sentence — 5d established the wording.

    `detail` is stored as the campaign's `abort_reason` and rendered straight
    into the client's campaign rail. He is billed in segments and reads the
    answer in segments; the dollar view goes to our log, where only we see it,
    and the wholesale rate must never reach him at all.
    """
    verdict = _assessment(0.0, 6)
    assert verdict["ok"] is False
    detail = verdict["detail"]
    assert "segments" in detail
    assert "$" not in detail
    # Numbers compared as numbers. This one reads a short sentence rather than a
    # response body, so it was never going to catch a timestamp — but the audit
    # in session P1b converted every site of the shape rather than leaving three
    # spellings of one assertion for the next person to choose between.
    scan.assert_no_wholesale_figure(detail, where="the capacity refusal")
    assert "Nothing was sent." in detail
    # The money view is present for the caller's log, and only there.
    assert verdict["required"] > 0
