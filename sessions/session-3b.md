# Session 3b — Today + Contacts screens

## Objective

Module 3b. The two screens he lives in, on the shell 3a just built.

**You are in a git worktree, running in parallel with session 4 (composer).** Session 4
owns `app/templates/campaigns.html`, `app/services/campaign_service.py`,
`app/routers/campaigns.py`, `app/models/campaign.py` and `alembic/`. Stay out of all of
them and there is nothing to merge.

**Do not touch `app/templates/base.html`.** 3a settled the shell and session 4 is writing
against the same block contract right now. If you need a shell change, stop and note it in
`status.md`.

## Prerequisites

- `CLAUDE.md` read in full.
- 3a merged. `bash agent/gate.sh` green — 76 tests. `npm run build:css` before serving.
- Block contract from 3a: `{% block title %}` is the **top-bar heading**, not the document
  title. `{% block page_actions %}` is the top-bar right slot. `{% block content %}` is the
  body.

## Design reference

`pen-exports/b3I3tf.png` (Today) and `pen-exports/j98DI.png` (Contacts).

Two deliberate departures from those renders, both because the underlying capability was
deferred with the scraper:

- **No line-type / "Mobile · Landline · VoIP" column on Contacts.** We have no line-type
  data and will not imply we do.
- **No Prospects anything.**

---

## Today (`app/templates/today.html`)

### The hero — read this before building it

The render shows "Next auction — Restaurant Equipment Liquidation, Wed 20 Aug 10:00a."
**There is no auctions table and you are not to invent one.** Deriving it:

1. The soonest campaign with a future scheduled send time, if scheduling exists yet.
   Session 4 adds it in parallel, so guard on the column/attribute being present rather
   than assuming it.
2. Otherwise the most recently created draft campaign.
3. Otherwise an empty state — "No campaign queued" with a button to Compose.

Show the campaign name, its category chip, the eligible audience count, and how many days
since that category was last texted. Keep the accent border from the render.

### Category cards — five, in `sort_order`

Each shows the category name with its `--s1`…`--s4`/neutral swatch, **days since last
send** as the large figure, and the contact count beneath. Red when it exceeds a
configurable staleness threshold (default 14 days).

**Compute "days since last send" from actual sends**, not from a campaign's audience
string: the most recent `sms_messages.sent_at` for any contact tagged with that category,
counting only `sent` and `delivered`. That's the truth rather than a proxy, it needs
nothing from session 4, and it stays correct when a campaign targets a union of two
categories. Index-friendly — one query per category, or one grouped query.

Never texted → show `—`, not `0`. Those mean opposite things.

### Stat tiles — four

Delivered (30 days, with the change vs the prior 30) · opt-out rate (30 days) · segments
this cycle, with "10,000 included · N billable" beneath · cost this cycle, from
`billing_service`. **Never compute a price in the template or the view** — call the
service.

### 14-day segment chart

One bar per day, segments sent. Days with no send render as a faint 3px rule, not a gap —
the render shows this. Ticks on alternating days. Build it with the token colours; `--s1`
for bars.

### Per-category last-send outcomes

For the three most recently texted categories: a stacked bar of delivered / failed /
blocked with the percentages direct-labelled beside it, and a legend. `--s1`/`--s2`/`--s3`
in that order. The direct labels are not decoration — they are what keeps the chart
readable for colourblind users, so they are required, not optional.

### `app/services/dashboard_service.py`

All of the above. One service, no queries in the router, no arithmetic in the template.

---

## Contacts (`app/templates/contacts.html`)

- **Category tabs** with live counts, plus "All". Swatch beside each.
- **Server-side pagination**, 50 per page. Must not degrade at 50,000 contacts — no
  loading the table into memory to count it.
- **Search** across name, phone and company (`attributes.company`).
- **Bulk select → add to category / remove from category / export CSV.**
- Per row: name, company beneath, phone in the mono face, category chips, source, last
  texted, send count.
- Empty states that say something useful, not a blank table.

### Retire the uncategorised import endpoints

`/api/contacts/import` (and any sibling) still accepts an upload with no category. That
bypasses the single guardrail module 2 exists to create, and it must not reach the client.

- Remove them, or make `category_id` required with a 400 that names the replacement.
- The category-first flow from module 2 (`import_service`) is the only supported path.
- `docs/API.md` documents the old endpoints. Module 8 owns that file — do **not** edit it;
  note the stale entry in `status.md` instead.
- Test that a POST without a category is rejected.

---

## Acceptance criteria (demonstrate each in the transcript)

- [ ] `bash agent/gate.sh` green at start (76 tests) and end — shown both times
- [ ] Suite twice in a row, green both
- [ ] `GET /` (or `/dashboard`) returns 200 and the HTML contains all five category labels
- [ ] A test seeds campaigns with known send dates and asserts days-since-last-send per
      category, **including a never-texted category rendering `—`**
- [ ] Seed 1,000 contacts: page 1 returns 50, and the query count is bounded — show it
- [ ] Category filter counts match a direct count; search finds a known contact
- [ ] POST to the old import endpoint without a category is rejected — shown
- [ ] Screenshots of both screens at 1440px beside their `pen-exports` renders
- [ ] `git diff --name-only` shows **no** `base.html`, `campaigns*`, `campaign_service.py`,
      `models/campaign.py` or `alembic/`
- [ ] No file over 500 lines
- [ ] `status.md` and `handoff.md` updated — append, don't rewrite; session 4 is editing
      them in parallel

## Constraints

- No Alembic migration in this session. If you think you need one, stop and escalate —
  session 4 owns `alembic/` this wave and two heads is a merge you don't want.
- No price, allowance, brand hex or carrier name in a template.
- No class names assembled from template variables.
- `--s1`…`--s4` are validated; four hues is the ceiling; General is neutral by design.
- `agent/gate.sh` and `agent.config.sh` are human-only.
- If the spec is wrong, verify before following it.
