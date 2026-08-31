# Session P1 — Prospect pipeline

## Objective

A holding pen between a scraper and the textable list, with the line-type gate that makes
scraping economic and the review step that keeps sellers out.

**No source implementations.** This session builds the machinery every future source
plugs into. If it is built right, adding Google Places is a class and a taxonomy; if it
is built wrong, every source inherits the damage.

## Prerequisites

- Read `CLAUDE.md`, `RULES.md`, `status.md`, `modules.md`, and the **Prospecting — plan
  of record** section of `A4A_BUILD_PLAN.md` in full before starting.
- The platform is live and stable: 449 tests, gate green, all decisions resolved.
- Production holds the client's real contacts and message history.
- Server: `ssh -i ~/.ssh/a4a_deploy appuser@67.205.180.62`, sudo limited to
  `systemctl restart a4a-sms`.
- Deploy: `SERVER=appuser@67.205.180.62 SERVICE=a4a-sms ./deployment/deploy.sh`

## The rule this session exists to enforce

**We are looking for people who BUY at his auctions, never people who sell into them.**

He has plenty of consignors. A supplier on this list costs money to text, dilutes the
audience and hands a competitor a seat on his own marketing channel. The plan of record
explains why this is enforced in three places rather than trusted once. Build all three.

---

# Part A — agent work

## A1. Tables

`prospects`, `scrape_jobs`, `phone_lookups`. Alembic revision, additive only.

Every prospect retains, permanently and non-nullably where possible:

- `source_url`, scrape timestamp, and `raw_payload` — so a bad record can be traced to
  the search that produced it rather than argued about
- the **search term** that found it and that term's **buyer rationale**, carried through
  to the review queue
- `promoted_contact_id`, null until promoted

## A2. `ProspectSource`, mirroring `ContactSource`

Same shape as `app/sources/base.py`: `fetch()` yields records, the base persists. A source
never touches the database directly, and never decides whether something is textable.

Read that file's module docstring before writing this one. It explains why the seam
exists — the reference system wired a 930-line scraper straight into the models and the
scheduler, so reuse meant deleting a third of the app. Do not reintroduce that.

## A3. Job runner

- Hard timeout.
- **Cleanup in a `finally` block.** The reference system leaked one browser process per
  daily run: 17 orphans, 1.6 GB RSS on a 3.9 GB box. This box has 2 GB.
- A job records what it attempted, what it produced, and what it cost.

## A4. Line-type lookup — the gate

Behind a carrier-agnostic interface, with a **persistent cache keyed on E.164**. A number
already looked up must never be looked up again.

Telnyx MCC/MNC is $0.0025 per query. The cache is what keeps that a one-time cost per
number rather than a recurring one.

**Landlines are excluded from promote-eligible by default.** Not deleted — recorded, with
their line type, so the same number scraped from a second source is known instantly.

This is the item that makes scraping economic at all: one live campaign produced 2,526
not-routable failures, 39% of the send, all paid for.

## A5. Scoring

Rank the queue so review is worth doing:

- line type (mobile first, and it is the dominant term)
- category confidence
- distance against the category's radius rule
- multi-source corroboration — a business found by two different searches is more likely
  real and more likely to match its category

## A6. Review queue

Sortable, bulk-select, promote-into-category, reject-with-reason.

- **The buyer rationale for the search term that found it is shown on every row.** The
  reviewer is answering "would this person bid?", not "is this a real business?"
- `seller_or_consignor` and `competitor` are first-class reject reasons.
- **Every rejection suppresses permanently.** Re-ingesting the same record from any source
  must not resurface it.
- A per-term rejection breakdown, so a search term producing mostly sellers is visible and
  can be removed. The system should teach us which searches find the wrong side of the room.

## A7. Promote

- Creates a `Contact` tagged with the chosen category, links `promoted_contact_id`.
- Runs the same guards as any other ingestion path: E.164 normalisation, blocklist refusal,
  opt-out check. A prospect is not a special case that gets to skip them.
- A blocklisted or opted-out number cannot be promoted, and the refusal says why.

---

## Part A acceptance

Demonstrate each in the transcript. Self-declared completion does not count.

1. `agent/gate.sh` passes all six checks, twice.
2. Promote creates a Contact tagged with the chosen category and links
   `promoted_contact_id`.
3. Reject suppresses permanently — re-ingest the same record from a *different* fake
   source and show it does not reappear.
4. A deliberately hung fake job is killed at timeout, and cleanup is **asserted to have
   run** — not assumed.
5. Landlines are excluded from promote-eligible by default.
6. A repeat lookup hits the cache and makes **no** call — assert on call count, not on
   elapsed time.
7. A blocklisted number and an opted-out number both cannot be promoted.
8. Every prospect retains `source_url`, scrape timestamp, `raw_payload`, search term and
   buyer rationale.
9. The review queue renders the buyer rationale, and the per-term rejection breakdown
   distinguishes `seller_or_consignor` from other reasons.
10. Behavioural mutation run per `CLAUDE.md` — mutations inside the current API, not an
    import failure. Follow `agent/mutate-5f.py`. At minimum: revert the landline
    exclusion, the cache, the permanent suppression, and the promote-time blocklist check.
11. After deploy: all screens 200 over HTTPS, no carrier name or raw provider payload in
    any client-facing page, API response or export.

Wire into a `/goal` stop condition with a turn cap, then do the review **yourself, in
session**, against the ten lenses. Spawned reviewers have produced nothing on this project
twice — see `CLAUDE.md`.

## Constraints

- Do not touch `.env`, `.env.production`, `agent/gate.sh`, `agent.config.sh`.
- **Do not send SMS. Do not modify, delete or re-import contact data.**
- **Do not call any paid API.** Line-type lookup is behind an interface and tested against
  a fake. No real Telnyx lookup calls, no Google Places calls, no spend.
- Do not weaken any pre-flight or compliance check.
- No source file over 500 lines. `app/sms/` stays DB-free.
- No hardcoded commercials, no hardcoded domain.

## Explicitly out of scope

- **Any actual source implementation.** Google Places is P2, registries are P3. A fake
  source for tests is in scope; a real one is not.
- The opt-in landing page and cold-send guardrails (old module 7).
- Quiet hours by recipient timezone.
- Changing what the client is charged.
