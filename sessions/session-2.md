# Session 2 — Categories & segmented upload

## Objective

Module 2. Make industry category a first-class concept the whole app understands, and
make importing a CSV against one trivial. This is the module the client's actual problem
lives in: he runs a different-niche auction almost every day, and a Memorabilia collector
must never get a text about a walk-in cooler.

Backend only. No screens — module 3 owns those.

**You are running in a git worktree, in parallel with session 5a (deploy scaffolding).**
5a touches only `deployment/`, `scripts/`, `docs/` and `README.md`. Stay out of those and
there is nothing to merge-conflict on.

## Prerequisites

- `CLAUDE.md` read in full, including the escalation list.
- Modules 1 and 1b complete. Run `bash agent/gate.sh` first and show it green — 46 tests.
- Note that `tests/conftest.py` now builds the scratch schema via `alembic upgrade head`.
  Your migration is therefore exercised by every test run. If it diverges from the models,
  the suite goes red — that is deliberate.

## Scope

Build:
1. `categories` and `contact_categories` tables, with a migration
2. Seeds for the five categories
3. Extended audience resolution
4. Category CRUD API
5. Category-first CSV import: preview, commit, undo

Do NOT build:
- Any template or screen (module 3)
- `campaigns.category_id` or any campaign guardrail (module 4)
- Anything prospect- or scraper-related (deferred)
- Line-type/mobile-vs-landline detection — we do not have it and will not fake it

---

## Detailed specification

### 1. Data model

```
categories
  id            int pk
  slug          str(50)  unique, not null      e.g. "food_service"
  label         str(100) not null              e.g. "Food Service"
  color_token   str(20)  not null              one of: s1 s2 s3 s4 neutral
  sort_order    int      not null default 0
  is_active     int      not null default 1
  created_at    str(50)

contact_categories
  id            int pk
  contact_id    int fk contacts.id    ON DELETE CASCADE
  category_id   int fk categories.id  ON DELETE CASCADE
  source        str(20)  not null     one of: upload manual inferred
  confidence    float    nullable     null = certain (a human said so)
  added_at      str(50)
  UNIQUE(contact_id, category_id)
  INDEX(category_id)
```

**`color_token`, not a hex.** The four category colors were chosen by running candidate
palettes through a colorblind-separation and contrast validator; they pass all-pairs in
both light and dark, and **four distinct hues is the proven ceiling**. Storing a token
that maps to the `--s1`…`--s4` CSS variables keeps that guarantee. Storing a hex would let
someone pick a fifth colour that fails. If a future category is added it gets `neutral`.

Seeds, in this order:

| slug | label | color_token |
|---|---|---|
| `food_service` | Food Service | `s1` |
| `equipment` | Equipment & Machinery | `s2` |
| `estates` | Estates | `s3` |
| `memorabilia` | Memorabilia | `s4` |
| `general` | General Merchandise | `neutral` |

Seed idempotently — running it twice must not duplicate or error. Put it in the migration
or a seed function called from it; either is fine, but a fresh `alembic upgrade head` must
produce exactly five categories.

### 2. Audience resolution

Extend `contact_service.resolve_audience()`. Existing selectors (`all`, `list:<id>`,
`source:<name>`) keep working unchanged.

```
category:food_service                  members of one category
category:food_service,equipment        UNION of two or more
category:equipment&list:12             INTERSECTION — in the category AND on the list
```

Grammar, stated so there is no guessing:
- `&` binds looser than `,`. `category:a,b&list:12` means `(a ∪ b) ∩ list12`.
- Only one `&` is supported. More than one is a `ValueError` with a clear message.
- An unknown slug is a `ValueError` naming the slug — never a silent empty audience. A
  typo that quietly resolves to zero recipients is how a campaign gets "sent" to nobody.
- Inactive contacts are excluded, as today.

Dedup is already guaranteed by `contacts.phone` being unique, so a contact in two
categories resolves once. Prove that in a test rather than assuming it.

Extend `audience_label()` to render these readably ("Food Service", "Food Service +
Equipment", "Equipment ∩ Aug 22 preview") and `list_summaries()` to include one entry per
active category with its live count, so module 3's dropdowns have a source.

### 3. Category CRUD API

`app/routers/categories.py`: list, create, update (label, color_token, sort_order,
is_active), and a soft delete that sets `is_active = 0`.

**Never hard-delete a category** that has members — the cascade would silently drop
tagging history. Reject with a clear error and point at deactivation.

Validate `color_token` against the allowed set on write.

### 4. Import: preview → commit → undo

New `app/services/import_service.py`. `csv_source.py` keeps parsing; this owns the flow.

**Preview** — parses, resolves nothing to the database, returns:

```
{
  "rows":              482,   # data rows in the file
  "valid_phones":      461,   # normalize() + is_valid() pass
  "unusable":           21,   # rows we cannot get a usable number from
  "already_in_category": 18,  # valid numbers already tagged with the chosen category
  "opted_out":           3,   # valid numbers on the blocklist — will be skipped
  "new_contacts":      440,   # would be created
  "headers": [...], "mapped": {...}, "unmapped": [...], "sample": [...]
}
```

`valid_phones`, not "valid mobiles" — we have no line-type data and must not imply we do.

**Commit** — requires `category_id`; reject the request outright if it is missing. Upsert
contacts, tag them with the category (`source="upload"`), and create a `ContactList` named
`"{Category Label} — {YYYY-MM-DD} upload"`, disambiguated with a counter if that name
already exists that day. Return the same counts as actuals.

**Undo** — reverses exactly one batch, identified by that list's id:
- Remove the category tag **only** where `contact_categories.source == "upload"` and the
  contact is in that batch. A tag a human added by hand survives.
- Delete the batch's `ContactList` and its memberships.
- Delete a contact **only** if all three hold: it was created by this batch, it now
  belongs to no category and no other list, and it has never been messaged
  (`sms_messages` has no row for it). Otherwise leave it — an orphan contact is
  recoverable, a deleted one with message history is not.
- Never touch the blocklist. An opt-out outlives any import.

### 5. Tests

`tests/test_categories.py` and `tests/test_import.py`. Cover:
- Fresh `alembic upgrade head` yields exactly five categories, correct order and tokens
- Seeding twice is idempotent
- Each selector form, including the `(a ∪ b) ∩ list` precedence
- A contact in two categories resolves exactly once
- Unknown slug raises, naming the slug
- Two `&` raises
- Invalid `color_token` rejected on create and update
- Hard-deleting a category with members is rejected; deactivation works
- Preview counts on a fixture CSV, asserted exactly — include a duplicate row, a
  malformed number, and a number already on the blocklist
- Commit tags every valid row and creates the batch list
- Undo removes batch tags and memberships, preserves a hand-added tag, preserves a
  contact with message history, and leaves the blocklist untouched

Write a fixture CSV with deliberately messy headers (`Cell`, `Contact #`, `Company`) —
the client's real exports will not say `phone`.

---

## Acceptance criteria (demonstrate each in the transcript)

- [ ] `bash agent/gate.sh` green at the start and at the end — output shown both times
- [ ] Suite run twice in a row, green both times
- [ ] `alembic upgrade head` from empty produces exactly 5 categories — shown
- [ ] `alembic check` reports no drift between migration and models — shown
- [ ] Selector tests pass, including precedence and the dedup case — output shown
- [ ] Preview counts on the fixture CSV asserted exactly — output shown
- [ ] Undo test shows a hand-added tag and a messaged contact both surviving
- [ ] No file over 500 lines
- [ ] `status.md` and `handoff.md` updated

## Constraints

- `agent/gate.sh` and `agent.config.sh` are human-only; the hook blocks edits.
- Do not touch `deployment/`, `scripts/`, `docs/` or `README.md` — session 5a owns them.
- Do not touch `count_sms_segments()`, the pre-flight check, or billing.
- Do not add `campaigns.category_id`; module 4 owns the campaign side.
- `app/sms/` stays DB-free.
- If the spec is wrong about something, verify before following it. Session 1b was asked
  to delete a true statement on a false premise and correctly refused — do that again if
  it applies.
