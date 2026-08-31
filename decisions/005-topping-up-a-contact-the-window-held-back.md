# Should a top-up reach someone the hold-back window held back, once the hold has cleared?

**Blocks:** nothing today — 5e Part A is complete, `agent/accept-5e.sh` passes
checks 1-10 and the gate is green twice. This is a gap a client will find in the
first week, and the wording that hides it is already fixed.
**Why this is not mine to decide:** escalation item 5 (opt-out and suppression
behaviour). "The recent-contact suppression window… These decide whether a real
person gets a text they didn't want. Implement what the spec says; do not tune
the rules yourself." `sessions/session-5e.md` A4 says what a top-up must **not**
do — "a top-up must never re-send to someone the campaign already reached" — and
is silent on someone it never reached.

## Context

Suppression is partitioned when the draft is built
(`campaign_builder.create_campaign()`, `app/services/campaign_builder.py:149`),
and every held-back contact gets a real `sms_messages` row with
`status="skipped"` (`:200-208`). That row is what puts the count on screen before
the send, and it is correct.

The consequence is that `skipped` now means two unrelated things on the same
column:

| written where | meaning | permanent? |
|---|---|---|
| `campaign_builder.py:205` | held back by the window | **no** — clears with time |
| `campaign_service.py:383` | destination region not enabled | yes |

Nothing downstream can tell them apart, and a top-up has to. Found by the
fresh-context review of this session, reproduced:

```
window = 3; a 4-person list, 2 of them texted yesterday
create  -> total_recipients=2  suppressed=2
send    -> completed, sent 2
rows    -> [(…0001,'skipped'), (…0002,'skipped'), (…0003,'sent'), (…0004,'sent')]

window lowered to 0   (the hold has cleared; this is production's setting)
top-up  -> refused, "nothing to send"
```

Two real buyers were never texted, the rule that stopped them has expired, and
there is no way to reach them inside that campaign. The client's remedy is to
rebuild the campaign — which is exactly what A4 exists to avoid, and which loses
the first send's numbers.

**Two things about this are already fixed and are not what I am asking about.**
The refusal used to read "Everyone in this audience has already been through this
campaign", which is a false statement about people who were never texted; it now
describes the *list* instead. And the top-up's candidate set is now "members of
the campaign's list added since the campaign was created", which is A4's own
wording and which fixed two worse defects (a top-up defeating `batch_size`, and a
top-up on an `all` audience texting everyone imported since for any reason). The
held-back contacts were on the list *before* the campaign, so they are outside
that window either way.

## Options

1. **Leave it.** A top-up is for contacts added since, full stop; someone the
   window held back is reached by the next campaign, which is what the window is
   for. / cost: a client who runs one campaign per auction never sends that
   auction's message to those buyers at all. At the 3-day window that shipped,
   that was 6,856 of 6,857 people.

2. **A top-up re-adjudicates held-back rows.** Include contacts whose only row
   for this campaign is a suppression `skipped`, re-run the window against
   today's value, and flip the existing row to `pending` rather than writing a
   second one. / cost: needs `skipped`-for-suppression to be distinguishable from
   `skipped`-for-region — a new `MESSAGE_STATUSES` member (`held_back`, outside
   `BILLABLE_STATUSES`, the same shape as 5d's `not_sent`) and a decision about
   the existing rows on the box, which cannot be classified after the fact.

3. **A separate "send to the ones held back" action**, distinct from Top up and
   named for what it does. / cost: a third send entry point on a screen that has
   two, and the same status problem as option 2 underneath it.

## Recommendation

**Option 2**, with the new status and no backfill — existing `skipped` rows keep
their meaning and are never re-adjudicated, so the change only affects campaigns
built after it lands. It is the option that makes the product's behaviour match
what the client believes is happening, and the status split is worth having on
its own merits: `skipped` carrying a temporary deferral and a permanent
exclusion is the overloaded-column mistake CLAUDE.md opens with, and every future
reader of that column has to re-derive which one they are looking at.

Option 1 is defensible only while the window is 0, which is where production sits
today — so this is not urgent, and it becomes urgent the moment anybody raises it.

---

# Decision — Option 2, and it is urgent

**Decided by:** Jordan (via Cowork), 2026-08-27
**Status:** resolved

Add the status. Re-adjudicate held-back rows on top-up. No backfill.

## One correction: this is urgent, and not for the reason given

The escalation says option 1 is defensible while the window is 0, which is where
production sits, so this is not urgent.

But **5e A5 put the window on the Settings page this session.** Before today it was an
`.env` value only someone with ssh could change. Now the client can raise it himself,
from a form, with a plain-language description telling him what it does — and the moment
he does, every contact it holds back becomes unreachable inside that campaign, silently,
with no way back except rebuilding the campaign and losing the first send's numbers.

A5 and this defect are safe apart and hazardous together, and they landed in the same
session. So this lands before the client gets the login, alongside A5 — not after.

## Why option 2 rather than 1 or 3

`sms_message.py:11` documents `skipped` as *"filtered before send (wrong region) —
non-billable, not a failure."* `campaign_builder.py:205` writes suppression rows under
it. The rows contradict the model's own docstring, which is worse than an overloaded
column — it is a column whose contract is already written down and already violated.

And the codebase has half-made this decision already. `campaign.py` splits
`suppressed_count` from `skipped_count` with a comment spelling out why: *"we held 37
people back so they aren't texted twice this week" and "37 numbers were undeliverable"
are different news.* Splitting the counters and conflating the rows underneath them is
an unfinished decision, not a design.

Option 1 loses the auction. At the 3-day window that shipped, that was 6,856 of 6,857
people who never received the message for the sale they were being invited to. Option 3
adds a third send button to a screen with two and still needs the status split
underneath, so it pays option 2's cost without option 2's simplicity.

## Riders

**1. `held_back` is the right name — keep it.** It matches the words A6 already shows the
client ("held back, clears 10:11am"), and a status the operator sees named differently
from the sentence they read about it is a small tax paid forever. Outside
`BILLABLE_STATUSES`, same shape as 5d's `not_sent`.

**2. No backfill, exactly as you propose.** Existing `skipped` rows cannot be classified
after the fact and must not be guessed at. Same principle as 5g's "do not unblock
anything": a rule change governs future events, and re-adjudicating history on an
inference is how you turn one defect into an unauditable set of them. The change affects
campaigns built after it lands. Say so in the migration comment.

**3. Flip the existing row, don't write a second one.** As specified. A person held back
once and reached later is one row with a history, not two rows that a report has to
reconcile.

**4. Re-run the window against today's value, not the campaign's.** The hold is a
property of now, not of when the draft was built.
