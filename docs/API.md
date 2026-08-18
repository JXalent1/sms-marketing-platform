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

### `POST /api/campaigns` · rate limited 5/min
Creates a **draft**. Sends nothing.
```json
{"name": "August sale", "message_template": "Hi {name}…",
 "audience": "list:3", "batch_size": 50}
```
Returns the campaign including `estimated_segments` and `estimated_cost`.

### `POST /api/campaigns/{id}/send` · rate limited 5/min
Starts the background send. Runs the pre-flight balance check first — if the
carrier balance can't fund the campaign, it is aborted with `status: "aborted"`
and an `abort_reason`, having sent nothing.

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

---

## Contacts & lists

| Route | Purpose |
|---|---|
| `GET /api/contacts?q=&list_id=&skip=&limit=` | Search / paginate |
| `POST /api/contacts` | Add one contact |
| `POST /api/contacts/import/preview` | Show detected CSV column mapping |
| `POST /api/contacts/import` | Import CSV (multipart: `file`, `list_name`) |
| `GET /api/lists` | Lists with counts |
| `DELETE /api/lists/{id}` | Remove the list; contacts are kept |

Import response: `{"created": 412, "updated": 88, "duplicates": 12, "invalid": 3, "total": 515}`

---

## Blocklist

| Route | Purpose |
|---|---|
| `GET /api/blocklist` | All blocked numbers |
| `POST /api/blocklist/block` | `{"phone", "reason", "notes"}` |
| `POST /api/blocklist/unblock` | `{"phone"}` |
| `GET /api/blocklist/count` | Count only |

Reasons: `stop_keyword`, `delivery_failure`, `carrier_block`, `manual`.

---

## Usage & billing

### `GET /api/usage/current`
```json
{"month": "August 2026", "allowance": 15000, "used_segments": 57664,
 "remaining": 0, "percentage_used": 100, "overage": 42664,
 "overage_cost": 867.62, "base_fee": 400.0, "total_due": 1267.62,
 "reset_date": "2026-09-01"}
```

### `GET /api/usage/history?cycles=6` · `GET /api/usage/pricing`
History per cycle; pricing rendered from `.env` so the UI can't drift from what
the code bills.

### `GET /api/usage/balance`
Carrier balance. **Operator-facing** — don't surface it to clients.
```json
{"balance": 17.42, "threshold": 50.0, "low": true}
```

---

## Settings

| Route | Purpose |
|---|---|
| `GET/PUT /api/settings/auto-reply` | Inbound auto-reply text (+ segment breakdown) |
| `POST /api/settings/auto-reply/reset` | Restore the default |
| `GET /api/settings/system` | Provider status, sender number, **webhook URL** |

`GET /api/settings/system` is where you find the webhook URL to paste into the
carrier portal.

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
