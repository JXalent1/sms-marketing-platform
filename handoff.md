# Handoff

_Last updated: 2026-08-19_

## What just happened
Session 1b — corrective work on module 1's review findings. No features, no module 2.
One commit on top of `43f21f4`, closing four defects and correcting one status note.

## State of the code
`bash agent/gate.sh` passes. 46 tests green, twice in a row. `alembic upgrade head`
applies to an empty database, and the test suite now builds its own schema that way, so
a broken migration fails at collection instead of hiding behind `create_all()`.

## What changed in 1b, and why it matters to you

**Our wholesale rate is no longer on the client's screen.**
`PREFLIGHT_COST_PER_SEGMENT` is now `WHOLESALE_COST_PER_SEGMENT` — it is what *we* pay
the carrier, it feeds the pre-flight capacity check and our logs, and it must never
reach a response body or a template. The campaign cost estimate no longer crosses the
API boundary (`_campaign_dict` in `app/routers/campaigns.py`), and the three UI strings
that quoted it now read in segments. The client's rate is `BILLING_PRICE_PER_SEGMENT`
and the only thing allowed to price anything for him is `billing_service`.

When you add a client-facing number, the question is not "is this accurate" but "whose
number is it". The removed field was accurate — at our cost, which read about 40% below
the invoice he actually gets.

**Billing arithmetic is `Decimal` end to end.** `cost_for_segments()` returns a
`Decimal`, not a float, and that return type is load-bearing: at $0.015 every odd
billable count lands exactly on a half-cent, and in float about a quarter of them land
just below it, so half-up rounding went a cent low on 11,782 of the first 50,000. The
model, the rate, the allowance and the billable status set `('sent', 'delivered')` are
unchanged — 1b fixed arithmetic, not terms. Changing any of those four is still an
escalation.

**Alembic owns the schema; nothing else creates a table.**
`Base.metadata.create_all()` is gone from `app/main.py`. Outside production the app runs
`alembic upgrade head` at startup; production never migrates on startup, and
`deployment/deploy.sh` runs it explicitly before the restart (and refuses, with
instructions, if it finds a pre-Alembic database rather than guessing whether to stamp).

This matters directly to module 2. Before 1b, adding `categories` to the models meant
that merely *starting the app* created the table with no version bump, and the migration
you then wrote for it failed — whichever order you happened to work in decided whether
the schema was right. That is no longer possible. `tests/conftest.py` builds the scratch
database with `alembic upgrade head`, and `tests/test_migrations.py` fails if the
migrations drift from the models, so your `categories` migration is a tested artifact
before you write a line of it.

**`tests/test_whitelabel.py` runs the app and scans what comes back.** The gate greps
for the carrier's name; every leak this project has actually had was assembled at
runtime — an f-string, a URL built from `provider.name`, `str(e)` from an SDK exception,
a JS template literal reading an API field — and a grep structurally cannot see any of
them. The test discovers GET routes from the app itself, so a client-facing route you
add in module 2 is scanned the day it lands. If it has a path parameter, add a sample
value to `PATH_VALUES` — the coverage test fails rather than skipping the route, which
is deliberate: passing by omission is the failure mode being designed out.

## What the next session needs to know

**Environment.** Work inside `.venv`, and install from both requirements files:
`pip install -r requirements.txt -r requirements-dev.txt`. `requirements.txt` has no
pytest on purpose. `npm install && npm run build:css` before starting the app —
`app/static/app.css` is a gitignored build artifact, and without it every page renders
unstyled.

**Migrations.** Every schema change is an Alembic revision. `env.py` takes the URL from
`settings.DATABASE_URL`, so `DATABASE_URL=... alembic upgrade head` works and
`alembic.ini` holds no URL. `render_as_batch` is on for SQLite, so column alters will
work on the client's actual database. Autogenerate module 2's migration, then read it
before applying: it diffs against whatever is in `Base.metadata`, including anything
half-finished.

Build Alembic's `Config` without `alembic.ini` when calling it from Python
(`Config()` plus `script_location`). Passing the ini file makes `env.py` call
`fileConfig()`, which disables every existing logger — including the app's.

**Styling.** Colors are CSS custom properties in `app/assets/tailwind.css`, exposed as
Tailwind utilities (`bg-surface`, `text-ink-2`, `border-line`, `bg-brand`,
`text-on-brand`, `s1`–`s4`). Dark is the default; light is `[data-theme="light"]` on
`<html>`. Two consequences worth holding on to:

- A class assembled at runtime gets purged by the compiler. `bg-{{ brand.color }}-600`
  is exactly why the brand color had to move to a variable. Do not build class names
  from template variables or JS string concatenation of partial names.
- The brand hex comes from `.env` via `app/templates/_brand.html`. Never write a hex
  into a template. `--s1`–`--s4` are not brand-configurable — see the note in the
  stylesheet before touching them.

**Screens are only half-converted, and that is intentional.** `base.html`, `login.html`
and `usage.html` are on the dark tokens. `dashboard.html`, `contacts.html`,
`campaigns.html`, `blocklist.html` and `settings.html` still carry the skeleton's
light `bg-white`/`text-gray-*` classes and will look like light cards on a dark page.
They got the mechanical brand-class swap only, so they render and function. Modules 3
and 8 own their redesign.

**Billing.** `billable = max(0, segments - BILLING_SEGMENTS_INCLUDED)`,
`cost = BILLING_MONTHLY_FEE + billable * BILLING_PRICE_PER_SEGMENT`, all in `Decimal`.
Round with `billing_service.to_money()` and only at display — it is half-up, because
Python's `round()` is not, and it only stays correct because what reaches it is exact.

**Login is rate-limited at 10/minute per IP** and the whole suite logs in from one
address inside one window. Test modules take a module-scoped login fixture for that
reason; if you add a module, do the same and assert the login succeeded. A 429 there
does not fail loudly — it just makes every later assertion run against a 401 body.

## Open decisions
See "Blocked on" in `status.md`. Nothing blocks module 2. The out-of-scope issues found
while working are logged under "Found while working" there, each tagged with the module
that owns the file. One of them is a correction: 1b's own spec asked for the
`scripts/balance_alert.py` note to be deleted because the file does not exist. It does
exist and is tracked, and the note was accurate, so it stands — with the verification
recorded next to it.
