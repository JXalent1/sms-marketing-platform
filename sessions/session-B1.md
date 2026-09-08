# Session B1 — Stripe: settle August, then auto-bill usage

**Module:** B1 · **Depends on:** nothing in the prospecting track · **Parallel-safe with:** P2b, 5k

One checkout that does two things: charges the outstanding August balance and attaches the
card that every month afterwards is billed against. The client does it once and is never
invoiced by hand again.

---

## Prerequisites

- Read `CLAUDE.md`, `RULES.md`, `modules.md`, `status.md`, and every file in `decisions/`.
- Run inside the project venv. 696 tests, gate green twice as of `8a14af0`.
- **Billing math is escalation item 1.** The terms below were set by Jordan and are not
  yours to adjust. Anything this spec does not settle is an escalation, not a judgement
  call.

## The commercial terms — fixed, do not derive them from anything else

- **No monthly fee.** There is no flat subscription line. This is not the same shape as
  the reference build.
- **10,000 segments included free every month.**
- **$0.015 per segment beyond that.**
- **Outstanding August balance: $270.03**, being 18,002 billable segments at $0.015 after
  the 10,000 allowance — 28,002 segments sent in August.
- September to date is ~1,503 segments, under the allowance, so the gap between Sept 1 and
  the day he pays is $0 and needs no invoice.

All three live in `.env` already: `BILLING_MONTHLY_FEE=0`,
`BILLING_SEGMENTS_INCLUDED=10000`, `BILLING_PRICE_PER_SEGMENT=0.015`. Never hardcode any of
them.

---

## A1 — the allowance belongs to the Stripe price, not to the reporting code

**This is the requirement most likely to be got wrong, and it silently under-bills by
exactly $150 a month when it is.**

`billing_service.billable_segments()` is `max(0, segments - 10000)`. That is the same
arithmetic a Stripe **graduated tiered price** performs:

    tier 1   up to 10,000 units      $0
    tier 2   thereafter              $0.015 per unit

So the meter must receive the **raw count of segments in `BILLABLE_STATUSES`** — every
`sent` and `delivered` segment — and the tier applies the allowance.

**Never report `billable_segments()` to the meter.** Doing so subtracts the allowance
twice: once in our code and once in the tier. A month of 15,000 segments would bill
$0 instead of $75, and nothing on any screen would look wrong.

There is a test for this and it is acceptance criterion 2.

## A2 — the allowance now lives in two systems, so make them prove they agree

`BILLING_SEGMENTS_INCLUDED` is in this repo. The tier boundary is in Stripe's dashboard,
under nobody's version control. Two definitions of one number is the defect this codebase
has hit repeatedly — `SENT_STATUSES` twice, the opt-out definition, the suppression
window.

Build a check that fetches the metered price's tiers from Stripe and asserts:

- the first tier's `up_to` equals `BILLING_SEGMENTS_INCLUDED`
- the second tier's `unit_amount` equals `BILLING_PRICE_PER_SEGMENT`

Run it on demand and surface the result on `/health` alongside `sending_ok`. It must fail
**loudly and visibly**, not silently: an `.env` edit that never reached Stripe is a
mispriced invoice, and an invoice is the one artefact the client audits.

`/health` stays HTTP 200 when it disagrees, for `deployment/deploy.sh`'s reason — a 503
there rolls back every deploy including the one that fixes it. Report the state in a field.

## A3 — one checkout, both jobs

`mode=subscription` Checkout Session carrying:

- the **metered tiered price** — no `quantity`. Stripe rejects a quantity on a metered
  price; only licensed prices take one.
- the **August balance as a one-time charge on the first invoice**. Two mechanisms exist:
  a one-time price as an additional line item, or `subscription_data.add_invoice_items`.
  **Verify which one this API version accepts against a real session — do not choose from
  memory.** The reference build lost time to exactly this class of assumption.

`payment_method_collection='always'`. Without it a subscription whose recurring total is
$0 can complete without capturing a card, and every month under 10,000 segments is a $0
invoice. Here the first invoice is $270.03 so a card is taken anyway — but the setting is
what guarantees it, not the balance, and the balance is a one-off.

**Payment Links cannot carry usage-based prices.** Stripe greys the option out. Checkout
Sessions only.

## A4 — the cycle anchor, so the screen and the invoice describe one window

Stripe anchors the subscription's billing cycle to the moment checkout completes. That is
already the behaviour Jordan wants — billing runs from the day the client pays — and needs
no `billing_cycle_anchor`.

**The trap is on our side.** `BILLING_CYCLE_DAY` defaults to `1`. If he subscribes on the
9th and nobody changes it, `/usage` shows a 1st→1st cycle while Stripe bills 9th→9th: the
client reads one number on his dashboard and is charged against another. He would be right
to distrust both.

So: on `checkout.session.completed`, store the subscription's `current_period_start` day
in the settings table, and have `get_billing_cycle()` prefer the stored anchor over
`BILLING_CYCLE_DAY`. Config remains the fallback for a box that has never subscribed.

A test asserts the window `/usage` reports and the window the subscription bills are the
same two dates. This is the "two moments, one sentence-maker" rule applied to a period
rather than a sentence.

## A5 — the shared-account guard, and why it is not optional

Jordan runs more than one client through Stripe. A naive `checkout.session.completed`
handler stores the customer id from **any** completed checkout on the account — so another
client paying an unrelated link overwrites this client's customer id, and **A4A's segments
get metered onto that client's card.**

Before storing anything, fetch the session's line items and confirm they include this
product's metered price id. That price is the fingerprint: nothing else on the account
bills against this meter. Apply the identical guard to the success-page callback, since
anyone can hit that URL with any session id.

Verify webhook signatures and **fail closed**: with no signing secret configured, ignore
the payload rather than trusting it.

## A6 — reporting that cannot break sending, and cannot double-bill

- `report_segments(segments, campaign_id, db)` posts a meter event with a deterministic
  `identifier` of `campaign_<id>`. Stripe dedupes on it, so retries, redeploys and
  backfills each count once.
- Called fire-and-forget in a `try/except` **after** messages have gone out. A Stripe
  outage must never stop a send. `CLAUDE.md`'s rule about the alert channel applies: pick
  the failure mode deliberately.
- `backfill_unreported(db, dry_run=True)` replays missed periods, safe because of the
  identifier.
- `BILLABLE_STATUSES` from `app/models/sms_message.py` is the one definition. Import it;
  do not restate the tuple. Opt-outs, carrier rejections, region skips, `held_back` and
  failures are all excluded already.

## A7 — the back-bill tool

`tools/bill_period.py`, args `--start`, `--end`, `--create`, `--charge`. Dry-run by
default; `--create` drafts; `--charge` finalizes.

It uses `billing_service.compute_usage()` and `cost_for_segments()` — **the same functions
`/usage` renders from**, so the invoice and the dashboard cannot disagree. Idempotency key
derived from the period, so running it twice returns the same invoice rather than charging
again.

Its dry run over August must print **$270.03**. If it prints anything else, stop and
escalate rather than adjusting the tool until it agrees — the figure came from the app's
own math and a mismatch means one of the two is wrong.

## A8 — Stripe is not the carrier

The white-label rule covers the **SMS carrier**. Stripe is the payment processor, it
appears on the client's card statement, and hiding it would break checkout. Do not scrub
it.

`WHOLESALE_COST_PER_SEGMENT` must still never reach any of these surfaces, and the new
routes join the runtime scan in `tests/test_whitelabel.py` — which discovers routes from
the app's own route table, so they are covered the moment they exist. Confirm that, and
add the subscribe page's rendered body to `tests/_wholesale_scan.py`'s field-by-field walk.

---

## Out of scope

- Refunds, proration, plan changes, dunning beyond Stripe's own retries.
- Any second client or any multi-tenant billing.
- The customer portal beyond enabling it in the dashboard.
- Changing `BILLING_MONTHLY_FEE`, `BILLING_SEGMENTS_INCLUDED` or
  `BILLING_PRICE_PER_SEGMENT`. Escalation item 1.
- `.env` and `.env.production`. Human-only; a PreToolUse hook blocks agents.

## File list

    app/services/stripe_billing.py          (new)
    app/routers/billing.py                  (new)
    app/templates/subscribe.html            (new)
    app/services/billing_service.py         (A4: prefer the stored anchor)
    app/services/campaign_dispatch.py       (A6: fire-and-forget report)
    app/routers/pages.py                    (A2: /health field)
    app/core/config.py
    app/main.py
    tools/bill_period.py                    (new)
    requirements.txt                        (stripe — named here, so authorized)
    .env.example
    tests/
    agent/accept-B1.sh
    agent/mutate-B1.py

## Acceptance

`agent/accept-B1.sh` is the stop condition. Each criterion runs its tests in isolation.

1. **No real Stripe call in the suite.** Every client is replaced; fixtures are recorded.
   A test that reaches the network fails the run.
2. **The meter receives raw billable-status segments, not `billable_segments()`.** Drive a
   15,000-segment month and assert the reported value is 15,000. Mutating the reporting to
   send `billable_segments()` must go red.
3. **The tier-drift check fails on a mismatched tier** — feed it a price whose first tier
   is 5,000 and confirm it reports disagreement, and that `/health` still answers 200.
4. **`/usage`'s cycle and the subscription's cycle are the same two dates** once an anchor
   is stored, and fall back to `BILLING_CYCLE_DAY` when none is.
5. **`session_is_ours()` rejects a session for an unrelated product**, and the success page
   applies the same guard.
6. **An unsigned webhook stores nothing**, and an unconfigured signing secret ignores the
   payload rather than trusting it.
7. **A repeated meter event with the same identifier bills once.**
8. **`tools/bill_period.py --start 2026-08-01 --end 2026-08-31` dry-runs to $270.03**, from
   `billing_service`'s own functions.
9. **With no Stripe keys set**, `/subscribe` renders a clear "not available yet" notice and
   the checkout endpoint returns 503 — the page is safe to deploy before Stripe exists.
10. `bash agent/gate.sh` green, twice.
11. **Mutation run** on a scratch tree verified byte-identical to the repo, printing
    `SCRATCH VERIFIED PRISTINE`, reporting the same verdict on two consecutive
    invocations. Copy `agent/mutate-P1b.py`'s check.

### Mutations the harness must include

- Report `billable_segments()` to the meter instead of the raw count.
- Drop the tier-drift check.
- Put a `quantity` on the metered line item.
- Drop `payment_method_collection='always'`.
- Remove the `session_is_ours()` guard from the webhook, then from the success page.
- Trust an unsigned webhook.
- Make the meter identifier non-deterministic.
- Let the reporting call raise out of the send loop.
- Prefer `BILLING_CYCLE_DAY` over the stored anchor.
- Restate `BILLABLE_STATUSES` locally instead of importing it.

## `/goal`

> Session B1 is complete when `bash agent/accept-B1.sh` exits 0 with every criterion
> printed, `bash agent/gate.sh` is green twice, and the mutation run reports the same
> result on two consecutive invocations with both shown. Turn cap 60. Show the output; do
> not declare completion from a summary.

## Review

One synchronous fresh-context review, in session. No spawned reviewers. Lenses:

1. **Where is the allowance applied, and how many times?** Trace a 15,000-segment month
   from `sms_messages` to the invoice line and count every place 10,000 is subtracted. The
   answer must be one.
2. **What does the client see versus what is he charged?** `/usage` and the invoice, same
   period, same figure.
3. **What arrives on this path that is not ours?** A4's webhook and success page both take
   ids from outside. What does another client's session do to them?
4. **What is the failure mode when Stripe is down** — during a send, during a webhook,
   during the tier check?
5. **The scan's own first version.** Run criterion 3 against a price you know is wrong
   before quoting the check as evidence.

## Part B — Jordan's, in the Stripe dashboard

Do these yourself; the session must not automate them.

1. **Billing → Meters → Create meter.** Event name `sms_segments`, aggregation **Sum**,
   customer mapping `stripe_customer_id`, value key `value`.
2. **Product catalog → Create product** "Auctions4America — Text platform". Add one price:
   **usage-based, graduated tiers**, monthly, linked to the meter —
   **up to 10,000 → $0**, **thereafter → $0.015 per unit**. There is no flat price.
3. **A one-time price of $270.03** for the August balance, or leave it to
   `add_invoice_items` if that is the mechanism that verifies.
4. **Developers → Webhooks** → `https://app.onlineauctions.co/webhooks/stripe`, event
   `checkout.session.completed`. Copy the `whsec_…`.
5. **Settings → Billing → Invoice settings → Invoice finalization grace period → Add
   rule.** Set the finalization delay to **72 hours**, conditioned on *Has a metered price*
   **and** *Invoice is from a subscription cycle*.
   <br>**This is not optional.** The default is 1 hour; `BILLING_SETTLE_HOURS` is 24 and the
   metering pass runs hourly, so without this rule every campaign sent in the last day of a
   cycle is metered after that invoice has finalised and **is never billed on any invoice**.
   `decisions/012` has the reasoning and Stripe's own wording.
   <br>While you are on that page: **never change the metered price mid-cycle** — Stripe
   drops grace-period usage from the current *and* subsequent invoices when a subscription
   item's price changes during a cycle. Rate changes wait for a boundary.
6. **Settings → Billing → Customer portal → enable**, so he can update his own card later.
7. Put `STRIPE_SECRET_KEY`, `STRIPE_PRICE_METERED`, `STRIPE_METER_EVENT_NAME`,
   `STRIPE_WEBHOOK_SECRET` and `PUBLIC_BASE_URL` in `/home/appuser/app/.env` and restart.

Then hand the session the **price IDs** (`price_…`, not `prod_…`).
