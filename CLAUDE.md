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

- **Tests:** `python -m pytest tests/ -q` — must exit 0. Baseline is 22 passing.
- **Migrations:** `alembic upgrade head` — must succeed from a clean DB.
- **Run it:** `./run.sh` then `curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/login` → `200`
- **Stylesheet is local:** `curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/static/app.css` → `200`
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
