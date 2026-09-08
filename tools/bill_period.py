#!/usr/bin/env python
"""Bill a closed period by hand, from the app's own arithmetic.

    python tools/bill_period.py --start 2026-08-01 --end 2026-08-31
    python tools/bill_period.py --start 2026-08-01 --end 2026-08-31 --create
    python tools/bill_period.py --start 2026-08-01 --end 2026-08-31 --create --charge

Dry run by default. `--create` drafts an invoice; `--charge` finalises it, which
is the step that puts it in front of the client.

## Why it does not do its own arithmetic

The total comes from `billing_service.compute_usage()` and
`cost_for_segments()` — **the same two functions `/usage` renders from**. A tool
that re-implemented the plan would eventually disagree with the dashboard, and
the client would be looking at both. This is the same rule that keeps
`SENT_STATUSES`, the opt-out definition and the suppression window in one place
each; a back-bill is just the loudest place for two definitions to differ.

Its dry run over August 2026 must print **$270.03** — 28,002 segments, less the
10,000 included, at $0.015. If it prints something else, stop: the figure came
from the app's own math and a mismatch means one of the two is wrong. Adjusting
the tool until it agrees would be fixing the thermometer.

## The window is not checked by the arithmetic, so it is checked here

`cost_for_segments()` subtracts the 10,000-segment allowance from **whatever
window it is handed**, because that is the only thing it can do — it prices a
cycle and has no way to know whether it was given one. Hand it half of July and
it gives that half a fresh allowance:

    2026-07-01 .. 2026-07-31   18,000 segments   ->  $120.00
    2026-07-01 .. 2026-07-15    9,000 segments   ->    $0.00
    2026-07-16 .. 2026-07-31    9,000 segments   ->    $0.00

Two invocations, $120 given away, and every figure on both is correct. So the
tool computes the billing cycle containing `--start` and says loudly when the
window is not that cycle. It warns rather than refuses, and the reason is who is
holding the mouse: `decisions/002` refuses on the composer because the operator
there is the *client*, and a back-bill is run by us, from a terminal, after
reading a dry run. A refusal here would also make a deliberate partial period
impossible, and there are legitimate ones.

## Running it twice does not charge twice

The idempotency key is derived from the period, so a second run inside Stripe's
24-hour idempotency window returns the invoice the first one created rather than
raising a second. Past that window a second `--create` really would draft a
second invoice, so both invoice and line item carry the period in `metadata` —
which is what makes a duplicate findable in the dashboard rather than merely
unlikely. Read the drafts before `--charge`; that is what the two flags are for.
"""

import argparse
import os
import sys
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings                            # noqa: E402
from app.core.database import SessionLocal                      # noqa: E402
from app.services import stripe_billing, stripe_meter          # noqa: E402

PERIOD_METADATA_KEY = "back_billed_period"


def idempotency_key(start: date, end: date) -> str:
    """One key per period, so a re-run is the same request and not a second one."""
    return f"a4a-backbill-{start.isoformat()}-{end.isoformat()}"


def period_label(start: date, end: date) -> str:
    return f"Text platform usage, {start.isoformat()} to {end.isoformat()}"


def window_warning(db, start: date, end: date) -> str:
    """A loud line when the window is not exactly one billing cycle, else "".

    Computed with `billing_service`'s own cycle function and the anchor actually
    in force, so it moves with the client's subscription rather than sitting on
    `BILLING_CYCLE_DAY`.

    No ASCII box. A frame drawn with padding arithmetic is a second thing to
    keep true, and the first version of this got its own alignment wrong while
    the sentence inside it was right.
    """
    from app.services import billing_service

    cycle_start, cycle_end, _, label = billing_service.get_billing_cycle(start, db=db)
    if (start, end) == (cycle_start, cycle_end):
        return ""
    return "\n".join([
        "",
        "  !! THIS WINDOW IS NOT A BILLING CYCLE !!",
        f"     you asked for   {start.isoformat()} to {end.isoformat()}",
        f"     the cycle is    {cycle_start.isoformat()} to {cycle_end.isoformat()}"
        f"   ({label})",
        "",
        f"     The total above gives THIS window its own free "
        f"{settings.BILLING_SEGMENTS_INCLUDED:,}-segment allowance.",
        "     Splitting one cycle across two runs therefore gives the allowance",
        "     away twice, and both invoices look correct. Re-run over the cycle,",
        "     or continue only if a partial period is what you meant.",
    ])


def describe(usage: dict) -> str:
    """The dry-run report. Every figure is his, and every figure is from one place."""
    lines = [
        f"  Period            {usage['start']} to {usage['end']}",
        f"  Messages          {usage['messages']:,}",
        f"  Segments sent     {usage['segments']:,}",
        f"  Included free     {usage['included_segments']:,}",
        f"  Billable segments {usage['billable_segments']:,}",
        f"  Rate              ${usage['price_per_segment']} per segment",
    ]
    if settings.BILLING_MONTHLY_FEE:
        lines.append(f"  Monthly fee       ${settings.BILLING_MONTHLY_FEE}")
    lines.append(f"  TOTAL DUE         ${usage['total_due']:.2f}")
    return "\n".join(lines)


def amount_in_cents(exact: Decimal) -> int:
    """Cents for Stripe, rounded once, half-up, from the exact Decimal.

    Not `int(total_due * 100)`. `total_due` has already been through
    `to_money()` and is a float; multiplying a float by 100 and truncating is
    how $344.10 becomes 34409 cents. The exact Decimal is carried all the way
    here for `cost_for_segments()`'s reason — at $0.015 every odd billable count
    sits on a half-cent — and it is rounded exactly once, here.
    """
    return int((exact * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def create_invoice(db, usage: dict, charge: bool) -> dict:
    """Draft the invoice, and finalise it only when asked."""
    customer = stripe_billing.customer_id(db)
    if not customer:
        raise SystemExit("No Stripe customer is stored for this account. The "
                         "client has not completed checkout, so there is "
                         "nothing to invoice against.")

    start = date.fromisoformat(usage["start"])
    end = date.fromisoformat(usage["end"])
    key = idempotency_key(start, end)
    api = stripe_billing.api()

    api.create_invoice_item(
        customer=customer,
        currency="usd",
        amount=amount_in_cents(usage["exact_total"]),
        description=period_label(start, end),
        metadata={PERIOD_METADATA_KEY: f"{start.isoformat()}:{end.isoformat()}"},
        idempotency_key=f"{key}-item",
    )
    invoice = api.create_invoice(
        customer=customer,
        collection_method="charge_automatically",
        # Not auto_advance: a back-bill is a deliberate act, and `--charge` is
        # the flag that says so. A draft can be read and deleted; a finalised
        # invoice has already gone to the client.
        auto_advance=False,
        description=period_label(start, end),
        metadata={PERIOD_METADATA_KEY: f"{start.isoformat()}:{end.isoformat()}"},
        idempotency_key=key,
    )
    invoice_id = invoice.get("id") if isinstance(invoice, dict) else invoice.id
    out = {"invoice_id": invoice_id, "finalized": False}
    if charge:
        api.finalize_invoice(invoice_id, idempotency_key=f"{key}-finalize")
        out["finalized"] = True
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--start", required=True, help="first day, YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="last day, YYYY-MM-DD (inclusive)")
    parser.add_argument("--create", action="store_true",
                        help="draft the invoice in Stripe")
    parser.add_argument("--charge", action="store_true",
                        help="finalise the draft — the client is billed")
    args = parser.parse_args(argv)

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    if end < start:
        raise SystemExit("--end is before --start")
    if args.charge and not args.create:
        raise SystemExit("--charge needs --create: there is nothing to finalise")

    db = SessionLocal()
    try:
        usage = stripe_meter.period_usage(db, start, end)
        warning = window_warning(db, start, end)
        print(describe(usage))
        if warning:
            # Printed after the figures, not before: the operator reads the
            # total and then reads why it might be the wrong total. Printed on
            # `--create` as well as on the dry run, because the flag that
            # commits is the one where it matters.
            print(warning)
        if not args.create:
            print("\n  (dry run — nothing was sent to Stripe. Add --create to draft "
                  "an invoice.)")
            return 0
        if usage["total_due"] <= 0:
            print("\n  Nothing to invoice: the period is inside the allowance.")
            return 0
        outcome = create_invoice(db, usage, charge=args.charge)
        print(f"\n  Invoice {outcome['invoice_id']} "
              f"{'finalised — the client has been billed' if outcome['finalized'] else 'drafted (not finalised)'}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":                      # pragma: no cover
    raise SystemExit(main())
