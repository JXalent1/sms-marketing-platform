# Which carrier failures should permanently delete a buyer from the list?

**Blocks:** nothing today — 5d Part A is complete and the gate is green. This is
the ruling that should land before the client gets a login.
**Why this is not mine to decide:** escalation item 5 (opt-out and suppression
behaviour). "Implement what the spec says; do not tune the rules yourself."

## Context

Session 5d A7 did what it was told: `should_auto_block()` now also runs on the
delivery-webhook failure path (`app/routers/webhooks/common.py:113`), blocking
with `reason="delivery_failure"`, and `"deemed invalid"` was added to
`AUTO_BLOCK_ERROR_FRAGMENTS` (`app/sms/compliance.py:87-93`). The spec asked me
to verify one transient wording — `"Blocked as spam - temporary"` — and it does
not match. That is tested in both directions.

What the spec could not anticipate is the change in **exposure**. Before 5d that
function saw only submission rejections, where a carrier refuses outright. It now
sees every delivery failure: 2,673 events in campaign 4 against a handful on the
old path. The fragment list was written for the narrow path and is now on the
wide one, and it is a list of unanchored substrings with no transient exclusion.

Three separate problems, all found by the 5d review and all reproduced by running
`should_auto_block()`:

**1. `"unreachable"` is standard carrier wording for a handset that is switched
off.** Twilio 30003 is documented as *Unreachable destination handset*.

    'Recipient temporarily unreachable'                          -> BLOCKS
    'Handset temporarily unreachable, will retry'                -> BLOCKS
    'Service unavailable: upstream carrier unreachable, retry'   -> BLOCKS
    'SMPP bind failed - SMSC unreachable'                        -> BLOCKS

The last two block the *recipient* for a carrier-side outage. A blast run while a
buyer's phone is off now deletes that buyer permanently, and the client never
finds out why his list is shrinking.

**2. The numeric fragments collide with phone numbers quoted in error text.**
`"21610"`, `"21612"` and `"40300"` are plain `in` tests against the whole string,
and `error_detail` on the Telnyx path is built as `f"{title}: {detail}"` from
carrier-controlled JSON (`app/routers/webhooks/telnyx.py:58`):

    'Delivery to +13216105555 timed out at the carrier, retry later' -> BLOCKS
    'Temporary network congestion sending to +13216125555'           -> BLOCKS
    'Delivery report ref 9f21610a-... pending'                       -> BLOCKS

+1 321-610-xxxx and 321-612-xxxx are assignable Brevard County, Florida numbers —
the client's own market.

**3. `"has not been enabled for the region"` blocks the recipient for *our*
account misconfiguration.** Twilio 21408 is a geo-permission setting on the
sending account. Enabling the region fixes it; the destination was never the
problem, and the number stays blocked forever.

**4. Genuine carrier-level opt-outs are filed as delivery failures, so A8's new
headline under-reports the figure it exists to fix.** `common.py:116` hard-codes
`reason="delivery_failure"`, as the spec instructed — but `"unsubscribed"`,
`"opted out"`, `"opt-out"` and code 21610 (Twilio: *Attempt to send to
unsubscribed recipient*) are opt-outs. `blocklist_service.OPT_OUT_REASONS` is
`("stop_keyword",)`, matching the dashboard tile. Four webhooks, one a real
opt-out:

    counts: {'opt_outs': 0, 'unreachable': 4, 'other': 0, 'total': 4}

A8 exists because the headline counted the wrong thing. A7 now feeds it the wrong
thing from the other direction, and `blocklist_service.py:88-91` claims this
figure cannot disagree with the dashboard.

## Options

1. **Rule out transient failures, then leave the list alone** — refuse to block
   when the text matches a transient marker (`temporar`, `retry`, `congestion`,
   `try again`), regardless of fragment hit. Cost: one more heuristic, and it
   silently protects nothing if a carrier words a permanent failure with "retry".
   Fixes 1 and part of 3; does nothing for 2 or 4.
2. **Anchor the list: word-bound the words, and match the numeric codes only
   where the carrier actually puts a code** (a dedicated field, or `\b21610\b`).
   Cost: `\b21610\b` still matches a bare `21610` in prose; the real fix is to
   stop matching free text for codes at all, which means reading the carrier's
   structured error field per provider. More work, and it is per-carrier work.
3. **Both, plus map opt-out fragments to `reason="stop_keyword"`** so the
   headline counts them where the client will look. Cost: `stop_keyword` is
   described in `blocked_number.py:14` as "legally binding", and a carrier's
   opt-out record is not the same evidence as our own inbound STOP. That may be a
   distinction worth keeping, in which case A8 needs a third bucket instead.
4. **Do nothing and watch it.** Cost: the failure is invisible by construction —
   a wrongly blocked buyer simply stops appearing in campaigns, and the only
   trace is a `delivery_failure` row among thousands of correct ones.

## Recommendation

Option 3, sequenced: the transient guard first, because it is the one that is
losing real buyers today and it is a single condition; then the numeric anchoring;
then the opt-out mapping, which is a reporting fix rather than a harm.

Worth pairing with the item already in `modules.md` under "Found in live use":
line-type screening at import. A7 stops a dead number recurring *after* one paid
failure. Screening at import stops paying for the first one, and a number that
never enters the list never needs a rule to get it out.

Run the fragment list against the distinct error strings in campaign 4's
2,673-failure dataset before deciding. That dataset answers the empirical half of
this — the four wordings above are reproduced from carrier documentation, not
from A4A's own traffic, and the traffic is what matters.

---

# Decision — Option 3, resequenced against real traffic

**Decided by:** Jordan (via Cowork), 2026-08-26
**Status:** resolved

## What the traffic actually says

The whole failure corpus for this account is **four distinct strings** across 3,037
failures (run against the deployed pre-5d fragment list):

| n | blocks today | string |
|---:|---|---|
| 2,847 | yes | `Not routable: The destination number is either a landline or a non-routable wireless number.` |
| 112 | no | `Invalid messaging destination number: The destination phone number was deemed invalid by the carrier.` |
| 76 | no | `Blocked as spam - temporary: The message was flagged by a SPAM filter and was not delivered.` |
| 2 | yes | `Error code: 400 - {'errors': [{'code': '10002', 'title': 'Invalid phone number', 'detail': 'Invalid destinatio…` |

Every current outcome is correct. The 112 begin blocking once 5d deploys, which is the
intended effect of adding `"deemed invalid"`. The transient-spam 76 are correctly left
alone.

**So every hazard in this escalation is real in shape and hypothetical in this account's
traffic today.** None of `"unreachable"`, `"21610"`, `"40300"` or the region wording
appears even once. That does not make them wrong — it makes them cheap insurance rather
than an emergency, and it sets the order.

## What the data added that the escalation did not have

Row 4 is a **Python dict repr of the provider's raw error payload**, stored in
`error_message`. A7 and A8 make that field the source of `blocked_numbers.notes`, which
is rendered to the client on the Opt-outs page.

This is the same defect class the 5d review just fixed in `scrub_provider_text()`, from
the other end: there the risk was the carrier's *name* surviving a bad regex; here it is
the carrier's *entire JSON structure* being stringified into a client-facing column.
Only two rows so far, and the visible prefix happens not to carry a brand — but nothing
makes that true in general, and `detail` on a destination error is exactly where a
recipient's phone number would appear.

It is also the direct cause of hazard 2. The reason `"21610"` can collide with a Brevard
County number is that codes are being matched against prose that contains numbers. Row 4
proves the structured code (`'code': '10002'`) is present and being discarded on the way
into a string. Parse it instead of regexing it.

## Ruling, in this order

**1. Never block on a transient failure.** A buyer whose handset was off during a blast
must not be deleted. Refuse to block when the text carries a transient marker
(`temporar`, `retry`, `congestion`, `try again`, `will retry`), regardless of any
fragment hit. The escalation's own objection — that a carrier might word a permanent
failure with "retry" — is the safe direction to fail: a dead number that survives one
extra campaign costs half a cent, and a live buyer deleted costs the buyer.

**2. Capture the carrier error code as a structured field; stop matching codes in prose.**
Add an `error_code` column, populate it from `errors[].code` on the Telnyx webhook path
(`telnyx.py:58` currently keeps title and detail and drops the code), and match numeric
codes against that column only. Remove `"21610"`, `"21612"`, `"40300"` from the text
fragment list entirely — do **not** `\b`-anchor them. Anchoring is what failed in
`scrub_provider_text()` this same session; `\b21610\b` still matches a bare code inside
prose, and prose is not where codes belong.

**3. Parse the provider error rather than stringifying it.** Row 4 should never have
become a dict repr. Whatever reaches `error_message` must be assembled from named fields
and passed through `scrub_provider_text()`, same as everything else the client can see.

**4. Word-boundary the remaining word fragments.** Cheap, and it stops
`"landline"` matching a hypothetical `"landlines-are-fine"`. Defence in depth, not the
main event.

**5. Never block a recipient for our own account misconfiguration.** `"has not been
enabled for the region"` is a sending-account geo permission. Drop it from the block
list. It should raise an operator alert instead — the destination was never the problem
and enabling the region fixes it.

**6. Carrier opt-outs get their own reason, not `stop_keyword`.** Add `carrier_opt_out`
to `BLOCK_REASONS`. You are right that a carrier's record is not the same evidence as our
own inbound STOP, and collapsing them would weaken a compliance record that exists to be
audited. Count it in the opt-out figures rather than under delivery failures.

`OPT_OUT_REASONS` and `dashboard_service.py:248` must change in the same commit. The
invariant asserted at `blocklist_service.py:88-91` — that this figure cannot disagree
with the dashboard — is currently true and must stay true. Zero of these appear in the
traffic today, which is why this is last, not because it does not matter.

## Also endorsed

The pairing suggested at the end of the escalation is right and is already logged in
`modules.md` under "Found in live use": **line-type screening at import**. A7 stops a
dead number recurring after one paid failure; screening stops paying for the first one.
2,959 of A4A's numbers are now known dead — at roughly half a cent a segment that is
about $15 a blast, every blast, until they are screened at the door instead of at the
exit.
