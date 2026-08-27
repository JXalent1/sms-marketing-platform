# Should "unreachable" block a buyer when the carrier does not call it temporary?

**Blocks:** nothing today — 5g Part A is complete, `agent/accept-5g.sh` passes
checks 1-8 and the gate is green twice. This is a gap between the rule decision
003 ordered and the reason it gave for ordering it.
**Why this is not mine to decide:** escalation item 5 (opt-out and suppression
behaviour). "Implement what the spec says; do not tune the rules yourself."

## Context

Session 5g A1 says, verbatim:

> Refuse to block when the error text carries a transient marker — `temporar`,
> `retry`, `congestion`, `try again` — regardless of any fragment hit.
>
> `"unreachable"` is standard carrier wording for a switched-off handset (Twilio
> 30003). Without this guard, a blast run while a buyer's phone is off deletes
> that buyer permanently and nobody ever finds out why the list is shrinking.

Both halves are implemented (`app/sms/compliance.py:103`, checked first at
`:232-234`), and the four markers are exactly the four named. The gap is that
**Twilio 30003's own `ErrorMessage` carries none of them.** It reads
`Unreachable destination handset`. Run against the shipped classifier:

    classify_failure("Unreachable destination handset")
      -> block=True, reason=delivery_failure
    classify_failure("Unreachable destination handset", "30003")
      -> block=True, reason=delivery_failure
    classify_failure("SMPP bind failed - SMSC unreachable")
      -> block=True, reason=delivery_failure

So the wording A1's rationale is *built on* still deletes the buyer. Of decision
003's four quoted examples, the guard catches the two that happen to contain
"temporarily"/"retry" and misses the two that do not — including
`SMPP bind failed - SMSC unreachable`, which is a carrier-side outage condemning
the *recipient*.

`"unreachable"` was left in `AUTO_BLOCK_ERROR_FRAGMENTS` deliberately: acceptance
criterion 3 asks for a transient-worded error *containing* "unreachable" to stop
blocking, which only means something if the fragment is still there. That reading
is right for the criterion and leaves the fragment doing harm on every wording
the carrier does not decorate.

**None of this is in A4A's traffic.** The whole failure corpus is four strings
across 3,037 failures and not one contains "unreachable". This is the same class
of cheap insurance as the rest of 5g — which is exactly why it is a question
rather than a patch.

## Options

1. **Add 30003 and its siblings to a structured transient-code set** — treat the
   codes carriers document as retryable as transient, the way `21610` is now
   treated as an opt-out. Cost: a per-carrier list to maintain, and it does
   nothing for the same failure arriving with no code at all, which is how the
   Telnyx path mostly reports.
2. **Drop `"unreachable"` from the fragment list entirely.** Cost: a genuinely
   dead line that the carrier describes only as "unreachable" survives, and gets
   paid for once per campaign forever. Acceptance criterion 3 becomes vacuous —
   the transient guard would no longer be what stops that string, so the test
   proving it stops would prove nothing.
3. **Require a second, permanence-bearing signal before blocking on
   `"unreachable"` alone** — block only when the text also carries a
   dead-number word (`landline`, `not a mobile`, `deemed invalid`) or a
   structured code. Cost: a rule with two tiers where every other fragment has
   one, and it is a real narrowing — some carriers say only "unreachable" and
   mean it.
4. **Leave it and log it.** Cost: the buyer this guard was written to protect is
   still deleted whenever the carrier is terse, and the reason it looks fixed is
   that the guard's test data is decorated with adjectives the live carrier does
   not always use.

## Recommendation

Option 3, restricted to `"unreachable"` and to nothing else. It is the only one
that keeps the fragment earning its place while making the wording match the
harm: "unreachable" on its own is ambiguous between a dead line, a switched-off
handset and an SMSC outage, and the other two fragments in the list are not.
Option 1 is worth doing *as well*, whenever a second carrier goes live and there
is a code table worth maintaining.

Worth deciding against the same evidence decision 003 used: A4A's own traffic
says this has never happened here. If the answer is option 4, say so explicitly
and it can be recorded in `compliance.py` as a known limit rather than sitting in
a `status.md` bullet where the next session will re-find it.

---

# Decision — Option 2, not Option 3

**Decided by:** Jordan (via Cowork), 2026-08-26
**Status:** resolved

Drop `"unreachable"` from `AUTO_BLOCK_ERROR_FRAGMENTS` entirely.

## First, this is my error

5g A1 named Twilio 30003 as the justification for the transient guard and then
specified a mechanism — four adjective markers — that 30003's own `ErrorMessage`
does not contain. The rationale and the rule did not line up, and the escalation
is right that the guard's tests look convincing only because their sample data
carries adjectives the live carrier does not always use.

## Why Option 3 collapses into Option 2

Option 3 proposes blocking on `"unreachable"` only when the text also carries a
dead-number word — `landline`, `not a mobile`, `deemed invalid` — or a structured
code.

But if the text carries `"landline"`, then `"landline"` already blocks it. Same for
the other two. In the text-only case, Option 3 gives `"unreachable"` a job that is
already done by the fragment sitting next to it in the same string, and the rule
contributes nothing except a second tier of logic to maintain and misread.

The part of Option 3 that isn't redundant is the structured-code half — and codes
should be matched as codes, which is what 5g A2 already built the `error_code`
column for. That belongs in Option 1, not in a two-tier text rule.

## Why dropping it is right on the merits

`"unreachable"` is ambiguous across three meanings, and two are transient:

- a dead line (permanent)
- a switched-off handset (transient — Twilio 30003)
- an SMSC or upstream outage (transient, and not about the recipient at all)

No other fragment in the list has that problem. `"landline"` means one thing.

And the cost asymmetry is not close. A wrongly kept dead number costs about half a
cent per campaign — at one blast a day, under two dollars a year. A wrongly deleted
buyer at an auction house costs a bidder, permanently and invisibly, and the client
never learns why his list is shrinking. When a signal is ambiguous, the cheap error
is the one to make.

**The deeper point: permanence is a property of the line, and we have a proper way
to establish it.** Line-type lookup answers "is this number textable" authoritatively
for about $0.004. An error string is a lossy proxy for that question. Building a
two-tier heuristic on a lossy proxy, when the authoritative check is already on the
roadmap under "Found in live use", is work that gets thrown away the moment the real
check ships.

## Riders

**1. Acceptance criterion 3 must be rewritten, not deleted.** The escalation is
correct that with the fragment gone, "a transient-worded error containing
`unreachable` does not block" proves nothing — the guard is no longer what stops it.
Re-point that criterion at a wording that *does* carry a live fragment, so the test
exercises the guard rather than its absence.

This is the same failure the mutation harness caught in the parametrized transient
cases: green ticks standing in for proofs. Do not preserve a harmful rule in order to
keep a test meaningful — fix the test.

**2. Record the residual in `compliance.py`, as you asked.** A comment at the
fragment list saying that a line the carrier describes *only* as "unreachable"
survives on purpose, that the asymmetry above is the reason, and that line-type
screening at import is the intended real fix. A `status.md` bullet gets re-found and
re-litigated; a comment at the site does not.

**3. Option 1 stays open and is worth doing — later.** A structured transient-code
set (30003 and siblings) is the correct mechanism, and 5g A2's `error_code` column is
the seam for it. Do it when a second carrier goes live and there is a code table
worth maintaining, or when Telnyx starts populating codes reliably on the delivery
path. Not now: today it would be a table of one entry that A4A's traffic has never
produced.

## For the record

This was decided against the same evidence as 003: A4A's entire failure corpus is
four strings across 3,037 failures and not one contains "unreachable". The change is
cheap insurance either way. What tipped it is that Option 2 removes a rule, and
Option 3 adds one — and the rule being removed is the only one in the list whose
plain meaning does not match the action it triggers.
