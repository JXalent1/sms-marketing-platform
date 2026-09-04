# Session P2 — Google Places source

## Objective

The first real source. P1 built the pipeline, the line-type gate and the review queue;
this fills them.

Everything here spends money — Google per request, Telnyx per number — so the caps and
the dedup are not hygiene, they are the feature.

## Prerequisites

- Read `CLAUDE.md`, `RULES.md`, `status.md`, `modules.md`, and the Prospecting plan of
  record in `A4A_BUILD_PLAN.md` **including the Seashells section**.
- P1 and P1b are merged and deployed. 544 tests, gate green twice.
- Production holds the client's real contacts and message history.
- Deploy: `SERVER=appuser@67.205.180.62 SERVICE=a4a-sms ./deployment/deploy.sh`
- **A Google Places API key is Jordan's to obtain** (Part B). Build against fixtures; the
  key blocks the first live run, not the work.

## The rule this session serves

**Buyers, never sellers.** He has plenty of consignors; a supplier on this list costs
money to text and hands a competitor a seat on his own channel.

Two inversions that have already caught us out, both recorded in the plan of record:

- **Estate liquidators are sellers.** They consign to him. Excluded.
- **"People who sell seashells" are buyers.** A shell shop buys inventory to stock its
  shelves. But **shell wholesalers and importers are sellers** — they consign surplus.

If a taxonomy entry cannot survive being read back as "this person would raise a paddle
because ___", it does not ship.

---

# Part A — agent work

## A1. The taxonomy

Category → search terms, and **every term carries a written `buyer_rationale`**. P1 made
that field non-nullable and refuses a record without one; this is where the values come
from. The rationale reaches the review queue, so write it for the person reviewing, not
for the code.

Bias every term toward **businesses that answer their own phone**. A restaurant lists a
landline; a food truck lists the owner's cell, because the business is the person. That
distinction is worth more than any other targeting choice here — it is the difference
between a 30% and a 70% mobile rate, and every landline costs $0.0025 to discover and
returns nothing.

Seed groups from the plan of record: food service, equipment, estates, memorabilia,
general, marine, **and the seashell group** — shell and beach shops, coastal souvenir and
gift shops, nautical decor retailers, aquarium and reef shops, jewellery and craft
suppliers.

**The exclusion list is part of the taxonomy, not a filter bolted on:** other auction
houses, estate-sale companies, estate liquidators, appraisers, consignment galleries,
"we buy houses" operators, shell wholesalers and importers. Match on business name, and
make the list one shared definition — `CLAUDE.md` records what happens when the same
reserved set gets copied into three layers.

## A2. The source

`GooglePlacesSource(ProspectSource)`. `fetch()` yields, the base persists. It never
touches the database and never decides what is textable.

- **Enterprise tier is required** — the phone number only comes back at that tier.
- Text Search with pagination; up to 20 places per request, three pages per query.
- Per-category radius from config: food service / equipment / general 150 miles,
  estates 100, memorabilia and marine national. **Seashells runs twice** — a dense
  Florida sweep and a national sweep, different runs with different radii, because the
  trade centres on the Gulf coast but collectors are everywhere.

## A3. Spend control

Two meters, and they are independent:

- **Google**: $35 per 1,000 Text Search requests, first 1,000 a month free. A hard
  monthly request cap in config, checked before the call, refusing cleanly and logging
  what was skipped.
- **Telnyx lookups**: P1b's cap already exists. Do not duplicate it — call through it.

**Dedup before spending, not after.** A place already in `prospects`, already a `Contact`,
already rejected, or already in `phone_lookups` costs nothing to skip and $0.0025 to
rediscover. Assert on call counts that a second identical run makes no paid call of
either kind.

Record per job what was attempted, produced and spent. A scrape whose cost cannot be
attributed to its results is a scrape nobody can decide to repeat.

## A4. Back-port the pristine check

`agent/mutate-P1.py` and the three earlier harnesses do not verify their scratch trees,
although `CLAUDE.md` states that every harness does and prints `SCRATCH VERIFIED PRISTINE`.
P1's protection came from `accept-P1.sh` doing `rm -rf` before the rsync — weaker, and it
only holds when the harness runs through that script.

Back-port the real check to all four. A documented guarantee that is true in one place is
worse than no guarantee, because it is quoted as though it were true everywhere.

---

## Part A acceptance

1. `agent/gate.sh` passes all six checks, twice.
2. Against recorded fixtures, N API results produce N prospects; **a second run produces
   0 new and makes 0 paid calls of either kind** — assert on call counts.
3. The radius rule is asserted per category, and the seashell group runs both sweeps.
4. Exceeding the Google request cap stops the job cleanly and logs what was skipped. Show
   it refusing mid-run without partial corruption.
5. Every prospect carries its search term and that term's buyer rationale, and the review
   queue renders both.
6. Every excluded business type is rejected by name before it becomes a prospect — one
   test per exclusion, including shell wholesalers.
7. A place already a Contact, already rejected, or already looked up is skipped before any
   spend.
8. All four earlier harnesses verify their scratch trees and print `SCRATCH VERIFIED
   PRISTINE`.
9. Behavioural mutation run on a verified-pristine tree. At minimum: revert the exclusion
   list, the pre-spend dedup, and the request cap.
10. After deploy: all screens 200 over HTTPS, no carrier name, raw provider payload or
    wholesale figure anywhere client-facing.

`/goal` stop condition with a turn cap, then review yourself in session against the ten
lenses. No spawned reviewers.

## Constraints

- Do not touch `.env`, `.env.production`, `agent/gate.sh`, `agent.config.sh`.
- **Do not send SMS. Do not modify, delete or re-import contact data.**
- **Do not make a real paid API call** — not Google, not Telnyx, not to "just check the
  key works". Fixtures only. The first live run is Jordan's.
- Do not weaken any pre-flight or compliance check.
- No source file over 500 lines. `app/sms/` stays DB-free.

## Explicitly out of scope

- Registries, marketplaces and enrichment — P3.
- Promoting anything into the live contact list. That is a human at the review queue.
- Turning on `PROSPECT_LOOKUP_PROVIDER`. Jordan's, in Part B.

---

# Part B — Jordan

1. **Get a Google Places API key**, Enterprise tier enabled, with a billing budget alert
   set below the configured cap as a second net.
2. Add `GOOGLE_PLACES_API_KEY` and set `PROSPECT_LOOKUP_PROVIDER=telnyx` in
   `/home/appuser/app/.env`, then restart. Until both are set, a scrape produces prospects
   whose line type is `unknown` — and `unknown` is not promote-eligible, by design.
3. **First live run: one category, smallest radius, cap set low.** Read the review queue
   before scaling. The question is not "did it find businesses" but "would these people
   bid?"
4. Watch the Telnyx balance. Lookups and sends draw on the same pot, so a large scrape can
   fail the next morning's campaign pre-flight.
