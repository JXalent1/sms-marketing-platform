# Handoff

_Last updated: 2026-08-19_

## What just happened
Module 2 — categories and segmented upload. Backend only, no screens. Built in a
worktree on branch `module-2`, in parallel with session 5a on `deploy-scaffolding`;
nothing here touches `deployment/`, `scripts/`, `docs/` or `README.md`.

## State of the code
`bash agent/gate.sh` passes. 76 tests green, twice in a row (46 baseline + 30 new).
`alembic upgrade head` from an empty database produces exactly five categories,
`alembic check` reports no drift, and the migration round-trips down and back up with
the seed still at five.

## What you can now do that you could not before

**Ask for an audience by niche.** `resolve_audience()` takes four selector shapes now:

    category:food_service                    members of one category
    category:food_service,equipment          the union
    category:equipment&list:12                the intersection
    category:food_service,equipment&list:12  (a ∪ b) ∩ list12

`,` binds tighter than `&`. Exactly one `&` is allowed; two raise. An unknown slug
raises and names itself, because the alternative — a typo resolving to zero recipients —
is a campaign that reports success and reaches nobody.

Each term contributes an `IN (subquery)`, never a join. That is deliberate: a join
across `contact_categories` returns a contact in two categories twice, and the send loop
would text that person twice. `tests/test_categories.py::test_contact_in_two_categories_resolves_exactly_once`
is the guard.

Deactivating a category does **not** stop it resolving. A campaign already pointed at it
keeps working; `list_summaries()` is what hides it from the pickers. Retiring a category
should not silently empty someone's saved audience.

**Import a CSV against a category, see what it will do, and undo it.**
`/api/imports/preview` → `/api/imports/commit` → `/api/imports/{list_id}/undo`.
`category_id` is a required form field on preview and commit.

## Three things not to undo

**`color_token` is a token, not a hex.** The four hues passed a colorblind-separation
and contrast validator as a set, and four is the ceiling. `category_service` validates
against `COLOR_TOKENS` on every write and the router surfaces the failure as a 400. A
sixth category takes `neutral`. Changing the palette is on the escalation list.

**A category with members is never hard-deleted.** The FK is `ON DELETE CASCADE`, so the
delete would take every `contact_categories` row with it and say nothing — the one thing
in that table that cannot be rebuilt from a CSV. `DELETE /api/categories/{id}`
deactivates; `?hard=true` is refused with a 409 while anyone is tagged.

**Undo is subtractive.** It removes only tags this batch created (`created_tag`) that
are also `source="upload"`, so a hand-added tag survives; it deletes a contact only when
this batch created it *and* it now has no category, no other list and no message
history; and it never touches the blocklist. All three are asserted in
`tests/test_import.py::test_undo_reverses_the_batch_and_nothing_else`.

## What module 3 needs from this

- `contact_service.list_summaries()` returns every audience the pickers should offer —
  `all`, then each active category, then each list — each with a live count and a
  `kind` of `all` / `category` / `list`. That is the dropdown's data source.
- `GET /api/categories` returns each category with its `color_token`, its
  `selector` (`category:<slug>`) and its member count, plus the allowed token list. Map
  the token to the `--s1`…`--s4` CSS variables; do not read a hex from the API, because
  there isn't one.
- `audience_label()` gives the human wording for any selector and never raises.
- Point the Contacts screen's upload at `/api/imports/*`, not `/api/contacts/import` —
  the latter is the skeleton's uncategorised flow and is noted in `status.md` for
  retirement.

## Two decisions I made rather than escalated

Both are in `status.md` under "Decisions taken inside module 2" with the full reasoning.
Short version:

1. **The preview returns `duplicates` and `existing_contacts` on top of the six counts
   the spec named.** Without them the report does not add up as soon as a file repeats a
   row or contains a number we already hold but have not tagged. Every count the spec
   named kept the meaning the spec gave it.

2. **Undo needed `contact_lists.category_id` and
   `contact_list_members.created_contact` / `created_tag`.** The alternative was parsing
   the category back out of the list's name, which is the `auction_date` mistake the
   reference system made. All three are nullable and additive; the downgrade drops them;
   `ix_contacts_phone` is untouched.

## Where the counts come from

`tests/fixtures/contacts_messy.csv` is built to look like one of his real exports —
phone column called `Cell`, a second one called `Contact #`, a `Company` column nothing
maps to, one repeated row, one number that is not a number, one row with no number at
all. Against the module's fixture state it yields, exactly:

    rows 12 = valid_phones 8 + unusable 3 + duplicates 1
    valid_phones 8 = opted_out 1 + already_in_category 1 + existing_contacts 1 + new_contacts 5

They are asserted as a dict comparison, not one loose `>=` at a time. If you change the
fixture, the arithmetic identities in `test_preview_counts_are_exact` will tell you what
you broke.

## Still open

- His real CSVs, one per category. Not blocking any more — the header mapping is
  covered — but the launch import in module 8 needs them to confirm his actual headers
  and per-category counts.
- Sender number strategy, before the first live send.
- `SMS_PROVIDER` is still `console`. Flipping it is a human step and stays one.
