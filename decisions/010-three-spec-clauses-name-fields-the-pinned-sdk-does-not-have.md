# Three of session B1's clauses name Stripe fields the pinned SDK does not have

**Blocks:** nothing. B1 Part A is complete, `agent/accept-B1.sh` exits 0 on all
eleven local criteria, the gate is green twice and the mutation run is 38 caught
/ 0 survived on two consecutive invocations. This records departures already
shipped and asks for a ruling that supersedes two clauses in
`sessions/session-B1.md`.
**Why this is recorded rather than absorbed:** `RULES.md` — a session spec is not
edited after the fact except when a resolved decision supersedes something in it.
Two of B1's clauses are wrong about the API and leaving them wrong is worse than
editing them. This is the third consecutive session to hit the pattern
`CLAUDE.md` already names: *a spec clause that names a mechanism is a guess
wearing a spec's authority* (`decisions/007`, `decisions/008`).

## Context

B1 pins `stripe==15.6.1`, whose API version is `2026-08-26.dahlia`. Each clause
below was checked against the installed package before any code was written, and
each check now runs on every suite invocation in `tests/test_stripe_contract.py`
— the `test_provider_status.py` discipline, one vendor along.

### 1. A3 — `subscription_data.add_invoice_items`. Not superseded; recorded.

A3 offers two mechanisms for the August balance and says, correctly, to verify
which the API accepts rather than choosing from memory. The answer is that
`add_invoice_items` is **not a Checkout parameter at all** and never was:

```
stripe.params.checkout._session_create_params.SessionCreateParamsSubscriptionData
  -> application_fee_percent, billing_cycle_anchor, billing_cycle_anchor_config,
     billing_mode, default_tax_rates, description, invoice_settings, metadata,
     on_behalf_of, pending_invoice_item_interval, proration_behavior,
     transfer_data, trial_end, trial_period_days, trial_settings
```

It is a parameter of `Subscription.create`, `Subscription.update` and
`SubscriptionSchedule` — exactly the sort of thing a reader half-remembers as
available everywhere. Checked against `stripe==11.6.0` as well: absent there too,
so this is not a removal. The balance therefore rides as a **second line item**
with `quantity=1`, and Part B item 3 needs a one-time price
(`STRIPE_PRICE_BALANCE`) rather than being left to `add_invoice_items`.

A3 asked for the verification and got it, so nothing here supersedes anything.
It is recorded because the verification is the interesting artefact and because
`.env.example` and `requirements.txt` both now carry the conclusion.

### 2. A2 — "the second tier's `unit_amount` equals `BILLING_PRICE_PER_SEGMENT`"

`stripe.Price.Tier.unit_amount` is an **integer number of cents**. This client's
rate is $0.015 — one and a half cents — so it is not representable in that field.
Stripe carries a sub-cent rate in `unit_amount_decimal`, a decimal string of
cents (`"1.5"`).

A check written to A2's letter would compare `0.015` against `2` and report
**disagreement on a correctly configured price**. The likely repair, once
somebody hit that on a live box the morning of a sale, is to relax the
comparison until it agreed — which is how a drift check stops being one.

Shipped instead: `unit_amount_decimal` first, `unit_amount` as the fallback for a
whole-cent rate (which is what a future client's plan is most likely to be), and
the comparison done in `Decimal` end to end because `0.015 * 100` is
`1.4999999999999998` in binary floating point. `stripe_meter.compare_tiers()`,
and `test_a_sub_cent_rate_is_compared_as_a_decimal_number_of_cents`. Mutation
`A2c` reverts it to the spec's field and two tests go red.

### 3. A4 — "store the subscription's `current_period_start` day"

`current_period_start` and `current_period_end` were moved off the Subscription
object; they live on its **items** in this API version:

```
"current_period_start" in stripe.Subscription.__annotations__      -> False
"current_period_start" in stripe.SubscriptionItem.__annotations__  -> True
"billing_cycle_anchor" in stripe.Subscription.__annotations__      -> True
```

Written to A4's letter, `subscription.current_period_start` is `None`, no anchor
is ever stored, and `/usage` silently keeps reporting `BILLING_CYCLE_DAY` — the
exact 1st-versus-9th disagreement A4 exists to prevent, arrived at *through* the
fix rather than through its absence. That is the worst shape of failure: the
guard is present, tested, and reads correct.

Shipped instead: the *window* comes from the item's period, and the *day* comes
from `Subscription.billing_cycle_anchor`, which is the field that defines it and
which does still exist. The anchor is preferred over the period start for a
second reason the spec could not have known: a subscription anchored on the 31st
has a February period starting on the 28th, and storing 28 would move the
client's billing day for good, silently, in the direction nobody checks.
`test_an_anchor_on_the_31st_is_not_flattened_by_a_short_month`.

## Options

1. **Supersede clauses A2 and A4 in `sessions/session-B1.md`** — strike the field
   names, cite this decision, leave the *properties* (the two definitions of the
   allowance agree; one window is described twice) exactly as written / cost: an
   edited spec, which `RULES.md` permits only through a decision, which is what
   this file is for.
2. **Leave the spec as written** — cost: the next reader of A2 or A4 finds a
   clause that contradicts the code, and the code is the one that was checked
   against the API. `decisions/007` and `008` both rejected this, for this
   reason.

## Recommendation

Option 1, plus one addition to `CLAUDE.md`. The existing rule says *state the
property, not the mechanism*. What B1 adds is **where to look when a spec does
name a mechanism**: a pinned SDK's own declared parameter types are checkable
offline, on every run, and they settled all three of these before a line of code
was written. That is cheaper than a live API call and it is the same artefact
`tests/test_provider_status.py` was built on after `telnyx==2.1.2` — the
difference being that this time the shapes were read *before* the code rather
than after the outage.

---

# Decision — upheld, and the pattern is mine to fix

**Decided by:** Jordan (via Cowork), 2026-09-07
**Status:** resolved

All three departures are correct and each is better than what the spec said.
`subscription_data.add_invoice_items` is not a Checkout parameter in the pinned SDK, a
tier's `unit_amount` is integer cents and cannot express $0.015 (it is
`unit_amount_decimal`), and `current_period_start` moved onto the subscription's items.
Written to the letter, the second would have called a correct price wrong and the third
would have stored no anchor at all — reaching A4's 1st-versus-9th disagreement *through*
the fix for it.

**This is the fourth consecutive session where a clause I wrote named a mechanism that did
not survive contact** — `decisions/007` (subtract by module), `008` (assert no full scan),
this one, and the identifier lifetime in `011`. The common factor is not Stripe or
FastAPI; it is that I wrote an API's field names from memory and gave them a spec's
authority.

`RULES.md` now carries the rule that follows: a spec clause naming a third-party field,
parameter or return shape is **unverified until the session checks it**, and must be
marked as such. The session verifies before building, and a mismatch is a finding to
record, not a departure to justify.
