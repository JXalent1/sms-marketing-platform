# Modules

_Last updated: 2026-09-08_

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
| 3a | UI shell (base.html only) | Done | 2 | `app/templates/base.html`, `app/routers/pages.py` |
| 3b | Today + Contacts screens | Done | 3a | `app/templates/today.html`, `app/templates/contacts.html`, `app/routers/dashboard.py`, `app/services/dashboard_service.py`, `tests/test_dashboard.py`, `tests/test_contacts_api.py` |
| 4 | Composer & campaign guardrails | Done | 3a | `app/models/campaign.py`, `app/services/campaign_service.py`, `app/routers/campaigns.py`, `app/templates/campaigns.html`, `alembic/versions/*`, `tests/test_campaign_guardrails.py` |
| 5a | Deploy scaffolding | Done · gaps for 5b | 1b | `deployment/**`, `scripts/backup.sh`, `docs/CLIENT_GUIDE.md`, `README.md` |
| 5b | **Go live** | Part A done · B3 verified · B4/B5 pending | 3b, 4, 5a | `deployment/**`, `scripts/**`, `.env.example`, `docs/CLIENT_GUIDE.md`, `app/main.py` |
| 5c | **Live-send blockers** | Part A done · deploy + B pending | 5b Part A | `requirements.txt`, `app/sms/factory.py`, `app/routers/settings.py`, `app/routers/pages.py`, `app/templates/settings.html`, `app/templates/base.html`, `deployment/nginx.conf.template`, `tests/`, `agent/accept-5c.sh` |
| 5d | **Refuse to send from a degraded box** | Part A done · deploy pending · B is Jordan's | 5c | `app/services/campaign_service.py`, `app/services/campaign_dispatch.py`, `app/services/preflight_service.py`, `app/services/blocklist_service.py`, `app/models/sms_message.py`, `app/sms/factory.py`, `app/sms/compliance.py`, `app/main.py`, `app/routers/campaigns.py`, `app/routers/pages.py`, `app/routers/blocklist.py`, `app/routers/webhooks/{common,telnyx,twilio}.py`, `app/templates/blocklist.html`, `.claude/hooks/verify-gate.sh`, `docs/API.md`, `tests/`, `agent/accept-5d.sh` |
| 5e | **Campaign-first flow & QoL** | Part A done 2026-08-27 · deploy pending | 5d | `app/routers/campaigns.py`, `app/routers/campaign_uploads.py`, `app/routers/contacts.py`, `app/routers/imports.py`, `app/routers/settings.py`, `app/templates/campaigns.html`, `app/templates/_composer-script.html`, `app/templates/_composer-upload.html`, `app/templates/contacts.html`, `app/templates/settings.html`, `app/services/campaign_service.py`, `app/services/campaign_builder.py`, `app/services/campaign_outcome.py`, `app/services/campaign_topup.py`, `app/services/suppression_service.py`, `app/services/preflight_service.py`, `app/services/import_service.py`, `app/services/contact_service.py`, `app/models/sms_message.py`, `alembic/versions/`, `tests/` |
| 5f | **Short links & reporting** | Part A done 2026-08-31 · deploy pending | 5e | `app/models/short_link.py`, `app/models/campaign.py`, `app/models/sms_message.py`, `app/routers/links.py`, `app/routers/reports.py`, `app/routers/campaigns.py`, `app/routers/campaign_uploads.py`, `app/routers/pages.py`, `app/services/link_service.py`, `app/services/click_classifier.py`, `app/main.py`, `deployment/{nginx.conf.template,bootstrap.sh,deploy.sh}`, `.env.example`, `app/services/report_service.py`, `app/services/history_service.py`, `app/services/cost_reconciliation.py`, `app/services/message_render.py`, `app/services/preflight_totals.py`, `app/services/campaign_builder.py`, `app/services/campaign_service.py`, `app/services/campaign_topup.py`, `app/services/preflight_service.py`, `app/sms/base.py`, `app/sms/providers/{telnyx,console}.py`, `app/core/config.py`, `app/templates/{history,campaign-report,contact-history,campaigns,contacts,base}.html`, `app/templates/_composer-{link,script,upload}.html`, `scripts/cost_report.py`, `alembic/versions/`, `tests/` |
| 5g | **Blocklist correctness** | Part A done 2026-08-26 · deploy pending | 5d | `app/sms/compliance.py`, `app/sms/phone.py`, `app/sms/providers/telnyx.py`, `app/routers/webhooks/telnyx.py`, `app/routers/webhooks/twilio.py`, `app/routers/webhooks/common.py`, `app/routers/pages.py`, `app/models/sms_message.py`, `app/models/blocked_number.py`, `app/services/blocklist_service.py`, `app/services/dashboard_service.py`, `app/services/monitoring_service.py`, `alembic/versions/`, `tests/` |
| 5h | **Held-back rows & the capacity floor** | Part A done 2026-08-30 · deploy pending | 5e | `app/models/sms_message.py`, `app/services/campaign_builder.py`, `app/services/campaign_service.py`, `app/services/campaign_release.py`, `app/services/campaign_topup.py`, `app/routers/campaign_uploads.py`, `app/templates/_composer-upload.html`, `alembic/versions/`, `tests/` |
| 5i | **Named lists replace categories** | Part A done 2026-09-04 · deploy pending | 5h, P1b | `app/models/{contact_list,sms_message}.py`, `app/services/{contact_service,dashboard_service,campaign_builder,campaign_service,import_service,report_service,history_service}.py`, `app/routers/{campaigns,contacts,dashboard,imports}.py`, `app/templates/{campaigns,contacts,today}.html`, `app/templates/_composer-{script,upload}.html`, `alembic/versions/`, `tests/`, `agent/{accept-5i.sh,mutate-5i.py}` |
| 5j | **Index migration, cost guard & list archive/rename** | **Part A done 2026-09-04** · accept + gate + mutation green · **deployed 2026-09-04** | 5i | `app/models/{contact_list,sms_message}.py`, `app/services/{contact_service,dashboard_service}.py`, `app/routers/contacts.py`, `app/services/list_admin.py` (new), `app/templates/{campaigns}.html`, `app/templates/_composer-{lists,script}.html`, `alembic/versions/`, `tests/`, `agent/{accept-5j.sh,mutate-5j.py}` |
| 5k | **Close the attribute-escaping class** | Specced · small, run before or beside P2 | 5j | `app/templates/{base,_composer-script,blocklist,contact-history,settings}.html`, `tests/test_attribute_escaping.py`, `agent/accept-5k.sh` |
| 5m | **Composer audience panel; time in Eastern** | **Specced · URGENT · ahead of everything** | 5j | `app/templates/{_composer-script,campaigns,today,history,campaign-report}.html`, `app/services/{campaign_dispatch,campaign_builder,suppression_service,dashboard_service}.py`, `app/routers/campaigns.py`, `app/core/config.py`, `alembic/versions/`, `tests/`, `agent/{accept-5m.sh,mutate-5m.py}` |
| P1 | **Prospect pipeline** | Done · deployed 2026-09-01 | 5f | `app/models/{prospect,scrape}.py`, `app/models/__init__.py`, `app/sms/lookup.py`, `app/sources/prospect_base.py`, `app/sources/__init__.py`, `app/services/{prospect_service,prospect_queue,prospect_scoring,lookup_service,scrape_runner,link_service}.py`, `app/routers/prospects.py`, `app/routers/pages.py`, `app/templates/{prospects,base}.html`, `app/core/config.py`, `app/main.py`, `alembic/versions/`, `tests/`, `agent/{accept-P1.sh,mutate-P1.py}` |
| P1b | **Lookup provider & gate flake** | Done · deployed 2026-09-01 | P1 | `app/sms/providers/telnyx_lookup.py`, `app/sms/lookup.py`, `app/services/lookup_service.py`, `app/core/config.py`, `.env.example`, `docs/API.md`, `CLAUDE.md`, `tests/{test_lookup_provider,test_wholesale_scan,_wholesale_scan}.py`, `tests/fixtures/number_lookup_responses.json`, `tests/{test_campaign_reports,test_whitelabel,test_campaign_preflight,test_capacity_rounding,test_degraded_send_path,test_prospect_review,test_prospect_pipeline}.py`, `agent/{accept-P1b.sh,mutate-P1b.py,accept-P1.sh}` |
| P2 | **Google Places source** | **Part A NOT complete** · accept-P2 criterion 9 fails (D2 survived) · deployed but inert without a key | P1b | `app/sources/{google_places,taxonomy,exclusions,__init__,prospect_base}.py`, `app/services/{prospect_ingest,prospect_service,api_budget,scrape_runner}.py`, `app/models/scrape.py`, `app/core/config.py`, `.env.example`, `alembic/versions/`, `agent/{accept-P2.sh,mutate-P2.py,mutate-{5e,5f,5g,5h,P1}.py}`, `tests/` |
| P2b | **Close D2; make the mutation harness reproducible** | Specced · next | P2 | `app/services/scrape_runner.py`, `app/sources/google_places.py`, `tests/`, `agent/{mutate-P2.py,accept-P2.sh}` |
| B1 | **Stripe: settle August, then auto-bill usage** | **Part A done 2026-09-07** · accept 1-11 + gate twice · 47/0 mutations, 34/0 after B1b retired the thirteen that moved · reviewed · **Part B is Jordan's, after B1b** | — | `app/services/{stripe_billing,stripe_meter,stripe_tiers}.py`, `app/routers/billing.py`, `app/templates/{subscribe,base}.html`, `app/services/{billing_service,campaign_dispatch}.py`, `app/routers/pages.py`, `app/core/config.py`, `app/main.py`, `tools/bill_period.py`, `requirements.txt`, `.env.example`, `tests/`, `agent/{accept-B1.sh,mutate-B1.py}` |
| B1b | **Meter once, late, and correctly** | **Part A done 2026-09-08** · accept 0-10 + gate twice + 35/0 mutations · reviewed · `decisions/012` open, not blocking · **Part B is Jordan's** | B1 | `app/models/sms_message.py`, `app/services/{stripe_meter,stripe_reconcile,campaign_dispatch,campaign_topup}.py`, `app/main.py`, `app/core/config.py`, `tools/bill_period.py`, `alembic/versions/`, `tests/`, `agent/{accept-B1b.sh,mutate-B1b.py,accept-B1.sh,mutate-B1.py}` |
| B1c | **Invoice the refused window as the plan's increment** | Specced pending · after Part B | B1b | `tools/bill_period.py`, `app/models/sms_message.py`, `app/services/stripe_reconcile.py`, `alembic/versions/`, `tests/` |
| P3 | **Registries, marketplaces & enrichment** | After P2 | P1 | `app/sources/dbpr.py`, `app/sources/sunbiz.py`, `tests/` |

**That's the launch — six sessions, but only four waves. See "Parallel plan" below.**

**B1's file list above is wider than the one this table carried before the
session, in three places and each for a stated reason.**
`stripe_meter.py` and `stripe_tiers.py` are both new, and both exist because of
the 500-line rule rather than because the spec asked for them. Each split runs
along a boundary the code already had: `stripe_billing` owns *this account's
link to Stripe* (checkout, the ownership guard, the webhook, the cycle anchor),
`stripe_meter` owns *what Stripe is told* (the meter, the backfill,
`period_usage()`), and `stripe_tiers` owns *whether Stripe is configured to
price it the way we are* (A2's drift check and its five states). Dependencies
run one way and nothing imports back. `app/templates/base.html` gains one nav
tuple and one icon arm, because `/subscribe` is the one screen in this product
the client has to reach on purpose and a page absent from the nav is a page he
does not have — `base.html`'s own comment documents that exact operation.
`tools/` did not exist before this session.

**Three of B1's spec clauses named Stripe fields the pinned SDK does not have**
— `subscription_data.add_invoice_items`, a tier's `unit_amount` for a sub-cent
rate, and `subscription.current_period_start`. All three were checked against
`stripe==15.6.1` before any code was written and all three now have a running
assertion in `tests/test_stripe_contract.py`. `decisions/010` records them and
asks for a ruling superseding two of the clauses; the third (A3) asked for the
verification and got it. Third consecutive session to hit the mechanism-clause
pattern after `decisions/007` and `008`.

**One escalation is open and it is not blocking:** `decisions/011` — a
campaign's metered figure is written once and can never be corrected, so we
over-bill a campaign with delivery failures (the webhook moves rows out of
`BILLABLE_STATUSES` after the meter has already fired) and under-bill every
top-up. Both are consequences of `identifier=campaign_<id>`, which is what makes
a retry free. Neither costs anything until Part B lands and the client
subscribes.

**The fresh-context review found twelve defects in a tree that was green twice
with 38 of 38 mutations caught.** Eight became mutations `R1`-`R8`. That is the
clearest evidence this project has for running a reviewer *and* a harness: the
harness proves a rule cannot be reverted, and only a reader notices a rule that
was written slightly wrong to begin with. The sharpest finding was two similar
arithmetics for one question with a docstring between them asserting they
agreed — they disagreed by a factor of three on any legacy row over 160
characters. B1's file list also gained `app/services/link_service.py`
(`RESERVED_SLUGS` must name `subscribe`, on the `prospects` precedent) and one
line each in `report_service.py` and `preflight_totals.py`, which had a session
in scope and were opening a second one per call.

5c was not in the original breakdown. It exists because flipping the provider to live
revealed that `requirements.txt` pinned a telnyx SDK major version the provider was not
written against, and `get_provider()`'s console fallback hid it behind a normal-looking
"Dry run" pill. The pin is the bug; the invisibility is the defect worth fixing.

Part A landed 2026-08-20: pin at 4.175.0 and verified against the real package, the
fallback recorded and rendered as a third send mode ("Sending unavailable"), ten tests
that fail against the pre-fix tree, and security headers in the nginx template. The two
things left are a deploy — the box still runs the hot-patched SDK and the pre-5c nginx
config, and the nginx half needs root — and Part B, which is Jordan's. Acceptance is
`agent/accept-5c.sh`; criteria 1-5 pass locally, criterion 6 is `--with-remote`.

5d finished what 5c started: 5c made a failed carrier *visible*, 5d made the product
*refuse to send* on one, and made the rows such a box writes non-billable. Part A landed
2026-08-26 — 184 tests (145 + 39), gate green twice, `agent/accept-5d.sh` as the stop
condition. The deploy is still pending, and Part B is Jordan's.

**5d's file list above is wider than the one this table carried before the session, and
deliberately so.** The spec's own requirements reach files it did not name: the composer
pre-flight row lives in `preflight_service.build_report()` and is passed in from
`routers/campaigns.py`; `/health` is in `routers/pages.py`, not `app/main.py`; the split
blocklist headline needs a grouped count in `blocklist_service` and `routers/blocklist.py`
rather than a client-side tally over a capped list; and the webhook auto-block needs the
provider name from `webhooks/telnyx.py` and `webhooks/twilio.py`.
`app/services/campaign_dispatch.py` is new because `campaign_service.py` crossed the
500-line rule. `billing_service.py` was in the list and was **not** touched — the
non-billable status is a change to `sms_message.py`'s status set, which is where
`BILLABLE_STATUSES` already lived, and the billing query needed no edit.

**P1's file list above is wider than the one this table carried before the session,
and deliberately so** — the same precedent 5d set. Where it grew and why:

- `app/models/scrape.py` is separate from `prospect.py` because the two halves of
  the module are two subjects (the holding pen and the job/cache record) and one
  file carrying both would have gone past 500 lines within the session.
- `app/sms/lookup.py` exists because the line-type provider talks to a carrier and
  must not import the DB layer, exactly as `app/sms/factory.py` must not. Only the
  *cache* is a database concern, and that is `lookup_service.py`.
- `prospect_queue.py` and `prospect_scoring.py` split the reads and the scoring off
  `prospect_service.py`, which is the writes and is already 450 lines.
- `app/routers/pages.py`, `app/main.py`, `app/models/__init__.py`,
  `app/sources/__init__.py` and `app/templates/base.html` are the mechanical wiring
  a new screen implies. `base.html` had a comment naming the exact edit that
  restores the Prospects nav entry; this is that edit.
- `app/core/config.py` carries the five new settings, because the plan of record
  says the per-category radii are config values and not code.
- `app/services/link_service.py` gained one word: `prospects` joins `RESERVED_SLUGS`.
  A new root page is the event that list exists for, and the same set is subtracted
  on both the minting and the serving side.

**Two tables beyond the three the session named.** `prospect_sightings` and
`prospect_rejections`. The alternative to each is an overloaded column — a JSON
list of corroborating terms, and a status somebody could tidy away — which is the
mistake `CLAUDE.md` opens with. Reasoning is in the migration's docstring.

**What was deliberately NOT built:** a carrier line-type provider. The interface and
the cache are here and the default provider makes no network call at all. Wiring a
paid API in is escalation item 7, and an unexercised provider class is the pinned-SDK
bet this project has already lost once. It lands with P2, when there is a search to
spend it on.

### Deferred until after launch

Not cancelled — descoped so the client can start sending. The plan for each is still in
`A4A_BUILD_PLAN.md` §4 and the module details below.

- **Prospect engine & review queue** (was 5)
- **Discovery sources** — Google Places, DBPR, licences, Sunbiz (was 6)
- **Opt-in landing page & cold-send guardrails** (was 7)
- **Redesigned Categories-admin / Opt-outs / Usage screens** (was 8) — the skeleton's
  versions of all three already work; they just aren't on the new dark design.
  Functional beats pretty for launch. **History is no longer among them:** 5f built
  campaign history, the per-campaign report and per-contact message history, because
  without them the client can send and cannot answer "did it work?".

#### Found in live use, not yet scheduled

- ~~**No way to add a single contact in the UI.**~~ **Closed by 5e A3, 2026-08-27.**
  The form is on the Contacts screen and the endpoint gained the two guards an import
  has: E.164 normalisation, and a blocklist check that refuses rather than silently
  skipping.
- **Quiet hours.** Nothing stops an 11pm blast but the operator's judgement.
- **Line-type screening at import.** A live campaign found 2,526 landlines in a 6,857
  list. 5d stops them recurring *after* a failed send; screening at import stops paying
  for the first one. Telnyx number lookup is ~$0.004/number.

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

---

## Requested 2026-08-24 — how the product actually gets used

Jordan's own words after the first two live campaigns. These reshape the product's
centre of gravity: the tool was built around a persistent contact database segmented
into five categories, and in practice he works campaign-by-campaign off a fresh list.

The engine already supports this. `contact_lists` / `contact_list_members` exist,
campaigns already accept `audience = "list:<id>"`, `contact_service` has a selector
grammar (`category:food_service&list:12`), and `import_service.commit()` already records
per-batch provenance so an upload can be undone. What is missing is the *flow*, not the
model — so this is UI and routing work, not a rebuild.

### Decisions taken (Jordan, 2026-08-24)

- **Categories become an optional tag on upload.** Audience is the list you just
  uploaded. Tagging stays available so cross-campaign rollups remain possible
  ("how do estate buyers perform vs memorabilia"), but is never required.
- **Short links get a short dedicated domain**, not a subdomain of the main site.
  `go.auctions4america.com/a7k` is 27 chars against `a4a.bz/a7k` at 10 — 11% of a
  segment, and enough to push a tight message to two segments. Precedent: `es.pn`,
  `swoo.sh`. Domain is a config value; registration does not block the build.
  Register to Auctions4America, not the agency, so WHOIS matches the 10DLC brand.
  Avoid `.link` / `.click` / `.xyz` / `.top` — carriers weight TLD reputation.
- **Top-up sends go out and count.** Contacts added to an already-sent campaign
  receive the same message and fold into that campaign's totals.

### Non-negotiable regardless of flow

- **Opt-outs are global and permanent.** A STOP suppresses that number on every
  future upload, forever. Legal, not preference.
- **The delivery-failure blocklist applies to every upload.** One live campaign left
  2,626 dead numbers; re-paying for them on every send is ~$24 a campaign.
- **The contacts table stays underneath uploads.** Per-person history is what makes
  "has this buyer ever been texted, did they ever click" answerable.

### 5e — Campaign-first flow & QoL

1. **Upload a list as step one of creating a campaign.** The list is named for the
   campaign, so a report on 8/25 reads "Italian restaurants" rather than a list id.
2. **Optional category tag** on that upload.
3. **Add a single contact from the UI.** `POST /api/contacts` exists; there is no form.
   The client will hit this the first time somebody phones in.
4. **Top-up send** — add contacts to an already-sent campaign, send them the same
   message, count them in that campaign.
5. **`RECENT_CONTACT_SUPPRESSION_DAYS` becomes a Settings field.** It is currently
   buried in `.env`. Shipped at 3 days, it silently withheld 6,856 of 6,857 recipients
   across two campaigns and required SQL to diagnose. Currently set to 0 in production
   at Jordan's instruction.
6. **The composer shows suppression before you queue**, not after: "X held back,
   clears at 10:11am".
7. **A campaign that sends zero aborts loudly** with the reason on the record. Two
   campaigns reported `completed` with `sent=0`.

**5e's file list above is wider than the one this table carried before the session, on
the same basis as 5d's and 5g's.** The spec's own requirements reach files it did not
name. `campaign_service.py` crossed the 500-line rule for the third time, so campaign
*creation* moved to `campaign_builder.py` — the seam is deciding what a campaign is
against running it, matching `campaign_dispatch.py` on the other side of *when a send
begins*. `preflight_service.py` then crossed it too and the hold-back window moved to
`suppression_service.py`, which it deserved on its own merits: it is the one rule that
decides whether a real person gets a text they did not ask for, and it is on the
escalation list by name. A5 says "surface it in Settings", which means
`app/routers/settings.py`. A4's top-up needs a record of what was added and when, which
is one additive nullable column (`sms_messages.top_up_at`) and therefore
`alembic/versions/` and `app/models/sms_message.py`. A1's upload endpoints are multipart
and `app/routers/campaigns.py` was at the line limit, so they are in
`campaign_uploads.py` under the same prefix. A2 reaches `app/routers/imports.py`, which
had to keep requiring a category while the service stopped doing so.
`app/services/contact_service.py` is the one file outside every reading of the spec:
`add_to_list()` left `added_at` to a server default that writes UTC while the rest of the
app writes local time, and A4's "added since" comparison cannot be correct with two
clocks in one column.

**Part A landed 2026-08-27.** 319 tests (259 + 60), gate green twice,
`agent/accept-5e.sh` as the stop condition, `agent/mutate-5e.py` as the check with teeth
(28 behavioural mutations, all caught). One synchronous fresh-context review found six
defects — two of them live ways for a top-up to text people nobody chose — five fixed
here and one escalated as `decisions/005`. The deploy is still pending, as it is for 5c,
5d and 5g.

### 5f — Short links & reporting

Part A landed 2026-08-31. 424 tests (371 + 53), gate green twice,
`agent/accept-5f.sh` as the stop condition and `agent/mutate-5f.py` (31 behavioural
mutations) as the check with teeth. The deploy — acceptance criterion 11 — is still
pending, as it is for 5c, 5d, 5e, 5g and 5h.

1. `short_links` table, redirect route, one hop only, closed to outside minting.
   **One link per recipient per campaign**, which is what makes "which buyers
   clicked" answerable — the only version of the number an auction house can act on.
2. Composer merge tag for a campaign's link, refused at *compose* time when
   `SHORT_LINK_DOMAIN` is unset, and counted on the rendered link rather than on the
   six characters of the tag.
3. Per-link click stats, with suspected scanners marked rather than discarded and
   both numbers on screen.
4. **Per-campaign report** — recipients, delivered, failed, held back, opt-outs,
   clicks, click-through and cost at *his* price, exportable as CSV.
5. **Campaign history and message history screens** (was module 8, deferred at
   launch), both paginated.
6. **What the carrier actually charged**, captured per message with its
   rate/carrier-fee split. Operator-only: `app/services/cost_reconciliation.py` and
   `scripts/cost_report.py`, never a route.

**5f's file list above is wider than the one this table carried before the session,
on the same basis as 5d's, 5e's, 5g's and 5h's.** The spec's own requirements reach
files it did not name. A3's "the segment counter must count the rendered link"
is a change to the renderer (`message_render.py`, split out of `campaign_service.py`
when the tag pushed it past 500 lines for the fourth time), to the pre-flight
endpoint that passes the renderer a link, and to the top-up, which mints for the
rows it creates. A4's cost capture reaches `app/sms/base.py` (the provider
contract discarded the fields), both providers, and `sms_messages`. A5's "cost at
his price" is `report_service`; A6's two screens are `history_service`,
`routers/pages.py`, `base.html`'s nav and three templates. `preflight_totals.py`
is new because `preflight_service.py` crossed the 500-line rule when the link
check landed, and `report_service.py` took `top_up_history()` off
`campaign_topup.py` for the same reason — it is a reporting query rather than part
of running a top-up.

**A host-based routing guard was added on 2026-08-31, after 5f landed.** 5f
shipped a second public hostname and both names answered every route, so
`bida4a.com/login` served the admin panel on the domain printed in every text
message. `short_link_host_guard` in `app/main.py` now serves only the redirect
route on `SHORT_LINK_DOMAIN` and 404s everything else before routing; the
redirect still resolves on the primary host so links already in people's phones
survive. An nginx path denylist was considered and rejected — it drifts the
moment a route is added, and the failure is silent on the host nobody looks at.
`deployment/nginx.conf.template` gained the short-link server block (previously
a hand edit on the box, which a `bootstrap.sh` run would have reverted) with the
domain as a placeholder, and `bootstrap.sh` takes `--short-domain` and
`--auction-site`. Acceptance is `agent/accept-5f.sh` criteria 12-14, marked in
that file as an addition rather than folded into 5f's spec.

**Where criterion 7 landed, and why it is not on the client's report.** The
criterion asks for "the campaign report reconciles estimate against actual". The
estimate is `WHOLESALE_COST_PER_SEGMENT`, which CLAUDE.md forbids reaching a
response body, a template or an export, and the admin login *is* the client — so
"operator-only" cannot mean "behind auth", it has to mean "not served". The
reconciliation is a service plus `scripts/cost_report.py` plus one INFO line per
finished campaign, and the client's report is denominated in
`BILLING_PRICE_PER_SEGMENT` as every other money figure is.

### 5g — Blocklist correctness

Part A landed 2026-08-26. Implements `decisions/003-auto-block-fragments-on-the-webhook-path.md`
in the order that decision rules: the transient guard first, then structured codes,
then the payload parse, then boundaries, then the misconfiguration signal, then the
opt-out reason. 259 tests (184 + 75), gate green twice, `agent/accept-5g.sh` as the
stop condition. The deploy — acceptance criterion 9 — is still pending, as it is for
5c and 5d.

1. **A transient failure never blocks.** `temporar`, `retry`, `congestion`,
   `try again` beat every other rule — a carrier calling a dead-number wording
   temporary is the one that knows.
1b. **`"unreachable"` left the fragment list** (`decisions/004`). It meant three
   things and two were transient, and the guard could not separate them: Twilio
   30003's own `ErrorMessage` is `Unreachable destination handset`, which carries no
   marker. Accepted residual, recorded at the fragment list: a line described *only*
   as unreachable survives and is paid for again — half a cent a blast against a
   bidder deleted permanently. Line-type screening at import is the real fix.
2. **Carrier codes match `sms_messages.error_code`, never prose.** `21610`, `21612`
   and `40300` left the text fragment list and were *not* `\b`-anchored back in.
3. **`error_message` is assembled from named fields.** The SDK's `__str__` of an API
   error is the response body dict-repr'd, and that column feeds
   `blocked_numbers.notes`, which the client reads.
4. **Word fragments are word-bounded.** Defence in depth, not the main event.
5. **A region-permission error blocks nobody** and raises an operator signal on
   `/health` — not over SMS, and not a subprocess per webhook event.
6. **`carrier_opt_out` is its own block reason**, counted in the opt-out figures.
   `OPT_OUT_REASONS` and the dashboard tile changed in the same commit, and the tile
   now imports the definition rather than repeating it.

**5g's file list above is narrower than the change set, on the same basis as 5d's.**
The spec's own text reaches five files the table did not name.
`app/routers/webhooks/twilio.py` posts `ErrorCode` as its own form field, so A2's
"match codes against a structured field" is only half-wired without it.
`app/sms/providers/telnyx.py` is where A3's dict repr is created — `str(e)` on the
SDK exception — and `app/sms/phone.py` is where `scrub_provider_text()` lives, which
A3 names explicitly; the payload strip is there as the backstop for every provider we
do not parse. A5 says "raise an operator-visible signal" and forbids texting it, which
means `app/services/monitoring_service.py` (the alert channel) and
`app/routers/pages.py` (`/health`, which CLAUDE.md already names as the channel that
survives a dead carrier). Nothing in 5e's file set was touched — including
`campaign_service.py`, which is why `should_auto_block()` kept its one-argument
signature.

### 5h — Held-back rows & the capacity floor

Part A landed 2026-08-30, decision 006's wording fix the same day. Two send-path
corrections from 5e's review. 371 tests (319 + 52), gate green twice, `agent/accept-5h.sh` as the stop condition and
`agent/mutate-5h.py` (27 mutations) as the check with teeth. The deploy —
acceptance criterion 9 — is still pending, as it is for 5c, 5d, 5e and 5g.

1. **`held_back` is its own message status**, outside `BILLABLE_STATUSES`, the same
   shape as 5d's `not_sent`. Implements `decisions/005` option 2 with all four
   riders: the name, no backfill, flip the row rather than write a second one, and
   re-run the window against today's value. `skipped` goes back to meaning only
   what the model's own docstring says it means.
2. **A top-up releases a hold that has cleared.** Contacts whose only row on the
   campaign is `held_back` are re-adjudicated and their existing rows flipped to
   `pending`. This half does **not** require a `list:` audience — see the file-list
   note below.
3. **The capacity guard compares exact `Decimal` money.** `wholesale_estimate()`
   rounded to cents before the comparison; the requirement is now derived from
   `wholesale_cost()`, unrounded, and rounding happens only for the Float column
   and the log line.

**5h's file list above is wider than the one this table carried before the session,
on the same basis as 5d's, 5e's and 5g's.** A1's third bullet — "a top-up includes
contacts whose only row for this campaign is `held_back`… and flips the existing
row" — is a change to the top-up, which lives in `campaign_topup.py`, not in any of
the four files the table named. The endpoint and the confirm dialog came with it
because both describe a top-up to the client in words that stopped being true
("everyone added to its list since it went out"), and a client-facing sentence that
is false is the defect 5d's `abort_reason` work exists to remove.
`campaign_release.py` is new because `campaign_topup.py` crossed the 500-line rule.
`app/models/campaign.py` and a second migration came from the review: a released
hold ignored the campaign's `batch_size` cap, and the cap could not be honoured
because it was applied at build time and never recorded.

**One question left open.** `decisions/006-which-campaigns-may-release-a-hold.open.md`
— whether a capped campaign, or one the window suppressed entirely (which ends
`aborted`, and a top-up refuses those), should be able to release its hold. Both
currently do nothing, which is what they did before 5h, so nothing is blocked.

**One thing the session spec asserts that this tree does not bear out.** A2 says a
small send "can require `$0.00` and pass the capacity check on an empty account". At
`WHOLESALE_COST_PER_SEGMENT=0.009` — the value in `.env`, `.env.example` and the code
default, and the one production inherits — a one-segment estimate rounds *up*, to
$0.01, so that send was already refused. The zero case needs a blended rate under
half a cent. What was wrong at 0.009 is that the requirement lands up to three
quarters of a cent under the true one wherever the estimate rounds down, so a
campaign could start on a balance that did not cover it. Both are the same defect
and the same fix; `agent/accept-5h.sh` check 6b prints the before/after at both
rates rather than asserting the spec's version of it.

### P1b — the lookup provider, and a gate that flakes

Part A landed 2026-08-31. 544 tests (494 + 50), gate green twice,
`agent/accept-P1b.sh` as the stop condition and `agent/mutate-P1b.py` (27
mutations, on a tree verified byte-identical to the repo first) as the check
with teeth. The deploy — acceptance criterion 10 — is still pending, as it is
for 5c, 5d, 5e, 5g, 5h, 5f and P1.

**The file list above differs from the one this table carried before the
session, in one place that matters.** It named
`app/services/lookup_providers/telnyx.py`; the class shipped as
`app/sms/providers/telnyx_lookup.py` instead. The registry it has to be
registered in is `PROVIDERS` in `app/sms/lookup.py`, and `app/sms/` may not
import from `app.services` — that boundary is the one the gate's check 6
enforces and the reason the SMS engine survived being moved between clients. A
provider that only talks to a carrier is `app/sms/` work by that rule; the parts
that need the database (the cache, the monthly spend cap, the "never spend on a
number nobody will use" guard) stayed in `app/services/lookup_service.py`, which
is where they were already.

`agent/accept-P1.sh` is in the list because P1b **is** the ruling on RULES.md
escalation item 7, and P1's check 8d asserted that no carrier lookup provider
was registered. That assertion was right for P1 and wrong the moment this
session shipped. Struck through in place with the old text quoted and the
superseding session named, per RULES.md's rule about specs a later decision
overtakes; what it was really protecting — that reaching the carrier takes a
deliberate `.env` edit on a live box — is still asserted, on the default.

**`PROSPECT_LOOKUP_MONTHLY_CAP` is new config, defaulting to $50.** Neither
`.env` nor `.env.production` was touched, so production inherits the default and
screening stays off until `PROSPECT_LOOKUP_PROVIDER=telnyx` is set by hand.
That edit spends money on every scrape and is escalation item 6/7's territory,
not an agent's.

### Still to brainstorm

**Data streams for A4A** — what recurring sources of *buyers* exist. Deferred with the
prospecting engine; now the next design conversation, not a build item yet.

---

## Requested 2026-09-04 — retire the category model for named lists

Jordan's dad prefers how the Williamson build (wagmarketingbot.com) handles lists: **one
flat dropdown of past lists, each titled by the campaign that first used it**, newest
first, with `⭐ ALL BIDDERS — MAIN LIST` pinned at the top. No industry sections. You either
upload a fresh list or pick one you used before.

This retires the five-category model — Food Service, Equipment & Machinery, Estates,
Memorabilia, General Merchandise — that the entire build originally existed to serve.

**The trigger was real.** His dad asked where to put the list for a yacht auction, and the
honest answer was that none of the five categories fit. A sixth would have had no colour
left: the palette is validated at four hues plus neutral and is already full.

### Most of this is already done

Session 5e made the category optional and made upload-per-campaign the primary flow, and
`contact_lists` / `audience = "list:<id>"` have existed since the skeleton. What remains is
largely presentation:

- Remove the category picker from the audience selector
- Present a flat recency-sorted list of named lists, ALL BIDDERS pinned
- Decide the fate of the Contacts page category tabs and the five dashboard category cards

### Decided 2026-09-04 (Jordan)

**Hidden but retained.** Every category surface comes off the client's screens; nothing
comes out of the schema. `categories`, `contact_categories`, `contact_lists.category_id`,
`campaigns.category_id`, `campaigns.cross_category_override` and the `--s1`..`--s4`
palette variables all stay.

Three reasons it is retention and not a compromise:

1. **Prospecting is keyed on categories.** `prospect_scoring.py`, `prospect_service.py`,
   `prospect_base.py` and the five per-category radius settings in `core/config.py` are
   P2's taxonomy. Removal is not a UI change — it is a redesign of a specced module.
2. **Historical campaigns store `category:<slug>` selectors**, and `audience_label()`
   renders them in campaign history and every per-campaign report. Deleting the
   resolution path breaks every report older than this session.
3. It keeps the cross-campaign reporting axis open at the cost of a comment.

**The dashboard's five category cards become recent-list cards** — the pinned
`⭐ ALL BIDDERS — MAIN LIST` first, then the five most recent lists, each showing its
contact count and days since it was last actually texted. The em-dash rule for a list
never texted is carried over verbatim.

**The prospect review queue keeps its category** and still requires one to promote. That
is the taxonomy, not the list model, and it is stated in the spec so a later session does
not tidy it away.

### The defect this surfaced

`contact_lists.created_at` has two writers keeping two clocks — the same shape as
`contact_list_members.added_at` in `CLAUDE.md`. `contact_service.py:90` omits the column
and gets SQLite's `CURRENT_TIMESTAMP` (**UTC**, space-separated); `import_service.py:247`
writes `datetime.now().isoformat()` (**local**, `T`-separated). Both spellings are in the
live database. Nothing compared these rows until now; 5i makes recency the sort order of
the picker, so the error becomes visible and is one-directional — every server-defaulted
row reads up to five hours newer than it is.

Fixed in 5i A1: one writer, a normalising migration classified by spelling (the two
writers are 1:1 with the two formats), and ordering by a parsed value rather than a
string.

### The session

`sessions/session-5i.md`, written 2026-09-04. **Part A ran and passed the same day** —
`agent/accept-5i.sh` exits 0, the gate is green twice, and `agent/mutate-5i.py` reports
16 mutations caught on a tree it verified pristine. 577 tests. Its file set is disjoint
from P2's, so the two could still run in parallel.

Four files outside the spec's list were edited, each forced by a requirement inside it
and each recorded in `status.md`: `app/routers/imports.py` (A5's import cannot take a
list name or commit untagged without it), `app/services/campaign_service.py` (the
`resolve_category` wrapper would otherwise state a second, older version of the rule),
`app/services/import_service.py` (a docstring naming callers that no longer exist) and
the Python-side default on `contact_list.created_at` described below.

**Three things the spec did not know, and one it could not have.**

1. **The table's DDL still carries `DEFAULT (CURRENT_TIMESTAMP)`.** Removing
   `server_default` from the *model* does not remove the old writer — an insert that
   omits the column still gets a UTC value from SQLite. Dropping a column default there
   means rebuilding the table, which is escalation item 8, so the model carries a
   Python-side `default=now_iso` instead and `parse_created_at()` still understands the
   space-separated spelling for the raw-`INSERT` path that can still reach it.
2. **A7 as written cannot hold** — ruled on in `decisions/007` and struck through in the
   spec in place — because other clauses of the same spec retain six
   surfaces that name a category — the prospect queue (A8), the taxonomy CRUD it reads,
   the contacts payload and export (`contact_query_service`, out of the file list), and
   the two report screens (the file list puts `report_service`/`history_service` in
   scope "for one reason only"). The sweep subtracts them with the retaining clause
   written beside each, and a companion test fails when an exemption stops being needed.
3. **"Point the freshness query at `BILLABLE_STATUSES`" is invisible to an identity
   assertion** — the two literals are equal and CPython folds them to one object. The
   test asserts the *binding* instead: change what "texted" means and the freshness
   figure must follow.
4. **Relaxing the category rule turned a malformed `list:` selector into a 500.** It
   used to be refused by that rule; with the rule gone it reaches the resolver, whose
   `ValueError` the router does not map. Now mapped to a 400 carrying the selector
   grammar's own sentence.

### 5j — the index migration, the archive/rename controls

**Ran 2026-09-04. `agent/accept-5j.sh` exits 0 with every criterion printed, the gate
is green twice at 613 tests, and `agent/mutate-5j.py` reports 19 mutations caught / 0
survived on a scratch tree verified pristine. Detail in `status.md`.**

Two things forced it into existence on deploy day, and they belong together because both
are about the list picker being the product now rather than a corner of it.

**1. Production's schema leads the migration history by two indexes.** The 5i freshness
join took 10m02s on production and 0.95s after `ix_sms_messages_contact_id` and
`ix_clm_contact_id` were created by hand — see the incident entry at the end of
`status.md`. Those indexes exist on the live box and in no migration. 5j carries them,
**written to tolerate their already existing**: a bare `op.create_index()` raises on the
live database and `deploy.sh` aborts the deploy on a failed migration, which would mean
the fix for the outage is the thing that cannot ship.

It also needs the regression guard that would have caught this: an `EXPLAIN QUERY PLAN`
assertion that the freshness join is index-backed, so an unindexed join fails the suite
rather than the client. The gate has no timing check and the mutation harness is silent
about cost — this is the first check in the project that is about what a query costs.

**2. Nothing in the product can rename or hide a list.** Every test upload becomes a
permanent dropdown entry. That was invisible while categories were the primary axis and
is now the picker itself — ten pieces of debris were cleared by hand on 2026-09-04 and
there is no way for the client to do the same. `DELETE /api/lists/{id}` exists, has no
caller, and hard-deletes, which is the wrong tool: deleting a list a campaign referenced
degrades that campaign's report label to the raw `list:20`. The right shape is an
`archived` flag with the same ruling categories got — **hidden from the picker, still
resolving for history** — plus a rename, both reachable from where he picks an audience.

Nineteen of the twenty-one lists were never used by a campaign, so there is no campaign
name for most of them to inherit. The client has to name them himself; this is the tool
that lets him.

**What the session found that the spec did not know**

1. **The cost assertion the spec named is wrong in both directions.** "No full scan of
   `sms_messages` or `contact_list_members`" is *green* on the 10-minute plan — SQLite
   searches `sms_messages` through `idx_sms_status`, and `status` has four distinct
   values, so an index lookup walks a quarter of the table — and *red* on the plan that
   ended the outage, which scans `contact_list_members` outright. The guard asserts on
   the **join key** instead: the plan reaches a table through an index keyed on
   `contact_id`, and *both* sides carry one whose leading column is `contact_id`. Both
   sides, because which one SQLite searches flips between suite scale and production
   scale. Criterion 8 prints the spec's rule evaluated on the outage plan so the reason
   is on the transcript. **This departs from a clause of the spec and wants a ruling.**
2. **`campaigns.audience_label` is a stored snapshot, so a rename does not "land
   everywhere" on its own.** `_term_label()` does look the name up live, but the rail,
   history and the report all read the column `campaign_builder` wrote once at creation.
   The rename treats it as a cache of `audience_label()` and recomputes it for every
   campaign whose selector names the list.
3. **`esc()` was being used in attribute position**, and A5's rename box is what makes a
   list label a string the client types. A local `attr()` helper now escapes the quote,
   with a shape test over the composer templates.
4. **`app/services/list_admin.py` is new and outside the file list.**
   `contact_service.py` was at 482 lines and the 500-line rule is hard, so the
   administration verbs split out along the natural seam.

### 5j — Part A done 2026-09-04

`agent/accept-5j.sh` exits 0 on all ten criteria, gate green twice at **613 tests**,
19 mutations caught / 0 survived on a verified-pristine tree, migrations exercised fresh /
production-shape / down-and-up, and the rename, collision, 409 refusal, archive and
unarchive driven by hand against a running box with the state restored afterwards.
`_last_sent_by_list()` at production row counts: **0.019s**, from 10m02s. The whole Today
screen is 0.049s.

**Deployed 2026-09-04.** Both revisions applied on the live box and Part B confirms the
index migration **converged rather than accumulated**: `sms_messages` carries
`idx_sms_contact` and no `ix_sms_messages_contact_id`; `contact_list_members` carries
`idx_member_contact` and no `ix_clm_contact_id`. The composer, the picker and the new
Manage lists panel verified by hand against production — panel opens, rows carry an
editable name, contact count, freshness, Save name and Archive; no console errors.

**One spec clause was superseded — `decisions/008`.** A2 told the guard to assert "no full
scan of `sms_messages` or `contact_list_members`". Measured before it was written, that is
wrong in both directions: the outage plan reaches `sms_messages` through `idx_sms_status`
and has no `SCAN sms_messages` line, so the first half is green on the ten-minute plan;
and the repaired plan still scans `contact_list_members`, so the second half is red on the
schema that fixed production. The guard asserts on the join key instead, plus a schema
assertion that both sides carry an index leading on `contact_id`. The rejected rule is
kept as an executable failing example. Struck through in the spec in place.

That is **two superseded clauses in two sessions, both specifying a mechanism where the
spec should have stated a property** — 007 and 008. Folded into `CLAUDE.md`.

**Two things the spec did not know.** `campaigns.audience_label` is a stored render, so a
rename would have left the rail, history and reports quoting the old name;
`list_admin.rename()` recomputes it from `audience_label()` rather than by substituting
into the old string, because a campaign's label may name a second term. And `esc()` does
not escape the quote that ends an attribute — see residual 1.

**Two departures from the file list**, both recorded: `app/services/list_admin.py` is new
(`contact_service.py` was at 482 lines and the 500-line rule is hard; it is now 494), and
`sms_messages` gains an index, which escalation item 8 names. The index was correct to
proceed on — A1 names that index, that table and the reason explicitly, which is what
makes it authorized rather than assumed, it is additive, and criterion 3 exercises the
downgrade.

### Residuals, in the order they should be picked up

Detail for each is in `status.md` under "Found while working".

0. **`esc()` in attribute position survives in three templates outside the composer, and
   one of them is fed by the carrier.** 5j added `attr()` and a shape test, both scoped to
   the composer templates, because the others are outside its file list. The remaining
   sites are `blocklist.html:116` (`title="${esc(n.notes)}"`), `contact-history.html:86`
   (`title="${esc(m.message)}"`) and `settings.html:145` (`data-mode="${esc(d.send_mode)}"`).
   **`blocked_numbers.notes` is carrier free text written by the delivery webhook at
   thousands of rows a campaign** — input from outside the system, and the reason this is
   residual zero rather than residual seven. The fix is small: move `attr()` from
   `_composer-script.html` to `base.html` beside `esc()`, use it at the three sites, and
   widen `test_composer_markup.py`'s sweep from the composer list to every template.
   `settings.html` renders an internal enum and is the least of the three; the other two
   are not. **Specced as 5k — `sessions/session-5k.md`, 107 lines.**

1. **`/api/contacts/export.csv` still carries a `categories` column** — the one remaining
   surface the client actually sees, because he opens the file. `contact_query_service.py`
   was outside 5i's list and the sweep exempts it for that reason. First item of whichever
   session next touches that file; it should not wait for module 8.
2. **`app/services/campaign_service.py` is at exactly 500 lines.** The next addition to it
   forces a split before anything else can land there. Its own docstring describes the seam
   used last time. Untouched by 5j.
2b. **A CSV uploaded under the name of an archived list writes into that list, and it
   stays hidden from the picker.** `get_or_create_list()` matches on name and knows nothing
   about `archived`. The send works and A5's panel offers Unarchive on the same screen, so
   there is a remedy — but "does writing into a list un-archive it?" is a behaviour
   decision, not an oversight, and 5j left it alone. One line in `contact_service` when it
   is ruled on.
3. **`docs/API.md` describes none of 5i** — `/api/campaigns` lost four fields,
   `/api/dashboard` renamed `categories` to `lists` and `next_up.category` to
   `next_up.list`, `/api/campaigns/audiences` and `/api/lists` changed shape, and
   `/api/imports/commit` gained `list_name` and stopped requiring `category_id`.
4. **`pages.PAGE_CONTEXT["contacts"]` still computes `category_tabs`** on every Contacts
   page load, and after A5 nothing renders them.
5. **`import_service.require_category()` has no caller** — a correct function stating a
   rule nothing enforces any more.
6. **`test_whitelabel.py:104` reads `"id"` off a `/api/lists` entry**, which those entries
   have never had, so `PATH_VALUES["list_id"]` has always been the `999999` fallback.
   Pre-existing; harmless until a GET route takes a `{list_id}`.
7. **A per-contact "which lists is this person on" column** on Contacts, explicitly out of
   5i's scope and still the right product answer.

---

### P2's file list above is wider than the one this table carried before the
session, and each departure is recorded in `status.md` with the requirement that
forced it — the precedent 5d and P1 set. The four that matter:

- **`app/sources/exclusions.py`** holds the never-prospect list on its own,
  because it is enforced from `prospect_ingest` and must not import a module
  that reaches back into `app.services` — which `taxonomy.py` does, deliberately,
  for the radius fallback.
- **`app/services/prospect_ingest.py`** is the 500-line rule. The exclusion check
  and the already-a-contact check took `prospect_service.py` past it, which P1's
  own status note predicted. Ingestion moved; what a reviewer does to a prospect
  stayed.
- **`app/services/api_budget.py`** is the Google request meter. A source may not
  query the database and the ceiling has to be checked inside the paging loop, so
  the runner reads the meter and hands it in — `LookupBudget`'s seam exactly.
- **`alembic/versions/e7c05b3a1d94`** adds five columns to `scrape_jobs`:
  `records_excluded`, `records_known`, `api_requests`, `api_requests_skipped`,
  `api_cost`. Additive, nullable, backfilled, no index, no server default.
  Two meters and two kinds of nothing, kept apart on purpose.

**The spec's A4 names `agent/mutate-1.py` and `agent/mutate-5d.py`, which do not
exist.** The harnesses that lacked the pristine check were `5e`, `5f`, `5g`, `5h`
and `P1`. All five have it; `accept-P2.sh` check 8 now asserts the property over
every `agent/mutate-*.py` rather than over a list.

## Prospecting, decided 2026-09-04

**`decisions/009` reverses the seashell exclusion.** Shell wholesalers, importers and
distributors are **buyers, and the priority group** — not sellers. The plan of record had
inferred "seller" from the word "wholesaler" without asking the client; a wholesaler is a
merchant who buys inventory at margin, so a discounted lot is exactly what they bid on. As
specced, P2 would have found Atlantic Coral Enterprise and discarded it.

The niche is **three industries sharing a material**, and they do not share a radius:

1. Wholesalers, importers, distributors — **priority**, national
2. Businesses that use shells as material (decor and furniture makers, mosaic and surface
   fabricators, craft manufacturers, sign and wall installers) — national
3. Shell aggregate and landscape supply (crushed shell by the cubic yard) — **regional,
   150 miles at most**; freight dominates the price, so "can they collect it" binds harder
   here than for a walk-in cooler

Retail shell and beach shops are demoted to a low-priority fourth group.

**Two consequences for P2, both written into the spec:**

- **Radius becomes a property of the search-term group**, with the category value as
  fallback. One category cannot carry two national groups and one regional one.
- **`PROSPECT_CATEGORY_RADIUS_MILES` has no `marine` and no seashell entry**, so both fall
  back to the deliberately-conservative `PROSPECT_DEFAULT_RADIUS_MILES = 150`. The two
  groups that most need to be national would quietly run as 150-mile searches.

### Volume, stated plainly

Jordan's target is 2,000–3,000 new textable numbers as a test. **Seashells cannot supply
that and should not be asked to.** The priority group is 50–200 businesses nationally; the
whole seashell niche is 400–900 mobiles at a storefront-retail mobile rate.

**Memorabilia carries the volume, and it is already national:** 9,755 pawn shops and 3,256
sports card stores in the US, plus coin and comic dealers. Marine is national too and its
radius entry is missing. The three national groups together clear 15,000 businesses, which
at the plan's 25–35% mobile rate is 3,700–5,200 numbers — the 2–3k test comes out of that
on the first run.

Regional groups — food service, equipment, estates, general — stay regional. Those lots are
collected in person, so a buyer 1,000 miles away never bids and costs a segment on every
send forever.

**Cost of a 15,000-business run:** roughly $37.50 of line-type screening, and about nothing
for Places in the first month (first 1,000 requests free). That fits under
`PROSPECT_LOOKUP_MONTHLY_CAP = 50.0` but only just — one re-run trips it, and the guard
refuses cleanly rather than overspending.

**Blocker, Jordan's:** a Google Places API key with **Enterprise tier**. The phone number
only comes back at that tier. P2 can be *built* without it — the spec forbids real API
calls in the session — so the key gates the first live run, not the work.

### P2 — Part A NOT complete, 2026-09-04

**`agent/accept-P2.sh --with-remote` FAILS on criterion 9.** Mutation **D2** — "the search
ledger is not consulted, so every nightly re-run pays $0.035 a query to be told the same
sixty businesses" — reverts with **no test noticing**. The ledger is the guard that stops a
nightly re-run re-paying for every search, and nothing stands behind it.

**The harness gave two different answers to the same question.** The in-session run reported
36 caught / 0 survived on a tree it printed `SCRATCH VERIFIED PRISTINE`; the verification
run reported 36 mutations, 1 survived. Same mutations, same tree, opposite verdict. Until
that is explained, **no verdict from this harness is evidence** — `CLAUDE.md` already
records that this suite has no isolation, is green exactly once, and that a mutation caught
by a test which does not name it was never caught. A catch that comes and goes between runs
is the same defect wearing a green tick.

**Why criterion 2 did not cover it.** Criterion 2 asserts a second run produces 0 new
prospects and 0 paid calls, and it passes — but *two meters, two bills*. The prospect-level
dedup stops the **$0.0025** lookups. The ledger is what stops the **$0.035** requests, and
D2 says no test reaches it.

**Exposure today: none.** The code is deployed but `google_places.py` cannot run without
`GOOGLE_PLACES_API_KEY`, which is not set. The untested guard costs nothing until the first
live run — and must be closed before it.

Criterion 10 has not run: the invocation was given a placeholder password and got a 401.

Gate is green twice at 696 tests and migration `e7c05b3a1d94` is clean up, down and up
again; those parts stand.

`app/sources/taxonomy.py` — term groups across seven categories, each with the written
`buyer_rationale` the review queue renders. `app/sources/exclusions.py` — one shared
definition, enforced in `record_prospect()` so a future source inherits it rather than
remembering it.

**`decisions/009` shipped as written and is guarded in the direction the plan was wrong:**
a test running `excluded_reason()` over the five real companies the decision names, a
structural test that no exclusion phrase contains `wholesal`/`importer`/`distribut`, and
mutation X3 which re-adds the exclusion and takes ten tests red. Radius is a property of
the group with an `INHERIT` sentinel distinct from `None` (national), and
`test_no_single_value_satisfies_all_three_seashell_groups` is the property behind criterion
6b. `marine` and the seashell groups were added to the config map and `.env.example`.

**Two meters, because they are two bills.** The carrier's is P1b's, called through.
Google's is new and needed its own dedup: a request is charged for *asking*, so the cache,
the rejection list and the new already-a-contact check each save $0.0025 and not one cent
of $0.035. `run_plan()` runs one job per search with `scrape_jobs.search_term` as the
ledger.

**Three defects the session found in its own code**, all with tests verified red against
the pre-fix tree: `skipped_cap` double-subtracted the already-searched (right on a first
run, wrong on every run after); the "was it interrupted" clause had no time bound, so one
bad night kept a search out of the ledger forever and re-paid nightly; and a `cap <= 0`
guard copied from `LookupBudget` decided nothing over an integer counter. Folded into
`CLAUDE.md`.

**A4 — the spec named two harnesses that do not exist.** `agent/mutate-1.py` and
`agent/mutate-5d.py` were never written; the file list in this table carried the wrong
names and the kick-off repeated them. The five actually lacking the pristine check were
5e, 5f, 5g, 5h and P1, all five now have it, and check 8 asserts the property over **every**
`agent/mutate-*.py` by handing all nine a dirty tree — which is the durable form, since it
cannot name a file that is not there.

### Residuals added by P2

8. **`agent/mutate-5e.py` and `agent/mutate-5h.py` have bit-rotted** against 5i and 5j —
   five and one patch no longer apply, because those sessions moved the code the patches
   name. They now say so rather than reading as a pass, which is the important half. Until
   repaired, neither session has live mutation coverage; repairing them or retiring them
   deliberately is a decision somebody should take rather than inherit.
9. **`_screen()`'s `status == "pending"` filter has a comment that is no longer true**
   (mutation R12b, the one survivor on the repaired `mutate-P1`). Its justification moved
   into `line_type_for()` in P1b. The filter still avoids a query and a rescore loop — a
   real reason and a different one — and the comment should say that. P1's scope.

### B1 — Part A done 2026-09-07, and why Part B waits

`agent/accept-B1.sh` ACCEPT PASS, gate green twice at **764 tests**, 47 mutations 0
survived on two consecutive runs. The back-bill dry run prices August at **$270.03** —
28,002 segments, 18,002 billable — from `billing_service`'s own functions.

The review found twelve defects in a tree that was green twice with 38 of 38 mutations
caught, and the sharpest is worth carrying: `stripe_meter` priced a legacy row as 1 segment
while `/usage` priced it by length — 480 characters metered as 1, rendered as 3 — **with a
docstring between them asserting they agreed.**

**`decisions/011` blocks Part B.** Three measured billing defects: an over-bill on every
campaign with delivery failures, an under-bill on every top-up, and — the largest, from a
clause in B1's own spec — a **double-bill on any backfill older than 24 hours**, because
Stripe enforces meter-identifier uniqueness only over a rolling day. `sessions/session-B1b.md`
fixes all three by marking rows in our own database rather than trusting that identifier,
metering on a schedule once receipts settle, and stamping each event with the send time
(Stripe backdates up to 35 days) so a late pass still bills the right cycle.

None of it is exposed today: nothing meters until a customer id exists and Part B has not
run. **Do not do the Stripe dashboard setup and drop the keys in until B1b is green.**

`decisions/010` is also resolved — three spec clauses named SDK fields that do not exist.
That plus 007, 008 and 011 is four consecutive sessions departing from a mechanism I wrote
from memory, and `RULES.md` now carries the rule that follows: a spec clause naming a
third-party field is a hypothesis until the session verifies it.

### B1b — Part A done 2026-09-08: the meter is a scheduled, ledgered pass

`agent/accept-B1b.sh` ACCEPT PASS with every criterion printed, gate green twice
at **788 tests**, 35 mutations 0 survived on two consecutive invocations, each on
a tree verified byte-identical to the repo. `decisions/011` implemented as
decided: Option 2 with its three amendments. The review found two money-moving
defects in a tree at 26/0 — the manual-invoice remedy double-billed a
partly-metered window (now refused; the design is `decisions/012`, open), and
a database lock during the mark could turn into a double bill after a status
flip (the batch is now staged before the call). Eight mutations carry them.

**What changed, in one paragraph.** `sms_messages.metered_at` is the ledger.
An hourly pass selects rows that are billable, *settled* (`delivered`, or `sent`
for more than `BILLING_SETTLE_HOURS`, default 24), unmarked and sent on or after
the stored subscription start; posts one meter event per campaign per calendar
day, stamped with the send time; and marks exactly those rows in the same
transaction. Nothing on the send path meters — the Send button, the scheduler
and the top-up all just write unmarked rows. Usage older than Stripe's 35-day
horizon is refused loudly and goes to `tools/bill_period.py`, which gained
`--unmetered` to say which rows in a window are unmetered and why. The backfill
*is* the pass, run by a human, dry run by default.

**Both third-party clauses the spec marked unverified held** — the 24-hour
identifier window and the 35-day timestamp horizon, read from the pinned SDK's
own docstrings and now asserted on every run. First time in five sessions.

**File list widened, each edit recorded in `status.md`:**
`app/services/stripe_reconcile.py` is new (the 500-line split: `stripe_meter`
reports, `stripe_reconcile` reads back); `agent/mutate-B1.py` and
`agent/accept-B1.sh` were repaired because thirteen of B1's mutations sat on
lines this session removed or inverted, and a harness that exits 2 forever is
the bit-rot CLAUDE.md already lists for 5e and 5h. B1's harness runs 34/0 on
the new tree.

**Part B is unchanged and now unblocked.** Do the Stripe dashboard setup from
B1's handoff list, then run `accept-B1.sh --with-stripe` with the test key.
Two things Part B should look at that the SDK could not settle offline: what
Stripe does with an event timestamped before the subscription's first period
(rows sent on the anchor day before the checkout moment), and whether a
backdated event arriving after an invoice is finalised lands on that invoice
or the next.

### B1b — Part A done 2026-09-08. Part B is unblocked, with one added step.

`agent/accept-B1b.sh` ACCEPT PASS, gate green twice at **788 tests**, 35 mutations 0
survived identically on two runs, and B1's repaired harness green twice at 34/0. Review
lens 5 confirmed criterion 1 goes red on the pre-fix tree for the right reason — 12,000
metered against 8,000 used.

`decisions/011` implemented as decided. The meter is out of the send path; an hourly pass
meters settled, unmarked rows, one event per campaign per calendar day stamped with the
send time, marking `metered_at` on exactly those rows. A top-up needs no hook — it is a
later batch.

**Two money defects the review caught, both fixed:** the remedy B1b's own A3 named
double-billed (a part-metered window priced through `compute_usage()` applies the allowance
twice — measured $210.00 against a correct $180.00), and a database lock during the mark
could bill survivors twice on retry, now closed by staging the batch in `app_settings`
before the Stripe call.

**`decisions/012` — resolved, Option 1, and not blocking.** The refused-window invoice is
priced as the plan's increment, `cost_for_segments(metered + refused) - cost_for_segments(metered)`,
and stamped `invoiced_at` so the refusal stops. Specced as **B1c**, after Part B: it needs
35 days of the pass not running before it matters.

**Part B gains a mandatory dashboard step**, from the same investigation. Stripe's default
invoice finalization grace period is **1 hour**; `BILLING_SETTLE_HOURS` is 24 and the pass
runs hourly, so a campaign sent in the last day of a cycle is metered after that invoice
finalises — past the grace period for that invoice, and timestamped inside a closed period
so it never reaches the next one. **It is never billed at all.** Set a 72-hour finalization
delay on a rule conditioned on *Has a metered price* + *Invoice is from a subscription
cycle*. Step 5 of `sessions/session-B1.md` Part B.

**And a standing billing rule:** never change the metered price mid-cycle — Stripe drops
grace-period usage from the current *and* subsequent invoices when a subscription item's
price changes during a cycle. Rate changes wait for a boundary.

## Found in production 2026-09-08 — two live defects, `sessions/session-5m.md`

Both reported by the client's own operator while building a campaign. **5m runs ahead of
P2b, 5k and B1c.**

**1. The composer's summary panel describes two audiences at once.** With a 443-contact
list selected, the panel read `Audience: ⭐ ALL BIDDERS — MAIN LIST`, `Recipients 10,146`,
`Segments 443`. Three claims, three different audiences. `sumSegments` has two writers —
`refreshPreview()` and the pre-flight report path — and nothing sequences the debounced
preview responses, so a slow reply for a large audience can overwrite a fast one for a
small one. The obvious explanation (a missing repaint) is **not** it: `paintAudienceSummary()`
is wired to the select's `change` event. Whether a send would have gone to 443 or 10,146 is
the first thing 5m must establish.

**2. There is no timezone anywhere, and the droplet runs UTC.** `due_campaign_ids()`
compares `scheduled_at` — a naive wall-clock from a `datetime-local` input — against a naive
`datetime.now()`. No `TZ`, `tzinfo`, `ZoneInfo` or `utcnow` exists in `config.py`,
`main.py` or `campaign_dispatch.py`. **A campaign scheduled for 6:00 PM Eastern dispatches
at 2:00 PM Eastern**, four hours early in EDT and five in EST. For an auction house selling
"the sale is tonight", an afternoon blast is worse than none. This is the third
two-clocks-one-column defect in this codebase — after `contact_list_members.added_at` and
`contact_lists.created_at` — and the first one that decides when a real person is texted.
