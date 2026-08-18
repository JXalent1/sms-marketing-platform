# Architecture

## The shape of it

```
        ┌──────────────┐
        │   Sources    │  ← swap per client (CSV, CRM API, scraper)
        └──────┬───────┘
               │  ContactRecord
        ┌──────▼───────┐
        │   Contacts   │  phone-keyed, deduped, list membership
        └──────┬───────┘
               │  audience selector
        ┌──────▼───────┐
        │  Campaigns   │  render → preflight → send loop
        └──────┬───────┘
               │  SendResult
        ┌──────▼───────┐
        │  SMS module  │  ← swap per carrier (Telnyx, Twilio, console)
        └──────┬───────┘
               │
     ┌─────────┴─────────┐
     │                   │
┌────▼─────┐      ┌──────▼──────┐
│ Webhooks │      │   Billing   │
│ delivery │      │  segments → │
│  + STOP  │      │   invoice   │
└──────────┘      └─────────────┘
```

Two seams carry all the per-client variation: **Sources** (where contacts come
from) and **SMS providers** (which carrier sends). Everything between them is
identical for every client.

## Layers

| Layer | Directory | Rule |
|---|---|---|
| Core | `app/core/` | Config, DB, auth, logging, branding. No business logic. |
| Models | `app/models/` | SQLAlchemy tables. No queries beyond trivial helpers. |
| SMS | `app/sms/` | Carrier abstraction, segments, phone rules, compliance. **No DB imports.** |
| Services | `app/services/` | Business logic. The only layer that both queries the DB and calls the SMS layer. |
| Sources | `app/sources/` | Contact ingestion plugins. Produce records; never touch the DB directly. |
| Routers | `app/routers/` | HTTP only — validate, delegate, serialize. No business logic. |
| Templates | `app/templates/` | Jinja + Tailwind. Read `brand.*`, never a client name. |

The dependency rule: **routers → services → models**, with `app/sms/` callable
from services but importing nothing above it. `app/sms/` staying DB-free is what
makes it liftable into another project wholesale.

## Request flow: sending a campaign

1. `POST /api/campaigns` → `CampaignService.create_campaign()`
   Resolves the audience, renders one message per contact, stores them `pending`,
   and records an estimated cost.
2. `POST /api/campaigns/{id}/send` → queues a background task.
3. `preflight()` checks the carrier balance can fund the whole campaign. If not,
   the campaign is **aborted before any message is sent**.
4. The send loop, per message, in this order:
   - on the blocklist → `blocked` (free)
   - out of region → `skipped` (free)
   - otherwise → carrier send (costs money) → `sent` or `failed`
5. Minutes later, delivery webhooks arrive and move messages to `delivered` or
   `undelivered`, correcting the campaign counters.
6. `billing_service` counts `('sent', 'delivered')` segments for the cycle.

## Message status model

```
pending ──► blocked      on the blocklist, never attempted    (not billable)
        ──► skipped      wrong region, filtered pre-send      (not billable)
        ──► failed       carrier rejected the send            (not billable to client)
        ──► sent ──┬──► delivered     carrier confirmed        (billable)
                   └──► undelivered   carrier dropped it       (not billed to client)
```

`sent` is provisional. Everything after it arrives asynchronously by webhook.

## What each layer must not do

- **Routers** must not query models directly for anything beyond a lookup, and
  must not contain send/billing rules.
- **`app/sms/`** must not import from `app.models` or `app.services`. If you need
  a DB write in a carrier module, you have put logic in the wrong layer.
- **Sources** must not write to the DB. `fetch()` yields records; `ingest()` in
  the base class handles normalization, dedup and persistence.
- **Templates** must not hardcode a client name, phone number, or price.

## Extension points

**Add a carrier** — subclass `SMSProvider` in `app/sms/providers/`, implement
`send()`, register in `app/sms/factory.py`, add credentials to `core/config.py`.
Implement `get_balance()` if the carrier exposes one; that enables the pre-flight
check.

**Add a contact source** — subclass `ContactSource` in `app/sources/`, implement
`fetch()`, register in `app/sources/__init__.py`. See `csv_source.py` for a
complete example and `example_api_source.py` for an API-backed one.

**Add a merge tag** — put it in `Contact.attributes`. `CampaignService.render()`
exposes every attribute key as `{key}` with no code change.

**Add a scheduled job** — register it in the `lifespan` block in `app/main.py`.

**Change the pricing plan** — edit `.env`. No code, and the UI follows.

## Database

SQLite by default; set `DATABASE_URL` to a `postgresql://` URI to switch (pool
settings activate automatically). Tables are created on startup via
`Base.metadata.create_all()`, which is adequate for a single-tenant app. Alembic
is in `requirements.txt` if you want real migrations — worth adding before the
first schema change on a live client.

Indexes exist on the columns the hot paths actually use: `contacts.phone`
(unique — the dedup guarantee), `sms_messages.campaign_id`, `.status`,
`.sent_at` (the billing range query), and `.external_id` (webhook matching).
