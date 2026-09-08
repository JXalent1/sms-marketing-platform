"""Do this repo and Stripe's dashboard still agree about the allowance?

`BILLING_SEGMENTS_INCLUDED` lives here, in version control. The tier boundary
that applies it lives in Stripe's dashboard, where nothing tracks it. Two
definitions of one number is the defect this codebase has hit five times —
`SENT_STATUSES` twice, the opt-out definition, the suppression window, the
freshness set — and this is the first one where being wrong produces a
**mispriced invoice**, which is the single artefact the client audits.

Split out of `stripe_meter.py` on the 500-line rule, along the boundary the two
already had: that module owns what Stripe is *told* about usage, and this one
owns whether Stripe is *configured* to price it the way we are. It imports from
`stripe_billing` and nothing imports back.

## Five states, and the field says which

`agree` / `disagree` are the two the check exists to distinguish. `unavailable`
is a Stripe outage, which is neither — a degraded answer that reads as a chosen
one is the defect `send_mode()` exists to prevent, one subsystem along.
`never_checked` is a configured box nobody has run it on, and it is **not** ok:
a check nobody has run is indistinguishable from one that would fail. `stale` is
an agreement nobody has re-checked inside `TIER_CHECK_MAX_AGE_DAYS` — a verdict
is a claim about a price at a moment, and reporting September's answer in March
is the same defect with time as the degradation.

## `/health` gets the state, not the figures

The issues below name this account's allowance and per-segment rate. `/health`
has no login and no rate limit; `public_pricing_issues()` is what it renders,
and it says which state without saying what the numbers are. The figures go to
the log at ERROR and to the two authenticated billing routes. `config_issues`
sets that precedent one field up.
"""

import logging
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.app_setting import get_setting, set_setting
from app.services import stripe_billing

logger = logging.getLogger("billing.stripe")

# The stored verdict of the last tier-drift check. `/health` reads this row
# rather than calling Stripe, for two reasons: an uptime monitor polling
# /health must not become a Stripe request per poll, and every field /health
# gains is a new way to fail the endpoint `deployment/deploy.sh` rolls back on.
TIER_CHECK_KEY = "stripe_tier_check"

TIER_STATES = ("agree", "disagree", "unavailable", "never_checked", "stale",
               "not_configured")

# How long a tier verdict is treated as still true.
#
# Not a commercial term and not in `.env`: it is a statement about how often a
# price can change without anybody noticing, and the answer is "not for a week".
# The first version of this session reported a verdict of any age as current, so
# a tier edited in Stripe's dashboard six months after checkout would never have
# been detected — the exact failure A2 exists to prevent, arrived at through the
# check rather than through its absence. `check_tier_drift()` runs daily on the
# scheduler; this is what makes a box whose scheduler died say so instead of
# quoting the answer it got in September.
TIER_CHECK_MAX_AGE_DAYS = 7

# ─── A2: the allowance lives in two systems, so make them prove they agree ───


def _cents(value) -> Optional[Decimal]:
    """A Stripe amount as an exact Decimal number of cents, or None."""
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError):
        return None


def compare_tiers(price) -> dict:
    """Does the Stripe price still price the plan this repo is configured for?

    Two assertions, both from `sessions/session-B1.md` A2:

      * the first tier's `up_to` equals `BILLING_SEGMENTS_INCLUDED`
      * the second tier's per-unit price equals `BILLING_PRICE_PER_SEGMENT`

    The second is read from `unit_amount_decimal`, not `unit_amount`, and that
    is not a style choice: `unit_amount` is an **integer** number of cents and
    $0.015 is one and a half of them, so this client's actual rate is not
    representable in the field the spec names. A check written against
    `unit_amount` would compare 0.015 against 2 and report disagreement on a
    correctly configured price — or, worse, be "fixed" by rounding until it
    agreed. `unit_amount` is still read as the fallback for a whole-cent rate,
    which is what a future client's plan is most likely to be.

    Money is compared in `Decimal` end to end, for `cost_for_segments()`'s
    reason: `0.015 * 100` is 1.4999999999999998 in binary floating point.
    """
    issues = []
    tiers = list(getattr(price, "tiers", None) or [])
    if len(tiers) < 2:
        return {"state": "disagree", "issues": [
            f"The Stripe price has {len(tiers)} tier(s). The plan needs two: "
            f"{settings.BILLING_SEGMENTS_INCLUDED:,} included, then a rate."]}

    included = getattr(tiers[0], "up_to", None)
    if included is None and isinstance(tiers[0], dict):
        included = tiers[0].get("up_to")
    if included != settings.BILLING_SEGMENTS_INCLUDED:
        issues.append(
            f"Stripe's first tier includes {included} segments; this account is "
            f"configured for {settings.BILLING_SEGMENTS_INCLUDED}.")

    second = tiers[1] if not isinstance(tiers[1], dict) else _AsObject(tiers[1])
    rate_cents = _cents(getattr(second, "unit_amount_decimal", None))
    if rate_cents is None:
        rate_cents = _cents(getattr(second, "unit_amount", None))
    expected_cents = Decimal(str(settings.BILLING_PRICE_PER_SEGMENT)) * 100
    if rate_cents is None or rate_cents != expected_cents:
        # Trailing zeros trimmed for the sentence only, never for the
        # comparison: `Decimal("0.015") * 100` is `1.500`, and an operator
        # reading "configured for 1.500 cents" in an alert is being asked to
        # parse Decimal's exponent bookkeeping. `.normalize()` is not used —
        # it renders 200 cents as `2E+2`.
        issues.append(
            f"Stripe's per-segment rate is {_plain(rate_cents)} cents; this "
            f"account is configured for {_plain(expected_cents)} cents.")

    return {"state": "disagree" if issues else "agree", "issues": issues}


def _plain(value) -> str:
    """A Decimal for a human: no exponent, no trailing zeros, no lost digits."""
    if value is None:
        return "not set"
    text = f"{value:f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


class _AsObject:
    """Attribute access over a plain dict, so one comparison serves both shapes."""

    def __init__(self, data: dict):
        self.__dict__.update(data)


def check_tier_drift(db: Session) -> dict:
    """Run the comparison against Stripe now and store the verdict.

    On demand, not on a timer and not on `/health`: this is a Stripe API call,
    and an endpoint an uptime monitor polls every minute must not turn into a
    Stripe request every minute. `/health` reports the stored row.

    A Stripe outage is `unavailable`, which is neither agreement nor
    disagreement and must not be reported as either — a degraded answer that
    looks like a chosen one is the defect `send_mode()` exists to prevent, one
    subsystem along. Three states, and the field names which.
    """
    if not stripe_billing.configured():
        return _store_verdict(db, "not_configured", [])
    try:
        price = stripe_billing.api().retrieve_price(settings.STRIPE_PRICE_METERED)
    except Exception as exc:
        logger.error("Could not read the metered price from Stripe: %s", exc)
        return _store_verdict(db, "unavailable", [
            "The Stripe price could not be read, so the plan has not been "
            "verified against it."])
    outcome = compare_tiers(price)
    if outcome["issues"]:
        # Loudly, and in the log the operator actually reads: an .env edit that
        # never reached Stripe is a mispriced invoice, and an invoice is the one
        # artefact the client audits.
        for issue in outcome["issues"]:
            logger.error("BILLING TIER DRIFT: %s", issue)
    return _store_verdict(db, outcome["state"], outcome["issues"])


def _store_verdict(db: Session, state: str, issues: list) -> dict:
    # A plain `raise`, not `assert`: assertions are stripped under `python -O`,
    # and this one is the only thing keeping an unknown state out of a field
    # `/health` publishes.
    if state not in TIER_STATES:
        raise ValueError(f"unknown tier-check state {state!r}")
    verdict = {"state": state, "issues": issues,
               "checked_at": datetime.now().isoformat(timespec="seconds")}
    set_setting(db, TIER_CHECK_KEY, stripe_billing.dumps(verdict),
                "Last comparison of the Stripe price tiers against the plan")
    return verdict


def tier_verdict() -> dict:
    """The stored verdict, for `/health`. Opens its own session and never raises.

    Its own session, without `Depends(get_db)`, for the reason `/health` reads
    the configuration alerts that way: a dependency that raises means the
    handler never runs, and a 503 there rolls back every deploy including the
    one that fixes it. A database this endpoint cannot reach costs the pricing
    signal and nothing else.
    """
    from app.core.database import SessionLocal
    db = None
    try:
        db = SessionLocal()
        stored = stripe_billing.loads(get_setting(db, TIER_CHECK_KEY))
        if not stored:
            # Configured and never checked is *not* ok. A check nobody has run
            # is indistinguishable from a check that would fail, and this one
            # guards an invoice.
            return {"state": "not_configured" if not stripe_billing.configured()
                    else "never_checked",
                    "issues": ([] if not stripe_billing.configured() else
                               ["The Stripe price has never been checked against "
                                "this account's plan."]),
                    "checked_at": None}
        return _aged(stored)
    except Exception as exc:                    # pragma: no cover — defensive
        logger.error("Could not read the stored tier verdict: %s", exc)
        return {"state": "unavailable", "issues": [], "checked_at": None}
    finally:
        if db is not None:
            db.close()


def daily_tier_check() -> dict:
    """The scheduler's entry point. Owns its own session and never raises.

    Its own session for `campaign_dispatch`'s reason — an APScheduler job has no
    request to borrow one from — and in a `finally` with every return below it.
    It cannot raise, because an exception out of a scheduled job is a job
    APScheduler stops running, and the failure would be a check that quietly
    stopped checking.
    """
    from app.core.database import SessionLocal
    db = None
    try:
        db = SessionLocal()
        verdict = check_tier_drift(db)
        logger.info("Daily tier check: %s", verdict["state"])
        return verdict
    except Exception as exc:
        logger.error("Daily tier check failed: %s", exc)
        return {"state": "unavailable", "issues": [], "checked_at": None}
    finally:
        if db is not None:
            db.close()


def _aged(verdict: dict) -> dict:
    """Turn an agreement nobody has re-checked lately into its own state.

    A verdict is a claim about a price at a moment. Reporting September's answer
    in March is the "degraded fallback described as a chosen one" defect with
    time as the degradation: the field says `agree`, the price has moved, and
    nothing on any screen distinguishes the two. Only agreement goes stale —
    `disagree` is still true until somebody fixes it, and the other states carry
    their own meaning.
    """
    if verdict.get("state") != "agree" or not verdict.get("checked_at"):
        return verdict
    try:
        checked = datetime.fromisoformat(verdict["checked_at"])
    except (TypeError, ValueError):
        return verdict
    if datetime.now() - checked <= timedelta(days=TIER_CHECK_MAX_AGE_DAYS):
        return verdict
    aged = dict(verdict)
    aged["state"] = "stale"
    aged["issues"] = [f"The Stripe price has not been checked against this "
                      f"account's plan since {verdict['checked_at']}."]
    return aged


def pricing_ok(verdict: dict) -> bool:
    """One definition of "is the pricing sound", so /health and the page agree."""
    return verdict.get("state") in ("agree", "not_configured")


# What an unauthenticated caller is told, per state. Deliberately generic.
#
# The detailed issues name this account's allowance and rate — "Stripe's first
# tier includes 5000 segments; this account is configured for 10000" — and
# `/health` has no login, no rate limit and is the endpoint an uptime monitor
# and every scanner on the internet reaches. Publishing the commercial terms
# there is a new disclosure, not a continuation: `config_issues`, the precedent
# beside it, carries fixed generic wordings from
# `compliance.CONFIGURATION_ALERT_DETAIL` and no figures of this account's.
#
# The figures still exist, loudly, everywhere they are useful and nowhere else:
# in the log at ERROR, and in the authenticated `POST /api/billing/tier-check`
# and `GET /api/billing/status`. A2 asked for the state in a field, and the
# state is what a monitor pages on.
PUBLIC_PRICING_DETAIL = {
    "disagree": "The Stripe price does not match this account's configured plan.",
    "unavailable": "The Stripe price could not be read, so the plan has not "
                   "been verified against it.",
    "never_checked": "The Stripe price has never been checked against this "
                     "account's plan.",
    "stale": "The Stripe price has not been checked against this account's "
             "plan recently.",
}


def public_pricing_issues(verdict: dict) -> list:
    """`/health`'s wording: says which state, names no figure of his."""
    detail = PUBLIC_PRICING_DETAIL.get(verdict.get("state"))
    return [detail] if detail else []
