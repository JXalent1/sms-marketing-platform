# CLAUDE.md

Context for any Claude session writing code in this project. Read this first, every time.

## What this project is

A text-marketing platform for **Auctions4America**, a Fort Lauderdale auction house
that runs a different-niche auction almost every day. Two jobs: send category-correct
SMS campaigns to their buyer list, and grow that list by finding new bidders.

Built on the `sms-marketing-platform` skeleton (extracted from a prior client build).
The SMS engine is proven — categories, prospecting and the UI are the new work.

## Stack

- Python 3.12, FastAPI, Uvicorn
- SQLAlchemy 2.0 + SQLite (Postgres-ready via `DATABASE_URL`)
- Alembic for migrations
- Jinja2 templates + Tailwind CSS (compiled at build time — see below)
- pytest
- SMS carrier behind a provider abstraction; `console` provider = dry run

## Project layout

```
sms-marketing-platform/
  app/
    core/         config, db, auth, logging, branding — no business logic
    models/       SQLAlchemy tables
    sms/          carrier abstraction, segments, phone rules — NO DB IMPORTS
    services/     business logic; the only layer touching both DB and SMS
    sources/      contact ingestion plugins (CSV, etc.)
    prospects/    prospect discovery + scoring (added during build)
    routers/      HTTP only — validate, delegate, serialize
    templates/    Jinja + Tailwind
    static/       compiled app.css, self-hosted fonts
  alembic/        migrations
  scripts/
  tests/
```

## How to verify work in this project

These are the commands acceptance criteria reference. Run them and show the output.

- **Run everything inside the project venv.** `agent/gate.sh` and the commands below
  call bare `python` and `alembic`, so they test whatever is first on `PATH`. If that
  is a system or conda python, the gate dies at collection with
  `ModuleNotFoundError: slowapi` and reports **"test suite is red"** — a true statement
  about the wrong interpreter, and a convincing false alarm. Either activate `.venv`
  or run `PATH="$PWD/.venv/bin:$PATH" bash agent/gate.sh`.
- **Tests:** `python -m pytest tests/ -q` — must exit 0. **259 passing as of session 5g.**
  A lower count means you are on a stale branch, not that tests vanished.
- **Migrations:** `alembic upgrade head` — must succeed from a clean DB.
- **Run it:** `./run.sh` then `curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/login` → `200`
- **Stylesheet is local:** run `npm run build:css` first — `app/static/app.css` is a
  gitignored build artifact, so it 404s in a fresh clone or worktree until you build it.
  Then `curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/static/app.css` → `200`.
  A 404 here is a missing build step, not a regression.
- **No runtime CDN:** `grep -rn "cdn.tailwindcss.com\|fonts.googleapis.com" app/templates/` returns nothing
- **White-label check:** `grep -rni "telnyx" app/templates/ | grep -v "^app/templates/.*#"` returns nothing

There is no linter configured. If you add one, update this section.

## The two rules that shape this codebase

### 1. Layering

`routers → services → models`. `app/sms/` may be called from services but **must not
import from `app.models` or `app.services`**. If you need a DB write inside a carrier
module, the logic is in the wrong layer. This boundary is why the SMS engine was
reusable across clients — do not breach it.

`app/sources/` and `app/prospects/` produce records; they never write to the DB
directly. The base class handles normalization, dedup and persistence.

### 2. White-label

This is a **client-facing product under the Auctions4America brand**. The SMS carrier
is our implementation detail. The carrier's name must never appear in a template, a
user-visible string, an error message, or an export. `scrub_provider_text()` already
strips carrier branding from error text — extend that discipline everywhere. The client
sees *his* segments, *his* number, *his* cost.

## Commercials (config-driven, never hardcoded)

- **No monthly fee.**
- **10,000 segments included free per month.**
- **$0.015 per segment** beyond that.

All three live in `.env` and render from one source. Never put a price in a template.

## Conventions

### File size
No source file exceeds **500 lines**. Approaching it means splitting along a natural
boundary. This keeps future sessions token-efficient.

### Naming
`snake_case` for Python, `kebab-case` for filenames and CSS classes, `PascalCase` for
SQLAlchemy models.

### Testing
Every module ships with tests. The suite must stay green — a module that leaves a red
test is not done.

### Comments
The prior codebase's best trait was comments explaining *why* a non-obvious decision was
made (the billing cutover, the region filter, the segment-count algorithm). Keep that
habit. Explain reasoning, not mechanics.

### Migrations
Schema changes go through Alembic. Never rely on `Base.metadata.create_all()` for a
change to a live table.

## Things that will bite you (learned from the prior client's production system)

- **Never let a domain concept live as an overloaded string.** The prior build stuffed
  three meanings into one `auction_date` column and every consumer re-parsed it. Categories
  get real tables.
- **Close Playwright contexts in a `finally` block.** The prior server leaked one browser
  driver process per daily scrape — 17 orphans, 1.6 GB RSS on a 3.9 GB box.
- **The pre-flight balance check is the most valuable feature in the codebase.** It turns
  "we lost 4,623 messages mid-blast" into "the campaign refused to start." Don't weaken it.
- **`count_sms_segments()` is correct and hard-won.** It matches the carrier's own `parts`
  value. Do not "simplify" it.
- **Emoji flips encoding to UCS-2**, cutting 160 chars/segment to 70 and roughly tripling
  cost. The UI must warn loudly.
- **Most scraped business numbers are landlines.** Texts to them fail and cost money.
  Filter on line type before sending, always.
- **White-label leaks are usually assembled at runtime, not written as literals.** Every
  leak found so far was an f-string, a URL built from the provider name, a `str(e)` from
  an SDK exception, or a JS template literal reading an API field. `agent/gate.sh` greps
  for literals and structurally cannot see any of them. When you add a client-facing
  surface, ask what the string is *built from*, not just what it contains — and add a case
  to `tests/test_whitelabel.py`, which runs the code rather than reading it.
- **A degraded fallback must not look like a chosen one.** `get_provider()` falling back
  to console when a carrier credential is wrong is correct — a dashboard must not die
  over a credential. What broke the launch was that the product then *described* the
  result as a deliberate dry run: same pill, same badge, same wording, with the real
  cause in an ERROR line in a journal the service account could not read. A live box sat
  unable to send while every screen said it was fine. Any fallback you add gets three
  states, not two, and the failed one says so on screen. `send_mode()` in
  `app/sms/factory.py` is the pattern: one function owns the client-safe wording, every
  surface renders it verbatim, and the exception stays in the log.
- **A pinned SDK is a runtime contract, not a version number.** `requirements.txt` said
  `telnyx==2.1.2` while the provider was written against the 4.x client class. Nothing
  failed at import, at boot or in the suite — the app served every page. If a module
  drives a third-party API, assert the shape it drives in a test
  (`tests/test_provider_status.py`), so a bad pin fails at `pip install` rather than on
  the morning of a sale.
- **`WHOLESALE_COST_PER_SEGMENT` is our cost, not the client's price.** It must never
  reach a response body, a template, or a log the client can see. The client's rate is
  `BILLING_PRICE_PER_SEGMENT`. Showing him the wholesale number discloses our margin and
  under-states his bill by roughly 40%.
- **Float arithmetic on money drifts below half-cent boundaries.** `n * 0.015` for odd `n`
  lands on `x.xx5` and about a quarter of the time floats just under, so half-up rounding
  goes one cent low. Do currency arithmetic in `Decimal` from end to end, not just at the
  rounding step.
- **A safeguard that interrogates the fallback gets the fallback's answer.** The pre-flight
  capacity check asked `provider.get_balance()` — and on a degraded box that provider is
  the console stub, which returns `999_999.0` under a comment saying it "never trips the
  pre-flight check". The most valuable guard in the codebase passed cleanly on the one
  state it most needed to stop. When a check reads something through an abstraction that
  has a fallback, ask what the *fallback* answers, and check the fallback's own health
  before you trust anything it says. `send_path_assessment()` runs before
  `capacity_assessment()` in `campaign_service.py` for exactly this reason.
- **A guard with one call site is a guard on one path.** `should_auto_block()` had the
  right fragments and the right comment since the skeleton, wired into the *submission*
  failure path only. Most dead numbers are accepted at submission and fail later by
  delivery webhook, which never called it — so 2,526 landlines stayed on the list and were
  paid for on every campaign, under a guard that existed to prevent precisely that. When
  you add a rule, enumerate every path its condition can arrive on, not just the one in
  front of you.
- **The table can be right while the headline lies.** `/blocklist` rendered a correct
  "Delivery failure" badge on every row and a single red **2,626** labelled "Blocked" above
  them, on a screen titled "Opt-outs". The client reads the number, not the rows. A summary
  stat is a separate claim from the data under it and needs its own definition — and if
  another screen already defines that word (`dashboard_service.py` filters opt-outs on
  `reason == "stop_keyword"`), reuse it rather than inventing a second one.
- **Count in the database, not in the page.** `/api/blocklist` caps `numbers` at 5,000
  rows. A headline tallied in JS from that list would under-report the day it overflows,
  and under-report it as *fewer opt-outs* — the direction nobody sanity-checks.
- **`/health` is the only alert channel left when the carrier is down**, so it reports send
  status as well as liveness. It stays **HTTP 200 while degraded**: `deployment/deploy.sh`
  rolls a release back on a non-200 there, so a 503 for a bad credential would revert every
  deploy to a degraded box, including the one that fixes it. Monitor the `sending_ok`
  field. Never route a "cannot send SMS" alert over SMS.
- **`scrub_provider_text()` has no word boundaries, and putting them back is a leak.**
  It was `\b(?:telnyx|twilio|…)\b` and the trailing `\b` fails whenever an SDK glues the
  name to a word — `TelnyxError`, `telnyx_api`, `TwilioRestException` all went through
  untouched. It only started to matter when a delivery-webhook auto-block began writing
  carrier free text into `blocked_numbers.notes` at 2,673 rows a campaign. When you probe
  a scrubber, probe the *glued* spelling: `"Telnyx error"` (with a space) is caught by
  both versions and proves nothing.
- **Widening a guard's call sites widens its false positives too.** `should_auto_block()`
  was written for the submission path, where a carrier rejects outright. Pointed at the
  delivery-webhook path it sees the entire transient-failure vocabulary, and its fragments
  are unanchored substrings: `"unreachable"` is Twilio's wording for a switched-off
  handset, and `"21610"` matches a Brevard County phone number quoted in an error string.
  Before reusing a matcher on a new path, ask what *else* arrives on that path.
- **A fragment that means more than one thing does not belong in a list that triggers
  an irreversible action.** `"unreachable"` meant a dead line, a switched-off handset
  and an upstream outage — two of the three transient, one not even about the
  recipient. Session 5g added a transient-marker guard to separate them and it could
  not: Twilio 30003's own `ErrorMessage` is `Unreachable destination handset`, with no
  marker in it, so the guard only ever caught the wordings a carrier happened to
  decorate with an adjective. `decisions/004` removed the fragment rather than adding
  a second tier of logic. Two things generalize. First, when a signal is ambiguous and
  the errors are asymmetric — half a cent against a bidder deleted invisibly — make
  the cheap error. Second, **do not build a better heuristic on a lossy proxy when the
  authoritative check is cheap**: permanence is a property of the line, and line-type
  lookup answers it for about $0.004.
- **A partial failure must not report success.** When the degraded backstop marks rows
  `not_sent`, the campaign is `aborted` with a reason, not `completed`. The campaign rail
  is the entire UI — there is no detail screen — so it renders a status badge and shows a
  reason only when `abort_reason` is set. A blast that reached nobody reporting "completed"
  is the same defect as a failed carrier reporting "Dry run", one level down.
- **Refuse before you queue, not inside the background task.** `POST /{id}/send` used to
  answer "Campaign sending started" and let the refusal surface seconds later on a poll.
  Refusing synchronously also leaves the campaign a **draft**: nothing in this codebase
  moves a campaign back from `aborted`, and decision 002's own justification is that a
  campaign which never ran can simply be re-run.
- **Tense is not decoration on a refusal.** One string served both the composer checklist
  and the stored `abort_reason`, so a draft he had not sent was labelled "Nothing was
  sent." — which reads as a past campaign having silently failed, on the screen whose only
  job is to stop him beforehand. Pre-send and post-hoc wordings live together in
  `send_path_assessment()` so a new surface cannot invent a third.
- **Which way a matcher should fail depends on what it triggers, and the two lists in one
  function can differ.** `app/sms/compliance.py` holds both: `AUTO_BLOCK_ERROR_FRAGMENTS`
  causes an *action* — permanently deleting a buyer — so it is word-anchored and narrow,
  and an over-match costs a real person. `TRANSIENT_FAILURE_MARKERS` causes a *refusal to
  act*, so it is deliberately unanchored and wider than it needs to be (`temporar`, not
  `temporary`), and an over-match costs half a cent for one more send attempt. Anchoring
  is not a style question. Before you widen or narrow a matcher, ask what happens when it
  is wrong in each direction, and write the answer next to the list.
- **A numeric code matched against prose matches phone numbers.** `"21610"` was a plain
  `in` test against carrier free text, and +1 321-610-xxxx is an assignable Brevard County
  number in this client's own market — so an error string that merely *quoted* a
  destination blocked a different, innocent one. Bare codes also live inside UUIDs, byte
  counts and durations. `\b`-anchoring does not fix this; a bare code in prose still
  matches. Match codes against a column the carrier populated
  (`sms_messages.error_code`). The general form: when a rule has to hunt for a field
  inside a string, look upstream — `telnyx.py:58` was building `f"{title}: {detail}"` and
  dropping `code` on the floor, and that discard was the actual bug.
- **Pick an alert channel for the shape of the path it fires on, not only for its
  independence.** `agent/notify.sh` is the right channel for a nightly digest and the
  wrong one for a delivery webhook: it shells out with a 20-second timeout, and these
  arrive thousands at a time inside a request that must answer promptly, so a carrier's
  retries would become a fork bomb. A5's operator signal is a row read back by `/health`
  instead. And every field `/health` gains is a new way to fail the endpoint
  `deployment/deploy.sh` rolls back on — which is why it reads that row *without*
  `Depends(get_db)`: a dependency that raises means the handler never runs, and there is
  nothing left to catch it in.
- **A test that pins a literal fails on the intended change and passes on the dangerous
  one.** `test_the_opt_out_definition_matches_the_dashboard_tile` asserted
  `OPT_OUT_REASONS == ("stop_keyword",)`. It went red the moment decision 003 added a
  member — and it had been green all along over the thing it existed to prevent, a second
  literal `reason == "stop_keyword"` filter sitting in `dashboard_service`. Assert the
  property the list exists for (the two screens agree), not the list.
- **A guard on the wrong set is not a guard.** 5e's top-up had a correct, tested rule —
  never re-send to a number this campaign already has a message row for — sitting on top
  of a candidate set built by re-resolving the campaign's audience selector and
  subtracting those rows. Every test asked "does the guard stop a re-send?" and the
  answer was yes. Nobody asked what was in the set before the guard ran, and the answer
  was: everyone a `batch_size` cap had deliberately withheld, and on an `all` audience,
  every contact imported afterwards for a different auction. One click, last month's
  message, an audience nobody chose. When a rule filters a set, test the set as well as
  the filter.
- **A column with a server default has a second writer, and it may keep a different
  clock.** `contact_list_members.added_at` defaults to SQLite's `CURRENT_TIMESTAMP`,
  which is **UTC**; everything else in this application writes `datetime.now()`, which is
  local. Rows from the two writers are not comparable, and the error is silently
  one-directional — every defaulted row reads up to five hours newer than it is. The same
  column also carried two ISO spellings (`T` versus a space), which breaks a
  lexicographic comparison the same way. If you are going to compare a timestamp column,
  make one writer own it and parse rather than compare strings.
- **Run each acceptance criterion's tests in isolation, because that is how they will
  fail.** `accept-5e.sh` invokes pytest once per criterion, which is what caught two
  tests that only passed inside a full run: one leaned on contacts another module had
  seeded, and one read a list an earlier test in its own file had filled — the latter
  *passing* in isolation with two assertions comparing `0 == 0`, which is worse than
  failing. A test that needs its neighbours is a test that proves nothing about the
  criterion it is named for.
- **Write mutations you can reach.** `agent/mutate-5e.py`'s first run reported CAUGHT for
  two mutations that changed nothing: one flipped a default argument every caller passes
  explicitly, the other passed a raw phone number to a function that normalises
  internally. A third was "caught" only because an unrelated test was leaking a stored
  setting between modules, which inflated every verdict it appeared in. Before trusting a
  green mutation run, check that each mutation is on a path something executes and that
  the tests failing for it are the tests that name it.
- **`skipped` means two things on `sms_messages`, and something will eventually have to
  tell them apart.** The suppression window writes it at draft time for a contact who is
  merely *deferred*; the send loop writes it for a destination region that is permanently
  not enabled. `decisions/005` is the first consumer that needs the distinction. This is
  the overloaded-column mistake this file opens with, caught on the way in rather than
  after five consumers re-derived it. **Resolved in 5h:** `held_back` is its own status,
  outside `BILLABLE_STATUSES`, and rows written `skipped` before that change are never
  re-adjudicated — they cannot be classified after the fact, and the migration that would
  have backfilled them says so instead.
- **A guard that refuses without explaining reads as a broken tool.** `decisions/006`
  upheld two refusals and rejected both sentences that carried them. "This campaign was
  stopped before it sent" is true and useless: it names no cause, offers no time, and the
  client concludes the product is broken — which is what happened here for two
  consecutive campaigns before anyone ran SQL. A refusal owes three things: what stopped
  it, in his units; when it lifts, if that is knowable; and the remedy that actually
  works on *this* object. `zero_send_reason()`'s suppressed branch and
  `capped_campaign_hold()` are the pattern. This is the same defect as a failed carrier
  reporting "Dry run", moved from the badge into the sentence underneath it.
- **One rule, two moments, one sentence-maker.** The suppression window is described
  before a send (the composer's checklist row) and after it (the stored `abort_reason`).
  Those are two surfaces on one rule, and a second copy of the arithmetic or the
  formatting is how they come to disagree about the same instant by an hour.
  `suppression_clears_at()` computes the moment and `clears_at_clock()` renders it, once;
  `preflight_service._clock()` delegates rather than implements. Tense still differs by
  surface — a pre-send refusal must not be worded as a post-hoc one — but the *facts*
  come from one place.

## Where things live

- `A4A_BUILD_PLAN.md` — the full project plan and reasoning
- `Auctions4America.pen` — the UI design (Pencil); `pen-exports/*.png` are the renders
- `modules.md` — module breakdown and build order
- `sessions/session-N.md` — the deep spec for each build session
- `status.md` — current task state; update when a task completes
- `handoff.md` — session handoff snapshot; rewrite at end of each session

## What NOT to do

- Do not work outside the current session's module. Stay in the file list from `modules.md`.
- Do not create files over 500 lines.
- Do not declare a session complete without running the checks in "How to verify work"
  and showing the output.
- Do not name the SMS carrier anywhere a user could see it.
- Do not hardcode a price, an allowance, or a brand string.
- Do not import `app.models` or `app.services` from `app/sms/`.
- Do not invent requirements. If something's ambiguous, stop and ask.

## Current session

Before starting, read `sessions/session-N.md` where N is the session named in the
kick-start prompt.

---

## Autonomy and escalation

You are running unattended. No one will approve your tool calls. Behave accordingly.

### Source of truth

The repository is the state, not any chat history. Before starting, read `modules.md`,
`status.md`, the relevant `sessions/*.md`, and every file in `decisions/`. Answered
decisions are binding — do not relitigate them.

### What you decide alone

Implementation. File layout, naming, control flow, local refactors, test structure,
error handling, template markup, dependency upgrades within a major version. Make the
call and keep moving. Do not ask permission for these.

### What you never decide alone

Stop and escalate on any of the following.

1. **Billing math.** The commercial terms are: no monthly fee, 10,000 segments included
   per month, $0.015 per segment after. What counts as a billable segment — currently
   only `sent` and `delivered` — is a commercial decision, not an implementation detail.
   Do not change the model, the rounding, or the billable-status set.

2. **`count_sms_segments()` in `app/sms/segments.py`.** It matches the carrier's own
   `parts` value, it is correct, and it was expensive to get right. Do not "simplify" or
   "fix" it. If a test disagrees with it, the test is probably wrong — escalate.

3. **The pre-flight capacity check in `app/services/campaign_service.py`.** It is the
   single most valuable safeguard in the codebase: it converts "we lost 4,623 messages
   mid-blast" into "the campaign refused to start." Never weaken, bypass, or make it
   advisory.

4. **The unique index on `contacts.phone`.** That constraint *is* the dedup guarantee —
   it's what stops the same person entering the list five times from five imports. Any
   migration touching it, or any code path that could insert a duplicate, is an escalation.

5. **Opt-out and suppression behaviour.** The blocklist, the fuzzy opt-out matcher, the
   recent-contact suppression window, quiet hours, and sender-pool assignment. These
   decide whether a real person gets a text they didn't want. Implement what the spec
   says; do not tune the rules yourself.

6. **Anything that could send a real message.** `SMS_PROVIDER` stays `console` (dry run)
   unless a human changes it. Never set a live carrier credential, never switch the
   provider, never call a real send endpoint in a test.

7. **Paid third-party APIs and new dependencies.** Google Places, any data vendor, any
   package not already in `requirements.txt` or the lockfile. Several of these cost money
   per call — adding one is a budget decision.

8. **Migrations that are not trivially reversible.** Dropped or renamed columns, index
   changes on `contacts` or `sms_messages`, anything destructive.

9. **The category palette.** `--s1` through `--s4` in the stylesheet were selected by
   running candidate colors through a colorblind-separation and contrast validator; they
   pass all-pairs in both light and dark. Four hues is the proven ceiling. Do not add a
   fifth, and do not adjust them by eye.

10. **Anything the spec does not cover and you are about to guess at.**

### The white-label rule (not negotiable, not an escalation — just never do it)

This is a client-facing product under the Auctions4America brand. The SMS carrier is our
implementation detail. Its name must never appear in a template, a user-visible string,
an error message, or an export. Internal module names and `agent/notify.sh` are exempt —
those are ours, not the client's. The gate enforces this on `app/templates/` and
`app/routers/`.

### How to escalate

Write `decisions/NNN-short-slug.open.md`:

```markdown
# <one-line question>

**Blocks:** <module slug>
**Why this is not mine to decide:** <which category above>

## Context
<what you found, in 3-6 lines. Cite files and line numbers.>

## Options
1. **<name>** — <what it does> / cost: <what it forfeits>
2. **<name>** — <what it does> / cost: <what it forfeits>

## Recommendation
<your pick and the single reason it wins>
```

Then stop. Do not implement a placeholder, do not pick the option you like and note it
for later, do not work around the blocker in an adjacent file. A wrong guess costs more
to unwind than the wait costs.

### The gate

`GATE_CMD` in `agent.config.sh` decides whether you are done — it runs `agent/gate.sh`.
You are not the judge of your own work. Run it yourself before you stop:
`bash agent/gate.sh`.

It checks: the test suite, that migrations apply to a clean database, that no carrier
name reached a client-facing surface, that no template fetches CSS or fonts at runtime,
the 500-line rule, and that `app/sms/` still imports nothing from the DB layer.

If the gate fails you will be handed the output and expected to fix the root cause. Never
make the gate pass by deleting an assertion, marking a test skipped or xfail, widening a
type, adding a blanket try/except, narrowing a test's input until it agrees with the
code, or loosening a check in `gate.sh` itself. If the correct fix requires something in
the escalation list, escalate instead.

### Scope

Stay inside the module named in your session prompt, and inside that module's file list
in `modules.md`. If you find a real bug elsewhere, write it to `status.md` under "Found
while working" and leave it alone. Drive-by fixes across module boundaries make review
impossible, and review is the only thing standing between this loop and a repo of
confident, plausible, wrong code.

### "The new tests fail against the pre-fix tree" is a weak proof

Session 5g found this by testing for it. `accept-5g.sh` check 8 confirmed the new test
modules fail against the pre-fix tree — true, and nearly meaningless: they failed at
*import*, because the pre-fix tree lacks the symbols they reference. That says nothing
about whether they would catch the bug coming back.

The proof with teeth is behavioural mutation: revert each fix one at a time, inside the
current API, in a scratch copy, and confirm the suite goes red for that specific reason.
Ten mutations, ten catches, and it surfaced something no amount of reading the tests
would have shown — two of five parametrized transient wordings contain no block fragment
at all, so they passed whether or not the guard existed. Five green ticks were standing
in for three proofs.

`agent/mutate-5g.py` is the harness, wired in as acceptance check 8b. Follow that
pattern: when a session's value is "this class of bug cannot come back", the acceptance
criterion is a mutation run, not an import failure. It costs about fifteen seconds.

### A rationale and its mechanism have to be checked against each other

5g A1 justified the transient guard with Twilio 30003, then specified four adjective
markers as the mechanism. 30003's own `ErrorMessage` — `Unreachable destination handset`
— carries none of them. The rule could not catch the case that motivated it, and the
tests looked convincing because their sample data was decorated with adjectives the live
carrier does not always use.

When a spec says "do X because Y", run Y through X before shipping the spec. See
`decisions/004`.

### Decide classifier rules against the account's own traffic, not carrier docs

A4A's entire failure corpus is four distinct strings across 3,037 failures. Decisions 003
and 004 were both settled by dumping the real strings and running the classifier over
them, which reordered the work and killed one proposed rule outright. Carrier
documentation describes what a carrier *can* emit; the traffic says what it *does*.

### One synchronous reviewer, not a fan-out

Session 5g ran four fresh-context reviewers. Three produced nothing across roughly a
dozen idle cycles and five direct requests, including one that said "reply in your NEXT
message". The one synchronous reviewer found four real defects. The remaining coverage
came from working the uncovered lens directly — which is also what produced the mutation
harness.

Default to one synchronous fresh-context review plus `agent/mutate-*.py`. Fan-out costs
tokens and interruptions and, on this project's evidence, buys nothing.
