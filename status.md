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
