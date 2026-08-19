# Session 4 — Composer & campaign guardrails

## Objective

Module 4. **This is the module the whole project exists for.** He runs a different-niche
auction almost every day; the failure this prevents is a Memorabilia collector getting a
text about a walk-in cooler. Everything here makes that structurally hard rather than a
matter of him being careful.

Last build module before go-live.

**You are in a git worktree, running in parallel with session 3b (Today + Contacts).**
3b owns `app/templates/today.html`, `contacts.html`, `app/routers/pages.py`,
`dashboard.py` and `app/services/dashboard_service.py`. Stay out of them.

**Do not touch `app/templates/base.html`.** 3a settled the shell; write against its
blocks: `{% block title %}` is the top-bar heading, `{% block page_actions %}` the top-bar
right slot, `{% block content %}` the body.

You own `alembic/` this wave. 3b has been told to add no migration.

## Prerequisites

- `CLAUDE.md` read in full, **especially the escalation list** — this session works next
  to billing and the pre-flight check, and neither is yours to change.
- 3a merged. `bash agent/gate.sh` green — 76 tests. `npm run build:css` before serving.

## Design reference

`pen-exports/BWsLw.png`. Three numbered cards down the left, preview and summary pinned
right.

---

## Scope

### 1. `campaigns.category_id`

Nullable FK to `categories`, with a migration. Nullable in the schema because historic
campaigns predate it; **required by the API** on create.

Creating a campaign without a category returns 400 naming the problem. The one escape is
an explicit cross-category override — a separate field the caller must set deliberately,
never a default, and recorded on the campaign so the audit trail shows a human chose it.

Backfill nothing. Old campaigns keep `NULL` and the UI shows "—".

### 2. The composer

Rebuild `campaigns.html` as three numbered steps.

**Step 1 — which auction is this for?** Category as a segmented control, required, marked
as such. Audience select (everyone in the category / category minus recent recipients /
a saved list ∩ category). Optional cap. A line stating that contacts in more than one
category are texted once and recent recipients are suppressed automatically.

**Step 2 — message.** Textarea, merge-tag insert buttons, and a live counter row:
characters, encoding, segments per message, recipients, total segments, estimated cost.

**The cost shown is the client's**, from `BILLING_PRICE_PER_SEGMENT` via `billing_service`.
`WHOLESALE_COST_PER_SEGMENT` is our cost and must never reach a response body or a
template — session 1b removed a leak of exactly this kind and there is a test guarding it.

A loud, unmissable warning when the body flips to UCS-2: one emoji cuts 160 characters per
segment to 70 and roughly triples the bill. Say what it costs, in dollars, at the current
recipient count.

**Step 3 — pre-flight.** A checklist, each item pass / warn / fail with a reason.

**Live phone preview** on the right, rendering merge tags against a real sample contact,
plus a "this send" summary: category, recipients, suppressed, opted out, segments,
estimated cost.

### 3. The pre-flight endpoint

`POST /api/campaigns/preflight` returning structured checks — the UI renders them, it does
not compute them:

| check | fails when |
|---|---|
| capacity | the account can't fund the whole send |
| opt-out language | no STOP instruction in the body |
| brand identified | business name absent from the opening |
| segment count | message exceeds a configurable segment ceiling (warn) |
| recent overlap | N recipients were texted within the suppression window (warn, with the count) |
| link shortener | a shortened domain is present (warn) — carriers filter these far harder than full domains |
| category match | body contains keywords mapped to a different category (warn, naming both) |

The keyword map lives in config, seeded per category — "fryer", "walk-in", "hood" for food
service; "lathe", "welder", "drill press" for equipment; and so on. Cheap, and it catches
the copy-paste mistake that is the single most likely way this goes wrong.

**Do not weaken, bypass or make advisory the existing capacity check in
`campaign_service.py`.** It is the reason a mid-blast funding failure becomes "the campaign
refused to start" instead of thousands of lost messages. Surfacing it in this endpoint is
additive.

### 4. Recent-contact suppression

Default 3 days, configurable. Applies **across categories** — a contact in three
categories must not get three texts in a week. Excluded recipients are `skipped`, never
billed, and the count is visible before sending, not discovered afterwards.

### 5. Scheduled send

A campaign may carry a future send time. The scheduler already exists in `main.py`'s
lifespan — register there. Due campaigns run through the identical send path; nothing
about scheduling may bypass pre-flight.

Session 3b's hero reads the soonest scheduled campaign if the attribute exists, so keep
the naming obvious.

### 6. Heading

The nav says "Compose"; the template still heads itself "Campaigns". Make them agree.

---

## Acceptance criteria (demonstrate each in the transcript)

- [ ] `bash agent/gate.sh` green at start (76 tests) and end — shown both times
- [ ] Suite twice in a row, green both
- [ ] `alembic upgrade head` from empty succeeds; `alembic check` reports no drift
- [ ] Campaign create without a category → 400; with the explicit override → accepted and
      the override recorded
- [ ] Suppression test: a contact texted 2 days ago is excluded, 5 days ago included, and
      the excluded one is `skipped` and unbilled
- [ ] Pre-flight returns every check with pass/warn/fail and a reason — full JSON shown
- [ ] Category-mismatch check fires: a food-service campaign whose body says "drill press"
      warns and names both categories
- [ ] UCS-2 test: adding one emoji changes segments per message and the estimate; the
      dollar figure shown uses the **client** rate
- [ ] `grep -rn "WHOLESALE_COST_PER_SEGMENT" app/routers/ app/templates/` → nothing
- [ ] A scheduled campaign fires through the normal send path with pre-flight applied
- [ ] `git diff --name-only` shows **no** `base.html`, `today.html`, `contacts.html`,
      `pages.py`, `dashboard*`
- [ ] No file over 500 lines — `campaigns.html` will want to exceed it; split the JS out
- [ ] `status.md` and `handoff.md` updated — append, don't rewrite; 3b is editing them too

## Constraints

- **Escalate rather than change**: the billing model, rate, allowance or billable-status
  set; `count_sms_segments()`; the capacity check's behaviour.
- Nothing may cause a real message to be sent. `SMS_PROVIDER` stays `console`.
- No price, allowance, brand hex or carrier name in a template.
- No class names assembled from template variables.
- `agent/gate.sh` and `agent.config.sh` are human-only.
- If the spec is wrong, verify before following it.
