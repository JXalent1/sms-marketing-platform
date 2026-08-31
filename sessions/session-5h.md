# Session 5h — Held-back rows, and a guard that rounds itself away

## Objective

Two send-path correctness items from 5e's review. Both are small. Both must land before
the client gets a login.

**Runs in parallel with 5f.** File sets are disjoint — 5f is short links, reports and
history; this is the send path and the message model. Whichever merges second rebases its
Alembic revision.

## Prerequisites

- Read `CLAUDE.md`, `RULES.md`, `status.md`, `modules.md`, and
  `decisions/005-topping-up-a-contact-the-window-held-back.md` in full.
- 5e is merged. 319 tests, gate green, `agent/mutate-5e.py` at 28 mutations.
- Production holds the client's real contacts and message history.
- Server: `ssh -i ~/.ssh/a4a_deploy appuser@67.205.180.62`, sudo limited to
  `systemctl restart a4a-sms`.
- Deploy: `SERVER=appuser@67.205.180.62 SERVICE=a4a-sms ./deployment/deploy.sh`

---

# Part A — agent work

## A1. `held_back` becomes its own message status

Implements decision 005, option 2. Read that file before starting; it carries the
reasoning and four riders, all of which are binding.

- Add `held_back` to `MESSAGE_STATUSES`. **Outside `BILLABLE_STATUSES`** — same shape as
  5d's `not_sent`.
- `campaign_builder.py:205` writes suppression rows as `held_back`, not `skipped`.
  `skipped` goes back to meaning only what `sms_message.py:11` says it means: filtered
  before send, wrong region, permanent.
- A top-up includes contacts whose only row for this campaign is `held_back`, re-runs the
  window **against today's value**, and **flips the existing row** to `pending` rather
  than writing a second one.
- **No backfill.** Existing `skipped` rows keep their meaning and are never
  re-adjudicated — they cannot be classified after the fact. State this in the migration
  comment so the next reader does not helpfully "fix" it.

Why this is urgent rather than tidy: 5e A5 put the hold-back window on the Settings page.
The client can now raise it from a form. Every contact it holds back is currently
unreachable inside that campaign, permanently, and his only remedy is to rebuild the
campaign and lose the first send's numbers. A5 and this defect are safe apart and
hazardous together.

## A2. The capacity guard must not round itself to zero

~~`wholesale_estimate()` (`campaign_service.py:58`) rounds to two decimal places, so a
small send can require `$0.00` and pass the capacity check on an empty account. The guard
that exists to stop a blast dying halfway through can be satisfied by nothing.~~

**Superseded by `decisions/006-which-campaigns-may-release-a-hold.md` → "On A2 — you were
right and my spec was wrong".** The premise above is false at this installation's rate.
At `WHOLESALE_COST_PER_SEGMENT = 0.009` — the value in `.env`, `.env.example` and the code
default, which `.env.production` does not override — a one-segment estimate rounds *up*
to `$0.01` and was already refused on an empty account. The `$0.00` case needs a blended
rate under half a cent.

The defect that is real at 0.009 is smaller and in the same place: rounding *down* loses
up to three quarters of a cent of requirement, so six segments ask for `$0.075` against a
true `$0.081` and a campaign can start on a balance that does not cover it. Struck rather
than rewritten because the ruling records the failure as an instance of a lesson this
project already carries — `CLAUDE.md`, "a rationale and its mechanism have to be checked
against each other" — and a silently corrected spec would hide that.

The three bullets below stand as written and were implemented.

This is escalation item 3 — the pre-flight capacity check — and it is **ruled on: fix
it.** The fix strictly tightens the guard, which is the direction that list protects.

- Compare unrounded `Decimal` values. Round for display only, at the edge, never before a
  comparison.
- `CLAUDE.md` already carries this lesson from module 1 — *"float arithmetic on money
  drifts below half-cent boundaries; use `Decimal` end to end"* — so this is that lesson
  unlearned in a second place. Check whether it is unlearned in a third: audit every
  other site where a money value is rounded before being compared, not just this one.
- Keep the wording the client sees denominated in segments, as 5d established. This
  changes the arithmetic, not the sentence.

---

## Part A acceptance

Demonstrate each in the transcript.

1. `agent/gate.sh` passes all six checks, twice.
2. A campaign built with the window > 0 writes `held_back` rows, not `skipped`. A
   region-filtered contact still writes `skipped`.
3. Reproduce decision 005's scenario end to end: 4-person list, 2 texted yesterday,
   window 3 → send → window lowered to 0 → **top-up reaches exactly those 2**, by
   flipping their existing rows, writing no duplicates.
4. `held_back` rows are excluded from billing. Show a cycle count with them present.
5. Pre-existing `skipped` rows on a campaign built before this change are untouched by a
   top-up.
6. A one-segment send on a zero balance is **refused**. Show it passing before the fix
   and refusing after.
7. The audit from A2 names every remaining site where money is rounded before comparison,
   or states that none remain.
8. Behavioural mutation run per `CLAUDE.md` — mutations inside the current API, not an
   import failure. Follow `agent/mutate-5e.py`; wire in as check 8b. At minimum: revert
   the status split, revert the row-flip to a second-row write, revert the Decimal
   comparison.
9. After deploy: seven screens 200 over HTTPS, no carrier name or raw provider payload in
   any rendered page or API response.

Wire into a `/goal` stop condition with a turn cap, then **one synchronous** fresh-context
review. No background reviewers — see `CLAUDE.md`.

## Constraints

- Do not touch `.env`, `.env.production`, `agent/gate.sh`, `agent.config.sh`.
- **Do not send SMS. Do not modify, delete or re-import contact data.**
- Do not re-adjudicate any existing row — not `skipped`, not blocklist entries.
- No source file over 500 lines. `app/sms/` stays DB-free.
- No hardcoded commercials.

## Explicitly out of scope

- Short links, click stats, reports, history screens — 5f.
- Line-type screening at import.
- The structured transient-code set from decision 004 rider 3.
