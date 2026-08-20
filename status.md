# Status

_Last updated: 2026-08-19_

## Where we are
Module 3a complete — the dark application shell is in, every existing page still
returns 200 on top of it, and 3b and 4 can now start in parallel against a settled
`base.html`. The child templates are still on the skeleton's light palette and look
wrong on the dark page; that is 3b, 4 and module 8's work, not a regression.

Module 2 before it. Industry category is now a real concept the whole app understands:
two tables, the five seeded categories, a selector grammar that unions and intersects,
CRUD that will not let anyone silently discard tagging history, and an import flow that
previews before it commits and can be undone without destroying anything a human added.
Backend only — no screens; module 3 owns those.

`bash agent/gate.sh` passes; 76 tests (46 baseline + 30 new), green twice in a row.

## Current module
None in progress. Modules 1, 1b, 2, 5a and 3a are done. Next up: **3b (Today +
Contacts) and 4 (Composer & guardrails)**, which can run in parallel now that the
shell has landed.

_(The three merge conflicts left in this file by the module-2 merge are resolved as
of session 3a. Both sides were additions and both were true, so both were kept.)_

## Done
- Reviewed skeleton + prior client's production system
- Scope settled: category segmentation, buyer acquisition, dark UI
- Commercials settled: no monthly fee, 10,000 included segments, $0.015/segment after
- UI designed and approved
- **Module 1 — Foundation, pricing & white-label**
  - Test isolation: `tests/conftest.py` points the suite at a per-run scratch SQLite
    file and creates the schema itself. The suite was green exactly once per database
    before this; it is now green twice in a row, and any single test file runs alone.
  - `requirements-dev.txt` pins pytest; README documents the venv workflow
  - Alembic wired to `settings.DATABASE_URL` and `Base.metadata`, with an initial
    migration capturing all seven tables. `alembic check` reports no drift.
  - Tailwind compiled to `app/static/app.css`; Inter self-hosted (4 weights, 96 KB);
    no runtime CDN anywhere; `/static` mounted; build wired into `deployment/deploy.sh`
  - CSS custom-property tokens, dark by default, light under `[data-theme="light"]`;
    brand hex from `.env` through one included partial
  - Billing switched to fee + included allowance + flat rate; tier table removed;
    rounding half-up at display; `tests/test_billing.py` covers the spec's numbers
  - White-label sweep, including three runtime-assembled leaks a grep could not find
- **Session 1b — module 1 review fixes**
  - The client no longer sees our wholesale rate. `PREFLIGHT_COST_PER_SEGMENT` is now
    `WHOLESALE_COST_PER_SEGMENT`, the campaign cost estimate no longer crosses the API
    boundary, and the three UI strings that quoted it are worded in segments.
  - Billing arithmetic is `Decimal` end to end. `cost_for_segments()` returned a float,
    so every odd billable count landed just under its half-cent and 11,782 of the first
    50,000 odd counts invoiced a cent low. The model, rate, allowance and status set are
    unchanged — this was an arithmetic defect, not a commercial one.
  - Alembic owns the schema. `Base.metadata.create_all()` is gone from `app/main.py`,
    `tests/conftest.py` builds the scratch database with `alembic upgrade head`, and
    `tests/test_migrations.py` fails if a migration drifts from the models. Module 2's
    `categories` migration is a tested artifact before it is written.
  - `tests/test_whitelabel.py` exercises the app and scans what comes back. It
    discovers routes from the app, so a new client-facing GET route is scanned the day
    it lands, and an unresolvable path parameter fails rather than being skipped.

- **Module 5a — Deploy scaffolding**
  - `deployment/bootstrap.sh` brings up a fresh Ubuntu box: packages, non-root service
    user, venv, runtime directories, nginx site and systemd unit rendered from the
    existing templates, nightly backup cron, ufw. It stops short of writing `.env`,
    running migrations and issuing the certificate, and prints those as manual steps —
    `.env` carries the sending credentials and the provider switch stays a human's.
  - `deployment/deploy.sh` gained `--dry-run`. Existing behaviour is unchanged; the dry
    run needs no `SERVER`/`SERVICE` and contacts nothing, so the rsync excludes can be
    reviewed before they are pointed at a live database.
  - `scripts/backup.sh` uses SQLite's online backup API rather than `cp`, so an archive
    taken mid-campaign is consistent. gzip, count-based retention, optional off-box scp
    via `BACKUP_REMOTE`. `--verify` restores the archive and runs `PRAGMA
    integrity_check`; cron runs `--verify` nightly, because an unverified backup is a
    rumour.
  - The verify step also asserts the restored database has tables. This is not belt and
    braces: a zero-byte archive returns `integrity_check: ok`, demonstrated during this
    session. The table count is the assertion that actually catches an empty backup.
  - `docs/CLIENT_GUIDE.md` written for A4A: the six screens that exist today, segments
    and the emoji cost multiplier, the commercial terms, opt-out behaviour, and how to
    read a failed send. No carrier name.
  - README has a deployment section and a written rollback — how to stop a campaign in
    flight and how to restore yesterday's database.
  - `backups/` added to `.gitignore`. Without it the first cron run leaves client data
    in `git status`.
- **Module 2 — Categories & segmented upload**
  - `categories` and `contact_categories`, seeded with the five in one migration.
    The seed is idempotent and `tests/test_categories.py` calls the migration's own
    `_seed_categories()` a second time to prove it rather than reimplementing it.
    `alembic check` reports no drift; the migration round-trips down and back up.
  - `resolve_audience()` understands `category:<slug>`, comma-union and one `&`
    intersection, with `,` binding tighter. Unknown slugs raise and name themselves —
    a typo must never resolve to an empty audience. Each term contributes an IN
    sub-select rather than a join, so a contact in two categories resolves once.
  - `audience_label()` renders "Food Service + Equipment & Machinery ∩ Aug 22 preview"
    and never raises; `list_summaries()` now offers every active category with a live
    count, which is what module 3's dropdowns read from.
  - Category CRUD at `/api/categories`. `color_token` is validated against the five
    tokens on every write, and a hard delete is refused (409) while anyone is tagged —
    the FK cascades, so it would drop the tagging history without a word.
  - `/api/imports/{preview,commit,undo}`. `category_id` is required at every step.
    Preview writes nothing; commit reports the same numbers as actuals and skips
    opted-out numbers outright; undo removes only what that batch added.
  - Two decisions worth knowing about, both recorded below under "Decisions taken
    inside module 2".
- **Module 3a — UI shell**
  - `app/templates/base.html` is the dark application shell from the design file: a
    216px sidebar, brand block, grouped nav with section captions, and a footer pinned
    to the bottom by a flex spacer carrying segments-this-month and the masked sender
    number. Top bar is heading, send-mode pill, and a slot for the page's own buttons.
  - The nav ships six items, not the design's eight. History and Categories (module 8)
    and Prospects (engine deferred) are absent rather than 404ing, and the template says
    so and says what restores them.
  - Block contract for 3b and 4, documented at the top of the file: `title` (top-bar
    heading), `page_actions`, `content`, `head`, `scripts`. `title` changed meaning —
    it used to be the document `<title>` — so each of the six children had its one-line
    declaration updated to a bare page name. Nothing else in them was touched.
  - `pages.py` gained `shell_context(db)`: one place computing the footer figures and
    the send-mode pill, rather than the same query in six handlers.
  - Below 768px the sidebar leaves the flow and becomes a drawer behind a labelled
    toggle. Verified at 375px: no horizontal scroll open or closed.
- **Module 4 — Composer & campaign guardrails** _(added by session 4; 3b is editing this
  file in parallel, so this is an append)_
  - `campaigns.category_id` exists, nullable in the schema and required by the API. The
    one escape is `cross_category_override`, which the caller has to type and which is
    recorded on the campaign. Old campaigns keep NULL and the UI shows "—"; nothing was
    backfilled, because a guessed category is indistinguishable from one a human chose.
  - `campaigns.html` is the three-step composer from `pen-exports/BWsLw.png`: category
    as a segmented control, message with live metering, pre-flight checklist, with the
    phone preview and the "this send" summary pinned right. The page heads itself
    "Compose" now, agreeing with the nav.
  - `POST /api/campaigns/preflight` returns seven checks as key / label / status /
    reason. The UI draws them and computes none of them, so a check cannot say one thing
    on screen and another over the API.
  - Recent-contact suppression, 3 days, `RECENT_CONTACT_SUPPRESSION_DAYS`. Across
    categories: a buyer in three of them is one person with one phone. Held-back
    contacts get a `skipped` row at create time, so the count is on screen before the
    send rather than inferable after it, and `skipped` is outside `BILLABLE_STATUSES`.
  - Scheduled send: `campaigns.scheduled_at`, dispatched by a one-minute APScheduler job
    registered in `main.py`'s lifespan. The job only finds what is due; it hands each
    campaign to the same `send_campaign()` the button reaches, so a scheduled blast gets
    the capacity pre-flight and every filter. `tests/test_campaign_guardrails.py` proves
    it by making pre-flight *refuse* — the scheduled campaign comes out `aborted` and
    the stub provider's `send()` asserts if it is ever reached.
  - The composer's money is the client's money. `estimated_cost` on `/preview` and
    `/preflight` is `BILLING_PRICE_PER_SEGMENT` net of the month's allowance, computed
    as the difference of two `billing_service.cost_for_segments()` Decimals so the
    half-cent boundary survives. Nothing in module 4 reads the wholesale rate except the
    capacity check, which needs it as a divisor and returns only `ok` and `detail`.
  - The capacity check itself is unchanged. Its arithmetic moved into
    `capacity_assessment()` so the endpoint can re-state the same verdict from the same
    numbers; the threshold, the branches and the wording are identical, and a test
    asserts the endpoint's row and `preflight()`'s string are the same string.

## Next
1. Module 3b — Today + Contacts screens
2. Module 5b — Go live (needs 3b and 4 merged)

## Blocked on
- A4A logo and brand hex colors. **No longer blocking:** the placeholder palette from
  the design file is in use and the real hex is a one-line `.env` change
  (`BRAND_COLOR_HEX` / `BRAND_ACCENT_HEX`) — verified, no template edit and no CSS
  rebuild required.
- Sample CSVs, one per category. **No longer blocking module 2:** the header mapping is
  covered by `tests/fixtures/contacts_messy.csv`, built to look like a real export
  (`Cell`, `Contact #`, `Company`, a repeated row, a number that is not a number).
  Still needed before the launch import in module 8, to confirm his actual headers
  match and the per-category counts come out right.
- Sender number strategy (needed before the first live send)

## Decisions taken inside module 2

Both are implementation choices, made rather than escalated, and both are visible in
the schema — flagging them so module 3 does not have to reverse-engineer them.

**The preview reports two counts the spec did not name.** The spec's six figures only
add up when nothing in the file is a repeat and every number we already hold is already
in the chosen category. `duplicates` (the same number twice in one file — the number is
fine, it just imports once) and `existing_contacts` (a number we hold but have not
tagged with *this* category — it gets the tag without being created) are what make the
report reconcile:

    rows = valid_phones + unusable + duplicates
    valid_phones = opted_out + already_in_category + existing_contacts + new_contacts

Every figure the spec named keeps the meaning the spec gave it. `opted_out` takes
precedence where a number is both blocklisted and already tagged, because "will be
skipped" is the fact that changes what happens next.

**Undo needed three columns to be subtractive rather than destructive.**
`contact_lists.category_id` records which category a batch tagged, and
`contact_list_members.created_contact` / `created_tag` record what that batch actually
did to each contact. Without them, undo has to parse the category back out of the list
*name* ("Food Service — 2026-08-19 upload"), which is the overloaded-string mistake the
reference system made with `auction_date` and which breaks the moment someone renames a
list. All three are nullable, additive, and dropped by the downgrade. Nothing touches
`ix_contacts_phone`.

## Found while working
Real issues outside the current module's scope. Left alone deliberately; each names the
module that owns the file.

- **`app/routers/usage.py:46` reads `WHOLESALE_COST_PER_SEGMENT`.** Session 4's acceptance
  criteria include `grep -rn "WHOLESALE_COST_PER_SEGMENT" app/routers/ app/templates/`
  returning nothing, and this one line is why it does not. It is not a leak: session 1b
  put it there deliberately as the divisor that turns our carrier balance into his segment
  capacity, and neither the rate nor the balance is in the response —
  `tests/test_whitelabel.py::test_no_response_quotes_our_wholesale_rate` proves that by
  running the route. But the literal grep is a structural rule the runtime test cannot
  replace, and satisfying it means moving the conversion behind a service helper.
  `usage.py` is module 8's file, so this is a note rather than a drive-by. Module 4's own
  routers and templates are clean.
- **`docs/API.md` does not document any of module 4.** `POST /api/campaigns` now requires
  `category_id` or `cross_category_override` and accepts `scheduled_at`;
  `POST /api/campaigns/preflight` is new; `/preview` returns cost, suppressed and
  opted-out counts. Docs are module 8's, and `docs/API.md:45` is already stale from
  session 1b.
- **The suite's shared rate-limit budget is now nearly spent.** `POST /api/campaigns` is
  5/minute per IP and the whole suite runs inside one window from one address:
  test_smoke makes 1 create, test_whitelabel 2, test_campaign_guardrails 4. The last of
  those resets the limiter around itself so it neither inherits nor leaves debt, but a
  future module that creates campaigns over HTTP and does not do the same will fail on a
  429 that looks like a bug in the code under test. The same shape as the 10/minute login
  cap already noted below.

- **`sessions/session-5a.md` does not exist.** Module 5a was built from its row in
  `modules.md` (scope, file list, "5a stays out of `app/main.py`") plus the acceptance
  criteria in the session prompt. Every other module has a session spec; if one was
  written for 5a it never landed in the repo. Worth knowing before 5b, which depends on
  5a and has the same risk.
- **`run.sh` still creates `venv/` while the project uses `.venv/`.** Unchanged from the
  module 1 note below. `deployment/bootstrap.sh` creates the server venv at
  `$APP_DIR/venv`, matching `app.service.template` and `deploy.sh`, so the server side is
  self-consistent; the local mismatch is untouched and still module 8's.
- **`sessions/session-2.md` was never committed.** It existed only as an untracked file
  in the primary worktree, so `git worktree add` produced a module-2 branch with no
  spec on it. Committed here alongside the module. Worth checking that session 3's
  spec is tracked before its worktree is cut.
- **The uncategorised import endpoints are still live.** `/api/contacts/import` and
  `/api/contacts/import/preview` are the skeleton's original flow and take no category,
  which is the one thing module 2 exists to make impossible. Left alone because
  `docs/API.md` documents them and module 8 owns the docs; module 3 should point the
  Contacts screen at `/api/imports/*` and retire them together with the doc entry.
- **`app/static/app.css` is gitignored, so a fresh worktree serves it as 404** until
  `npm run build:css` runs. Correct — it is a build artifact, and `deployment/deploy.sh`
  and the README both build it. But CLAUDE.md's "How to verify work" lists the 200 as
  though it always holds, which sends the next agent hunting a regression that is not
  there. One sentence in CLAUDE.md would fix it; that file is nobody's module.
  <br>_Fixed 2026-08-19: the repo-level `CLAUDE.md` now says so under "How to verify
  work". The copy one directory up still carries the old wording._
- **The six child templates are still on the skeleton's light palette.** They render as
  white cards on the dark shell — known, expected, and explicitly not session 3a's to
  fix. `dashboard.html` is the worst of it: its own `text-gray-900` page heading is
  near-invisible on `bg-page`. 3b owns `dashboard.html`/`today.html` and `contacts.html`,
  4 owns `campaigns.html`, module 8 owns `blocklist.html`, `usage.html`, `settings.html`.
- **Nav label and page heading disagree on two screens.** The nav says "Compose" and
  "Opt-outs" (the design's wording, and the spec's); the pages still head themselves
  "Campaigns" and "Blocklist". Session 3a changed only `dashboard.html`'s heading, to
  "Today", because the spec named that rename explicitly. The other two belong to the
  sessions that own those templates — one word each, in `{% block title %}`.
- **`status.md` carried three unresolved merge-conflict markers** from the module-2
  merge (lines 15, 57 and 152 as committed). Resolved in this session, since it is a
  file 3a is required to update and the markers made the "Current module" section
  unreadable. Both sides of all three were additions and both were kept.

- **`docs/API.md` and `docs/NEW_CLIENT_CHECKLIST.md` still document the old billing
  model** (`base_fee`, `overage_cost`, `BILLING_OVERAGE_TIERS`) and the removed
  `webhook_url` field on `/api/settings/system`. `docs/API.md:45` also still documents
  the campaign cost estimate as part of the campaign payload, which session 1b removed.
  Docs are module 8's territory.
- **`--on-brand` is a fixed value, not derived from the brand hex.** Set a dark
  `BRAND_COLOR_HEX` and the text on brand-colored buttons keeps its light-on-dark
  contrast by luck. Real, and worth doing — it needs A4A's actual brand colors, which
  we do not have yet. Deferred deliberately (session 1b, out of scope).
- **`app/routers/pages.py:53` caps POST /login at 10/minute per IP, and the suite logs
  in from one address inside one window.** Session 1b made `test_smoke.py`'s client
  fixture module-scoped (8 logins → 1) and `test_whitelabel.py` asserts its login
  succeeded, so a future 429 fails loudly instead of silently scanning 401 bodies. A
  fifth test module is still fine; a fifteenth would not be.
- **`app/routers/webhooks/common.py:86` writes carrier error text to
  `sms_messages.error_message` unscrubbed.** Not currently a leak — every path that
  reads it back for the client scrubs on read (`app/routers/campaigns.py:162,183`) —
  but the write side is inconsistent with `campaign_service.py`, which scrubs at
  write. A future export or new consumer would leak. Module 7 touches the export path.
- **`run.sh` creates and uses `venv/`, while the project venv and the README are
  `.venv/`.** Harmless today (it installs `requirements.txt` and starts uvicorn) but it
  builds a second environment without the dev dependencies. `run.sh` is not in any
  module's file list; worth folding into module 8's deploy work.
- **`scripts/seed_demo_data.py` and `scripts/balance_alert.py` were not exercised** by
  module 1. `balance_alert.py:61` calls `provider.get_balance()` directly rather than
  going through `/api/usage/balance`, so module 1's segment-denominated change to that
  endpoint did not affect it. It texts `ALERT_PHONE` — our number, not the client's —
  so the dollar figures in it are not a white-label problem.
  <br>_Re-verified 2026-08-19 (session 1b)._ Session 1b's spec asked for this entry to
  be deleted on the grounds that `scripts/balance_alert.py` does not exist. It does:
  `git ls-files scripts/` returns three files and the script has been tracked since the
  initial commit. Both halves of the original note check out against the source, so it
  stands. Flagged rather than quietly applied — deleting a true note to satisfy a
  review finding is the same failure the finding was trying to prevent.

---

## Module 3b — Today + Contacts (appended by session 3b, 2026-08-19)

Appended rather than rewritten: session 4 is editing this file in a parallel
worktree and a rewrite would be a merge conflict over the whole document.

**Done.** `bash agent/gate.sh` green at both ends; 96 tests (76 inherited + 20
new), green twice in a row on fresh databases. No migration, no `alembic/`, no
`base.html`, nothing of session 4's touched.

- **Today** (`app/templates/today.html`, `app/routers/dashboard.py`,
  `app/services/dashboard_service.py`). `/` and `/dashboard` render server-side
  from one service call; `/api/dashboard` returns the same payload.
  - The hero is derived, not stored. There is no auctions table and none was
    invented: soonest scheduled campaign if `campaigns.scheduled_at` exists —
    guarded with `getattr`, because session 4 is adding it right now — else the
    newest draft, else an empty state offering Compose. Its category comes from
    `campaigns.category_id` when that column lands and from the audience
    selector until then, so it survives the merge either way.
  - **Days since last send is computed from `sms_messages`, not from a
    campaign's audience string** — one grouped query joining messages to
    `contact_categories`, counting only `sent` and `delivered`. Never texted
    renders `—`, never `0`: those are opposite facts, and `0` reads as "texted
    today". A category whose only send *failed* also shows `—`, for the same
    reason. `SENT_STATUSES` deliberately does not import `BILLABLE_STATUSES`
    even though the two sets match today; a commercial change to what we
    invoice for must not silently rewrite the freshness figures.
  - Stale threshold is `DASHBOARD_STALE_DAYS` in config (default 14), not a
    constant: "stale" is a judgement about his auction calendar.
  - Four tiles, 14-day segment chart (quiet days are a 3px rule, not a gap),
    and per-category last-send outcome bars scoped to that category's most
    recent campaign — scoped to a campaign rather than a date window because a
    `blocked` message has no `sent_at` and a window would drop exactly the
    outcome worth seeing. Percentages are direct-labelled beside every bar.
  - Every currency figure comes from `billing_service`. No price, allowance or
    rate appears in a template, and `WHOLESALE_COST_PER_SEGMENT` is asserted
    absent from the tile payload.
- **Contacts** (`app/templates/contacts.html`,
  `app/services/contact_query_service.py`, `app/routers/contacts.py`).
  Category tabs with live counts rendered server-side, search across name,
  phone and `attributes.company`, bulk add/remove category, streamed CSV export.
  - Paging is a COUNT plus LIMIT/OFFSET. **Page 1 of 1,000 contacts is 4
    queries** — count, page, chips for the page, send counts for the page —
    asserted with a bound, because a per-row lookup would leave every other
    assertion passing.
  - Search takes "(954) 600-0777" as well as `+19546000777`: the digits are
    stripped and matched too, since that is how a number appears on the card in
    his hand.
  - No line-type column. We hold no line-type data and will not imply we do.
  - `contact_service.py` gained `audience_count()` (counts a selector without
    hydrating 50,000 rows) and nothing else; the screen's query layer is its own
    module so neither file goes near 500 lines.
- **The uncategorised import endpoints are retired.** `POST
  /api/contacts/import` and `/api/contacts/import/preview` now answer 400 with
  text naming `/api/imports/preview` and `/api/imports/commit`, rather than
  404ing — "gone" without "go here instead" is how an integration gets rebuilt
  against the wrong flow twice. The Contacts screen's own import panel is
  category-first and refuses to submit without one. Tested both ways.
- `app/templates/dashboard.html` is deleted; `today.html` replaces it. `/` and
  `/dashboard` moved out of `pages.py`'s generic PAGES table into
  `routers/dashboard.py`, and `pages.py` gained a per-page context hook so
  Contacts gets its tabs on the first paint.

### Found while working (session 3b)

- **`docs/API.md:77-78` still documents `POST /api/contacts/import` and
  `/api/contacts/import/preview` as the import flow.** They are retired as of
  this session and answer 400. Left alone deliberately — module 8 owns the
  docs. The replacement to document is `/api/imports/{preview,commit,undo}`,
  all requiring `category_id`.
- **The design renders are not in the repo.** `sessions/session-3b.md` cites
  `pen-exports/b3I3tf.png` and `pen-exports/j98DI.png`, and session 4 cites
  `pen-exports/BWsLw.png`; there is no `pen-exports/` directory and
  `Auctions4America.pen` is not tracked either (`git ls-files | grep -i pen`
  returns nothing). Both screens were built from the written spec, which
  describes the layouts in enough detail to do it. Worth fixing before module 8
  redesigns four more screens against the same missing file.
- **`.env` sets `ENVIRONMENT=production`, so a local `./run.sh` never migrates
  and every page 500s on a fresh `data/app.db`** with "no such table:
  sms_messages", from `shell_context` — i.e. on all seven pages, not a 3b
  regression. `alembic upgrade head` first and it is fine, which is exactly what
  `deploy.sh` does. Not a bug in the app; a foot-gun in the local `.env`, and
  `run.sh` is nobody's module (see the two existing notes about it).
- **`run.sh` builds its own `venv/` from `requirements.txt` on first use**, so
  the first invocation in a fresh worktree takes long enough to look hung. Same
  root cause as the `venv/` vs `.venv/` note above.

---

## Module 5b Part A — Go live prep (appended by session 5b, 2026-08-19)

**Status: Part A complete. Part B is human work and was not attempted.**
Nothing in this session can send a message. `SMS_PROVIDER` stays `console` in
every file touched, no live credential was written anywhere, and the only
provider call added is `get_balance()`, which is a read.

Gate green at both ends. **119 tests at start, 133 at end** (+14: 3 pre-flight,
11 monitoring). Suite run twice, green both.

### A1 — the 5a gaps

- **`docs/RUNBOOK.md` written.** It did not exist; `sessions/session-5a.md` asked
  for it and 5a was built without its spec. Deploy · roll back · restore a backup
  · **stop a campaign mid-send** · rotate the carrier credential · read the logs
  · check the certificate · disk full · a five-line health sweep. Real commands.
  It does **not** name the carrier, even though it is ours — the acceptance
  criterion greps it, and the habit is the control.
- **`deployment/deploy.sh` hardened**, all five items: `fonts:sync` alongside
  `build:css` (with a guard that refuses to sync an empty `static/fonts/`, since
  the `rsync --delete` that follows would delete the server's copy); backup
  immediately before migrating; migrate before restarting and abort the deploy if
  it fails; `/health` check after restart with code rollback to `app.prev` on
  failure; refusal to deploy from a dirty tree.
  - **The dirty-tree check reports rather than aborts under `--dry-run`.** A dry
    run's job is to print the later steps for review, and one that exits at step
    1 because the reviewer has an editor open shows nothing. `--allow-dirty`
    overrides it on a real run.

### A2 — local environment

`.env.example` now sets `ENVIRONMENT=development` (was `production`) and
`PUBLIC_BASE_URL=http://localhost:8000`, with the reasoning inline. This closes
the 3b "Found while working" note below. README gained a "Two commands stand
between a fresh clone and a working app" section naming `npm run build:css` and
`alembic upgrade head`, and saying plainly that neither symptom is a regression.

### A3 — monitoring

`app/services/monitoring_service.py`, both jobs registered in `app/main.py`'s
lifespan and both alerting through `agent/notify.sh`:

| id | trigger | does |
|---|---|---|
| `low_balance_alert` | hourly | pages below `BALANCE_ALERT_THRESHOLD`, re-alerts at most every 12h, re-arms on recovery |
| `daily_failure_digest` | cron 07:00 | yesterday's failures grouped by reason, silent on a clean day |

- **The alert no longer goes over the carrier account it is warning about.**
  `scripts/balance_alert.py` used `provider.send()`, and its own docstring
  admitted the hole: at a true zero balance the warning cannot be sent either, so
  the one moment it matters is the one moment it is dropped. That script is now
  a thin cron entry point over the same service function — one threshold, one
  re-alert window, one state file.
- Startup now logs one line per registered job by id. "Scheduler started" told us
  a scheduler exists; a job that silently failed to register looks exactly like a
  quiet night.
- `notify()` logs `notify.sh`'s stderr at INFO. With no pager credential the
  script prints the message and exits 0, and discarding that captured output made
  the alert vanish on exactly the boxes least likely to be watched.

### A4 — `deployment/PRODUCTION_CHECKLIST.md`

Every variable, what it does, what breaks when it is wrong. Placeholders only; no
real credential, number, hostname or IP. `SMS_PROVIDER`, `PUBLIC_BASE_URL`,
`SECRET_KEY` and `DATABASE_URL` are called out first as the four that decide
whether it works at all. `bootstrap.sh`'s closing instructions now point at it
instead of at `.env.example`.

### A5 — documentation

- **`docs/API.md`**: the retired `/api/contacts/import*` entries replaced with the
  `/api/imports/{preview,commit,undo}` flow, response shapes included. Also added
  the undocumented `/api/campaigns/preflight`, corrected `POST /api/campaigns`
  (`category_id` required unless `cross_category_override`), added the categories
  section, and fixed the usage responses, which still described a tiered
  `allowance`/`overage`/`base_fee` model the code has not used since module 1b.
  Every JSON body was verified against a running instance, not written from
  memory. This closes two "Found while working" notes below.
- **`docs/CLIENT_GUIDE.md`** re-read against what shipped. It described "Dashboard
  / Campaigns" and a four-step send; the product has Today / Compose and a
  three-step composer with pre-flight. Six real screenshots added under
  `docs/screenshots/`. There were no `[SCREENSHOT: ...]` markers to fill — 5a
  never wrote them — so the captures were placed where the sections needed them.
  No carrier name.

### A6 — the three light-palette screens

`settings.html` and `blocklist.html` ported onto the shell's tokens; a mechanical
class swap, no restructuring. `usage.html` was already on tokens. All three had a
page `<h1>` under the shell's top-bar heading, so the page name appeared twice —
all three removed. `blocklist.html` and `usage.html` also renamed their
`{% block title %}` to match the nav ("Opt-outs", "Usage & billing"), which
closes the 3a "nav label and page heading disagree" note. `base.html`'s header
comment, which documented the light palette as known-and-deliberate, updated to
say the opposite is now true.

### A7 — pre-flight measures what is actually sent

`build_report()` counted segments off the raw template, where `{first_name}` is
twelve literal characters and no recipient's message is. It now takes
`exact_segment_totals()`, which renders the template per resolved recipient —
using the send path's own `render()`, passed in — and sums the real segments. New
`merge_expansion` check flags when rendering pushes anyone past a boundary the
template did not predict. The composer's keystroke counter stays cheap and is now
labelled an estimate, with a line explaining why.

The `/preflight` endpoint's capacity row is now computed from the exact total,
which is what `create_campaign()` already stores in `estimated_segments`. That
makes the composer's capacity verdict and the send path's enforced one the same
arithmetic rather than two estimates that agree most of the time. **The capacity
check itself is untouched**, as is the rate, the allowance and the
billable-status set — this was accuracy, not policy.

**The spec's worked example is arithmetically impossible and was not followed
literally.** It describes a 158-character template with `{first_name}` crossing a
boundary for an 11-character name. `{first_name}` is twelve characters, so an
eleven-character name always makes the message *shorter*; a template at or under
160 stays at or under 160. Verified, then implemented with the tag that does
produce the under-count: `{name}` is six characters, so a 158-character template
holding it is one segment and renders to 163 — two — for a Christopher. Same 158,
same two names, arithmetic that works. Both directions are tested, and the
reasoning is written into the test file so the next reader does not re-derive it.

### Verified this session

- `bash agent/gate.sh` green at start (119) and end (133) — both shown
- Suite twice in a row, 133 both times
- `bash -n` clean on all four shell scripts; `shellcheck` is not installed
- `deploy.sh --dry-run` and `bootstrap.sh --dry-run` both exit 0, no side effects
- `deploy.sh --dry-run` output shows the migrate-before-restart order, the
  pre-migrate backup, the fonts sync, the health check and the dirty-tree refusal
- Both monitoring jobs log their registration on startup, by id
- A simulated $9.90 balance against a $50 threshold pages through `notify.sh` and
  is held by the re-alert window on the second tick — with a stub provider whose
  `send()` raises, so a regression that routes the alert back through the carrier
  fails loudly instead of texting someone
- `grep -rniE "telnyx|twilio" docs/CLIENT_GUIDE.md docs/RUNBOOK.md` → nothing
- `grep -rn "SMS_PROVIDER" deployment/` → only `console`
- Settings, Opt-outs and Usage screenshotted on dark tokens; "Settings" appears
  once

### Found while working (session 5b)

- **`app/routers/usage.py:46` still reads `WHOLESALE_COST_PER_SEGMENT`.** Session
  4's note below is unchanged and still module 8's. The runtime test proves the
  rate does not reach the response; the literal grep is what fails.
- **`run.sh` still creates `venv/` while the project uses `.venv/`.** Third
  session in a row this has been noted. `docs/API.md`, `README.md` and now
  `PRODUCTION_CHECKLIST.md` all assume `.venv`. It is a two-line fix in a file
  nobody owns.
- **`agent/notify.sh` sends over the carrier account by default.** It reads
  `TELNYX_*` — the same credential the low-balance alert is warning about. The
  monitoring code is now correct (it does not use `provider.send()`), but the
  transport underneath it can still be pointed at the empty account. `notify.sh`
  is human-only, so this is a note: **give it a separate credential before
  go-live**, or the fix in A3 is undone by configuration.
- **`docs/NEW_CLIENT_CHECKLIST.md` predates the category work.** It describes the
  uncategorised import flow and does not mention categories, pre-flight or the
  composer. Not touched — 5b's file list does not include it, and it is the
  skeleton's doc rather than this client's.
