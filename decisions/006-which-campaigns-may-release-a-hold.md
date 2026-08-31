# Which campaigns may release a hold the suppression window froze?

**Blocks:** nothing today — 5h Part A is complete, `agent/accept-5h.sh` passes
checks 1-8b and the gate is green twice. Both cases below currently do **nothing**,
which is exactly what they did before 5h, so no behaviour is waiting on this.
**Why this is not mine to decide:** escalation item 5 (opt-out and suppression
behaviour) — "these decide whether a real person gets a text they didn't want" —
and item 10 for the first case, which `sessions/session-5h.md` and
`decisions/005` are both silent on.

## Context

5h A1 implements decision 005: a top-up re-adjudicates the rows the hold-back
window froze (`held_back`, `app/services/campaign_release.py`) and flips them to
`pending`. Two kinds of campaign carry held-back rows that the release does not
reach, and in both the reason is structural rather than an oversight.

### 1. A campaign the client capped

`campaign_builder.create_campaign()` partitions suppression **before** applying
`batch_size` (`campaign_builder.py:185-195`), deliberately — the comment says
*"'Send to the first 50' has to mean fifty people receive it"*. So the cap is
applied to the sendable set and **never** to the held-back rows, which are
written for every suppressed contact. Reproduced during this session's review:

```
20-person list, 10 texted yesterday, window 3, cap 3
build   total_recipients=3   suppressed=10   held_back rows=10
send    completed, sent 3
release (before the guard below) -> 10 rows, one click, 13 sent
```

At this client's numbers — a 6,857-person list, a 3-day window that held 6,856
of them, a cap of 50 for a test send — that is one click queueing six thousand
messages against a campaign he capped at fifty.

`campaign_release.releasable()` now returns nothing for a campaign carrying a
`batch_size`, and `campaign_topup.CAPPED_CAMPAIGN_HOLD` says so. **That is the
absence of a change, not an answer**: it is what a capped campaign did before
5h. `campaigns.batch_size` (migration `a91d5f2c6b70`, additive, nullable) exists
because the cap was applied and thrown away, so nothing downstream could even
tell one had been asked for — 5e hit the same blind spot from the other side.

### 2. A campaign the window suppressed entirely

If every resolved recipient is inside the window, `total_recipients` is 0, the
send loop finds nothing pending, and `campaign_outcome.zero_send_reason()` ends
the campaign **`aborted`** (`campaign_service.py:466`). `campaign_topup.assess()`
refuses anything that is not `completed`, and `TOP_UP_STATE_ERRORS["aborted"]`
carries a comment explaining why that must not be loosened casually: nothing in
this codebase moves a campaign back from `aborted`, and decision 002's
justification for refusing before queueing is that a campaign which never ran can
simply be re-run.

So a 100%-suppressed campaign keeps decision 005's original defect. Reproduced:

```
build   total=0  suppressed=2   send -> aborted
top-up  refused: "This campaign was stopped before it sent and cannot be topped
        up. Create a new campaign for these contacts."
held_back rows still on it: 2
```

Decision 005's own example is 6,856 of 6,857 — one send, so `completed`, so the
release works. One more suppressed contact and it does not. A client re-sending
to the same list two days running at a 3-day window lands here.

## Options

1. **Leave both.** A capped campaign is a test send and an aborted one is
   re-run; the held-back people are reached by the next campaign, which is what
   the window is for. / cost: case 2 is decision 005's defect surviving at
   exactly the ratio that decision was written about, and the client cannot tell
   from the screen which side of it he is on.

2. **Case 1 only: release up to the cap.** A capped campaign releases at most
   `batch_size` held-back rows per top-up. / cost: invents a meaning for the cap
   that nobody has stated — is it per run or per campaign? — and "50 more each
   time you click" is a strange control. Needs a rule for which 50.

3. **Case 2 only: let a fully-suppressed campaign be topped up.** Distinguish
   "aborted because pre-flight refused" from "aborted because everyone was held
   back", and allow the release on the second. / cost: a second abort reason has
   to become a real column — matching on `abort_reason` text is the
   overloaded-string mistake this project opens with — and it is the first thing
   in the codebase to move a campaign out of `aborted`, which decision 002's
   comment warns against by name.

4. **Both, via option 2 and option 3.**

## Recommendation

**Option 3, and not option 2.**

Case 2 is the defect decision 005 was written to remove, one recipient along
from its own example, and the remedy the screen offers ("create a new campaign")
is the one that decision exists to make unnecessary. It is worth a column:
`campaigns.abort_kind`, or a `held_back_only` boolean set where
`zero_send_reason()` already knows which branch it took — the information is
present at the moment of the abort and is only lost because nothing stores it.

Case 1 is different in kind. A cap is a number the client typed, and every
answer other than "do nothing" sends to somebody he did not count. Leaving it
refused costs him one rebuild of a campaign he had already marked as a test.
That is the cheap error, and the errors here are asymmetric in the way
`decisions/004` describes.

---

# Decision — Option 1 for both, with a mandatory wording fix

**Decided by:** Jordan (via Cowork), 2026-08-27
**Status:** resolved

This departs from the recommendation on case 2. The reasoning is below; if it is wrong,
the thing that makes it wrong is named at the end.

## Case 1 — a capped campaign: leave it refused. Agreed.

Nothing to add to your own argument. A cap is a number the client typed, every answer
except "do nothing" sends to somebody he did not count, and option 2 invents a semantics
for `batch_size` that nobody has stated. "Fifty more each time you click" is not a
control anyone asked for.

Recording `campaigns.batch_size` was right independent of the ruling — a cap that was
applied and thrown away is a fact the system needed and did not have, which is the same
blind spot 5e hit from the other side.

## Case 2 — a fully-suppressed campaign: also leave it, and here is why I disagree with you

Your case is that this is decision 005's defect surviving one recipient along from 005's
own example. That is true as stated and I think it misses an asymmetry that changes the
answer.

**In 005's scenario, rebuilding loses something. In case 2, it loses nothing.**

005's example is 6,856 held and **1 sent**. Rebuild that campaign and you lose the record
of the send that happened — the delivered row, the cost, the campaign's place in the
history. That loss is what made "create a new campaign" an unacceptable remedy and what
justified the whole re-adjudication mechanism.

A 100%-suppressed campaign sent **zero** messages. There is no delivery record, no cost,
no history to preserve. A new campaign built from the same list, after the hold clears,
is identical in every respect to what a release would have produced. The remedy the
screen already offers is not a worse version of the fix — it *is* the fix, reached by a
different route.

Against that, option 3 costs a new column and makes this the first thing in the codebase
to move a campaign out of `aborted` — which `TOP_UP_STATE_ERRORS["aborted"]` warns
against by name, and which I wrote into decision 002 on purpose. That is real
architectural weight to buy a few clicks.

## The actual defect in case 2 is the sentence, not the state

> "This campaign was stopped before it sent and cannot be topped up. Create a new
> campaign for these contacts."

That is true, and it is useless. It does not say *why* nothing sent, and it does not say
*when* the client could try again. He is left thinking the tool broke — which is exactly
what happened to Jordan for two consecutive campaigns before anyone ran SQL against it.

**Required:** when a campaign aborts because the hold-back window took everyone, say so,
and say when it clears:

> "Nobody was sent this — all 2,140 contacts were texted within the last 3 days. The hold
> clears at 10:11am. Create the campaign again after that."

The information is present at the moment of the abort; `zero_send_reason()` already knows
which branch it took, and the clearing time is computable from `max(last_messaged_at)`
across the held set plus the window. Reuse A6's wording so the composer and the abort
reason say the same thing about the same rule.

That is a wording change plus a computed timestamp. No new state transition, no
`abort_kind` column, and the client's confusion — which is the real harm here — is gone.

**Same requirement for case 1.** `CAPPED_CAMPAIGN_HOLD` must name the cap and the
remedy: "This campaign was capped at 50. 6,806 contacts are still held back — create a
new campaign to reach them." Refusing without explaining is how a correct guard reads as
a broken tool.

## What would change this ruling

If the client turns the window on and hits case 2 repeatedly — same list, consecutive
days, an auction cadence that collides with the window — then the rebuild stops being a
few clicks and starts being the daily workflow. At that point `abort_kind` and your
option 3 are the right answer and this decision should be reopened rather than worked
around.

Watch for it. The window is 0 in production today, so nothing can hit this yet; the
moment it is raised, this is the first thing to check.

## Endorsed without change

- **The release not requiring a `list:` audience.** A held-back row is a contact the
  campaign resolved for itself, which is categorically different from "everyone who
  joined `all` since". Right call, right reason.
- **Editing three files outside the module's table.** The endpoint and the confirm dialog
  both told the client a top-up sends "to everyone added to its list", and that stopped
  being true. A stale sentence about who receives a text is not a documentation problem.
  Same principle as the 5g spec edit — see `RULES.md`.
- **Rejecting the `_pre_fix_verdict()` finding.** Your explanation holds.

## On A2 — you were right and my spec was wrong

The premise in `session-5h.md` A2 was that a one-segment send requires `$0.00`. At
`WHOLESALE_COST_PER_SEGMENT = 0.009` it rounds *up* to `$0.01` and was already refused;
the `$0.00` case needs a rate under half a cent. I asserted a threshold without checking
it against the configured rate, which is the same failure as 5g A1's rationale — see
`CLAUDE.md`, "a rationale and its mechanism have to be checked against each other".

The real defect you found — a sub-cent under-requirement, $0.075 asked against $0.081
true — is smaller and genuinely present. Taking the larger of the exact cost and the
caller's stated one is better than what the spec asked for, because replacing the rounded
figure outright would have loosened the guard wherever rounding went up. Escalation item
3 permits tightening only, and you read that correctly.
