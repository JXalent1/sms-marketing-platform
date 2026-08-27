# Session 5g — Blocklist correctness

## Objective

5d A7 put `should_auto_block()` on the delivery-webhook path, taking it from a handful of
events to 3,037. The fragment list it now runs on was written for the narrow path. Make
it safe on the wide one.

The failure this prevents is invisible by construction: a wrongly blocked buyer simply
stops appearing in campaigns, and the only trace is one `delivery_failure` row among
thousands of correct ones.

**Runs in parallel with 5e.** File sets are disjoint — 5e is campaigns/contacts/UI, this
is compliance/webhooks/blocklist. Neither touches the other's files. Whichever merges
second rebases its Alembic revision.

## Prerequisites

- Read `CLAUDE.md`, `RULES.md`, `status.md`, `modules.md`, and
  `decisions/003-auto-block-fragments-on-the-webhook-path.md` **in full** before starting.
  That file carries the reasoning; this spec carries only the work.
- 5d is merged and deployed. 184 tests, gate green.
- Production holds real client data. Treat it accordingly.
- Server: `ssh -i ~/.ssh/a4a_deploy appuser@67.205.180.62`, sudo limited to
  `systemctl restart a4a-sms`.
- Deploy: `SERVER=appuser@67.205.180.62 SERVICE=a4a-sms ./deployment/deploy.sh`

## The traffic you are designing against

Four distinct strings across 3,037 failures. This is the whole corpus:

| n | string |
|---:|---|
| 2,847 | `Not routable: The destination number is either a landline or a non-routable wireless number.` |
| 112 | `Invalid messaging destination number: The destination phone number was deemed invalid by the carrier.` |
| 76 | `Blocked as spam - temporary: The message was flagged by a SPAM filter and was not delivered.` |
| 2 | `Error code: 400 - {'errors': [{'code': '10002', 'title': 'Invalid phone number', 'detail': 'Invalid destinatio…` |

Every hazard below is real in shape and absent from this account's traffic today. Build
the guards anyway; do not treat their absence as evidence they are unnecessary.

---

# Part A — agent work

## A1. Never block on a transient failure

Refuse to block when the error text carries a transient marker — `temporar`, `retry`,
`congestion`, `try again` — regardless of any fragment hit. This check wins over every
other rule.

`"unreachable"` is standard carrier wording for a switched-off handset (Twilio 30003).
Without this guard, a blast run while a buyer's phone is off deletes that buyer
permanently and nobody ever finds out why the list is shrinking.

If a carrier ever words a permanent failure with "retry", that number survives one extra
campaign and costs half a cent. That is the correct direction to be wrong in.

## A2. Match carrier codes against a structured field, never against prose

- Add an `error_code` column to `sms_messages`, with an Alembic revision.
- Populate it from `errors[].code` on the Telnyx webhook path.
  `app/routers/webhooks/telnyx.py:58` currently builds `f"{title}: {detail}"` and drops
  the code on the floor.
- Match numeric codes against that column only.
- **Remove `"21610"`, `"21612"` and `"40300"` from the text fragment list.** Do not
  `\b`-anchor them.

Anchoring is exactly what failed in `scrub_provider_text()` during 5d — `\b` let
`TelnyxError` through. `\b21610\b` still matches a bare code sitting in prose, and
`+1 321-610-xxxx` and `321-612-xxxx` are assignable Brevard County numbers in the
client's own market.

## A3. Parse the provider error; never stringify it

Row 4 above is a Python dict repr of the provider's raw JSON payload sitting in
`error_message` — which A7/A8 render into `blocked_numbers.notes` and show the client on
the Opt-outs page.

Assemble whatever reaches `error_message` from named fields, and pass it through
`scrub_provider_text()` like everything else the client can see. A `detail` field on a
destination error is precisely where a recipient's phone number would show up.

Add a test that a raw provider payload never reaches `error_message` verbatim.

## A4. Word-boundary the remaining word fragments

Cheap defence in depth. Not the main event — do it, keep it small, move on.

## A5. Never block a recipient for our own misconfiguration

Drop `"has not been enabled for the region"` from the block list. It is a geo permission
on the *sending account* (Twilio 21408). The destination was never the problem, enabling
the region fixes it, and today the number stays blocked forever.

Raise an operator-visible signal instead — this is our configuration error and we should
hear about it. Do not text it (see 5d A4: the alert channel must not depend on the thing
that is failing).

## A6. Carrier opt-outs get their own reason

- Add `carrier_opt_out` to `BLOCK_REASONS`.
- Map opt-out wordings (`unsubscribed`, `opted out`, `opt-out`, and Twilio 21610 via the
  new `error_code` field) to it, not to `delivery_failure` and not to `stop_keyword`.
- Count it in the opt-out figures.

`stop_keyword` is documented in `blocked_number.py:14` as legally binding, and a
carrier's opt-out record is not the same evidence as our own inbound STOP. Keep them
distinct in the record and combined in the metric.

**`OPT_OUT_REASONS` and `dashboard_service.py:248` change in the same commit.** The
invariant at `blocklist_service.py:88-91` — that the blocklist figure cannot disagree
with the dashboard tile — is true today and must stay true. A8's headline and the
dashboard's opt-out rate must tell the same story.

---

## Part A acceptance

Demonstrate each in the transcript.

1. `agent/gate.sh` passes all six checks, twice.
2. All four real strings above classify correctly: landline blocks, deemed-invalid
   blocks, temporary-spam does **not**, raw-JSON row blocks *and* its stored
   `error_message` is no longer a dict repr.
3. ~~A transient-worded error containing `"unreachable"` does not block.~~ Show it
   blocking before the fix and not after.

   **Rewritten by `decisions/004-unreachable-without-a-transient-adjective.md`,
   rider 1.** That decision removes `"unreachable"` from the fragment list
   entirely, at which point this criterion would have passed for the wrong
   reason — the string stops blocking because the fragment is gone, not because
   the guard caught it, so the test would prove nothing about the guard. The
   criterion is now:

   > A transient-worded error **that carries a live block fragment** does not
   > block. Show it blocking before the fix and not after, and show that the same
   > string blocks with the transient markers removed — so the guard is provably
   > what stopped it.

   The second half is the part with teeth and is asserted in
   `test_every_transient_wording_would_block_without_the_guard`, which makes the
   discriminating property a check rather than the test author's judgement.
   Separately, `"unreachable"` wordings must not block; that is decision 004's
   own criterion and is asserted apart from the guard, under
   `test_an_unreachable_wording_does_not_block`.
4. An error whose text contains `+13216105555` does not block on the `21610` fragment.
5. An error carrying structured code `21610` **does** block, as `carrier_opt_out`.
6. A region-permission error does not block the recipient and raises the operator signal.
7. The blocklist opt-out figure and `dashboard_service`'s opt-out rate agree, with
   `carrier_opt_out` rows present.
8. Every new test goes red against the pre-fix tree — show both directions.
9. After deploy: seven screens 200 over HTTPS, no carrier name or raw provider payload in
   any rendered page or API response.

Wire into a `/goal` stop condition with a turn cap, then a fresh-context review pass.

## Constraints

- Do not touch `.env`, `.env.production`, `agent/gate.sh`, `agent.config.sh`.
- **Do not send SMS. Do not modify, delete or re-import contact data.**
- **Do not unblock anything.** The 2,959 currently-blocked numbers stay blocked; this
  session changes what happens to *future* failures. Whether any past block was wrong is
  a separate question and a separate decision.
- No source file over 500 lines. `app/sms/` stays DB-free.

## Explicitly out of scope

- Line-type screening at import — endorsed in decision 003, belongs with the import flow.
- Re-adjudicating existing blocklist rows.
- Anything in 5e's file set.
