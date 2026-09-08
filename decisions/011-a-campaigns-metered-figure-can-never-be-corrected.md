# A campaign's metered figure is written once and can never be corrected. Both directions are wrong.

**Blocks:** nothing built. B1 Part A is complete and implements A6 exactly as
written — the call site, the timing and the identifier are all as specified, and
each has a mutation. This is a gap between A6's stated *mechanism* and A1's
stated *property*, and it starts costing money the day Part B lands and the
client subscribes. Not before: nothing is metered until a customer is stored.
**Why this is not mine to decide:** escalation item 1. What reaches the meter is
what appears on the invoice. A6 fixes the call site
(`app/services/campaign_dispatch.py`, the only service file B1's list names for
it), the moment ("after messages have gone out") and the event identifier
(`campaign_<id>`). Changing any of the three changes what counts as a billable
segment.

## The one mechanism, and the two ways it is wrong

`report_segments()` posts one meter event per campaign with
`identifier = f"campaign_{campaign_id}"`. Stripe records the first event under an
identifier and **ignores every later one** — that is what makes a retry, a
redeploy and a backfill free, and it is A6's entire idempotency story.

The same property means the figure can never be revised. A campaign's billable
segment count is not stable at the moment we report it, and it moves in both
directions.

### Direction 1 — we over-bill, and this is the serious one

`campaign_dispatch.report_usage()` runs the instant `send_campaign()` returns,
when every row is `sent`. Minutes later the delivery webhook
(`app/routers/webhooks/common.py`) writes `undelivered` for the ones the carrier
accepted and then dropped — spam filters, dead handsets — and `undelivered` is
outside `BILLABLE_STATUSES`. Measured on a scratch database:

```
metered at send time     : 12000
/usage after the webhook : 8000
re-report attempted      : 8000
what Stripe actually has : 12000     <-- the correction is discarded
```

The client is billed for 12,000 segments while his own dashboard says he used
8,000, permanently, **in our favour**. This account's corpus is 3,037 failures
across 2,526 landlines, so the gap is not hypothetical; at $0.015 a 30% failure
rate on a 20,000-segment month is about $90 he does not owe.

It is also the worst shape this project keeps finding: two screens that disagree,
with the one he checks showing the smaller number, and nothing anywhere saying
which is the invoice.

### Direction 2 — we under-bill, on the top-up path

`campaign_topup.top_up_background` is the third path that writes `sent` rows and
it is **not in B1's file list**, so nothing meters it. A top-up is not a second
campaign: it is the same campaign reaching people the first run did not — a
`batch_size` cap withheld them, the hold-back window did, or they were imported
afterwards (`sessions/session-5e.md` A4, `decisions/005`). Same `campaign_id`,
same send loop, counted by `compute_usage()`.

Even with a hook there, `campaign_<id>` is already spent, so a second event
carrying the larger total is discarded exactly as the correction above is.

A top-up is one click on the campaign rail, in precisely the state
`decisions/006` leaves the client in after a fully-suppressed campaign.

## Options

1. **Report a delta under an identifier naming the transition.** Keep
   `campaign_<id>` for the first report. A later report of the same campaign
   posts only the difference — positive or negative — under
   `campaign_<id>_from_<n>_to_<m>`, read from the ledger row `report_segments()`
   already writes. Still fully deterministic: replaying the same transition is
   still free, which is the property A6 actually asked for. Adjudicated by a
   scheduled job once delivery receipts have settled, and hooked into
   `campaign_topup.top_up_background`.
   / cost: two files outside B1's list, a second identifier shape, and Stripe
   meter events cannot carry a negative value — so direction 1 needs the delay
   below rather than a negative event.

2. **Report late instead of correcting.** Meter a campaign once, on a scheduled
   pass, some hours after its last send — long enough for delivery receipts and
   for any top-up. One event, one identifier, no deltas, and the figure is right
   the first time. / cost: usage appears on the Stripe meter hours late, which
   matters at a month boundary: a campaign sent at 23:00 on the last day of a
   cycle would meter into the next one. A6 says "after messages have gone out",
   and this is a different reading of *after*.

3. **Combine them.** Report on the schedule of option 2, and keep option 1's
   delta identifier for anything that moves afterwards. / cost: the most
   machinery, and option 1's delta path would be exercised rarely — which
   `CLAUDE.md` warns is how a guard becomes untested code.

4. **Leave it and reconcile by hand.** `tools/bill_period.py` prices any window
   from the same functions `/usage` renders from, so both errors are visible and
   recoverable. / cost: "he is never invoiced by hand again" becomes "except
   when a campaign had failures, or when the top-up button was used", and
   nothing on any screen says which months those were.

## Recommendation

**Option 2**, in a small follow-on session that owns `campaign_dispatch.py`,
`campaign_topup.py` and `stripe_meter.py`.

It is the one option with no second identifier shape, no delta arithmetic and no
rarely-exercised path, and it makes the figure right the first time rather than
right after a correction — which is the difference between a system that agrees
with `/usage` and one that catches up with it. The month-boundary cost is real
and bounded: it is one campaign's segments landing in the adjacent cycle, on a
plan with a 10,000-segment allowance, and it can be closed by adjudicating at
the cycle boundary as well as on the timer.

Two notes for whoever writes that spec. State the property — **every billable
segment reaches the meter exactly once, and the figure metered is the figure
`compute_usage()` would report** — and let the session prove its chosen mechanism
against a case whose answer is known, because A6's mechanism was specified from a
model of Stripe that is right about retries and silent about revisions. And note
that this is the *third* consecutive session where a spec clause named a
mechanism that did not survive contact (`decisions/007`, `008`, `010`).

Until then this is a **known over-bill on every campaign with delivery failures
and a known under-bill on every top-up**, and both are in `status.md` under
"Found while working" so neither is rediscovered from an invoice.

---

# Decision — Option 2, with three amendments. And a fifth spec error, in my own idempotency clause.

**Decided by:** Jordan (via Cowork), 2026-09-07
**Status:** resolved

**Option 2 is right** and the reasoning holds: one event, one figure, right the first time,
no delta arithmetic, no negative meter events, no rarely-exercised correction path.
Adopted.

Three amendments — two closing gaps the recommendation leaves open, one fixing a defect in
the spec clause this session was built from.

## Amendment 1 — our database is the idempotency ledger, not Stripe's identifier

**B1 A6 is wrong, and I wrote it.** It says the deterministic identifier means "retries,
redeploys and **backfills** each count once." Stripe's own reference says otherwise:

> `identifier` … Stripe enforces uniqueness within a rolling period of **at least 24
> hours**. The enforcement of uniqueness primarily addresses issues arising from accidental
> retries or other problems occurring within extremely brief time intervals.

So `backfill_unreported()` — which exists precisely to replay a missed period — **double-bills
any period older than about a day.** That is a second over-bill, larger and less visible
than the one this escalation was raised about, and it is in the shipped tree.

The fix is not a better identifier. **Mark the rows.** A billable row carries `metered_at`;
a pass meters only rows that are billable, settled and unmarked, and marks them in the same
transaction. Stripe's identifier stays as a second line of defence for the same-minute retry
it is actually designed for, and stops being load-bearing.

That also makes the property statable exactly as this file asks: *every billable segment
reaches the meter exactly once, and the figure metered is the figure `compute_usage()` would
report.*

## Amendment 2 — stamp the event with the send time; the month boundary then costs nothing

The recommendation accepts a bounded cost: a campaign sent at 23:00 on the last day of a
cycle meters into the next one. It does not have to.

> `timestamp` … Must be within the past **35 calendar days** or up to 5 minutes in the
> future. Defaults to current timestamp if not specified.

Set it to the campaign's send time. A pass running hours or days later still lands the usage
in the cycle the send belongs to. The month-boundary cost is removed rather than bounded,
and no adjudication at the cycle edge is needed.

The 35-day limit is itself a rule: **usage older than 35 days cannot reach the meter at
all.** It must go out as a one-off invoice through `tools/bill_period.py`, and the metering
pass has to say so rather than silently dropping it.

## Amendment 3 — the unit is a settled batch, not a campaign

Option 2 says one event per campaign, metered "long enough for delivery receipts **and for
any top-up**". A top-up is not bounded by that window: `decisions/005` and `006` describe a
top-up releasing rows the hold-back window froze, and that window is measured in days. A
top-up routinely lands after any settle delay worth waiting.

One identifier per campaign therefore leaves direction 2 exactly where it is. The unit is
the **batch of billable rows settled and not yet metered**, identified from that batch. A
top-up is a later batch on the same campaign; the meter aggregates `Sum`, so the two events
add. With Amendment 1 the marking makes this correct on its own and the identifier is merely
deterministic.

## Not exposed today

Nothing meters until a customer id is stored and Part B has not run. B1 is uncommitted. Both
over-bills and the under-bill are theoretical until the client subscribes — which is why
this is a follow-on session rather than a hotfix.

## The session

`sessions/session-B1b.md`. It owns `campaign_dispatch.py`, `campaign_topup.py`,
`stripe_meter.py`, a migration adding `metered_at`, and `backfill_unreported()`.
