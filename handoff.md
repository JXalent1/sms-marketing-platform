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

---

# Session 5b handoff — Part A, go-live prep (2026-08-19)

## Where this leaves things

The build is done and prepared. **The next action is a human's**, and it is Part
B of `sessions/session-5b.md`: create the droplet, write the production `.env`
from `deployment/PRODUCTION_CHECKLIST.md`, deploy in dry run, import the real
CSVs, and only then the send sequence. Nothing in this session can send a
message; `SMS_PROVIDER` is `console` in every file it touched.

Gate green, 133 tests (was 119).

## What landed

| Area | File |
|---|---|
| Runbook (ours) | `docs/RUNBOOK.md` — new |
| Production config | `deployment/PRODUCTION_CHECKLIST.md` — new |
| Deploy hardening | `deployment/deploy.sh` |
| Monitoring | `app/services/monitoring_service.py` — new; wired in `app/main.py` |
| Cron entry point | `scripts/balance_alert.py` — now a shell over the service |
| Exact pre-flight | `app/services/preflight_service.py`, `app/routers/campaigns.py` |
| Dark tokens | `settings.html`, `blocklist.html`, `usage.html`, `base.html` |
| Docs | `docs/API.md`, `docs/CLIENT_GUIDE.md`, `docs/screenshots/`, `README.md` |
| Dev defaults | `.env.example` |

## Five things worth knowing before you touch any of it

**1. `deploy.sh`'s order is the design, not a preference.**
`clean tree → build → sync → BACK UP → MIGRATE → restart → health check →
roll back`. Back up before migrating because a migration is the one step with no
undo. Migrate before restarting because a worker meeting an unknown schema serves
500s on every page. Health-check after restarting because "systemctl says active"
and "the app answers" are different claims. Reordering any pair of these removes
a specific protection.

The rollback restores **code only**. A migration is not undone — the script says
so in its own output, and the pre-migrate backup is what you restore by hand if
the schema is what broke.

**2. Pre-flight's segment total is measured, not estimated, and that is load-bearing.**
`exact_segment_totals()` renders the template against every resolved recipient
using `CampaignService.render` — the send path's own function, passed in as a
callable rather than imported, because `campaign_service` already imports
`preflight_service` and because measuring with a second copy of that logic would
eventually measure something the send path does not produce.

If you add a merge tag, nothing here needs to change. If you change how rendering
works, this follows automatically. That is the point of passing the callable.

`/preview` deliberately still reports the cheap template estimate — it runs on
every keystroke. When the two disagree, pre-flight is right, and the composer now
says so on screen.

**3. The low-balance alert must not go back through `provider.send()`.**
That is how it was, and at a true zero balance the warning fails with everything
else. It goes through `agent/notify.sh` now. `notify.sh` is human-only and still
reads the carrier credential by default — **give it its own credential before
go-live** or the fix is undone by configuration. It is in `status.md` under
"Found while working" for that reason.

**4. No child template may carry its own `<h1>`.**
The shell renders the page heading from `{% block title %}`. All three remaining
offenders were fixed this session and `base.html`'s header comment now says so.
Adding one back puts the page name on screen twice, which is what Settings looked
like until now.

**5. The spec's segment example was wrong and the correction is documented in the test.**
`sessions/session-5b.md` A7 describes a 158-character template with
`{first_name}` crossing a boundary for an 11-character name. That cannot happen —
the tag is twelve characters, the name is eleven, so the message gets shorter.
`tests/test_campaign_preflight.py` carries the arithmetic and tests both
directions. Do not "fix" the test back toward the spec.

## Verified this session

- Gate green at both ends — 119 start, 133 end
- Suite twice, 133 both
- `bash -n` clean on every script; `shellcheck` not installed on this machine
- `deploy.sh --dry-run` and `bootstrap.sh --dry-run` exit 0, no side effects
- Both scheduler jobs log their registration by id on startup
- A simulated low balance pages through `notify.sh` with a provider stub whose
  `send()` raises — nothing was sent, and a regression would fail loudly
- No carrier name in `CLIENT_GUIDE.md` or `RUNBOOK.md`
- `SMS_PROVIDER` is never set to a live provider anywhere in `deployment/`

## Not done, and deliberately

- **All of Part B.** Server, DNS, certificate, production `.env`, real
  credentials, the provider switch, the real CSVs, the first message. Human work,
  in the escalation list, and it stays there.
- **Module 8.** History and Categories screens are still absent from the nav;
  `app/routers/usage.py`'s wholesale-rate literal is still its note to close.
- **`docs/NEW_CLIENT_CHECKLIST.md`** still predates the category work. Out of
  5b's file list.
