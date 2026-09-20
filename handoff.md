# Handoff

_Last updated: 2026-09-07 (session B1). Sections append; the bottom is current._

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

## What the review changed

Twelve findings, all fixed except the escalation. The ones with a lesson in them:

- **Two similar arithmetics beat two different ones, and a comment asserting
  they agree is not an assertion.** `stripe_meter` priced a legacy row as 1
  segment; `billing_service` priced it by length. A 480-character row metered as
  1 and rendered as 3. `legacy_segment_count()` is now the one implementation.
  The original test used a two-character message, so it could not see it.
- **A tool that prices a window will price any window.** `bill_period.py` gave
  half of July its own free 10,000 allowance — $120.00 as one run, $0.00 + $0.00
  as two. It now names the cycle containing `--start` when the window is not it.
- **A check that runs once reports its answer forever.** The tier check only ran
  at checkout. Now: daily on the scheduler, and an unrefreshed agreement ages
  into `stale` so a box whose scheduler died says so.
- **A self-check that calls the function it just replaced cannot fail.** Check
  1b did exactly that, and tested the one socket patch of three that an HTTP
  client does not take. Fifth measurement script in this project to need fixing
  before the code did.

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

---

# Session 5c handoff — live-send blockers (2026-08-20)

## Where this leaves things

Part A is done and its acceptance runs as a script rather than a claim:
`bash agent/accept-5c.sh`. Criteria 1-5 pass. **Criterion 6 does not — it needs
the deploy, and the deploy is Jordan's.** Until it runs, the live box is still on
the hot-patched SDK and the pre-5c nginx config; the repo is ahead of the server
on both.

Nothing here can send. `SMS_PROVIDER` is `console` in every file this session
touched, no live credential was read or written, and no contacts were imported.

## What landed

- **`telnyx==4.175.0`** in `requirements.txt`, verified against the real package
  in a venv built from `requirements.txt` alone — the send call, the response
  shape, *and* `messages.retrieve()`, which `get_message_status()` uses and which
  the spec did not name. Nothing else in the file resolves differently: diffing
  full installs of the old and new pins, only telnyx moves, `distro` arrives and
  three 2.x transitives nothing imports (`cffi`, `pycparser`, `PyNaCl`) leave.
- **A third send mode.** `get_provider()` now records a `ProviderFallback`
  instead of only logging one, and `send_mode()` in `app/sms/factory.py` returns
  `live` / `dry_run` / `unavailable`. The pill, the Settings banner and
  `/api/settings/system` all render its `label`/`detail` verbatim.
- **Twelve tests**, in `tests/test_provider_status.py` and `tests/test_whitelabel.py`,
  over `tests/_provider_setup.py`. All ten fail against the pre-fix tree — the
  acceptance script proves it by checking out that commit and running them there.
- **Security headers and a CSP** in `deployment/nginx.conf.template`.

## Four things worth knowing before you touch any of it

**`shell_context()`'s `send_mode` changed meaning.** It used to be the label
("Live" / "Dry run"); it is now the state key (`live` / `dry_run` /
`unavailable`), and the label is `send_mode_label`. The session 3a section above
still describes the old shape. Only `base.html` consumed it, and it was updated
in step.

**The wording lives in one place on purpose.** `SEND_MODES` in
`app/sms/factory.py`. That module knows the carrier's name, which is exactly why
it owns the client-safe projection of the carrier's state — the same argument
that puts `scrub_provider_text()` in `app/sms/phone.py`. A surface that computes
its own answer is how the API and the UI came to disagree in the first place.

**`ProviderFallback.error` is raw SDK text and names the carrier.** It says
`module 'telnyx' has no attribute 'Telnyx'`. It is deliberately not in any
response, and `test_degraded_send_path_names_no_carrier` re-scans every
client-facing route with that string held in memory. Do not "help support" by
rendering it.

**Anything that flips the provider must restore it.** `factory` caches one
instance per process and the suite shares it; `CampaignService` resolves the
provider when it is constructed. Use the context managers in
`tests/_provider_setup.py` rather than assigning `settings.SMS_PROVIDER` by hand.

## Verified this session

- Gate green at both ends — 133 start, 145 end
- Suite twice, 145 both
- Clean venv from `requirements.txt` installs 4.175.0 and `TelnyxProvider()`
  constructs against it — construction only, nothing sent
- The nginx template run in a real nginx (container) with the app behind it: all
  five headers present on a page, on an app 404 and on nginx's own `/.env` 404;
  `/static/app.css` and all four Inter weights 200 under the CSP
- The new tests fail 11/11 against `0e89818` with that tree's own pins

## Not done, and deliberately

- **The deploy, and therefore acceptance criterion 6.** Run it, then
  `A4A_URL=... A4A_PASSWORD=... bash agent/accept-5c.sh --with-remote`.
- **The nginx config on the box.** `deploy.sh` does not touch nginx and `appuser`
  has no root. The template's header comment carries the merge instructions and
  the two curls that prove it took.
- **All of Part B.** Contacts, the first send, the STOP test. Human work.
- **The `SECRET_KEY` guard.** Still a note in `status.md`, still post-launch, as
  the 5c spec says.
- **`docs/API.md`, `docs/RUNBOOK.md` and `docs/CLIENT_GUIDE.md` are now stale on
  the send-mode pill** — all three describe two states. Module 8's files;
  recorded under "Found while working (session 5c)".

---

# Session 5g handoff — blocklist correctness (2026-08-26)

_(Session 5d did not append here. Its handoff is `status.md` → "Module 5d Part A".)_

## Where this leaves things

`decisions/003-auto-block-fragments-on-the-webhook-path.md` is implemented, in the
order that decision rules. `bash agent/gate.sh` passes at both ends, twice in a
row: **259 tests**, up from 184. `bash agent/accept-5g.sh` — the Part A stop
condition — passes checks 1-8. Check 9 needs the deployed box.

Nothing sent. Nothing unblocked. No contact data touched. The 2,959 existing
blocklist rows are exactly as they were; this session changes what happens to
*future* failures, which is what the spec asked for and all it asked for.

## What landed

- **`app/sms/compliance.py`** is now a classifier rather than a boolean.
  `classify_failure(error_message, error_code)` returns `{block, reason,
  alert_key, alert_detail}`; `should_auto_block()` is the thin wrapper over it
  and kept a signature `campaign_service.py:427` can still call, because that
  file belongs to 5e.
- **`sms_messages.error_code`** (migration `b7e3c9a1d024`, additive, nullable,
  nothing backfilled), populated from `errors[].code` on the Telnyx webhook and
  from `ErrorCode` on the Twilio status callback. Numeric rules read it and
  nothing else.
- **`describe_send_error()`** in `app/sms/providers/telnyx.py` assembles the
  error from `title`/`detail` off `exc.body`. **`strip_payload()`** in
  `app/sms/phone.py` is the backstop, called from inside `scrub_provider_text()`.
- **`carrier_opt_out`** in `BLOCK_REASONS` and in `OPT_OUT_REASONS`;
  `dashboard_service.stat_tiles()` imports that tuple instead of filtering on a
  literal.
- **A configuration alert** — `record_config_alert()` /
  `active_config_alerts()` in `monitoring_service.py`, surfaced as `config_ok`
  and `config_issues` on `/health`.

## Five things worth knowing before you touch any of it

1. **The two fragment lists in `compliance.py` are matched differently on
   purpose.** The block list is `\b`-anchored and narrow; the transient list is
   unanchored and deliberately wide. One causes an action that deletes a buyer,
   the other causes a refusal that costs half a cent. Do not "make them
   consistent".

   **And `"unreachable"` is not in the block list, deliberately** — decision 004.
   It meant three things, two of them transient, and the guard could not separate
   them because Twilio 30003's own wording carries no marker. The residual is
   written out at the fragment list: a line described only as unreachable
   survives. `agent/mutate-5g.py` R11 fails if it comes back.
2. **Codes never go back into the text list.** Not even `\b`-anchored — decision
   003 rules that out by name, because `\b21610\b` still matches a bare code in
   prose and prose is where the carrier quotes the destination number.
3. **`/health` reads the alert without `Depends(get_db)`.** That is not an
   oversight. `deployment/deploy.sh` rolls the release back on a non-200 there,
   so a dependency that raises would revert the deploy that fixes the box —
   the same trap as returning 503, one layer up. `active_config_alerts()` opens
   its own session, selects columns rather than entities, and returns `[]` on
   anything going wrong.
4. **`agent/mutate-5g.py` is the check with teeth, not check 8.** If you change
   a 5g rule, add a mutation for it. The harness reverts each fix inside the
   current API and requires a test to notice; a rule with no mutation is a rule
   nobody will find out has regressed.
5. **The two 5g test modules split along "what a failure does" / "what a failure
   says"**, with `tests/_carrier_failure_setup.py` holding the live error corpus
   and the fixtures. Same 500-line-rule pattern as `_guardrail_setup.py`. The
   four corpus strings are verbatim from the live box — paraphrasing them tests
   our paraphrase, which is precisely how "deemed invalid" got missed.

## Verified this session

- `agent/gate.sh` green twice, all six checks, at 259 tests
- Migration `b7e3c9a1d024` applies to a clean database, downgrades, and
  re-applies
- `agent/accept-5g.sh` checks 1-8b, including the before/after classifier table
  against `f16998b`: nine hazard wordings blocked a buyer on the pre-fix tree
  and none does here, while all three live wordings that must block still do
- Check 8b: each of the eleven fixes reverted behaviourally, one at a time, in a
  scratch copy of the tree (`agent/mutate-5g.py`). Every one breaks at least one
  test that names it — no mutation survives
- Decision 004 implemented: `"unreachable"` removed, acceptance criterion 3
  re-pointed at wordings that carry a live fragment, and the discriminating
  property itself asserted in
  `test_every_transient_wording_would_block_without_the_guard`
- Adversarial classifier probe run by hand over ints, empty strings, padded
  codes, `None`, plurals, punctuation and mixed case
- All twelve `scrub_provider_text()` call sites reviewed: every one is an error
  string, none a message body, so `strip_payload()` cannot eat a merge tag

## Not done, and deliberately

- **The deploy, and therefore acceptance criterion 9.** Run it, then
  `A4A_URL=... A4A_PASSWORD=... bash agent/accept-5g.sh --with-remote`. The box
  still runs the pre-5c nginx config, the hot-patched SDK and pre-5d code.
- **Re-adjudicating the existing 2,959 rows.** Out of scope by name. Whether any
  past block was wrong is a separate question and a separate decision.
- **Line-type screening at import.** Endorsed in decision 003, belongs with the
  import flow, still logged in `modules.md` under "Found in live use".
- **The submission path's reason and code.** `campaign_service.py` still files
  everything as `delivery_failure` and `SendResult` has no field to carry a
  code. It is in 5e's file set. Three lines, whenever 5e touches it.
- **Anything in 5e's file set**, including the `campaign_service.py` capacity-row
  and campaign-recovery items carried over from 5d.

---

# Session 5e handoff — campaign-first flow & QoL (2026-08-27)

## Where this leaves things

`sessions/session-5e.md` Part A is implemented. `bash agent/gate.sh` passes at
both ends, twice in a row: **319 tests**, up from 259. `bash agent/accept-5e.sh`
— the Part A stop condition — passes checks 1-10. Check 11 needs the deployed box.

Nothing sent. No live credential read. No contact data touched outside the
suite's scratch database and a local dev one.

One escalation is open and does **not** block:
`decisions/005-topping-up-a-contact-the-window-held-back.open.md`.

## What landed

| | |
|---|---|
| Upload as step one | `app/routers/campaign_uploads.py`, `app/services/campaign_builder.py` |
| Optional category tag | `app/services/import_service.py`, `app/routers/imports.py` |
| Add one contact | `app/routers/contacts.py`, `app/templates/contacts.html` |
| Top-up | `app/services/campaign_topup.py`, migration `c4f1a80b6e37` |
| The hold-back window | `app/services/suppression_service.py`, `app/routers/settings.py`, `settings.html` |
| A run that reached nobody | `app/services/campaign_outcome.py`, `campaign_service.py` |
| Composer | `campaigns.html`, `_composer-script.html`, `_composer-upload.html` (new) |

## Seven things worth knowing before you touch any of it

1. **A campaign now has three ways to record who it is for**, not two: a
   category, a typed cross-category override, or an audience that is a list
   uploaded for it. The third is not an escape from the rule — `POST
   /api/campaigns` is unchanged and still demands one of the first two for
   `all`, `category:` and an existing list, because for those the audience does
   not say which auction the message is about. `list_audience=True` is passed
   only by the upload flow. See `campaign_builder.py`'s module docstring.

2. **A top-up's candidate set is "members of this campaign's list added after the
   campaign was created"** — read from `contact_list_members.added_at`, never
   inferred by subtracting message rows. The review found two live defects in
   the subtract version: it defeated `batch_size`, and on an `all` audience it
   offered to text everyone imported since for a different auction. A top-up
   therefore requires a `list:` audience and refuses anything else by name.

3. **`add_to_list()` stamps `added_at` itself, and that is load-bearing.** The
   column's server default is SQLite's `CURRENT_TIMESTAMP` — UTC, where every
   other timestamp in this app is local. Two clocks in one column made every
   hand-added contact look up to five hours newer than it was. One writer, one
   clock; the comparison parses rather than compares strings, because the column
   also carries two ISO spellings.

4. **The hold-back window lives in `suppression_service.py` and every function
   there takes a Session, with no default.** A caller that forgot would fall back
   to the `.env` value and the Settings field would silently stop applying on
   that one path — the same shape as a guard wired into one of its call sites.
   The *rule* is unchanged and is on the escalation list; only the source of the
   number moved.

5. **A7 is scoped to the run, not the campaign.** A first send that reaches
   nobody is `aborted` with a reason; a *top-up* that reaches nobody keeps
   `completed` and stores a reason fronted with "Top-up of N recipients:". A
   campaign that reached 1,200 people did complete, and one later event cannot
   revoke that — the same argument that stopped a late failure webhook from
   un-delivering a message in 5d. The sentence carries the distinction because
   the badge beside it cannot.

6. **Both composer partials share one scope, and `composerMode` is declared in
   the first.** `_composer-upload.html` owns what changes it; declaring it there
   would leave it in its temporal dead zone when the first `/preview` response
   lands. Both `refreshPreview()` and `runPreflight()` guard on it — the review
   found the second one missing, which had "Run checks" drawing a capacity
   verdict over every contact in the database under a composer pointed at a file.

7. **`agent/mutate-5e.py` is the check with teeth, not the test count.** 28
   mutations, all caught, none unapplied. If you change a 5e rule, add a mutation for it — and
   write one you can actually *reach*: the first run reported two green ticks for
   mutations of dead defaults, and a third that was being "caught" by an
   unrelated test leaking a stored setting between modules.

## Verified this session

- `agent/gate.sh` green twice, all six checks, at 319 tests
- `agent/accept-5e.sh` checks 1-10, including the mutation run
- Migration `c4f1a80b6e37` applies to a clean database; `test_migrations.py`
  asserts the four `sms_messages` indexes and both added columns survive
- The whole flow driven against a running instance: a 5-row CSV with a repeat and
  an unusable number → preview counts → campaign named "Italian restaurants" on
  its own list, `category_id` NULL, 3 recipients → sent → one contact added by
  hand → topped up → 4 sent, 4 recipients, `3 + 1 added 27 Aug`, and **every
  recipient holding exactly one message row**
- The window raised to 7 in the running app: `/preview` reported 4 held back and
  a clearing time, and the checklist row read "…in the last 7 days… The hold
  clears at 10:47am on 3 Sep"
- A campaign whose audience was fully held back came out `aborted` with the
  reason on the record, where before 5e it would have read `completed, sent 0`
- All seven screens 200 over a real server; no carrier name in any of them
- Every `<script>` block in the four touched templates parsed with `node --check`

## Not done, and deliberately

- **The deploy, and therefore acceptance criterion 11.** Run it, then
  `A4A_URL=... A4A_PASSWORD=... bash agent/accept-5e.sh --with-remote`.
- **`decisions/005`** — whether a top-up should reach somebody the window held
  back once the hold has cleared. Escalation item 5, and the fix needs a new
  `MESSAGE_STATUSES` member to stop `skipped` meaning two things.
- **The pre-flight capacity floor.** `wholesale_estimate()` rounds to 2dp, so a
  one-segment top-up needs $0.00 and passes on an empty account. Pre-existing,
  and the fix is a change to the guard — escalation item 3.
- **The submission path's block reason and code**, carried over from 5g. Three
  lines plus a field on `SendResult`, but it is blocklist behaviour on a path 5e
  was not sent to change.
- **All of Part B**, and anything in 5f (short links, click stats, reports).

---

## Session 5h — held-back rows & the capacity floor (2026-08-30)

## What just happened

Two send-path corrections from 5e's review, both small, both blocking the client
handover. `decisions/005` implemented (option 2, all four riders), and the
pre-flight capacity check made exact.

## State of the code

`bash agent/gate.sh` green at both ends, twice in a row. **371 tests** (319 + 52).
`agent/accept-5h.sh` checks 1-8b pass; check 9 needs the deploy.
`agent/mutate-5h.py`: 27 mutations, none surviving.

## The seven things to know before touching this

1. **`held_back` and `skipped` are different statuses and the difference is the
   whole session.** `held_back` is a hold that expires; `skipped` is "wrong
   region" and is permanent. Both are outside `BILLABLE_STATUSES`. If you are
   adding a status to `sms_messages`, the question to answer in its docstring is
   which of those two shapes it has.

2. **Rows written `skipped` before 2026-08-30 mean either thing and are never
   re-adjudicated.** Decision 005 rider 2, and
   `alembic/versions/e2a7c3d15b48_held_back_message_status.py` — a migration that
   deliberately does nothing — is where it is written down, because that is where
   somebody about to write the backfill will look. `agent/mutate-5h.py` R10 is
   the backfill, and it has to keep failing a test.

3. **A release flips the row; it never writes a second one.** One person held
   back once and reached later is one row with a history. The candidate rule is
   in `campaign_release.py` (who may go), the write is in `campaign_topup.py`
   (a partial write is what that module exists to prevent), and the split is
   there because `campaign_topup.py` crossed 500 lines.

4. **The release does not need a `list:` audience; "added since" still does.**
   That is not an inconsistency. `NOT_A_LIST_AUDIENCE` refuses a set nobody
   chose — everyone who joined `all` since the campaign sent. A held-back row is
   a contact this campaign resolved and counted itself. Do not "tidy" the two
   halves into one rule.

5. **A released row's `top_up_at` is when it was released, not when it was
   written.** The column funds one report — how the recipient count reached the
   number on screen — and a released row joins that count on the day it goes out.
   `top_up_history()` excludes `held_back` rows for the same reason.

6. **Compare `wholesale_cost()`, store and log `wholesale_estimate()`.** The
   first is exact `Decimal`, the second is it rounded to cents. The capacity
   check takes the **larger** of the exact cost and the caller's stated one, so
   the change can only ever tighten — replacing the rounded figure with the exact
   one would have *loosened* the guard wherever rounding went up, and the
   pre-flight check is escalation item 3.

7. **`agent/mutate-5h.py` is the check with teeth, not the test count.** It found
   two mutations no test noticed and one that had no reachable behaviour at all,
   which was dropped rather than papered over. If you change a 5h rule, add a
   mutation — and check the tests that fail for it are the tests that name it.

## Verified this session

- `agent/gate.sh` green twice, all six checks, at 355 tests
- `agent/accept-5h.sh` checks 1-8b, each criterion's tests run in isolation
- 15 behavioural mutations, 0 survivors, 0 unapplied
- Migration `e2a7c3d15b48` applies to a clean database and performs no DDL
- Decision 005's reproduction driven end to end at its own numbers: 4-person
  list, 2 texted yesterday, window 3 → 2 sent, 2 `held_back` → window 0 → top-up
  reaches exactly those 2, by flipping their rows, **one row per phone**
- The capacity verdict printed before and after the fix at both rates
- The `round()` audit walked by AST: five sites, four percentages and one
  segment count computed after the comparison

## Not done, and deliberately

- **The deploy, and therefore acceptance criterion 9.** Run it, then
  `A4A_URL=... A4A_PASSWORD=... bash agent/accept-5h.sh --with-remote`.
- **Any backfill of pre-5h `skipped` rows.** Ruled out by decision 005 rider 2
  and not a task waiting to be done.
- **The submission path's block reason and code**, carried over from 5g and 5e.
  Blocklist behaviour, escalation item 5, no 5h criterion touches it.
- **All of Part B**, and anything in 5f (short links, click stats, reports) —
  including how the rail should word a top-up's own held-back count.

### What the review changed (5h, same day)

One synchronous fresh-context reviewer. Read `status.md` → "What the fresh-context
review changed" for the detail; the two that alter how you should think about this
code:

8. **A capped campaign releases nobody, and that is a placeholder for a ruling,
   not a design.** `campaigns.batch_size` is recorded now (migration
   `a91d5f2c6b70`) purely so `campaign_release.releasable()` can decline. Before
   the guard, a campaign capped at 3 with 10 held back sent to 13 on one click.
   `decisions/006-which-campaigns-may-release-a-hold.open.md` is **open** and
   covers this and the fully-suppressed-campaign case with it. Do not close
   either by guessing.

9. **`releasable()` returns three lists, not two.** `(release, still_held,
   departed)`. A held-back row whose contact no longer exists is not being held
   by the window — it is a person who left the list — and folding the two
   together had the refusal blame a rule that was switched off. If you add a
   fourth reason a row cannot be released, give it its own bucket rather than
   widening `still_held`.

### Decision 006 implemented — the two sentences a hold produces (same day)

006 resolved as **option 1 for both cases** with a mandatory wording fix: a capped
campaign still releases nobody, a fully-suppressed campaign still stays `aborted`,
and both sentences now say why and what to do. **No state change, no new column.**
Read `status.md` → "Implementing decision 006" for the reasoning; the four things
to know before touching it:

10. **A held row is frozen at build time and read at send time, and those are
    different moments.** The campaign can have nothing to send while the people
    it held are perfectly reachable — a draft saved Monday and sent Friday, a
    scheduled send, or the window turned off on Settings. `_still_ahead()` in
    `campaign_outcome.py` splits the three cases: the hold is ahead (name the
    time, wait), the hold has gone (rebuild now), or it is unknown (say neither).
    Any new sentence about the window has to answer that question before it
    picks a tense.

11. **`suppression_clears_at()` computes the moment and `clears_at_clock()`
    renders it, once each.** The composer's checklist row and the abort reason
    are two surfaces on one rule; `preflight_service._clock()` delegates rather
    than implements. Do not add a second formatter.

12. **`capped_campaign_hold()` lives in `campaign_release.py`, not beside the
    other refusals.** The layer that makes a decision owns the client-safe
    sentence about it — `send_mode()` is the pattern — and `campaign_topup.py`
    was over 500 lines again. It re-exports the name.

13. **`hold_clears_at()` returns None rather than raising, on purpose.** It runs
    between the send loop finishing and the campaign's final status being
    written, which was pure arithmetic before 006. An exception there leaves the
    campaign `running` for ever, which is strictly worse than a sentence missing
    a clause. Same reasoning as `/health` reading its row without `Depends`.

---

## Session 5f — short links, click stats and reporting (2026-08-31)

Part A complete except the deploy. **418 tests** (371 + 47), gate green twice at
both ends, `agent/accept-5f.sh` the stop condition (60-turn cap in its header),
`agent/mutate-5f.py` the check with teeth — 26 behavioural mutations, none
surviving. Nothing in this session can send: `SMS_PROVIDER` stays `console`
everywhere, `.env` was not written, and the one costing carrier stub returns a
`SendResult` without touching a network.

Read `status.md` → "Module 5f Part A" for the full account. What the next session
has to know before touching any of it:

14. **A short link is per recipient per campaign, and that is the feature.**
    "340 clicks" is a statistic; "these 340 people" is a phone list, and for an
    auction house that is the highest-intent audience it will ever have. Any
    change that makes minting cheaper by sharing a slug throws the whole thing
    away.

15. **`SHORT_LINK_DOMAIN` unset is a supported state, not a broken one.** The
    domain may not be registered. Unset means the composer *refuses* the tag,
    at compose time, with a sentence naming the cause — it never mints a link
    nobody can follow and never defers the refusal to the send. The setting is
    read live on every call, so a change takes effect without a restart, and
    every test that sets it restores it.

16. **`link_service.placeholder_url()` is the same length as a real link by
    construction, and something depends on that.** Pre-flight cannot mint 4,200
    rows on every press of "Run checks", so it renders with the placeholder and
    quotes that count. If the two lengths ever diverge, the quote and the invoice
    diverge silently. `test_the_placeholder_is_the_same_length_as_a_minted_link`
    is the pin, and `test_the_preflight_endpoint_quotes_the_rendered_link_not_the_tag`
    is the one that proves the *endpoint* does it — the helper-only version of
    that test let a mutation through.

17. **`{` and `}` are GSM-7 extended characters.** `{link}` is eight septets, not
    six. Any arithmetic about template length has to be done with
    `count_segments()`, not `len()`.

18. **`GET /{slug}` is a root catch-all and must stay registered last in
    `main.py`.** Starlette matches in registration order. Anything included after
    it is unreachable.

19. **Clicks are two numbers, always.** The human count leads, the filtered count
    sits beside it in words, and every suspected-scanner row is kept with its
    user agent and the rule that fired. The classifier is deliberately wider than
    the auto-block list and `click_classifier.py` says why: this one changes which
    of two visible numbers is the headline, the other one deletes a buyer.

20. **The `carrier_cost*` columns and `WHOLESALE_COST_PER_SEGMENT` are ours.**
    The admin login *is* the client, so "operator-only" means not served, not
    "behind auth". `cost_reconciliation.py` is the only reader and there is no
    route to it; `scripts/cost_report.py` and one INFO line per finished campaign
    are how a human sees it. `accept-5f.sh` check 8b fails if a router or
    template so much as names one of them.

21. **The report's cost is marginal within its own billing cycle**, not
    `segments × rate`. Two campaigns in one cycle therefore do not sum to the
    cycle total once the allowance is crossed between them — that is a property
    of an allowance, the screen says "Added to August" rather than "cost", and
    the Usage screen still holds the number that is billed.

22. **A history page's query count must not move with its row count.** The tests
    assert two rows against eight cost the *same* number of queries, not an
    absolute bound: a bound alone passes happily on a per-row lookup while the
    fixture is small, which is how an N+1 ships under a green suite.


---

## Host guard — the short domain serves only short links (2026-08-31)

Added after 5f, because 5f shipped a second public hostname and both names
answered every route. 449 tests, gate green twice, `agent/accept-5f.sh` criteria
12-14, six new mutations. Read `status.md` -> "Host-based routing guard" for the
full account. Four things before touching it:

23. **The authority is the app, not nginx.** `deployment/nginx.conf.template`'s
    short-link block proxies everything and names no admin path on purpose. A
    denylist there would have to be updated with every new route, and it would
    be wrong silently. If you find yourself adding `location /something { deny }`
    to that block, the answer is almost certainly in
    `link_service.is_slug_path()`.

24. **`proxy_set_header Host $host` is load-bearing.** The guard compares that
    header against `SHORT_LINK_DOMAIN`. Change it to a literal or to
    `$proxy_host` and the guard silently stops matching — every admin route
    answers on the short domain again, with nothing failing.

25. **The guard fails open when `SHORT_LINK_DOMAIN` is the admin host**, and
    that is deliberate: enabling it there 404s every page of the product. The
    startup log distinguishes all three states (conflict, active, unset), so a
    box behaving oddly is one `journalctl` away from the answer.

26. **`HEALTH_URL` in `deploy.sh` must never be the short domain.** `/health` is
    404 there, a non-200 rolls the release back, and that would revert every
    deploy including the one that fixes the box. The default is
    `127.0.0.1:8000`, which matches no domain, so this only bites someone who
    overrides it.

---

# Session P1 — the prospect pipeline (Part A), 2026-08-31

## What just happened

The holding pen between a scraper and the textable list, with the line-type gate
that makes scraping economic and the review step that keeps sellers out. **No
source implementations** — this session built the machinery, because if it is
built right adding Google Places is a class and a taxonomy, and if it is built
wrong every source inherits the damage.

## State of the code

`bash agent/gate.sh` passes, twice. **494 tests** (449 + 45). Migration
`c8a2e5f14b90` applies to a clean database and `compare_metadata` reports no
drift. `bash agent/accept-P1.sh` is the stop condition: criteria 1-10 and the
four structural checks pass locally; criterion 11 is `--with-remote` and needs
the deploy, which is **not done** — same state as 5c through 5h.

## Where things are

| What | Where |
|---|---|
| Tables | `app/models/prospect.py` (prospects, sightings, rejections), `app/models/scrape.py` (jobs, lookups) |
| Migration | `alembic/versions/c8a2e5f14b90_prospect_pipeline.py` — additive, five tables |
| Source seam | `app/sources/prospect_base.py`; registry in `app/sources/__init__.py` (`PROSPECT_SOURCES`, empty) |
| Carrier lookup interface | `app/sms/lookup.py` — DB-free, default provider makes no call |
| Lookup cache | `app/services/lookup_service.py` — `PROMOTABLE_LINE_TYPES` lives here |
| Writes | `app/services/prospect_service.py` — record, reject, promote |
| Reads | `app/services/prospect_queue.py` — queue, summary, term breakdown |
| Scoring | `app/services/prospect_scoring.py` |
| Job runner | `app/services/scrape_runner.py` |
| API | `app/routers/prospects.py` (`/api/prospects`) |
| Screen | `app/templates/prospects.html`, route in `app/routers/pages.py` |
| Settings | `app/core/config.py` — five `PROSPECT_*` keys |
| Acceptance | `agent/accept-P1.sh`, `agent/mutate-P1.py` |

## The five things to know before touching this

1. **`PROMOTABLE_LINE_TYPES` is one tuple in one place.** The "Ready to promote"
   tile, the queue's `eligible` filter and the promote guard all read it. A
   second membership test anywhere is how the tile comes to promise more than
   the button delivers.
2. **`unknown` is not promote-eligible, and that is the gate working.** With no
   screening provider configured — the default — every prospect reads
   `unknown` and nothing can be promoted. That is correct: a gate that is
   switched off refuses, it does not wave things through. The screen says so
   in a notice rather than looking broken.
3. **A rejection is permanent and is keyed on the phone.** `prospect_rejections`
   is checked at ingest, before anything is written, so the same business from a
   different source with a different name and payload is suppressed on arrival.
   Un-rejecting is not a feature.
4. **`cleanup()` belongs to the source and must be safe to call while `fetch()`
   is still running.** The runner calls it in a `finally` on every exit path
   including the timeout, and it does not wait for a worker it can no longer
   stop — Python cannot kill a thread.
5. **`raw_payload` and the two `cost` columns never cross the API boundary.**
   The payload is unvetted third-party data; the costs are our spend, on the
   `WHOLESALE_COST_PER_SEGMENT` footing. `accept-P1.sh` check 8b asserts it by
   AST, and there is a mutation for each.

## For whoever writes P2 (Google Places)

- Subclass `ProspectSource`, implement `fetch()` and `cleanup()`, register in
  `PROSPECT_SOURCES`, and run it through `scrape_runner.run_job()` — never
  directly. Nothing else needs to change to take a source.
- **Every search term needs a written buyer rationale.** A record without one is
  counted `invalid` and persisted nowhere. That is not a formality: the reviewer
  is answering "would this person bid?" and the rationale is the claim they are
  agreeing with.
- **`source_url` is shown to the client and goes into the CSV export.** It must
  be the human-facing page or search, never the API endpoint — those carry the
  key in the query string.
- **A carrier line-type provider is a budget decision** (escalation item 7). The
  interface is `LineTypeProvider` in `app/sms/lookup.py`; add the class, add one
  line to `PROVIDERS`, add the credential to config. `accept-P1.sh` check 8d
  asserts `PROVIDERS == {"none"}` — that check is P1's, and P2's own acceptance
  script replaces it.
- **Dedup against existing contacts is P2's**, per `modules.md`. Today a
  prospect who is already a contact will appear in the queue.
- The taxonomy in `A4A_BUILD_PLAN.md` includes **Marine**, which has no category
  row on this box. An unrecognised `category_slug` is logged and dropped rather
  than refused, so such a prospect lands uncategorised and the reviewer picks at
  promote time.

## Not done

- **Deploy.** The box still runs the pre-P1 tree. `deployment/deploy.sh` runs
  `alembic upgrade head`, and this migration is additive with no backfill, so it
  is a safe one — but it is a live box holding the client's real contacts and
  message history, and the deploy is a human's call, not this session's.
- **Criterion 11** (all screens 200 over HTTPS, no leaks) needs that deploy:
  `A4A_URL=... A4A_PASSWORD=... bash agent/accept-P1.sh --with-remote`.

---

# Session P1b — the lookup provider, and a gate that flakes (Part A), 2026-08-31

## What just happened

Three things, one of which spends money and one of which could fail a green
build.

1. **The Telnyx line-type lookup provider is wired in.** RULES.md escalation
   item 7 was ruled on by the session spec, so this is the paid API the whole
   prospecting economic case rests on: $0.0025 a number, against 2,526
   not-routable numbers in one campaign's failures.
2. **The flaky white-label assertion is gone**, along with four more sites of
   the same defect class — including the one that scanned *every* client-facing
   route and had simply not lost the dice roll yet. The fifth was found by the
   acceptance check rather than by reading, which is the argument for writing
   the check.
3. **`docs/API.md` documents prospects, reports/history and the public
   short-link route.** It stopped at Settings before.

## State of the code

- **544 tests** (494 + 50). `bash agent/gate.sh` green twice.
- `bash agent/accept-P1b.sh` — criteria 1-9 pass. Criterion 10 needs the deploy.
- `agent/mutate-P1b.py`: 27 mutations, 0 survive, on a scratch tree verified
  byte-identical to the repo before the first patch (it prints
  `SCRATCH VERIFIED PRISTINE`).
- No migration. Nothing about this session changes the schema.

## Where things are

- `app/sms/providers/telnyx_lookup.py` — the provider. One class,
  `client.number_lookup.retrieve(phone, type="carrier")`.
- `app/sms/lookup.py` — the registry (`PROVIDERS`, now two entries, loaded by
  import path to avoid a cycle) and `cost_per_lookup()`, which moved here.
- `app/services/lookup_service.py` — the cache, the monthly spend cap
  (`LookupBudget`, `spend_this_month()`, `monthly_cap()`) and
  `unusable_numbers()`.
- `tests/_wholesale_scan.py` — the one way to ask "did our own cost reach the
  client", and it parses before it compares. `tests/test_wholesale_scan.py`
  proves it in both directions.
- `tests/fixtures/number_lookup_responses.json` — recorded carrier responses,
  replayed through the SDK's own model.

## The five things to know before touching this

- **Lookups and sends draw on the same carrier balance.** That is the whole
  reason the cap exists: a 10,000-number screening run takes $25 out of the pot
  `capacity_assessment()` measures, so an overnight scrape can make the next
  morning's campaign refuse to start with nothing on any screen connecting the
  two events. `PROSPECT_LOOKUP_MONTHLY_CAP` is checked **before** each call, in
  `line_type_for()`. Do not move it to the caller: `screen()` passes a budget
  down as an optimisation, and the rule must survive a caller that forgets.
- **A refusal of ours is not an answer to cache.** A number skipped for cost or
  because nobody will ever text it gets **no row**, so it is screenable next
  month. Writing `unknown` there would be P1's permanent-hole failure with a new
  cause.
- **`phone_lookups.cost` accumulates on a retry.** The monthly total is summed
  from that column; overwriting it lets a number billed twice in a month count
  once, and a ceiling that under-counts permits more than it says.
- **The default provider still makes no call, and that is deliberate.**
  Screening starts spending the moment `PROSPECT_LOOKUP_PROVIDER=telnyx` is set,
  so it is a human's `.env` edit on a live box. Neither `.env` nor
  `.env.production` was touched by this session.
- **`_wholesale_scan` compares parsed numbers, never substrings — and parsing
  numerically was necessary but not sufficient.** `14:23:00.009000` parses as
  *exactly* 0.009, so clock times are stripped before tokenising and a token
  with a redundant leading zero is refused. A colon is deliberately not
  excluded: FastAPI serialises compactly, and a real leak reads `{"rate":0.009}`
  with no space. If you add a client-facing surface, scan it with
  `assert_no_wholesale_field()` (JSON) or `assert_no_wholesale_figure()`
  (HTML/CSV). Do not reintroduce `str(settings.SOMETHING) not in body`: that is
  the assertion this session removed from five places, and
  `agent/accept-P1b.sh` check 8 greps for it.

## For whoever switches screening on

1. Set `PROSPECT_LOOKUP_PROVIDER=telnyx` in `.env` on the box. `TELNYX_API_KEY`
   is already there.
2. Decide `PROSPECT_LOOKUP_MONTHLY_CAP`. The default is $50 and it is not in
   `.env`, so the box inherits it from `app/core/config.py`. $50 is 20,000
   lookups, and it comes out of the same balance sends draw on.
3. Watch the journal for `Screening stopped at the monthly cap`. That is the
   only place the cap is reported — by design, since it is denominated in our
   money and the client's screens say only that a number is unscreened.

## Not done

- **Deploy.** The box still runs the pre-P1 tree. This session adds no
  migration, so P1's is still the one waiting. A live box holding the client's
  contacts and message history is a human's call.
- **Criterion 10** (all screens 200 over HTTPS, no carrier name, no wholesale
  figure) needs that deploy:
  `A4A_URL=... A4A_PASSWORD=... bash agent/accept-P1b.sh --with-remote`.
- **Screening the existing contact list.** Explicitly out of scope — spending
  real money on live data is Jordan's decision.
- **A "skipped by the cap" column on `scrape_jobs`.** A run that screened
  nothing because the cap was reached currently looks like a run with nothing to
  screen. Noted in `status.md` under "Found while working"; it needs
  `scrape_runner.py` and a migration, both outside this session's file list.

---

# Session 5i handoff — named lists replace categories (2026-09-04)

## Where this leaves things

The audience picker is now the Williamson model: **the pinned
`⭐ ALL BIDDERS — MAIN LIST` entry, then every named list, newest first.** No
categories anywhere the client can see — composer, Contacts, Today. Every
category table, column, index and palette variable is exactly where it was, and
the prospect review queue still uses them.

`bash agent/accept-5i.sh` exits 0. The gate is green twice. **577 tests.**
`agent/mutate-5i.py`: 16 mutations, 0 survived, on a tree verified byte-identical
to the repo before the first patch.

## What landed

- **A1** — `contact_lists.created_at` has one writer, one clock and one spelling,
  and migration `f4a1c7d90e52` normalises the rows already stored.
- **A2** — `list_summaries()` is the picker; `SENT_STATUSES` consolidated into
  `app/models/sms_message.py`.
- **A3-A6** — composer, Contacts and Today, with the em-dash rule carried over
  verbatim.
- **A7** — `tests/test_audience_surfaces.py`, a runtime render sweep.
- **A8** — the prospect queue, deliberately untouched and now asserted.

## Six things worth knowing before you touch any of it

**1. The table still has `DEFAULT (CURRENT_TIMESTAMP)` on `created_at`.**
Removing `server_default` from the model does not remove the old writer — SQLite
still fills an omitted column, in UTC, with a space separator. Dropping a column
default there means rebuilding the table, which is escalation item 8. What closes
it is the **Python-side `default=now_iso`** on the column. A raw `INSERT` that
names no `created_at` can still reach the DDL default, which is why
`parse_created_at()` keeps understanding the space-separated spelling instead of
assuming it away. Do not "simplify" either half.

**2. `resolve_audience()`, `_term_ids_query()`, `_term_label()` and
`audience_count()` keep their `category:` branches, permanently.** Every campaign
this client has run stores `category:<slug>` in `campaigns.audience`, and
`audience_label()` renders it in the campaign rail, in history and in every
per-campaign report. The picker stops *producing* them; the resolver must keep
*consuming* them. Mutation `A2b` deletes that branch and ten tests go red.

**3. The pinned wording lives in exactly one place.**
`contact_service.ALL_BIDDERS_LABEL`. It renders in the composer dropdown, on the
first dashboard card, and through `audience_label()` in the summary panel and
every report. A literal anywhere else is a second spelling waiting to drift —
mutation `A2c` proves the test catches it.

**4. The A7 sweep has six exemptions and each one is load-bearing.** They are the
surfaces other clauses of the spec retain: the prospect queue, the taxonomy CRUD
it reads, the contacts payload and export, and the two report screens.
`test_no_exemption_has_gone_stale` fails when an exempted route stops carrying
the word, so the list cannot rot the way a denylist does. If you make one of
those surfaces category-free, delete its entry in the same commit.

**5. `campaign_service.py` is at exactly 500 lines.** The next addition forces a
split. Its docstring already describes the seam 5e used.

**6. `import_service.require_category()` has no caller.** `/api/imports/*` stopped
calling it in 5i — an import lands in a list the client names, and the name
carries what the tag used to. The function and its docstring say so; deleting it
is a separate decision in a file outside 5i's list.

## Verified this session

- `agent/accept-5i.sh` — every criterion, each in its own pytest process.
- `alembic upgrade head` from a clean database **and against a copy of the real
  `data/app.db`**: `1 converted from UTC, 1 left alone`;
  `2026-08-20 00:12:30` → `2026-08-19T20:12:30`. The original was not written.
- `agent/gate.sh` twice; `./run.sh`-equivalent boot with `/login` 200,
  `/static/app.css` 200, `/health` healthy.
- The composer's JavaScript by **execution**, not by reading: every `<script>`
  block on the changed pages concatenated in include order, `node --check`ed, and
  swept for identifiers called but never defined. A3 and A5 delete four functions
  and eight DOM ids; a leftover call site is a `ReferenceError` that kills the
  screen while every render test still passes.

## One thing that happened to your local database

**Starting the app applied 5i's migration to `data/app.db`.** `app/main.py` runs
`alembic upgrade head` on import in development — production does not, and
`deployment/deploy.sh` does it as a deliberate step — so booting a local uvicorn
to check `/login` converted `Demo list` from `2026-08-20 00:12:30` to
`2026-08-19T20:12:30`. That is the migration doing exactly what it is for, and it
is the same result `accept-5i.sh` demonstrates on a copy. Nothing else in that
database was touched: 9 contacts, 8 messages, 2 campaigns, unchanged.

It also cost an acceptance check its evidence, which is the part worth
remembering: check 2b showed the conversion on a copy of `data/app.db`, and once
the app has been started there is nothing left in there to convert — so it
printed an identical before and after and still reported "ok". It now seeds a
server-default-spelled row into the copy **and rolls `alembic_version` back to
`c8a2e5f14b90`**, the revision the live box is on, so what runs is the real
migration against real rows from the revision production will run it from.
Seeding alone was not enough, and the check is what said so — `upgrade head` on
a copy already at head runs nothing.

## Not done

- **Deploy.** Jordan's, as every session since 5b. **A1's migration rewrites data
  in a live table — `scripts/backup.sh` runs before `alembic upgrade head`.**
- **Part B of the session spec, and it gates the migration.** Dump every
  `contact_lists.created_at` on production and confirm each value is either
  `YYYY-MM-DD HH:MM:SS` or `YYYY-MM-DDTHH:MM:SS.ffffff`. A third spelling means
  the format-based classification is not 1:1 and the migration is wrong. It
  leaves an unrecognised value alone and logs it, so a third spelling is
  survivable — as an un-normalised row sorting by a clock nobody has checked.
- **A lists management page** — rename, merge, archive, delete. Out of scope and
  still absent. The client can create a list and pick one; he cannot tidy them.
- **A "which lists is this person on" column on Contacts.** The right product
  answer, a new query on a paged screen, and a session of its own.
- **`docs/API.md`** does not describe any of 5i. Module 8's.

---

# Session 5j handoff — the index migration, the cost guard, list archive/rename (2026-09-04)

## Where this leaves things

Part A is complete and unshipped. `bash agent/accept-5j.sh` exits 0 with every
criterion printed, `bash agent/gate.sh` is green twice at **613 tests**, and
`agent/mutate-5j.py` reports **19 mutations, 0 survived** on a scratch tree it
verified byte-identical to the repo first. Full detail is the 5j section at the
end of `status.md`.

The deploy is Jordan's, and it is the one this session exists to make possible:
`deployment/deploy.sh` aborts without restarting when a migration fails, and
production's schema carries two indexes that no migration describes.

## What landed

**Two migrations, deliberately separate.**

- `a3f1e08c5d47` — creates `idx_sms_contact` and `idx_member_contact` **if
  absent**, then drops `ix_sms_messages_contact_id` and `ix_clm_contact_id` **if
  present**. Create before drop, per table: both orders reach the same schema and
  only one of them leaves an instant with the join unindexed.
- `b52d9c1f4e08` — `contact_lists.archived`, additive, nullable, backfilled to 0,
  no server default.

A reviewer can revert either without the other. `downgrade()` on the first does
**not** recreate the hand-made names.

**The cost guard.** `tests/test_query_cost.py` plus `tests/_query_plan.py`: the
first check in this repo about what a query costs rather than what it returns.

**Archive, rename, delete.** `app/services/list_admin.py` (new — the 500-line
rule forced a split), `PATCH /api/lists/{id}`, `GET /api/lists/manage`, a
`DELETE` that refuses a referenced list with 409, and A5's panel beside the
audience picker in `_composer-lists.html`.

## Four things worth knowing before you touch any of it

1. **The spec's cost assertion was wrong and the shipped one differs.** "No full
   scan of `sms_messages` or `contact_list_members`" is *green* on the 10-minute
   plan (SQLite searches `sms_messages` through `idx_sms_status`, and four
   distinct statuses means each lookup walks a quarter of the table) and *red* on
   the plan that fixed production (which scans `contact_list_members`). The guard
   asserts on the **join key** instead, plus a schema assertion that both sides
   carry an index leading on `contact_id`. Criterion 8 prints the spec's rule
   evaluated on the outage plan so the reasoning is on the transcript. **If you
   disagree with the departure, this is the thing to rule on** — it is written up
   at the top of the 5j "A2" block in `status.md`.

2. **`campaigns.audience_label` is a stored render, not a live lookup.** The rail,
   history and every report read that column, written once by `campaign_builder`.
   `list_admin.rename()` recomputes it for every campaign whose selector names the
   list, **from `audience_label()`** — never by substituting the new name into the
   old string, because a compound selector's label carries another term. Do not
   "optimise" that into a string replace, and do not freeze the label at send time:
   renaming a list is *meant* to change what old reports say.

3. **Archived is one predicate, `contact_service.is_archived()`, and one read,
   `_list_rows()`.** The picker says "archived" by absence; A5's panel says it with
   a badge. `resolve_audience()`, `_term_ids_query()`, `_term_label()` and
   `audience_count()` never look at the flag — that is what keeps a campaign that
   targeted list 20 rendering its name instead of the string `list:20`.

4. **The composer now escapes attributes with `attr()`, not `esc()`.** `esc()`
   does not escape the double quote that ends an attribute, and 5j is what made a
   list label a string the client types. `tests/test_composer_markup.py` sweeps
   for the shape. `base.html` is untouched; other templates using `esc()` in
   attribute position have not been audited.

## Not done, and deliberately

- **Merging two lists.** Out of scope in the spec and a different problem —
  membership provenance (`created_contact`, `created_tag`) makes a merge
  irreversible in a way an archive is not.
- **The `categories` column in `/api/contacts/export.csv`** — residual 1 in
  `modules.md`, still the first item of whichever session touches
  `contact_query_service.py`.
- **A CSV uploaded under an archived list's name still writes into that list and
  it stays hidden.** A behaviour decision, not an oversight; both answers are
  defensible and one of them matters to anyone with a recurring import. One line
  in `contact_service.get_or_create_list()` when it is ruled on. See "Found while
  working" in `status.md`.
- **No delete button in A5's panel.** Archive is what "take it out of my dropdown"
  means; `DELETE` is API-only and refuses anything a campaign referenced.

## Verified this session

`accept-5j.sh` ACCEPT PASS · gate green twice (613 passed) · 19/19 mutations
caught on a pristine-verified tree · migrations exercised on a fresh clone, on
production's shape twice through, and down-and-up again · `./run.sh` with
`/login` 200, `/static/app.css` 200, `/health` `sending_ok: true` · the whole
rename/collision/delete-refusal/archive/unarchive flow driven by hand against the
running box and the state restored · composer JavaScript checked by execution
(`node --check`, no undefined identifier, no undeclared DOM id).

## Part B — Jordan's, after the deploy

```
ssh -i ~/.ssh/a4a_deploy appuser@67.205.180.62 'cd /home/appuser/app && ./venv/bin/python -c "
import sqlite3
c = sqlite3.connect(\"data/app.db\")
for t in (\"sms_messages\", \"contact_list_members\"):
    print(t, [r[1] for r in c.execute(\"PRAGMA index_list(%s)\" % t)])
"'
```

Expect `idx_sms_contact` and `idx_member_contact` present, and
`ix_sms_messages_contact_id` and `ix_clm_contact_id` gone. **Four indexes where
there should be two means the migration created rather than converged**, and the
duplicates cost write time on every send.

---

# Session P2 handoff — the Google Places source (2026-09-04)

## Where this leaves things

Part A is complete and unshipped. `bash agent/accept-P2.sh` exits 0 with every
criterion printed, `bash agent/gate.sh` is green twice at **696 tests**, and
`agent/mutate-P2.py` reports **36 mutations, 0 survived, 0 failed to apply** on
a scratch tree it verified byte-identical to the repo first. Full detail is the
P2 section at the end of `status.md`.

Nothing here has made a paid API call. The first live run is Jordan's, and it
needs a key.

## What landed

**A taxonomy, in two files.** `app/sources/taxonomy.py` is twelve search-term
groups across seven categories, each carrying the written buyer rationale the
review queue renders and `record_prospect()` refuses a record without.
`app/sources/exclusions.py` is the never-prospect list — auction houses,
estate-sale companies, estate liquidators, appraisers, consignment galleries,
"we buy houses" — matched on business name from **one shared definition**,
enforced in `record_prospect()` so every future source inherits it.

**`decisions/009` is implemented as written.** Shell wholesalers, importers and
distributors are buyers and they are `priority=1`. Radius is a property of the
group with the category as fallback: two national seashell groups, one regional
one, inside one category. The two national groups *state* their radius rather
than inheriting it, because the fallback reads `.env`.

**A source that never touches the database and never decides who is textable.**
`GooglePlacesSource.fetch()` pages one planned search and yields records; the
base persists. It does decide geography, because that is what a search is — a
result outside the group's radius is dropped before it can cost $0.0025 to
screen.

**Two spend meters, independent, because they are two bills.** The carrier's is
P1b's, called through. Google's is new: `GOOGLE_PLACES_MONTHLY_REQUEST_CAP`,
checked before every request, refusing cleanly mid-run with everything already
found kept and one ERROR line naming the count and the remedy.

**`scrape_runner.run_plan()` runs one job per search.** That is the design, not a
convenience: `scrape_jobs.search_term` becomes the ledger that stops a re-run
paying $0.035 for sixty businesses we already hold, the job row carries what
*that search* cost, and the monthly meter is exact across runs because each job
re-reads it from the table.

## Five things worth knowing before you touch any of it

1. **A Places request is charged for asking.** The cache, the rejection list and
   the already-a-contact check all stop a second $0.0025 and **none of them
   stops a second $0.035.** That is why there is a search ledger at all, and why
   it keys on a completed, *uninterrupted* job.
2. **`prospect_service.py` split.** Ingestion — `record_prospect()`,
   `_add_sighting()`, `is_suppressed()`, `RECORD_OUTCOMES` — is now
   `app/services/prospect_ingest.py`. The 500-line rule forced it and P1's own
   status note predicted it. If you are looking for where a source's records
   become rows, it is there.
3. **`RECORD_OUTCOMES` has six members**, and `ProspectSource.ingest()` refuses
   one it cannot count. `excluded` and `known` are separate counters on purpose:
   a term that returns competitors should be retired, and a term that returns
   businesses the client already has is working correctly on a covered niche.
   Adding a seventh means adding a counter and a column in the same commit.
4. **`RequestBudget.allows()` is one expression on purpose.** It shipped with an
   `if self.cap <= 0: return False` above it and the mutation reverting that
   guard *survived* — with an integer counter and a non-negative spend, no
   arrangement makes it decide anything. `self.spent < self.cap` is the line
   that holds the ceiling, and `<` rather than `<=` is the whole rule.
5. **Nothing calls `run_plan()` from a route or the scheduler.** Its two queries
   are 6 ms and 3 ms at a year of production row counts, and both would be on
   the event loop the moment somebody wires a button to them. 5i's incident, one
   module over, and the fix then is a worker rather than an index.

## Not done, and deliberately

- **Any real API call**, Google or carrier. Fixtures only; `accept-P2.sh` check
  2b asserts the suite structurally cannot make one. The fixture file says
  plainly that it was written to the documented schema rather than recorded.
- **A scheduler, a route or a button for discovery.** The first run is a human's.
- **Seeding `marine` and `seashells` as categories.** They have no rows, so
  their prospects are uncategorised and the *scorer* measures them against the
  150-mile default even though the *search* was national. Bounded at 15 of 100
  points, contrary to `decisions/009`'s intent, and the fix is a product
  decision about the palette. First item in `status.md`'s "Found while working".
- **Repairing `mutate-5e.py` and `mutate-5h.py`.** Six of their patches no
  longer apply because 5i and 5j moved the code they name. Both now report it
  and exit 1 rather than reading as a pass. That is another module's work.
  `mutate-P1.py`'s one rotted anchor *was* repaired — that file was already open
  — and it now surfaces a real survivor, `R12b`, described in `status.md`.

## Verified this session

- `bash agent/gate.sh` — green twice, 696 tests.
- `bash agent/accept-P2.sh` — ACCEPT PASS, criteria 1-9 and 6b.
- `agent/mutate-P2.py` — 36 mutations, 0 survived, `SCRATCH VERIFIED PRISTINE`.
- Every `agent/mutate-*.py` refuses a dirty scratch tree naming the files, and
  `mutate-5g.py` runs green on a clean one.
- `./run.sh` — `/login`, `/static/app.css`, `/`, `/prospects`, `/contacts`,
  `/campaigns`, `/settings`, `/health` all 200. `/prospects` and the four
  prospect API routes carry no carrier name, no raw payload, no API endpoint and
  no cost of ours, scanned with the parsed-number checker.
- Driven with real P2 data over HTTP: `/api/prospects` returns the search term
  and its buyer rationale on every row, and a shell importer at the top of the
  queue.

## Part B — Jordan's

1. **A Google Places API key with Enterprise tier**, and a billing budget alert
   set below `GOOGLE_PLACES_MONTHLY_REQUEST_CAP` as a second net. Without that
   tier the response parses cleanly and carries no phone at all — the log says
   so rather than reporting an empty niche, but the run is wasted.
2. Add `GOOGLE_PLACES_API_KEY` and set `PROSPECT_LOOKUP_PROVIDER=telnyx` in
   `/home/appuser/app/.env`, then restart. Until both are set, a scrape produces
   prospects whose line type is `unknown`, and `unknown` is not promote-eligible
   by design.
3. **Check `PROSPECT_CATEGORY_RADIUS_MILES` in the live `.env`.** If it is
   pinned there as the old five-key map, add `"marine":null` and
   `"seashells":null`. The taxonomy's national groups state their own radius so
   they are safe either way, but the scorer reads that map.
4. **First live run: one category, smallest radius, cap set low.** Read the
   review queue before scaling. The question is not "did it find businesses" but
   "would these people bid?" Memorabilia and seashells are the first sweep —
   the volume and the conversion — and which sweep runs first is yours.
5. **Watch the carrier balance.** Lookups and sends draw on the same pot, so a
   large scrape can fail the next morning's campaign pre-flight.

---

# Session B1 — Stripe: settle August, then auto-bill usage (2026-09-07)

## Where this leaves things

One checkout that does two things: it charges the balance outstanding from
before billing was automated, and it attaches the card every month afterwards is
billed against. After that the client is never invoiced by hand again.

**Nothing bills anybody yet.** There are no Stripe keys on this machine or in the
repo's `.env`, so `/subscribe` renders "card payments are not switched on for
this account yet", `POST /api/billing/checkout` answers 503, and the meter
records nothing. That is a supported state, not a broken one, and it is the state
the box will be in until Part B is done.

`bash agent/gate.sh` green twice, 764 tests. `bash agent/accept-B1.sh` exits 0 on
criteria 1-11. `agent/mutate-B1.py`: 47 mutations, 0 survived, identical on two
consecutive invocations.

**Nine of those forty-seven exist because of the review, not because of the
build.** One synchronous fresh-context pass found twelve defects in a tree where
the gate was green twice and the first thirty-eight mutations were all caught.
That is the clearest evidence this project has produced for running both: a
mutation harness proves a rule cannot be reverted, and only a reader notices a
rule that was written slightly wrong in the first place. The sharpest was two
*similar* arithmetics for the same question — how many segments is a row with no
stored count — with a docstring between them asserting they agreed. They
disagreed by a factor of three.

## What landed

- **`app/services/stripe_billing.py`** — the one object in the codebase that
  touches the Stripe SDK (`StripeAPI`), the Checkout Session, `session_is_ours()`,
  webhook verification, and the stored cycle anchor.
- **`app/services/stripe_meter.py`** — what Stripe is *told*: `report_segments()`,
  `backfill_unreported()`, and `period_usage()`, which the back-bill tool and
  `/usage` both price from.
- **`app/services/stripe_tiers.py`** — whether Stripe is *configured* to price
  it the way we are: A2's drift check, its five states, and the wording
  `/health` renders.
- **`app/routers/billing.py`** — `/subscribe`, `/billing/success`,
  `/api/billing/{status,checkout,tier-check}`, `POST /webhooks/stripe`.
- **`tools/bill_period.py`** — the back-bill tool. Its August dry run prints
  $270.03, from `billing_service`'s own functions.
- **`/health` gained `pricing_ok`, `pricing_state`, `pricing_issues`,
  `pricing_checked_at`.** Point the uptime monitor at `pricing_ok` as well as
  `sending_ok`.

## Six things worth knowing before you touch any of it

1. **The meter gets the RAW segment count.** `billable_segments()` and the Stripe
   tier both subtract 10,000; sending the first to the second subtracts it twice
   and a 15,000-segment month invoices $0 while `/usage` still says $75 due. This
   is the single most valuable line in the session and it has three tests and two
   mutations on it.
2. **Three of the spec's clauses named fields the pinned SDK does not have** —
   `subscription_data.add_invoice_items`, a tier's `unit_amount` for a sub-cent
   rate, and `subscription.current_period_start`. `decisions/010` records all
   three with the evidence. If you change any of that code, read
   `tests/test_stripe_contract.py` first: it asserts the shapes against the
   installed package, so a bad pin fails at `pip install` rather than on the
   morning the client tries to pay.
3. **A campaign's metered figure is written once and can never be corrected.**
   `decisions/011`, open, an escalation on billing item 1, and the one thing on
   this list that costs real money. `identifier=campaign_<id>` is what makes a
   retry free and it is the same property that discards a revision.
   - We **over-bill** when a campaign has delivery failures: the meter fires
     when every row is `sent`, the webhook later writes `undelivered`, and the
     correction is discarded. Measured: 12,000 metered, `/usage` says 8,000.
   - We **under-bill** every top-up: `campaign_topup.top_up_background` is the
     third path that writes `sent` rows and nothing meters it.
   Neither bites until the client subscribes. Do not "just add the hook" — the
   identifier is spent. The recommendation is to meter once, on a schedule,
   after delivery receipts have settled.
4. **`backfill_unreported()` is bounded by the subscription start**, and that
   bound is load-bearing. The identifier makes a *repeat* free; it does nothing
   about *history*, and August's segments are already settled by the one-time
   price. With no subscription stored it replays nothing and says so.
5. **`billing_service.get_billing_cycle()` and `cycle_day()` take an optional
   `db`.** With none they open a short-lived session to read the stored anchor
   and fall back to `BILLING_CYCLE_DAY` on anything going wrong. Three callers
   outside the module pass nothing (`report_service`, `preflight_totals`,
   `usage.py`) and that costs one indexed single-row read each.
6. **`/health` never calls Stripe, and never names a figure.** The tier check
   runs daily on the scheduler and on demand, and stores a row; `/health` reads
   the row without `Depends(get_db)`, for the reason `active_config_alerts()`
   does. `test_health_makes_no_stripe_call` asserts the call count is zero.
   The endpoint has no login and no rate limit, so `pricing_issues` says which
   *state* and never what this account's allowance or rate is — the figures go
   to the log and to the two authenticated billing routes. And an agreement
   nobody has re-checked for a week reports as `stale`, not `agree`: a verdict
   is a claim about a price at a moment.

## Not done, and deliberately

- **A3's live verification against a real Checkout Session.** It is
  `bash agent/accept-B1.sh --with-stripe`, it refuses anything but an `sk_test_`
  key, and it cannot run until Part B produces one. The offline half — reading
  the SDK's own declared parameters — ran on every invocation and is what settled
  the mechanism.
- **A scheduled tier check.** A2 says on demand. It runs from
  `POST /api/billing/tier-check` and once automatically after a completed
  checkout.
- **Refunds, proration, plan changes, dunning, the customer portal beyond
  enabling it.** Out of scope by the spec.
- **The deploy.** Unchanged from every session since 5c: the box is behind.

## Verified this session

- `bash agent/accept-B1.sh` — criteria 1-11 pass; 12 skipped for want of keys.
- The back-bill tool's window warning, both ways: silent on a whole cycle,
  loud on half of one, naming the cycle it should have been given.
- The suite run with `socket.connect`, `connect_ex` and `create_connection`
  raising: 85 passed across the four billing modules. The guard's own ability to
  fire is proved separately against `api.stripe.com`.
- The drift check printed against three prices — one correct, one with a
  5,000-segment first tier, one at 2 cents — with `/health` answering 200 and
  `sending_ok: true` on all three.
- `tools/bill_period.py --start 2026-08-01 --end 2026-08-31` against a database
  seeded with 28,002 August segments plus 15,000 that must not count (a failed
  send, a held-back row, a September row): **$270.03**.
- `--charge` without `--create` refused.

## Part B — Jordan's, in the Stripe dashboard

Unchanged from `sessions/session-B1.md` except item 3, which is now settled:

1. **Billing → Meters → Create meter.** Event name `sms_segments`, aggregation
   **Sum**, customer mapping `stripe_customer_id`, value key `value`. Those two
   payload keys are asserted in
   `test_the_meter_event_payload_uses_the_meters_own_field_names` — a payload
   spelling either differently is accepted by the API and aggregates to nothing,
   and a meter that reads zero looks exactly like a quiet month.
2. **Product → one price: usage-based, graduated tiers, monthly, linked to the
   meter** — up to 10,000 → $0, thereafter → $0.015 per unit. No flat price.
3. **A one-time price of $270.03 is required**, not optional.
   `subscription_data.add_invoice_items` does not exist on a Checkout Session in
   this API version, so the balance rides as a second line item. Put its id in
   `STRIPE_PRICE_BALANCE`.
4. **Developers → Webhooks** → `{PUBLIC_BASE_URL}/webhooks/stripe`, event
   `checkout.session.completed`. Copy the `whsec_…`. **Until it is set, every
   webhook payload is ignored** — deliberately: what a trusted payload writes is
   the customer id this client's segments are billed to.
5. **Settings → Billing → Customer portal → enable.**
6. Put `STRIPE_SECRET_KEY`, `STRIPE_PRICE_METERED`, `STRIPE_PRICE_BALANCE`,
   `STRIPE_METER_EVENT_NAME`, `STRIPE_WEBHOOK_SECRET` and `PUBLIC_BASE_URL` in
   `/home/appuser/app/.env` and restart.
7. **Then run the tier check once** — `POST /api/billing/tier-check`, or just
   complete the checkout, which runs it — and confirm `/health` reports
   `pricing_ok: true`. Until it has run, a configured box reports
   `pricing_state: "never_checked"` and `pricing_ok: false`, on purpose.
8. **Then, once, with the test-mode key:**
   `STRIPE_SECRET_KEY=sk_test_… STRIPE_PRICE_METERED=price_… bash agent/accept-B1.sh --with-stripe`

---

# Session B1b — meter once, late, and correctly (2026-09-08)

## Where this leaves things

The usage meter is no longer called from the send path. An hourly pass reads
`sms_messages`, reports every billable row that has settled and has not been
reported, stamps `metered_at` on exactly those rows, and stops. That closes the
three defects `decisions/011` measured — the over-bill on delivery failures,
the unbilled top-up, and the double-billed backfill — and it makes the property
statable: **every billable segment reaches the meter exactly once, and the
figure metered is the figure `compute_usage()` reports for the same window.**

**Still nothing bills anybody.** No keys, no customer, and the pass returns
"no subscription yet" every hour. Part B is now unblocked.

`bash agent/gate.sh` green twice, **788 tests**. `bash agent/accept-B1b.sh`
exits 0 with every criterion printed. `agent/mutate-B1b.py`: 35 mutations, 0
survived, identical on two consecutive invocations — eight of them (`R1`-`R8`)
from the review, which found two money-moving defects in a tree at 26/0.
`agent/accept-B1.sh` still passes; its harness runs 34/0 after the thirteen
mutations that moved to B1b were retired with their successors named.

**One escalation is open and it is not blocking:** `decisions/012` — how usage
the meter *refused* (older than 35 days) is invoiced by hand and recorded.
The tool the spec named for it would have billed a partly-metered window
twice; it now refuses that window. Nothing is refused until the pass has been
unable to run for 35 days.

## What landed

- **`alembic/versions/d7e2a91c4f36_sms_messages_metered_at.py`** — one
  nullable column, added in place. NULL means never metered, and for every
  existing row that is the truth.
- **`app/models/sms_message.py`** — `metered_at`, and `SETTLED_STATUSES`
  beside `BILLABLE_STATUSES`, read through the module and never restated.
- **`app/core/config.py`** — `BILLING_SETTLE_HOURS = 24`, with the direction
  to err written next to it.
- **`app/services/stripe_meter.py`** — rewritten: the pass
  (`meter_settled_rows`), the batch, the mark, the job
  (`metering_pass_job`), and `backfill_unreported()` as the pass run by hand.
- **`app/services/stripe_reconcile.py`** — new: `period_usage()` (moved) and
  `unmetered_breakdown()`, the reconciliation view.
- **`app/services/campaign_dispatch.py`** — `report_usage()` gone, and a
  docstring saying why it must stay gone. `campaign_topup.py`: one sentence.
- **`app/main.py`** — the hourly job, sync, under `usage_metering`.
- **`tools/bill_period.py --unmetered`** — which rows in a window have not
  reached the meter, grouped by reason, plus the residue running the other way.
- **`tests/test_metering_pass.py`** — 32 tests; the B1 meter tests moved here.

## Six things worth knowing before you touch any of it

1. **The ledger is ours. Stripe's identifier is not.** It dedupes for a
   rolling 24 hours and no longer — the pinned SDK says so and a test reads
   it every run. `metered_at` is what stops a double report; the identifier
   only absorbs the same-minute retry after a lost commit.
2. **Stage, report, mark-and-clear, per batch.** The batch is written to
   `app_settings` before the Stripe call and cleared in the same commit as
   the mark. A Stripe failure marks nothing and stops the pass; a mark that
   fails after Stripe accepted leaves the batch staged, and the next pass
   re-offers the *staged* identifier — never one recomputed from rows a
   webhook may have flipped since. A batch staged longer than 24 hours is
   refused until `stripe_meter.resolve_pending_batch(db, recorded=…)` is run
   from Stripe's event summary. Mutations `S3`, `S4`, `S9b`, `B7`, `R2`-`R5`,
   `R8`.
3. **A marked row stays metered whatever happens to it afterwards.** That is
   the one residue this session leaves, it can only hit a row that was
   settled, and `--unmetered` shows it. Do not "fix" it by unmarking: Stripe
   cannot take a negative event and the row would be re-reported.
4. **`delivered` settles at once; `sent` waits 24 hours.** Lengthen the window
   before shortening it. Metering before a late receipt is the over-bill in
   our favour, which 011 calls the serious direction.
5. **The event carries the send time.** A pass that runs after the cycle
   boundary still lands usage in the right cycle, and a campaign that straddles
   midnight is split into two batches because the cycle here is a date. Usage
   older than 35 days cannot reach the meter and is refused at ERROR every
   pass. **Do not `--create` an invoice over a window the meter has touched**:
   the tool refuses it, because that would bill the metered half twice.
   `decisions/012` is how the refused half gets invoiced.
6. **There is exactly one place a segment is metered, and a test counts it.**
   `create_meter_event` has one call site outside the adapter, and the three
   modules that write `sent` rows import neither Stripe module — asserted by
   AST walk, because B1's hook was an import inside a function body.

## Part B — Jordan's, in the Stripe dashboard

B1's list stands. Two things to look at on the first live dry run that the SDK
could not settle offline:

- rows sent on the anchor day *before* the checkout moment are timestamped
  before the subscription's first period — see what the meter does with them;
- usage from a cycle's last day is metered up to ~25 hours after the cycle
  closes, backdated; check the account's invoice finalisation delay, or accept
  that those segments bill on the next invoice.

**Added by session 5m — read `decisions/013` before doing any of the above.**
5m put the application in the client's timezone, which changes what
`stripe_billing._local_date()` computes: a subscription created at 02:00 UTC on
the 1st anchors to day **1** on a UTC box and day **31** on an Eastern one. The
anchor is derived once, at checkout, and nothing is anchored yet — so this is a
question to answer *before* item 2 above, not a correction to make afterwards.
It is the one piece of 5m's work a later session can get wrong for free.

---

# Session 5m — the composer's audience panel, and time in Eastern

_2026-09-08. Two live defects, both found by the client's own operator._

## What just happened

**A1.** The composer's summary panel described three audiences at once —
`⭐ ALL BIDDERS`, 10,146 recipients, and 443 segments — with a 443-contact list
selected. Reproduced exactly, character for character, before anything was
changed, against a database at the production shape; the write-up is in
`status.md` under "A1 — what actually happened, reproduced". Three mechanisms,
none of them the obvious one:

1. assigning `select.value` fires no `change` event, so the Audience row was
   never repainted after the upload flow moved the dropdown;
2. nothing sequenced the `/preview` replies, and the reply about the audience he
   had left behind is **twelve times slower** at this client's shape (237.9 ms
   against 20.3 ms, measured), so it landed last;
3. `runPreflight()` wrote two of the panel's six rows.

**A send would have gone to 443, not 10,146** — established on both sides of the
wire and written up. The panel is a description; the submit handler reads the
dropdown, and `create_campaign()` resolves the selector itself.

**A2.** The application had no timezone and the droplet runs UTC, so a campaign
scheduled for 6:00 PM Eastern was dispatched at 2:00 PM. Every naive timestamp
now means wall clock in `APP_TIMEZONE` (`America/New_York`), `clock.now()` asks
`zoneinfo`, and the process sets itself to the client's zone at import so the
sixty ambient `datetime.now()` writers agree without sixty edits.

## State of the code

`bash agent/gate.sh` green twice. **835 tests**, up from 788 at B1b.
`bash agent/accept-5m.sh` exits 0 on checks 0-10. `agent/mutate-5m.py` reports
**18 caught / 0 survived** on two consecutive invocations, both on a
verified-pristine tree.

## Read this before touching either area

1. **The panel has one writer and one gate per response.** `paintSummary()` in
   `_composer-summary.html` writes all six rows; `refreshPreview()` and
   `runPreflight()` each take a ticket from `panelSequence` and return early if a
   newer one exists. `paintSummary()` deliberately does not re-check the ticket —
   with both callers gating it would be a guard no arrangement can reach, and
   this project has shipped one of those already.
2. **`node` is now a dependency of the test suite.** The panel is JavaScript and
   `paintAudienceSummary()` *was* correctly wired, so no shape test over the
   template could have found the defect. `tests/js/` runs the real partials in a
   `vm`; `tests/test_composer_panel.py` feeds them response bodies from the real
   endpoints in the same process. Absent node the tests **fail**, they do not
   skip.
3. **`scheduled_at` is wall clock, not an instant, and that is a ruling.**
   `app/core/clock.py` carries the three reasons. Do not "fix" it by converting
   to UTC: the values in the database were typed as Eastern wall clock, and
   converting them would move the 6:00 PM campaign to 10:00 PM. Migration
   `b7d43f0c9a15` is the adjudication and it converts only offset-bearing values,
   which nothing in this application writes.
4. **The repeated hour is safe because of the draft filter.** 1:30 AM happens
   twice on 1 November; the campaign is dispatched on the first and is no longer
   a draft on the second. `due_campaign_ids()` filters `status == "draft"` and
   that filter is now load-bearing for a second reason. Do not weaken it.
5. **Times render from one formatter, and it never builds a `Date` from a stored
   value.** `fmtDate`/`fmtDay`/`fmtClock` in `base.html`, `clock.clock_time()` on
   the server. A sweep fails the suite if any template constructs a `Date` from
   a value again.
6. **The panel says nothing while it waits, and that is deliberate.** Picking an
   audience clears every figure to `…` until an answer about *that* audience
   arrives. A self-consistent stale panel is harder to notice than the
   contradictory one this session started with, and Create reads the dropdown.
7. **`decisions/013` is open and it is about money.** Putting the process in the
   client's zone moves which billing cycle an evening send lands in, and changes
   the cycle anchor that will be derived from Stripe's subscription. Measured; no
   billing file was edited. **Answer it before B1 Part B runs** — the anchor is
   computed once, and nothing is metered or anchored yet.

## For the deploy

- The droplet needs **no `.env` edit**: `APP_TIMEZONE` defaults to the client's
  zone. `.env` and `.env.production` were not touched, per the spec.
- The box's own `TZ` no longer matters to this application. It still matters to
  cron, to `scripts/backup.sh` and to the systemd journal's timestamps.
- `alembic upgrade head` prints the adjudication of `campaigns.scheduled_at`:
  how many rows were left as wall clock, how many converted, how many it could
  not read and their ids. **That line is the record** — the development database
  has no scheduled campaign at all, so the deploy is where this is answered.
  Run `scripts/backup.sh` first, as with any migration that touches data.
- There is at least one pending scheduled draft on the box
  (`09/09, 6:00 PM Private Record Collection`). After this deploy it goes out at
  6:00 PM Eastern. Before it, it would have gone out at 2:00 PM.

---

# Session 5n — upload-mode template metrics, and cancelling a scheduled campaign

_2026-09-20. Sections append; this is the bottom and the current state._

## What just happened

Two operator-reported defects, both fixed, both tested by running the code the
client runs.

**A1.** The message row under the upload tab — the primary flow — read
`Characters 0 · Encoding — · Segments/msg 0` under a full message, because
`refreshPreview()` returned outright in upload mode and the emoji warning was
silent with it. The request now asks `/preview` about **no audience**; the
endpoint (moved to `app/routers/campaign_preview.py`) answers the message half
exactly as before and the audience half as `null`, which the panel paints as
`—`. Not 0: "0 recipients" above a list he is about to upload is a claim.

**A2.** There was no way to cancel a scheduled campaign short of SQL.
`campaign_dispatch.cancel_scheduled()` clears `scheduled_at` and leaves an
editable draft; refuses, with a sentence naming the state, on anything that
has started; and the rail draws Cancel on a scheduled draft only. The race —
selected by the tick, cancelled, dispatched anyway — was measured on the
pre-fix tree (`completed`, one row `sent`) and is closed three ways:
`still_scheduled()` re-asking immediately before each dispatch, a conditional
clear on the cancel side so a cancel that loses says so, and — after the
review measured the remaining gap open — the flip to `running` itself being a
conditional claim (`campaign_claim.take()`).

## State of the code

`bash agent/gate.sh` green twice. **858 tests**, up from 835 at 5m.
`bash agent/accept-5n.sh` exits 0 on checks 0-10. `agent/mutate-5n.py`
reports **15 caught / 0 survived** on two consecutive invocations, both on a
verified-pristine tree. `agent/mutate-5m.py` still 18 / 0 after three of its
anchors moved with `/preview`.

## Read this before touching either area

1. **`null` and `0` are different answers from `/preview`, and the panel
   renders them differently.** `UNKNOWN_AUDIENCE` in `campaign_preview.py` is
   what "no audience" returns; `paintSummary()`'s `figure()` maps null to `—`,
   pending to `…`, and a number to itself. Do not "tidy" `(value || 0)` back in.
2. **The upload tab sends `audience: null` on purpose.** The dropdown still
   holds the other tab's selection; sending it would count that audience under
   a composer whose audience is a file. Mutation `U5`.
3. **The tab switch schedules a preview in both modes.** `resetSummary()`
   retires every reply in flight, including the one about the message as it
   stands; without the re-ask the counter describes the previous keystroke.
4. **Cancelling leaves a draft, and that is a ruling with a stated
   consequence** — see `cancel_scheduled()`'s docstring. It cannot be given a
   new time from the rail; "wrong day" is cancel, then create again.
5. **The flip to `running` is a claim, and that is what closes the race.**
   `campaign_claim.take()` is a conditional UPDATE keyed on the row still
   being the draft the caller loaded, with the same `scheduled_at`; it raises
   `SendClaimLost` before anything is sent when it matches nothing.
   `still_scheduled()` before dispatch is the cheap, logged common case; the
   claim is the guarantee, and it holds whatever thread, loop or worker the
   cancel arrives on. The review measured the gap open in three of four
   provider/handler arrangements before it existed — including a `def` cancel
   route with the deployed provider. Do not make the flip unconditional
   again to "simplify"; mutation `C7` is there to notice.
6. **`run_due_campaigns()` returns what it dispatched, not what it selected.**
   It was the same list until a campaign could be cancelled after selection.

## For the deploy

- No migration this session. No `.env` edit.
- `app/services/campaign_claim.py` is new and on every first send: the flip
  to `running` is now a conditional UPDATE. A lost claim logs
  "stood down" at INFO from the scheduler and "background send failed" from
  the button path; neither sends.
- `app/routers/campaign_preview.py` is new and registered in `app/main.py`;
  `POST /api/campaigns/preview` is unchanged in path and, with an audience, in
  shape. Without one it now returns nulls where it never used to be asked.
- `POST /api/campaigns/{id}/cancel` is new: 200 with the campaign, 409 with a
  sentence, 404 for an unknown id. No rate limit — it spends nothing.
- The pending scheduled draft on the box (`09/09, 6:00 PM …`) has passed its
  time by now; whatever state it is in, the rail will say, and Cancel will
  refuse it with that state's sentence if it is no longer a draft.
