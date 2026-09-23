# Status

_Header last rewritten 2026-08-20, at module 3a. Sessions 5b through 5g append
their own sections rather than rewriting it — **the bottom of this file is the
current state**, and `handoff.md` is the snapshot. Latest: "Module 5g Part A —
blocklist correctness", 2026-08-26._

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
- **`requirements.txt` pinned a carrier SDK the provider cannot drive.** Found at
  launch, on the box, with `SMS_PROVIDER` already flipped to the live carrier: the
  pin was `telnyx==2.1.2`, `app/sms/providers/telnyx.py:35` constructs the 4.x
  client class, `__init__` died on `AttributeError: module 'telnyx' has no
  attribute 'Telnyx'`, and `get_provider()` caught it and fell back to console.
  The app served every screen, the dashboard showed the ordinary amber "Dry run"
  pill, the sender number rendered in the sidebar, and nothing could send. The
  only record was an ERROR line in a journal `appuser` is not in the `adm` group
  to read. Two defects in one incident: the pin, and a fallback that is invisible
  by design.
  <br>_Fixed in session 5c: pin bumped to 4.175.0 and verified against the real
  package; the fallback is now recorded and rendered as a third send mode._

- **Nothing refuses to boot without a `SECRET_KEY`.** `app/core/config.py:36`
  defaults it to `""` and `app/core/auth.py:59` hands that straight to
  `URLSafeTimedSerializer`, which signs session cookies with it quite happily.
  There is no startup guard anywhere. Found during Part B: on a freshly
  bootstrapped box with no `.env` yet, `ENVIRONMENT` also defaults to
  `development`, so the app starts, auto-migrates on startup — the exact race
  `deploy.sh` runs migrations separately to avoid — and serves a public login
  whose sessions are signed with a key every reader of this repo knows. Anyone
  can mint an admin cookie. The window is small on a careful deploy and
  permanent on a careless one, and nothing in the app or the gate would say so.
  `PRODUCTION_CHECKLIST.md` already documents `SECRET_KEY` correctly, which is
  why this went unnoticed: the docs are right and the code does not enforce
  them. The fix is a startup check that refuses to serve on an empty
  `SECRET_KEY` — unconditionally, not only when `ENVIRONMENT=production`, since
  the dangerous case is precisely the box where `ENVIRONMENT` was never set.
  `app/core/config.py` belongs to no current module, so this is a note rather
  than a drive-by fix.

---

## Module 5c Part A — live-send blockers (appended by session 5c, 2026-08-20)

**Status: Part A complete. Part B is Jordan's and was not attempted.** Nothing in
this session can send a message: `SMS_PROVIDER` is `console` in every file
touched and in `tests/conftest.py`, no live credential was read or written, and
the only carrier object built anywhere is constructed with a visibly fake key and
never called. No contacts were imported.

Gate green at both ends. **133 tests at start, 145 at end** (+12: 11 provider
status, 1 white-label). Suite run twice, green both.

### A1 — the pin

`requirements.txt` now pins `telnyx==4.175.0`. Verified against the real package
rather than taken from the spec, in a venv built from `requirements.txt` alone:

- `client.messages.send()` accepts `to`, `from_`, `text`, `messaging_profile_id`
  (and returns `MessageSendResponse`, whose `.data` carries `id` and `parts`)
- `client.messages.retrieve(id=...)` still exists and its payload still has
  `to[].status`, which is what `get_message_status()` reads — the provider's
  other SDK call, which the spec did not name and which would have failed the
  same silent way
- its declared deps are `anyio<5`, `distro<2`, `httpx<1`, `pydantic<3`,
  `sniffio`, `typing-extensions>=4.14`

**Nothing else in `requirements.txt` resolves differently.** Diffing a full
install of the old pins against the new ones, the only changes are telnyx itself,
`distro` arriving, and `cffi`/`pycparser`/`PyNaCl` leaving — 2.x transitives that
nothing in this codebase imports. Every other version is identical.

### A2 — a failed provider no longer looks like a chosen one

`get_provider()` records a `ProviderFallback` (requested provider, exception type,
raw message) instead of only logging one, and `send_mode()` turns the two-state
"is it console?" question into three: `live`, `dry_run`, `unavailable`.

- The wording lives in `app/sms/factory.py`, next to the fallback that causes it
  and behind the same boundary as `scrub_provider_text()`. The pill and the
  Settings page render `label`/`detail` verbatim; neither computes its own
  answer, so they cannot disagree the way the API and the UI did before.
- `/api/settings/system` keeps `dry_run` but narrows it to *chosen* dry run, and
  adds `sending_unavailable`, `send_mode`, `send_mode_label`, `send_mode_detail`.
- The top-bar pill carries `data-mode` and goes red with different words. It is
  in the shell, so it is on all six screens — the state matters most on Today and
  Compose, which is where he actually is.
- The Settings banner renders **server-side** from the shell context rather than
  from the `/api/settings/system` fetch. A page that announces an outage only
  once a script has run announces it last, or never if the fetch is the thing
  that broke.
- The exception text stays in the log. It says `module 'telnyx' has no attribute
  'Telnyx'` — the carrier's name, twice, in the one string a future reader is
  most tempted to render "so support can see what broke".

### A3 — the tests that would have caught it

`tests/test_provider_status.py` (11) and one addition to `tests/test_whitelabel.py`,
sharing `tests/_provider_setup.py`, which drives the factory into each of its
three states and always restores it. All ten run authenticated; the new module
resets the login limiter around itself, because the suite was at nine logins
against a 10/minute cap and a tenth would have started failing modules on 429s
that read like auth bugs.

The one that matters most is the cheapest: `test_carrier_sdk_matches_the_provider_
it_is_used_through` asserts the installed SDK has the client class the provider
constructs. That is the whole of the launch-day bug, and it fails on
`pip install -r requirements.txt` rather than on the morning of a sale.

### A4 — nginx security headers

`deployment/nginx.conf.template` sets HSTS (`max-age=300`, deliberately five
minutes — HSTS is not revocable and this has to be walkable-back),
`X-Frame-Options DENY`, `X-Content-Type-Options nosniff`,
`Referrer-Policy same-origin`, and a CSP matching the app's actual profile:
`default-src 'self'` with `'unsafe-inline'` for scripts and styles, which is
load-bearing (the pre-paint theme script, per-page script blocks, `_brand.html`'s
inline palette, two screens' style attributes) and not laziness.

Verified by running nginx over the rendered template in a container with the app
behind it: config test passes, all five headers are present on a page, on a 404
from the app and on nginx's own 404 for `/.env`, and `/static/app.css` plus all
four Inter weights return 200 with the policy applied. The compiled stylesheet
references the fonts as `/static/fonts/...` — same-origin, so `font-src 'self'`
covers them.

**The live box is not updated.** `appuser` has sudo for `systemctl restart
a4a-sms` only, `deploy.sh` does not touch nginx, and the spec says not to
hand-edit the box. The template's header comment now carries the merge-into-an-
existing-certbot-block instructions and the two curl commands that prove it
worked.

### A5 — this file

The `SECRET_KEY` note was already committed (in `0e89818`, with the 5c spec), so
there was nothing uncommitted to rescue; the telnyx entry is filed beside it
above.

### Acceptance

`agent/accept-5c.sh` is the Part A stop condition — the six criteria from
`sessions/session-5c.md`, each as a check that runs rather than a claim. Criteria
1-5 pass locally. Criterion 6 needs the deployed site and is opt-in:

    A4A_URL=https://... A4A_PASSWORD=... bash agent/accept-5c.sh --with-remote

Criterion 5 is the interesting one: it checks out the pre-fix commit into a
worktree, builds a venv from *that* tree's `requirements.txt` (old pin included,
since the old pin is half the bug), copies the new tests in and requires them to
fail. All nine do.

### Found while working (session 5c)

- **A degraded box still accepts campaigns, marks every message `sent`, and bills
  for them.** Nothing on the send path consults `send_mode()`.
  `campaign_service.py:64` holds the console fallback, `console.py:43` answers the
  pre-flight balance question with `999_999.0` ("never trips the pre-flight
  check"), `console.py:35-40` reports every send successful,
  `campaign_service.py:356` writes `status="sent"`, and `sent` is in
  `BILLABLE_STATUSES`. So on a live box whose carrier failed to start, all 1,223
  contacts can be "sent" to, every row goes green, and the segments are counted
  against the 10,000 and invoiced — for messages nobody received. The pre-flight
  check, the safeguard that exists for precisely this, cannot fire because the
  provider it interrogates is a stub with a bottomless balance.
  Not a regression — identical before 5c, and correct in a *chosen* dry run. What
  changed is that the two cases are now distinguishable, so acting on the
  difference is possible for the first time. A fix touches the billable-status
  set and the pre-flight check, both escalation items, so it is
  **`decisions/002-degraded-box-still-bills.open.md`** rather than a quiet edit.
  Found by the fresh-context review of this session.
- **An unknown `SMS_PROVIDER` is the one misconfiguration with no send mode.**
  `factory.py:100` raises `ValueError` rather than degrading, so every page 500s
  — three lines above a docstring arguing that a bad configuration "should
  degrade to 'sends nothing', never to a crash loop on a box that is also serving
  the client's dashboard". Loud beats silent and this may well be right, but it
  is currently an accident of ordering rather than a decision, and the new
  three-mode enum has no state for it. Pre-existing; not changed here, because
  what a misconfigured box should do is not an implementation detail.

- **`agent/gate.sh` runs bare `python` and `alembic`, so it tests whatever is
  first on `PATH`.** On this machine that is a conda base env, where the gate
  dies at collection with `ModuleNotFoundError: slowapi` and reports "test suite
  is red" — a true statement about the wrong interpreter, and a very convincing
  false alarm for the next agent. `accept-5c.sh` puts `.venv/bin` in front before
  calling it, and `CLAUDE.md` now says to. The gate itself is human-only, so the
  fix is escalated: **`decisions/001-gate-interpreter-path.open.md`**. That file
  also records two problems in `.claude/hooks/verify-gate.sh` found alongside it
  — its stdin JSON parse raises, so `stop_hook_active` is never set and the
  `MAX_GATE_ATTEMPTS` guard can never fire, and the attempt counter degrades to a
  single fixed path (`/tmp/gate-attempts-`) shared across every session. This one
  was on attempt 22 of 4.
- **`docs/API.md:280-283` describes `/api/settings/system` as the place to find
  the webhook URL.** Worse than merely stale: that field was *removed* on
  white-label grounds — it was `PUBLIC_BASE_URL + "/webhooks/" + provider.name`,
  so it printed the carrier's name onto a client-facing screen — and
  `tests/test_whitelabel.py::test_system_info_exposes_no_webhook_url` pins its
  absence. The doc tells a reader to look for something the code deliberately
  does not return. The same entry now also misses this session's additions:
  `send_mode`, `send_mode_label`, `send_mode_detail` and `sending_unavailable`
  are new, and `dry_run` narrowed to mean a *chosen* dry run. Docs are module
  8's and `docs/` is not in 5c's file list, so this was left rather than quietly
  widened.
- **`docs/RUNBOOK.md:147` and `docs/CLIENT_GUIDE.md:145-150` describe the pill as
  two-state.** Both say it reads "Dry run" whenever the provider is console,
  which was the defect. Same owner, same reason for leaving it, and the client
  guide is the one he actually reads.
- **The live box still has the pre-5c nginx config and the hot-patched SDK.** The
  repo is now the truth for both, but neither reaches the server without a deploy
  (SDK) and a human with root (nginx).

## Module 5d Part A — refuse to send from a degraded box (appended by session 5d, 2026-08-26)

**Status: Part A complete except the deploy. Part B is Jordan's and was not
attempted.** Nothing in this session can send a message: `SMS_PROVIDER` is
`console` in `.env`, in `tests/conftest.py` and in every subprocess the
acceptance script starts; the degraded states are reached through
`tests/_provider_setup.py`, which replaces the carrier class with one whose
constructor raises and so never builds a carrier object at all. No live
credential was read. No contact data was imported, modified or deleted.

Gate green at both ends, twice in a row. **145 tests at start, 184 at end**
(+39: 17 degraded send path, 13 blocklist reasons, 8 hook loop guard, 1
white-label case for the auto-block notes).

### A1 — a degraded box refuses to start a campaign

`send_path_assessment()` in `campaign_service.py` turns `send_mode()` into the
same `{ok, detail}` shape `capacity_assessment()` returns, and `preflight()`
runs it **first**. A box that cannot reach a carrier has no capacity question to
answer, and the answer it would give is `console.get_balance()`'s 999,999 —
which is precisely how a degraded box sailed through the check that exists to
stop exactly this.

- The composer shows it *before* the send, as `check_send_path()` — a pre-flight
  row of its own, first in the checklist, FAIL not WARN. The composer draws
  whatever the server returns, so no template changed.
- `dry_run` is excluded and the console flow is byte-for-byte unchanged.
  `agent/accept-5d.sh` check 3 proves that rather than asserting it: it runs the
  same campaign through the console path on the pre-5d tree and on this one and
  diffs the two results.
- Module-level, not a method: it reads nothing off the instance, and the
  test-send endpoint needs the same verdict with no campaign to hang it on.

**Scope note — the test-send endpoint was included, and it is not in the spec.**
`POST /api/campaigns/test-sms` answered "Test SMS sent to +1…" on the console
fallback, about a message that reached nobody. That is the same lie A1 exists to
remove, on the one screen someone uses to decide whether the box works, and it
is three lines in a file A1 already required editing. Flagged here rather than
buried: it is one `if` in `routers/campaigns.py` and one test, and it reverts on
its own if Jordan disagrees.

### A2 — a degraded row is never billable

`not_sent`, added to `MESSAGE_STATUSES` and deliberately outside
`BILLABLE_STATUSES`. Written in the send loop as step 3, before the provider
call, checked **per message** rather than once — a provider that degrades
between the pre-flight and the blast would otherwise leave the first thousand
rows honest and the rest billable.

`billing_service.py` was not touched. The billable set already lived in
`sms_message.py` and `compute_usage()` already filters on it, so the correct
change was a status the query cannot count, not an edit to the query.

The test asserts against the billing query itself, with `sent_at` set so the row
is inside the cycle window: the only thing keeping it off the invoice is its
status. It flips the same row to `sent` as a positive control, because "excluded
from the count" is also true of a `compute_usage()` that is broken outright.

### A3 — an unknown provider degrades instead of 500-ing

The unrecognised-name case now raises *inside* the same `try` that already
handles a provider failing to construct, so it takes the identical path: console
fallback, `ProviderFallback` recorded, `send_mode()` reports `unavailable`. No
programmer-error case was kept, and the docstring says why — the only input is
`settings.SMS_PROVIDER`, which comes from `.env`, so there is no way to reach it
except a misconfiguration.

Verified by running the app with `SMS_PROVIDER=telnix`: it boots, all seven
screens return 200, the pill is red, and the real cause is the one ERROR line in
the log. Before this it raised at import and every page 500'd.

`active_sender_number()` now returns `""` rather than `"(dry run)"` for an
unrecognised provider. "(dry run)" is reserved for console being *chosen*;
saying it here had the Settings page call the box a dry run directly beneath a
banner saying sending was unavailable. It renders "—" / "not configured".

### A4 — `/health` reports degraded state

`{"status": "degraded", "sending_ok": false, "send_mode": "unavailable",
"reason": "…"}`. White-label: `reason` is `send_mode().detail`, our wording; the
SDK exception stays in the log.

**Still HTTP 200 while degraded, and that is a decision, not an oversight.**
`deployment/deploy.sh:198` health-checks this endpoint after the restart and
rolls the release back on a non-200. A 503 for a bad carrier credential would
therefore revert every deploy to a degraded box, including the deploy that fixes
it. The app being up and the app being able to send are different facts. The
monitor must be configured on the `sending_ok` field — `docs/API.md` says so in
the new Health section.

Nothing was routed over SMS and `agent/notify.sh` was not touched, per
decision 002.

### A5 — `.claude/hooks/verify-gate.sh`

Both bugs from decision 001, plus the one underneath them.

- The parse now reads the payload from an **environment variable** and uses
  `strict=False`. The old form pasted stdin into a Python triple-quoted literal
  inside an unquoted heredoc, which had three ways to fail on input we do not
  control — a literal `'''`, a backslash, `$` expansion — and the control
  character was only the one that actually bit.
- The attempt counter is keyed on the session id, and a **missing** id gets a
  per-invocation path rather than the shared one. Inheriting a stranger's count
  is what produced "Attempt 22 of 4".

`tests/test_hook_loop_guard.py` runs the real hook in a throwaway sandbox with
its own `agent.config.sh`, its own decisions directory and a **stub
`notify.sh`** — the real one sends an SMS through the carrier when a credential
is in the environment, and the real `decisions/` is the queue the hook itself
reads, so a test writing a `*.open.md` there would silently block every future
session from stopping. Three of its seven tests fail against the pre-fix hook,
with the actual `JSONDecodeError` in the traceback.

### A6 — `docs/API.md`

The Settings entry no longer tells the reader to find the webhook URL there; it
now documents why that field is absent and which test pins its absence,
alongside 5c's four `send_mode` fields. New sections for `/health` and the
`send_path` pre-flight row, the `not_sent` status under billing, and the
blocklist `counts` object and webhook auto-block.

`docs/RUNBOOK.md:147` and `docs/CLIENT_GUIDE.md:145-150` still describe the pill
as two-state. Left alone — A6 named `docs/API.md` only, and the client guide is
the one he actually reads, so it wants a human's eye on the wording.

### A7 — the delivery webhook consults the auto-block list

`should_auto_block()` had exactly one call site: the *submission* path, where a
provider rejects a send outright. The larger share of dead numbers is accepted
at submission and fails later by webhook, which never consulted the list — so
2,673 of one campaign's 6,857 recipients stayed live and would have been paid
for again on every send.

- The call now also sits in `record_delivery_status()`, **inside** the branch
  that only runs on the first terminal event, so a carrier retrying for three
  days blocks the number once. `block_number()` refusing duplicates is the
  second layer, not the first.
- `"deemed invalid"` added to `AUTO_BLOCK_ERROR_FRAGMENTS`. The carrier's
  wording is "the destination phone number was deemed invalid by the carrier",
  and neither `"is not a valid"` nor `"invalid phone number"` occurs in it.
- `"Blocked as spam - temporary"` matches nothing, asserted in both directions.
  Blocking on a transient failure deletes a reachable buyer permanently, which
  costs far more than one retry.
- `record_delivery_status()` gained a `source` parameter, passed as the provider
  from each webhook router. The client never sees it — `_neutral_source()` maps
  anything but "manual" to "Automatic" — but a two-carrier box needs to know
  which one condemned a number.

The campaign-4 backfill was **not** re-run. The route test posts the carrier's
real payload shape and asserts the code path now produces the same outcome
unaided.

### A8 — the Opt-outs headline

Two figures where there was one: **Opt-outs** (`stop_keyword`, the only one in
red) and **Unreachable** (`delivery_failure` + `carrier_block`, neutral). A third
**Manually blocked** tile renders only when there are any — manual blocks are
neither a request from the person nor a verdict from a carrier, and folding them
into either would put the headline back to counting the wrong thing.

`OPT_OUT_REASONS` is `("stop_keyword",)`, matching `dashboard_service.py:248`
rather than inventing a second definition, and a test pins that.

Counted server-side by a grouped query, not tallied in JS from `data.numbers` —
that list is capped at 5,000 rows, so a client-side tally would under-report a
long list, and under-report it as *fewer opt-outs*, the direction nobody checks.

Seen in a browser against 7 seeded rows shaped like the live box: `1 Opt-outs ·
5 Unreachable · 1 Manually blocked`, with only the first red.

### The 500-line rule, twice

`campaign_service.py` reached 559 lines, so the dispatch and scheduling entry
points moved verbatim to **`app/services/campaign_dispatch.py`** (82 lines). The
boundary is *when a send begins*, not *how it runs* — nothing about whether a
campaign may go out moved with it, and both entry points still hand the work to
the same `send_campaign()` a button press reaches. No logic changed. Importers
updated: `app/main.py`, `app/routers/campaigns.py`,
`tests/test_campaign_guardrails.py`.

The review fixes then pushed it back to 524 and the gate caught it again. Rather
than trim comments, `send_path_assessment()` and the two refusal strings moved to
**`app/sms/factory.py`**, beside `send_mode()` — which is where they should have
been from the start, for the reason that module's own docstring gives: the layer
that knows the carrier's name is the layer responsible for every client-safe
sentence about it. The function reads nothing from the DB, so the layering rule
is intact. `campaign_service.py` is 478; `factory.py` is 220. The second time the
500-line rule forced a split it produced a better design than the one it
interrupted, which is the argument for having the rule.

### Acceptance

`agent/accept-5d.sh` is the Part A stop condition — the eleven criteria from
`sessions/session-5d.md`, each as a check that runs rather than a claim, with the
60-turn cap recorded in its header. Criteria 1-8 and 10-11 pass locally.
Criterion 9 and the live half of 11 need the deployed site and are opt-in:

    A4A_URL=https://... A4A_PASSWORD=... bash agent/accept-5d.sh --with-remote

The two interesting checks are both differential:

- **Check 3** runs the same campaign through the console path on the pre-fix
  worktree and on this tree and requires the results to be byte-identical. That
  is what "the dry-run flow is unchanged" means, rather than a promise.
- **Check 8b** runs the same *degraded* campaign on both trees using only
  symbols the pre-fix tree already had, and prints them side by side. Before:
  `completed`, three rows `sent`, three segments billed. After: `aborted`,
  nothing written, nothing billable. Check 8 on its own is weak — two of the
  three new test modules fail at *import* against the pre-fix tree because the
  symbols they test did not exist, which is true and proves nothing about the
  bug. 8b is the one with teeth.

### The review pass

Two things beyond the suite, both run rather than reasoned about.

**A leak probe in the degraded state.** Logged in, drove the factory into
fallback, and scanned the *rendered bytes* of all six shell pages, `/health`,
`/api/settings/system`, `/api/blocklist`, `/api/usage/current`,
`/api/campaigns`, `POST /preflight` and `POST /test-sms` for the carrier name
and for `WHOLESALE_COST_PER_SEGMENT`. The interesting case is the auto-block:
its notes are built from raw carrier text, so the probe fed it
`"Telnyx error 40300: Not routable — the destination number is a landline. See
https://developers.telnyx.com/docs/errors"`. The client sees
`"Auto-blocked: SMS carrier error 40300: Not routable — the destination number
is a landline. See"` with `source: "auto"`. Nothing leaked anywhere.

**A mutation check**, because a test that passes on reverted code is
decoration. Each change was reverted in turn and the matching tests had to go
red:

| reverted | result |
|---|---|
| `app/sms/factory.py` | 3 failed |
| `/health` in `routers/pages.py` | 1 failed |
| the webhook auto-block (3 files) | 6 failed |
| the split blocklist counts (3 files) | collection error, then failures |
| the composer pre-flight row (2 files) | collection error, then failures |
| just the `"deemed invalid"` fragment | 2 failed |

No test in the new modules survives its own subject being removed.

### What the fresh-context review changed

Three independent reviewers, none sharing this session's context. Five real
defects, four of them in code this session wrote. All fixed, each with a test
that fails when the fix is reverted.

**1. The scrubber's word boundaries let the carrier's name through.**
`PROVIDER_WORDS` was `\b(?:telnyx|twilio|…)\b`, and the *trailing* `\b` fails the
moment an SDK glues the name to a word — `TelnyxError`, `telnyx_api`,
`TwilioRestException` all passed through unscrubbed. This mattered because A7
made `blocked_numbers.notes` the first client-rendered string in the codebase
assembled verbatim from carrier free text at volume: 2,673 rows in one campaign,
each carrying whatever the carrier chose to call itself, rendered in the Notes
column and its tooltip. My own probe missed it because I used
`"Telnyx error 40300"` — with a space, which the anchored regex *does* catch.
Boundaries removed (`app/sms/phone.py:103`), and
`test_whitelabel.py::test_carrier_branded_text_written_by_a_webhook_is_scrubbed_on_the_way_out`
writes carrier-branded text through the real webhook and reads it back off
`/api/blocklist` and `/blocklist`. The suite structurally could not have caught
this: the existing sweep walks `/api/blocklist` but only over whatever rows
happen to exist, and no module wrote branded notes.

**2. A delivered message could be permanently blocked by a later failure.**
`record_delivery_status()`'s guard admits `msg.status == "delivered"`, so
delivered-then-failed — a carrier retry, a duplicate, or a race between two of
its workers — reached the new auto-block for a message that provably arrived on
a handset. Pre-existing condition, brand-new consequence: it used to cost a wrong
counter, and with A7 it deletes a buyer who received the text. Now guarded on
`msg.delivered_at is None`. A handset receipt is not revocable by a later failure
event.

**3. When the A2 backstop fired, the campaign reported `completed`.** Rows were
written `not_sent` correctly, then the loop fell through to
`campaign.status = "completed"` with `abort_reason` null. The campaign rail is
the entire UI — there is no detail screen — and it renders the badge and shows a
reason only when `abort_reason` is set, so a blast that reached nobody read
exactly like one that worked. That is the same lie this session exists to remove,
one level down. The loop now counts degraded rows and aborts the campaign with
the reason.

**4. The refusal was in the wrong tense on the composer.** One string served both
surfaces, so a draft he had not sent was labelled "Nothing was sent." — which
reads as a past campaign having silently failed, on the screen whose only job is
to stop him *before* he starts. Split into `DEGRADED_REFUSAL_BEFORE` ("This
campaign will not be started.") and `DEGRADED_REFUSAL_AFTER`, both returned by
`send_path_assessment()` so no call site invents a third.

**5. The send button answered "Campaign sending started" on a degraded box.**
The refusal happened in the background task and surfaced when the rail next
polled. `POST /{id}/send` now refuses synchronously with 409 — and refusing
*before* the task is queued leaves the campaign a **draft** rather than consuming
it as `aborted`, which matters because decision 002's own justification is that a
campaign that never ran can simply be re-run and nothing in this codebase moves
one back from `aborted`. The background and scheduled paths keep their own
refusal; this is an additional layer, not a replacement.

Also fixed: `test_blocklist_reasons.py` was the only new module without the
login-limiter reset its siblings document (`/login` is 10/minute per IP and the
suite spends 13 logins in one window; the failure would have surfaced as
`assert "counts" in payload` against a 429 body).

Reviewers confirmed, independently of my own checks: `BILLABLE_STATUSES` is
byte-identical to HEAD and `billing_service.py` is not in the change set at all;
`_instance`/`_fallback` in the factory cannot end up describing different
moments; no test constructs a live provider or reaches a non-loopback socket
(one reviewer ran the whole suite with `socket.connect` patched to raise —
180 passed, zero outbound attempts); `agent/gate.sh` and `agent.config.sh` are
byte-identical; `.env` untouched.

### Found while working (session 5d)

- **The capacity row still reads PASS on a degraded box.** It sits directly under
  the red "Sending status" refusal, saying "capacity covers the estimated 4
  segments" — which is the console stub's bottomless balance answering honestly
  about the wrong provider. The refusal is first, unambiguous, and `report.ok` is
  false, so nothing invites a send; but a green tick under a red cross is a
  judgement call worth a human's eye. Not changed here: the capacity check is on
  the do-not-weaken list and the spec says not to refactor anything else in that
  file. Candidate for 5e.
- **`/health` is unauthenticated and now discloses that the box cannot send.**
  That is the point — a monitor has no session — but it is one bit of
  operational state a stranger can read. Judged worth it: the alternative is no
  alert channel at all when the carrier is down.
- **The scheduled-campaign path inherits the refusal for free**, because
  `run_due_campaigns()` calls the same `send_campaign()`. Worth stating because
  it is the property that makes the guard complete: a degraded box cannot send
  by button *or* by schedule.
- **ESCALATED — `decisions/003-auto-block-fragments-on-the-webhook-path.open.md`.**
  A7 is implemented exactly as specified, and the fragment list it now runs on a
  2,673-event path was written for one that carried a handful. Four problems, all
  reproduced by running `should_auto_block()`: `"unreachable"` is standard
  carrier wording for a switched-off handset (Twilio 30003) and also fires on
  carrier-side outages; the numeric fragments `21610`/`21612`/`40300` are
  unanchored and collide with Brevard County phone numbers quoted in error text;
  `"has not been enabled for the region"` blocks the recipient for *our* account
  misconfiguration; and genuine carrier-level opt-outs are filed as
  `delivery_failure`, so A8's new headline under-reports the one figure it exists
  to fix. Escalation item 5 — implement what the spec says, do not tune the
  blocklist rules yourself — so the file asks rather than guesses. Not blocking:
  Part A is complete and the gate is green. It should be answered before the
  client gets a login, and answered against campaign 4's real error strings
  rather than the carrier documentation the file quotes.
- **A refused campaign is unrecoverable, and 5d only half-fixed that.** Nothing
  anywhere moves a campaign back to `draft` (`campaigns.py`,
  `campaign_service.py:370`, `campaign_dispatch.py:55` all gate on it). The
  send *button* now refuses before queueing, so a draft survives — but a
  **scheduled** blast that lands in a degraded window is still consumed
  permanently and has to be rebuilt. Decision 002's justification is "a campaign
  that never ran can simply be re-run"; for the scheduled path the code does not
  permit that. Candidate for 5e.
- **Neither webhook verifies a signature.** Pre-existing, but the blast radius
  changed: an unauthenticated POST to `/webhooks/telnyx` or
  `/webhooks/twilio/status` could previously only flip a message status, and can
  now create a permanent blocklist row. It still needs a valid `external_id`,
  which is not guessable, so this is a hardening item rather than an open hole.
- **A transient webhook consumes a message's one shot.** `record_delivery_status`
  is idempotent on the first terminal event, so if a transient failure arrives
  first, a later "not routable" for the same message is dropped and the number is
  never blocked. Correct idempotency, small cost, worth knowing.
- **Line-type screening at import is still the bigger win.** A7 stops a dead
  number recurring after the first failed send; it still costs one send to
  discover. Already logged in `modules.md` under "Found in live use".
- **The live box has not been deployed to.** Everything above is in the repo and
  proven locally; the box still runs the pre-5d code, the pre-5c nginx config and
  the hot-patched SDK. Criterion 9 cannot pass until someone deploys.

---

## Module 5g Part A — blocklist correctness (appended by session 5g, 2026-08-26)

**Status: Part A complete except the deploy.** Implements
`decisions/003-auto-block-fragments-on-the-webhook-path.md` in the order that
decision rules. Nothing in this session can send a message: `SMS_PROVIDER` is
`console` in `.env`, in `tests/conftest.py` and in every subprocess the
acceptance script starts; the classifier probe is a pure function over strings
and touches no provider and no network. No live credential was read. No contact
data was imported, modified or deleted, and **nothing was unblocked** — the
2,959 existing rows are untouched, as the spec requires. This session changes
what happens to *future* failures.

Gate green at both ends, twice in a row. **184 tests at start, 259 at end**
(+75: 73 across `tests/test_blocklist_correctness.py` and
`tests/test_carrier_error_surfaces.py`, 1 SDK-shape case in
`tests/test_provider_status.py`, 1 index check in `tests/test_migrations.py`).
Decision 004 added nine more after the fact — see the section at the end of this file.
One existing test rewritten — see below.

### The problem in one line

5d A7 pointed `should_auto_block()` at the delivery-webhook path, taking it from
a handful of events per campaign to 3,037. Its fragment list was written for the
*submission* path, where a carrier refuses outright; the wide path carries the
whole transient-failure vocabulary as well, and the list was unanchored
substrings with three bare numeric codes in it. The failure is invisible by
construction: a wrongly blocked buyer stops appearing in campaigns and leaves one
`delivery_failure` row among thousands of correct ones.

### A1 — a transient failure never blocks

`TRANSIENT_FAILURE_MARKERS` in `app/sms/compliance.py` — `temporar`, `retry`,
`congestion`, `try again` — is checked first and beats every other rule,
including a structured code. `"unreachable"` **stays** in the block list; the
transient marker is what separates a dead line from a handset switched off during
a blast.

Deliberately unanchored substrings, and deliberately wider than they need to be.
That is the inverse of the trade the block fragments make: a transient marker
causes a *refusal to act*, so over-matching costs half a cent for one more
attempt, while under-matching deletes a live buyer. Written down in the module,
because the asymmetry is the only reason the two lists are matched differently.

### A2 — codes come from a column, never from prose

`sms_messages.error_code`, migration `b7e3c9a1d024`, additive and nullable,
nothing backfilled. `webhooks/telnyx.py` was building `f"{title}: {detail}"` and
dropping `errors[].code` on the floor; it now carries the code as a code.
`webhooks/twilio.py` posts `ErrorCode` as its own form field and now passes it
through as one.

`"21610"`, `"21612"` and `"40300"` left the text fragment list and were **not**
`\b`-anchored back in. `\b21610\b` still matches a bare code sitting in prose,
and prose is where the carrier quotes the destination number — +1 321-610-xxxx
and 321-612-xxxx are assignable Brevard County numbers in this client's own
market. The codes now match `AUTO_BLOCK_ERROR_CODES` / `CARRIER_OPT_OUT_CODES`
against the new column only.

`should_auto_block()` kept its one-argument-compatible signature on purpose:
`campaign_service.py:427` is its other call site and that file belongs to 5e.

### A3 — the provider error is parsed, not stringified

Row 4 of the live corpus is the SDK's `__str__` of an API error, which is the
response body dict-repr'd into the message — and since 5d that column is the
source of `blocked_numbers.notes`, which the client reads on the Opt-outs page.
`describe_send_error()` in `app/sms/providers/telnyx.py` reads `title` and
`detail` off `exc.body` by name. Duck-typed on the attribute rather than caught
by SDK class, so the module stays importable without the SDK — and
`tests/test_provider_status.py` now asserts `APIStatusError.__init__` still takes
`body`, so a version bump that renames it fails at `pip install` rather than
silently getting worse.

`strip_payload()` in `app/sms/phone.py` is the backstop, called from inside
`scrub_provider_text()` so every client-facing surface gets it: any provider,
any SDK version, any path that reaches a client string without going through
ours. It cuts at two structural characters in a row (`{'`, `[{`, `{"`) — one
brace alone is not enough signal to truncate an error message on. All twelve
`scrub_provider_text()` call sites are error strings; none carries a message
body, so no template merge tag can be eaten by it.

### A4 — word boundaries

`_anchored()` compiles the word fragments with `\b` on both ends. Anchoring is
safe here in a way it was not in `scrub_provider_text()` during 5d: that regex
reads text an SDK assembled, where the carrier's name is glued to a word
character and a trailing `\b` fails open. These read a carrier's English prose,
where "landline" is a word. Cheap, and not the rule doing the work.

### A5 — never block a recipient for our own misconfiguration

`"has not been enabled for the region"` is gone from the block list; it is a geo
permission on the *sending* account (Twilio 21408). It now raises a
configuration alert, and the recipient stays on the list.

**The signal is a row read back by `/health`, not a text.** `agent/notify.sh`
sends an SMS over the carrier account and 5d ruled an alert must not travel over
the thing it is warning about. It is also the wrong shape for this path: these
arrive on the delivery webhook, thousands in a burst, inside a request that must
answer promptly — a subprocess per event would turn a carrier's retries into a
fork bomb. `record_config_alert()` / `active_config_alerts()` in
`monitoring_service.py`; `config_ok` and `config_issues` on `/health`; the alert
stops reporting seven days after it was last raised, because a field stuck red
forever is a field people learn to ignore.

`/health` reads it **without `Depends(get_db)`**. A dependency that raises means
the handler never runs, which is the same rollback trap as returning 503 one
layer up — `deployment/deploy.sh` restores the previous release on a non-200
there. `active_config_alerts()` opens its own session, selects columns rather
than entities so nothing detaches when it closes, and returns `[]` on anything
going wrong. Asserted by
`test_health_survives_a_database_it_cannot_read`.

### A6 — carrier opt-outs get their own reason

`carrier_opt_out` added to `BLOCK_REASONS`; opt-out wordings and code 21610 map
to it. `OPT_OUT_REASONS` becomes `("stop_keyword", "carrier_opt_out")` and
`dashboard_service.stat_tiles()` now **imports** that tuple instead of filtering
on the literal `"stop_keyword"` — same commit, as decision 003 requires, so the
invariant at `blocklist_service.py:88-91` stays true.

Distinct in the record, combined in the metric. `stop_keyword` is a message this
system received and can produce on demand; a carrier's opt-out record is not that
evidence, and collapsing them weakens a compliance record that exists to be
audited.

### The one existing test that changed

`test_the_opt_out_definition_matches_the_dashboard_tile` asserted the literal
`OPT_OUT_REASONS == ("stop_keyword",)`. Decision 003 ruling 6 changes that tuple,
so it had to move — and it was pinning the wrong thing anyway. It tested the
membership list rather than the property the membership list exists for: it went
red on an intended change, and it would have stayed **green** on the change that
actually matters, a second literal filter appearing in `dashboard_service`, which
is exactly what was sitting there. It now asserts the invariant, and the counts
are compared row for row in
`test_the_blocklist_headline_and_the_dashboard_tile_agree`.

### Acceptance

`agent/accept-5g.sh` is the Part A stop condition — the nine criteria from
`sessions/session-5g.md`, each a check that runs rather than a claim, with the
60-turn cap recorded in its header. Criteria 1-8 pass locally. Criterion 9 needs
the deployed site and is opt-in:

    A4A_URL=https://... A4A_PASSWORD=... bash agent/accept-5g.sh --with-remote

**Check 3 is the one with teeth.** It runs one fixed corpus of thirteen strings
through `should_auto_block()` on the pre-fix worktree and on this tree, using
only the one-argument signature the pre-fix tree shipped with, and prints both
verdicts side by side:

                                       before   after
       A1: congestion                  True     False  <-- changed
       A1: switched-off handset        True     False  <-- changed
       A1: upstream outage             True     False  <-- changed
       A1: will retry                  True     False  <-- changed
       A2: code inside a uuid          True     False  <-- changed
       A2: number that is not a code   True     False  <-- changed
       A2: quoted brevard number       True     False  <-- changed
       A4: fragment inside a word      True     False  <-- changed
       A5: region not enabled          True     False  <-- changed
       live: deemed invalid (112)      True     True
       live: not routable (2,847)      True     True
       live: raw payload (2)           True     True
       live: temporary spam (76)       False    False

It fails if any hazard row did *not* block at the base ref — a fix whose defect
does not reproduce proves nothing — and it fails if any of the three live
wordings stops blocking, which is the expensive direction: 2,847 landlines a
campaign at roughly half a cent each.

**Check 8b answers the question check 8 cannot.** Check 8 (new tests red against
the pre-fix tree) is true and weak: the modules fail at *import* there, because
the symbols they test did not exist yet, and "the module is new" is not "these
tests would catch the bug coming back". So `agent/mutate-5g.py` reverts each of
the ten fixes **behaviourally** — inside the current API, one at a time, in a
scratch copy of the tree — and requires at least one test to go red for each:

    R1  transient guard removed              CAUGHT (5 failed)
    R2  codes matched in prose again         CAUGHT (6 failed)
    R3  word boundaries removed              CAUGHT (3 failed)
    R4  region wording blocks again          CAUGHT (4 failed)
    R5  opt-outs counted as unreachable      CAUGHT (3 failed)
    R6  dashboard uses the literal again     CAUGHT (1 failed)
    R7  strip_payload is a no-op             CAUGHT (9 failed)
    R8  provider stringifies again           CAUGHT (4 failed)
    R9  webhook drops the carrier code       CAUGHT (2 failed)
    R10 carrier_opt_out reason not used      CAUGHT (5 failed)

No mutation survives. It also found the one place the suite reads stronger than
it is: two of the five parametrized transient wordings contain no block fragment
at all and pass whether or not the guard exists. They are real carrier wordings
worth keeping, and the parametrize block now says so rather than showing five
green ticks for three proofs.

### Found while working (session 5g)

- **`\b`-anchoring narrows plurals, and one of them is an opt-out wording.** A
  regression against HEAD, small and deliberate: `"opt-out"` used to match inside
  `"opt-outs"` as a plain substring and no longer does. Allowing an optional `s`
  would restore it and would simultaneously re-break decision 003's own stated
  example — `\blandlines?\b` matches "landlines-are-fine", which ruling 4 exists
  to prevent. Ruling 4 asks for the boundaries explicitly, so the narrowing is
  inherent to what was ordered. For the *dead-number* fragments it is the safe
  direction; for an opt-out it is a missing compliance record rather than a
  wasted half-cent, which is why it is written down rather than silently
  accepted. Not tuned here — the opt-out fragment list is escalation item 5.
  Absent from this account's traffic: the whole corpus is four strings and none
  contains "opt-out" in any spelling.
- **`"opted-out"` (hyphenated) matches no fragment, before or after.** The list
  has `"opted out"` and `"opt-out"`. Pre-existing, unchanged by this session,
  same escalation item.
- **RESOLVED — `decisions/004-unreachable-without-a-transient-adjective.md`,
  option 2, implemented.** A1's transient guard did not stop the wording A1's own
  rationale was built on: Twilio 30003's `ErrorMessage` is literally
  `Unreachable destination handset` — none of the four markers — so it still
  blocked, as did decision 003's own `SMPP bind failed - SMSC unreachable`. The
  ruling drops `"unreachable"` from `AUTO_BLOCK_ERROR_FRAGMENTS` rather than
  adding a second tier of logic: option 3's non-redundant half was the
  structured-code case, and codes belong in the `error_code` column A2 already
  built. See "Implementing decision 004" below.
- **A transient webhook still consumes a message's one shot.** Carried over from
  5d and now slightly sharper: `record_delivery_status` is idempotent on the
  first terminal event, so a transient failure arriving first means a later
  "not routable" for the same message is dropped and the number is never blocked.
  Correct idempotency, small cost, worth knowing.
- **Neither webhook verifies a signature.** Carried over from 5d, and the blast
  radius grew again: an unauthenticated POST can now also write an
  `app_settings` row via the configuration alert. It still needs a valid
  `external_id`, which is not guessable, so this remains a hardening item rather
  than an open hole — but it is the second session in a row where the answer to
  "what can an unsigned webhook do?" got longer.
- **`campaign_service.py`'s submission path gets none of 5g.** It is in 5e's
  file set and was deliberately not touched, so `should_auto_block()` kept a
  signature it can still call — but that call discards the verdict's `reason` and
  `alert_key` and hardcodes `delivery_failure` (`campaign_service.py:427-433`).
  Three consequences, all real and all bounded by the fact that the webhook path
  carries the overwhelming majority of failures (3,037 against a handful):
  a carrier opt-out rejected at submission is still filed as an unreachable
  number; a region-permission refusal at submission raises no operator signal, so
  `/health` stays green for it; and `21612`/`40300` rejections that used to
  auto-block there via a prose substring now do not, because `SendResult` has no
  field to carry a code. That last one is a narrowing in the direction decision
  003 wanted — matching codes in prose *was* the bug — but it is a behaviour
  change on a path this session was told not to touch, and it should be stated
  rather than discovered. One field on `SendResult`, one line in the provider,
  three in `campaign_service.py`, whenever 5e opens that file.
- **`sms_messages.error_code` is one namespace for two carriers.** No provider
  discriminator on the column, and `AUTO_BLOCK_ERROR_CODES` mixes a Twilio code
  (`21612`) with a Telnyx one (`40300`). No collision today — Twilio uses 2xxxx
  and 3xxxx, Telnyx 1xxxx and 4xxxx — and nothing enforces that. `source` on the
  message's blocklist row already records which carrier reported it, so the fix
  when it matters is to key the code sets by provider rather than to add a
  column.
- **What the fresh-context review changed.** Four defects it found and this
  session fixed: `strip_payload()` cut at the opening brace and discarded
  everything after it, so a carrier's remediation advice was lost and an error
  that was *only* payload became the empty string — which rendered as
  "Auto-blocked:" and nothing else on the client's Opt-outs page; the config
  alert wrote its row once per event on a path whose whole justification was
  avoiding per-event work; and the migration used `batch_alter_table`, which
  rebuilds `sms_messages` and every index on it, under a docstring claiming it
  touched no index. The first is now a span removal with a bracket scanner, the
  second is throttled, the third is a plain in-place `ADD COLUMN` with
  `test_the_error_code_migration_did_not_rebuild_the_sms_message_indexes`
  pinning it. All four were reproduced before being fixed.
- **The live box has still not been deployed to.** Criterion 9 cannot pass until
  someone does. Everything above is in the repo and proven locally; the box runs
  the pre-5c nginx config, the hot-patched SDK, and pre-5d application code.

---

## Implementing decision 004 — "unreachable" leaves the block list (2026-08-26)

Option 2, as ruled. **259 tests** (250 → 259), gate green twice,
`agent/accept-5g.sh` checks 1-8b pass.

### The change

One fragment removed from `AUTO_BLOCK_ERROR_FRAGMENTS` in `app/sms/compliance.py`.
Nothing else in the classifier moved: no second tier, no code table, no new
branch. That was the ruling's own argument — option 3's text half was already
done by whichever fragment sat beside `"unreachable"` in the same string, and its
structured-code half belongs in the `error_code` column A2 built, not in a
two-tier text rule.

### Rider 1 — the criterion, re-pointed rather than deleted

Acceptance criterion 3 was "a transient-worded error containing `unreachable`
does not block". With the fragment gone that passes for the wrong reason: the
string stops blocking because nothing matches it, not because the guard caught
it. Three changes, in `sessions/session-5g.md`, the suite and
`agent/accept-5g.sh`:

- **The criterion now requires a live block fragment** in the wording, so the
  guard is provably the thing that stopped it. Four wordings, each a carrier
  describing a dead-number failure as temporary — "destination not routable via
  this route, will retry", "invalid phone number returned by the upstream lookup;
  temporary failure, retrying", and two more.
- **The discriminating property is asserted, not judged.**
  `test_every_transient_wording_would_block_without_the_guard` strips the
  transient markers out of each wording and requires the result to block. A row
  that carries no fragment fails that test rather than passing the one above for
  free. It uses only the public API — it asserts the property instead of
  re-checking my own arithmetic against the module's regexes.
- **The `"unreachable"` wordings moved to their own test**, named for the
  fragment removal rather than the guard, because three of the four carry no
  transient marker and the guard never sees them. Filing them under the guard is
  what made the old suite read stronger than it was. Twilio 30003 verbatim and
  decision 003's SMPP outage are both in it.

Criterion 3's before/after table now labels the two groups separately —
`A1 guard:` rows, where the guard is what changed the verdict, and
`004 dropped:` rows, where the fragment removal is. Both must be True at
`f16998b` and False here; conflating them was the original defect.

### Rider 2 — the residual, recorded at the site

A comment block at `AUTO_BLOCK_ERROR_FRAGMENTS` states the three meanings the
fragment carried, why the guard could not separate them, and the accepted cost in
plain terms: **a line the carrier describes only as "unreachable" survives and is
paid for again on every campaign** — about half a cent a blast, under two dollars
a year, against a bidder deleted permanently and invisibly. It names line-type
screening at import as the intended real fix and says not to rebuild a two-tier
rule, because decision 004 considered and rejected exactly that.

`agent/mutate-5g.py` gained **R11, "unreachable back in the block list"**, which
must break a test. The residual is a cost argument rather than an obvious one, so
the fragment is exactly the kind of thing a future session restores in good
faith; R11 is what makes that loud rather than quiet. Eleven mutations now, all
CAUGHT.

### Rider 3 — not done, deliberately

A structured transient-code set (30003 and siblings) stays open. The ruling puts
it behind a second carrier going live or Telnyx populating codes reliably on the
delivery path; today it would be a table of one entry that A4A's traffic has
never produced. `sms_messages.error_code` is the seam when it is time.

---

## Module 5e Part A — campaign-first flow & QoL (appended by session 5e, 2026-08-27)

**Status: Part A complete except the deploy.** Nothing in this session can send
a message: `SMS_PROVIDER` is `console` in `.env`, in `tests/conftest.py` and in
every subprocess the acceptance script starts. No live credential was read. No
contact data was imported, modified or deleted outside the suite's own scratch
database and a local dev database.

Gate green at both ends, twice in a row. **259 tests at start, 319 at end**
(+60: 17 campaign-first flow, 19 top-up and zero-send, 21 suppression window,
plus 2 white-label cases and 1 migration case in existing modules). One existing
test moved layer and two were made self-sufficient — see below.

### A1 — the upload is step one of creating a campaign

`POST /api/campaigns/from-upload` (multipart) commits the CSV through the
*existing* importer and creates the campaign on the list it produced.
`POST /api/campaigns/upload-preview` is the same `import_service.preview()` the
Contacts screen calls — reusing it is what makes the composer's counts and the
commit's counts the same counts.

- The list is named for the campaign, so a report three weeks later reads
  "Italian restaurants" rather than a list id. `ContactList.name` is unique, so
  collisions suffix (`… (2)`) rather than erroring: two campaigns of the same
  name is the ordinary case, and without the suffix the second is an
  IntegrityError partway through a commit with a list already written.
- **The import is rolled back if the campaign cannot be created.** Otherwise a
  rejected campaign leaves a named list and a few hundred contacts behind, and
  the next attempt collides with the name it just orphaned. `undo()` is the
  existing, tested reversal.
- **The spec's file reference is stale and was not followed literally.** A1 says
  to reuse `POST /api/contacts/import/preview` and `/import`; session 3b retired
  both, and they answer 400 naming `/api/imports/*`. The intent — reuse the
  importer, do not write a second one — is honoured through
  `import_service.preview()`/`commit()` directly.

### A2 — the category tag is optional on that upload

`import_service` takes `Optional[Category]` throughout. `category_id` stays a
**required positional argument with no default**, so `None` is a value somebody
passed rather than one that drifted.

- `/api/imports/*` — the standalone Contacts-screen import — **still refuses
  without a category**, and says so in a sentence rather than a 422. That import
  produces contacts and no campaign, so an untagged one is the untagged blob the
  category work exists to prevent. The campaign upload is different: the list it
  creates is the campaign's entire audience, so the targeting *is* the list.
- **`undo()`'s marker moved from `category_id` to `source`.** "No category means
  this is not an import batch" stopped being true the moment an upload could
  legitimately carry none, and an untagged campaign upload would have been told
  it was not an import — with no way to reverse it.
- A campaign from an upload records `category_id` NULL **and**
  `cross_category_override` 0. Recording it as an override would put a decision
  nobody made into the audit trail and make the column useless as evidence.
  `resolve_category()` gained a third way to satisfy the rule, not an escape from
  it: `POST /api/campaigns` is byte-for-byte unchanged and still demands a
  category or a typed override for `all`, `category:` and an existing list.

### A3 — one contact, by hand

`POST /api/contacts` gained a form, company, an optional category, and the two
guards an import has always had: E.164 normalisation, and a blocklist check. A
blocklisted number is **refused with a sentence naming Opt-outs**, not silently
skipped — unlike a 6,000-row file this is one number somebody deliberately typed.
Both guards run before anything is written.

### A4 — top-up send on an already-sent campaign

`POST /api/campaigns/{id}/top-up`, refusing **synchronously** before anything is
queued (5d's lesson from `POST /{id}/send`; here it matters twice over, because a
refusal after the rows were written would leave a completed campaign carrying
orphan `pending` messages). `campaign_topup.assess()` is the single verdict both
the router and the send path read.

- **"Added since" is read from `contact_list_members.added_at`, not inferred by
  subtracting message rows.** The review found two defects in the subtract-rows
  version, both live: a campaign capped with `batch_size` had its withheld
  remainder delivered to on one click, and a campaign on `all` texted everyone
  imported afterwards for a *different auction*. A top-up now needs a `list:`
  audience and says so.
- `add_to_list()` now stamps `added_at` explicitly. The column's server default
  is SQLite's `CURRENT_TIMESTAMP`, which is **UTC** while every other timestamp
  here is local — two clocks in one column, and comparing them made every
  hand-added contact look up to five hours newer than it was.
- The guard against re-sending is on the **phone number**, not the contact id: a
  contact deleted and re-imported is a new row with the same person holding the
  handset.
- `sms_messages.top_up_at` (migration `c4f1a80b6e37`, additive, nullable, nothing
  backfilled, in-place `ADD COLUMN`) keeps the addition distinguishable, so a
  report reads "1,200 + 5 added 26 Aug" rather than a silently different number.
  Counted by one grouped query for the whole rail, not one per row.

### A5 — the hold-back window is a Settings field

`app/services/suppression_service.py` — its own module, and the 500-line rule is
only the second reason. This is the one rule that decides whether a real person
gets a text they did not ask for, it is on the escalation list by name, and it
shipped at 3 days and withheld 6,856 of 6,857 recipients across two campaigns
before anyone could see what was doing it.

**The rule is not tuned.** The comparison, its blindness to category and the
lexicographic-on-ISO trick moved file verbatim. What changed is where the
*number* comes from: `.env` is the default, a stored value overrides it, and it is
read fresh on every call — so a change takes effect on the next send with nothing
to invalidate and no worker holding an old copy. Every function takes a Session
and none may default it, because a caller that forgot would silently fall back to
`.env` on that one path.

### A6 — suppression is visible before the campaign is queued

`/preview` and `/preflight` both return `suppression_days` and
`suppression_clears_at`, and the composer draws them. The clearing time is the
**latest** held-back contact's `last_messaged_at` plus the window: the question is
"when can I send this to all of them", and the earliest gives a time at which most
of the hold is still in force. At a window of 0 both are silent, and the checklist
row says the rule is *off* rather than reporting "nobody was texted in the last 0
days", which reads as a fact about the audience instead of about the rule.

### A7 — a run that reached nobody aborts loudly

`campaign_outcome.zero_send_reason()` — pure functions over counts, no session,
no writes. A run that put no message on a carrier ends `aborted` with a sentence
naming the cause: everyone suppressed, everyone opted out, everyone out of
region, everyone rejected, or a mixed breakdown that accounts for every recipient
(the remainder is named rather than dropped, because a breakdown that does not add
up invites "so the rest went out, then").

- Scoped to the **run**, not the campaign's lifetime. A top-up that reaches
  nobody keeps `completed` — the original blast really did reach 1,200 people and
  one later event cannot revoke that, the same argument that stopped a late
  failure webhook from un-delivering a message in 5d — but still stores a reason,
  fronted with "Top-up of N recipients:" so the sentence survives the badge next
  to it saying completed. A later successful top-up clears it.
- A deliberate dry run is not special-cased into looking like a failure, and a
  test asserts that by driving a successful console send.

### The 500-line rule, twice more

`campaign_service.py` crossed it for the third time, so **creation** moved to
`campaign_builder.py` — the seam is *deciding what a campaign is* against
*running it*, matching `campaign_dispatch.py` on the other side of *when a send
begins*. `CampaignService.create_campaign()` and `.resolve_category()` remain as
thin delegates so every existing caller keeps its entry point, and `CampaignError`
/ `NO_CATEGORY_ERROR` / `wholesale_estimate` are re-exported from their old
address.

`preflight_service.py` then crossed it too, and suppression moved out — see A5.

### Acceptance

`agent/accept-5e.sh` is the Part A stop condition — the eleven criteria from
`sessions/session-5e.md`, each a check that runs rather than a claim, with the
60-turn cap recorded in its header. Criteria 1-10 pass locally. Criterion 11 needs
the deployed site and is opt-in:

    A4A_URL=https://... A4A_PASSWORD=... bash agent/accept-5e.sh --with-remote

**Check 10 is the one with teeth**, and CLAUDE.md is explicit about why it is not
"the new tests fail against the pre-fix tree". `agent/mutate-5e.py` reverts each
fix behaviourally, one at a time, in a scratch copy, and requires a test to go
red for each — 28 mutations, none surviving. It earned its cost immediately:

- Two mutations survived the first run — `M2` mutated a default no caller
  reaches, and `M7` mutated a redundancy (`upsert_contact()` normalises
  internally). Both were retargeted at the behaviour that actually matters.
- A third was being "caught" by an unrelated test that leaked a stored setting
  between modules, which inflated every CAUGHT verdict it appeared in.
- `M18` was then genuinely **not** caught: the test called `check_recent_overlap`
  directly, so mutating `build_report`'s call site was invisible to it. It now
  drives `/api/campaigns/preflight`.

### What the fresh-context review changed

One synchronous reviewer, per the 5g lesson. Six real defects, five fixed here
and one escalated. Each fix has a test that fails when it is reverted (`M23`-`M27`).

1. **A top-up defeated the campaign's cap.** `batch_size` is applied at build
   time and never persisted, so "everyone the audience resolves to now, minus
   everyone with a message row" could not tell the withheld remainder from
   somebody added since. A campaign capped at 2 of 10 offered to send to the
   other 8 on one click.
2. **A top-up on an `all` or `category:` audience texted everyone imported
   since.** Last month's memorabilia message to tonight's restaurant buyers, one
   click, behind a confirm dialog that said "everyone added to its list" about a
   campaign with no list. Both fixed by A4's `added_at` window above.
3. **"Run checks" ran pre-flight against the wrong audience.** `refreshPreview()`
   guards on upload mode and `runPreflight()` did not, while the audience
   dropdown defaults to "All contacts" — so the checklist drew a full report,
   capacity verdict included, over every contact in the database, under a
   composer whose audience was a file. The same shape as `should_auto_block()`
   having one call site for two paths.
4. **A7's reason recommended a remedy that cannot work.** It said "lower the
   hold-back window, or wait for it to clear"; suppression is frozen into
   `skipped` rows at build time and nothing moves a campaign back from `aborted`,
   so a client following that advice watches the same campaign fail the same way
   — and may already have had the window at 0. It now says to create the campaign
   again.
5. **The manual-add form accepted a `category_id` that named nothing** and
   answered `tagged: true`. `tag_contact()` validates the tag's source and never
   the category, and SQLite does not enforce the FK here, so it wrote a dangling
   row that would then keep the contact alive forever through
   `_still_referenced()` on an undo.
6. **A test was vacuous when run alone.**
   `test_the_checklist_row_and_the_summary_panel_quote_one_window` read a list a
   previous test had seeded, so in isolation two of its three assertions compared
   `0 == 0` — and `accept-5e.sh` runs each criterion's tests in isolation, so it
   reported green while proving nothing. It seeds its own contacts now, and
   asserts the hold is non-empty before comparing.

The reviewer independently confirmed: `count_sms_segments()`, `BILLABLE_STATUSES`,
`billing_service.py`, the rounding and the billable-status set are untouched;
`ix_contacts_phone` and every `sms_messages` index survive; the suppression *rule*
is not tuned; no new client-facing string names the carrier; there is no import
cycle among the new service modules; and no refusal path leaves orphan `pending`
rows.

### Found while working (session 5e)

- **ESCALATED —
  `decisions/005-topping-up-a-contact-the-window-held-back.open.md`.** A contact
  the window held back gets a `skipped` row, and `skipped` also means "region not
  enabled" — two meanings on one column, and a top-up has to tell them apart. So
  a buyer who was merely *deferred* can never be reached inside that campaign,
  even after the hold clears. Escalation item 5, and the fix needs a new
  `MESSAGE_STATUSES` member. Not urgent while the window is 0 (production's
  setting); urgent the moment anybody raises it.
- **The capacity check is inert for a top-up of one or two segments.**
  `wholesale_estimate()` rounds to 2dp, so ~$0.004 becomes `0.0`, `required`
  becomes `0.0`, and `balance < 0.0` is never true. Pre-existing — it is true of
  any campaign that small — but 5e's `preflight(segments=…, cost=…)` override
  makes small numbers reachable on a new path. **Not changed here: the pre-flight
  capacity check is escalation item 3** and the correct fix is a floor on
  `required`, which is a change to the guard. Impact is a few failed messages, not
  a half-sent blast, and the degraded-path check still runs first.
- **`undo()`'s new `source == "csv"` marker also matches lists built by
  `ContactSource.ingest()`** (`app/sources/base.py:72`), which sets
  `source=self.name`. No caller exists today and `add_to_list()` never sets
  `created_contact`, so nothing would be deleted — but the prospecting engine is
  scheduled and will add sources. Worth a dedicated column when it does.
- **`campaign_service.py`'s submission path still files everything as
  `delivery_failure`.** Carried over from 5g, which left `should_auto_block()` a
  one-argument signature because that file belonged to 5e. This session opened it
  and did **not** take the fix: it is three lines plus a field on `SendResult`,
  and it is blocklist behaviour — escalation item 5 — on a path 5e was not sent to
  change. Still open, still bounded by the webhook path carrying the
  overwhelming majority of failures.
- **`assess()` runs twice per top-up**, once in the router to refuse
  synchronously and once on the path that does the work. That is deliberate — the
  check has to live where the work happens — but each pass resolves the campaign's
  list. Fine at 6,857 rows; worth remembering at 50,000.
- **Two rapid "Top up" clicks are safe but silent.** Both can pass the router's
  `assess()`, and the second background task then sees `status == "running"` and
  refuses — so the top-up is dropped rather than duplicated, with nothing on
  screen saying so.
- **`docs/API.md` documents none of 5e.** `POST /api/campaigns/from-upload`,
  `/upload-preview` and `/{id}/top-up` are new; `/preview` and `/preflight` gained
  `suppression_days` and `suppression_clears_at`; `GET`/`PUT /api/settings/suppression`
  are new; the campaign payload gained `top_ups`. 5d updated that file because its
  spec named it (A6); 5e's does not, and docs are module 8's. Worth doing before the
  client's integration, such as it is, and before 5f adds more.
- **`_composer-script.html` is at 493 lines and `contacts.html` at 488.** Both under
  the rule and neither has room for the next feature. The composer's natural next
  split is the pre-flight checklist renderer, which is self-contained; Contacts'
  is the bulk-action bar. Flagged now because the 500-line rule has twice produced a
  better design when it forced a split and twice been discovered mid-session.
- **`record_click()` increments its counters in Python, not in SQL.** Safe today:
  `deployment/app.service.template` starts uvicorn with a single worker and the
  handler calls the recorder inline, so no two clicks interleave. Raise
  `--workers` and it becomes a read-modify-write across processes, and the loser
  of the race under-reports a click on the client's report while the
  `link_clicks` row it came from is still there. The rows are the record; the
  counters are the fast path. Fix by making it `UPDATE … SET click_count =
  click_count + 1` if that day comes.
- **A phone with two message rows on one campaign gets two links, and the report
  would count it as two clickers.** Nothing in the product creates that state —
  5h's `R7` mutation had to construct it directly — but `clickers` is a count of
  links with a click rather than of distinct contacts, and those stop being the
  same number the moment it can. Worth knowing before anything is built that
  writes a second row for one recipient.
- **The live box has still not been deployed to.** Criterion 11 cannot pass until
  someone does. The box still runs the pre-5c nginx config, the hot-patched SDK,
  and pre-5d application code.

---

## Module 5h Part A — held-back rows & the capacity floor (appended by session 5h, 2026-08-30)

**Status: Part A complete except the deploy.** Nothing in this session can send
a message: `SMS_PROVIDER` is `console` in `.env`, in `tests/conftest.py` and in
every subprocess the acceptance script starts. No live credential was read. No
contact data was imported, modified or deleted outside the suite's own scratch
database.

Gate green at both ends, twice in a row. **319 tests at start, 359 at end**
(+40: 14 held-back release, 26 capacity rounding). Three existing tests changed —
see below. No source file over 500 lines; `campaign_topup.py` crossed it and
`campaign_release.py` is the split.

### A1 — `held_back` is its own status

Implements `decisions/005-topping-up-a-contact-the-window-held-back.md`, option 2,
with all four riders.

- `held_back` added to `MESSAGE_STATUSES`, **outside `BILLABLE_STATUSES`** — the
  same shape as 5d's `not_sent`, and for the same reason: a message that never
  reached a carrier is not a segment. `campaign_builder.py` writes it for a
  contact the suppression window holds back, so `skipped` goes back to meaning
  only what `sms_message.py` has always said it means.
- **A top-up releases a hold that has cleared.** Contacts whose *only* row on
  this campaign is `held_back` are re-adjudicated against **today's** window and
  their existing rows **flipped** to `pending`. `campaign_release.py` owns the
  candidate rule; `campaign_topup.py` owns the flip, because a partial write is
  the outcome that module exists to prevent.
- **No backfill, and it is recorded where a reader will look for it.**
  Migration `e2a7c3d15b48` changes no schema — `sms_messages.status` is a plain
  VARCHAR — and exists solely to state that pre-5h `skipped` rows cannot be
  classified after the fact and must never be guessed at. A row guessed
  *held back* becomes a text to a number the region filter excluded.
- The released row keeps **its own body**. It was rendered for that contact by
  this campaign; re-rendering would quote one message and queue another.
- `error_message` is cleared on release: a sent message still carrying "held back
  so nobody gets two messages in a row" is a lie in the client's own log.
- The campaign's counters move **both ways**. A released contact is added to
  `total_recipients` and subtracted from `suppressed_count`/`skipped_count`, or
  the rail reads "6 recipients · 2 held back" about a campaign that reached six.

**The release does not require a `list:` audience, and the "added since" half
still does.** That is a widening of 5e's refusal and it is deliberate.
`NOT_A_LIST_AUDIENCE` exists because "everyone who has joined that audience
since" is a set nobody chose — last month's memorabilia message to tonight's
restaurant buyers, which is the defect 5e's review found live. A held-back row is
the opposite: this campaign resolved that contact itself, counted them, and put
the number on screen before the send. Refusing to release them because the
audience was not an upload would leave decision 005's defect standing on every
campaign that was not built from one. The refusal still fires when there is
nothing to release.

**Scope note — three files outside the table's 5h list were edited.**
`campaign_topup.py` because A1's own third bullet is a change to the top-up;
`routers/campaign_uploads.py` and `_composer-upload.html` because both told the
client a top-up sends "to everyone added to its list since it went out", which
stopped being true. `top_up_summary()` owns the sentence now and the endpoint
renders it verbatim — a released contact is not a new one, and describing them as
one sends him looking for an upload he never made.

**One reporting fix came with the status split.** `top_up_history()` counted a
top-up's *held-back* rows as recipients, while `total_recipients` never has — so
the rail reported the original send as smaller than it was, in the direction
nobody sanity-checks. Two lines, and they were unwritable before this session:
the query could not tell a top-up's held-back row from a top-up's sent one.

### A2 — the capacity guard compares exact money

`wholesale_cost()` is the new exact `Decimal`; `wholesale_estimate()` is it
rounded, and is now for the Float column and our log line only.
`capacity_assessment()` compares `Decimal` on both sides.

**The session spec's premise does not hold at this box's own rate, and the
criterion was demonstrated at both.** A2 says a small send "can require $0.00 and
pass the capacity check on an empty account". At
`WHOLESALE_COST_PER_SEGMENT=0.009` — `.env`, `.env.example` and the code default,
and `.env.production` does not override it — one segment estimates at
`round(0.009, 2)` = **$0.01**, so that send was already refused. The zero case
needs a blended rate under half a cent; 5e's note quoting "~$0.004" was using the
line-type lookup price from `compliance.py`, not the segment rate. What *is* wrong
at 0.009 is that rounding down loses up to three quarters of a cent of
requirement — six segments ask for $0.075 against a true $0.081 — so a campaign
can start on a balance that does not cover it. `agent/accept-5h.sh` check 6b
prints the before/after verdict at both rates rather than asserting the spec's
version of events.

**The requirement is the larger of the exact cost and the caller's stated one,
and that is not belt and braces.** Replacing the rounded figure with the exact
one *loosens* the guard at every count where rounding went up — about half of
them. The pre-flight capacity check is escalation item 3, which the ruling
permits tightening and nothing else, so `max()` is what makes the change
monotonic. `test_the_fix_only_ever_tightens` runs the pre-fix arithmetic and
asserts it: nothing the old check refused now starts.

**The audit (criterion 7): one site, and it was this one.** Every `round()` in
`app/` was walked by AST rather than grepped — three of the eight matching lines
are prose inside docstrings *about* rounding — and the five real call sites are
four percentages and `required_segments`, which is a segment count computed
*after* the comparison and never fed back into it. `billing_service` was already
Decimal end to end since 1b; `marginal_cost()` subtracts before rounding;
`monitoring_service.check_low_balance()` and `routers/usage.py` compare raw
provider floats against a raw threshold with no rounding anywhere. **No other
site rounds a money value before comparing it.** `accept-5h.sh` check 7 freezes
that list so the next one has to be declared.

### The three existing tests that changed

1. `test_suppression_excludes_two_days_and_includes_five` asserted the held-back
   row was `skipped`. It now asserts `held_back` **and** that it is not
   `skipped` — the two halves fail differently, and a build that wrote neither
   would satisfy an `!=` on its own.
2. `test_a_suppressed_message_is_never_billed` — same rename. The reason it is
   unbilled did not change.
3. `test_a_top_up_refuses_an_audience_that_is_not_its_own_list` pinned the exact
   `NOT_A_LIST_AUDIENCE` sentence. Its campaign's audience is `all`, so it
   resolves to every contact in the shared test database and holds back whichever
   of them the modules that ran first had texted — which is now a second, equally
   correct refusal. It asserts the property it exists for instead (nobody
   imported for a different auction is a candidate, by either route), and the
   sentence is pinned in `test_held_back_release.py` on a campaign constructed
   with no rows at all. Same lesson as 5g's `OPT_OUT_REASONS` test.

### Acceptance

`agent/accept-5h.sh` is the Part A stop condition — the nine criteria from
`sessions/session-5h.md`, each a check that runs rather than a claim, with the
60-turn cap recorded in its header. Criteria 1-8b pass locally. Criterion 9 needs
the deployed site and is opt-in:

    A4A_URL=https://... A4A_PASSWORD=... bash agent/accept-5h.sh --with-remote

**Check 8b is the one with teeth.** `agent/mutate-5h.py` reverts each fix
behaviourally, one at a time, in a scratch copy, and requires a test to go red
for each — 18 mutations, none surviving. It earned its cost on the first run:

- `R7` (two held-back rows for one phone are queued twice) was **not caught**.
  Nothing in the product can create that state, so no test had reached the
  guard. It is tested now by constructing the row directly — a guard that only
  holds while a *different* guard holds is one refactor from not holding.
- `R16` (held-back rows counted as top-up recipients again) was **not caught**,
  because the end-to-end test's top-up held nobody back. The new case runs a
  top-up that releases one contact and holds another in the same run, which is
  the only shape where the two counts can disagree.
- The mutation that would have been `R15` — "compare the balance as a float
  again" — was dropped rather than made to pass. `float(exact_required)` and
  `Decimal(str(balance))` order identically at every magnitude a carrier balance
  can hold; what carried the defect was the rounding, which `R13`/`R14` catch.
  The Decimal comparison is hygiene, and hygiene with no reachable mutation is
  worth saying out loud rather than dressing up as a green tick. Both the harness
  and `campaign_service.py` say so at the line.

### What the fresh-context review changed

One synchronous reviewer, per the 5g lesson. Five real defects and one
misreading; three fixed here, one escalated, one recorded.

1. **A capped campaign released everything the window held back.** The serious
   one, and the same shape as the defect 5e's review found one set along: the
   release guard was correct and the set it filtered was wrong. `batch_size` is
   applied to the *sendable* set at build time, never to the held-back rows, so
   a campaign capped at 3 with 10 held back sent to 13 on one click — at this
   client's numbers, a cap of 50 queueing six thousand messages. Reproduced,
   then closed in the safe direction: `campaigns.batch_size` is now recorded
   (migration `a91d5f2c6b70`, additive, nullable) and `releasable()` returns
   nothing for a campaign that carries one. **That is the absence of a change,
   not a ruling** — it is what a capped campaign did before 5h — and the
   question of what it *should* do is
   `decisions/006-which-campaigns-may-release-a-hold.open.md`.
2. **ESCALATED, same decision file: a campaign the window suppressed *entirely*
   ends `aborted`, so its held-back rows can never be released.** Decision 005's
   own example is 6,856 of 6,857 — one send, so `completed`, so the release
   works. One more suppressed contact and it does not. Fixing it means either
   moving a campaign out of `aborted` (which `TOP_UP_STATE_ERRORS` warns against
   by name, on decision 002's reasoning) or storing why it aborted. Both are
   policy. Recorded at the site in `campaign_outcome.py` as well as in 006.
3. **"Held back" was said about people the window was not holding.**
   `releasable()`'s `still_held` was "everything not clear", which swept in rows
   whose contact no longer exists — `import_service.undo()` deletes contacts, so
   this is reachable. With the window at **0** the refusal still read "was texted
   recently and is being held back", which is a sentence about a rule that is not
   running: the same defect as a failed carrier reporting a chosen dry run. Three
   buckets now, and no sentence blames the window for a contact who left.
4. **A test was vacuous.** `test_a_widened_window_does_not_un_send_anybody` built
   its campaign with the window at 0, so it had no held-back rows and asserted
   `[] == []` twice — 5e's own vacuous-in-isolation defect, written again. It now
   holds one contact from the start and asserts the precondition.
5. **`test_a_suppressed_message_is_never_billed` read a row its neighbour left
   behind**, and `agent/accept-5h.sh` running each criterion in isolation is what
   found it — the third time that discipline has caught a test leaning on another.
   It builds its own campaign now. Pre-existing, from module 4.
6. **One finding was a misreading and is recorded rather than acted on.** The
   reviewer read `_pre_fix_verdict()` in `test_capacity_rounding.py` as failing to
   model the old arithmetic, on the grounds that `wholesale_estimate()` rounds
   half-up. It does *now* — this session changed it from Python's banker's
   `round()`, which is what the helper reproduces. The reviewer re-ran the sweep
   against what it believed was the real pre-fix boundary and found the new check
   still refuses at every one, so the property holds either way. The helper now
   says in its docstring why it must not be "corrected" to call
   `wholesale_estimate()`: doing so would compare the fix against itself.

The reviewer independently confirmed: no double-send on any path it could
construct; a released row still meets the blocklist and region filters (driven
with a post-hold STOP — final status `blocked`, nothing sent); the counters hold
across three successive top-ups at descending windows; `count_sms_segments()`,
`BILLABLE_STATUSES`, `billing_service` and the suppression *rule* are byte-for-byte
untouched; `ix_contacts_phone` and every `sms_messages` index survive; the
migration applies, reverses and re-applies on a clean database; no new
client-facing string names the carrier; and — brute-forced over 200,000 segment
counts at eight rates — the capacity check is never more permissive than the one
it replaced, the worst gap being one float ULP.

### Implementing decision 006 — the two sentences a hold produces (2026-08-30)

Option 1 for both cases, as ruled: a capped campaign releases nobody, and a
campaign the window held *entirely* stays `aborted`. **No state change, no new
column, no new transition** — the ruling's whole point is that the defect was in
the sentence. 359 → 366 tests, gate green twice, `agent/mutate-5h.py` at 27.

**Why the ruling went against the escalation's own recommendation, recorded
because the reasoning is not obvious.** 005's scenario is 6,856 held and *one
sent*: rebuilding loses the delivery record, the cost and the campaign's place
in history, which is what made "create a new campaign" unacceptable and
justified the whole release mechanism. A 100%-suppressed campaign sent **zero**
messages — there is nothing to preserve, so a fresh campaign built after the
hold clears is identical to what a release would have produced. Option 3 would
have bought a few clicks with a new column and the first code in this repo to
move a campaign out of `aborted`.

**Case 2 — the abort reason.** `campaign_outcome._all_held_back()`: the cause
("all 2,140 contacts were texted in the last 3 days and held back"), the
clearing time ("The hold clears at 10:11am."), and the remedy that works
("Create the campaign again after that; this one cannot be restarted"). The
clearing time is `max(last_messaged_at)` across the held set plus **today's**
window — `campaign_release.hold_clears_at()`, which calls the same
`suppression_service.suppression_clears_at()` the composer uses one screen
earlier. Latest, not earliest, for A6's own reason: the earliest names a time at
which most of the audience is still held, which reads as a promise.

**A6's phrasing is reused by moving the renderer, not by copying it.**
`preflight_service._clock()` became `suppression_service.clears_at_clock()` and
the old name delegates. Two sentences about one instant that render it
differently are worse than one sentence, and 006 asks for A6's wording by name.

**Case 1 — the capped refusal.** `CAPPED_CAMPAIGN_HOLD` was a constant and is
now `campaign_release.capped_campaign_hold(cap, held, clears_at)`, naming both
numbers, the remedy, and — after review — when the remedy will work. It lives in
`campaign_release` because that module makes the decision it explains, which is
the `send_mode()` pattern, and because `campaign_topup.py` crossed the 500-line
rule again.

**Both clauses are dropped rather than invented when unknown.** A contact whose
`last_messaged_at` will not parse still counts as held, and
`suppression_clears_at()` returns None for it; the sentence then loses the time
and switches its remedy to "once the hold clears". Never `None` on screen, never
"the last 0 days" — a wrong clearing time is a date the client plans an auction
around.

**And when the hold has already lifted, the remedy changes.** Held rows are
frozen when the draft is built and the run can happen days later, so the
campaign can have nothing to send while the people it held are perfectly
reachable — a draft saved Monday and sent Friday, a scheduled send, or the
window switched to 0 on the Settings screen. Both now read "That hold has since
cleared. Create the campaign again now." Found by review; the first version
printed a clearing time three days in the past, in the present tense, and told
him to wait for it.

**`sessions/session-5h.md` A2 is struck through**, per RULES: 006 records that
the spec's `$0.00` premise was wrong at the configured rate. Struck rather than
rewritten, so the ruling's own point — that it was an instance of "a rationale
and its mechanism have to be checked against each other" — is not hidden by a
silent correction. Three lessons went into `CLAUDE.md`.

### What the second fresh-context review changed

One synchronous reviewer over the 006 work. Six real findings; five fixed, one
disagreed with in part.

1. **The gate was red and I said it was green.** Two files over the 500-line
   rule — `campaign_topup.py` at 506, `campaign_service.py` at 503 — because I
   ran the gate *before* the 006 edits and reported that run. It is the whole
   reason `agent/gate.sh` exists and I did not re-run it. `capped_campaign_hold()`
   moved to `campaign_release.py`, where the decision it explains is made, and
   the A2 comments in `capacity_assessment()` were consolidated with the
   docstring that already said the same thing. 482 and 499 now.
2. **The abort reason told the client to wait for a moment that had gone.** The
   held rows are frozen at build time and the run can be days later, so a draft
   saved Monday and sent Friday printed "The hold clears at 27 Aug" in the
   present tense about people who were then reachable. A window changed to 0 on
   Settings produced the same shape. `_still_ahead()` splits the three cases and
   two tests drive them through the real send path.
3. **`test_the_reason_never_quotes_a_window_of_zero_days` asserted the branch was
   unreachable, and it was reachable.** The docstring's premise was checked
   against the mechanism only after review — inside the change that cites that
   lesson.
4. **The capped refusal's remedy reached nobody.** "Create a new campaign without
   a cap" while the window still holds them builds a campaign that holds them
   again. It now names the clearing time and says so. My own docstring had
   argued the opposite — that these contacts "are not waiting on the window" —
   which the sentence contradicted three clauses earlier.
5. **"all 1 contacts were texted in the last 1 day".** The day was singularised
   and the count was not, and the test named for plurals pinned the broken half.
6. **The new lookups could strand a campaign in `running`.** The zero-send
   adjudication was pure arithmetic before 006 and now issues four queries
   between the loop finishing and the status being written. `hold_clears_at()`
   returns None rather than raising — the `/health` pattern from 5g — and the
   two facts are only read on the branch that uses them.

**Partly disagreed with, and recorded rather than acted on.** The reviewer
observed that `test_the_abort_reason_and_the_composer_row_quote_one_moment`
compares one function against itself and so cannot catch finding 2. That is
true, and it is what the test is for: after 006 there is one implementation, and
the assertion pins that neither surface has grown a second. Finding 2's coverage
is the two new tests. Same shape as the R15 gap — a property worth stating and
worth being honest about the strength of.

### One red gate I could not reproduce

Recorded because a suite that is green five times out of six is exactly what this
project treats as a defect rather than noise, and because the next session should
know it was seen.

`agent/gate.sh` reported **"test suite is red"** once, on a run issued in the same
shell command as a full `pytest tests/` and while `agent/mutate-5h.py` was still
running in the background. The gate's own pytest emitted no `N passed` line, which
is consistent with a collection error rather than an assertion failure. I did not
capture the output — the invocation filtered it — which is the actual mistake here.

Six runs since, all green: three with nothing else in flight, one deliberately
raced against a mutation run, and one repeating the exact pytest-then-gate
sequence. No shared-state candidate holds up on inspection either: `conftest.py`
takes a `tempfile.mkstemp` database per run, `monitoring_service.STATE_FILE` is
under the tree's own `data/` and the mutation harness's scratch tree has its own,
and the harness passes a restricted env to every subprocess.

So: unexplained, not reproduced, and not attributed to a cause I cannot show. If
it recurs, capture the full gate log before doing anything else.

### Found while working (session 5h)

- **`skipped` still carries two meanings on rows written before this change**,
  and always will. That is decision 005 rider 2 and it is not a defect to fix
  later — the rows cannot be classified. Anything reading that column has to know
  the change has a date.
- **A capped campaign's held-back rows stay held, and a fully-suppressed
  campaign's can never be released at all.** Both **ruled** by `decisions/006`,
  option 1: correct as they stand, and both sentences rewritten to say so. What
  006 asks to be watched for is case 2 becoming routine — same list, consecutive
  days, an auction cadence that collides with the window. At that point the
  rebuild stops being a few clicks and `abort_kind` is the right answer, and 006
  says to reopen the decision rather than work around it. **The window is 0 in
  production, so nothing can hit this yet; the moment it is raised, check this
  first.**
- **`campaigns.batch_size` is recorded but read by exactly one caller.** It
  exists because the cap was being applied and discarded, which both 5e and 5h
  hit from opposite sides. Nothing else consults it, and the composer still
  sends it as a request rather than reading it back.
- **`assess()` still runs twice per top-up** and now resolves the held-back set
  on each pass as well. Carried over from 5e, same reasoning (the check has to
  live where the work happens), same note: fine at 6,857 rows, worth remembering
  at 50,000.
- **A released row's `top_up_at` says when it was released, not when it was
  written.** Deliberate and documented on the column: the stamp funds one report
  — how the recipient count reached the number on screen — and a released row
  joins that count on the day it goes out. If a later consumer needs "when was
  this row created", that is a different column and it does not exist yet.
- **The campaign rail shows no held-back figure for a top-up that held somebody
  back.** `suppressed_count` moves and the rail renders it, but the "N held back"
  text does not distinguish the original send's hold from a top-up's. 5f owns
  reporting; this is a wording question rather than a correctness one.
- **`preflight_service.py` was not touched**, though the 5h file list names it.
  Nothing in either A-item reaches it: it re-states `capacity_assessment()`'s
  verdict rather than computing one, so the exact comparison arrives there for
  free, and `check_recent_overlap()` describes the window rather than the rows.
  Stated because a file listed and untouched otherwise reads as an oversight.
- **The submission path's block reason and code** are still open, carried from 5g
  and 5e. Unchanged here — blocklist behaviour is escalation item 5 and no 5h
  criterion touches it.
- **The live box has still not been deployed to.** Criterion 9 cannot pass until
  someone does. The box runs the pre-5c nginx config, the hot-patched SDK, and
  pre-5d application code.

---

## Module 5f Part A — short links, click stats and reporting (appended by session 5f, 2026-08-31)

**Status: Part A complete except the deploy.** Nothing in this session can send a
message: `SMS_PROVIDER` is `console` in `.env`, in `tests/conftest.py` and in
every subprocess the acceptance script starts. The one carrier stub that reports
a cost (`tests/_link_setup.CostingProvider`) returns a `SendResult` and touches
no network. No live credential was read, no contact data was imported, modified
or deleted outside the suite's scratch database, and `.env` was not written —
`SHORT_LINK_DOMAIN` is set on the settings object by a context manager that
always restores it.

Gate green at both ends, twice in a row. **371 tests at start, 424 at end**
(+53: 26 short links, 17 click filtering, 10 reports and cost).

### A1 — short links, one per recipient

`short_links` and `link_clicks`, plus `link_service.py`. One link per contact per
campaign, minted at creation, because the whole value of the feature is *which*
buyers clicked — "340 clicks" is a statistic and "these 340 people" is a phone
list. About 4,200 rows on a full send, against a table that already carries a
message row each.

- **One hop, never a chain.** `validate_target()` refuses a non-http(s) target, a
  public shortener (that is hop two, and it is also how *our* domain ends up
  filtered for pointing at one), and a link on our own short domain. The route
  answers a single 302 to the stored target.
- **302 with `Cache-Control: no-store`, not 301.** A permanent redirect is cached
  by the handset and by every proxy in between, so the second click never arrives
  and the report under-counts everyone who looked twice.
- **Closed minting.** There is no endpoint that creates a link; campaign creation
  does, and it is behind `require_auth`. The public route resolves and never
  creates. An open redirector is found and abused within weeks, and the price is
  the branded sending domain on a carrier blocklist.
- **Slugs are 8 characters from a 32-letter alphabet with no `0/O/1/l/I`** — a
  slug gets read out loud — and are checked against the table in one `IN` query
  per batch rather than one per row.
- **The redirect does not use `Depends(get_db)`**, for `/health`'s reason: a
  dependency that raises means the handler never runs. And `record_click()` cannot
  raise; the route wraps it in a *second* try anyway, so a future edit that puts a
  raise back costs the click data and not the click-through.
- `target_url` is stored on every link as well as on the campaign. The campaign's
  is the current destination a top-up mints against; the link's is where that
  message's link actually pointed, which is what a report needs after the auction
  page is gone.

### A2 — scanners are marked, not discarded

`click_classifier.py`. Named crawlers and appliances match unanchored
(`Googlebot/2.1` has no boundary before its `bot`); generic words are
`\b`-anchored, because `CUBOT_X20` is an Android handset that appears in real
user agents and an unanchored `bot` files that buyer as a robot forever. A timing
rule (`CLICK_MIN_HUMAN_SECONDS`, default 8) is the backstop for a scanner wearing
a browser's user agent.

**Which way this matcher should fail is the opposite of the auto-block list, and
the file says so.** `AUTO_BLOCK_ERROR_FRAGMENTS` triggers an irreversible action
so it is narrow; this one triggers a *presentation* choice with both numbers on
screen and nothing discarded, so it is allowed to be wider. Every click row keeps
its user agent and the rule that fired.

Reports lead with the human count and print the filtered one beside it in words —
"340 clicks (12 filtered as automated)". A bare number that quietly excludes
things is the defect, not the filtering.

### A3 — the merge tag, and the count that matters

`{link}` renders to each recipient's own URL. `message_render.render()` fills it
only when a link is supplied and leaves the literal `{link}` otherwise — the same
rule as any unknown placeholder, so a caller that forgets ships a visibly broken
message rather than a link that quietly points somewhere wrong.

**Pre-flight measures the rendered link.** It cannot mint 4,200 rows on every
press of "Run checks", so it renders with `link_service.placeholder_url()`, which
is the same length *by construction* — same domain, same `SLUG_LENGTH` — and a
test pins that. Without it the quote is short by nine characters a message; a
template that measures as one segment lands as two.

The refusal is at **compose** time and never at send time: no
`SHORT_LINK_DOMAIN`, or no destination, and `resolve_link_target()` raises before
the audience is resolved, so nothing is created and no link is minted. The
composer draws the same sentence, because `preflight_service.check_short_link()`
renders `link_service`'s wording verbatim — the `send_mode()` pattern.

**The composer's live keystroke counter measures the link too**, which A3 asks
for and the first cut of this session did not do. `link_service.for_counting()`
substitutes the placeholder before `describe()` sees the template, in
`/api/campaigns/preview` and in `build_report()`. Without it the "Segments / msg"
figure read 1 while the phone preview an inch to its right showed a message that
is 2, and the pre-flight total said 4 — a screen contradicting itself, which is
5d's Opt-outs headline one screen over. The *checks* still read the raw
template: they are about the copy he wrote, and a slug in the body would be a
slug in the category-keyword scan.

`{` and `}` are GSM-7 *extended* characters and cost two septets each, so
`{link}` is eight septets and not six. The test grows its template to the segment
boundary rather than hardcoding 160 characters, because a length-based literal
there is wrong in the direction that makes the test pass anyway.

### A4 — what the carrier actually charged

`SendResult` gained `cost`, `cost_rate`, `cost_carrier_fee`, `cost_currency`; the
Telnyx provider reads them off `data.cost` / `data.cost_breakdown`;
`sms_messages` stores them as **strings, exactly as reported**, and
`cost_reconciliation.py` sums them in `Decimal`. A Float column would put a
binary expansion between the carrier's figure and ours before four thousand of
them are added up — the 1b lesson, one layer along.

**None is not zero.** A message the carrier priced at nothing and a message it
did not price are different facts, and `coverage` is the first field the
reconciliation returns: a total from twelve priced rows out of 4,200 is not a
campaign's cost. Most carriers price at delivery rather than at submission.

**The console provider still reports no cost.** A dry run spends nothing, and a
plausible invented figure would look exactly like a measurement inside the
reconciliation — the same argument as `get_balance()`'s 999,999 being the wrong
answer on a degraded box.

**It is operator-only, and that is a reading of criterion 7 rather than a
shortcut.** The criterion says "the campaign report reconciles estimate against
actual"; the estimate is `WHOLESALE_COST_PER_SEGMENT`, which must never reach a
response body, a template or an export, and the admin login *is* the client. So
"operator-only" cannot mean "behind auth" — it means not served.
`scripts/cost_report.py` and one INFO line per finished campaign are the readers.

### A5 — the per-campaign report

`/history/{id}`, from `report_service.campaign_report()`: recipients, sent,
delivered, failed, held back, blocked, opt-outs since the send, clicks (people
and taps), click-through, cost, top-up contributions, and the abort reason
**verbatim** — 5h and decision 006 settled what a refusal says and a paraphrase
here would be a third sentence about one fact.

- **Cost is marginal, not `segments × rate`.** The plan includes 10,000 segments
  a month, so a campaign the allowance swallowed cost nothing and quoting the
  flat rate would show him a bill he never received. It prices the campaign as
  the last thing in its own cycle, from `billing_service`'s exact Decimals, and
  the screen says "Added to August" rather than "cost". The consequence is
  written down at the function: two campaigns in one cycle are each priced as the
  marginal one, so they do not sum to the cycle total when the allowance is
  crossed between them. That is a property of an allowance; the Usage screen
  still holds the number that is billed.
- Opt-out attribution is by phone and by time — blocked after this campaign
  started, among the numbers it texted. A STOP arrives on a webhook that knows
  nothing about campaigns, so anything cleverer would be invented. The label says
  "Opted out since".
- Exportable as CSV, streamed, recipients read in pages. It is the artefact that
  leaves the building, so the white-label scan covers it.

### A6 — campaign history and message history

`/history` (campaigns, newest first) and `/contacts/{id}/history` (one person:
what they were sent, when, whether it arrived, whether they clicked). The Contacts
screen's name column links to the second, which is the entry point that made it
reachable. **History is back in the nav**, per `base.html`'s own instructions for
restoring one.

Both paginate, and the test asserts the *comparison* rather than an absolute
bound: two rows against eight must cost the same number of queries. An absolute
bound alone passes happily on a per-row lookup as long as the fixture is small,
which is how an N+1 ships under a green suite. Measured: 5 queries for a page of
campaigns at either size, 3 for a page of messages at either size.

### The 500-line rule, three times

`campaign_service.py` reached 529 with the link-aware renderer, so `render()`
moved verbatim to **`app/services/message_render.py`** — the seam is *what a
message says* against *whether and how it is sent*, matching `campaign_builder`
and `campaign_dispatch` on the other two. `preflight_service.py` reached 510 with
the link check, so `exact_segment_totals()`, `marginal_cost()` and
`cost_estimates()` moved to **`preflight_totals.py`**: measuring against judging,
and nothing there returns a verdict. `campaign_topup.py` reached 542, so
`top_up_history()` moved to **`report_service.py`**, where it belongs anyway — it
is a reporting query, not part of running a top-up. All three keep re-exports at
the old addresses, because that is where every caller reaches for them.

### Acceptance

`agent/accept-5f.sh` is the Part A stop condition — the eleven criteria from
`sessions/session-5f.md`, each a check that runs rather than a claim, with the
60-turn cap recorded in its header. Criteria 1-10 pass locally. Criterion 11 needs
the deployed site and is opt-in:

    A4A_URL=https://... A4A_PASSWORD=... bash agent/accept-5f.sh --with-remote

Check 8b is by AST rather than by grep, and its first draft is why: a plain grep
failed on `routers/reports.py`, whose *docstring* says these fields must never be
returned. A check that counts prose about a rule as a violation of it is the
same defect `accept-5h` check 7 already had to fix once. The one real reader —
`routers/usage.py`'s balance-to-capacity divisor, deliberate since session 1b —
is now declared by name and printed as allowed, so a *new* one fails.

Check 2b walks the redirect and prints every hop rather than asserting a status
code, and check 10 is `agent/mutate-5f.py`: 31 behavioural mutations, each a
plausible edit inside the current API, each required to break a test that names
it. The harness earned its cost on the first run — `R3` (pre-flight measuring
the tag rather than the rendered link) was **not caught**, because the test for
5f's headline requirement measured the *helper* and not the endpoint that quotes
him a number. `test_the_preflight_endpoint_quotes_the_rendered_link_not_the_tag`
is what closed it. A property proved of a function and not of its only caller is
proved of nothing.

**And it caught two defects in itself**, which is the 5e "write mutations you can
reach" lesson arriving from both directions at once. `R9` stopped applying when
the redirect route was restructured, and an unapplied patch proves nothing — the
harness prints it as a separate category for exactly that reason. `R9b` applied
and was *not* caught, correctly: it only reinstated the early `return`, and with
one `finally` covering the whole handler an early return still closes the
session, so there was no defect behind the mutation. It was rewritten to
reproduce the leak properly (close per branch, return before any of them) rather
than deleted, because the leak is real and there is a test for it. A mutation
with nothing reachable behind it has to be said out loud, not dressed up as a
green tick — 5h dropped its `R15` on the same reasoning. Check 8b is the structural half of the white-label criterion — no router or
template may so much as name `carrier_cost` or `WHOLESALE_COST_PER_SEGMENT`, so a
route added later inherits the property instead of needing a new test.

### What the review found

**The spawned fresh-context reviewer produced nothing** — no report at all,
across the whole session, two direct requests for one, and two idle
notifications. It read the tree and ran tests (the suite was observably busy for
a stretch) and then returned nothing. That is the 5g experience repeating
almost exactly: four reviewers, three silent across a dozen idle cycles and five
requests. Two sessions is now a pattern rather than an incident, and the second
data point is the useful part of recording it.

What CLAUDE.md already prescribes for this is what was done: work the uncovered
lenses directly. Every finding below came out of running the ten review angles
by hand — each new test file and each new test *function* in isolation, the
migration down-and-up on a clean database, the query counts at 120 rows rather
than 8, the route table for shadowing, the connection pool, and `git diff` over
every file on the escalation list.

Three defects, each in a class this project has hit before.

1. **The unknown-slug path returned without closing its session.** `follow()`
   opened its own `SessionLocal()` — deliberately, for `/health`'s reason — and
   closed it on the resolved path and the exception path but not on the early
   `return` inside the `try`. Measured: forty requests to a slug that does not
   resolve left **seven connections checked out**, reclaimed only when the
   garbage collector got round to them. That is the path a *scanner* takes at
   the root of a domain that will be swept, not the path a buyer takes, so it
   would have shown up as a box that got slower the longer it ran. Closed by
   one `finally` with every return below it — the same shape as the browser
   contexts the reference system leaked one per daily scrape.
   `test_the_redirect_route_never_leaks_a_database_connection` asserts against
   the pool rather than against the presence of a `finally`.
2. **The composer's live counter measured the tag, not the rendered link.** A3
   says the segment counter must count the rendered link; the first cut applied
   that to pre-flight and not to the keystroke counter beside the message box.
   So "Segments / msg" read 1 while the phone preview an inch to its right
   showed a two-segment message and pre-flight's total said 4 — one screen
   contradicting itself, which is 5d's Opt-outs headline one screen over.
   `link_service.for_counting()` substitutes the placeholder before `describe()`
   sees the template, in `/preview` and in `build_report()`. The *checks* still
   read the raw template: they are about the copy he wrote, and a slug in the
   body would be a slug in the category-keyword scan.
3. **A slug could spell a page name.** `GET /{slug}` is registered last so a
   root page always wins the match, which means a slug reading `settings` — the
   one page name that is eight characters of the slug alphabet — is a link
   whose recipient lands on a login screen instead of the auction. One in 10^12,
   and closing the class costs a set-membership test, so `RESERVED_SLUGS` is
   checked at mint time. The test reads the app's own route table rather than
   pinning the list, so a page added later fails here instead of on a handset.

### Found while working (session 5f)

- **`GET /{slug}` is a root-level catch-all and must stay registered last.**
  `app/main.py` says so at the line. Starlette matches in registration order, so
  a router added after it would be unreachable — and the reason it is at the root
  at all is the 2026-08-24 costing, which compared bare domains: `a4a.bz/a7k9x2pq`
  against a subdomain is most of a segment on a tight message.
- **The rendered link carries no `https://` by default.** Same costing: the
  decision compared `a4a.bz/a7k` at 10 characters, not 18.
  `SHORT_LINK_INCLUDE_SCHEME` is the one-line escape if a handset in this
  client's audience turns out not to linkify a bare domain. Worth checking on the
  first real send.
- **`docs/API.md` does not document any of 5f.** `/api/reports/*`, the redirect
  route, `link_target_url` on both campaign-creating endpoints, and the four
  `carrier_cost*` columns are all absent. Docs are module 8's and `docs/` is not
  in 5f's file list.
- **`docs/RUNBOOK.md:147` and `docs/CLIENT_GUIDE.md:145-150` still describe the
  send-mode pill as two-state.** Carried from 5c and 5d, unchanged, same owner.
- **The Opt-outs, Usage and Categories screens are still the skeleton's.** 5f
  closed the History third of that deferred module and did not touch the rest.
- **`campaigns.link_target_url` is editable in the composer and frozen on the
  campaign.** A top-up mints against the campaign's current value, so changing it
  after a send would point new recipients somewhere the original ones did not go.
  Nothing in the UI can change it after creation today; if an edit screen is ever
  added, that is the question to answer first.
- **Click data has no retention rule.** `link_clicks` grows by one row per
  arrival, scanners included, and nothing prunes it. At this client's volume that
  is thousands a campaign and fine for a long time; it is not fine forever, and
  the backup script copies all of it nightly.
- **`record_click()` increments its counters in Python, not in SQL.** Safe today:
  `deployment/app.service.template` starts uvicorn with a single worker and the
  handler calls the recorder inline, so no two clicks interleave. Raise
  `--workers` and it becomes a read-modify-write across processes, and the loser
  of the race under-reports a click on the client's report while the
  `link_clicks` row it came from is still there. The rows are the record; the
  counters are the fast path. Fix by making it `UPDATE … SET click_count =
  click_count + 1` if that day comes.
- **A phone with two message rows on one campaign gets two links, and the report
  would count it as two clickers.** Nothing in the product creates that state —
  5h's `R7` mutation had to construct it directly — but `clickers` is a count of
  links with a click rather than of distinct contacts, and those stop being the
  same number the moment it can. Worth knowing before anything is built that
  writes a second row for one recipient.
- **The live box has still not been deployed to.** Criterion 11 cannot pass until
  someone does. The box runs the pre-5c nginx config, the hot-patched SDK, and
  pre-5d application code.


---

## Host-based routing guard — the short domain serves only short links (2026-08-31)

Requested after 5f landed, and it closes a hole 5f opened by shipping a second
public hostname. `bida4a.com` and `app.onlineauctions.co` are one uvicorn behind
one nginx, so every route answered on both names: **`bida4a.com/login` and
`bida4a.com/dashboard` served the admin panel** — the client's entire contact
list — on the one domain a stranger is handed with every campaign.

449 tests (447 + 2 from the review below), gate green twice,
`agent/accept-5f.sh` extended with criteria 12-14 as the stop condition and six
new mutations in `agent/mutate-5f.py`.

### The guard

`short_link_host_guard` in `app/main.py`: when the request `Host` (port
stripped, case-insensitive) is `SHORT_LINK_DOMAIN`, only the redirect route
resolves and everything else is 404 **before routing**. Middleware rather than a
dependency because it has to run before a route matches, and because a mount
like `/static` has no dependency to hang one on.

- **No nginx path denylist, and that was the decision.** A denylist has to name
  every admin path, so it is wrong the first time somebody adds a page — and the
  failure is silent, on the host nobody looks at. The guard asks the opposite
  question: is this path *positively* a short link? Anything new is excluded by
  default. `link_service` owns both halves of the answer because it already owns
  the domain and the slug shape; `main.py` stays wiring, as its own docstring
  requires.
- **The 404 body is the one an expired slug gets.** A probe of
  `bida4a.com/dashboard` and a probe of a dead slug are byte-identical, so
  sweeping the short domain reveals nothing about what else is behind the
  process.
- **The redirect still resolves on the primary host**, deliberately: links
  already in people's phones survive a domain change, and those are unrecallable.
- **`Host` only, never `X-Forwarded-Host`.** nginx sets `Host` from `$host`,
  i.e. from the server block that matched; a client-supplied forwarding header
  is not evidence. There is no bypass in ignoring it either — a request that
  lies about its Host is routed by nginx to the admin block it would have
  reached anyway.
- Guarded paths cost **zero queries**: the guard runs before routing and before
  any session is opened, so a scanner sweeping the short domain is free.

### nginx and bootstrap

`deployment/nginx.conf.template` gained the short-link server block, with
`YOUR_SHORT_DOMAIN` and `YOUR_AUCTION_SITE` as placeholders. It proxies
everything and lets the middleware decide, keeping only the ACME location and
the bare-`/` redirect to the auction house's own site — the one path there that
is a person rather than a link. Security headers are the empty-policy set
(`default-src 'none'`), and `Referrer-Policy` is deliberately *not* set because
the app already sets `no-referrer` on the redirect and `add_header` appends
rather than replaces.

**The block existed only as a hand edit on the box**, so a `bootstrap.sh` run
would have reverted it — and the symptom of that is the admin panel answering on
the short domain again, on a host nobody looks at. `bootstrap.sh` now takes
`--short-domain` and `--auction-site` and substitutes all three placeholders in
one pass.

`deploy.sh` gained a warning at `HEALTH_URL`: it must never point at the short
domain, because `/health` is 404 there and a non-200 rolls the release back —
which would revert every deploy including the one that fixes the box. The
default is an IP and a port, so it matches no configured domain and the guard is
a no-op for it.

`.env.example` documents `SHORT_LINK_DOMAIN`, `SHORT_LINK_INCLUDE_SCHEME` and
`CLICK_MIN_HUMAN_SECONDS` for the first time — 5f added all three settings and
documented none of them, and `.env.example` is the file a new box is copied from.

### What the review found (ten lenses, worked in session)

No reviewer was spawned: two produced nothing across 5f, and CLAUDE.md now says
to work the lenses directly. Two defects, both found by running something rather
than by reading.

1. **`/settings` served its page on the short host.** `settings` is eight
   characters of the slug alphabet, so the shape test said "that is a slug", and
   routing — where `/settings` is registered before `/{slug}` — then handed it
   to the admin page. Measured: 302 to the login form while every *other* admin
   path answered 404. One page leaking is the whole leak. `is_slug_path()` now
   subtracts `RESERVED_SLUGS`, which costs nothing because no slug is ever
   minted from that set. This is the same collision `RESERVED_SLUGS` already
   closed on the *minting* side, arriving on the serving side — the two halves
   of one namespace.
2. **A misconfigured `SHORT_LINK_DOMAIN` would 404 the entire product.** Set it
   to the admin host — a copy-paste away — and the guard matches every request,
   so every page answers "This link has expired or was mistyped." with nothing
   on screen connecting it to a setting. There is no configuration in which
   blocking is right when the two names are the same, so
   `short_domain_conflicts()` fails the guard **open** and the startup log says
   so loudly. The cheap error is the pre-existing state (both surfaces on one
   name); the expensive one is an undiagnosable outage.

Also checked and clean: the send path is untouched (the whole change is +113
lines across two files, purely additive); no migration; `app/sms/` still imports
nothing from the DB layer; every one of the 25 new tests passes on its own; the
nginx template parses under real nginx (`nginx -t` in a container); carriers'
webhooks still work on the primary host and 404 on the short one; and the
`/health` rollback trap is documented at the line that could spring it.

Three of my own measurement scripts were wrong before the code was — a shell
loop that split a parametrized test id on whitespace, a layering grep that
counted a comment *stating* the rule, and a white-label grep that counted
`main.py`'s internal webhook module imports. All three are the "prose about a
rule is not a violation of it" shape that `accept-5h` check 7 and this session's
own check 8b had to fix. A check written in a hurry is a check that reports on
itself.

### Found while working

- **A trailing slash is not a slug.** `/{slug}/` answers 404 on the short host
  where the primary host 307s to the canonical path. Deliberate — a slug with a
  slash is not a link we minted — but if a handset or an SMS client is ever
  found appending one, that is where to look.
- **The guard is keyed on one domain.** A second short domain (a rebrand, a
  parallel client) needs `SHORT_LINK_DOMAIN` to become a list, and every reader
  of it — `domain()`, `url_for()`, `validate_target()` — assumes one.
- **`campaign_service.py` is at exactly 500 lines.** Not over, so the gate
  passes, but the next line anywhere in it forces a split.

---

## Module P1 Part A — the prospect pipeline, 2026-08-31

The holding pen between a scraper and the textable list. **No source
implementations** — this session built the machinery every future source plugs
into, because if it is built right adding Google Places is a class and a
taxonomy, and if it is built wrong every source inherits the damage.

`bash agent/gate.sh` passes, twice. **494 tests** (449 + 45 new).
`bash agent/accept-P1.sh` is the stop condition; criteria 1-10 pass locally,
criterion 11 is `--with-remote` and needs the deploy.

### The rule the module exists to enforce

**Buyers, never sellers.** A consignor on the list costs money to text, dilutes
the audience and puts a competitor on the client's own marketing channel. The
plan of record enforces it in three places rather than trusting it once, and all
three are built:

1. **`search_term` and `buyer_rationale` are non-nullable**, and
   `ProspectSource.ingest()` counts a record without a rationale as `invalid`
   and persists nothing. A term with no written claim about why those people
   would raise a paddle cannot reach a reviewer, because a reviewer cannot
   disagree with a blank.
2. **The queue shows that rationale on every row**, so the question being
   answered is "would this person bid?" rather than "is this a real business?"
   Those have different answers for an estate liquidator with a good website.
3. **`seller_or_consignor` and `competitor` are first-class reject reasons**,
   both suppress permanently, and the per-term breakdown counts them apart from
   every other reason — so a search finding the wrong side of the room becomes
   visible instead of being something somebody eventually notices.

### Five tables, not three

`prospects`, `scrape_jobs` and `phone_lookups` are the three the session named.
Two more, and the alternative to each is an overloaded column:

- **`prospect_sightings`** — one row per (prospect, source, term), uniquely
  constrained. Multi-source corroboration is a `COUNT(DISTINCT source)` over it.
  The column version is a counter plus a JSON list of terms, which is a domain
  concept living as a string. The constraint is also what makes a nightly re-run
  idempotent instead of score-inflating.
- **`prospect_rejections`** — keyed on the phone, permanent. "Re-ingesting from
  any source must not resurface it" is a claim about a *number*, not about a row
  somebody might later tidy up. Same separation `blocked_numbers` has from
  `contacts.is_active`.

`prospects.phone` is uniquely indexed for the reason `contacts.phone` is: the
number is the identity. The migration is additive only and touches no existing
column or index; `ix_contacts_phone` is not in it, and nothing in this module
inserts a contact except `promote()`, which goes through
`contact_service.upsert_contact()` like every other ingestion path.

### The gate

Line-type lookup sits in front of everything, because 2,526 of one campaign's
failures were not-routable numbers — 39% of a 6,857 send, all paid for, all
still on the list the next morning. Screening costs about $0.0025 a number;
10,000 businesses is $25 **once**, and the cache is what keeps it once.

- The provider interface is `app/sms/lookup.py` and is DB-free, on the rule that
  keeps `app/sms/` portable. The cache is `app/services/lookup_service.py`.
- **The default provider makes no network call at all.** Wiring a paid API in is
  escalation item 7, and an unexercised provider class is the pinned-SDK bet this
  project has already lost. It lands with P2.
- **An answer is permanent; a failure is not.** A carrier that says "landline"
  is final. A carrier that times out has not answered, and caching that would
  turn one bad afternoon into a permanent hole — every number screened during it
  filed `unknown` forever, and `unknown` cannot be promoted.
- **`unknown` and `toll_free` are not promote-eligible either**, and the reason
  is asymmetry, not caution: a mobile wrongly held back waits in a queue, and a
  landline wrongly promoted is paid for on every send from now on. A gate that is
  switched off refuses; it does not wave things through.

`PROMOTABLE_LINE_TYPES` is one tuple in one place. The queue's "ready to
promote" tile and the promote button's guard both read it, and a test asserts
the *property* that the two agree rather than pinning the tuple.

### The runner

Hard timeout, cleanup in a `finally`, and `cleanup_ran` as a **column** rather
than a log line — the reference system's 17 orphaned browser processes were
invisible because "the cleanup ran" was only knowable by reading a journal
nobody read. A timed-out job with `cleanup_ran = 0` is now something you can
query for.

Two layers of deadline: cooperative (`source.request_stop()`, which a
well-behaved `fetch()` checks in its paging loop) and abandonment (a daemon
worker thread, because Python cannot kill a thread and pretending otherwise is
how a runner comes to believe it cleaned up something still running). That
second layer is exactly why `cleanup()` belongs to the source: only the source
holds the handle, and it is called on the runner's thread the moment the
deadline passes.

A timeout is not a rollback. Records produced before the deadline are persisted,
through the source's own `ingest()` — not through a loop of the runner's, which
would be a second call site for the rationale check, the suppression check and
the provenance requirement.

### What the review found (ten lenses, worked in session)

No reviewer was spawned — CLAUDE.md says to work the lenses directly, and two
fan-outs produced nothing across 5f and 5g. Eight defects. Every one was found
by running something — the suite, the mutation harness, driving the real screen
over HTTP — rather than by reading.

1. **`source_count` could never reach 2.** `_add_sighting()` read the distinct
   sources *after* `db.add()`ing the new sighting. SQLAlchemy autoflushes on
   query, so the pending row flushed, the query found its own source in the
   answer, and the increment never fired — the corroboration term of the score
   would have been permanently dead while every test about "two sources" still
   passed on the sighting count. Fixed by reading before the add.
2. **A repeat of the same search never screened what it found.** `_screen()` was
   scoped to `sightings.job_id`, and a re-run of the same source and term writes
   no new sighting — that idempotence is what stops corroboration inflating. So a
   prospect that missed screening the first time could never be screened by a
   repeat of the search that found it: `unknown` forever, unpromotable, with
   nothing on screen saying why. Now scoped to the numbers the run produced.
3. **The screening pass paid to look up numbers a human had already rejected.**
   A rejected number is suppressed at ingest, so it never enters the cache — so
   every re-run of a search that keeps finding it was a fresh $0.0025 for an
   answer nobody will ever act on. Found by checking the docstring against the
   code: it claimed rejected records were not paid for, and the query said
   otherwise. Now filtered to `status == "pending"`.
4. **A job that blew up in ingestion was reported `completed`.** `_collect()`
   catches everything the *source* raises, so `failure` is set on that path —
   but an exception out of the persistence or screening step reached the
   `finally` with `failure` still None, and it fell straight through to
   `status = "completed"`. Same defect as a blast that reached nobody reporting
   "completed", one level down.
5. **The runner had its own persistence loop.** `ProspectSource.ingest()` is
   documented as the only path from a source into these tables, and the runner
   went round it because it drains `fetch()` on another thread and persists
   afterwards. That is the "guard with one call site" shape before it has a
   chance to bite. `ingest()` now takes the already-collected records, and a
   test watches it get called.
6. **The rescore test proved nothing.** It ran the second job under a
   *different* source name, so the score rose from corroboration whether or not
   screening rescored anything. Same shape as 5e's "two assertions comparing
   0 == 0". Rewritten to use the same source and term, and to assert
   `source_count` did not move.
7. **Two cache guards were untested, and only the mutation harness said so.**
   `screen()` short-circuits on a fully cached set, so the criterion-6 test
   never reached `line_type_for()`'s own cache read, and never reached the
   per-number "skip what we already know" check either — a partly cached batch,
   which is what a nightly job over an overlapping set actually is. Both
   reverted cleanly with the suite green. Two tests added.

8. **Every sort ran descending.** The API takes a direction and the screen sent
   `desc` for all of them, so picking "Business" sorted Z to A and picking
   "Distance" put the farthest business first — the opposite of the question
   being asked. One map from sort key to direction; score and recency are the
   only two where bigger is better.

### The mutation harness caught me out first

The first run reported 38 caught, 0 survived — and it was worthless. The run
before it had been killed on a timeout mid-mutation, leaving the scratch tree
**already mutated**, so `pristine` was captured from a dirty tree and every
verdict in the next run was measured on top of a leftover edit. The tell was
visible in the output and I nearly missed it: one test,
`test_the_runner_persists_through_the_sources_own_ingest`, was failing for
almost every mutation, including scoring-weight and router-auth changes it has
no business noticing. Several mutations were "caught" by that test **and nothing
else** — R30 among them, which no test actually caught.

Every later run therefore verifies the scratch tree is byte-identical to the
repo before applying a single patch, and prints `SCRATCH VERIFIED PRISTINE`.
This is 5g's lesson arriving from a new direction: before trusting a green
mutation run, check that the tests failing for a mutation are the tests that
*name* it.

Also checked and clean: the send path is untouched; `app/sms/` still imports
nothing from the DB layer and neither does `app/sources/`; the migration applies
to an empty database and `compare_metadata` reports no drift; every new test
passes on its own; no router reads a cost column or the raw payload (asserted by
AST, not grep); and every prospect surface — the page, four API routes and the
CSV export — is scanned at runtime for carrier names, our spend and the raw
third-party payload.

### Deliberately not built

- **Any source implementation.** Google Places is P2, registries are P3. The
  registry (`PROSPECT_SOURCES`) is empty and the fakes live in `tests/`.
- **A carrier lookup provider.** See above — budget decision.
- **DNC scrub.** The old module-5 sketch listed it; `sessions/session-P1.md` A7
  specifies E.164 normalisation, blocklist refusal and the opt-out check, and
  acceptance criterion 7 names only those. Not built, not guessed at.
- **A "promote landlines anyway" setting.** "Excluded by default" was read as
  "out of the box", not "unless configured otherwise". Adding a toggle would
  have been inventing a requirement and weakening a guard in one edit.

### Found while working

- **`contacts.py:BLOCKED_CONTACT_ERROR` and `prospect_service`'s refusals are
  two wordings for one rule.** The *rule* has one definition
  (`blocklist_service.is_blocked` / `OPT_OUT_REASONS`), and the two sentences
  differ because the verbs differ ("not added" vs "not promoted") — but if
  module 8 touches the Contacts screen, they should be folded into one
  sentence-maker the way `send_path_assessment()` owns both tenses of the
  degraded refusal.
- **A prospect is not deduped against existing contacts.** `modules.md` puts
  that in P2's scope, so it was left there. Promoting one that is already a
  contact is harmless today — `upsert_contact()` fills gaps and `tag_contact()`
  is idempotent — but the queue will show businesses the client already has.
- **`prospect_service.py` is 451 lines and `campaign_service.py` is still at
  exactly 500.** The next addition to either forces a split.
- **The job cost is recorded and never rendered.** `scrape_jobs.cost` and
  `phone_lookups.cost` are our spend, on the same footing as
  `WHOLESALE_COST_PER_SEGMENT`; there is no screen for jobs yet, and when one is
  built neither those nor `scrape_jobs.error` (raw, unscrubbed, a developer's
  diagnostic) may be on it.
- **`tests/test_campaign_reports.py::test_no_new_surface_leaks_the_carrier_or_our_cost`
  is latently flaky, and it is not mine.** It failed once in six full-suite runs
  during this session and I could not reproduce it in five more. The mechanism is
  almost certainly line 313: `assert str(settings.WHOLESALE_COST_PER_SEGMENT) not
  in body` matches the bare string `"0.009"` against an entire response body,
  and an ISO timestamp spells it whenever the seconds end in `0` and the
  microseconds start `009` — `...T14:23:40.009312` contains `0.009`. Verified
  the substring logic; did *not* catch it in the act, so this is a hypothesis
  with a mechanism, not a confirmed diagnosis. `"0.0043"` on line 314 is the
  same shape, ten times rarer. This is `CLAUDE.md`'s "a numeric code matched
  against prose matches phone numbers", one column over: a bare decimal matched
  against a whole body matches timestamps. The fix is to assert against the
  parsed fields, or to anchor the figure the way a money string would actually
  appear. **Left alone** — it is 5f's test and a drive-by edit to another
  session's white-label assertion is exactly the change that should be reviewed
  on its own. It can intermittently fail `agent/gate.sh`, which runs with
  `--maxfail=1`; if it does, re-run before believing it.
- **`docs/API.md` does not document the prospects API — nor 5f's reports and
  links routes.** The drift predates this session; the file stops at Settings,
  Health and Webhooks. Worth one pass when somebody is in there.

---

## Module P1b Part A — the lookup provider, and a gate that flakes, 2026-08-31

Three things: the paid line-type provider RULES.md escalation item 7 was
blocking, the flaky white-label assertion that could fail a sound build, and
`docs/API.md`'s drift. `bash agent/accept-P1b.sh` is the stop condition;
criteria 1-9 pass locally, criterion 10 is `--with-remote` and needs the deploy.

**544 tests** (494 + 50 new). `bash agent/gate.sh` passes, twice.

### A1 — the provider, and the two things it is not allowed to spend on

`app/sms/providers/telnyx_lookup.py` is one class against the interface P1
built: `client.number_lookup.retrieve(phone, type="carrier")`, the cheap MCC/MNC
query rather than the CNAM one. Recorded responses live in
`tests/fixtures/number_lookup_responses.json` and are replayed **through the
SDK's own response model** — a fixture the SDK will not parse means the pin and
the code have drifted, which is the defect that took a launch morning once
already.

**The default did not move.** `PROSPECT_LOOKUP_PROVIDER` is still `none`, which
makes no call at all. Switching screening on starts spending real money, so it
is one line of `.env` on a live box rather than a consequence of deploying.

Three constraints, and the first is the session's reason for existing.

1. **Lookups and sends draw on the same balance.**
   `PROSPECT_LOOKUP_MONTHLY_CAP` (default $50) is a hard ceiling checked
   *before* every call, in `line_type_for()` — the one place a call is actually
   made — so a caller that forgets to pass a budget still cannot spend past it.
   A pass that reaches the cap stops spending, writes **no row** for the numbers
   it skipped (they are screenable next month; a refusal of ours is not an
   answer to cache), and logs one line naming the count and the remedy. Nothing
   about the cap reaches a client surface: the queue says a number is
   unscreened, which is true, and the cap is denominated in our money.
2. **The cache is mandatory.** Unchanged from P1 and now asserted from the
   other direction too — an already-known number is one of the three
   populations we must never pay for.
3. **Never spend on a number nobody will use.** `unusable_numbers()` subtracts
   permanently rejected prospects and blocklisted numbers. It queries the two
   tables directly rather than calling `prospect_service.is_suppressed()` and
   `blocklist_service.is_blocked()` — it runs over a whole scrape, and
   `prospect_service` imports `lookup_service`, so the reverse import is a
   cycle. That leaves two statements of one rule, so the test asserts the
   *property* that they agree rather than pinning either list.

Two smaller decisions worth knowing about:

- **`cost_per_lookup()` moved to `app/sms/lookup.py`** and is re-exported here.
  The carrier charges per call, so the price belongs beside the carrier
  conversation; the provider needs it to report what it spent and cannot import
  a service, and the cap needs the same number. Two copies is how a cap comes to
  disagree with the ledger it caps.
- **`phone_lookups.cost` accumulates on a retry** instead of being overwritten.
  The monthly total is summed from that column, so a number billed twice inside
  one month would otherwise count once and the ceiling would permit more than it
  says. Across a month boundary it over-states slightly, which is the direction
  to be wrong in.

An answer we cannot act on is still an answer: `fixed line or mobile`,
`voicemail`, `pager` and the rest come back `ok=True, line_type="unknown"` and
are cached forever, because asking again next month cannot make them less
ambiguous. The one case that is **not** an answer is a response carrying no line
type at all — that is schema drift or our own bug, the money is already spent,
and freezing it into the cache would be P1's permanent-hole failure arriving
through a new door.

### A2 — the assertion that could fail a sound build

`assert str(settings.WHOLESALE_COST_PER_SEGMENT) not in body` searched an entire
serialized response for the bare string `"0.009"`, and `...T14:23:40.009312`
contains it. `tests/_wholesale_scan.py` replaces it: numbers are found **as
numbers** and compared **as numbers**, and a JSON response is compared field by
field. `0.0090` and `0.009` now compare equal, which the substring test got
wrong in the other direction.

`PROSPECT_LOOKUP_MONTHLY_CAP` is deliberately **not** in the value sweep. Its
default is `50.0`, and a bare 50 is a message count, a page size and a contact
id — sweeping for it would reproduce the exact defect being removed. Round
numbers are asserted by field name.

**Parsing numerically was necessary and not sufficient, and the check written
for the criterion is what said so.** Criterion 7 asks for twenty consecutive
runs; twenty samples is weak evidence about a one-in-several-hundred event, so
check 7b walks *every* timestamp containing `0.009` instead — and it failed
immediately, on `14:23:00.009000`. Same timestamp family, but its seconds field
parses as **exactly** 0.009: the token boundaries are right and the number
really is our rate, so no lookbehind can tell them apart. Clock times are now
removed before anything is tokenised, and the tokeniser separately refuses a
redundant leading zero — a price is `0.009`, and `00.009` is a clock. A colon is
deliberately *not* excluded, because FastAPI serialises compactly and a real
leak reads `{"rate":0.009}` with no space; a rule that dismissed anything after
a colon would have missed every JSON leak in the app while looking rigorous.

### The audit: five sites, and the two I did not find by reading

Criterion 8. Every site in the suite that tested one of our figures as a
substring:

1. `test_campaign_reports.py::test_no_new_surface_leaks_the_carrier_or_our_cost`
   — the known flake, two assertions (`0.009` and `0.0043`) over eight bodies
   including two HTML pages and a CSV export. **Converted.**
2. `test_whitelabel.py::test_no_response_quotes_our_wholesale_rate` — **the one
   the session was sent to find.** The same assertion over *every* client-facing
   GET route, discovered from the app's own route table, and those routes render
   timestamps by the hundred. It had simply not lost the dice roll yet, and it
   would have failed on a route nobody had touched. **Converted.**
3. `test_campaign_preflight.py::test_preflight_response_never_quotes_our_wholesale_rate`
   — the whole preflight body. **Converted** to a field-by-field walk.
4. `test_capacity_rounding.py` and `test_degraded_send_path.py` — two assertions
   over a short refusal *sentence*. Low risk, since a sentence has no timestamp
   in it, but converted anyway: leaving three spellings of one assertion is how
   the next person picks the wrong one.
5. `test_monitoring.py::test_the_alert_never_quotes_our_wholesale_rate` — **and
   I did not find this one by reading.** `agent/accept-P1b.sh` check 8 greps for
   the shape, and the grep found it on its first run. Converted. That is the
   argument for automating the audit rather than declaring it done: my own sweep
   of the suite missed a site and the sweep I wrote for the criterion did not.

`agent/accept-P1b.sh` check 8 keeps the property. **No remaining site tests one
of our figures as a substring.**

**The check's own first version was wrong, in this project's most familiar
way.** It flagged a *comment* in `test_whitelabel.py` quoting the old assertion,
plus three `__pycache__` binaries. That is the fourth time here that a
measurement script has counted prose describing a rule as a violation of it. It
is now anchored to an `assert` statement and restricted to `*.py`.

### A3 — `docs/API.md`

Prospects, reports and history, and the public short-link route: three sections
that did not exist. The payload shapes were read off the services rather than
recalled, and four details were wrong on the first pass and corrected against
the code (`per_page` defaults, the `top_ups` keys, the prospect sort names, the
reject-reason labels).

### What the review found (worked in session, no spawned reviewers)

The mutation harness earned its keep again: **27 mutations, three survived the
first run, and all three were holes in my tests rather than in the code.**

1. **`C3 a cap of zero means unlimited` survived.** The test named the guard and
   never reached it: at the configured price, `cost <= remaining` already
   refuses on an exhausted cap, so the assertion passed whether or not the
   `cap <= 0` branch existed. The branch only bites when a lookup is priced at
   **zero** — which is exactly what somebody sets while wiring up a provider
   they believe is free. The test now drives that case.
2. **`P3 an unmapped carrier word falls through as mobile` survived.** Nothing
   exercised a line type outside the recorded fixtures. Now the SDK's own
   `Literal` is read and every value it declares must have a row in
   `CARRIER_LINE_TYPES` — so a classification added upstream fails here rather
   than defaulting onto the contact list.
3. **`P7 the raw SDK error string is stored, payload and all` survived**, and
   this one generalises. The assertion was on the **stored row**, and
   `scrub_provider_text()` strips a payload as well as a carrier name — so the
   scrubber was covering for the provider and the provider's own guarantee was
   untested. Same shape as 5f's "a property proved of a helper is not proved of
   its only caller": if two layers each fix a thing, a test downstream of both
   proves nothing about either. The assertion is now on what the provider
   returns, before the scrubber sees it.

Three more came from working the lenses directly rather than from the harness:

4. **An unreadable cost string would have taken the screening pass down *and*
   poisoned the ledger.** `Decimal(cost)` sat between the provider call and the
   row write, so a provider reporting `"$0.0025 USD"` would have raised after
   the money was spent and before the answer was stored — and if it had been
   stored raw, every later `spend_this_month()` would have raised on it, which
   is one bad row disabling the ceiling for the rest of the month. Found by
   reading the write path against the read path. `_charged()` parses at the
   boundary, logs, and treats unreadable as zero; the column can now only hold
   something `Decimal` can read.
5. **A pass reported `rate x attempts` rather than what it was charged.** The
   two are the same number until a call fails, and a failed call is a lookup
   performed and not a charge — so a bad afternoon would have written spend into
   `scrape_jobs.cost` that the carrier never billed. One module was reporting our
   spend two ways; it now reports the ledger's own delta.
6. **`phone_lookups.provider` holds the carrier's name on a screening box**, and
   before this session it read `disabled` on every row. Nothing renders it —
   checked against `prospect_queue`, the four API routes and `EXPORT_COLUMNS` —
   but the existing runtime scan could not have caught it even in principle,
   because it screens with a fake called `fake-lookup` and a surface rendering
   the column would have passed by carrying a word that is not a carrier's.
   There is now a test that stamps the real name on a row and runs every
   prospect surface again.

A fourth came from the acceptance script's own machinery. When the scanner's
regex changed, mutation `S2` stopped applying — and the harness reported
**PATCH DID NOT APPLY** and failed the run rather than counting it as a pass.
That is the guard working: a patch that does not apply proves nothing, and the
alternative is a mutation quietly dropping out of the set every time the code it
targets is edited. Repaired against the new regex, with two more added for the
clock strip and the leading-zero rule.

Also checked and clean: the send path is untouched; `app/sms/` still imports
nothing from the DB layer; no migration was needed (no schema change); the
suite is green twice; every new test passes on its own; and the mutation run is
27 caught, 0 survived, on a tree verified byte-identical to the repo before the
first patch.

### Deliberately not done

- **No real API call, at any point.** Not to validate the provider, not in the
  gate, not in acceptance. Every fixture is recorded and every provider under
  test has its client replaced before use.
- **`.env` and `.env.production` untouched**, as the session constrains.
  Production therefore inherits `PROSPECT_LOOKUP_MONTHLY_CAP=50.0` from the code
  default, and screening stays off until Jordan sets
  `PROSPECT_LOOKUP_PROVIDER=telnyx` — which is escalation item 6/7's territory,
  not this session's.
- **The existing contact list is not screened.** Explicitly out of scope: that
  is a decision about spending real money on live data.
- **No jobs screen.** `scrape_jobs.cost`, `phone_lookups.cost` and the cap are
  ours; when a screen is built, none of the three may be on it.

### Found while working

- **`agent/mutate-P1.py` does not verify its scratch tree**, though `CLAUDE.md`
  says every run now does and prints `SCRATCH VERIFIED PRISTINE`. What actually
  protects P1's run is `accept-P1.sh` doing `rm -rf` before the rsync, which is
  a different guarantee: it holds only when the harness is invoked through that
  script. `agent/mutate-P1b.py` implements the documented check for real —
  every file it patches is compared byte for byte against the repo's copy first,
  and the run aborts if any differs. Worth back-porting to the four earlier
  harnesses; not done here, since they are other sessions' files.
- **`agent/accept-P1.sh` check 8d had to be amended.** It asserted
  `set(lookup.PROVIDERS) == {"none"}` with the comment "that is a budget
  decision — RULES.md escalation item 7". P1b *is* the ruling on that item, so
  the assertion was right for P1 and wrong now. Struck through in place with the
  old text quoted and the superseding session named, per RULES.md; what it was
  really protecting — that reaching the carrier takes a deliberate `.env` edit —
  is still asserted, on the default.
- **`lookup_service.py` is 421 lines** and `prospect_service.py` is 451. The
  next addition to either forces a split.
- **"Screening is off" and "screening is broken" are one state today.**
  `get_lookup_provider()` falls back to the no-call provider on a bad name, a
  missing SDK or a missing credential — correct, for `get_provider()`'s reason —
  and `DisabledLineTypeProvider.DETAIL` then says screening "is not switched
  on", which is false in the broken case. It reaches no screen: nothing renders
  `phone_lookups.error`, and the queue's own wording ("this number has not been
  screened yet") is true either way. But this is 5d's shape with the surface not
  yet built, so if a screening or jobs screen is ever added it needs **three**
  states, not two, and the failed one has to say so — `send_mode()` is the
  pattern.
- **A screening pass stopped by the cap is invisible on the jobs record.**
  `scrape_jobs` has `lookups_performed` and `lookups_cached` and no column for
  "skipped", so a run that screened nothing because the cap was reached looks
  like a run that had nothing to screen. The prospects are correctly shown as
  unscreened and the log names the cause, but the job row does not.
  `scrape_runner.py` and a migration are outside this session's file list.

---

## Module 5i Part A — named lists replace categories (appended by session 5i, 2026-09-04)

Jordan's dad asked where the list for a yacht auction goes. None of the five
categories fit and a sixth was not available — the palette is validated at four
hues plus neutral and is full. So the picker becomes what the Williamson build
always had: **one flat dropdown of past lists, newest first, with
`⭐ ALL BIDDERS — MAIN LIST` pinned at the top.**

`bash agent/accept-5i.sh` is the stop condition; criteria 1-10 pass locally.

**577 tests** (544 + 33 new). `bash agent/gate.sh` passes, twice.

**Categories are hidden, not removed.** No table, column or index was dropped or
renamed. `categories`, `contact_categories`, `contact_lists.category_id`,
`campaigns.category_id`, `campaigns.cross_category_override` and `--s1`..`--s4`
are all exactly as they were, and the prospect screens still use them.

### A1 — one writer on `contact_lists.created_at`, and one thing the spec did not know

The defect was as specced: `contact_service.get_or_create_list()` omitted the
column and got SQLite's `CURRENT_TIMESTAMP` (**UTC**, `2026-08-20 00:12:30`),
`import_service.commit()` wrote `datetime.now().isoformat()` (**local**,
`2026-08-27T10:46:45.685160`), and both rows are in the local database.

Three parts, and the third is not in the session spec.

1. **The server default is off the model** and both insert paths write the value
   explicitly, from `datetime.now().isoformat()`.
2. **Migration `f4a1c7d90e52`** normalises what is stored. It classifies by
   *shape* — `SERVER_DEFAULT_SHAPE.fullmatch()`, not "does it contain a T" —
   because the classification decides whether a row is **rewritten**, and a test
   for the absence of something says yes to a bare date and to a truncated
   write. Those are left alone and logged. Run against a copy of the real
   `data/app.db`: `1 converted from UTC, 1 left alone`, and
   `2026-08-20 00:12:30` became `2026-08-19T20:12:30`.
3. **The table's own DDL still says `DEFAULT (CURRENT_TIMESTAMP)`.**
   `f69dc078ee13:85` created the column that way and removing a column default
   in SQLite means rebuilding the table — escalation item 8. So removing
   `server_default` from the *model* does not remove the old writer; it only
   stops SQLAlchemy declaring it. An insert that omitted the column would still
   have got a UTC value from the database.
   <br>The fix is a Python-side `default=now_iso` on the column. That makes "one
   writer" a property of the model rather than a convention every future insert
   site has to remember — which is the "guard with one call site" shape, and
   this column has had two writers already. `parse_created_at()` still
   understands the space-separated spelling on purpose: a raw `INSERT` naming no
   `created_at` can still reach the DDL default, and a reader that assumed the
   spelling away would be wrong on exactly the row that matters.

**Ordering is by `parse_created_at()`, never by the string.** A space (0x20)
sorts before a `T` (0x54), so under a string comparison a server-defaulted row
always loses to a same-day isoformat row however much later it was written.
`accept-5i.sh` check 1b prints both orders on the two live spellings side by
side, so the disagreement is on the transcript rather than asserted out of sight.

### A2 — the picker

`list_summaries()` returns the pinned entry (`selector: "all"`, label from the
one constant) and then every list newest first. No category entries. Each entry
gained `last_sent_at` and `days_since_sent`, so the dashboard's cards and the
composer's dropdown are one query and cannot report different numbers for the
same list.

Three decisions worth knowing about:

- **`ALL_BIDDERS_LABEL` is also what `_term_label()` returns for `all`.** A3
  makes the composer's summary panel read `audience_label()`, so a dropdown
  saying `⭐ ALL BIDDERS — MAIN LIST` beside a panel saying "All contacts" would
  be one audience wearing two names on one screen. One rule, two moments, one
  sentence-maker.
- **The list count is now *active* contacts**, not membership rows. It has to
  match `audience_count()` on the same selector, or the picker promises people
  no campaign could reach.
- **`SENT_STATUSES` moved to `app/models/sms_message.py`**, beside
  `BILLABLE_STATUSES`, carrying `dashboard_service`'s comment about why the two
  must not be bound together. It was defined independently in `dashboard_service`
  and `report_service`; the freshness query would have made it three.
  `test_the_four_modules_read_one_sent_status_set` asserts identity across the
  four readers rather than the tuple's contents.

**The tuple contents could not be asserted the way the spec's mutation implies.**
"Point the freshness query at `BILLABLE_STATUSES`" is invisible to an `is not`
assertion: the two literals are equal, and CPython folds equal literal tuples in
one module to one object, so `SENT_STATUSES is not BILLABLE_STATUSES` fails for a
reason that has nothing to do with this codebase. What can be told apart is the
*binding* — `test_freshness_is_bound_to_the_sent_set_and_not_to_the_billable_one`
changes what "texted" means and requires the freshness figure to follow, and
separately asserts `contact_service` does not hold the billable set at all.

### A3-A6 — the screens

The composer loses the category fieldset, the cross-category checkbox, the
chip-rendering JS and the upload tab's optional tag; the audience `<select>` is
the whole of the "existing audience" tab. The summary panel's `Category` row
became **Audience**, filled from the selected option's `data-label` — not from
its visible text, because the pinned label *contains* an em dash and splitting on
one would truncate exactly the label the row exists to show.

The Contacts page loses the filter tabs, the per-row chips, the bulk tagging
controls and the add-form's category select; the import block asks for a **list
name** instead of a category.

The dashboard's `category_cards()` became `list_cards()` — the pinned card, then
the five most recent lists, no swatch, same grid, same threshold, and the em-dash
rule verbatim. `last_send_outcomes()` moved to list membership with it, because
`dashboard()` hands the same cards to both. The hero's chip is gone, its tile
reads "List last texted", and its fallback caption is "not a saved list" rather
than a blank under an em dash.

`/api/campaigns` stopped carrying `category_id`, `category_label`,
`category_color_token` and `cross_category_override`. The columns are untouched
and `test_the_category_columns_still_record_what_a_caller_supplies` says so.

### A7 — the render scan, and the part of it that could not be built as specced

`tests/test_audience_surfaces.py` renders every client-facing GET route from the
app's own route table (reusing `test_whitelabel._get_routes()`) and asserts no
body contains `categor`.

**A7 as literally written cannot hold, and the reason is other clauses of the
same spec.** Six surfaces still carry the word on purpose:

| surface | what keeps it |
|---|---|
| `app.routers.prospects` | A8 — the review queue keeps its category |
| `/prospects` (the page) | A8; served by `pages.page`, so not subtractable by module |
| `app.routers.categories` | the taxonomy CRUD the promote dropdown reads |
| `/api/contacts`, `/api/contacts/export.csv` | per-row chips and the export column, in `contact_query_service`, which is outside the file list |
| `app.routers.reports` | the `category_label` key, in `report_service`/`history_service`, which the file list puts in scope "for one reason only" |
| `/history`, `/history/{campaign_id}` | the two report templates read that key; not in the file list |

So the sweep subtracts them, each with the clause that retains it written beside
it — `test_whitelabel.py`'s own `EXEMPT_PATHS` is the precedent. What remains is
exactly the audience surface: `/campaigns`, `/contacts`, `/`, `/dashboard`,
`/api/lists`, `/api/campaigns/audiences`, `/api/campaigns`, `/api/dashboard`, and
`test_the_sweep_covers_the_audience_surface` names them so the exemptions cannot
quietly eat the sweep.

Two guards on the guard, both from this project's own history with measurement
scripts: `test_the_sweep_actually_fires` points the same matcher at
`/api/categories`, whose body is entirely about categories, and
`test_no_exemption_has_gone_stale` fails when an exempted route stops carrying
the word — which is the moment the entry should be deleted rather than the moment
somebody notices.

### A8 — the prospect queue, deliberately untouched

`/prospects` still renders its category chip and still refuses to promote without
one. Criterion 7 asserts both, so the next session cannot remove them by
accident. `/api/categories`, `/api/contacts/categories` and the two bulk routes
are retained with no caller in the UI.

### Deviations from the file list, and why

Four files outside `sessions/session-5i.md`'s list were edited. Decision 006
endorsed exactly this shape — "a stale sentence about who receives a text is not
a documentation problem" — and each is the minimum the spec's own requirements
force.

- **`app/routers/imports.py`.** A5 requires the Contacts import to take a list
  name and commit with `category_id=None`. The route called
  `import_service.require_category()`, which refuses `None`, and had no
  `list_name` form field. Without this edit A5 is not a UI change, it is a broken
  import. `import_service.commit()` already accepted `list_name`.
- **`app/services/campaign_service.py`.** `CampaignService.resolve_category()`
  is the wrapper `test_campaign_guardrails` pins as "the rule lives in the
  service". A wrapper whose signature is a subset of the rule it delegates to is
  a second, quieter version of that rule — which one you get depends on which
  name you called. Three lines.
- **`app/services/import_service.py`** — docstring only. `require_category()`
  named `/api/imports/preview` and `/commit` as its callers and they are no
  longer.
- **`app/models/contact_list.py`** is in the list; the Python-side `default` on
  it is the part A1 did not specify. See A1 above.

### What the review found (worked in session, no spawned reviewers)

The mutation harness earned its keep again: **16 mutations, one survived the
first run, and it was the one the spec singled out.**

**`A1b`, ordering by the raw string, survived — and the reason is the exact
contamination `accept-5e.sh` was built to find.** The ordering test read the two
fixture rows, but `test_the_migration_converts_a_space_row_and_leaves_a_t_row
_alone` runs earlier **in the same file** and rewrites one of them — that is its
job. So by the time the ordering test ran, both values were isoformat, where a
string comparison happens to give the right answer. The test passed under the
mutation it was written to catch. Both tests now restore the two spellings
themselves rather than trusting the fixture.

**Relaxing the category rule moved a malformed selector one step down the path,
onto an unmapped exception.** Before 5i, `POST /api/campaigns` with
`audience="list:not-a-number"` was refused by the category rule — the wrong
sentence, but a 400. With the rule relaxed for list audiences it reaches
`contact_service._int_arg()`, whose `ValueError` the router does not map, so it
became **a 500 reading "Could not create campaign"** with the real reason
("needs a numeric id") left in a log the client cannot read. That is the
"refusing without explaining reads as a broken tool" defect, arriving through a
door this session opened. `create_campaign()` now maps it to a `CampaignError`
carrying the selector grammar's own sentence, and mutation `A4c` puts the 500
back. Found by asking what *else* arrives on the widened path, which is the
lens `decisions/003` established.

Two smaller things from the same pass:

- **`_last_sent_overall()` had no `contact_id` filter** where the per-list query
  joins memberships. Nothing writes a message row with no contact today — all
  four construction sites pass one — but the pinned card's freshness is the one
  query that would silently start counting one.
- **The recency sort had no tiebreaker.** Two lists created in the same second
  fell back on whatever order the ungrouped query returned. Id descending makes
  "newest first" a total order.

Worked directly against the review's lenses:

1. **The set before the guard.** `list_cards()` slices to five *after* sorting
   the whole list set, so "the five most recent" is five one-off uploads from
   last week if that is what he made — which is the right answer for a card grid
   whose question is "when did I last text these people". The composer's picker
   is deliberately **not** capped: it is a dropdown, and every list he has used
   belongs in it. The cap is named `RECENT_LIST_CARDS` and lives on the dashboard
   side only.
2. **Two moments, one sentence-maker.** Proved by mutation `A2c`, which renames
   the constant and hard-codes the old string in `_term_label()`. Two tests go
   red. A test comparing three literals that happen to match would not have.
3. **A property proved of a helper is not proved of its caller.** The ordering
   and picker-shape assertions go through `GET /api/campaigns/audiences`, and
   criterion 5 goes through `POST /api/campaigns`. Mutation `A1b` fails on the
   endpoint.
4. **What else arrives on this path.** The freshness join runs over
   `contact_list_members` and **does not read `added_at`** — checked, and stated
   in `_last_sent_by_list()`'s docstring. That column has its own UTC/local
   defect and nothing in 5i touches it.
5. **What the fallback answers.** A list with no members reads 0 and is still
   offered; a list whose members were all deactivated reads 0 and agrees with
   `audience_count()`; a database with no lists shows the pinned card alone. All
   three are tested.
6. **The scan's own first version.** `test_the_sweep_actually_fires` runs the
   matcher against a body whose answer is known before the sweep is quoted as
   evidence.

**The composer's JavaScript was checked by execution, not by reading.** A3 and
A5 delete four functions and eight DOM ids across four templates, and a leftover
call to one of them is a `ReferenceError` that kills the whole screen while every
render test still passes — the HTML is fine and the failure is at execution.
Every `<script>` block on the changed pages was concatenated in include order,
run through `node --check`, and swept for identifiers called but never defined.
Clean: no `loadCategories`, `selectCategory` or `chipClass` survives a call site,
and every id the composer partials read is declared in `campaigns.html`.

Also checked and clean: `app/sms/` still imports nothing from the DB layer; no
table, column or index was dropped or renamed; the suite is green twice; every
new test passes on its own; and the mutation run is 15 caught, 0 survived, on a
tree verified byte-identical to the repo before the first patch.

### Deploy — read this first

**A1's migration rewrites data in a live table.** `scripts/backup.sh` runs before
`alembic upgrade head`, not after.

And **Part B of the session spec runs before the migration is trusted on
production**: dump every `contact_lists.created_at` there and confirm each value
is either `YYYY-MM-DD HH:MM:SS` or `YYYY-MM-DDTHH:MM:SS.ffffff`. A third spelling
means the classification is not 1:1 and the migration is wrong — stop and
escalate rather than converting. The migration leaves an unrecognised value alone
and logs it, so a third spelling is survivable, but it is survivable as an
un-normalised row that will sort by a clock nobody has checked.

### Found while working

- **`data/app.db` was migrated by starting the app, and that changed the
  developer's database.** `app/main.py` runs `alembic upgrade head` on import in
  development (production does not — `deployment/deploy.sh` does it as a
  deliberate step), so booting a local uvicorn to run CLAUDE.md's "Run it" check
  applied `f4a1c7d90e52` in place: `Demo list` went from `2026-08-20 00:12:30` to
  `2026-08-19T20:12:30`. That is the migration doing exactly what it is for and
  the result is correct, but it was not a deliberate act and it is worth knowing
  before anyone reads that database expecting the old value.
  <br>It also silently drained an acceptance check. `accept-5i.sh` check 2b
  demonstrates the conversion on a **copy** of `data/app.db` — and once the app
  has been started, there is nothing left in there to convert, so the check
  printed an identical before and after while still reporting "ok". A check whose
  evidence evaporates the first time somebody starts the server is a green light
  wired to nothing. It now puts the copy back into the state production is in
  before migrating it — a row carrying the exact server-default spelling, and
  `alembic_version` rolled back to `c8a2e5f14b90`, the revision the live box is
  on — and asserts that specific row converts to the same instant on the local
  clock. Seeding the row alone was not enough and the check said so: an `upgrade
  head` on a copy already stamped `f4a1c7d90e52` runs nothing at all.
- **`contact_lists.created_at` still has `DEFAULT (CURRENT_TIMESTAMP)` in the
  table.** Closed at the ORM layer (see A1) and left in the DDL, because removing
  it means rebuilding the table and that is escalation item 8. A raw `INSERT`
  that names no `created_at` still gets a UTC, space-separated value. Worth a
  line in whichever session next has cause to rebuild that table.
- **`app/services/campaign_service.py` is at exactly 500 lines.** The 5i
  passthrough had to be written into the space the old docstring occupied. The
  next addition to that file forces a split, and its own docstring already
  describes the seam that was used last time.
- **`import_service.require_category()` now has no caller.** It is a correct
  function stating a rule nothing enforces any more; its docstring says so. It is
  in a file outside 5i's list, so deleting it is a separate decision.
- **`pages.PAGE_CONTEXT["contacts"]` still computes `category_tabs` on every
  Contacts page load**, and after A5 nothing renders them. One wasted grouped
  query per page view. `pages.py` is in nobody's file list.
- **A per-contact "which lists is this person on" column is still absent** from
  the Contacts page. It is the right product answer, it is a new query on a paged
  screen, and `sessions/session-5i.md` puts it explicitly out of scope. Noted in
  `contacts.html`'s own header comment too, so the next person to open that file
  finds it.
- **`test_whitelabel.py:104` reads `"id"` off a `/api/lists` entry**, and those
  entries have never had one — they carry `selector`. So `PATH_VALUES["list_id"]`
  has always been the `999999` fallback. Harmless today, because no GET route has
  a `{list_id}` parameter and the coverage test only fires on GET routes; it
  would matter the day one is added. Pre-existing, and `test_whitelabel.py` is
  not in 5i's list. `tests/test_audience_surfaces.py` computes it from the
  selector.
- **`docs/API.md` does not describe any of 5i.** `/api/campaigns` lost four
  fields, `/api/dashboard` renamed `categories` to `lists` and `next_up.category`
  to `next_up.list`, `/api/campaigns/audiences` and `/api/lists` changed shape,
  and `/api/imports/commit` gained `list_name` and stopped requiring
  `category_id`. Docs are module 8's.

---

## Incident — 5i deployed, composer unusable for 25 minutes, 2026-09-04

**Resolved by two indexes created by hand on production. No rollback, no data loss.**

### What happened

5i deployed clean at 16:23 — migration ran (`0 converted from UTC, 21 left alone`,
exactly as Part B predicted), health check green. A click-through of the live composer
found the audience dropdown and the Recent campaigns rail both stuck on `Loading…` and
never resolving. No JavaScript errors; every request eventually returned 200.

`contact_service.list_summaries()` timed on the box: **10 minutes 2 seconds.**

### Why

`_last_sent_by_list()` joins `contact_list_members` to `sms_messages` on `contact_id`,
and **neither side was indexed on that column**:

- `contact_list_members` has `idx_member_list` on `list_id` alone. Its unique
  `(list_id, contact_id)` index has `list_id` leading, so it cannot serve a lookup
  by `contact_id`.
- `sms_messages` had `idx_sms_campaign`, `idx_sms_status`, `idx_sms_sent_at` — nothing
  on `contact_id`.

~30,000 message rows scanned against ~15,500 membership rows on one vCPU.

### The part that mattered more than the dropdown

`list_summaries()` is a **synchronous** query inside an `async def` route, so it held the
event loop while it ran — and `run_due_campaigns` is on that loop. The journal shows
`Run time of job "run_due_campaigns" was missed by 0:00:24` and `0:00:38` during the
window. **A reporting screen delayed the send path.** Both ticks recovered; a full
ten-minute block could have skipped one outright.

The composer's Recent campaigns rail also polls, so roughly twenty
`GET /api/campaigns?limit=8` stacked up behind one blocked request.

### The fix

Backup, then two additive indexes and an `ANALYZE`, applied directly to production:

```sql
CREATE INDEX IF NOT EXISTS ix_sms_messages_contact_id ON sms_messages(contact_id);
CREATE INDEX IF NOT EXISTS ix_clm_contact_id ON contact_list_members(contact_id);
ANALYZE;
```

**10m02s → 0.95s**, most of which is interpreter start-up. Composer verified by hand
afterwards: pinned entry renders `⭐ ALL BIDDERS — MAIN LIST — 9,747 contacts`, "Most
recent first." beneath it, no category fieldset, no cross-category checkbox, Recent
campaigns populated, summary panel showing the audience.

**Production's schema now leads the migration history by two indexes.** 5j must add them
as a real migration written to tolerate their already existing — `op.create_index` will
raise on the live box otherwise, and the deploy aborts on a failed migration.

### Why nothing caught it

The test database has twelve rows where production has forty-five thousand across that
join. The gate has no timing check. The mutation harness proves behaviour and is silent
about cost. And the session spec's review lenses — mine — asked whether
`list_summaries()` returned the *right* set and never asked what it cost. Folded into
`CLAUDE.md`.

### Also confirmed working

The campaign rail's subtitle reads `Estates`, `Memorabilia` for pre-5i campaigns. That is
`audience_label()` resolving a historical `category:<slug>` selector to the category's
**label** — clause 2 of the retention decision doing exactly its job. The word "category"
appears nowhere, which is why the A7 sweep passes over `/api/campaigns` while that text
is on screen.

---

## Module 5j Part A — the index migration, the cost guard, list archive/rename (appended by session 5j, 2026-09-04)

Forced into existence by deploy day. `bash agent/accept-5j.sh` is the stop
condition; criteria 1-10 pass locally and every criterion is printed.

**613 tests** (577 + 36 new). `bash agent/gate.sh` passes, twice.
`agent/mutate-5j.py`: **19 mutations, 0 survived, 0 failed to apply**, on a
scratch tree verified byte-identical to the repo before the first patch.

Two migrations, deliberately not one:

| revision | what | reversible |
|---|---|---|
| `a3f1e08c5d47` | `idx_sms_contact`, `idx_member_contact`; retires the hand-made names | yes — downgrade drops both |
| `b52d9c1f4e08` | `contact_lists.archived` | yes — downgrade drops the column |

A reviewer can revert either without the other. Nothing else was dropped or
renamed.

### A1 — the migration that has to be right on two different databases

Production's schema leads the migration history by two indexes created by hand
on 2026-09-04. The migration inspects before it acts —
`sa.inspect(op.get_bind()).get_indexes(table)`, not SQLite's `IF NOT EXISTS`,
because this project is Postgres-ready through `DATABASE_URL` — creates the
convention-named index if absent, then drops the hand-made one if present.

**Create before drop, per table.** Both orders converge on the same schema, so
no assertion about the *result* can tell them apart; only the DDL the migration
issues can, and `test_the_new_index_is_created_before_the_hand_made_one_is_dropped`
captures it through a `before_cursor_execute` listener. The box is live during
business hours with a composer that polls, and the wrong order opens a window in
which the join that caused the outage runs unindexed.

`downgrade()` does **not** recreate the hand-made names. They were an incident
response, not a schema; recreating them would put a rolled-back box back into
the state nothing describes, which is how this session happened. Mutation `A1d`
puts that back and a test names it.

Both indexes are declared on the models as well. The spec's reason ("a fresh
test database is built by `create_all()`") is no longer true — `tests/conftest.py`
builds it with `alembic upgrade head` — but the requirement is right for a better
reason: `test_migrations_match_the_models` compares the two, so an index in the
migration and not in the model is drift that the next autogenerated revision
would propose dropping. Mutation `A1c` is caught by `alembic check` and by
`test_the_convergence_is_declared_on_the_models_as_well`.

### A2 — the cost guard, and why its assertion is not the one the spec named

**This is the finding of the session and it needs a ruling if you disagree with
what shipped.** `sessions/session-5j.md` A2 specifies the assertion as *"no full
scan of `sms_messages` or `contact_list_members`"*. Measured against real plans
before it was written, that rule is **wrong in both directions**.

At production row counts (9,700 contacts, 30,000 messages, 15,500 memberships,
seeded and timed — see below), the **outage** plan is:

```
SCAN contact_list_members USING COVERING INDEX sqlite_autoindex_contact_list_members_1
SEARCH sms_messages USING INDEX idx_sms_status (status=?)     <- 39s on this machine
```

There is no scan of `sms_messages` in it. `status` has four distinct values, so
each "search" walks a quarter of the table — SQLite is using an index, and the
query still took ten minutes on the live box. **"No full scan of `sms_messages`"
is green on the plan that caused the incident.** And the plan that *ended* the
outage still scans `contact_list_members` outright, so the other half of the rule
is **red on the schema that fixed production**.

What separates the two plans is the column the index is keyed on. So the guard is
two assertions, in `tests/test_query_cost.py`:

1. **The plan** — the join reaches one of the two tables through an index keyed
   on `contact_id` (`SEARCH … (contact_id=?)`), for the statement
   `_last_sent_by_list()` actually issues.
2. **The schema** — *both* sides of the join key carry an index whose **leading**
   column is `contact_id`. Both, because which side SQLite searches is a
   statistics decision that flips with row counts: at suite scale it searches
   `contact_list_members`, at production scale it searches `sms_messages`. An
   index on only today's favoured side is one `ANALYZE` away from being the
   wrong one, and the plan check alone would not notice.

Assertion 2 is also what makes acceptance criterion 8 work **as written**:
dropping `idx_member_contact` alone leaves the plan green (the other index covers
it) and turns the schema assertion red. Criterion 8 runs A2's own test functions —
not a copy of their assertions — against alembic-built scratch databases with the
indexes dropped, and prints the plan and the verdicts. It also prints the spec's
rule evaluated on the outage plan, so the reason it was not used is on the
transcript rather than in a comment:

```
"no full scan of sms_messages" on the OUTAGE plan: GREEN — it would not have caught it
```

**The guard reads the statement the service issues.** `tests/_query_plan.py`
captures SQL through a `before_cursor_execute` listener wrapped around a callable
that runs the service; nothing hand-writes a copy of the query.
`test_the_guard_reads_the_statement_the_service_issued` proves the capture is not
a quote by pointing it at a different runner and requiring different SQL. Mutation
`A2b` is the spec's own: it hands the guard a hard-coded copy **and** degrades the
service's query — and, exactly as the spec predicted, the plan assertion stays
green and only that one test fails. That is the argument for building the guard
the harder way, and it is in the harness output.

`_last_sent_by_list()`'s docstring said the join was "messages → contacts →
memberships". There is no `Contact` in that query. Fixed, and the cost, the event
loop and the index are written next to it.

### A3-A6 — archive, rename, delete

- **`archived`**: Python-side default, **no server default**. `created_at` two
  fields up is the cautionary tale — its `DEFAULT (CURRENT_TIMESTAMP)` is still in
  the DDL because removing it means rebuilding the table. Existing rows are
  backfilled to 0 so the column reads one way, and `is_archived()` still treats
  NULL as "not archived" because a raw `INSERT` can still produce one.
- **One definition of "archived"**, `contact_service.is_archived()`, read by both
  surfaces: the picker states it by **absence** and A5's panel states it with a
  badge. The test asserts the two agree *about the same list* rather than against
  a literal. Mutation `A3d` inverts the predicate; 15 tests go red.
- **One read, two readers.** `_list_rows()` is the shared query;
  `list_summaries()` drops the archived rows and prepends the pinned entry,
  `list_admin.admin_summaries()` keeps them and marks them. The panel and the
  dropdown cannot report different counts. Mutation `A3c` makes `list_cards()`
  query lists directly; 5 tests go red.
- **Still resolving for history.** `resolve_audience()`, `_term_ids_query()`,
  `_term_label()` and `audience_count()` never read the flag. Mutation `A3b`
  teaches `_term_ids_query()` about it and criterion 4 goes red.
- **A rename lands everywhere — and the spec's premise was half true.**
  `_term_label()` does look the name up live, but `campaigns.audience_label` is a
  **stored** render written once by `campaign_builder`, and the rail, history and
  the report all read that column. So renaming a list would have left three
  screens quoting a name the client had just replaced. `list_admin.rename()`
  treats that column as a cache of `audience_label()` and recomputes it for every
  campaign whose selector names the list — rebuilt from `audience_label()`, never
  patched by substituting the new name into the old string, so a compound
  selector keeps its other term. `report_service`, `history_service` and
  `routers/campaigns.py` are outside 5j's file list and were not touched.
- **"Referenced" is decided by `_split_terms()`**, not by a substring test:
  `category:estates&list:12` names list 12, and `"list:12" in "list:120"` is the
  unanchored-substring mistake this project has now paid for three times.
- **The refusals name a remedy that works on *this* object** (decision 006). A
  name collision says which list holds the name and — differently — what to do
  when that list is one he has already archived, because "archive that one first"
  is useless advice then. `DELETE` refuses a referenced list with 409, names the
  campaigns, and says archiving is what he wants.
- **A PATCH carrying both fields applies neither when the name is refused.** The
  rename runs first because it is the half that can be refused.

### Lens 1 — what each query costs at production scale

Seeded to the live box's counts (9,700 contacts · 30,000 messages · 15,500
memberships · 11 lists · 400 campaigns), `ANALYZE` run, measured:

| query | time | what serves it |
|---|---|---|
| `_last_sent_by_list()` | **0.019s** (was 10m02s) | `idx_sms_contact` on the join key |
| `_list_rows()` | 0.019s | `contact_lists` scanned (11 rows), members by `(list_id,…)`, contacts by rowid |
| `list_summaries()` — picker, `/api/lists` | 0.020s | as above |
| `admin_summaries()` — A5 panel | 0.018s | the same read |
| `list_cards()` — dashboard | 0.019s | the same read |
| `campaigns_using()` — rename, delete | 0.003s | full scan of 400 campaigns, in Python, on a click |
| `dashboard()` — whole Today screen | 0.049s | |

`campaigns_using()` loads every campaign row and filters in Python because `LIKE`
cannot tell `list:12` from `list:120`. At roughly one auction a day that table is
in the hundreds; if it ever reaches five figures the fix is a `LIKE` prefilter
with this predicate still deciding, not this predicate replaced. Written into its
docstring.

### Lens 2 — the set before the guard

Everything that reads `list_summaries()`: `/api/lists`, `/api/campaigns/audiences`
and `dashboard_service.list_cards()` — and, through the cards,
`last_send_outcomes()` and `next_up()`. All of them are "what may he pick", so all
of them should lose an archived list, which is criterion 5. Nothing reads that set
expecting everything; the one reader that needs the archived rows,
`admin_summaries()`, takes the shared `_list_rows()` instead of a second query.

### Found while working

- **Uploading a CSV under the name of an archived list writes into that list, and
  it stays hidden.** `import_service.commit()` and `create_contact` both go
  through `contact_service.get_or_create_list()`, which matches on name and knows
  nothing about `archived`. The send still works — the campaign-first flow points
  the campaign at the list directly and `resolve_audience()` ignores the flag —
  but the list he has just uploaded into is not in his dropdown afterwards. A5's
  panel shows it with an Unarchive button, so there is a one-click remedy on the
  same screen. **Not fixed, because it is a behaviour decision, not an oversight:**
  "writing into a list un-archives it" is defensible and so is "an archive stays
  archived until he says otherwise" — and the second matters to anyone who
  archives a list that a recurring import keeps feeding. `get_or_create_list()` is
  in 5j's file list, so this is a one-line change whenever it is ruled on.
- **`esc()` is not enough inside a quoted attribute, and 5j made that reachable.**
  `base.html`'s `esc()` escapes `<`, `>` and `&` via `textContent`/`innerHTML`, and
  not the double quote that ends an attribute. `_composer-script.html` was
  interpolating a list label into `data-label="${esc(a.label)}"`. It has always
  been a hole; what changed is that A5's rename box now lets the client type the
  string, so `Bob" onmouseover=…` is a name he can enter. A local `attr()` helper
  escapes `&quot;` and both call sites use it, with
  `tests/test_composer_markup.py` sweeping the composer templates for the shape
  (and firing against a known-bad string first). `base.html` is outside the file
  list and was not touched; other templates using `esc()` in attribute position
  are worth a sweep in whichever session owns them.
- **`escalation item 8 names "index changes on `contacts` or `sms_messages`".**
  A1 adds one to `sms_messages`. Proceeded because the session spec names the
  index, the table and the reason explicitly and the session exists for it; the
  change is additive, its downgrade is exercised by criterion 3, and no existing
  index was rebuilt or renamed. Flagging it so the rule is seen to have been read.
- **`app/services/list_admin.py` is new and is not in 5j's file list.**
  `contact_service.py` was at 482 lines before A4/A5/A6 and the 500-line rule is a
  hard one, so the additions had to split somewhere. The seam is the natural one:
  `contact_service` answers "what is this audience and who is in it", `list_admin`
  answers "what may the client do to the list itself". It imports `_split_terms`
  and `_int_arg` rather than re-implementing the selector grammar — two
  definitions of what `category:a&list:12` names is how a rename and a delete come
  to disagree about the same campaign.
- **`app/services/campaign_service.py` is still at exactly 500 lines.** Untouched
  this session. Residual 2 in `modules.md` stands.
- **Starting the app migrated `data/app.db` again.** `app/main.py` runs
  `alembic upgrade head` on import outside production, so the "Run it" check
  applied both 5j revisions to the developer's database. Expected, and harmless
  here: unlike 5i, neither revision rewrites data, and `accept-5j.sh` has no
  live-copy check whose evidence could evaporate. Its header says so.

### Verified this session

- `bash agent/accept-5j.sh` — **ACCEPT PASS**, criteria 1-10 printed.
- `bash agent/gate.sh` — green twice, 613 passed each time.
- `agent/mutate-5j.py` — 19 mutations, 0 survived, on a tree printed
  `SCRATCH VERIFIED PRISTINE`.
- Migrations: fresh clone, production's shape (twice through), downgrade of both
  revisions, then a clean `upgrade head`.
- `./run.sh` then `/login` → 200, `/static/app.css` → 200 (after
  `npm run build:css`), `/health` → `sending_ok: true, send_mode: dry_run`.
- The composer driven by hand against the running box: rename (2 campaign labels
  followed), a name collision (400, naming the name), `DELETE` on a referenced
  list (409, naming the campaigns), archive (gone from the picker, campaign label
  intact), unarchive. State restored afterwards.
- Composer JavaScript checked **by execution**, not by reading: all six script
  blocks concatenated in include order, `node --check` clean, no identifier
  called but never defined, no DOM id read but never declared.
- `app/sms/` still imports nothing from the DB layer; no table, column or index
  was dropped or renamed; every new test passes on its own.

---

## Module P2 Part A — the Google Places source, 2026-09-04

The first real discovery source. P1 built the pipeline, the line-type gate and
the review queue; this fills them, under two independent spend meters.

`bash agent/gate.sh` passes, twice. **696 tests** (613 + 83 new).
`bash agent/accept-P2.sh` is the stop condition; criteria 1-9 and 6b pass
locally, criterion 10 is `--with-remote` and needs the deploy.
`agent/mutate-P2.py`: **36 mutations, 0 survived, 0 failed to apply**, on a tree
verified byte-identical to the repo.

### decisions/009, implemented

**Shell wholesalers, importers and distributors are buyers and they are the
priority group.** They are not in the exclusion list; they are `shell_wholesale`,
`priority=1`, national, running two sweeps. Every other exclusion stands.

The reversal is guarded **in the direction the plan was wrong**, because the next
session to read the plan's old wording is the risk:

- `test_a_shell_wholesaler_importer_or_distributor_is_accepted` runs
  `excluded_reason()` over the five real companies `decisions/009` quotes —
  Atlantic Coral Enterprise, US Shell, Worldwide Wildlife Products, California
  Seashell, Blue Seas Trading — and requires all five to pass.
- `test_no_exclusion_phrase_names_a_wholesaler_importer_or_distributor` is the
  structural half: it fails if any phrase in the list contains `wholesal`,
  `importer`, `distribut`, whoever the victims would have been.
- `agent/mutate-P2.py` `X3` re-adds the exclusion and requires the suite to go
  red for it. It does: 10 tests.

**Radius is a property of the search-term group**, with the category as its
fallback (`taxonomy.radius_for_group()`), because one category now carries two
national groups and one regional one. Asserted individually, and the property
behind the three assertions is asserted too — `test_no_single_value_satisfies_
all_three_seashell_groups` fails if the three ever resolve to one number, which
is the defect criterion 6b exists to catch.

| group | priority | radius | sweeps |
|---|---|---|---|
| `shell_wholesale` | 1 | national | national + gulf_coast |
| `shell_makers` | 1 | national | national + gulf_coast |
| `shell_aggregate` | 2 | **150 miles** | home |
| `shell_retail` | 4 | national | national |

**The two national groups state `radius_miles=None` rather than inheriting it**,
and so do `memorabilia_dealers` and `marine_trade`. The fallback reads
`PROSPECT_CATEGORY_RADIUS_MILES` from `.env`, and a box whose `.env` pins the old
five-key map would otherwise run the priority group at 150 miles with nothing on
any screen saying so. The config default gained `marine: null` and
`seashells: null` as the spec requires, and `.env.example` gained them too — but
belt and braces, because the .env is the one file this session may not read on
the live box.

### Two meters, because they are two bills

- **Google**: $35 per 1,000 Text Search requests, first 1,000 of a calendar
  month free. `GOOGLE_PLACES_MONTHLY_REQUEST_CAP` defaults to exactly the free
  allowance, is checked **before** every request, and refuses cleanly: the job
  is `completed` with `api_requests_skipped > 0`, everything already found is
  persisted, and one ERROR line names the count and the remedy.
- **Carrier lookups**: P1b's cap, called through, not duplicated.

**The Google meter needed its own dedup, and that is the half that is easy to
miss.** A request is charged for *asking*, so nothing downstream can save a
repeat — the cache, the rejection list and the contact check all stop a second
$0.0025 and none of them stops a second $0.035. So a search already run inside
`GOOGLE_PLACES_QUERY_REPEAT_DAYS` is not run again, and the ledger is
`scrape_jobs.search_term` on a **completed, uninterrupted** job. That is why
`run_plan()` runs **one job per search**: the ledger, the per-search cost
attribution A3 asks for, and an exact monthly meter across runs all fall out of
it, with no shared mutable state between searches.

A second identical run is asserted on call counts to make **zero** paid calls of
either kind.

### Two defects the in-session review found, and both were mine

Neither was found by reading. One was found by working through the arithmetic of
a figure nothing yet displays, the other by asking what a query does a year from
now.

1. **`run_plan`'s `skipped_cap` double-subtracted the already-searched.** It was
   `len(plan) - index - skipped_recent`, and those searches sit at indices below
   the break, so the slice has already excluded them. Correct on a first run,
   under-reporting on every run after — which is the only kind of run that
   figure is read on. Now counted by walking what is left.
2. **The "was it interrupted" clause was unbounded in time.** It subtracted
   every term *ever* interrupted from the fresh set, so a search the cap stopped
   in March and that completed cleanly in April would have stayed out of the
   ledger permanently — re-run, and paid for, every night forever. It is now one
   clause of the freshness query, with `is_(None)` beside it because
   `api_requests_skipped = 0` is NULL, not true, for every job row written
   before this session's migration.

Both have a test that goes red against the pre-fix tree (verified by reverting
each in a scratch copy) and a mutation in `agent/mutate-P2.py` (`C8`, `D6`, plus
`D3` for the NULL branch).

### The mutation harness caught a guard that decided nothing

`RequestBudget.allows()` shipped as `if self.cap <= 0: return False` above
`return self.remaining > 0` — the same shape as `LookupBudget.allows()`, where
it is load-bearing because a cost can be zero. The mutation reverting it
**survived**, and the reason is that with an integer request counter and a
non-negative spend there is no arrangement in which that `if` decides anything
the comparison below it would not have decided the same way. It read like a
guard and was a comment with an `if` in front of it.

This is P1b's lesson from the other end. There the fix was to construct the
arrangement that reaches the guard; here no such arrangement exists, so the
guard is gone and the rule now lives in the one expression that holds the
ceiling: `return self.spent < self.cap`. `C3` mutates that to `<=` — the
off-by-one that turns a cap of zero into a cap of one — and five tests catch it.

### Edits outside P2's file list in `modules.md`

`modules.md` lists `app/sources/google_places.py`, `app/sources/taxonomy.py`,
`app/core/config.py`, `agent/mutate-{1,5d,5f,P1}.py` and `tests/`. 5d and P1 set
the precedent for recording what forced each departure.

| file | what forced it |
|---|---|
| `app/sources/exclusions.py` (new) | A1's "make the list one shared definition". It is enforced from `prospect_ingest`, which must not import a module that reaches back into `app.services` — and `taxonomy.py` does, for the radius fallback. Splitting it also kept `taxonomy.py` off the 500-line rule. |
| `app/services/prospect_ingest.py` (new) | The 500-line rule. Criteria 6 and 7 add the exclusion check and the already-a-contact check to `record_prospect()`, and `prospect_service.py` was at 451 lines — P1's own status note says the next addition forces the split. Ingestion moved; review outcomes stayed. |
| `app/services/prospect_service.py` | The other side of that split, and its docstring. |
| `app/services/api_budget.py` (new) | A3's Google request cap. A source may not query the database and the ceiling has to be checked inside the paging loop, so the meter is read by the runner and handed in — `LookupBudget`'s seam exactly. |
| `app/services/scrape_runner.py` | A3. The runner builds the request budget, records both meters on the job, and owns `run_plan()` and the search ledger. |
| `app/sources/prospect_base.py` | Criteria 6 and 7 add two outcomes, so `ProspectIngestResult` needs two counters — the base class refuses an outcome it cannot count, by design. |
| `app/models/scrape.py` + `alembic/versions/e7c05b3a1d94` | A3's "record per job what was attempted, produced and spent", and the monthly request cap needs a persistent counter. Five additive nullable columns, backfilled, no index, no server default. |
| `app/sources/__init__.py` | `prospect_base`'s own instructions: "Register it in app/sources/__init__.py". |
| `.env.example` | The radius map is pinned in that file, so a box built from it would not have picked up the `marine` and `seashells` keys the spec requires. |
| `tests/test_prospect_review.py`, `tests/test_lookup_provider.py` | Fallout, not scope. One fixture was named "Coastal Estate Liquidators", which the exclusion list now stops at ingest; renamed, with a note saying why a fixture for the *rejection* rule must not be a name the *exclusion* rule catches. The other referenced `prospect_service.is_suppressed`, which moved. |

### A4 — the pristine check, back-ported

`CLAUDE.md` states that every mutation harness verifies its scratch tree and
prints `SCRATCH VERIFIED PRISTINE`. Before this session that was true of **two
of seven**: `mutate-P1b.py` and `mutate-5j.py` (and `5i`, which copied P1b).
A documented guarantee that is true in two places is worse than none, because
it gets quoted.

**The spec names `mutate-1` and `mutate-5d`, and neither exists.** The repo is
the state: the harnesses lacking the check were `5e`, `5f`, `5g`, `5h` and `P1`
— five, not four. All five now carry it, and `accept-P2.sh` check 8 asserts the
property over **every** `agent/mutate-*.py`, so a harness added tomorrow is
covered without anybody remembering.

Check 8 proves it fires rather than merely existing: it dirties every `.py` file
in a scratch tree and requires each of the nine harnesses to exit 2 naming the
files. That works because a harness that refuses exits before applying a patch,
so one dirty tree serves all nine and nothing needs restoring. Check 8b runs
`mutate-5g.py` on a clean tree so the check is not merely "everything always
exits 2".

*(The first version of check 8 parsed each harness's `MUTATIONS` with
`ast.literal_eval` to choose a victim file, and broke on `mutate-5j.py`, which
names its path through a variable. A check that has to understand the thing it
is checking is a check with its own bugs — the sixth time in this project a
measurement script has been wrong before the code was.)*

### Query cost, measured before it is a screen

`CLAUDE.md`'s 5i lesson is to time a new query against production-scale row
counts rather than the fixture. Two new queries, measured against **32,485
`scrape_jobs` rows** — a year of nightly runs at one job per search:

```
searched_recently        5.9 ms   -> 89 terms    SEARCH ... USING INDEX idx_scrape_jobs_status
spend_this_month         3.0 ms   -> 712         SCAN scrape_jobs
```

`spend_this_month` full-scans, and it is called once per job — about 270 ms
across an 89-search sweep at a year of history. Both are off every request path:
**nothing calls `run_plan()` from a route or the scheduler**, so neither query
can hold the event loop the way 5i's freshness join did. No index was added for
a query nothing waits on. **If a route or a scheduled job is ever wired to
`run_plan()`, that is the moment this becomes 5i's defect**, and the fix is a
worker rather than an index.

### Found while working

- **`agent/mutate-P1.py` had rotted too, and one of its mutations is now
  vacuous.** Two separate things, and only the first was repaired here.
  <br>`R5` — "the cache is never read" — stopped applying when P1b added
  `"skipped": None` to `line_type_for()`'s cached return, so that mutation had
  been silently not-run ever since. The anchor is repaired; `mutate-P1.py` is in
  this session's file list and was already open for the `prospect_ingest`
  repointing. With it fixed the harness reports **40 mutations, 0 failed to
  apply, 1 survived** — R5 is caught by
  `test_the_single_number_path_reads_the_cache_too`, and the survivor is R12b
  below. The four mutations P2 repointed at `prospect_ingest.py` (R14, R22, R23,
  R23b) are all caught by tests that name them.
  <br>`R12b` — "the screening pass pays to look up numbers a human already
  rejected" — **survives**, and that is a finding rather than a hole. It reverts
  `_screen()`'s `status == "pending"` filter, whose stated justification is the
  $0.0025 a rejected number would cost. P1b then put `unusable_numbers()` inside
  `line_type_for()`, at the one place a call is actually made, so a rejected
  number is refused there whether or not the filter above it ran. The money is
  saved by the lower guard now, and no test can see the upper one. **A guard
  whose only justification is money, sitting above a guard that saves the same
  money, is a filter rather than a guard** — it still avoids a query and a
  rescore loop, and the comment above it should say that instead. Left alone:
  deciding what that filter is now for is P1's scope, not P2's.
- **`agent/mutate-5e.py` and `agent/mutate-5h.py` have bit-rotted.** Five and one
  patch respectively no longer apply, because 5i and 5j moved the code they name:
  `campaign_service.py` (`scheduled_at`), `campaign_builder.py`
  (`cross_category_override or list_audience`) and `campaign_topup.py` (three
  anchors, plus `SMSMessage.top_up_at.isnot(None)`). Both harnesses now *report*
  it — "PATCH DID NOT APPLY … fix the harness, not the code" — and exit 1, which
  is correct behaviour and a broken tool. Not repaired here: understanding what
  5i/5j changed about the top-up path is that module's work, not P2's. The
  pristine back-port is unaffected — it runs before any patch is applied.
- **A national group's prospects are scored against a 150-mile radius.**
  `marine` and `seashells` have no `categories` rows, so `_category_id_for_slug`
  leaves those prospects uncategorised, and `prospect_scoring.radius_for(None)`
  returns `PROSPECT_DEFAULT_RADIUS_MILES`. The *search* is national — the source
  applies the group's rule and drops nothing — but the *score* gives a
  962-mile-away wholesaler 0 of 15 distance points while a Florida one gets up to
  15. The priority group therefore sorts below local retail at equal line type.
  Bounded (15 of 100, and line type is 60) and not a correctness bug, but
  contrary to `decisions/009`'s intent. Two fixes, both outside P2's file list:
  seed `marine` and `seashells` as categories (a product decision — the palette
  is escalation item 9), or carry the applied radius on the prospect row beside
  `search_term` and `buyer_rationale`, which are denormalised there for exactly
  this reason. **First item for whichever session next touches
  `prospect_scoring.py` or the category seed.**
- **`PROSPECT_ORIGIN_LAT/LON` is silent when wrong.** A mistyped origin moves
  every regional search and every distance without any symptom. No sanity check
  was added — a plausible-coordinates assertion is a requirement nobody stated.
- **There is still no jobs screen**, so a discovery run that the cap stopped
  early is only visible in the log and in `scrape_jobs`. When one is built,
  `api_requests_skipped` and `run_plan()`'s `skipped_cap` are what it must show,
  and `api_cost`, `cost` and `scrape_jobs.error` are what it must not.
- **`app/services/scrape_runner.py` is 451 lines.** The next addition forces a
  split, and the seam is already visible: the plan and the ledger on one side,
  the deadline-and-cleanup job runner on the other.
- **The job counters record what was persisted, not what the API returned.** A
  request that produced three prospects and a request that returned three places
  are the same row. The difference — places returned, places with no phone,
  places outside the radius — is logged per page rather than given a sixth
  column, because there is no jobs screen for it to appear on and the systemic
  case (a key without Enterprise tier, so *no* result carries a phone) already
  logs at ERROR. If a jobs screen is ever built, that log line is the shape of
  what it should show.
- **`docs/API.md` still does not document the prospects API**, nor 5f's reports
  and links routes. P1 recorded this; P2 added no route, so it is unchanged and
  still worth one pass.

### Deliberately not built

- **Any real API call.** The spec forbids it and `accept-P2.sh` check 2b asserts
  it structurally: no test constructs a live `PlacesClient`, names a real carrier
  provider, or sets a Places key that does not name itself as fake. The fixture
  file says plainly that it was **written to the documented schema, not recorded
  from a call** — saying "recorded" about a file that was typed is the kind of
  claim this project keeps finding underneath a green check. What the repo *can*
  check is that the field mask, the parser and the fixture agree with each other,
  and `test_the_field_mask_asks_for_everything_the_parser_reads` does.
- **A scheduler or a route for `run_plan()`.** The first live run is Jordan's,
  one category, smallest radius, cap low.
- **Enrichment, registries and marketplaces.** P3.
- **A "promote anything unscreened" path.** Unchanged from P1, and still the
  cheap error.

## Module B1 Part A — Stripe: settle August, then auto-bill usage (2026-09-07)

One checkout that does two things: it charges the balance outstanding from before
billing was automated, and it attaches the card every month afterwards is billed
against. The client does it once and is never invoiced by hand again.

`bash agent/gate.sh` passes, twice. **764 tests** (696 + 68 new).
`bash agent/accept-B1.sh` is the stop condition; criteria 1-11 pass locally,
criterion 12 is `--with-stripe` and needs Part B and a test-mode key.
`agent/mutate-B1.py`: **47 mutations, 0 survived, 0 failed to apply**, identical
on two consecutive invocations, each on a tree verified byte-identical to the
repo. Nine of the forty-seven (`R1`-`R8`, `R4b`) exist because a fresh-context
review found defects in a tree where the first thirty-eight were all green.

### A1 — the allowance is subtracted once, and it is subtracted in Stripe

The requirement that under-bills silently when it is wrong, and the reason it is
worth naming twice. `billable_segments()` is `max(0, segments - 10,000)`, which
is precisely what a graduated tiered price does. So the meter receives the **raw
count of segments in `BILLABLE_STATUSES`** and the tier applies the allowance.
Report `billable_segments()` and a 15,000-segment month invoices $0 instead of
$75 — while `/usage` goes on showing 15,000 used and $75 due, which is why
nothing on any screen would look wrong.

`campaign_segments()` reads `BILLABLE_STATUSES` **through the model module**
rather than binding it at import. That is not style. A local restatement of the
tuple is behaviourally identical today, so no assertion about its *contents*
could ever tell the two apart — 5i's lesson, arrived at the hard way when
`SENT_STATUSES is not BILLABLE_STATUSES` failed for a reason that had nothing to
do with this codebase. Late binding is what lets
`test_the_meter_follows_the_models_definition_of_billable` change what the
constant *means* and require the metered figure to follow.

### Three spec clauses named fields the pinned SDK does not have

`decisions/010`, open. All three were checked against `stripe==15.6.1` before a
line of code was written, and each check now runs on every suite invocation in
`tests/test_stripe_contract.py`:

- **A3's `subscription_data.add_invoice_items` is not a Checkout parameter** and
  never was — it belongs to `Subscription` and `SubscriptionSchedule` (confirmed
  against `stripe==11.6.0` too, so this is not a removal). The August balance
  therefore rides as a **second line item** with `quantity=1`. A3 asked for this
  verification rather than a memory, so nothing is superseded; Part B item 3 does
  need a one-time price (`STRIPE_PRICE_BALANCE`).
- **A2's `unit_amount` is an integer number of cents**, and $0.015 is one and a
  half of them. A check written to the letter would report disagreement on a
  correctly configured price — and the tempting repair, hit on a live box, is to
  relax it until it agreed. `unit_amount_decimal` first, `unit_amount` as the
  whole-cent fallback, compared in `Decimal` because `0.015 * 100` is
  `1.4999999999999998`.
- **A4's `subscription.current_period_start` was moved onto the subscription's
  items.** Written to the letter it reads `None`, no anchor is ever stored, and
  `/usage` silently keeps reporting `BILLING_CYCLE_DAY` — the exact 1st-versus-9th
  disagreement A4 exists to prevent, arrived at *through* the fix. The window
  comes from the item's period; the day comes from `billing_cycle_anchor`, which
  is also what stops a February period start flattening an anchor of the 31st to
  the 28th for good.

Third consecutive session to hit "a spec clause that names a mechanism is a guess
wearing a spec's authority" (`decisions/007`, `008`). What B1 adds is where to
look: **a pinned SDK's declared parameter types are checkable offline, on every
run**, and they settled all three before any code existed.

### What is where, and why the file list is wider than `modules.md` carried

- `app/services/stripe_billing.py` — the account's link to Stripe: the one object
  that touches the SDK, checkout, `session_is_ours()`, the webhook, the anchor.
- `app/services/stripe_meter.py` and `app/services/stripe_tiers.py` — **both
  new and neither in the spec's list.** One session's subject split into three
  files by the 500-line rule, each time along a boundary the code already had:
  `stripe_billing` owns *this account's link to Stripe* (checkout, the ownership
  guard, the webhook, the cycle anchor); `stripe_meter` owns *what Stripe is
  told* (the meter, the backfill, `period_usage()`); `stripe_tiers` owns
  *whether Stripe is configured to price it the way we are* (A2's drift check,
  the five states, `/health`'s wording). The dependencies run one way and
  nothing imports back. `stripe_tiers` came out last, when the review's fixes
  pushed `stripe_meter` to 533 lines.
- `app/templates/base.html` — **not in the spec's list.** One nav tuple and one
  icon arm, under Account beside Usage. A page the client cannot reach from the
  nav is a page he does not have, and `base.html`'s own comment documents this
  exact operation for restoring an absent entry.
- `tests/conftest.py` — blanks the four Stripe settings for the whole suite, the
  same rule that forces `SMS_PROVIDER=console`.
- `tests/_stripe_fixtures.py`, `tests/test_stripe_billing.py`,
  `tests/test_stripe_contract.py`, additions to `tests/test_whitelabel.py`.
- `tools/` did not exist. It does now, holding `bill_period.py`.
- `app/services/link_service.py` — **not in the spec's list**, added after the
  review: `RESERVED_SLUGS` must name `subscribe` and `billing`, on exactly the
  precedent its own comment sets for `prospects`.
- `app/services/{report_service,preflight_totals}.py` — one line each, passing
  the session they already hold to `get_billing_cycle()`. Not a drive-by: this
  session introduced the second-session-per-call cost and `preflight_totals` is
  on the composer's polled path.

### Nothing in the suite can reach Stripe, and it is asserted three ways

1. Every Stripe call goes through one replaceable object (`StripeAPI`), so a test
   that forgot to replace it gets `StripeNotConfigured` rather than a request.
2. `conftest.py` blanks the credentials, so there is nothing to authenticate with.
3. `accept-B1.sh` check 1 runs the four billing modules with `socket.connect`,
   `connect_ex` and `create_connection` raising. Check 1b then proves the guard
   can fire, against a connection to `api.stripe.com` whose answer is known —
   the fifth measurement script in this repo to need its own self-check.

The live verification A3 asks for is check 12, `--with-stripe`, opt-in, and it
**refuses anything but an `sk_test_` key**. It has not been run: Part B is not
done and there are no keys on this machine.

### The bound the identifier cannot supply

`report_segments()`'s deterministic `campaign_<id>` makes a *repeat* free. It
does nothing about *history*. Every campaign this client has ever sent predates
the subscription, and August's 28,002 segments are settled by the one-time price
on the first invoice — so an unbounded `backfill_unreported()` would meter them a
second time, into the current period, on top of a charge he has paid. The default
bound is the stored subscription start; with none stored it replays nothing and
says so. A subtraction needs a time bound, and that one is worth half a session.

### `/health` gained a third signal, and it does not call Stripe

`pricing_ok`, `pricing_state`, `pricing_issues`, `pricing_checked_at`. The
comparison runs on demand (`POST /api/billing/tier-check`, and automatically once
a checkout completes) and stores its verdict; `/health` reads the row without
`Depends(get_db)`, for the reason `active_config_alerts()` does. An endpoint an
uptime monitor polls every minute must not become a Stripe request every minute —
`test_health_makes_no_stripe_call` asserts the call count is zero.

Five states, not two: `agree`, `disagree`, `unavailable` (Stripe could not be
read — a degraded answer must not read as a chosen one), `never_checked` (a check
nobody has run is indistinguishable from one that would fail, so it is **not**
ok), and `not_configured` (no second definition exists here, so no alarm).
Still HTTP 200 throughout: a 503 rolls back every deploy, including the one that
fixes it.

### The fresh-context review, and what it found

One synchronous reviewer, five lenses from the spec plus the standing structural
checks. It cleared lenses 1, 3 and 4 outright — the allowance is subtracted
exactly once and in Stripe; nothing is written before the ownership guard on
either path; no degraded state is described as a chosen one — and found **twelve
defects**, two of them serious. Every one was live in a tree where the gate was
green twice and 38 of 38 mutations were caught, which is the argument for running
both: **the harness proves a rule cannot be reverted, and only a reader notices a
rule that was written slightly wrong in the first place.**

Fixed this session, each with a mutation (`R1`-`R8`) so it cannot come back:

- **The meter priced a legacy row as 1 segment while `/usage` priced it by
  length.** A 480-character row with `segments` NULL metered as **1** and
  appeared on the dashboard as **3** — and the docstring in between asserted the
  two agreed. Two similar arithmetics with a comment claiming equivalence is
  worse than two obviously different ones. `legacy_segment_count()` is now the
  one implementation and both call it; the test sweeps nine lengths across the
  160-character boundary, because the original used a two-character message and
  could not see the divergence at all.
- **`tools/bill_period.py` gave any window its own free allowance.** July's
  18,000 segments price at $120.00 as one window and **$0.00 + $0.00** as two
  half-months — two runs, $150 given away, every figure on both correct. The tool
  now computes the cycle containing `--start` and says loudly when the window is
  not it. It warns rather than refuses, and that is decision 002's own test
  applied honestly: 002 refuses on the composer *because the operator there is
  the client*, and a back-bill is run by us, from a terminal, after a mandatory
  dry run.
- **The tier check ran once, at checkout, and `/health` reported that verdict
  forever.** A tier edited in Stripe six months later would never have been
  detected — the exact failure A2 exists to prevent, reached *through* the check
  rather than through its absence. There is now a daily scheduler job, and
  `tier_verdict()` ages an unrefreshed agreement into its own `stale` state, so a
  box whose scheduler died says so instead of quoting September's answer. Only
  agreement ages; a disagreement is still true until somebody fixes it.
- **`/health` published the commercial terms to anyone who asked.** It has no
  login and no rate limit, and the stored issues read "Stripe's first tier
  includes 5000 segments; this account is configured for 10000".
  `public_pricing_issues()` now says which state without saying what the numbers
  are; the figures go to the log at ERROR and to the two authenticated billing
  routes. `config_issues` had set that precedent one field up and B1 did not
  follow it.
- **`/subscribe` was not in `RESERVED_SLUGS`** — protected only by being nine
  characters, which is precisely why `prospects` (also nine) is in the set. Added
  with `billing`.
- **The backfill was bounded on `Campaign.created_at`**, so an August draft sent
  in September fell outside the window forever. Bounded on `sms_messages.sent_at`
  now, which is also the column `compute_usage()` filters on.
- **`_first_item()` carried a branch nothing could reach.** Driven against the
  pinned SDK, `Subscription.items` is a `ListObject` and `callable()` is False on
  every path either caller can produce. Deleted rather than left to be defended.
- **`cycle_day()`'s docstring misattributed its own cost.** It claimed three
  outside callers had no session; two of them had one in scope and handed it to
  `compute_usage()` on the next line. Both now pass it — `preflight_totals` above
  all, which is a synchronous call inside an async route on the composer's
  *polled* path, and a second connection per poll is 5i's event-loop incident in
  miniature.
- **Check 1b could only ever pass.** It reassigned `socket.create_connection`,
  then called `socket.create_connection`, and reported that the guard worked — a
  tautology, and it exercised the one patch of three that does not matter, since
  an HTTP stack builds its own `socket.socket`. It now drives a real HTTP request
  at a closed local port and distinguishes the guard's exception from a
  connection refusal. Criterion 1 itself was sound; its self-check was not.
- Smaller: a bare `assert` guarding a field `/health` publishes (stripped under
  `-O`); a hardcoded `"sms_segments"` in a test that should read the setting; a
  config comment naming the wrong module; and "Your month runs from the
  2026-09-09 onwards" on the subscribe page.

**Not fixed — escalated.** `decisions/011`. See below.

### Found while working

- **A campaign's metered figure is written once and can never be corrected, and
  it is wrong in both directions.** `decisions/011`, open, escalation item 1.
  A6's `identifier=campaign_<id>` is what makes a retry free and it is the same
  property that discards a revision.
  - **Over-bill.** The meter fires the instant the send loop returns, when every
    row is `sent`. The delivery webhook then writes `undelivered`, which is
    outside `BILLABLE_STATUSES`. Measured: 12,000 metered, `/usage` reports
    8,000, the correction is discarded. The client is billed for segments his own
    dashboard says he did not use, **in our favour**, and this account's corpus
    is 3,037 failures.
  - **Under-bill.** `campaign_topup.top_up_background` is the third path that
    writes `sent` rows, it is not in B1's file list, and `campaign_<id>` is spent
    for that campaign anyway.
  Neither costs anything until Part B lands and the client subscribes. The
  recommendation is a follow-on session that meters once, on a schedule, after
  delivery receipts have settled — one event, one identifier, right the first
  time rather than right after a correction.
- **`app/routers/usage.py:46` still reads `WHOLESALE_COST_PER_SEGMENT`.**
  Unchanged since module 4's note. Not a leak — neither the rate nor the balance
  is in the response — but the literal grep is a structural rule the runtime test
  cannot replace. `usage.py` is module 8's file.
- **`docs/API.md` documents none of B1**, nor P1/P2's prospects API, nor 5f's
  reports and links routes. `/subscribe`, `/billing/success`,
  `/api/billing/{status,checkout,tier-check}` and `POST /webhooks/stripe` are all
  new. Module 8's, and now three sessions deep.
- **`pricing_table()` gained an optional `db`,** and `app/routers/usage.py` calls
  it without one, so `/api/usage/pricing` opens a second short-lived session to
  read the anchor. Correct but wasteful; passing `Depends(get_db)` through is a
  two-line change in module 8's file.
- **The suite's login budget is one tighter.** `test_stripe_billing.py` has a
  module-scoped login and resets the limiter around itself, as
  `test_provider_status.py` does. `POST /login` is 10/minute per IP and the whole
  suite runs from one address inside one window.

### Deliberately not built

- **Any real Stripe call, from anywhere but check 12.** Refunds, proration, plan
  changes and dunning beyond Stripe's own retries are out of scope by the spec.
- **A scheduled tier check.** A2 says on demand. It runs from the endpoint and
  once automatically after a completed checkout, which is the one moment it is
  guaranteed both to matter and not to be on a hot path. A configured box that has
  never run it reports `pricing_ok: false`, which is the loud behaviour A2 asks
  for.
- **The August figure anywhere in a template.** `$270.03` lives in a Stripe price
  and in `tools/bill_period.py`'s output, and Stripe's own checkout page shows it
  before the client confirms. A second copy in `subscribe.html` would be a second
  number to keep true, and the page already renders every other figure from
  `pricing_table()`.

## Module B1b Part A — meter once, late, and correctly (2026-09-08)

`decisions/011` implemented: the meter moved off the send path onto a scheduled,
ledgered pass. **The property**: every billable segment reaches the meter exactly
once, and the figure metered is the figure `compute_usage()` would report for the
same window.

`bash agent/gate.sh` passes, twice. **788 tests** (764 + 24 net new; the
sixteen B1 meter tests moved and were rewritten against the pass, 39 tests are
in the new module). `bash agent/accept-B1b.sh` is the stop condition and exits
0 with every criterion printed. `agent/mutate-B1b.py`: **35 mutations, 0
survived, 0 failed to apply**, identical on two consecutive invocations on a
tree verified byte-identical to the repo. Eight of the thirty-five (`R1`-`R8`)
exist because the review found defects in a tree where the first 26 were all
caught. `agent/accept-B1.sh` still passes after its harness was repaired (below).

### The two third-party clauses, verified before anything was built

`RULES.md`'s new rule had its first outing and both clauses survived it — the
first time in five sessions that a spec's third-party claims did. Read from the
pinned `stripe==15.6.1`'s own `MeterEventCreateParams` docstrings:

- `identifier`: "Stripe enforces uniqueness within a rolling period of **at
  least 24 hours** … primarily addresses issues arising from accidental retries."
- `timestamp`: "Must be within the past **35 calendar days** or up to 5 minutes
  in the future. Defaults to current timestamp if not specified." An `int` of
  epoch seconds.

Both now run on every suite invocation in
`test_the_sdk_still_documents_the_two_limits_this_session_builds_on`, which
also compares the SDK's 35 against the constant the pass refuses on, so the two
cannot drift apart. What could **not** be verified offline is recorded under
"Found while working": what Stripe does with a backdated event that arrives
after the closed period's invoice has been finalised.

### A1 — the ledger is `sms_messages.metered_at`

Migration `d7e2a91c4f36`, additive and nullable, added in place without
`batch_alter_table` (the `top_up_at` route). Its docstring says what NULL
means: **never metered**, and for every pre-existing row that is a fact rather
than a gap, because no customer has ever been stored. One writer, one clock —
the pass, `datetime.now()`. No index on the column: an index on `sms_messages`
is escalation item 8, and the selection ranges on `sent_at`, which has one.

The pass (`stripe_meter.meter_settled_rows()`) selects rows that are billable,
settled, unmarked and in scope, groups them into one batch per campaign per
calendar day, posts one meter event per batch, then stamps `metered_at` on
exactly those row ids and commits — report, then mark, then commit, per batch.
`_mark()`'s comment answers review lens 2: **a row whose status changes after
it is marked stays marked and stays metered.** The mark is by id and
unconditional, because the column records that *these rows' segments were
reported*, which is true whatever the row later becomes; unmarking would
re-report it and Stripe cannot take a negative event. That residue is the one
bounded over-bill this session leaves, it can only happen to a row that was
settled, and `tools/bill_period.py --unmetered` shows it as "metered, then
left the billable set" rather than hiding it.

Stripe's identifier stays, deterministic from the batch's row ids
(`u_c<campaign>_<day>_<min id>_<max id>_<n>_<segments>`), as the second line of
defence for the one window the ledger leaves: Stripe accepting the event and
the commit that marks its rows not happening — a process death, or (the
review's finding) a database lock under a webhook storm. The batch is
**staged** in `app_settings` before the call and cleared with the mark, so the
next pass re-offers exactly the staged rows under exactly the staged
identifier; see the review section. No comment anywhere calls the identifier
load-bearing.

### A2 — settled

`SETTLED_STATUSES = ("delivered",)` lives in `app/models/sms_message.py` beside
`BILLABLE_STATUSES`, and both are read through the model module at call time,
never restated — the comment says why, and two binding tests change what each
constant *means* and require the selection to follow. A `sent` row settles when
`BILLING_SETTLE_HOURS` (config, default **24**) have passed since `sent_at`; the
config comment states which direction to err (lengthen before shortening —
metering before a late receipt is the over-bill 011 calls the serious one).
`delivered` settles at once: it is the carrier's final word, and the webhook's
own comment treats a later failure on a delivered row as a carrier race.
`held_back` is outside the billable set and never reaches the question.

### A3 — the send time, and the 35-day refusal

Each event carries `timestamp` = the epoch of the batch's latest `sent_at`,
converted from the naive local string this app writes with the same local
clock `_local_date()` uses on the way back. A pass run on 2 June for a send at
23:00 on 31 May lands the event in May's cycle
(`test_the_event_carries_the_send_time_and_lands_in_the_earlier_cycle`), and a
campaign still sending at midnight is split into two batches because the
cycle boundary this codebase keeps is a date.

Rows in scope but older than 35 days are gathered by a separate query,
refused, left NULL, returned in the verdict, and logged at **ERROR** every pass
with the remedy in the sentence ("Invoice them with tools/bill_period.py; they
will be refused on every pass until then"). The positive control at 34 days
meters the same rows. The reconciliation view labels them.

### A4 — the pass is scheduled, and it is the only place

`app/main.py` registers `stripe_meter.metering_pass_job` hourly under its own
id, `coalesce=True`, `max_instances=1`. It is a **sync** function on purpose, so
APScheduler runs it on the executor thread rather than on the event loop the
send path shares (5i's lesson); a test asserts it is not a coroutine. It owns
its session, closes it in a `finally`, and never raises — two shapes tested:
Stripe raising (the pass returns) and something the pass does not expect (the
job returns).

`campaign_dispatch.py` lost `report_usage()` and both calls to it; its docstring
says why there is no hook and must not be one. `campaign_topup.py` gained one
sentence saying the same. Review lens 1 is asserted as a sweep:
`test_a_segment_can_be_metered_from_exactly_one_place` counts
`.create_meter_event(` call sites in `app/` (one, in `stripe_meter.py`) and walks
the AST of the three modules that write `sent` rows for any import of either
Stripe module, including inside function bodies, which is where B1's hook lived.
Criterion 7 also drives a real send on the console provider with a customer
stored and Stripe configured, and asserts zero Stripe calls; then the pass, a
settle window later, meters exactly what was sent.

**When Stripe is down (review lens 3):** the batch's report raises, nothing is
marked, the verdict names the failure, and the pass stops rather than hammering
a Stripe that is down. Earlier batches are already committed; later ones wait
for the next hour. `test_a_failure_after_the_first_batch_leaves_that_batch_committed`
drives exactly that mid-pass shape. If the webhook flips a row between the
SELECT and the UPDATE, the row is reported and marked — that is lens 2's
residue, visible in the breakdown.

### A5 — the backfill, and the tool

`backfill_unreported()` **is the pass**, called by a human: dry run by default,
`since` widens the bound deliberately, `now` is injectable. There is no second
replay logic to drift, so it is incapable of reporting a marked row for the same
reason the scheduler is — criterion 4 proves it against marked rows with a fake
that bills every call, and the positive control replays a campaign whose mark
was removed by hand. The pass's lower bound is still the stored subscription
start: with a customer stored and no start, it **refuses** rather than ranging
over the table, because a pass with no lower bound is the double bill the
backfill was written to avoid.

`tools/bill_period.py` gained `--unmetered` and nothing else; its arithmetic is
untouched. `stripe_reconcile.unmetered_breakdown()` splits a window's billable
segments into metered and unmetered-by-reason — not settled, too old for the
meter, before the subscription, awaiting the next pass, no send time — plus
the residue running the other way, and a test asserts `metered + unmetered`
equals `compute_usage()` for the same window. `unmetered_reason()` is the one
implementation of that classification.

### What is where, and why the file list is wider than the spec's

- **`app/services/stripe_reconcile.py` is new and not in the spec's list.**
  The pass pushed `stripe_meter.py` to 488 lines — twelve short of the rule,
  with a review still to land. Split along the boundary the code already had:
  `stripe_meter` *reports* (the pass, the batch, the mark, the job, the
  backfill); `stripe_reconcile` *reads back* (`period_usage()`, which moved,
  and `unmetered_breakdown()`). Nothing in it writes or calls Stripe, and
  `stripe_meter` imports nothing from it. `tools/bill_period.py` and
  `tests/test_stripe_billing.py` now import `period_usage` from there.
- **`agent/mutate-B1.py` and `agent/accept-B1.sh` were repaired, and that is
  outside the file list on purpose.** Thirteen of B1's mutations sat on lines
  this session removed or inverted (`A6a` — "the Send button's path never
  reports what it sent" — is now the *correct* behaviour). Left alone, B1's
  harness would have exited 2 at `ANCHORS VERIFIED` forever, which CLAUDE.md
  already lists for 5e and 5h as "a decision somebody should take rather than
  inherit". Taken: each moved mutation is retired with its successor named
  (`A1a`/`A1b` -> `B2`, `A1c` -> `S8`/`S8c`, `A1d`/`R1` -> `B3`, `A6a`/`A6a2`
  -> `S1`, `A6b` -> `B4`, `A6c` -> `S9`/`S9b`, `A6d` -> `B9`, `A6e` -> `S5`,
  `A6f`/`R6` -> `B1`/`B1b`), `A7a` is re-pointed to `stripe_reconcile.py`, and
  `accept-B1.sh`'s criteria 2 and 7 point at the tests that now carry those
  properties. B1's harness runs at **34 mutations, 0 survived** on the new tree.
- **`tests/test_stripe_billing.py` lost its sixteen meter tests** to
  `tests/test_metering_pass.py`, rewritten against the pass; the A1 property —
  the meter receives the RAW count, reads `BILLABLE_STATUSES` through the model,
  prices a legacy row as `/usage` does — is asserted there. `test_stripe_contract.py`
  gained the two-limits test and `timestamp`; `_stripe_fixtures.py` lost the
  app_settings ledger cleanup (there is no ledger there any more) and gained
  `FakeStripe(dedupe=False)`, so a test can prove the mark is what stopped a
  double report rather than the fake's own record.

### Cost, measured before it is a job

5j's rule. The pass's two selections against **360,000 rows** (a year at this
account's rate, eleven months already marked): `settled_unmetered` 12,943 rows
in **128 ms**, `too_old_unmetered` in **46 ms**. The planner uses
`idx_sms_status (status=?)`, walking the billable rows rather than ranging on
`sent_at` — fine hourly on the executor thread, and `accept-B1b.sh` check 8b
re-measures it every run with a two-second ceiling. If the table outgrows that,
the fix is a partial index on `(sent_at) WHERE metered_at IS NULL`, which is
escalation item 8 and not this session's.

### The fresh-context review, and what it found

One synchronous reviewer, five lenses from the spec plus the standing
structural checks, on a tree that was green twice with 26 of 26 mutations
caught. It cleared lens 2 (the mark's comment says what happens to a row that
changes afterwards, and the code does it) and lens 5 (every boundary,
comparison and clock agrees with `compute_usage()`; no wholesale figure or
carrier name in the pass's log or verdict), and found **eleven defects**, two
of which move money. Both are fixed, with mutations `R1`-`R8` so they cannot
come back; the rest are fixed or recorded below. Same shape as B1: the harness
proves a rule cannot be reverted, and only a reader notices the rule that was
written slightly wrong, or the remedy nobody ran.

- **The remedy the spec names for refused usage double-bills.** The pass's
  ERROR said "invoice them with `tools/bill_period.py`", and that tool prices
  a window through `compute_usage()`, which reads no `metered_at`. Measured:
  July with 12,000 metered and 12,000 refused priced as 24,000 segments and
  $210.00 — the metered half billed a second time, the allowance applied
  twice. And nothing records a manual invoice, so the ERROR fires forever and
  `--unmetered` cannot tell "invoiced by hand" from "not yet". **Shipped:**
  `--create` now refuses any window with metered rows in it and prints the
  breakdown under the refusal (`metered_refusal()`, mutation `R1`); the
  arithmetic is untouched and August still invoices. **Escalated:** how a
  refused-only invoice is priced against an allowance the meter has partly
  consumed, and how it is recorded so the refusal stops — `decisions/012`,
  open, escalation items 1 and 10. The ERROR line now points at both.
- **A database lock during the mark is a second, likelier way into the
  lost-commit window, and a status flip made it a double bill.** `_mark()` ran
  outside the per-batch handler; a lock under a delivery-webhook storm would
  escape to the job with Stripe holding the event and the rows unmarked. The
  first version then recomputed the identifier from the rows on the next pass,
  so one delivered-to-undelivered flip in the batch changed it and the
  survivors were billed twice. **Shipped:** the batch is **staged** — written
  to `app_settings` and committed — before the Stripe call, and cleared in the
  same commit as the mark. Every way out is now accounted for: Stripe fails,
  nothing marked, the batch stays staged; the mark fails or the process dies,
  the next pass re-offers *exactly the staged rows under exactly the staged
  identifier*, which Stripe dedupes. A batch staged longer than Stripe's
  24-hour window is refused at ERROR until a person answers from Stripe's
  event summary (`resolve_pending_batch()`). Mutations `R2`-`R5`, `R8`; the
  lost-commit test now flips a row between the two passes.
- **Post-commit N+1.** `expire_on_commit` expired every loaded row at the mark's
  commit, and `batch.summary()` then re-read each: 1,204 SELECTs for a
  1,200-row batch. `Batch` now computes its figures once, from the rows, and
  keeps ids only.
- **Three tests reached less than they said.** The job test's Stripe-down half
  ran on the real clock, so the 2021 row was refused as too old and the fake
  was never reached (`metering_pass_job` takes `now` for tests now, and the
  test asserts the call); the empty-customer mutation `B9` was caught one
  guard down, by a reason string that had nothing to do with it (the
  arrangement in which it is the only thing deciding — start stored, customer
  absent — has its own test); the real-send test selected across the whole
  database and would have failed on a neighbour's leftover row.
- **Guards with no test.** A key removed after checkout was a silent hourly
  skip until every row aged out — now an ERROR (`R6`); an unreadable stored
  start; a NULL `sent_at`; a dry run that posts (`R7`).
- **Wording.** The model said `delivered` is "the carrier's final word" while
  the webhook admits a later failure on a delivered row; the migration claimed
  five index names are asserted where the test names four; "in the same
  transaction as the successful report" described an HTTP call.

Recorded, not fixed: `break` on any per-batch error is head-of-line blocking
for a rejection specific to one batch (a timestamp crossing the 35-day edge
between our string check and Stripe's epoch check — a one-hour window at DST
fall-back), and sends in the repeated fall-back hour are stamped 3,600 s early
because `isoformat()` drops `fold`; both self-heal and matter only if that
hour straddles a Stripe period boundary. The cycle-boundary test proves the
app's date cycle, not Stripe's instant (below). `backfill_unreported(since=…)`
before the subscription start re-meters balance-settled rows; the dry run is
the only guard and the docstring says so.

### Found while working

- **`record_delivery_status()` writes `undelivered` over a row that has a
  handset receipt.** The guard on the auto-block reads `delivered_at is None`;
  the status write above it does not, so the delivered-then-failed sequence
  the webhook's own comment calls "a carrier retry, a duplicate, or a race"
  moves a row the carrier confirmed delivered out of the billable set. For the
  meter that is the one case a `delivered` row changes after settling; for the
  client it is a delivered message reported as not delivered. 5d/5g's file.
- **The subscription's anchor is an instant; the cycle here is a date.** Rows
  sent on the anchor day *before* the checkout moment are in scope (the bound
  is a date) and are timestamped before Stripe's period start. What Stripe
  does with an event before the first period cannot be verified offline; Part
  B's first dry run should look at the meter's event summary for the anchor
  day.
- **Invoice finalisation versus the settle window.** Usage from the last day
  of a cycle is metered up to ~25 hours after the cycle closes, backdated
  into it. Stripe finalises a subscription invoice about an hour after the
  period ends by default; whether a backdated event arriving after that is
  added to the closed invoice or carried to the next one is server behaviour
  the SDK cannot answer. Exactly-once is unaffected either way. Part B: check
  the account's invoice finalisation delay, or accept next-invoice billing for
  the last day's sends.
- **`idx_sms_status` is the plan's choice** for the pass's selection; see
  "Cost" above. Not a defect today.

### Deliberately not built

- **Any change to what is billable.** `BILLABLE_STATUSES` is untouched;
  `SETTLED_STATUSES` answers a different question and says so.
- **A `/health` field for refused usage.** The refusal is an ERROR log line
  every pass and a row in the reconciliation view. `/health` is unauthenticated
  and every field it gains is published; a count of unmetered segments is a
  commercial figure. If a monitor needs it, it belongs behind auth on
  `/api/billing/status`.
- **An index on `metered_at`.** Escalation item 8, and not needed at the
  measured scale.
- **A second identifier shape, a delta path, negative events.** Option 2 of
  `decisions/011`, as decided.

---

# Session 5m — the composer's audience panel, and time in Eastern

_Started 2026-09-08. A1 investigated and written up **before** any fix, which is
what this section is._

## A1 — what actually happened, reproduced

### The panel the operator photographed

    Audience     ⭐ ALL BIDDERS — MAIN LIST
    Recipients   10,146
    Opted out    3,460
    Segments     443
    Estimated cost  $0.00

with the dropdown reading `09/09, 6:00 PM Private Record Collection … — 443 contacts`.

### Reproduced, not reasoned about

A scratch database at the production shape — **10,146 active contacts, 3,460 on
the blocklist, one 443-member list** — and the two endpoints the panel is built
from, timed on this machine (best of three):

    POST /api/campaigns/preview   audience=all       237.9 ms   10,146 / 3,460 / 10,146 seg / $2.19
    POST /api/campaigns/preview   audience=list:1     20.3 ms      443 /     0 /    443 seg / $0.00
    POST /api/campaigns/preflight audience=list:1     34.9 ms      443 /     0 /    443 seg / $0.00

**A twelvefold gap between the two previews, and nothing sequences them.**

The composer's JavaScript is then run for real — `tests/js/composer_harness.mjs`
loads the four composer partials out of `app/templates/` into a `vm` context with
a DOM small enough to run them and no smaller, and
`tests/js/composer_scenarios.mjs` drives them with those measured latencies.
Scenario `three_audiences` prints:

    audience      "⭐ ALL BIDDERS — MAIN LIST"
    recipients    "10,146"
    opted_out     "3,460"
    segments      "443"
    cost          "$0.00"
    selector      "list:7"
    selected_text "09/09, 6:00 PM Private Record Collection — 443 contacts"

That is the photograph, character for character.

**Where that scenario went.** The three reproduction scenarios —
`three_audiences`, `stale_label` and `create_after_stale_panel` — were written
against the pre-fix tree and are **not** in the shipped file: once the defect was
fixed they could no longer reproduce it, and the six scenarios in
`tests/js/composer_scenarios.mjs` are what a *fixed* tree can assert. What
survives of the reproduction, executable from the repo:
`test_the_harness_reproduces_the_defect_it_was_written_for` strips the ordering
gate in a scratch copy of the templates and requires the stale reply to win, and
the latencies above are the fixture's own. Found by the fresh-context review,
which pointed out that the session's strongest artefact was the one a reader
could not re-run.

### The mechanism: three defects, one panel

The flow is the campaign-first one — he uploaded the record-collection CSV and
pressed Create, so `createFromUpload()` ran (`_composer-upload.html:115`).

**1. `select.value = …` fires no `change` event, so the Audience row is never
repainted.** `paintAudienceSummary()` *is* wired to the select's `change`
(`_composer-script.html:228`) and that is exactly why the obvious explanation
looked wrong. `change` is a user-interaction event; an assignment is not user
interaction. `createFromUpload` sets the new list programmatically
(`_composer-upload.html:159`), and the last thing that *did* call
`paintAudienceSummary()` was `loadAudiences()` one line earlier, while the
dropdown still held the pinned entry it was left on. So the row kept saying
**ALL BIDDERS**, the audience of the previous paint, for the rest of the session.

**2. Nothing sequences `/preview` responses, and the slowest one is the stale
one.** `loadAudiences()` ends with a bare, unawaited `refreshPreview()`
(`_composer-script.html:89`) — dispatched *before* the value is moved, so it asks
about `all`. `createFromUpload` then awaits a second `refreshPreview()` for
`list:7`. Request A (238 ms here) is twelve times slower than request B (20 ms),
so B paints 443 and A repaints 10,146 and 3,460 on top of it. `refreshPreview`
holds no notion of which request is newest; `composerMode === 'upload'` is the
only guard in it and it does not apply here.

**3. `sumSegments` and `sumCost` have a second writer.** `runPreflight()`
(`:292-295`) writes those two rows and nothing else, from a different response
about a different audience. Pressing **Run checks** after the panel had settled
on A's figures left Recipients and Opted out describing 10,146 people and
Segments and Estimated cost describing 443 — the third audience on screen.

### Why development never saw it

`setMode('existing')` leaves a 300 ms debounced refresh armed
(`_composer-upload.html:34`). On this machine A comes back in 238 ms, *before*
that late refresh, so the debounce lands last and quietly repairs the panel —
scenario `stale_label` shows exactly that: every number right, only the Audience
row wrong. On a one-vCPU droplet A is several hundred milliseconds slower than
the debounce and lands after it. The bug is invisible on a fast box **by
timing**, which is why it took a client's operator to find it.

## Whether a send would have gone to 443 or 10,146 — **443**

Established rather than assumed, on both sides of the wire.

**Client side.** With that exact panel on screen (scenario
`create_after_stale_panel`, pre-fix tree), pressing Create sent:

    {"name": "09/09, 6:00 PM Private Record Collection", …,
     "audience": "list:7", "batch_size": null, "scheduled_at": null}

The submit handler reads `el('audience').value` at submit time
(`_composer-script.html:303`). The panel is a *description*; it is never an
instruction, and no code path turns a painted row back into a selector.

**Server side.** The same selector through the real endpoint against the
production-shape database:

    posted selector       : list:1
    campaign.audience     : list:1
    campaign.audience_label: 09/09, 6:00 PM Private Record Collection
    total_recipients      : 443
    estimated_segments    : 443

`create_campaign` resolves the audience itself and stores its own label from
`contact_service.audience_label()`, so the stale text on screen cannot reach the
draft.

**So this is a display defect, not a mis-send** — and the display is the screen
whose entire job is to be trusted before a blast to ten thousand people, so it is
fixed as if it were one.

**The dangerous direction is reachable, and the ordering guard alone does not
close it.** The panel can under-report while the selector is larger: pick the 443
list, then pick ALL BIDDERS, and until the reply lands the panel describes the
audience he moved off while Create sends to the one he moved to. The token
decides *which reply wins*; it cannot make a reply arrive, and the window is 300
ms of debounce plus 237.9 ms of request — longer on his box. The first version of
this fix left that window showing a fully self-consistent stale panel, which is
harder to notice than the contradictory one it replaced; the fresh-context review
caught it. **Closed by clearing the panel on the change event** — every figure
reads `…` and the audience an em dash until an answer about the audience now
selected arrives (`test_the_panel_says_nothing_while_it_waits`).

## A1 — what shipped

**One function writes the panel, and one gate decides whether a reply may.**
`paintSummary()` in the new `app/templates/_composer-summary.html` writes all six
rows of "This send" from a single `view` object, and nothing else in the composer
touches any of them. Each response handler takes a ticket from `panelSequence`
before its request and returns early if a newer one has been taken since —
`refreshPreview()` and `runPreflight()`, one gate each, covering the rows the
panel does not own as well (the character count, the phone preview, the
checklist). `paintSummary()` deliberately does **not** re-check the ticket: with
both callers gating, a check there would be a guard no arrangement can reach,
which this project has shipped once already and had to delete (`RequestBudget`,
P2).

Both responses now name the audience they answered about. `/api/campaigns/preview`
and `/api/campaigns/preflight` return `audience` and `audience_label`, built by
`contact_service.audience_label()` — the same function `create_campaign()` stores
`campaigns.audience_label` with, so the panel, the rail and the draft cannot spell
one selector three ways.

Four things followed from the requirements rather than from taste, and each is
recorded here with the requirement that forced it:

- **`/preview` takes `batch_size`.** *("Segments and Recipients are consistent
  with each other.")* The composer's cap field was wired to re-run the preview
  and the value was never in the body, so the panel quoted 443 recipients for a
  send capped at 50 while the checklist beside it quoted 50. `create_campaign()`
  applies the cap, so the capped figure is the true one.
- **`/preview` prices an empty message at zero.** *("Every figure in the summary
  panel comes from one response about one audience.")* The panel is now fetched
  before a word is typed, because that response is the only thing allowed to fill
  it. `describe("")` answers **1** segment — it answers about text — which would
  have quoted 10,146 segments and $2.19 for an empty composer. Zeroed in the
  router, not in `count_sms_segments()`, which is correct and is not being asked
  this question.
- **`paintSummary()` also writes step 2's metering strip.** *("One writer per
  row.")* `pvRecipients`, `pvTotal` and `pvCost` are the same three figures in a
  second box, and until now the pre-flight path wrote the panel's copy and not
  the strip's: two boxes on one screen, different segment totals, different money.
- **`app/services/audience_split.py` is new.** *(the 500-line rule, and the
  layering rule.)* `_audience_split()` was a private function in
  `app/routers/campaigns.py`, which the session pushed to 515 lines. It resolves
  an audience, partitions the hold-back window and reads the blocklist — business
  logic in a router, which the layering rule says belongs in a service. Router is
  back to 453.

`app/templates/_composer-summary.html` is also new, for the 500-line rule:
`_composer-script.html` reached 538. The split is along the boundary the fix
already drew — the panel's whole writer in one file — and `campaigns.html`
includes it **first**, because `panelSequence` and `EMPTY_SUMMARY` are top-level
`const`s in their own script block and `setMode('upload')` paints the empty panel
synchronously at parse time.

**Two more defects closed on the way, both the same shape.** Switching to the
Upload tab used to zero the rows directly, and a `/preview` already in flight
filled them straight back in under an upload composer; `resetSummary()` now takes
a ticket, which retires every reply on its way. And `loadAudiences()` takes the
selector the caller wants selected rather than having the caller assign it
afterwards — one request for the right audience instead of two, the first of them
about the wrong one. That second change is a simplification, not a guard: with
the ticket in place the old sequence is already safe, and it has no mutation for
exactly that reason.

### How it is tested, and why it had to be

`tests/js/composer_harness.mjs` loads the four composer partials out of
`app/templates/` into a `vm` context with a DOM small enough to run them, and
`tests/js/composer_scenarios.mjs` drives six scenarios through them.
`tests/test_composer_panel.py` runs those scenarios in node against **response
bodies produced by the real endpoints in the same process** — a hand-written
fixture would be a second copy of the API drifting away from it, and the property
under test is exactly that the panel repeats what the API said.

The stub is literal about the two things the defect turned on: assigning
`select.value` fires no `change` event, and a display-size-1 `<select>` re-selects
its first option when the value has no match. `test_the_harness_reproduces_the_defect_
it_was_written_for` strips the gate in a scratch copy of the templates and requires
the stale reply to win, because a green check whose harness is broken is worse
than no check.

`node` is therefore required by the suite. `npm run build:css` is already a build
step of this project and of `deployment/deploy.sh`, so it is not a new
dependency — but it is a new dependency **of the tests**, and the failure is a
`pytest.fail` with that sentence in it rather than a skip.

## A2 — what shipped

**Every naive timestamp this application stores is wall clock in
`APP_TIMEZONE`**, which defaults to `America/New_York`. `app/core/clock.py` is
where that sentence lives, with the reasoning; `app/core/config.py` resolves the
zone and applies it to the process.

`scheduled_at` stays **wall clock** rather than becoming UTC, and the reader was
fixed instead of the data. Three reasons, in the module docstring: it is what the
operator typed and what the input submits, so the round trip has nothing in it to
get wrong; `sent_at`, `created_at` and `added_at` are naive local strings and are
out of this session's scope by name, so converting `scheduled_at` alone would
leave one table with two rules — and "the next session assumes one rule for all
of them" is review lens 3's own worry; and the comparison in `due_campaign_ids()`
therefore stays lexicographic, which is sound because one format and one zone are
now guaranteed on both sides.

Two mechanisms, two jobs, neither redundant:

- **`clock.now()` asks `zoneinfo`**, so the scheduler is right whatever the box's
  own clock says. `due_campaign_ids()` and `dashboard_service.next_up()` — the two
  readers of `scheduled_at` — both use it, and `now` may be passed as an aware
  instant, which is how every test in `tests/test_timezone.py` says something
  about `America/New_York` rather than about the laptop it runs on.
- **`config.apply_process_timezone()` sets the process TZ at import**, so the
  sixty-odd ambient `datetime.now()` calls that write `sent_at`, `created_at` and
  `last_messaged_at` mean the same thing. Rewriting sixty call sites would reach
  half the codebase and every one of those columns is out of scope; this makes
  them all correct at once and leaves each column's meaning exactly as its own
  comment describes it. It is applied in `config` rather than in `clock` so the
  two modules do not import each other — that works only while nothing imports
  `clock` first, and the day something does, the failure is an ImportError at boot
  with no obvious cause.

**One formatter, and it is not the viewer's.** `base.html` has one parser and
three renderers (`fmtDate`, `fmtDay`, `fmtClock`) in a script block of their own,
and `clock.clock_time()` is their server-side twin — `suppression_service.
clears_at_clock()` delegates to it rather than keeping a second copy of the
arithmetic. The composer's `shortDate` and `clockTime` are now one-line
delegations. `tests/test_timezone.py` runs the real block in node under three
viewer zones, and a sweep asserts no template builds a `Date` from a stored value.

Worth being exact about what the old formatter got wrong, because most of it was
*accidentally* right: `new Date(naive)` reads the digits in the viewer's zone and
`toLocaleString` prints them back in the viewer's zone, so a datetime string
round-trips its digits anywhere. **The four hours on screen were the stored value
being UTC**, and the process zone is what fixes that. What the formatter itself
got wrong, and what the replacement fixes, is a **date-only** value — parsed as
UTC midnight, so `/usage`'s reset date rendered as the day before, on the client's
own screen, every cycle — and a wall clock inside the viewer's own DST gap, which
`Date` moves. `test_the_render_check_goes_red_on_the_formatter_it_exists_to_reject`
runs the rejected version against both and requires it to fail.

### The two Sundays, answered rather than avoided

- **1 November, the hour that happens twice.** A campaign scheduled for 1:30 AM
  becomes due at the first 1:30 AM (EDT, 05:30 UTC) and is dispatched; by the
  second it is no longer a draft, and `due_campaign_ids()` filters on status. The
  draft filter is what makes the repeated hour safe, which is why the spec said
  not to weaken it while changing the comparison, and it has its own test and its
  own mutation.
- **8 March, the hour that does not exist.** A campaign scheduled for 2:30 AM
  becomes due at 3:00 AM: half an hour late in real time, at the first instant the
  wall clock has passed it. Late is the safe direction — early is the defect this
  session exists for.

### Which stored timestamps changed meaning, and which did not

| Column | Before | After |
|---|---|---|
| `campaigns.scheduled_at` | wall clock, **read as** the box's clock | wall clock, read as the client's. Values unchanged |
| `sms_messages.sent_at`, `contacts.last_messaged_at`, `campaigns.created_at`, `contact_lists.created_at`, `contact_list_members.added_at`, every other naive column | the **box's** local clock — UTC on the droplet | the client's local clock. Definition unchanged: "the application's local wall clock". What changed is what the application's local clock *is* |

Nothing was migrated except `scheduled_at`, and that only for offset-bearing
values nothing in this application writes. `alembic/versions/b7d43f0c9a15` is the
adjudication: it classifies every row by format, leaves a naive value exactly as
it is, converts an instant, and logs the id of anything it cannot read rather than
guessing. The development database has **no scheduled campaign at all**, so the
adjudication runs where the rows are — on the deploy — and says on the record what
it found. `agent/accept-5m.sh` check 6 seeds its own copy with the three shapes
*and rolls `alembic_version` back to `d7e2a91c4f36` first*, because `upgrade head`
against a copy already stamped at head runs nothing and looks calm doing it.

**The residue, and it is real:** rows written before this deploy are UTC wall
clock and rows written after are Eastern, in one column, and the error is
one-directional — an old row reads up to five hours later than it happened. Third
instance in this project of one column with two clocks, and the first where an
invoice is computed from it. That is `decisions/013`, below.

### Escalated: `decisions/013` — the zone moves a billing cycle's boundary

Measured, not assumed. One send at 8:00 PM Eastern on 31 August 2026:

    stored by a UTC box (before)   sent_at=2026-09-01T00:00:00  ->  cycle September 2026
    stored now (after)             sent_at=2026-08-31T20:00:00  ->  cycle August 2026

`billing_service.compute_usage()` filters `sent_at` between cycle **dates**, so an
evening send on a cycle boundary moves one cycle. `stripe_billing._local_date()`
is `datetime.fromtimestamp()`, so the cycle anchor derived from Stripe's
subscription changes with the process zone — a subscription created at 02:00 UTC
on the 1st anchors to day 1 on a UTC box and day 31 on an Eastern one. And
`stripe_meter`'s event timestamp shifts by the same offset. **No billing file was
edited**; the spec named this outcome in advance and routed it here. Nothing is
metered or anchored yet — B1 Part B has not run — so the anchor question is being
asked at the cheapest possible moment.

### Files touched beyond the spec's list, and the requirement that forced each

- `app/core/clock.py`, `app/services/audience_split.py`,
  `app/templates/_composer-summary.html` — **new** (A2 requirement 1; the
  500-line rule; the layering rule).
- `app/core/branding.py` — one template global, `app_timezone`. *("Every
  client-facing time renders in the client's zone, from one formatter" — the
  formatter is in the browser and needs the zone name; `install()` is the one
  function every template renderer calls.)*
- `app/templates/base.html` — the one formatter lives there, beside `esc()`.
  *(Same requirement.)*
- `app/main.py` — `AsyncIOScheduler(timezone=clock.ZONE)`. *(Requirement 5, and
  the spec's own observation that APScheduler prints its job times as UTC.)*
- `app/templates/_composer-upload.html` — `resetSummary()` and the upload flow's
  audience selection. *("One writer per row"; the panel's ticket.)*
- `.env.example` — `APP_TIMEZONE`, named and not set anywhere live, as the spec
  requires. **`.env` and `.env.production` were not touched. Jordan: the droplet
  needs no `.env` edit — the default is the client's zone — but the box's own
  `TZ` no longer matters to this application, and that is worth knowing before
  the next deploy.**

### Found while working

- **The monthly API budgets move with the zone too, and they are ours, not his.**
  `api_budget.py:88` and `lookup_service.py:180` key a spend cap on
  `datetime.now().strftime("%Y-%m")`. A lookup or a Places request made late on
  the last evening of a month now counts in that month rather than the next.
  This is the same shape as `decisions/013` and it is **not** the same decision:
  it is our own spend cap, not the client's invoice, and it moves in the
  direction that matches the calendar a human reads. Recorded rather than
  escalated; escalation item 7 is about *adding* a paid API, not about the
  accounting window of a cap that already exists.
- **`/usage`'s reset date was rendering a day early, and had been.**
  `fmtDate(d.reset_date)` was `new Date("2026-10-01")`, which JavaScript parses
  as midnight **UTC**, so every viewer west of UTC — which is every viewer of
  this product — saw the day before. Fixed by the same formatter A2 needed;
  nobody had reported it.
- **The composer's cap was quoted two different ways on one screen.** The batch
  field re-ran `/preview` and never sent its value, so "This send" quoted the
  whole list while the checklist beside it quoted the cap. Closed as part of
  criterion 4 rather than left, because a panel that disagrees with the
  checklist next to it is the defect this session was sent to fix.
- **The panel costs one extra `/preview` per tab switch, and it is the expensive
  one — so `/preview` came off the event loop.** Fetching the panel before a
  message is typed means a request for the pre-selected audience, the pinned
  all-bidders entry, which measured **237.9 ms** at production shape against
  20.3 ms for a list. Not per page *load*: the composer opens in upload mode,
  where `refreshPreview()` returns early by design. It used to happen on the
  first keystroke instead, so this is one extra request per switch to the
  "existing list" tab, not per keystroke — accepted, because it is what makes
  every row come from one response in every state, including before he has typed
  anything. `refreshPreview()` also now cancels a queued refresh when it runs,
  which took the upload commit from four `/preview` calls to one.
  <br>**What was not acceptable was where it ran.** `preview` was `async def`
  and awaits nothing, so that 237.9 ms of synchronous SQLAlchemy sat on the
  event loop `run_due_campaigns` ticks on every minute and the campaign rail
  polls every five seconds — 5j's outage, with one more caller and a lower
  trigger threshold. It is a `def` now, which FastAPI runs in a worker thread.
  Found by the fresh-context review. `preflight` stays `async` because it awaits
  the capacity assessment; its own per-recipient render is still on the loop and
  is pre-existing, unchanged, and recorded here rather than fixed in a session
  that did not measure it.
- **Two nightly jobs move four hours on the droplet, and that is a fix.**
  `daily_tier_check` (06:00) and `daily_failure_digest` (07:00) were firing at
  2 AM and 3 AM Eastern on a UTC box; their own comments already claimed "07:00
  local" and "an operator reading one signal at breakfast". After this deploy
  they fire at 6 and 7 AM Eastern, which is what they were written for. A
  behaviour change on deploy either way, so it is written down rather than
  discovered.
- **`requirements.txt` does not declare `tzdata`, and this session now depends
  on a zone database.** `zoneinfo` reads the OS one; Debian and
  `python:3.12-slim` both ship it, so this is a stated assumption rather than a
  live risk, and `_resolve_zone()` now refuses to start with a sentence naming
  the package rather than raising a `ZoneInfoNotFoundError` out of a module every
  entry point imports. Adding the `tzdata` Python package would remove the
  assumption; that is escalation item 7 (a new dependency) and is not this
  session's to take.
- **Deployment still keeps the box's clock, and should.** `cron`, the systemd
  journal and `scripts/backup.sh`'s log lines are the droplet's, not the
  application's. `backup.sh`'s retention is count-based, so nothing there depends
  on a day boundary. No file under `deployment/` was touched.

### Deliberately not built

- **No conversion of `sent_at`, `created_at`, `added_at` or
  `last_messaged_at`.** Out of scope by name, and the classification cannot be
  read off the format the way `f4a1c7d90e52` read `contact_lists.created_at` —
  both writers spell it identically, and the only discriminator is "written
  before the deploy", which nothing in the row records. `decisions/013` option 2
  says why a hand-picked cut-off is worse than the bounded error it would fix.
- **No per-user or per-tenant timezone.** One client, one zone, one setting, as
  the spec says.
- **No change to `count_sms_segments()`.** `/preview` reports zero segments for
  an *empty* template in the router; the SMS layer's answer to "how many segments
  is this text" is correct and is not being asked that question.
- **No `/health` field for the zone.** `/health` is unauthenticated and every
  field it gains is published. The zone is on the composer's schedule field,
  where the person who needs it is standing.

# Session 5n — upload-mode template metrics, and cancelling a scheduled campaign

_2026-09-20. Both defects reported by the client's operator. A1 is the message
row under the upload tab; A2 is a control that did not exist._

## A1 — what shipped

**The upload tab now asks `/preview` about no audience, and `/preview` answers
the half it can.** `refreshPreview()` used to return outright when
`composerMode === 'upload'`, so Characters, Encoding and Segments/msg were
never written under the primary flow, and the Unicode warning — the one
CLAUDE.md says must be loud — never fired there. The guard's reason was right
and its scope was wrong: it existed so that the dropdown's leftover audience
was not counted under a composer whose audience is a file, and that reason
covers three of the six figures.

So the request now carries `audience: null` in upload mode and the reply is
split along the same line the figures are:

| figure | depends on | with an audience | without one |
|---|---|---|---|
| Characters, Encoding, Segments/msg, the Unicode warning, risky links, the link tag's state | the message | measured | measured, identically |
| The phone preview and "Rendered for <name>" | the message *and* a sample contact from the audience | rendered against that contact | the raw template, merge tags literal, "Merge tags render against a real contact." |
| Recipients, Held back, Opted out, Total segments, Estimated cost, Audience | the audience | counted | **`null`** |

(The review caught the first draft of this table filing the phone preview
under "the message". It is built by `_template_half()` for convenience, and it
is honest in both modes, but it is not audience-independent.)

`null`, never 0, and the panel paints it as the em dash it already used for a
thing it does not know. Three renderings for three claims, and
`paintSummary()` now says which is which: `…` is "an answer is on its way",
`—` is "there is no answer to have", and a number — including 0 — is a fact
about an audience that resolved. `EMPTY_SUMMARY` carried zeros before this
session, so the "This send" panel under the upload tab read "Recipients 0"
too; that was the same false claim one box over, and the review lens asked
for every place that conflates them.

**No second segment calculation.** The figure on screen is the server's — the
`segments` field of the same reply, from `count_sms_segments()` through
`describe()` — and nothing in the browser measures a message. The test for
criterion 4 uses a 79-character message with one emoji, which is **2**
segments by the UCS-2 rule and **1** by `length / 160`, and asserts the screen
against the fixture reply, against `count_sms_segments()` called directly on
the text the endpoint counts, and against the naive figure it must not equal.
Mutation `U4` puts the naive count in the template and three tests go red.

**The Unicode warning is worded for what it knows.** With no audience it names
the per-recipient fact — "each recipient costs 2 segments instead of 1" — and
makes no claim about a recipient count or a dollar figure, because it has
neither; "At 0 recipients that is $0.00 instead of $0.00" would have been the
null-as-zero defect one sentence down. With an audience the dollar comparison
is exactly as it was.

**The tab switch asks again.** `setMode('upload')` clears the panel with a
fresh ticket, which retires every reply in flight — including one about the
message as it stands. Without a fresh request, "Hello 🎉" typed on the other
tab and carried across sat above a counter still describing "Hello", warning
silent. `setMode()` now schedules a preview in both modes, and the
audience-less request costs no audience resolution and no blocklist load.

**5m's staleness rule holds in both modes.** `refreshPreview()` takes a ticket
before the request whichever tab asked, and a reply about the message two
keystrokes ago is stale whichever tab it was asked from. The three composer
mutations of 5m that touch this path (`P1`, `P4`, `P6`) still resolve and are
still caught.

### Files touched beyond the spec's list, and the requirement that forced each

- **`app/routers/campaign_preview.py` — new.** `/preview` grew a second answer
  and `campaigns.py` reached 536 lines with the cancel endpoint; the 500-line
  rule. The split is along the endpoint's own story — the keystroke path,
  cheap and off the loop, the only thing allowed to fill the panel — and
  `_template_half()` is the one builder both answers share, so the message
  half cannot drift between them. `campaigns.py` is at 399.
- **`app/main.py`** — one `include_router` for the new module.
- **`app/services/campaign_claim.py` — new**, and three lines in
  `campaign_service.py`'s `run_send_loop()`. *(A2: "cancelling at the moment
  `run_due_campaigns` has selected the campaign must not produce a
  half-send" — the review's probe showed the re-check alone does not
  guarantee it.)* `campaign_service.py` is at exactly 500 lines, so the claim
  is its own module and the service calls it.
- **`agent/mutate-5m.py`** — `ROUTER` now names the new file. `P3`–`P5` edit
  the same lines at their new address; the anchors themselves are unchanged
  and all eighteen still resolve. B1's rule: a harness whose anchors do not
  resolve exits 2 and proves nothing, so the harness moved with the code.
- **`tests/js/composer_harness.mjs`** — `textContent` now coerces to a string
  on assignment, as the DOM's does. The stub was storing the raw number the
  composer writes into a cell, so a scenario read back `2` where a browser
  shows `"2"`; a stub that is wrong about the browser is a test that is wrong
  about the product. And the `panel()` reader gained the metering strip, the
  Unicode warning and the rail, split the way 5n splits them.

## A2 — what shipped

**What cancelling means, decided once.** `campaign_dispatch.cancel_scheduled()`
clears `scheduled_at` and leaves an ordinary **draft** — name, audience,
message and every `pending` row intact; not a terminal state. The usual reason
to cancel is a wrong time, and a draft can be sent by hand from the rail with
the audience it was built for. The consequence, stated in the docstring: it
cannot be given a *new* time from here, because scheduling is chosen at
creation and editing in place is its own session — so "wrong day" is cancel,
then create again. A cancelled draft is indistinguishable from one he never
scheduled, and that is the intended end state rather than a lost record: the
campaign never ran, and there is nothing about it to keep.

**Refused on anything that has started, by name.** `CANCEL_STATE_ERRORS`
carries one sentence per state, in his units and with the remedy that works on
this object (`decisions/006`): a `running` campaign says how many of how many
have gone and that a send which has started cannot be stopped; `completed`
points at History; `aborted` and `failed` say to create it again; a draft that
was never scheduled says it is one he sends by hand. The router turns the
sentence into a 409 verbatim, so the rail's toast and the API read the same
words.

**The race, measured before it was fixed.** On the pre-fix tree, with the
cancel simulated as the raw clear it would have had to be:

    selected [1]; then cancelled #1 (scheduled_at -> NULL, status still draft)
    dispatched=[1]  status=completed  sent rows=1

`due_campaign_ids()` answers about a moment; `run_due_campaigns()` then works
through its answer one campaign at a time, and a campaign at the back of that
list waits behind every blast in front of it — minutes at this client's list
sizes, with the send loop yielding after every message. A cancel in that gap
cleared a schedule on a campaign the tick had already decided to send, and
`send_campaign()` could not tell, because it checks `status` and a cancel
leaves the status alone on purpose.

Three mechanisms close it, and the third was the review's doing:

- **`still_scheduled()`** re-asks the two conditions a cancel can change —
  still a draft, still carrying a schedule — against the row as it stands,
  immediately before each dispatch. A column query, so it reads what another
  session has since committed. **Not the time:** selection already judged
  that, and the only thing that can move `scheduled_at` afterwards is a
  cancel, which clears it. The first version re-compared the wall clock too,
  and the review pointed out what that costs once a year — a campaign
  selected in the first 1 AM hour on the first Sunday in November and
  reached after the clocks fall back reads as "not yet due" again, is
  skipped, and is logged as cancelled. `run_due_campaigns()` logs the skip
  and returns only what it sent — it used to return what it selected, which
  after this session would have named a cancelled campaign as dispatched.
- **The cancel's clear is conditional at the database** — `UPDATE … WHERE
  status = 'draft' AND scheduled_at IS NOT NULL` — and a clear that matches no
  row re-reads the campaign and refuses with the state it is *now* in. So the
  other direction of the race, a dispatch that flipped the campaign to
  `running` between the cancel's read and its write, ends with the client
  told "already sending — 1 of 443 sent so far" rather than "cancelled" over a
  blast that is going out. `test_cancel_decides_on_the_row_as_it_stands_not_
  the_object_it_read` holds a stale identity map and proves it.
- **The flip to `running` is itself a claim** (`app/services/campaign_claim.py`).
  The first version of this session stopped at the two above and wrote down a
  residual: between `still_scheduled()` and the `running` commit sits the
  pre-flight, which awaits the provider's balance call, and whether a cancel
  can land in that gap "depends on the provider blocking the loop". The
  review measured it instead of taking it — four arrangements, a cancel fired
  150 ms into a 400 ms balance call:

      provider=blocking  cancel=loop    -> refused: already sending            (deployed config)
      provider=blocking  cancel=thread  -> cancel SUCCESS; status=completed, sent_rows=3
      provider=awaiting  cancel=loop    -> cancel SUCCESS; status=completed, sent_rows=3
      provider=awaiting  cancel=thread  -> cancel SUCCESS; status=completed, sent_rows=3

  Row 2 is a `def` cancel route with **the deployed provider**: `urlopen`
  releases the GIL and a threadpool thread runs the cancel in the meantime.
  And a `def` route is exactly what this project's own 5j lesson — sync
  SQLAlchemy on an `async def` route holds the loop — pushes the next session
  toward; the review mutated the route to `def` and every test stayed green.
  The closure was resting on three properties nothing asserted: the route on
  the loop, the provider blocking it, one uvicorn worker.

  So `run_send_loop()`'s flip is now `campaign_claim.take()`: on a first send,
  `UPDATE campaigns SET status='running', started_at=? WHERE id=? AND
  status='draft' AND scheduled_at IS <exactly what the caller loaded>`. One
  row means this run owns the send; zero means the row changed under it — a
  cancel cleared the schedule, or another path took it — and it raises
  `SendClaimLost` before anything is sent, with the row untouched. Between
  the cancel's conditional statement and this one the database decides the
  race, whichever thread, loop or worker either arrives on. The scheduler
  logs a lost claim as a stand-down, not a failure, and does not count it as
  dispatched. It closes the button path's double-click for the same reason.
  A top-up is not a claim — it runs on a `completed` campaign whose checks
  are `campaign_topup.assess()`'s — and its flip is the plain one it was.

  `campaign_service.py` was at exactly 500 lines. The claim lives in its own
  module and the four lines of flip became three; the file is at 500 again.
  Mutation `C7` reverts the flip to unconditional: four tests go red — the
  three mid-pre-flight cases with "3 sent" after a cancel the client was
  told succeeded, and the double-click case with a completed campaign
  relabelled `aborted: this audience resolved to nobody`, because the second
  run flipped it to `running` again and found nothing left pending.

**Tested as the race, not as the happy path.** One test injects the cancel at
the exact seam — after `due_campaign_ids()` returns and before
`send_campaign()` — by wrapping the selection. One runs it on the event loop:
two campaigns due, the second cancelled while the first is mid-blast, which
is the shape production has. Three parametrized cases put the cancel *inside*
a campaign's own pre-flight — from the loop with a provider that truly
awaits, from a thread with a provider that blocks (the deployed one, as a
`def` route would reach it), and both — and require the cancel to say
"cancelled" *and* nothing to send in the same run. One more hands the same
draft to two send loops. All assert no `sent` row, the status, and the
campaign absent from what the tick reports. `accept-5n.sh` 7b removes the
re-check from a scratch copy and 7c makes the flip unconditional in another,
and each judges by the **sentence the test names** rather than the exit
code: the review found the concurrent race test red on the pre-fix copy for
the wrong reason — the first test's campaign had sent (the defect), the
hold-back window then held the fixture's contacts back, and the second
test's "first" campaign resolved to nobody, aborted without yielding, and
failed its own precondition. Every sending test now starts from an untexted
list, and 7a runs each criterion-7 test alone as well.

**The control.** The rail draws Cancel on a draft that carries a
`scheduled_at`, and on nothing else — beside "Send now" on a draft that does
not and "Top up" on a completed one. `cancelCampaign()` confirms, posts,
shows the server's sentence, reloads. Proven through the real JavaScript
against a rail fixture that deliberately holds a scheduled draft *and* an
unscheduled one, so "Cancel on every draft" (`C5`) has something to fail on.

## Found while working

- **The re-ask on the tab switch made one of 5m's guards unreachable by its
  own test, and the harness said so.** `mutate-5m.py` P6 removes the ticket
  `resetSummary()` takes, and on the first run against this tree it
  **survived**: 5n's `setMode('upload')` now schedules a preview 300 ms later
  whose own ticket retires the stale reply, and 5m's scenario read the panel
  only after settling — by which time the re-ask had repaired it. The guard
  is still doing work: without it the abandoned audience's figures paint for
  up to 300 ms under the upload tab. Measured with P6 applied to a scratch
  copy, the panel read 100 ms after the switch:

      with P6      inside: ALL BIDDERS · 40 recipients · 40 segments   settled: — · — · —
      repo         inside: — · — · —                                    settled: — · — · —

  `upload_mode_clears_and_stays_clear` now times the switch so the reply
  lands inside the debounce and reads the panel there as well as after
  settling; P6 is caught again. B1's lesson, verbatim: a mutation reachable
  in one arrangement may not be in the next, and the caught-by list is what
  tells you — a second mechanism added beside a guard can take the guard's
  test away from it without taking the guard's job.
- **`audience_split.resolve()` answers an unresolvable selector with zeros,
  and `/preview` passes them through as a resolved audience of nobody.**
  `audience="nonsense:zzz"` returns `recipients: 0`, `total_segments: 0`,
  `estimated_cost: 0.0` and the selector as its own label. Pre-existing,
  unreachable from the dropdown, and outside this session's file list — but it
  is a 0 that means "I could not resolve this", not "nobody is in it", which
  is the conflation A1 was sent to remove one layer up. Found by the review.
- **`audience=""` now yields nulls where it used to yield zeros.** The
  endpoint's `if not payload.audience` catches the empty string, which is what
  the existing tab sends when the dropdown reads "No contacts yet". No
  audience is selected in that state, so "—" is the right claim; recorded
  because it is a behaviour change the session did not set out to make.
- **`run_due_campaigns()` returned the selected ids, not the dispatched ones.**
  Harmless while every selected campaign was sent; after this session it
  would have reported a cancelled campaign as dispatched. Returns
  `dispatched` now (`C4`). `test_metering_pass.py` reads the value and is
  unaffected.
- **The harness stub stored numbers where a browser stores strings.** See
  A1's file notes. Every existing scenario passed either way because none
  read a numeric cell; the first one that did found it.
- **`describe("")` reports `gsm7_segments_if_stripped: 1`** for an empty
  message (`count_segments("")` is 1, and the router zeroes `segments` but not
  this sibling). Pre-existing, unreachable from any renderer — the field is
  read only when the encoding is UCS-2, and an empty message is GSM-7 — and
  left alone rather than touched in `app/sms/`.
- **`test_run_due_campaigns_reports_only_what_it_dispatched` cannot see `C4`
  on its own.** Its cancelled campaign is cancelled *before* the tick, so the
  selection already excludes it and "selected" equals "dispatched". The two
  race tests are what pin `C4`; the test keeps its name because the property
  it asserts is real, but the mutation's caught-by list is the honest record.
  The review added that in a full-module run its "going out" campaign had
  resolved to nobody and aborted, and still counted as dispatched — so it now
  starts from an untexted list and asserts the campaign completed with rows
  sent.
- **The first version of `accept-5n.sh` 7b decided by exit code and ran one
  test.** An import error, a fixture failing its own precondition, or the
  neighbour-poisoned race test above would all have read as "red without the
  re-check". It now runs both race tests alone and greps for the assertion
  sentence each names, and 7c does the same for the claim.
- **Every earlier harness verified pristineness only for the files it
  patches.** A leftover edit in a test file in the scratch tree would not be
  noticed. `mutate-5n.py` compares every `.py`/`.html`/`.mjs`/`.json` under
  `app/`, `tests/` and `alembic/`. The older harnesses keep their narrower
  check; widening them is a one-block change each and is not this session's.
- **A visual check of the composer needs a login.** The dev server was booted
  and curled (`/login` 200, `/static/app.css` 200, `/health` healthy); looking
  at the upload tab in a browser requires typing the admin password into the
  login form, which is a human's action and not this session's. The node
  harness runs the real partials, which is what the criteria are proven on.

## Deliberately not built

- **Re-scheduling in place.** Out of scope by name; cancel returns the draft
  to a state the existing composer handles.
- **A "cancelled" marker.** A cancelled draft reads as a draft, not as a
  campaign that failed; the toast says what happened at the moment it
  happened. A badge would outlive its usefulness the moment he sent the draft
  by hand.
- **A mutation for `still_scheduled()` reading through `db.get()`.** The
  identity map cannot be stale at that call site today — nothing loads the
  campaign before the check, and every send commits, which expires everything
  — so the arrangement that makes the column query load-bearing does not
  exist to construct. The comment on the query says why it is a column query;
  it is not claimed as a guard.
- **A pin on the cancel route being `async def`.** The review suggested one
  as the minimum; with the flip a claim, nothing about the race depends on
  the route's kind, and the thread-arrival test covers what a `def` route
  would do. The route stays `async def` for consistency with its neighbours.

## Verified this session

- `python -m pytest tests/ -q` — **858 passed** (835 + 17 cancel + 6 panel).
- `bash agent/gate.sh` — green, twice.
- `bash agent/accept-5n.sh` — checks 0-10 pass; 4b and 6b show the figures and
  the sentences; 7a runs each criterion-7 test alone; 7b and 7c show the
  race tests red on the trees they exist to reject, by the sentence each
  names.
- `agent/mutate-5n.py` — **15 caught / 0 survived**, two consecutive runs on a
  verified-pristine tree, every mutation caught by a test that names it.
- `agent/mutate-5m.py` — 18 caught / 0 survived after its anchors moved and
  P6's scenario was re-timed (one survivor on the first run; see above).
- One synchronous fresh-context review; its findings are recorded above and
  every one of them changed something: the claim, the re-check's clock, the
  neighbour-proof tests, 7b's verdict, the table, the initial markup, a
  docstring, the harness's pristine scope.
- `./run.sh` → `/login` 200, `/static/app.css` 200 after `npm run build:css`;
  the CDN and white-label greps return nothing.

# Session L1 — the LiveAuctioneers bidder source (2026-09-22)

His registered bidders, read from his own partner portal every morning, land in
the contact list the way a CSV does, and their behaviour lands beside them in
its own table. `decisions/014` records why this reverses the 18 Aug ruling
without reopening marketplace scraping. **Nothing ran against LiveAuctioneers**;
the first live run is Jordan's (Part B).

## What was built

- **Three files ported, behind A4A's own interface.** `app/sources/platforms.py`
  (the registry, kept a registry), `app/sources/auction_scraper_base.py` (browser
  lifecycle, retry, paging, screenshots) and `app/sources/liveauctioneers.py`
  (the selectors). `example_api_source.py` is deleted. The sources produce
  `BidderRecord`s and never touch the database — `_save_profile` is unwound
  into `app/services/bidder_scrape.py`, which runs them through
  `ContactSource.ingest()` (the existing upsert, the unique phone index) and
  writes the behaviour.
- **`bidder_profiles`** — one row per contact per platform, real types:
  integer counts, `avg_hammer_cents` in integer cents plus a ceiling flag for
  "Less than $100", `member_since` a date, booleans for card on file and tax
  exemption. NULL means the page did not say. **`bidder_scrape_runs`** records
  each run with counts that add up to what was read, and `cleanup_ran`.
  Migration `c1e8f3a6b29d`: two new tables, `contacts` untouched.
- **The schedule**: daily at `BIDDER_SCRAPE_HOUR:MINUTE` in `clock.ZONE`,
  registered only when `LA_USERNAME`/`LA_PASSWORD`/`LA_HOUSE_ID` are all set,
  `max_instances=1`, a sync job on the executor thread with its own event loop.
  A process lock plus a fresh-`running`-row check refuse a second run.
- **The deadline** (`BIDDER_SCRAPE_TIMEOUT_SECONDS`, 3600) cancels the scrape,
  and the cancellation unwinds through the `finally` that closes the context
  and stops the driver. Each teardown step is bounded at 15 s.
- **The browser profile** lives under `BROWSER_PROFILE_ROOT`, default
  `~/.local/state/sms-platform/browser-profiles/<platform>`, mode 0700 —
  outside the deploy tree (`deploy.sh` rsyncs `app/` with `--delete`), outside
  anything served, outside what `backup.sh` archives (the database file only).
  The run refuses to start if it resolves inside the project.

## Decisions made alone, and why

- **A bidder with no phone is not kept.** `contacts.phone` is the identity and
  NOT NULL; a phoneless row could only be keyed on name + sale, which is the
  reference system's second identity — the thing A4A's dedup exists to
  prevent. Counted (`no_phone`), not stored. He still has them on the platform.
- **Screening, when on, applies the prospect gate exactly**: `mobile`/`voip`
  pass, `landline`, `toll_free` and `unknown` are held (not ingested) and
  re-screened on the next morning's read. When off (the default), bidders land
  unscreened, as a CSV row does. A held bidder who is already a contact from a
  CSV is not deactivated — this path just does not touch him.
- **A blocked bidder is skipped outright**, as `import_service.commit()` skips
  an opted-out CSV row: not created, not updated, not listed, no profile.
- **One list per platform** (`"LiveAuctioneers bidders"`, from the registry's
  label), not one per run — the daily read re-finds the same people.
- **Playwright pinned at 1.63.0**, not the reference 1.41.0: a browser driving
  a logged-in account should be current, and 1.63's Chromium was already cached.
- **The proxy support was not ported.** A4A has no proxy setting, and a paid
  proxy is a budget decision (escalation item 7).

## Peak RSS, real Chromium, fixture run

Measured by `tests/_la_rss_probe.py`: headless Chromium driven through the
unchanged source and `run_scrape()`, every request answered from the fixture by
route interception (2 requests, both fulfilled, none escaped). Summed RSS of
the Python process and every descendant, sampled every 100 ms — summing counts
shared pages once per process, so this **overstates**.

| run | bidders | peak tree | of which browser | processes at peak | after |
|---|---|---|---|---|---|
| fixture | 131 | **482-541 MB** (three runs) | 402-464 MB | 7-8 | 1 (clean) |
| scaled | 500 | **537 MB** | 460 MB | 7 | 1 (clean) |
| app server, serving `/login`, `/health`, static | — | **95 MB** | — | 1 | — |

**Headroom on 2 GB: yes, with a margin, on these numbers — and they are macOS
arm64 numbers, not the droplet's.** App + scrape at peak is about 0.63 GB,
leaving roughly 1.3 GB for the OS, nginx and a campaign send (0.64 GB at the
highest fixture reading). Memory grows
modestly with list size (+55 MB from 131 to 500 bidders): the table is 120
rows a page and the records are small. The reference box's 1.6 GB was
seventeen *leaked* drivers at ~95 MB each, not one scrape.

Not measured, and said so rather than estimated — the review's list, and it is
the half that matters more than the double-counting: the live portal is a React
single-page app, not a static page with a 60 ms panel; a real run is 25-60
minutes and hundreds of panel opens, not a hundred seconds; production runs
inside uvicorn on the executor thread, not a bare process; the droplet is Linux
x86; and the app was measured idle at 95 MB, never while sending. On 2 GB with
no swap, Chromium's renderers carry a raised OOM score and would likely be
killed first — the run fails part-way and keeps what it read — so the likelier
cost is CPU contention on one vCPU with a send. Part B
item 3 — `free -m` before and during the first live run — is the measurement
that settles it, and the answer if it is tight is a bigger droplet.

## Found while working

- **`Locator.is_visible(timeout=…)` ignores its timeout** in the pinned SDK
  (documented "Deprecated: This option is ignored… returns immediately"). The
  reference `_open_dropdown()` relied on it to wait three seconds for the
  dropdown and never waited at all. The port uses `wait_for(state="visible")`.
- **The reference panel wait read stale panels.** "Any profile marker is on
  the page" is true of the *previous* bidder's panel, so a slow click read
  bidder 1's phone as bidder 2's — on a phone-keyed dedup, two people merged
  into one contact. The wait now also requires the page text to change.
- **The reference phone regex ran over the whole page**, so any ten-digit
  figure in the table became a phoneless bidder's number. Phone is now read
  only from the lines the click added. Consequence, by design: two bidders on
  adjacent rows sharing a number — the second reads as `no_phone`, not as the
  first's number.
- **`member_since` was the first date on the page** — with a sale selected,
  the sale's own date. It is now read beside its label.
- **A changed page reported success.** Renamed panel labels meant every row was
  skipped and the run "completed" with zero bidders. It now fails with
  `SelectorDrift`.
- **Pagination clicks on the last page used the 60 s default timeout**, three
  attempts at two selectors — six minutes of a browser held open to learn the
  list had ended. Now 5 s.
- **Upload undo does not know about `bidder_profiles`.**
  `import_service._still_referenced` can delete a contact that has a profile,
  leaving an orphan row (SQLite does not enforce the foreign key). Outside the
  file list; left alone. (Review finding 19.)
- **Analytics are read from the whole page**, not only the lines the click
  changed — deliberately, since "Card on File / Yes" repeats between bidders.
  If the live portal redraws the panel piecemeal, the figures could be the
  previous bidder's while the phone is not. The first live run should spot-check
  three bidders against the portal by eye. (Review finding 5, unverified.)
- `docs/ARCHITECTURE.md` and `docs/NEW_CLIENT_CHECKLIST.md` still name
  `example_api_source.py`, which this session deleted. Outside the file list;
  left alone.

## The fresh-context review

One synchronous reviewer, 20 findings. What each changed:

- **Serious — the production unit cannot run this.** `deployment/app.service.template`
  has `ProtectHome=read-only` and writes allowed only inside the project, which
  `profile_dir_problem()` refuses — no location works. The unit is outside the
  file list, so it is **Part B item 0** below; the run now fails with a message
  naming the fix (`_check_writable()`, mutation `S18`) instead of a bare
  PermissionError from Chromium.
- **Serious — a changed phone field was a "completed" run creating nobody**
  (measured: 131 read, 131 no_phone, 0 created). Now `SelectorDrift` (`S14`).
- **Silent partials** — paging that stopped early, or most panels failing, read
  `completed`. New status `incomplete` with the shortfall in `error`
  (`rows_expected` from the page's own "of N"; read below 90% of rows seen). `S15`.
- **Two bidders with one name** — the reference clicked the first row with that
  name, so the second was a "repeat" and never landed. The row's own cell is
  clicked first now (`S12`); the fake opens by row, as the real page does.
- **One number, two people, across runs** — contact was one person, profile the
  other, flipping with page order. A profile owned by a different platform
  username is no longer overwritten; `profile_conflicts` counts it (`S16`).
- **A changed page at real timings reported `timed_out`, not the cause** — 15 s
  per missed panel. Five misses before anything is read now raise
  `SelectorDrift` (`S13`), and the row click has the short timeout.
- **Criterion 7 passed with the per-step teardown bound removed** — a hanging
  `close()` test now covers it (`S11`).
- **`misfire_grace_time` defaulted to one second** — a 09:00 firing missed by a
  stalled loop skipped the day silently. 3600 now (`S17`).
- **The AST scan** listed three files by hand; it now derives the scraper
  modules from the registry, and flags `sqlite3`/`importlib`/`ingest()`. The
  reviewer's eight deliberate bypasses (`getattr`, `__import__`, a session
  parameter named `sess`, …) still pass it — it is a structural tripwire for
  the realistic shape, not a sandbox, and five behavioural tests also fail on a
  real write.
- Also fixed: counters survive a rollback after `ingest()` committed;
  screenshots pruned after 14 days; the dead `except CancelledError` removed;
  the logged-in check no longer accepts `[class*="control"]`, which matches a
  login form.
- **Not changed, recorded:** see the next two sections.

## Part B additions (Jordan's)

0. **Before anything else, the unit.** Add to `/etc/systemd/system/<app>.service`
   under `[Service]`: `StateDirectory=sms-platform-browser` — and in `.env`:
   `BROWSER_PROFILE_ROOT=/var/lib/sms-platform-browser`. systemd creates it owned
   by `appuser`, writable under `ProtectSystem=full`, outside the project, the
   deploy rsync and the backup. Without it every run fails, loudly, on day one.
   (`deployment/app.service.template` should carry the same line; outside L1's list.)
5. **Do not deploy or restart during the 09:00 read.** The sync job runs on the
   loop's default executor and `asyncio.run` waits for it: a restart mid-scrape
   blocks up to systemd's 90 s kill, and the run row stays `running` until
   `_running_elsewhere` ages it out (deadline + 10 min). Chromium dies with the
   cgroup, so nothing leaks.
6. **`--no-sandbox`** is carried over from the reference. Removing it is right in
   principle and may not launch on Ubuntu's AppArmor userns restriction; try it on
   the first live run, and keep it only if Chromium refuses.

## File-list departures

- **`app/services/bidder_scrape.py` is new** and not in the list. The list names
  `contact_service.py` (494 lines) and `scrape_runner.py` (451); neither can take
  a runner plus a persistence pass under the 500-line rule, and `scrape_runner`'s
  job row is the prospect pipeline's. Neither file was edited.
- **`tests/conftest.py`** blanks `LA_*` and points `BROWSER_PROFILE_ROOT` at a temp
  dir, on the Stripe precedent, so a developer's real `.env` can never register
  the job or write a profile into their home during the suite.
