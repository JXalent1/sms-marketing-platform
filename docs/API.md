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
