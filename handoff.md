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

# Session 4 handoff — Composer & campaign guardrails

_Appended, not rewritten: 3b was editing this file in parallel._

## What landed

The module the project exists for. Sending the wrong niche the wrong message is now
structurally hard rather than a matter of him being careful.

- **`campaigns.category_id`**, plus `cross_category_override`, `suppressed_count` and
  `scheduled_at`, in `alembic/versions/8c1d4a2f70b3_*`. Additive, batch-mode (SQLite
  cannot add a REFERENCES constraint in place), and the downgrade drops exactly what the
  upgrade added. Nothing near `ix_contacts_phone`.
- **`app/templates/campaigns.html`** rebuilt as the three-step composer, with the
  behaviour in `app/templates/_composer-script.html`.
- **`app/services/preflight_service.py`** — suppression, the seven checks, and the
  client-rate cost arithmetic.
- **`POST /api/campaigns/preflight`**, and `/preview` extended with the client's cost,
  the suppressed and opted-out counts, and a preview rendered against a real contact.
- **A one-minute scheduler job** in `main.py`'s lifespan.

## Four things the next session should know

**The category rule lives in the service, not the router.** `resolve_category()` raises
`CampaignError`, the router turns that into a 400. A script, a future screen or module
5b's import path cannot route around it by not being HTTP.

**Suppression happens at create time, not send time.** Held-back contacts get a `skipped`
`SMSMessage` row the moment the draft is built, which is what puts the count on screen
while there is still time to change the audience. It also means `skipped_count` on a
fresh draft is already non-zero — that is the suppression, and the send loop adds
blocklist and region skips to it as it runs. `suppressed_count` is the one that means
only "held back as recently texted".

**The composer's dollar figure is marginal, not `segments × rate`.** He has 10,000
included segments a month, so the first campaign of the month usually costs $0.00 and the
one that crosses the allowance costs only the part above it. `marginal_cost()` is the
difference of two `billing_service.cost_for_segments()` Decimals, subtracted before any
rounding. Do not "simplify" it to a multiplication — it would over-state every early send
and under-state the crossing one.

**Two module-4 files exist because of a rate limit and a Tailwind glob**, and both are
load-bearing:
- `_composer-script.html` is a template, not a `.js` file, because Tailwind's content glob
  is `./app/templates/**/*.html`. The category-chip and checklist class names are built in
  JS; under `app/static/` they would be purged from the compiled stylesheet.
- `tests/test_campaign_guardrails.py` resets the campaign limiter around itself
  (it and `test_campaign_preflight.py` share `tests/_guardrail_setup.py`).
  `POST /api/campaigns` is 5/minute per IP, the whole suite runs inside one window from
  one address, and four creates here starved test_smoke and test_whitelabel of theirs.
  The cap is not weakened; the module gives back what it spends, and everything not
  testing the HTTP contract goes through the service instead.

## Verified this session

Gate green at both ends (76 tests before, 99 after). Suite green twice in a row and every
file green run alone. `alembic upgrade head` from empty, `alembic check` clean, and the
new revision round-trips down and back up. Composer served at `/campaigns` → 200 with
`app.css` → 200. A campaign scheduled two minutes in the past was picked up by the real
scheduler, logged `pre-flight OK`, and completed 4 sent / 1 skipped against the console
provider.

## Not done, and deliberately

- **The audience picker offers two shapes, not the spec's three.** §1 lists "everyone in
  the category / category minus recent recipients / a saved list ∩ category", but §4
  makes suppression unconditional — so options one and two resolve to the same audience
  and the only way to make them differ is to let one bypass suppression, which §4 forbids
  and which is on the escalation list. The picker offers the category and the list ∩
  category, and the always-on rule is stated under it. Flagged rather than guessed.
- **`SMS_PROVIDER` is still `console`.** Nothing in this session can send a real message.
