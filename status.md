# Status

_Last updated: 2026-08-19_

## Where we are
Module 1 complete, and session 1b has closed the five findings from its review. The
skeleton has real migrations that the test suite actually applies, a compiled stylesheet
with the dark-first token system from `Auctions4America.pen`, A4A's commercial terms
priced in exact decimal, and a white-label test that runs the app instead of grepping it.
`bash agent/gate.sh` passes; 46 tests.

## Current module
Module 2 (Categories & segmented upload) in progress in a parallel worktree.
Module 5a (Deploy scaffolding) complete — see below.

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

## Next
1. Module 2 — Categories & segmented upload

## Blocked on
- A4A logo and brand hex colors. **No longer blocking:** the placeholder palette from
  the design file is in use and the real hex is a one-line `.env` change
  (`BRAND_COLOR_HEX` / `BRAND_ACCENT_HEX`) — verified, no template edit and no CSS
  rebuild required.
- Sample CSVs, one per category (needed for module 2's header mapping)
- Sender number strategy (needed before the first live send)

## Found while working
Real issues outside the current module's scope. Left alone deliberately; each names the
module that owns the file.

- **`sessions/session-5a.md` does not exist.** Module 5a was built from its row in
  `modules.md` (scope, file list, "5a stays out of `app/main.py`") plus the acceptance
  criteria in the session prompt. Every other module has a session spec; if one was
  written for 5a it never landed in the repo. Worth knowing before 5b, which depends on
  5a and has the same risk.
- **`run.sh` still creates `venv/` while the project uses `.venv/`.** Unchanged from the
  module 1 note below. `deployment/bootstrap.sh` creates the server venv at
  `$APP_DIR/venv`, matching `app.service.template` and `deploy.sh`, so the server side is
  self-consistent; the local mismatch is untouched and still module 8's.

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
