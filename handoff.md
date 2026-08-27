# Handoff

_Last updated: 2026-08-26 (session 5g). Sections append; the bottom is current._

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
