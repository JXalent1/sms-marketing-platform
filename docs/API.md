# API reference

All `/api/*` routes require the session cookie and return 401 without it.
Page routes redirect to `/login`. The only public routes are `/health`,
`/login`, `/logout` and the carrier webhooks.

---

## Campaigns

### `GET /api/campaigns/audiences`
Selectable audiences with live counts.
```json
{"audiences": [
  {"selector": "all", "label": "All contacts", "count": 5231},
  {"selector": "list:3", "label": "August buyers", "count": 412}
]}
```

### `POST /api/campaigns/preview`
Cost and deliverability check. **Call this before every send.**
```json
// request
{"message_template": "🚨 Hi {name}, sale today!", "audience": "all"}

// response
{
  "encoding": "UCS-2",              // GSM-7 or UCS-2
  "characters": 26,
  "segments": 1,                    // per message
  "recipients": 5231,
  "total_segments": 5231,           // what the campaign will cost
  "forced_unicode_by": ["🚨"],      // what triggered UCS-2
  "gsm7_segments_if_stripped": 1,   // cost without those characters
  "risky_links": []                 // shortener domains carriers block
}
```

### `POST /api/campaigns/preflight`
The composer's step-3 checklist. Read-only, not rate limited.

```jsonc
// request
{"message_template": "…", "audience": "list:3", "category_id": 3, "batch_size": 50}

// response
{
  "ok": true,                       // false if any check FAILED
  "checks": [                       // fixed order, send_path then capacity
    {"key": "send_path", "label": "Sending status", "status": "pass", "reason": "…"},
    {"key": "capacity",  "label": "Sending capacity", "status": "pass", "reason": "…"}
    // opt_out_language · brand_identified · segment_count · merge_expansion
    // recent_overlap · link_shortener · category_match
  ],
  "counts": {
    "recipients": 1204,
    "suppressed": 37,               // texted inside the suppression window
    "opted_out": 12,
    "segments_per_message": 1,      // the TEMPLATE's own count — an estimate
    "max_segments_per_message": 2,  // the longest rendered message
    "template_total_segments": 1204,// what the template alone predicts
    "total_segments": 1251,         // MEASURED: rendered per recipient, summed
    "segments_measured": true       // false when no audience resolved
  },
  "encoding": "GSM-7",
  "estimated_cost": 18.77,          // HIS rate, net of the month's allowance
  "price_per_segment": 0.015
}
```

Every check returns the same `key / label / status / reason` shape, so the UI
draws whatever it is given and a check can never say one thing on screen and
another over the API.

`total_segments` is the template **rendered against every resolved recipient**
and summed, not `segments × recipients`. `{name}` is six characters and a
Christopher is eleven, so a 158-character template that counts as one segment
costs two for him — under-quoted at exactly the moment the quote matters.
`/preview` still reports the cheap template estimate, because it runs on every
keystroke; this endpoint is a deliberate action against a resolved audience and
is exact. When they disagree, this one is right.

Passing here is **not** permission to send: the `send_path` and `capacity` rows
re-state the send path's own verdicts, they do not replace them.

`send_path` fails when the configured carrier could not start and the app has
fallen back to the console provider. That is a **refusal**, not a warning — the
campaign will not run — and it is deliberately not reachable from a *chosen* dry
run, which passes and behaves exactly as it always has. The distinction is
`send_mode()`: `unavailable` means the box tried to reach a carrier and could
not; `dry_run` means someone asked for the console. Before the two were
distinguishable, a degraded box passed the capacity check on the console
provider's bottomless balance, marked every row `sent`, and invoiced for
messages nobody received.

### `POST /api/campaigns` · rate limited 5/min
Creates a **draft**. Sends nothing.
```json
{"name": "August sale", "message_template": "Hi {name}…",
 "audience": "list:3", "batch_size": 50, "category_id": 3,
 "scheduled_at": "2026-08-21T09:00"}
```
`category_id` is required **unless** `cross_category_override: true` is sent
explicitly. There is no third case and no default — the moment "no category"
becomes something the form can submit by accident, the guarantee the category
work exists for is gone.

`scheduled_at` hands the campaign to the same `send_campaign()` the Send button
reaches, via a scheduler tick. Scheduling is not a second, thinner send path.

Returns the campaign including `estimated_segments`. It does **not** return
`estimated_cost`: that column is priced at our wholesale rate and never leaves
the server. The client's cost comes from `/preflight` and `/preview`.

### `POST /api/campaigns/{id}/send` · rate limited 5/min
**409** while the send path is degraded, before the background task is queued —
so the answer is immediate rather than surfacing on the next poll, and the
campaign stays a **draft** that can be sent once the carrier is fixed. Nothing in
this codebase moves a campaign back from `aborted`, so consuming one that never
started would mean rebuilding it.

Otherwise it starts the background send. Two pre-flight refusals run there, in
this order, and either aborts the campaign with `status: "aborted"` and an
`abort_reason`, having sent nothing:

1. **the send path** — the configured carrier failed to start, so nothing can
   leave the building. Checked first: a box that cannot reach a carrier has no
   capacity question to answer, and the answer it *would* give is the console
   stub's 999,999.
2. **capacity** — the carrier balance cannot fund the campaign.

Any row the send loop writes while the send path is degraded gets
`status: "not_sent"`, which is outside the billable set, and the campaign ends
`aborted` with a reason rather than `completed` — a blast that reached nobody
must not read like one that worked. That is a backstop, not the mechanism: if
the refusals above hold, no such row is ever written.

### `GET /api/campaigns` · `GET /api/campaigns/{id}`
List, or detail with up to 200 messages plus `delivered_count` /
`undelivered_count` (populated asynchronously by webhooks).

### `GET /api/campaigns/{id}/failures`
Failure reasons grouped and counted — the first thing to check after a
disappointing campaign.
```json
{"total": 4652, "reasons": [
  {"reason": "Account inactive: out of funds", "count": 4623},
  {"reason": "Not routable: landline", "count": 19}
]}
```

### `POST /api/campaigns/test-sms` · rate limited 5/min
Send one message to a real handset. `{"phone": "+1...", "message": "..."}`

Refused with `{"success": false, "error": "…"}` while the send path is degraded,
for the same reason a campaign is: this is the screen someone uses to decide
whether the box works, and on the console fallback it would answer "Test SMS
sent" about a message that reached nobody. A chosen dry run is unaffected.

---

## Reports & history

Everything here reads. Nothing in this section can send, bill or change a row.

### `GET /api/reports/campaigns?page=1&per_page=25`
Campaign history, newest first, with each campaign's outcome and click totals
folded in.
```json
{"page": 1, "per_page": 25, "total": 34, "pages": 2,
 "campaigns": [{"id": 34, "name": "Thursday restaurant", "status": "completed",
                "category_label": "Food Service", "audience_label": "Food Service",
                "recipients": 1223, "created_at": "2026-08-30T18:02:11",
                "started_at": "...", "completed_at": "...", "scheduled_at": null,
                "abort_reason": null, "sent": 1218, "failed": 5, "held_back": 0,
                "clicks": 96, "clickers": 84}]}
```

`abort_reason` is rendered verbatim wherever it appears. Sessions 5d and 5h and
`decisions/006` settled what a refusal says; a surface that paraphrased it would
be a second sentence about one fact.

### `GET /api/reports/campaigns/{id}`
One campaign: what it was, what happened, what was clicked, and what it added to
the bill. `404` if there is no such campaign.
```json
{"campaign": {"id": 34, "name": "...", "status": "completed", "message_template": "...",
              "audience_label": "...", "category_label": "Food Service",
              "category_color_token": "s1", "cross_category_override": false,
              "created_at": "...", "started_at": "...", "completed_at": "...",
              "scheduled_at": null, "abort_reason": null,
              "link_target_url": "https://auctions4america.com/aug-30"},
 "outcome": {"recipients": 1223, "sent": 1218, "delivered": 1180, "failed": 5,
             "held_back": 0, "blocked": 0, "skipped": 0, "not_sent": 0,
             "pending": 0, "opted_out": 3},
 "clicks": {"clicks": 96, "filtered_clicks": 41, "links": 1218, "clickers": 84,
            "click_through_rate": 6.9},
 "cost": {"billing_month": "August 2026", "segments": 1218, "cycle_segments": 12664,
          "cost": 18.27, "price_per_segment": 0.015, "included_segments": 10000},
 "top_ups": [{"added_at": "2026-08-27T09:14:02", "recipients": 5}]}
```

Two click numbers, always. SMS links are opened by carrier scanners and handset
previews before any person sees them, so `filtered_clicks` sits beside the human
count rather than being folded away — the classification is a heuristic and a
bare number that quietly excludes things is not honest about being one.
`click_through_rate` is per distinct human clicker, and `null` when nothing sent.

`cost` is **his** cost, at `BILLING_PRICE_PER_SEGMENT`, priced as the marginal
campaign in its cycle: `cost_for_segments(cycle) - cost_for_segments(cycle - this)`.
A campaign that sat entirely inside the 10,000-segment allowance therefore costs
`0.00`, because that is what he was billed for it. Two campaigns in one cycle are
each priced as the marginal one, so their costs do not sum to the cycle total
when the allowance is crossed between them — that is a property of an allowance,
which is why the screen says "added to this month's bill". `WHOLESALE_COST_PER_SEGMENT`
appears nowhere in this payload and never will.

### `GET /api/reports/campaigns/{id}/messages?page=1&status=`
One page of a campaign's recipients, each with its click data. `status` filters
to one message status.
```json
{"page": 1, "per_page": 25, "total": 1223, "pages": 49,
 "messages": [{"id": 91021, "campaign_id": 34, "contact_id": 812,
               "phone": "+19545550123", "message": "...", "status": "delivered",
               "segments": 1, "sent_at": "...", "delivered_at": "...",
               "top_up_at": null, "error_message": null,
               "clicks": 1, "bot_clicks": 0, "last_clicked_at": "..."}]}
```

`error_message` is passed through `scrub_provider_text()` on the way out. It is
carrier free text and it has reached a client screen once already, via
`blocked_numbers.notes`.

### `GET /api/reports/contacts/{id}/messages?page=1`
What one person has been sent and whether they opened it. Same row shape, plus
`campaign_name` on every row — "Campaign 47" is not what makes a phone call
possible; "the flooring sale on Tuesday" is. `404` for an unknown contact.

### `GET /api/reports/campaigns/{id}/export`
The report and its recipients as one CSV (`text/csv`, `Content-Disposition:
attachment`). Summary rows first, then a row per recipient. Built from the same
report dict the screen reads, so the two cannot drift. It carries no carrier
name, no raw provider payload and no wholesale figure — it is the one artefact
that leaves the building, and `tests/test_campaign_reports.py` scans it at
runtime rather than trusting this paragraph.

---

## Contacts & lists

| Route | Purpose |
|---|---|
| `GET /api/contacts?q=&category_id=&list_id=&skip=&limit=` | Search / paginate |
| `GET /api/contacts/categories` | Category facets with counts |
| `GET /api/contacts/export.csv` | Export the current filter |
| `POST /api/contacts` | Add one contact |
| `POST /api/contacts/bulk/add-category` | Tag a selection |
| `POST /api/contacts/bulk/remove-category` | Untag a selection |
| `GET /api/lists` | Lists with counts |
| `DELETE /api/lists/{id}` | Remove the list; contacts are kept |

`POST /api/contacts/import` and `POST /api/contacts/import/preview` are
**retired** and answer `400` with a pointer to `/api/imports/*`. They took no
category, which is the one thing the category work exists to make impossible.
They answer 400 rather than 404 so a bookmarked script is told where the flow
went.

---

## Imports — category first

The import flow the client actually uses: pick tonight's niche, see what the
file will do, then commit. `category_id` is a **required** form field on preview
and commit. All three are `multipart/form-data`.

### `POST /api/imports/preview`
Counts and the detected column mapping. **Writes nothing.**

Worth the extra click every time: a CSV whose phone column was not recognised
imports zero rows and looks identical to a successful import of an empty file.

```jsonc
// request: file=<csv>, category_id=3
{
  "opted_out": 4,             // on the blocklist — skipped outright, not tagged
  "already_in_category": 88,  // present and already tagged
  "existing_contacts": 61,    // present, will gain this category
  "new_contacts": 412,        // will be created
  "category_id": 3,
  "category_label": "Food Service",
  "rows": 578,                // data rows in the file
  "valid_phones": 565,
  "unusable": 9,              // no phone cell, or not a usable number
  "duplicates": 4,            // same number twice inside this one file
  "headers": ["Name", "Cell", "Email"],
  "mapped":  {"Name": "name", "Cell": "phone", "Email": "email"},
  "unmapped": [],
  "sample":  [{"name": "…", "phone": "…", "email": "…"}]   // first 5 rows
}
```

### `POST /api/imports/commit`
Applies the same plan preview reported, so the "actuals" cannot drift from what
it did. Creates one batch list, which is what makes the import undoable.

```jsonc
// request: file=<csv>, category_id=3
{"success": true, "list_id": 17, "list_name": "Food Service — 2026-08-19",
 /* …every count from preview… */ }
```

Opted-out numbers are skipped outright — not created, not tagged, not added to
the batch. An opt-out is not a filter applied at send time; it means we should
not be building an audience around that person at all.

On any unexpected error the transaction is rolled back and it returns `500`. A
half-imported file with no batch to undo is the worst outcome available.

### `POST /api/imports/{list_id}/undo`
Reverses exactly one batch. Subtractive, not destructive.

```json
{"success": true, "list_id": 17, "tags_removed": 473,
 "memberships_removed": 473, "contacts_deleted": 412, "contacts_kept": 61}
```

Three things it deliberately will not do: remove a tag it did not add; delete a
contact that has anything left (another category, another list, or any message
history); or touch the blocklist. An opt-out outlives the import that surfaced
the number.

`400` if the list is not an import batch, `404` if it does not exist.

---

## Categories

| Route | Purpose |
|---|---|
| `GET /api/categories` | All categories with contact counts |
| `POST /api/categories` | Create |
| `PATCH /api/categories/{id}` | Rename / recolour |
| `DELETE /api/categories/{id}` | Remove the category; contacts are kept |

---

## Prospects

The holding pen between a scraper and the textable list. Nothing reaches
`contacts` from a discovery source except through `POST /api/prospects/promote`.

**The rule the whole module exists to enforce: buyers, never sellers.** A
consignor on the list costs money to text, dilutes the audience and puts a
competitor on the client's own marketing channel. `search_term` and
`buyer_rationale` are non-nullable, every queue row carries the rationale, and
`seller_or_consignor` and `competitor` are first-class reject reasons that
suppress permanently.

### `GET /api/prospects`
One page of the queue. Server-side paging and sorting, always.

| Parameter | Meaning |
|---|---|
| `status` | `pending` (default), `promoted`, `rejected` |
| `category_id` | one category |
| `line_type` | `mobile`, `voip`, `landline`, `toll_free`, `unknown` |
| `eligible` | `true` / `false` / `all` — the gate's own definition, not "is it mobile" |
| `search_term`, `q` | the term that found it; free-text over name and address |
| `sort`, `direction` | `score`, `created`, `business_name`, `distance`, `term` |
| `page`, `per_page` | |

```json
{"prospects": [{"id": 12, "phone": "+19545550123", "business_name": "Taco Truck",
                "address": "1 Test Way, Fort Lauderdale FL", "category_id": 1,
                "category_label": "Food Service", "category_slug": "food_service",
                "category_confidence": 0.8, "distance_miles": 12.0,
                "line_type": "mobile", "promote_eligible": true, "score": 71,
                "status": "pending", "source": "google_places",
                "source_url": "https://...", "scraped_at": "...", "source_count": 2,
                "search_term": "food trucks fort lauderdale",
                "buyer_rationale": "Food trucks buy used prep and refrigeration...",
                "promoted_contact_id": null}],
 "total": 118, "page": 1, "per_page": 50, "pages": 3}
```

`raw_payload` is deliberately absent, and so is anything a lookup or a scrape
cost us. The payload is retained on the row for tracing a disputed record and it
is whatever a third party returned — the one column here nobody has vetted for
what it contains or whom it names.

### `GET /api/prospects/summary`
The tiles above the queue, plus the reject dropdown's options.
```json
{"pending": 118, "promoted": 42, "rejected": 31, "total": 191,
 "promote_eligible": 63, "unscreened": 12,
 "reject_reasons": [{"value": "seller_or_consignor",
                    "label": "Seller or consignor — sells to us"}]}
```

`promote_eligible` is counted with `PROMOTABLE_LINE_TYPES` — the same tuple the
promote guard reads — rather than with a second idea of a good number.
`unscreened` is prospects with no lookup row at all; they cannot be promoted,
because a number nobody has screened has skipped the gate rather than passed it.

### `GET /api/prospects/terms`
Per search term: what it found, and what its rejections say about it.
```json
{"terms": [{"term": "estate liquidators near me", "buyer_rationale": "...",
            "found": 40, "pending": 2, "promoted": 1, "rejected": 37,
            "wrong_side": 33, "wrong_side_share": 0.89, "flagged": true,
            "reasons": {"seller_or_consignor": 30, "competitor": 3, "other": 4}}],
 "flag_rule": {"min_rejections": 5, "share": 0.5}}
```

`wrong_side` counts `seller_or_consignor` and `competitor` apart from every
other reason, which is how a search that finds the wrong side of the room
becomes visible instead of being something somebody eventually notices. Both
thresholds are config (`PROSPECT_TERM_FLAG_*`) and are returned so the screen
can explain the flag rather than assert it.

### `GET /api/prospects/export.csv`
The current filter as CSV, streamed and page-free. Columns: `phone`,
`business_name`, `category`, `line_type`, `score`, `distance_miles`, `status`,
`search_term`, `buyer_rationale`, `source`, `source_url`, `scraped_at`. No
payload, no cost.

### `POST /api/prospects/promote`
```json
{"prospect_ids": [12, 13, 14], "category_id": 1}
```
Turns the selection into contacts through `contact_service.upsert_contact()`,
tagged with the chosen category, and links `promoted_contact_id` back.

**Partial outcomes are the normal case and are reported as such** — a screenful
where three are landlines returns the ones that went in and the ones that did
not, each with its reason, rather than 400-ing the batch and making the client
re-tick fifty rows to find the three.
```json
{"success": true, "promoted": [...], "refused": [{"id": 14, "reason": "This is a landline, ..."}],
 "not_found": [], "category": {"id": 1, "slug": "food_service", "label": "Food Service"}}
```

Refusal order is opt-out, then unreachable, then blocked, then the line-type
gate: an opt-out outranks a landline because one is a person's request and the
other is a property of a wire. The gate's wording comes from
`lookup_service.refusal_for()`, so this screen and the queue's filter cannot
disagree about which numbers pass.

### `POST /api/prospects/reject`
```json
{"prospect_ids": [15], "reason": "seller_or_consignor", "notes": "auction house"}
```
Says no permanently. The rejection is keyed on the **phone number**, not the
row, so re-ingesting the same business from a different source does not
resurface it — the same separation `blocked_numbers` has from
`contacts.is_active`. An unknown reason is a 400, and the dropdown is served
from the same list `reject()` accepts.

### Line-type screening

Every prospect passes a line-type gate before it can be promoted, because 2,526
of one campaign's failures on this client's list were not-routable numbers — 39%
of a 6,857-message send, all paid for. There is no API for it: screening runs
with the discovery job.

`PROSPECT_LOOKUP_PROVIDER` defaults to `none`, which makes **no call at all** and
answers `unknown`, and `unknown` is not promote-eligible — so a box with no
screening credential holds prospects in the queue rather than promoting
landlines it never checked. Session P1b added the carrier provider beside it;
switching it on is one line of `.env` and it spends real money, which is why the
default did not move.

`PROSPECT_LOOKUP_MONTHLY_CAP` (default `$50`) is a hard ceiling on that spend,
checked before every call. **Lookups and sends draw on the same carrier
balance**, so an unbounded screening run is a silent transfer out of the pot the
campaign pre-flight check measures. A pass that reaches the cap stops spending,
logs how many numbers went unscreened, and leaves them screenable next month.
Nothing about the cap or the per-lookup price appears in any response: it is our
cost, on the same footing as `WHOLESALE_COST_PER_SEGMENT`.

---

## Blocklist

| Route | Purpose |
|---|---|
| `GET /api/blocklist` | Blocked numbers (capped at 5,000) plus `counts` |
| `POST /api/blocklist/block` | `{"phone", "reason", "notes"}` |
| `POST /api/blocklist/unblock` | `{"phone"}` |
| `GET /api/blocklist/count` | Count only |

Reasons: `stop_keyword`, `delivery_failure`, `carrier_block`, `manual`.

`GET /api/blocklist` returns `counts`, grouped server-side over the whole table:
```json
{"counts": {"opt_outs": 3, "unreachable": 2626, "other": 11, "total": 2640}}
```
`opt_outs` is `stop_keyword` alone — the same definition
`dashboard_service`'s opt-out-rate tile uses, deliberately, so the two screens
cannot disagree about the one number a client judges his list by. `unreachable`
is `delivery_failure` + `carrier_block`; `other` is everything else, manual
blocks today. The split exists because one figure labelled "Blocked" counted
2,626 auto-blocked landlines as if 2,626 people had opted out, on a screen
titled "Opt-outs". Only the opt-out figure is styled critical: an unreachable
number is a data-quality fact, not a compliance event.

Counted server-side rather than tallied from `numbers`, which is capped —
a client-side tally would under-report a long list, and under-report it as
*fewer opt-outs*, the direction nobody checks.

**Delivery webhooks auto-block.** A carrier failure matching
`AUTO_BLOCK_ERROR_FRAGMENTS` ("not routable", "landline", "deemed invalid", …)
blocks the number with `reason: "delivery_failure"`. This used to fire only on
the *submission* path, where a provider rejects a send outright — but most dead
numbers are accepted at submission and fail later via webhook, so they stayed
live and were paid for again on every campaign. Temporary failures
("Blocked as spam - temporary") deliberately match nothing: blocking on a
transient error deletes a reachable buyer permanently, which costs far more than
one retry.

Three conditions, all required. The message must not already carry a delivery
receipt (`delivered_at is None`) — the failed branch accepts `status ==
"delivered"`, so a contradictory failure webhook arriving after a receipt would
otherwise block a number that provably received the text, and a handset receipt
is not revocable. The branch runs only on the first terminal event per message,
so a carrier retrying for days blocks once. `block_number()` refusing duplicates
is the third layer, not the first.

The fragment list itself is under review — see
`decisions/003-auto-block-fragments-on-the-webhook-path.open.md`. It was written
for the low-volume submission path and its entries are unanchored substrings.

---

## Usage & billing

The plan is: monthly fee + (segments beyond the included allowance × rate). No
tiers, no "overage" as a separate concept.

### `GET /api/usage/current`
```json
{"month": "August 2026", "included_segments": 10000, "used_segments": 12664,
 "message_count": 9871, "remaining": 0, "percentage_used": 100,
 "billable_segments": 2664, "monthly_fee": 0.0, "price_per_segment": 0.015,
 "total_due": 39.96, "billing_start": "2026-08-01", "reset_date": "2026-09-01"}
```

Billed on `('sent', 'delivered')`. Counting only `sent` makes the meter appear
to freeze the moment delivery webhooks land — that bug hit a live client for
days. Currency arithmetic is `Decimal` end to end: `n × 0.015` for odd `n` lands
on a half-cent boundary and floats under it about a quarter of the time.

`not_sent`, `undelivered`, `failed`, `blocked` and `skipped` are outside that
set. `not_sent` is the one added by session 5d: a row queued while the send path
was degraded, which never reached a carrier. Excluding it is not a pricing
concession — a segment that never left the building is not a segment.

### `GET /api/usage/history?cycles=6` · `GET /api/usage/pricing`
History per cycle; pricing rendered from `.env` so the UI can't drift from what
the code bills.

### `GET /api/usage/balance`
Remaining sending capacity, **denominated in segments**.
```json
{"segments_remaining": 1160, "threshold_segments": 5555, "low": true}
```

Deliberately not a dollar balance. The only person logging in here is the
client, and what he needs is "how many more messages can I send" — not what we
pay per message or which carrier holds the funds. `WHOLESALE_COST_PER_SEGMENT`
is used as the divisor and is never returned.

---

## Settings

| Route | Purpose |
|---|---|
| `GET/PUT /api/settings/auto-reply` | Inbound auto-reply text (+ segment breakdown) |
| `POST /api/settings/auto-reply/reset` | Restore the default |
| `GET /api/settings/system` | Send mode, sender number, environment flags |

### `GET /api/settings/system`
```json
{
  "provider_configured": true,      // send_mode is "live"
  "dry_run": false,                 // a dry run was CHOSEN
  "sending_unavailable": false,     // the carrier failed to start
  "send_mode": "live",              // live | dry_run | unavailable
  "send_mode_label": "Live",        // rendered verbatim by every surface
  "send_mode_detail": "Campaigns are being sent.",
  "sender_number": "+19545554120",
  "environment": "production",
  "skip_non_us": true,
  "preflight_balance_check": true
}
```

Three send modes, not two. `dry_run` means someone **chose** the console
provider; `unavailable` means a carrier was configured and refused to start. The
two were one field until session 5c, so a broken live box answered this endpoint
exactly as a healthy dry-run one did — and the product then described a failed
carrier as a deliberate dry run on every screen. `send_mode_label` and
`send_mode_detail` are the only client-safe wording; they come from
`send_mode()` in `app/sms/factory.py` and nothing else may compose its own.

**There is no `webhook_url` field, and adding one is a white-label regression.**
It was built as `PUBLIC_BASE_URL + "/webhooks/" + provider.name`, so it printed
the carrier's name onto a client-facing screen without the name appearing in any
template — the leak a grep can never find. It is also a setup value only we use:
the client has no login to the carrier portal. Use
`settings.webhook_url(provider_name)` directly when configuring, and keep it in
the deployment notes. `tests/test_whitelabel.py::test_system_info_exposes_no_webhook_url`
pins its absence.

---

## Health

### `GET /health` — public
Liveness **and** send status. Unauthenticated because uptime monitors have no
session.
```json
{"status": "degraded", "sending_ok": false, "send_mode": "unavailable",
 "reason": "Campaigns cannot go out right now. Contact support.",
 "config_ok": false,
 "config_issues": [{"key": "region_not_enabled",
                    "detail": "A destination was refused because this messaging account is not enabled for its region. Nothing is wrong with the recipient — enable the region on the messaging account.",
                    "since": "2026-08-26T09:14:02"}]}
```
`status` is `"healthy"` and `reason` is `null` when sending works.

**Always HTTP 200, including while degraded.** `deployment/deploy.sh`
health-checks this endpoint after the restart and rolls the release back on a
non-200, so a 503 for a bad carrier credential would revert every deploy to a
degraded box — including the one that fixes it. The app being up and the app
being able to send are different facts. **Configure the uptime monitor on the
`sending_ok` field, not on the status code**; it is the only alert channel that
still works when the carrier does not, and an SMS alert about being unable to
send SMS is self-defeating.

`reason` is `send_mode().detail` — our wording, naming no carrier. The SDK
exception behind it stays in the log.

**`config_ok` is the second field worth a monitor** (session 5g). It goes false
when a carrier refuses a destination for a setting on *our* sending account
rather than anything about the recipient — today that means a region the
messaging account was never enabled for. Sending still works for every other
destination, so `sending_ok` stays true and this is the only place it surfaces.

It cannot be paged over SMS, for the reason above. It cannot be paged per event
either: these arrive on the delivery webhook, thousands at a time, inside a
request that must answer promptly. So it is written as a row and reported here.
An alert stops being reported `CONFIG_ALERT_WINDOW_DAYS` (7) after it was last
raised — anything still misconfigured re-raises on the next failure. `detail`
wordings live in `compliance.CONFIGURATION_ALERT_DETAIL` and name no carrier.

Until 5g the product's only response to this was to block the recipient forever
for a problem on our side.

---

## Short links (public — this is what the recipient taps)

### `GET /{slug}`
Resolves one slug and redirects (302) to the campaign's target URL, recording
the click. An unknown slug returns **404 with a plain-text body and no brand**:
this is served to whoever typed it, and an unknown slug is not an occasion to
tell a stranger whose links these are.

Two things about this route are load bearing:

- **It is registered last**, so `/settings` and every other page wins the match
  first. `RESERVED_SLUGS` also removes those words from slug *minting*, and
  `is_slug_path()` subtracts the same set on the serving side — one collision,
  three layers, and each must subtract the same definition rather than keep its
  own copy.
- **`SHORT_LINK_DOMAIN` gets its own host guard** (`short_link_host_guard` in
  `app/main.py`): the short domain serves short links and nothing else. It is a
  positive test rather than a denylist, so a page added tomorrow is excluded by
  default instead of by somebody remembering. If the setting is mis-set to the
  admin host the guard **fails open** and logs loudly at startup — there is no
  configuration in which blocking every request is the right answer.

A click arriving within `CLICK_MIN_HUMAN_SECONDS` of the carrier accepting the
message is recorded as a filtered click rather than a human one. Nobody reads a
text, unlocks a handset and taps a link in three seconds; the carrier's own URL
scanner does it in under one. Both counts are reported — see
`GET /api/reports/campaigns/{id}`.

---

## Webhooks (public — the carrier calls these)

### `POST /webhooks/telnyx`
Handles `message.received` (STOP/START/HELP/auto-reply) and delivery events
(`message.sent|delivered|failed|finalized`).

### `POST /webhooks/twilio/sms` · `POST /webhooks/twilio/status`
Inbound (returns TwiML) and delivery status callback.

Both always return 200, even on error — a non-200 makes carriers retry the same
event for hours.

`GET` on any webhook path returns a health check, so you can paste the URL into a
browser to confirm it's reachable.

---

## Notes

**Rate limits** are per-IP and depend on nginx forwarding `X-Forwarded-For`.
Without that header every request looks like `127.0.0.1` and the limiter becomes
one shared bucket.

**Error strings are scrubbed.** `scrub_provider_text()` strips carrier names and
doc URLs from anything client-facing, so you can switch carriers without the UI
contradicting itself.

**There is no OpenAPI schema.** `docs_url`, `redoc_url` and `openapi_url` are
disabled deliberately — the auto-generated docs published a complete map of every
endpoint including the send API.
