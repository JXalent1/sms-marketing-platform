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
