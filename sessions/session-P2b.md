# Session P2b — close D2, and make the harness reproducible

**Module:** P2b · **Depends on:** P2 (committed `8a14af0`, deployed, acceptance FAILING)

Small and focused. One survived mutation, and one question about whether any of that
harness's verdicts mean anything.

## Prerequisites

- Read `CLAUDE.md`, `RULES.md`, `modules.md`, `status.md`, and every file in `decisions/`.
- Run inside the project venv, or `PATH="$PWD/.venv/bin:$PATH" bash agent/gate.sh`.
- 696 tests, gate green twice as of `8a14af0`.
- `sessions/session-P2.md` is the parent spec. This session does not re-open it.

## The failure

`bash agent/accept-P2.sh --with-remote` fails criterion 9:

    D2 the search ledger is not consulted, so every nightly re-run pays $0.035
       a query to be told the same sixty businesses
       *** NOT CAUGHT ***  (0 failed, 0 errors)
    36 mutations, 1 survived, 0 failed to apply
    ACCEPT FAIL: a guard was reverted and no test noticed

The ledger is `scrape_jobs.search_term`, built by `searched_recently()`
(`app/services/scrape_runner.py:200`) and applied in `run_plan()` in the same file. It is
what stops a nightly re-run re-paying $0.035 for every search.

## A1 — explain the discrepancy before you fix anything

The P2 session's own run of this harness reported **36 caught, 0 survived**, on a tree it
printed `SCRATCH VERIFIED PRISTINE`. The verification run reported **1 survived**. Same
harness, same mutation set, opposite verdict.

One of those runs was wrong. Say which and why, in `status.md`, before touching any code.

`CLAUDE.md` records that this suite has no isolation and is green exactly once, and that a
mutation "caught" by a test which does not name it was never caught at all. If D2's earlier
catch depended on state another test leaked, that possibility is not confined to D2 — so
report what you checked across the other 35, not only this one.

**A harness whose verdict changes between runs is worth less than no harness**, because its
green gets quoted as evidence. This is the third time this project has met that shape.

## A2 — close D2

### Where the gap actually is

The ledger's **construction** is well covered and every one of those mutations is caught:
D3 the NULL branch, D4 a failed search, D5 a zero repeat window, D6 the interrupted clause,
C8 the skipped count. What no test covers is the ledger's **consumption** — that `run_plan()`
skips a search whose `ledger_key` is in the set `searched_recently()` returned.

Proven what goes in. Never proven that anything acts on what comes out.

### Why criterion 2 does not already cover it

**Two meters, two bills.** Criterion 2 asserts a second run produces 0 new prospects and 0
paid calls, and it passes — satisfied by the prospect-level dedup that saves the
**$0.0025** lookups. The ledger saves the **$0.035** requests, and that is a different
entry point.

Your own P1b lesson: test the entry point the mutation is on, not the one that is
convenient. A test through the wrapper does not prove a guard one layer down.

### What the test has to assert

The **request count**, not the prospect count. A second `run_plan()` over a plan whose
searches are already in the ledger must make **zero** Google requests and open **zero**
jobs for those searches. Assert on the call counter and on `scrape_jobs`, not on how many
prospects came back — an assertion on prospects is satisfied by the dedup one layer up,
which is exactly how this got through.

### A lead I already checked, so you do not have to

`google_places.py:378` passes `search_term=search.term` and `scrape_runner.py:309` passes
`search_term=search.ledger_key`, which looks like two writers disagreeing about the ledger
column. **It is not.** The first is on the `ProspectRecord` — the human-facing term the
review queue renders — and the second is on the `ScrapeJob`. Different objects. Only
`run_job()` writes the ledger. Recorded here so nobody spends a turn on it.

**Do not weaken D2, retire it, reclassify it as a finding, or narrow its patch.** It is a
money guard on the only source in this codebase that spends per request.

## A3 — criterion 10

Not yet run: the earlier invocation was handed a placeholder password and got a 401. Run it
against the deployed box with the real one.

## Out of scope

- The P2 taxonomy, the exclusion list, `decisions/009`. All verified and passing.
- `agent/mutate-5e.py` and `agent/mutate-5h.py` bit-rot — residual 8 in `modules.md`, a
  decision rather than a task.
- `_screen()`'s stale comment — residual 9, P1's scope.
- Any new feature. This session makes an existing guard provable.

## File list

    app/services/scrape_runner.py
    app/sources/google_places.py
    tests/
    agent/mutate-P2.py
    agent/accept-P2.sh

Widen it if a requirement here forces it, and record each edit in `status.md` with the
requirement that forced it — the precedent 5d, P1, 5j and P2 set.

## Acceptance

`agent/accept-P2.sh` remains the stop condition, unchanged except where A1 or A2 requires
it. All criteria 1-10 plus 6b.

1. **The mutation run gives the same verdict on two consecutive invocations.** Run it,
   run it again, print both. After today a single green run is not evidence.
2. **D2 is caught**, by a test that names the ledger and fails for that reason.
3. **A test proves the ledger is consulted before a request is made** — asserting on the
   request count and on jobs opened, not on prospects produced.
4. **Criterion 10 passes** against the deployed box.
5. `bash agent/gate.sh` green, twice.

## `/goal`

> Session P2b is complete when `bash agent/accept-P2.sh --with-remote` exits 0 with every
> criterion printed, the mutation run reports the same result on two consecutive
> invocations with both shown, and `agent/gate.sh` is green twice. Turn cap 30. Show the
> output; do not declare completion from a summary.

## Review

One synchronous fresh-context review. No spawned reviewers. Two lenses:

1. **Which entry point is each mutation on?** For the 36, name the test that would fail and
   check it exercises that path rather than a wrapper above it. D2 is the one that got
   through; the question is how many others are in the same position and were caught by
   luck.
2. **What state does a test need that it does not create?** A1's discrepancy is most likely
   leaked state. A test that passes only with its neighbours proves nothing about the
   criterion it is named for.
