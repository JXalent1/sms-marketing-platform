# Should `agent/gate.sh` pin the interpreter it tests with?

**Blocks:** 5c Part A (stopping cleanly), and every future session run on this machine
**Why this is not mine to decide:** `agent/gate.sh` and `agent.config.sh` are human-only
— CLAUDE.md's gate section and the PreToolUse hook both say so, and the hook's own
message says to open an escalation here instead.

## Context

`agent/gate.sh:39` runs `python -m pytest` and `:60` runs `alembic upgrade head`, both
unqualified. They therefore test whatever interpreter is first on `PATH`, which on this
machine is `/opt/anaconda3/bin/python`. That environment has `fastapi` and `pytest` but
not `slowapi`, so the gate dies at collection:

```
tests/test_campaign_guardrails.py:34: in <module>
    from app.main import app
app/main.py:9: in <module>
    from slowapi import Limiter, _rate_limit_exceeded_handler
E   ModuleNotFoundError: No module named 'slowapi'
GATE FAIL: test suite is red
```

The suite is not red. With the project venv in front it is green twice in a row:

```
PATH="$PWD/.venv/bin:$PATH" bash agent/gate.sh
144 passed
GATE PASS
```

"Test suite is red" is a true statement about the wrong interpreter, and a convincing
one — it names a real test file and a real import. An agent that believes it will go
looking for a regression that does not exist, or "fix" a passing test.

Two related problems in `.claude/hooks/verify-gate.sh`, found while hitting this:

1. **The loop guard does not fire.** Line 26 parses the hook's stdin JSON with
   `json.loads`, and on this session's input that raised
   `JSONDecodeError: Invalid control character at: line 1 column 567`. The `read` that
   follows then assigns nothing, so `STOP_ACTIVE` is empty rather than `true`, and the
   `MAX_GATE_ATTEMPTS` branch at line 63 — which requires `STOP_ACTIVE == "true"` — can
   never be reached. The gate bounces indefinitely on a failure it cannot clear.
2. **The attempt counter is not scoped to a run.** With the session id unparsed, the
   counter file is a fixed path and survives across sessions: this session was told
   "Attempt 22 of 4".

Neither is in scope for 5c, and `.claude/hooks/` is not in any module's file list.

## Options

1. **Pin the venv in `gate.sh`** — add, after the `cd` at line 18:
   `[[ -x .venv/bin/python ]] && PATH="$PWD/.venv/bin:$PATH"`.
   Cost: the gate now assumes the venv lives at `.venv`, which README,
   `requirements-dev.txt` and `PRODUCTION_CHECKLIST.md` already assume. A machine that
   deliberately runs the gate against a different interpreter loses that ability.
2. **Fail loudly instead of pinning** — have the gate refuse to run when
   `python -c "import slowapi"` fails, with "activate the project venv" rather than a
   pytest traceback. Cost: still needs a human to activate it; every session pays the
   round trip. Keeps the gate honest about testing whatever it was pointed at.
3. **Leave it and document it** — done as an interim measure: `CLAUDE.md`'s "How to
   verify work" now opens with the venv rule, and `agent/accept-5c.sh` puts `.venv/bin`
   in front before calling the gate. Cost: the Stop hook still runs the unqualified gate
   and will keep failing every session on this machine, so no agent can stop cleanly.
   That is what this file exists to work around, and working around it with an
   escalation is not a fix.

## Recommendation

Option 1. The gate exists to answer "is this repo sound?", and it cannot answer that
against an interpreter the repo was never installed into; a test run whose result
depends on the caller's shell is not a gate. It is one line, it matches what every other
document in the repo already assumes, and it removes a failure mode that costs a session
every time it appears. Option 2 is the strictly-more-honest version and I would take it
if this were a shared CI box, but here it buys a warning nobody needs twice.

Worth fixing `verify-gate.sh`'s JSON parse at the same time — until the loop guard works,
any gate failure is an infinite bounce rather than four attempts and an escalation.

---

# Decision — Option 1, pin the venv

**Decided by:** Jordan (via Cowork), 2026-08-20
**Status:** resolved

Pin it. Add after the `cd` at `agent/gate.sh:18`:

```bash
[[ -x .venv/bin/python ]] && PATH="$PWD/.venv/bin:$PATH"
```

The argument that settles it is the one in the file: a gate whose verdict depends on the
caller's shell is not a gate. It answers "is this repo sound?" and it cannot answer that
against an interpreter the repo was never installed into. Everything else in the repo —
README, `requirements-dev.txt`, `PRODUCTION_CHECKLIST.md` — already assumes `.venv`, so
pinning removes an inconsistency rather than adding an assumption.

Option 2 is the more honest design and the right one on a shared CI box. This is not a
shared CI box; it is one machine where conda's `python` shadows the project's, and the
cost of the honest version is a round trip every single session forever. If this project
ever grows real CI, revisit — the pin should be a fallback there, not the rule.

The cost is accepted explicitly: this machine can no longer run the gate against a
different interpreter without editing the script. Nobody wants to.

**Jordan applies this by hand.** `agent/gate.sh` is human-only under CLAUDE.md and the
PreToolUse hook, and that stays true — the fix to a guard is not an excuse to let agents
edit the guard.

## Also approved: fix `.claude/hooks/verify-gate.sh`

Not human-only, so an agent can do it. Both bugs are in scope for the follow-up session:

1. **The JSON parse.** `json.loads` is rejecting a legal control character in the hook's
   stdin. Use `json.loads(s, strict=False)`. Until this works, `STOP_ACTIVE` is never
   `true`, the `MAX_GATE_ATTEMPTS` branch is unreachable, and any gate failure bounces
   forever instead of escalating after four attempts. That is a worse failure than the
   one this decision fixes — an agent that cannot stop burns tokens until someone
   notices.
2. **The counter scope.** With the session id unparsed the counter file is a fixed path
   that survives across runs, which is how a fresh session got told "Attempt 22 of 4".
   Key it on the session id once parsing works, and treat a missing id as a fresh run
   rather than reusing the shared path.

Add a test or a smoke check that feeds the hook a payload containing a control character
and asserts the guard still arms. This bug was invisible precisely because the failure
path is the one nobody exercises.
