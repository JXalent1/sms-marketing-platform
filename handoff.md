# Handoff

_Last updated: 2026-08-18_

## What just happened
Session 1 / module 1 — foundation, pricing and white-label. No features were built.
Five commits on top of `33a7d5d`:

```
c2d67ed  Fix test isolation: per-run scratch DB in conftest, pin pytest in requirements-dev
f0c424f  Add Alembic with an initial migration capturing the current schema
0aa1eef  Compile Tailwind at build time, self-host Inter, move brand color to CSS tokens
7b72ddd  Switch billing to A4A terms: no monthly fee, 10,000 included, $0.015/segment
8e88074  White-label sweep: no carrier name or carrier-denominated money on client surfaces
```

## State of the code
`bash agent/gate.sh` passes. 34 tests green, twice in a row. `alembic upgrade head`
applies to an empty database and `alembic check` finds no drift against the models.

## What the next session needs to know

**Environment.** Work inside `.venv`, and install from both requirements files:
`pip install -r requirements.txt -r requirements-dev.txt`. `requirements.txt` has no
pytest on purpose. `npm install && npm run build:css` before starting the app —
`app/static/app.css` is a gitignored build artifact, and without it every page renders
unstyled.

**Migrations.** From here every schema change is an Alembic revision. `env.py` takes
the URL from `settings.DATABASE_URL`, so `DATABASE_URL=... alembic upgrade head` works
and `alembic.ini` holds no URL. `render_as_batch` is on for SQLite, so column alters
will work on the client's actual database. Module 2 adds the first real migration —
autogenerate it, then read it before applying: it will diff against whatever is in
`Base.metadata`, including anything half-finished.

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
`cost = BILLING_MONTHLY_FEE + billable * BILLING_PRICE_PER_SEGMENT`. Round with
`billing_service.to_money()` and only at display — it is half-up, because Python's
`round()` is not. The billable status set is still `('sent', 'delivered')` and changing
it is an escalation, not an implementation detail.

**White-label.** The gate greps for the carrier's name in `app/templates/` and
`app/routers/`, but the three leaks found this session were all assembled at runtime
and invisible to a grep: a webhook URL built from the provider name, an abort reason
quoting our carrier account balance, and `str(e)` from an SDK exception. When you add
a surface, ask what the string is built from, not just what it contains.

## Open decisions
See "Blocked on" in `status.md`. Nothing blocks module 2. Four out-of-scope issues
found while working are logged under "Found while working" there, each tagged with the
module that owns the file.
