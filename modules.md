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

**Scope changed 19 Aug 2026: ship the text-marketing platform ASAP.** The prospecting
engine is deferred, not cancelled. Everything below is the shortest honest path to the
client sending real campaigns.

| # | Module | Status | Depends on | Touches files |
|---|--------|--------|------------|---------------|
| 1 | Foundation, pricing & white-label | Built | — | see below |
| 1b | Module 1 review fixes | Done | 1 | `app/routers/campaigns.py`, `app/templates/campaigns.html`, `app/services/billing_service.py`, `app/services/campaign_service.py`, `app/core/config.py`, `app/main.py`, `tests/**`, `README.md`, `deployment/deploy.sh` |
| 2 | Categories & segmented upload | Done | 1b | `app/models/category.py`, `app/models/__init__.py`, `app/services/category_service.py`, `app/services/contact_service.py`, `app/services/import_service.py`, `app/routers/categories.py`, `app/routers/contacts.py`, `app/sources/csv_source.py`, `alembic/versions/*`, `tests/test_categories.py`, `tests/test_import.py` |
| 3a | UI shell (base.html only) | Next | 2 | `app/templates/base.html`, `app/routers/pages.py` |
| 3b | Today + Contacts screens | Not started | 3a | `app/templates/today.html`, `app/templates/contacts.html`, `app/routers/dashboard.py`, `app/services/dashboard_service.py`, `tests/test_dashboard.py`, `tests/test_contacts_api.py` |
| 4 | Composer & campaign guardrails | Not started | 3a | `app/models/campaign.py`, `app/services/campaign_service.py`, `app/routers/campaigns.py`, `app/templates/campaigns.html`, `alembic/versions/*`, `tests/test_campaign_guardrails.py` |
| 5a | Deploy scaffolding | Done · gaps for 5b | 1b | `deployment/**`, `scripts/backup.sh`, `docs/CLIENT_GUIDE.md`, `README.md` |
| 5b | **Go live** | Not started | 3b, 4, 5a | `deployment/**`, `scripts/**`, `.env.example`, `docs/CLIENT_GUIDE.md`, `app/main.py` |

**That's the launch — six sessions, but only four waves. See "Parallel plan" below.**

### Deferred until after launch

Not cancelled — descoped so the client can start sending. The plan for each is still in
`A4A_BUILD_PLAN.md` §4 and the module details below.

- **Prospect engine & review queue** (was 5)
- **Discovery sources** — Google Places, DBPR, licences, Sunbiz (was 6)
- **Opt-in landing page & cold-send guardrails** (was 7)
- **Redesigned History / Categories-admin / Opt-outs / Usage screens** (was 8) — the
  skeleton's versions of all four already work; they just aren't on the new dark design.
  Functional beats pretty for launch.

Nothing in modules 1–5 forecloses any of it. `ContactSource` stays as the ingestion seam,
categories are a real table from module 2, and the prospect tables are additive.

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

### 5. Go live

**Purpose:** Get it onto a server, loaded with his real contacts, sending real messages.

**Scope:**
- **Server bring-up script** for a fresh Ubuntu droplet: non-root service user, Python
  3.12 venv, Node (for the Tailwind build), nginx, Certbot, systemd unit. The repo
  already has `deployment/nginx.conf.template` and `app.service.template` — finish them
  rather than inventing a new shape.
- **Production `.env`**: brand, billing terms, `PUBLIC_BASE_URL`, carrier credentials,
  sender number. Generated from a documented checklist, never copied from dev.
- **Webhook registration** with the carrier — delivery status and inbound STOP — pointed
  at the live domain. Verify both arrive, because inbound STOP handling is the one thing
  that must work on day one.
- `alembic upgrade head` against the production database, from empty.
- **Import his real CSVs**, one per category, using the module 2 flow.
- **Nightly off-box backup** of the SQLite file. The prior client's 181 MB database had
  no backup at all.
- Low-credit alert and a daily failure digest.
- `docs/CLIENT_GUIDE.md` — how he uses it, in his language, with no carrier name in it.
- A written rollback: how to stop a running campaign and how to restore yesterday's DB.

**The provider switch is a human step, not an agent step.** The agent prepares
everything with `SMS_PROVIDER=console`. A human flips it to the live carrier and sends
the first real message. This is in the escalation list and it stays there.

**Launch sequence** (in this order, no skipping):
1. Deploy with `SMS_PROVIDER=console`; click through every screen on the live domain.
2. Human sets the live provider. Send **one** message to your own phone. Confirm it
   arrives and the delivery webhook records it.
3. Send to **one category, capped at 50**. Confirm delivery rate and that nothing is
   billed that shouldn't be.
4. Reply STOP from a test handset. Confirm it lands in the blocklist and that a
   follow-up send skips that number.
5. Only then hand him the login.

**Acceptance:**
- App reachable over HTTPS with a valid certificate; every page returns 200
- `alembic upgrade head` applied to the production DB from empty
- His real contacts imported, per-category counts matching the source files
- A real message delivered to a real handset, with the delivery webhook recorded
- A STOP reply blocklists the number, and a subsequent send skips it — demonstrated
- Backup script runs and produces a restorable file off-box
- `grep -rni "telnyx\|twilio" docs/CLIENT_GUIDE.md` returns nothing

---

## Parallel plan

Sequential is the default. These pairs are genuinely safe — dependencies built, file sets
disjoint — and they turn six sessions into four waves.

### Wave 1 (as soon as 1b is green): **2 ‖ 5a**

| | |
|---|---|
| **2 — Categories & segmented upload** | `app/models/`, `app/services/`, `app/routers/categories.py`, `app/routers/contacts.py`, `app/sources/`, `alembic/versions/`, `tests/` |
| **5a — Deploy scaffolding** | `deployment/**`, `scripts/backup.sh`, `docs/CLIENT_GUIDE.md`, README deploy section |

Zero file overlap, and 5a depends on nothing but a working app. Front-loading the server
work means go-live day is "run the script, import, test" rather than "start building a
deployment." 5a stays out of `app/main.py` — scheduler and monitoring wiring waits for 5b.

### Wave 2: **3a alone**

`base.html` is the file every other template extends. Landing the shell on its own — one
small session — is what makes wave 3 safe. Running 3b and 4 against a shell that is still
moving is how you get two templates written against different versions of the same
layout and a merge nobody can review.

### Wave 3: **3b ‖ 4**

| | |
|---|---|
| **3b — Today + Contacts** | `app/templates/today.html`, `contacts.html`, `app/routers/pages.py`, `dashboard.py`, `app/services/dashboard_service.py` |
| **4 — Composer & guardrails** | `app/models/campaign.py`, `app/services/campaign_service.py`, `app/routers/campaigns.py`, `app/templates/campaigns.html`, `alembic/versions/` |

Disjoint, and both build on a settled shell. Both add an Alembic revision, so whichever
merges second rebases its migration — cheap, but do it deliberately rather than
discovering it.

### Wave 4: **5b — go live**

Needs everything. Not parallelisable, and the launch sequence inside it is strictly
ordered on purpose.

### What is *not* safe

- **2 with anything that touches `app/services/contact_service.py`** — 2 rewrites audience
  resolution and everything downstream reads it.
- **3b with 4 before 3a lands** — the semantic conflict on `base.html` doesn't show up as
  a git conflict, which is what makes it dangerous.
- **Any wave with 5b** — go-live reads the finished state of all of it.

### The honest caveat

Parallelism buys wall-clock, not effort. Two worktrees means two agents' tokens, two
reviews, and a merge step. On a six-session build the saving is roughly one session of
elapsed time. Worth it here only because launch speed is the goal — if it weren't, I'd
run the whole thing sequentially and spend the attention on review instead.
