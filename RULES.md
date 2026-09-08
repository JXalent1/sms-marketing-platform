# Working rules — Auctions4America

## Project rules

- `A4A_BUILD_PLAN.md` is the reasoning; `modules.md` is the plan of record; `status.md`
  is the current state. Keep all three honest.
- The UI design lives in `Auctions4America.pen`. If the build deviates from the design,
  update the design — don't let them drift.
- Every non-obvious decision gets a comment explaining *why*, not what.

---

## Dev mode rules (added by dev-prep)

These rules are active from the start of the build phase onward.

### Cowork's role during dev
- Cowork writes **prompts**, not code. When asked for code directly, redirect: "Let's
  capture that in the session prompt so the coding agent runs it properly."
- Cowork keeps the build plan coherent — module breakdown, session specs, scope
  enforcement, verification, context maintenance.
- Claude Code does the actual implementation.

### Session discipline
- One module = one session = one prompt file (`sessions/session-N.md`).
- Do not pre-generate future session prompts. One at a time, after the previous session
  completes and verifies.
- If a coding session deviates from the spec, update `modules.md` before generating the next prompt.

### Verification is the exit condition
- Every session prompt states acceptance criteria the agent must demonstrate in the
  transcript, wired into a `/goal` stop condition with a turn cap.
- A module is not "done" until acceptance cleared AND a fresh-context review passed.
  Self-declared completion doesn't count.
- When a review or failed check reveals a reusable lesson, encode it in `CLAUDE.md`.

### Session specs vs resolved decisions

Session specs are not edited after the fact — except when a resolved decision in
`decisions/` supersedes something in one. Then the spec is wrong, and leaving it wrong is
worse than editing it. Strike the old text through, state which decision superseded it
and why, and never silently swap. Precedent: 5g criterion 3, superseded by decision 004.

### A spec clause naming a third-party field is unverified until the session checks it

Four consecutive sessions have departed from a clause because it named an API field,
parameter or return shape that does not exist as written — `decisions/007`, `008`, `010`,
and the identifier lifetime in `011`. The common factor is not Stripe or FastAPI. It is
that the name was written from memory and handed over with a spec's authority.

So: **when a spec names something a third party owns — a parameter, a field, a limit, a
guarantee — it is a hypothesis.** Cowork marks it as unverified when writing it. The
session confirms it against the SDK or the reference *before* building on it, and a
mismatch is a finding to record in `status.md`, not a departure to justify afterwards.

State the **property** the clause exists to guarantee; let the session choose and prove the
mechanism. The clause that cost the most so far claimed Stripe's meter identifier deduped
backfills. It deduplicates over a rolling 24 hours, and the tool built on that claim would
have billed a client twice.

### File size
- No source file exceeds 500 lines. Hard rule.

### Scope enforcement
- Session prompts state what's in scope AND what's explicitly out, and limit changes to
  the module's file list.

### Parallel work
- Only run modules in parallel when dependencies are built and file sets are disjoint
  (see `modules.md` → Parallel-safe work). Default sequential.

### Product rules that override convenience
- **White-label:** the SMS carrier's name never appears anywhere a user can see it.
- **No hardcoded commercials:** no monthly fee, 10,000 included segments, $0.015/segment —
  all from `.env`, rendered from one place.
