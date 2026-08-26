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
- **Tests:** `python -m pytest tests/ -q` — must exit 0. **184 passing as of session 5d.**
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
