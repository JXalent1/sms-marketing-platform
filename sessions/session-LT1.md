# Session LT1 — Lighting buyers, national, for the 9/29 sale

**Module:** LT1 · **Depends on:** P2 (Places source), P1b (lookup gate) · **Parallel-safe with:** nothing touching `app/sources/` or `scrape_runner.py`

**Deadline.** The All Factory Fresh Inventory Auction (4,200+ boxed light fixtures, lamps,
alabaster, statues, wall art) closes **Tuesday 2026-09-29 11:30am Eastern**. The list has to
be scraped, screened, reviewed and promoted in time for a **Monday 9/28** send. That means
this session finishes tonight. Scope is cut to fit, and every cut is listed under
"Out of scope". Do not add to it.

---

## Prerequisites

- Read `CLAUDE.md`, `RULES.md`, `modules.md`, `decisions/009`, and the P2 sections of
  `status.md` and `handoff.md`.
- Read `app/sources/taxonomy.py`, `exclusions.py`, `google_places.py`,
  `app/services/scrape_runner.py`, `prospect_scoring.py`, `prospect_service.promote()`.
- **No paid API call in this session.** Places and the lookup provider stay faked. The live
  run is Jordan's (Part B).
- P2 Part A is not formally closed (mutant D2, `P2b`). That does not block LT1. Do not
  work on P2b here.

## Rulings already made (do not reopen)

- **Buyer or seller:** buyers. An independent lighting store stocks fixtures at margin; a
  lighting wholesaler or distributor is a merchant, and `decisions/009` already ruled that
  merchants in the goods being auctioned are buyers. Neither goes in the exclusion list.
- **Geography:** national, from the first run. `radius_miles=None`, stated explicitly on
  every new group (not inherited — see taxonomy.py's own note on why).
- **Source:** Google Places through the existing P2 pipeline, then the existing line-type
  gate. Mobile and VoIP are promotable (`PROMOTABLE_LINE_TYPES`); nothing about that changes.
- **Spend matters.** Every run prints what it will cost before it spends.

## A1 — the lighting groups

Add to `taxonomy.py` (new `category_slug="lighting"`), each with a `buyer_rationale`:

| slug | priority | terms |
|---|---|---|
| `lighting_retail` | 1 | lighting store, lighting showroom, light fixture store, chandelier store, lamp store |
| `lighting_trade` | 1 | lighting wholesaler, lighting distributor, lighting supply |
| `lighting_decor` | 3 | home decor store, furniture and lighting store, lamp and shade shop |

`lighting_decor` exists so Jordan can widen the net if the first two come in under ~1,000
textable. It must **not** run unless named explicitly (see A3).

If `category_slug="lighting"` needs a DB row, a list, or a migration to be valid anywhere
(scoring, promote, the queue's category label after 5i), **stop and report** rather than
inventing one. If it only needs a key in the radius map, add `"lighting": null` to the
config default and `.env.example`, as P2 did for seashells.

`taxonomy.py` is 410 lines. If the groups push it past ~470, move the groups table into
`app/sources/taxonomy_groups.py` and re-export. The 500-line rule is hard.

## A2 — metro sweeps, because a national query caps at 60 results

A Text Search for "lighting store in the United States" returns at most 3 pages × 20.
National coverage means centring the same term on many metros.

- New file `app/sources/metros.py`: `METRO_SWEEPS`, a tuple of `Sweep`s for the **50 largest
  US metro areas** by population, each with a name and a (lat, lon) centre. Comment the
  source of the list.
- The three lighting groups use `sweeps=METRO_SWEEPS`. No other group changes.
- Overlapping metros will return the same store twice. That must land as **one prospect with
  two sightings**, through the existing phone-unique path. Prove it (criterion 5); do not
  build a second dedup.

## A3 — a command Jordan can run on the box, dry-run first

Nothing calls `run_plan()` today. Add `scripts/prospect_run.py`:

```
python -m scripts.prospect_run --groups lighting_retail,lighting_trade --dry-run
python -m scripts.prospect_run --groups lighting_retail,lighting_trade --live
```

- `--groups` is required. No default, so a bare invocation cannot run the whole taxonomy.
- `--dry-run` makes no network call and prints: searches planned, worst-case Places requests
  (searches × `GOOGLE_PLACES_MAX_PAGES`), requests already used this month, the current
  `GOOGLE_PLACES_MONTHLY_REQUEST_CAP`, worst-case Places cost beyond the free allowance, and
  the lookup cap and spend-to-date. Every figure from config or the meters, none hardcoded.
- `--live` calls `run_plan()` and prints its summary plus a line-type breakdown of what it
  found (mobile / voip / landline / unknown) and the promotable count.
- `--live` refuses cleanly, with one line naming the missing setting, if
  `GOOGLE_PLACES_API_KEY` is unset or `PROSPECT_LOOKUP_PROVIDER` is `none`.
- Output is for Jordan, but still never names the carrier.

## A4 — chains out

A national chain lists a corporate switchboard and buys through a central office. Add to
`exclusions.py`, with a comment saying why: Lamps Plus, Home Depot, Lowe's, Lumens, Ferguson,
RH / Restoration Hardware, Pottery Barn, Wayfair, Crate & Barrel, Arhaus, Kirkland's,
At Home, HomeGoods, IKEA, Walmart, Target, Costco, Menards, Floor & Decor.

The existing guard still has to pass: no exclusion phrase may contain `wholesal`,
`importer` or `distribut`.

## Out of scope

- Any route, UI or scheduler for the run. The script is the interface.
- A bulk-promote UI. `promote()` already takes a list of ids; use what the queue has.
- Owner-name enrichment, website scraping, paid data vendors.
- P2b / mutant D2. The mutation harness for this session (deadline cut; LT1 gets one after).
- Any change to the line-type gate, scoring weights, or `PROMOTABLE_LINE_TYPES`.

## File list

`app/sources/{taxonomy,metros,exclusions}.py` (`taxonomy_groups.py` only if split),
`app/core/config.py`, `.env.example`, `scripts/prospect_run.py`, `tests/`,
`agent/accept-LT1.sh`, `status.md`, `modules.md` (LT1 row only).

## Acceptance

`agent/accept-LT1.sh` prints each criterion and exits non-zero on any failure.

1. `taxonomy.searches(group_slugs=["lighting_retail","lighting_trade"])` yields
   8 terms × 50 metros = **400** searches, priority order, every one with a metro centre.
2. `lighting_decor` does not appear unless named. Asserted.
3. All three groups have a non-empty `buyer_rationale` and `radius_for_group()` returns
   `None` for each.
4. `METRO_SWEEPS` has 50 entries, unique names, every centre inside the continental US
   bounding box or AK/HI.
5. Fixture run: a fake Places source returns the same place (same phone) from two metros →
   **one** prospect, **two** sightings.
6. Exclusions: every chain above is rejected; "Capitol Lighting", "Lighting Showcase of
   Tampa", "ABC Lighting Distributors" are accepted; the wholesaler/importer/distributor
   guard still passes.
7. `prospect_run --dry-run` against a test DB prints every figure A3 lists and makes zero
   network calls (patched to fail if it tries).
8. `prospect_run --live` with no key refuses with one line and exit code ≠ 0.
9. `prospect_run --live` end-to-end on fakes: prospects queued, line-type breakdown printed,
   promotable count = mobile + voip.
10. White-label: `/prospects` and `/api/prospects` rendered output, and the script's stdout,
    contain no carrier name. Check rendered output, not source.
11. `python -m pytest tests/ -q` exits 0; `alembic upgrade head` from clean; `bash agent/gate.sh`
    green twice.

## `/goal`

> Session LT1 is complete when `bash agent/accept-LT1.sh` exits 0 with every criterion
> printed and `bash agent/gate.sh` is green twice, both shown. Turn cap 35. Show the output.

## Review

One synchronous fresh-context review, short.

1. What does the dry-run's worst-case number miss? (Pages beyond the first, repeat-days skips,
   the free allowance already consumed this month.)
2. Can `--live` spend past either cap by any path?
3. Does a store found in two metros ever score or promote twice?
4. Grep the script's output paths for anything that could print a raw provider error.

## Part B — Jordan's

1. **Google Places key, Enterprise tier** (without it no result carries a phone). Set a GCP
   budget alert at $25.
2. In `/home/appuser/app/.env`: `GOOGLE_PLACES_API_KEY=...`,
   `PROSPECT_LOOKUP_PROVIDER=telnyx`, `GOOGLE_PLACES_MONTHLY_REQUEST_CAP=1500`,
   and `"lighting":null` in `PROSPECT_CATEGORY_RADIUS_MILES` if that map is pinned there.
   Restart.
3. Deploy, then on the box: `--dry-run` first. Paste the output to Cowork before `--live`.
4. `--live`. Expect roughly 20–40 minutes for 400 searches plus lookups. Worst case about
   $17.50 Places (1,500 requests, first 1,000 free) + ~$10 lookups.
5. Check the carrier balance before and after: lookups and Monday's send use the same balance.
6. Review queue Monday morning: skim the top rows, promote the promotable ones into a new list
   (suggest "Lighting buyers — national"), send.
