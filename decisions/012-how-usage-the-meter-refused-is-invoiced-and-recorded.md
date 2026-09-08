# How is usage the meter refused invoiced by hand, and how is that recorded?

**Blocks:** nothing built. B1b Part A is complete: the pass refuses usage older
than Stripe's 35-day timestamp horizon, leaves `metered_at` NULL, and says so
at ERROR every hour, exactly as `sessions/session-B1b.md` A3 specifies. The
remedy that clause names — "a one-off invoice through `tools/bill_period.py`"
— is what this escalation is about.
**Why this is not mine to decide:** escalation item 1 (billing math: how a
partial-cycle manual invoice is priced against an allowance the meter has
partly consumed) and item 10 (the spec does not say how a manual invoice is
recorded so the refusal stops).

## Context

Found by the fresh-context review, measured on a scratch database.

`tools/bill_period.py` prices a window through `compute_usage()`, which counts
every billable row and reads no `metered_at`. That is correct for the period
it was written for — August, before a meter existed — and wrong for any window
the meter has touched. July with 12,000 segments metered normally and 12,000
refused as too old:

```
Segments sent     24,000        <-- 12,000 of these are already on the subscription invoice
Billable segments 14,000        <-- the allowance, applied a second time
TOTAL DUE         $210.00       <-- the right one-off figure is $180.00 (12,000 x $0.015)
```

`--create` would have drafted that invoice. And nothing writes anything after a
manual invoice, so the ERROR fires every hour indefinitely (confirmed at +1 day
and +3 months) and `--unmetered` cannot tell "invoiced by hand" from "not yet".

**Shipped in B1b, as a guard rather than a policy:** `--create` now **refuses**
any window that has metered rows in it, prints the reconciliation breakdown
under the refusal, and says this decision is open
(`tools/bill_period.py::metered_refusal()`, `test_the_tool_refuses_to_invoice_a_window_that_is_partly_on_the_meter`,
mutation `R1`). A window with no metered rows — August, or a cycle the pass
missed entirely — still invoices as before, and the arithmetic is unchanged.
The scenario needs 35 days of the pass not running or refusing (a key removed
after checkout, Stripe down for a month, the anchor never stored), which is the
case the too-old path exists for.

## Options

1. **Price the one-off invoice as the plan's increment, and record it in a
   column of its own.** `tools/bill_period.py --refused-only` invoices only the
   billable rows in the window the meter refused, at
   `cost_for_segments(metered + refused) - cost_for_segments(metered)` — the
   same plan, applied exactly once across both channels, so the allowance is
   consumed by whichever channel got there first. Stamp those rows in a new
   additive column `sms_messages.invoiced_at` (with the Stripe invoice id), so
   the pass's too-old selection excludes them, the ERROR stops, and
   `--unmetered` shows "invoiced by hand" as its own line. / cost: a second
   ledger column and one more migration; the increment arithmetic is new,
   though built from `cost_for_segments()` and nothing else.
2. **Invoice from Stripe's dashboard by hand, and give the tool a flag that
   only records it.** `--mark-invoiced <invoice id>` stamps the refused rows
   without pricing anything; the figure is typed into Stripe by a person from
   the `--unmetered` breakdown. / cost: the price is computed by a human, which
   is the situation B1 was built to end, and a typed figure has no test.
3. **Leave it.** The guard stops the double bill; the ERROR keeps firing until
   the rows are invoiced somehow, and the reconciliation view shows them
   forever. / cost: a permanent ERROR is one nobody reads, and "invoiced by
   hand" and "not yet" stay indistinguishable.

## Recommendation

**Option 1.** It is the only one where the figure on the invoice comes from
`billing_service`'s own functions, which is the rule every other billing
surface in this codebase follows, and the only one where the refusal can stop
for a reason the database records. The increment is not a new commercial term:
it is the existing plan — 10,000 included per cycle, $0.015 after — applied
once, with the meter having consumed some of the allowance first. Two things
for whoever specs it: the column must be its own (`invoiced_at`, not a value
smuggled into `metered_at` — one column, two meanings is the mistake
`CLAUDE.md` opens with), and the window handed to `--refused-only` must be a
billing cycle, for the reason `window_warning()` already states.

---

# Decision — Option 1. And a Part B setting without which the whole pass leaks.

**Decided by:** Jordan (via Cowork), 2026-09-08
**Status:** resolved · **Not blocking Part B**

**Option 1**, for the reason the recommendation gives: it is the only one where the figure
on the invoice comes from `billing_service`'s own functions, which is the rule every other
billing surface here follows, and the only one where the refusal can stop.

The increment — `cost_for_segments(metered + refused) - cost_for_segments(metered)` — is
the right shape. It applies the plan once across both channels and lets the allowance be
consumed by whichever channel reached it first, which is the property that makes $180.00
correct and $210.00 wrong. `invoiced_at` is additive and nullable, so not item 8.

The guard shipped in B1b is the right interim: `--create` refusing a part-metered window is
strictly better than pricing it wrong, and the scenario needs 35 days of the pass not
running. Specced as **B1c**, after Part B rather than before it.

## The finding that does gate Part B, and it is not in this file

Reviewing this raised the adjacent question B1b logged as unknown — what happens to usage
reported after an invoice finalises. Stripe answers it:

> All invoices have a default finalization grace period of **1 hour**. During the
> finalization grace period, you can continue to report usage for the previous billing
> period. … **Any usage reported beyond the grace period isn't included.**

And for a draft: *"If the usage timestamp is for any time after the creation time of the
draft invoice, we append the usage to the next invoice."* — which is about where the
timestamp sits, not when the report arrives.

`BILLING_SETTLE_HOURS = 24` and the pass runs hourly. So a campaign sent in the final day of
a cycle settles about 22 hours **after** that cycle's invoice has finalised. The report is
past the grace period, so it is not on that invoice; its timestamp is inside the closed
period, so it is not on the next one. **It is never billed.** On an account sending most
days that is one or two campaigns a month, lost silently, in the client's favour — which is
the direction nobody audits.

**Part B gains a mandatory step:** set an invoice finalization grace period of **72 hours**
(the maximum) under a rule conditioned on *Has a metered price* **and** *Invoice is from a
subscription cycle*. 72h clears the 24h settle window plus the hourly pass with margin, and
is well inside a monthly service period — Stripe's own caution is only against a grace
period longer than the service period.

Two riders from the same page, both worth carrying:

- **Never change the metered price mid-cycle.** *"If a subscription item's price changes
  during a billing cycle … any usage reported during the grace period doesn't appear on
  current or subsequent invoices."* A rate change waits for a cycle boundary. That is now a
  billing rule, not a preference.
- **The first invoice finalizes immediately regardless of any rule.** Harmless here — the
  first invoice is the $270.03 balance, not usage — but it means the grace period cannot be
  proven on it. Prove it on the second cycle, or in test mode.

The other unknown B1b logged — an event timestamped before the subscription's first period
— stays open and costs nothing this cycle: September to date is ~1,500 segments, under the
10,000 allowance, so any dropped pre-anchor event is worth $0. Settle it in test mode
rather than by reasoning.
