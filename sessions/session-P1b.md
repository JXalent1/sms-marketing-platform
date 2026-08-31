# Session P1b — The lookup provider, and a gate that flakes

## Objective

Three small things, one of which spends money and one of which is currently able to fail
a green build.

## Prerequisites

- Read `CLAUDE.md`, `RULES.md`, `status.md`, `modules.md`, and the Prospecting plan of
  record in `A4A_BUILD_PLAN.md`.
- P1 is merged. 494 tests, gate green twice, 40 mutations on a verified-pristine tree.
- Production holds the client's real contacts and message history.
- Deploy: `SERVER=appuser@67.205.180.62 SERVICE=a4a-sms ./deployment/deploy.sh`

---

# Part A — agent work

## A1. Ship the Telnyx line-type lookup provider

**Escalation item 7 is ruled on: wire it in.** $0.0025 per MCC/MNC query is the entire
economic premise of scraping — without it P2 produces a list that costs more than it
returns. One class plus one line in `PROVIDERS`, against the interface P1 already built.

Three constraints, and the first is not obvious:

**1. Lookups and sends draw on the same Telnyx balance.** A 10,000-number screening run
spends $25 out of the pot `capacity_assessment()` measures. An overnight scrape can
therefore fail the next morning's campaign pre-flight, and the operator would have no way
to connect the two events.

So: a **hard monthly spend cap** in config, enforced before the call, refusing cleanly and
logging what was skipped rather than partially draining the account. Default it low —
$50 — because the failure mode is a silent transfer from sending budget to lookup budget.

**2. The cache is mandatory, not an optimisation.** P1 built it. A number looked up once
is never looked up again, at any volume, from any source. Assert on call count.

**3. Never spend on a number we will not use.** Not a rejected prospect (P1 already fixed
this), not a blocklisted number, not a contact whose line type is already known.

Keep the client-facing surface unchanged: this is our cost, never his. `CLAUDE.md` records
that leak and it has been made once already.

Tests run against the counting fake. **No real API calls during the build or the gate.**

## A2. Fix the flaky white-label assertion

`tests/test_campaign_reports.py::test_no_new_surface_leaks_the_carrier_or_our_cost`, line
313, tests the bare string `"0.009"` against a whole response body — and
`...T14:23:40.009312` contains it. One failure in six runs, unreproducible in eight more.

This is the third appearance of one defect class in this repo:

- `21610` matched a Brevard County phone number inside prose
- `\b` let `TelnyxError` through because the SDK glues the name to a word
- `0.009` matches inside a microsecond timestamp

**Fix the assertion, not the timestamp.** The property worth testing is "no wholesale
figure is rendered as a price," which is about parsed values in known fields, not a
substring sweep of a serialized body. Assert against the fields the response actually
declares.

Then **audit every other bare-numeric assertion in the suite** for the same shape. This
has now cost three sessions; find the fourth one before it does.

Priority note: this is small but it is not cosmetic. The gate runs `--maxfail=1`, so this
test can fail a sound build and bounce an unattended agent onto a defect that does not
exist. A gate that flakes is worse than one that fails loudly.

## A3. `docs/API.md` drift

It documents neither the prospects API nor 5f's reports and links routes. Pre-existing,
flagged in P1. Close it.

---

## Part A acceptance

1. `agent/gate.sh` passes all six checks, twice.
2. The Telnyx lookup provider returns line type against a recorded fixture; the default
   provider still makes no call.
3. The spend cap refuses before calling, logs what was skipped, and does not partially
   drain. Show it refusing.
4. A repeat lookup hits the cache and makes no call — assert on call count.
5. Rejected, blocklisted and already-known numbers are never looked up.
6. No wholesale figure reaches any client-facing page, response or export.
7. The rewritten white-label assertion passes 20 consecutive runs, and fails when a real
   wholesale figure is injected into a declared field. Both directions.
8. The bare-numeric audit names every remaining site or states that none remain.
9. Behavioural mutation run on a **verified-pristine** scratch tree — the P1 lesson.
   At minimum: revert the spend cap, the cache, and the never-spend-on-unusable guard.
10. After deploy: all screens 200 over HTTPS, no carrier name, raw provider payload or
    wholesale figure anywhere client-facing.

Wire into a `/goal` stop condition with a turn cap, then review yourself in session
against the ten lenses. No spawned reviewers.

## Constraints

- Do not touch `.env`, `.env.production`, `agent/gate.sh`, `agent.config.sh`. The Telnyx
  key is already present; if a new setting is needed, escalate and Jordan adds it.
- **Do not send SMS. Do not modify, delete or re-import contact data.**
- **Do not make a real paid API call**, including to validate the provider. Fixtures only.
- Do not weaken any pre-flight or compliance check.
- No source file over 500 lines. `app/sms/` stays DB-free.

## Explicitly out of scope

- Google Places and the search taxonomy — P2.
- Registries and enrichment — P3.
- Screening the existing contact list. That is a decision about spending real money on
  live data and it is Jordan's to make, not this session's.
