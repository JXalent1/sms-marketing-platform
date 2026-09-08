# Session B1b — meter once, late, and correctly

**Module:** B1b · **Depends on:** B1 · **Blocks:** Part B going live

`decisions/011` resolved: **Option 2, with three amendments.** This session implements it.
Read that decision in full before anything else — the reasoning is there and is not
repeated here.

## Prerequisites

- Read `CLAUDE.md`, `RULES.md`, `modules.md`, `status.md`, and every file in `decisions/`
  — **`010` and `011` especially**.
- Run inside the project venv. 764 tests, gate green twice as of B1.
- **Billing math is escalation item 1.** The property below is settled; the mechanism is
  yours to choose and to prove.

## The property this session must guarantee

> **Every billable segment reaches the meter exactly once, and the figure metered is the
> figure `compute_usage()` would report for the same window.**

Everything below serves that sentence. If a mechanism you pick serves it better than the
one described, take it — and prove it against a case whose answer is known.

## What is wrong today

Three defects, all measured, none exposed until a customer id is stored.

**1. Over-bill on every campaign with delivery failures.** `report_usage()` fires the
instant `send_campaign()` returns, when every row is `sent`. The delivery webhook then
writes `undelivered`, which is outside `BILLABLE_STATUSES`. Measured: metered 12,000,
`/usage` shows 8,000, Stripe keeps 12,000. On this account's ~30% failure rate that is
about $90 a month the client does not owe, and his own dashboard shows the smaller number.

**2. Under-bill on every top-up.** `campaign_topup.top_up_background` writes `sent` rows
and nothing meters them. Even with a hook, `campaign_<id>` is spent.

**3. Double-bill on any backfill older than a day.** B1's A6 claimed the deterministic
identifier makes backfills free. Stripe enforces identifier uniqueness only **within a
rolling period of at least 24 hours** — it exists for same-minute retries. So
`backfill_unreported()`, whose whole purpose is replaying a missed period, bills a second
time for anything older than that. **This one is in the shipped tree and is the largest of
the three.**

## A1 — mark the rows; stop leaning on Stripe's identifier

Add `metered_at` to `sms_messages`. Additive, nullable — not escalation item 8.

A metering pass selects rows that are **billable, settled, and unmarked**, reports them,
and marks them in the same transaction as the successful report. A row already carrying
`metered_at` is never reported again, by any path, including the backfill.

Stripe's identifier stays, deterministic, as a second line of defence for the same-minute
retry it is designed for. It is no longer load-bearing and no comment may claim it is.

Rows written before this migration have no `metered_at` and have never been metered —
nothing has, since no customer exists. Say that in the migration's docstring rather than
leaving the next reader to work out whether NULL means "not yet" or "unknown".

## A2 — settled, defined

A row is settled when its status can no longer change: it reached a terminal state, or a
settle window has elapsed since the send. The window is config, with a stated default, and
exists so a receipt that never arrives cannot hold billing open forever.

Name what the terminal states are by importing them, not by restating a tuple —
`BILLABLE_STATUSES` and its neighbours live in `app/models/sms_message.py`. `held_back` is
not a send.

## A3 — stamp the event with the send time

Stripe's `timestamp` accepts anything **within the past 35 calendar days** or up to five
minutes ahead. Set it to the row's send time so a pass running hours or days later still
lands the usage in the cycle it belongs to. This removes the month-boundary cost rather
than bounding it.

**Usage older than 35 days cannot reach the meter at all.** The pass must detect that,
refuse to meter it, leave `metered_at` NULL, and say so loudly — it is a one-off invoice
through `tools/bill_period.py`, not a silent drop. A guard that is switched off refuses; it
does not wave things through.

## A4 — the pass itself

A scheduled adjudication, not a call in the send path. `run_due_campaigns` already
demonstrates the shape and the scheduler already exists.

It must not run inside the send loop, and it must never raise into one. `CLAUDE.md` on
`agent/notify.sh`: pick a channel for the shape of the path it fires on. Metering is a
digest, not a webhook.

Remove the fire-and-forget call from `campaign_dispatch.py`. A top-up needs no hook of its
own — its rows are unmarked, so the next pass takes them.

## A5 — backfill and the tools

`backfill_unreported()` is rewritten against `metered_at` or removed. If it stays, it must
be incapable of double-reporting a marked row, and criterion 4 proves it.

`tools/bill_period.py` is unchanged in its arithmetic. It gains one thing: a way to see
which rows in a window are unmetered and why, because that is the question anyone
reconciling an invoice will ask.

## Out of scope

- The rates. `BILLING_MONTHLY_FEE`, `BILLING_SEGMENTS_INCLUDED`,
  `BILLING_PRICE_PER_SEGMENT` — escalation item 1, settled.
- The tier-drift check, the checkout, the webhook guards, `/subscribe`. B1 built them and
  they pass.
- `.env`, `.env.production`. Human-only.
- Any change to what counts as billable. `BILLABLE_STATUSES` is not this session's.

## File list

    app/models/sms_message.py
    app/services/stripe_meter.py
    app/services/campaign_dispatch.py
    app/services/campaign_topup.py
    app/main.py                      (the scheduled pass)
    app/core/config.py               (the settle window)
    tools/bill_period.py
    alembic/versions/                (metered_at)
    tests/
    agent/accept-B1b.sh
    agent/mutate-B1b.py

Widen it if a requirement forces it and record each edit in `status.md` with the
requirement that forced it.

## Acceptance

1. **The over-bill is gone.** Send 12,000 segments, let the webhook write `undelivered`
   down to 8,000, run the pass: the meter receives **8,000**, once.
2. **The top-up is billed.** Top up a metered campaign; the next pass meters only the new
   rows, and the meter's total for that campaign equals `compute_usage()`.
3. **A second pass over the same rows reports nothing.** Not because Stripe deduped it —
   assert on the call count, with the client replaced.
4. **A backfill of a 30-day-old period double-bills nothing**, proven against marked rows
   rather than against Stripe's identifier window.
5. **Usage older than 35 days is refused, visibly**, with `metered_at` left NULL and the
   reason stated.
6. **The event carries the send timestamp**, and a pass run after a cycle boundary lands
   the usage in the earlier cycle.
7. **Nothing meters from the send path.** Reverting A4's removal must break a test.
8. **`/usage` and the metered total agree** for the same window, on a campaign with
   failures and a top-up.
9. `bash agent/gate.sh` green, twice.
10. **Mutation run on a verified-pristine tree, same verdict on two consecutive
    invocations, both shown.**

### Mutations

- Meter at send time again.
- Meter unsettled rows.
- Skip the `metered_at` mark after a successful report.
- Mark before the report succeeds.
- Report a marked row.
- Drop the 35-day refusal and meter silently.
- Omit the timestamp so the event lands in the reporting cycle.
- Restate `BILLABLE_STATUSES` locally.
- Let the pass raise into the scheduler.

## `/goal`

> Session B1b is complete when `bash agent/accept-B1b.sh` exits 0 with every criterion
> printed, `bash agent/gate.sh` is green twice, and the mutation run reports the same
> result on two consecutive invocations with both shown. Turn cap 40. Show the output.

## Review

One synchronous fresh-context review. No spawned reviewers.

1. **Count the places a segment can be metered.** The answer must be one.
2. **What happens to a row whose status changes after it is marked?** Say it in a comment
   next to the mark, whichever way it goes.
3. **What does the pass do when Stripe is down** — mid-batch, after some rows are marked?
4. **Which entry point is each mutation on?** B1's own review found a meter and a screen
   disagreeing with a docstring between them asserting they agreed.
5. **The scan's own first version.** Run criterion 1 against the pre-fix tree and confirm
   it goes red for the right reason.
