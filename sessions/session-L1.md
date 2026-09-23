# Session L1 — LiveAuctioneers bidder source

**Module:** L1 · **Depends on:** 5n · **Parallel-safe with:** P2b, 5k, B1c

Port the LiveAuctioneers partner-portal scraper into A4A as a **contact source**. Not the
application around it — A4A already has better versions of everything else in that folder.

Source material: `~/Desktop/liveauctioneers-scraper` (connected). It is a whole second app
— its own campaigns, Telnyx, Stripe, blocklist, dashboard. **Three files port. The rest is
reference only and must not be copied.**

---

## Prerequisites

- Read `CLAUDE.md`, `RULES.md`, `modules.md`, `status.md`, every file in `decisions/`.
- 858 tests, gate green twice as of 5n (`9c670dc`).
- Read `reference/original-server-source/PHASE-1-FIX-SCRAPER.md` and
  `PHASE-2-AUTO-SCRAPER.md` in the skeleton root — they record what broke in this
  scraper's production life.
- **Do not run the scraper against LiveAuctioneers during this session.** Build against
  recorded fixtures. The first live run is Jordan's, with his credentials.

## What this is, and what it is not

It logs into **A4A's own partner portal** — `partners.liveauctioneers.com/house/<id>/bidders`
— and reads the bidders who registered for his sales.

**These are his own customers, not prospects.** None of the prospecting rules apply: no
buyer rationale, no review queue, no `seller_or_consignor`. They go into the contact list
the way a CSV upload does.

It also **reverses a decision from 2026-08-18** — "contacts arrive as CSVs he sends; no
marketplace scraping for existing bidders." That decision was about scraping *marketplaces*
for strangers. Reading his own registered bidders out of his own account is a different
act, and the upload flow stays exactly as it is. Record the reversal in
`decisions/` before building.

## A1 — port three files, behind A4A's own interface

From the source folder:

- `app/services/base_scraper.py` — browser lifecycle, retry, paging, debug screenshots
- `app/services/scraper.py` — the LiveAuctioneers selectors and navigation
- `app/platforms.py` — the platform registry

They land as a `ContactSource` under `app/sources/`, and `app/sources/example_api_source.py`
says in its own docstring that this is where it belongs. Delete that placeholder.

**The layering rule is not negotiable.** `app/sources/` produces records; it never writes
to the database. The base class handles normalization, dedup and persistence. The ported
`_save_profile` writes rows directly — that has to be unwound into yielded records, or the
source is in the wrong layer and the next session inherits it.

Keep `platforms.py`'s registry. He sells on Proxibid and AuctionZip too, and the registry
exists so a second site is one entry plus one subclass. Do not collapse it to a constant.

## A2 — bidders become contacts, and the behaviour goes beside them

Phone is the identity. `contacts.phone` carries the unique index that **is** the dedup
guarantee — escalation item 4. Do not touch it, and do not add a path that could insert a
duplicate. Use the existing upsert.

A bidder with no phone is not a contact. The scraper saves them anyway (`profiles_no_phone`
is counted); decide whether A4A keeps them at all and say why.

**The behavioural fields do not belong on `contacts`.** `card_on_file`,
`auctions_attended`, `bids_placed`, `items_won`, `payment_rate`, `avg_hammer_price`,
`dispute_history`, `member_since` — that is a bidder profile, not a contact attribute, and
stuffing them onto `contacts` is the overloaded-column mistake `CLAUDE.md` opens with. Give
them their own table keyed to the contact, with the platform slug and the scrape timestamp.

**This is the most valuable thing in the port and it should be said out loud in the
docstring:** it makes "everyone who has won at least three items" or "average hammer over
$250" an audience. That is the segmentation the category model was reaching for. Building
those audiences is **not** this session — but the data has to land in a shape that allows
them, which means real columns and real types, not a JSON blob.

## A3 — screening and suppression are unchanged

A scraped bidder goes through the same line-type gate as anything else if screening is on.
A landline here is still a dead number paid for on every send.

The blocklist, the opt-out matcher and the suppression window apply without exception. A
bidder who opted out and then registers for another auction stays blocked — assert it.

## A4 — the schedule, and the box

The source app runs daily at 09:00 Eastern on its own APScheduler with an explicit
`timezone=eastern`. A4A now has its own timezone (session 5m) — use **that** setting, from
one place. Two definitions of the client's zone is how the next drift starts.

**The memory ceiling is the real risk.** The droplet is 1 vCPU / 2 GB and already runs the
app; `CLAUDE.md` records the prior system at 1.6 GB RSS with 17 orphaned drivers on a
3.9 GB box. Required:

- One scrape at a time. `max_instances=1`, and a job already running refuses rather than
  queues.
- A hard timeout that kills the browser, not just the job. `PROSPECT_JOB_TIMEOUT_SECONDS`
  is the existing precedent.
- The context and the Playwright driver close in a `finally`, on every path including the
  timeout one. The ported `shutdown()` already does this — keep it and test it.
- Measure peak RSS during a fixture run and put the number in `status.md`. If it does not
  leave headroom on 2 GB, say so plainly rather than shipping and finding out at 9am.

## A5 — credentials

`LA_USERNAME`, `LA_PASSWORD`, `LA_HOUSE_ID` in `.env` — **human-only, a PreToolUse hook
blocks agents.** Name them in `.env.example` with a comment and stop there.

The scraper keeps a logged-in browser profile on disk (`BROWSER_PROFILE_DIR`). Decide where
that lives on the box, make sure it is outside anything served, and make sure a backup
never picks it up — it is a live session for his auction account.

Nothing about LiveAuctioneers reaches a client-facing surface. The white-label rule is
about the SMS carrier, and LA is the client's own platform, so its name on *his* screens is
fine — but the credentials, the profile directory and any raw page text are not.

## Out of scope

- Everything else in that folder. Its campaigns, Telnyx, Twilio, Stripe, blocklist,
  dashboard and models are superseded.
- Proxibid and AuctionZip scrapers. The registry makes room; this session fills one entry.
- Audiences built on the behavioural fields. Land the data; build the segments later.
- The prospecting pipeline. Bidders are not prospects and never enter the review queue.
- `.env` and `.env.production`.

## File list

    app/sources/liveauctioneers.py          (new — the LA subclass)
    app/sources/auction_scraper_base.py     (new — the ported base)
    app/sources/platforms.py                (new — the registry)
    app/sources/example_api_source.py       (deleted — this replaces it)
    app/sources/__init__.py
    app/models/bidder_profile.py            (new)
    app/models/__init__.py
    app/services/contact_service.py
    app/services/scrape_runner.py
    app/core/config.py
    app/main.py
    requirements.txt                        (playwright — named here, so authorized)
    .env.example
    alembic/versions/
    tests/
    agent/accept-L1.sh
    agent/mutate-L1.py

## Acceptance

1. **No network call in the suite.** Playwright is replaced; every page is a fixture.
2. **A fixture run of N bidders produces N contacts**, and a second identical run produces
   0 new and no duplicate phone.
3. **`app/sources/` writes nothing to the database.** Asserted structurally, the way the
   existing layering check works.
4. **The behavioural fields land in their own table** with the right types, and a contact
   with no profile row still works everywhere.
5. **A bidder who is blocked stays blocked** after re-registering.
6. **A scrape already running refuses a second.**
7. **The browser closes on every path** — success, failure, and timeout. Assert the
   timeout path specifically.
8. **Peak RSS from a fixture run is recorded in `status.md`.**
9. **The schedule reads the app's timezone setting**, not its own.
10. `bash agent/gate.sh` green, twice.
11. **Mutation run**, verified-pristine tree, same verdict on two consecutive invocations,
    both shown.

### Mutations

- Let the source write a contact row directly.
- Skip the `finally` on the timeout path.
- Allow a second concurrent scrape.
- Put a behavioural field on `contacts`.
- Bypass the blocklist for a scraped bidder.
- Give the scheduler its own hardcoded timezone.

## `/goal`

> Session L1 is complete when `bash agent/accept-L1.sh` exits 0 with every criterion
> printed, `bash agent/gate.sh` is green twice, and the mutation run reports the same result
> on two consecutive invocations with both shown. Turn cap 50. Show the output.

## Review

One synchronous fresh-context review. No spawned reviewers.

1. **What does `app/sources/` touch that it should not?** Trace every write.
2. **What happens when a selector changes?** LA owns that page. Does the job fail loudly,
   or quietly return zero bidders — and which does the code do today?
3. **What is in the set before the dedup runs?** The port brings its own `profile_hash`;
   A4A dedups on phone. Two dedup keys on one path is how a duplicate appears.
4. **Peak memory, measured not estimated**, and what happens on 2 GB when the app is also
   serving.
5. **The scan's own first version.** Run criterion 7 against a deliberately broken teardown.

## Part B — Jordan's

1. `LA_USERNAME`, `LA_PASSWORD`, `LA_HOUSE_ID` in `/home/appuser/app/.env`.
2. `playwright install chromium` and `playwright install-deps chromium` on the droplet.
3. **First run by hand, watching memory.** `free -m` before and during. If it is tight, the
   answer is a bigger droplet, not a smaller scrape.
4. Decide whether the same account is scraping while a campaign is sending — both want the
   box.
