# A4A SMS platform — Cowork handoff

Paste this whole file as the first message in a new Cowork chat.

---

You are picking up a live project mid-flight. Read this, then read the repo docs it points
at before doing anything. The previous chat compacted several times, so **the repo is the
source of truth, not any conversation.**

## Who and what

I'm Jordan Dvora. I run a marketing agency. This is a **white-label SMS marketing
platform** I built for my client **Auctions4America (A4A)** — a Fort Lauderdale auction
house that runs a different-niche auction almost every day. I also run their Facebook ads.

The platform is **live and in daily use by the client**. It came from a skeleton extracted
from a prior client build for **Williamson Auction Gallery** (wagmarketingbot.com), which
still matters — see "What's next" below.

## Where the code lives

```
~/dev/sms-marketing-platform-skeleton/sms-marketing-platform
```

Read these first, in this order:

- `RULES.md` — how we work together. Non-negotiable.
- `CLAUDE.md` — 22 accumulated lessons from this build. Every one cost a session.
- `modules.md` — the plan of record. Session status, dependencies, file lists.
- `status.md` — current state and "found while working" items.
- `decisions/` — six resolved escalations, each with the full reasoning.
- `sessions/` — every session spec, 1 through P2.
- `A4A_BUILD_PLAN.md` — the prospecting plan of record and the seashells section.

## Your role

**Cowork writes prompts and plans. Claude Code writes the code.** From `RULES.md`:

> When asked for code directly, redirect: "Let's capture that in the session prompt so the
> coding agent runs it properly."

One module = one session = one prompt file in `sessions/`. Do not pre-generate future
session prompts — one at a time, after the previous one completes and verifies. Commit the
spec **before** the coding session starts; a session once built without its spec and lost
five hardening items.

You maintain the plan docs. You rule on escalations in `decisions/`. You do not write
application code.

## Hard rules, learned expensively

- **White-label.** The carrier's name never appears anywhere a client can see. Leaks get
  assembled at runtime, not written as literals — check rendered HTML and JSON, not source.
- **Never run `git` from Cowork on this repo.** `device_bash` can't `unlink`, so git leaves
  a `.git/index.lock` and wedges the repo. Jordan runs all git commands.
- **`.env`, `.env.production`, `agent/gate.sh` and `agent.config.sh` are human-only.** A
  PreToolUse hook blocks agents. Jordan edits them by hand.
- **No TCPA or compliance lectures.** Jordan has asked for this explicitly. Frame risk in
  business terms — deliverability, carrier filtering, wasted spend — or not at all.
- **One synchronous in-session review, never spawned reviewers.** Spawned ones have
  produced nothing twice across a dozen idle cycles. Work the ten lenses directly.
- **Every session ends with a behavioural mutation run** on a scratch tree verified
  byte-identical to the repo. "New tests fail against the pre-fix tree" is a weak proof —
  they usually fail at import.

## Infrastructure

| | |
|---|---|
| App | https://app.onlineauctions.co |
| Short links | https://bida4a.com (registered to A4A) |
| Droplet | 67.205.180.62, Ubuntu 24.04, 1vCPU / 2GB |
| App path | `/home/appuser/app` (**not** `/srv/a4a`) |
| Service | systemd `a4a-sms` |
| Deploy | `SERVER=appuser@67.205.180.62 SERVICE=a4a-sms ./deployment/deploy.sh` |
| SSH (app) | `ssh -i ~/.ssh/a4a_deploy appuser@67.205.180.62` — sudo limited to `systemctl restart a4a-sms`, no journal access |
| SSH (root) | `ssh root@67.205.180.62` — needed for nginx and certbot |
| DB | SQLite at `data/app.db`, ~7.6MB, Alembic at head |
| Sender | +1 954-738-2462, Telnyx 10DLC, brand and campaign both approved |
| Stack | Python 3.12, FastAPI, SQLAlchemy 2.0, Jinja2 + Tailwind, pytest |

**Commercials:** no monthly fee, 10,000 segments included free per month, $0.015 per
segment after. Our true all-in cost is roughly **$0.005/segment** — the configured
`WHOLESALE_COST_PER_SEGMENT = 0.009` is a deliberate over-reserve for the capacity guard,
not a rate. Never show our cost to the client.

## What's built and live

Sessions 1 through 5f plus a host guard, then P1 and P1b. **544 tests, gate green,
everything deployed, no open decisions.**

- Campaign-first flow: upload a CSV as step one, the list is named for the campaign
- Per-recipient short links with click attribution — *which* buyers clicked, not how many
- Per-campaign reports, campaign history, per-contact message history
- Degraded-provider refusal: a box whose carrier failed can't send or bill
- Delivery-webhook auto-blocking of dead numbers
- Prospect pipeline: holding pen, line-type gate, review queue, buyer-rationale enforcement
- Telnyx line-type lookup with spend cap and persistent cache

**Live numbers:** ~4,200 reachable mobiles. 2,959 numbers permanently blocked as landline
or invalid — one early campaign came back 39% undelivered, which is why line-type
screening now gates everything.

## What's next — two things

### 1. P2 — Google Places source, including seashells

`sessions/session-P2.md` is written and committed. Not started.

**The client specifically asked for seashell businesses** — they perform unusually well for
him. `A4A_BUILD_PLAN.md` has a Seashells section. The key trap, already recorded: "people
who sell seashells" sounds like a seller but a **shell shop buys inventory to stock its
shelves** — a buyer. **Shell wholesalers and importers are sellers** and are excluded.

The governing rule for all prospecting: **buyers, never sellers.** He has plenty of
consignors. Every search term ships with a written buyer rationale, the review queue
displays it, and `seller_or_consignor` is a first-class permanent reject reason.

**Before P2 can run live, Jordan needs a Google Places API key with the Enterprise tier**
(the phone number only comes back at that tier) and to set `PROSPECT_LOOKUP_PROVIDER=telnyx`
in `.env`. Lookups and sends draw on the same Telnyx balance, so a big scrape can fail the
next morning's campaign pre-flight.

### 2. NEW — retire the category model for Williamson-style named lists

**This has not been specced. It's the immediate next planning job.**

Jordan's dad prefers how wagmarketingbot.com (the Williamson build) handles lists: a single
flat dropdown of past lists, each titled by the campaign that first used it, with
"⭐ ALL BIDDERS — MAIN LIST" pinned at the top. Newest first. **No industry sections at all** —
you either upload a fresh list or pick one you used before.

This retires the five-category model (Food Service, Equipment & Machinery, Estates,
Memorabilia, General Merchandise) that the entire build originally existed to serve. The
trigger was real: his dad asked "if I launch a campaign for a yacht auction, where do I put
the list?" — and the honest answer was that none of the five fit.

Most of the groundwork is done. Session 5e already made the category optional and made
upload-per-campaign the primary flow. What remains is mostly UI: remove the category picker
from the audience selector, present a flat recency-sorted list of named lists with ALL
BIDDERS pinned, and decide what happens to the existing category tabs on the Contacts page
and the five category cards on the dashboard.

**Ask Jordan before speccing:** whether categories should be hidden but retained
underneath (keeping cross-campaign rollups possible) or removed entirely. Note that the
colour palette is validated at four hues plus neutral and is already full, which is one
more argument against categories as the organising principle.

## Open items, none blocking

- Revoke the `a4a-deploy-agent` SSH key — real contact data is on the box now
- `agent/notify.sh` reads the same carrier credential it's meant to warn about
- `sudo usermod -aG adm appuser` so logs are readable without root
- Nobody has clicked through the newer screens with human eyes — upload composer,
  add-contact form, campaign report, both history screens
- `A4A_BUILD_PLAN.md` may have lost its original content at some point; only the
  prospecting section is in it. `git log --oneline -- A4A_BUILD_PLAN.md` would say.

## How to start

Read `RULES.md`, `CLAUDE.md`, `modules.md` and `status.md`. Then ask me the category
question above, and let's spec the list-model change. P2 can run in parallel — its files
are disjoint.
