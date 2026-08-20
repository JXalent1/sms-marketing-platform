# Handoff

_Last updated: 2026-08-19_

## What just happened

Session 3a — the UI shell. One file of substance (`app/templates/base.html`) plus its
route wiring (`app/routers/pages.py`), and a one-line edit to each of the six child
templates that a block change forced. Nothing else was touched.

## State of the code

`bash agent/gate.sh` passes, at the start of the session and at the end. 76 tests, green
twice in a row. All six pages return 200 and every `href` the shell emits resolves.

## The block contract — read this before writing a template

3b and 4 are written against these names in parallel. Renaming one breaks the other
session's work, so they are fixed now:

    {% block title %}          the top-bar heading. A bare page name — "Contacts", not
                               "Contacts — Auctions4America". base.html builds the
                               document <title> from it and appends the brand itself.
    {% block page_actions %}   the top bar's right-hand slot, for this page's primary
                               buttons. Right-aligned, next to the theme toggle.
    {% block content %}        the page body. It is already inside the padded content
                               column — do not add another page frame around it.
    {% block head %}           extra <head> content. Unchanged.
    {% block scripts %}        page scripts, after the shared helpers. Unchanged.

`title` is the one that changed meaning. It used to be the document title, so every
child carried a " — {{ brand.app_name }}" suffix that would now render inside the `<h1>`.
All six were updated to a bare name; nothing else in them was edited.

`showToast()`, `esc()`, `fmtDate()` and `api()` are all still defined in `base.html` with
the same signatures. Child templates that call them keep working.

## What the shell gives you

- **216px sidebar**, `bg-surface`, hairline right border. Brand block reads `brand.*`
  only. Grouped nav with uppercase section captions. Footer pinned by a `flex-1` spacer
  on the `<nav>`.
- **Active state** is `aria-current="page"` plus `bg-brand-soft`, driven by the existing
  `active_page` context variable. The keys are unchanged: `dashboard`, `campaigns`,
  `contacts`, `blocklist`, `usage`, `settings`.
- **`shell_context(db)`** in `pages.py` supplies `segments_this_month`, `sender_number`,
  `send_mode` and `send_mode_live`. Any new page route must spread it into the template
  context or the footer renders its empty state. Every shell value goes through a Jinja
  `default(...)`, so a handler that forgets renders a blank footer rather than a 500.
- **Below 768px** the sidebar leaves the flow entirely and becomes a drawer over a
  backdrop, opened by a labelled toggle in a mobile header. Verified at 375px: no
  horizontal scroll with the drawer open or closed.

## Three things not to undo

**The nav ships six items, not the design's eight.** History and Categories are module 8;
Prospects is the deferred engine, so there is no badge and no placeholder route. A nav
item that 404s is worse than an absent one. The template comment says what restores each
— add its tuple to `nav_groups` and an arm to `nav_icon()`, nothing else.

**The status pill says send mode, not uptime.** With `SMS_PROVIDER=console` it reads
"Dry run" against a `warn` dot. A pill that said "Live" while the app was logging
messages instead of sending them is exactly the lie you do not want on the morning of a
sale. It reads `get_provider().name`, not the setting, because the factory falls back to
console when a carrier fails to initialise — and the name itself never reaches the
response.

**The sender number is masked, and nothing near it names the carrier.** `+1 954 ••• 4120`
— area code so he recognises it, last four so he can read it out. `mask_sender_number()`
returns `None` for anything that is not a phone number (including console's "(dry run)"),
and the footer shows "Not assigned" rather than a mangled string.

## What is still ugly, and whose it is

The six child templates are on the skeleton's light `bg-white`/`text-gray-*` palette and
render as white cards on the dark page. This is expected — 3a was scoped to the shell and
explicitly told not to restyle them. `dashboard.html` is the worst: its own page heading
is `text-gray-900` on `bg-page` and is nearly invisible. 3b replaces it.

Two nav labels disagree with their page headings: nav says "Compose" and "Opt-outs", the
pages still head themselves "Campaigns" and "Blocklist". One word each in
`{% block title %}`, in the sessions that own those files.

## Two things worth knowing before you screenshot anything

- `app/static/app.css` is a gitignored build artifact. Run `npm run build:css` or every
  page serves unstyled and `/static/app.css` 404s. That is a missing build step, not a
  regression.
- `run.sh` builds a second venv at `venv/` while the project uses `.venv/`. Still noted
  in `status.md`, still module 8's. Running `python -m uvicorn app.main:app` out of
  `.venv` is the shortcut, and it does not need a `.env` if you pass `SECRET_KEY`,
  `ADMIN_PASSWORD` and `COOKIE_SECURE=false` on the command line.

## Still open

- His real CSVs, one per category — needed for the launch import in module 8.
- Sender number strategy, before the first live send. The shell renders whatever
  `active_sender_number()` returns; today that is nothing.
- `SMS_PROVIDER` is still `console`. Flipping it is a human step and stays one.

---

## Session 3b handoff — Today + Contacts (2026-08-19)

Appended, not rewritten: session 4 is editing this file in parallel.

**State.** Module 3b complete. `bash agent/gate.sh` green (96 tests), suite
green twice in a row. Branch `module-3b`. No migration was added and `alembic/`
was not touched — session 4 owns it this wave.

**Files.** New: `app/templates/today.html`, `app/routers/dashboard.py`,
`app/services/dashboard_service.py`, `app/services/contact_query_service.py`,
`tests/test_dashboard.py`, `tests/test_contacts_api.py`. Modified:
`app/templates/contacts.html`, `app/routers/contacts.py`, `app/routers/pages.py`,
`app/services/contact_service.py`, `app/core/config.py`, `app/main.py`. Deleted:
`app/templates/dashboard.html`. Nothing of session 4's is in the diff.

**What the merge with session 4 needs to know.**
- `campaigns.category_id` and `campaigns.scheduled_at` are both read through
  `getattr`, so the hero already prefers them the moment the migration lands and
  needs no edit here. `dashboard_service._category_for_campaign()` is where.
- `app/routers/dashboard.py` imports `templates` and `shell_context` from
  `pages.py`. `main.py` gained two `include_router` lines next to the existing
  ones — the only overlap point, and it is additive.
- Both new test modules purge everything they seed, because the suite shares one
  database and `test_smoke` asserts an exact `sent_count` against audience "all".
  Any new test module that seeds contacts must do the same.

**Next.** Module 5b (go live) once 4 merges. Before the launch import, the
client's real CSVs are still needed — the header mapping is covered by a fixture
but his actual column names are not.
