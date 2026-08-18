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
