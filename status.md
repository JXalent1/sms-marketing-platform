# Status

_Last updated: 2026-08-19_

## Where we are
Module 2 complete. Industry category is now a real concept the whole app understands:
two tables, the five seeded categories, a selector grammar that unions and intersects,
CRUD that will not let anyone silently discard tagging history, and an import flow that
previews before it commits and can be undone without destroying anything a human added.
Backend only — no screens; module 3 owns those.

`bash agent/gate.sh` passes; 76 tests (46 baseline + 30 new), green twice in a row.

## Current module
None in progress. Next up: module 3 — Contacts & categories UI.

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

## Next
1. Module 3 — Contacts & categories UI

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
