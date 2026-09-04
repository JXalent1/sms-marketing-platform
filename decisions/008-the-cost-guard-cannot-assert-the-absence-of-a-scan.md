# Should the cost guard assert "no full scan of sms_messages or contact_list_members"?

**Blocks:** nothing — 5j Part A is complete, `agent/accept-5j.sh` exits 0 on all ten
criteria, the gate is green twice at 613 tests, and the mutation run is 19 caught / 0
survived. This records a ruling on a departure already shipped and supersedes a clause in
`sessions/session-5j.md`.
**Why this is recorded rather than absorbed:** `RULES.md` — a session spec is not edited
after the fact except when a resolved decision supersedes something in it. A2's assertion
is wrong and leaving it wrong is worse than editing it.

## Context

`sessions/session-5j.md` A2 asked for a query-plan guard and named the assertion:

> Add an `EXPLAIN QUERY PLAN` assertion over the freshness join: **no full scan of
> `sms_messages` or `contact_list_members`.**

The session measured both plans before writing the check, which is the discipline
`CLAUDE.md` asks for — run Y through X before shipping the spec — and found the assertion
is wrong in **both** directions.

**The 10m02s plan, at production row counts:**

```
SCAN contact_list_members USING COVERING INDEX sqlite_autoindex_contact_list_members_1
SEARCH sms_messages USING INDEX idx_sms_status (status=?)
```

**The plan that ended the outage** searches `sms_messages` through `idx_sms_contact` and
still scans `contact_list_members` outright — a single pass over the smaller table is the
correct shape for this join, not a defect.

So of the two halves I specified:

- **"No full scan of `sms_messages`"** is **green on the outage plan.** There is no scan
  of that table in it. `status` has four distinct values and the filter is an `IN` over
  two of them, so SQLite reaches it through `idx_sms_status` and calls walking half the
  table a SEARCH. The word the assertion hunts for never appears in the plan that cost ten
  minutes.
- **"No full scan of `contact_list_members`"** is **red on the schema that fixed
  production.** It is red on the outage plan too. An assertion that fails on both the
  broken and the repaired schema distinguishes nothing and would have blocked its own fix.

One clause always green, one always red. Neither separates the two plans.

## First, this is my error — and it is the same error as `decisions/007`

Both superseded clauses specify a **mechanism** where the spec should have stated a
**property**. 007 said "subtract the prospect routes by module" when the property was "the
sweep covers the audience surface and its exceptions cannot rot". This one said "assert no
full scan" when the property is "the join reaches its tables through an index on the
column it joins on".

A mechanism written from a plausible mental model is a guess with the authority of a spec
behind it, and the session then has to choose between shipping a guess and departing from
its own acceptance criteria. Twice now the right call was to depart. The rule I am taking
from it: **state the property and require the session to prove its chosen mechanism
against a case whose answer is known** — which A2 does, in
`test_the_guard_goes_red_on_the_schema_it_exists_to_reject`.

## The ruling

**Accepted, and it is better than what I specified.** What shipped asserts:

1. **The join reaches a table through an index on `contact_id`** —
   `join_key_searches()` in `tests/_query_plan.py`, reading the plan for the statement
   `_last_sent_by_list()` actually issues rather than a hand-written copy of it.
2. **Both sides carry an index whose *leading* column is `contact_id`.** Leading, because
   `contact_list_members` looked indexed before 5j and was not: the unique
   `(list_id, contact_id)` index leads with `list_id` and cannot serve a `contact_id`
   lookup. Both, because which side SQLite searches is a statistics decision that flips
   between suite scale and production scale — an index on only today's favoured side is
   one `ANALYZE` away from being the wrong one, and the plan check alone would not notice.

And the rejected assertion is kept **as a live demonstration rather than a comment**:
`test_the_guard_goes_red_on_the_schema_it_exists_to_reject` runs my rule against the
outage plan and asserts it passes. A spec clause that would have missed the incident it
was written for is worth a failing example somebody can execute, not a paragraph they can
skim.

## Riders

**1. The spec's A2 assertion is struck through in place**, quoting the old text and naming
this decision, per the precedent set by 004 and followed by 007.

**2. `SEARCH` is not a synonym for "fast".** `SEARCH sms_messages USING INDEX
idx_sms_status (status=?)` is the ten-minute plan. Any future plan assertion in this
project reads the **column** an index is keyed on, never the verb in front of it.

**3. This is the first check in the repo about what a query costs, and it is narrow.** It
covers one join. The lesson in `CLAUDE.md` is the general rule; this test is one instance
of it. A session that adds a query to a screen owes the same treatment and should copy the
pattern rather than assume the gate covers it.

**Decided by:** Jordan (via Cowork), 2026-09-04
**Status:** resolved
