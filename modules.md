# Modules

_Last updated: 2026-08-18_

Root for all paths: `sms-marketing-platform/`.

## What the skeleton already gives us (don't rebuild any of this)

Carrier abstraction and provider swap · `count_sms_segments()` · phone normalization
and E.164 dedup · blocklist and opt-out persistence · the campaign send loop ·
pre-flight balance check · delivery webhooks · billing/usage math · CSV contact
source with forgiving header matching · auth · 22 passing tests.

That's the entire send path. What's left is the segmentation layer, the prospecting
engine, and the new UI.

## Build order

| # | Module | Status | Depends on | Touches files |
|---|--------|--------|------------|---------------|
| 1 | Foundation, pricing & white-label | Not started | — | `package.json`, `tailwind.config.js`, `app/static/**`, `app/templates/base.html`, `app/main.py`, `app/core/config.py`, `app/core/branding.py`, `app/services/billing_service.py`, `app/routers/usage.py`, `app/templates/usage.html`, `.env.example`, `alembic/**`, `tests/test_billing.py` |
| 2 | Categories & segmented upload | Not started | 1 | `app/models/category.py`, `app/models/__init__.py`, `app/services/category_service.py`, `app/services/contact_service.py`, `app/services/import_service.py`, `app/routers/categories.py`, `app/routers/contacts.py`, `app/sources/csv_source.py`, `alembic/versions/*`, `tests/test_categories.py`, `tests/test_import.py` |
| 3 | UI: shell, Today, Contacts | Not started | 2 | `app/templates/base.html`, `app/templates/today.html`, `app/templates/contacts.html`, `app/routers/pages.py`, `app/routers/dashboard.py`, `app/services/dashboard_service.py`, `tests/test_dashboard.py`, `tests/test_contacts_api.py` |
| 4 | Composer & campaign guardrails | Not started | 3 | `app/models/campaign.py`, `app/services/campaign_service.py`, `app/routers/campaigns.py`, `app/templates/campaigns.html`, `alembic/versions/*`, `tests/test_campaign_guardrails.py` |
| 5 | Prospect engine & review queue | Not started | 3 | `app/models/prospect.py`, `app/models/scrape_job.py`, `app/models/phone_lookup.py`, `app/prospects/**`, `app/services/prospect_service.py`, `app/routers/prospects.py`, `app/templates/prospects.html`, `alembic/versions/*`, `tests/test_prospects.py`, `tests/test_scoring.py` |
| 6 | Discovery sources | Not started | 5 | `app/prospects/sources/**`, `app/prospects/taxonomy.py`, `app/core/config.py`, `tests/test_sources.py`, `tests/fixtures/**` |
| 7 | Opt-in page & cold-send guardrails | Not started | 4, 5 | `app/routers/public.py`, `app/templates/optin.html`, `app/models/consent.py`, `app/models/sender_pool.py`, `app/sms/quiet_hours.py`, `app/sms/optout_match.py`, `app/services/export_service.py`, `app/services/campaign_service.py`, `alembic/versions/*`, `tests/test_optin.py`, `tests/test_cold_guardrails.py` |
| 8 | Remaining screens, scheduling & deploy | Not started | 6, 7 | `app/templates/history.html`, `app/templates/categories.html`, `app/templates/blocklist.html`, `app/templates/usage.html`, `app/routers/pages.py`, `app/main.py`, `app/services/monitoring.py`, `scripts/*`, `deployment/*`, `docs/CLIENT_GUIDE.md` |

**Ship point: after module 4.** He can run category-segmented campaigns off CSVs, on
the new dark UI, billed correctly. Modules 5–8 add the prospecting engine.

Modules 5 and 6 are the two heavy ones. If either runs long, split it at the marked
seam rather than letting the session sprawl.

---

## Module details

### 1. Foundation, pricing & white-label
**Purpose:** Solid ground before any feature sits on top of it.
**Scope:**
- `git init` and an initial commit — the prior client's server had no repo and its local
  copy silently drifted six files behind production
- Alembic wired up, initial migration capturing the current schema
- Tailwind compiled at build time into `app/static/app.css`; Inter self-hosted; static
  mount added to `main.py`; `cdn.tailwindcss.com` and the runtime Google Fonts fetch removed
- Brand color moved from Tailwind class-name interpolation (`bg-{{ brand.color }}-600`)
  to CSS custom properties fed from `.env`, so a real brand hex works
- **Dark as the default theme**, light available via `data-theme`
- Billing switched to A4A's terms: no monthly fee, 10,000 segments included per month,
  $0.015/segment after — all from `.env`
- Carrier-name sweep across templates, error paths and exports

**Out of scope:** any new feature, screen, or schema change beyond the Alembic baseline.
**Acceptance (demonstrable):**
- `python -m pytest tests/ -q` exits 0 — 22 baseline plus the new billing tests
- Billing test: 32,940 segments in a cycle → `$344.10`; 8,000 segments → `$0.00`
- `alembic upgrade head` succeeds against a fresh DB
- `curl` on `/static/app.css` → 200; `grep -rn "cdn.tailwindcss.com\|fonts.googleapis.com" app/templates/` → nothing
- `grep -rni "telnyx" app/templates/ app/routers/` → nothing
- App renders correctly with the network blocked (screenshot in transcript)

---

### 2. Categories & segmented upload
**Purpose:** Make industry category a first-class concept, and make importing against it trivial.
**Scope:**
- `categories` table (slug, label, color, sort_order, is_active), seeded with the five
- `contact_categories` many-to-many with `source` and `confidence`, unique on (contact, category)
- `resolve_audience()` extended: `category:<slug>`, comma-union, `category:<slug>&list:<id>`
- Category CRUD API
- Upload flow: category chosen **before** parsing and required; preview returns per-category
  counts (rows, valid mobiles, already-in-category, opted out, unusable); import creates an
  `upload_batch` ContactList for provenance; undo-an-import reverses one batch

**Out of scope:** the Contacts screen UI (module 3).
**Acceptance:**
- Seeds produce exactly 5 categories
- Selector tests: single category, union of two, category∩list, and a contact in two
  categories resolving exactly once
- Preview on a fixture CSV returns the exact counts, asserted
- Undo removes that batch's memberships and leaves pre-existing contacts intact
- `alembic upgrade head` succeeds; suite green

---

### 3. UI: shell, Today, Contacts
**Purpose:** The dark interface from `Auctions4America.pen` — the shell plus the two screens he lives in.
**Scope:**
- Sidebar shell per the design: Send / Audience / Account groups, segment count and
  sender number pinned bottom
- **Today:** next-auction hero, five category cards showing *days since last send*, four
  stat tiles, 14-day segment bar chart, per-category last-send outcome bars
- **Contacts:** category tabs with counts, server-side pagination, search across
  name/phone/company, bulk add-to-category and export, line-type badge
- `dashboard_service` supplying the numbers

**Out of scope:** Compose and Prospects screens.
**Acceptance:**
- `GET /` returns 200 and contains all five category labels
- `GET /api/dashboard` returns days-since-last-send per category, verified against seeded campaigns
- Seed 1,000 contacts: page 1 returns 50 within a bounded query count; category filter
  counts match; search finds a known contact
- Rendered pages match the Pencil design (screenshots in transcript)
- Suite green

---

### 4. Composer & campaign guardrails
**Purpose:** Make it structurally hard to text the wrong niche. This is the module that solves the original problem.
**Scope:**
- `campaigns.category_id`; creation **requires** a category or an explicit typed override
- Three-step composer with live character/segment/cost metering and a loud UCS-2 warning
- Pre-flight endpoint returning structured checks: capacity, STOP present, segment count,
  recent-contact overlap, link shortener, off-category keyword match
- Recent-contact suppression (default 3 days, configurable)
- Scheduled send

**Out of scope:** cold-send guardrails (module 7).
**Acceptance:**
- Campaign without a category is rejected with a clear error
- Suppression: contact texted 2 days ago excluded, 5 days ago included
- Pre-flight returns each check as pass/warn with a reason
- UCS-2 test: adding one emoji changes segments-per-message and the estimate
- Suite green

---

### 5. Prospect engine & review queue
**Purpose:** A holding pen between a scraper and the textable list, with the ranking that makes it useful.
**Scope:**
- `prospects`, `scrape_jobs`, `phone_lookups` tables
- `ProspectSource` base class mirroring `ContactSource` — `fetch()` yields, base persists
- Job runner with a hard timeout and **`finally`-block cleanup** (the prior system leaked
  one browser process per daily run — 17 orphans, 1.6 GB RSS on a 3.9 GB box)
- Line-type lookup behind a carrier-agnostic interface, with a persistent cache keyed on E.164
- DNC scrub on wireless numbers, applied at promote time
- Scoring: line type, category confidence, distance vs the category's radius rule,
  reseller licence present, multi-source corroboration
- Review queue UI: sortable, bulk select, promote-into-category, reject-with-reason;
  rejections write a permanent suppression list

**Seam if this runs long:** split after the runner + tables, leaving validation/scoring/UI
for a 5b.
**Out of scope:** any actual source implementation.
**Acceptance:**
- Promote creates a Contact tagged with the chosen category and links `promoted_contact_id`
- Reject suppresses permanently; re-ingesting the same record does not reappear
- A deliberately hung fake job is killed at timeout and cleanup is asserted to have run
- Landlines excluded from promote-eligible by default; a repeat lookup hits the cache and
  makes no call (asserted on call count)
- A DNC-listed number cannot be promoted (test)
- Every prospect retains `source_url`, scrape timestamp and `raw_payload` (test)
- Suite green

---

### 6. Discovery sources
**Purpose:** Actually find the buyers.
**Scope:**
- **Google Places** — category → search-term taxonomy, **per-category radius**
  (equipment/food service ~150mi, estates ~100mi, memorabilia national, general
  configurable), pagination, quota handling and a spend cap, dedup against existing
  prospects and contacts
- **FL DBPR** licensed food-service establishments
- **Contractor and county secondhand-dealer licences**
- **Sunbiz** officer-name enrichment

**Seam if this runs long:** Google Places alone is a complete session; the registry
sources are a natural 6b.
**Acceptance:**
- Against recorded fixtures, N API results produce N prospects; a second run produces 0 new
- Radius rule asserted per category
- Exceeding the configured quota stops the job cleanly and logs what was skipped
- Each registry source turns a fixture into normalized records with the right category
- Sunbiz enrichment attaches an officer name to a matching prospect
- Suite green

---

### 7. Opt-in page & cold-send guardrails
**Purpose:** The clean-consent channel that reaches collectors, plus the firewall that keeps cold traffic away from the warm list.
**Scope:**
- Public category-tagged opt-in page for ad traffic; consent record (timestamp, IP, source,
  wording shown); contact export shaped for Facebook/Google Custom Audiences
- Separate sender pool for cold traffic
- Quiet hours by **recipient** timezone; throttled ramp on new numbers
- Fuzzy opt-out matching ("stop texting me", "remove me", not just the keyword)
- Failure/opt-out-rate auto-pause with alerting

**Acceptance:**
- POST to the opt-in endpoint creates a contact with the category and a consent row
- Export produces the documented column set
- A send scheduled into a recipient's quiet hours is deferred, not dropped
- Ten opt-out phrasings all match
- A simulated campaign crossing the failure threshold halts itself and records why
- Suite green

---

### 8. Remaining screens, scheduling & deploy
**Purpose:** Close out the four secondary screens, then make it run without someone watching.
**Scope:**
- **History** — past campaigns, filterable by category, with per-campaign outcome
  breakdown and grouped failure reasons
- **Categories** — rename, reorder, recolor, set the per-category suppression window
- **Opt-outs** — searchable, showing the message that triggered each one
- **Usage & billing** — cycle meter against the 10,000 included, per-category cost
  attribution, history
- Nightly scrape cron, low-credit alert, daily failure digest
- Deploy config and a client-facing guide

These four screens are thin views over data that already exists by this point, which is
why they ride along here rather than taking their own module.
**Acceptance:** each screen returns 200 and renders seeded data correctly (screenshots);
per-category cost attribution sums to the cycle total (test); scheduler registers the
expected jobs on startup; deploy script runs in dry-run; `docs/CLIENT_GUIDE.md` contains
no carrier name. Suite green.

---

## Parallel-safe work

Default is sequential. These have built dependencies and disjoint file sets, so they can
run concurrently in separate worktrees:

- **4 + 5** — composer/campaign path vs prospect backend. Only overlap is
  `campaign_service.py`, which module 5 doesn't touch. Safe.
- **6 + 7** — discovery sources vs opt-in page and cold guardrails. No overlap.

Everything else shares files or dependencies.


---

## Coverage against the plan

| Plan section | Module |
|---|---|
| Part 1 §3.1 data model · §3.2 selectors · §3.3 upload | 2 |
| Part 1 §3.4 campaign guardrails | 4 |
| Part 1 §3.5 per-category reporting | 3 (Today) + 8 (History, Usage) |
| Part 2 §4.1 buyer types — trade & reseller | 6 |
| Part 2 §4.1 buyer types — collectors | 7 (opt-in page) |
| Part 2 §4.2 per-category radius rule | 6 |
| Part 2 §4.3 sources, tiers 1 & 2 | 6 |
| Part 2 §4.4 line-type filtering | 5 |
| Part 2 §4.5 pipeline, scoring, review queue | 5 |
| §5 cold-send guardrails | 7 |
| Part 3 UI — shell, Today, Contacts | 3 |
| Part 3 UI — Compose | 4 |
| Part 3 UI — Prospects | 5 |
| Part 3 UI — History, Categories, Opt-outs, Usage | 8 |
| Front-end pipeline fixes · commercials · white-label | 1 |

## Deliberately not scoped

Three things from the plan are **not** in any module. Each is a decision, not an oversight.

1. **Owner phone-append enrichment.** The plan (§4.4) calls this "the whole game" — turning
   a business main line into the owner's mobile. It needs a paid data vendor and a
   per-lookup budget, which is a commercial decision, not a build one. Pick a vendor and
   it becomes a small module. Until then module 6 gets as far as the officer's *name* via
   Sunbiz.
2. **Competitor marketplace scraping.** Flagged in the plan as likely violating those
   platforms' terms. Left out on purpose; if it happens it should be a manual occasional
   job, never the daily cron.
3. **Invoicing and payment collection.** Module 1 computes what he owes. Nothing sends him
   a bill or takes money — assumed to stay on your existing process.
