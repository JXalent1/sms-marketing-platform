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
- **The live box has still not been deployed to.** Criterion 11 cannot pass until
  someone does. The box still runs the pre-5c nginx config, the hot-patched SDK,
  and pre-5d application code.
