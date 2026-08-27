# Session 5e — Campaign-first flow & quality of life

## Objective

The tool was built around a persistent contact database segmented into five categories.
In practice Jordan works campaign by campaign off a fresh list, and the auction name is
the thing he needs on a report three weeks later.

Reshape the flow to match. Then close five gaps that cost real time or real money in the
first week of live use.

**This is a flow change, not a rebuild.** `contact_lists` and `contact_list_members`
already exist, `contact_service` already has a selector grammar
(`"list:12"`, `"category:food_service&list:12"`), campaigns already accept
`audience = "list:<id>"`, and `import_service.commit()` already records per-batch
provenance so an upload can be undone. The model is right; the routing is what is missing.

## Prerequisites

- Read `CLAUDE.md`, `RULES.md`, `status.md`, `modules.md` before starting.
- 5d and 5g are merged and deployed. 259 tests, gate green.
- **Runs in parallel with nothing.** 5g is done; 5f depends on this.
- Production holds the client's real contacts and message history. Treat it accordingly.
- Server: `ssh -i ~/.ssh/a4a_deploy appuser@67.205.180.62`, sudo limited to
  `systemctl restart a4a-sms`.
- Deploy: `SERVER=appuser@67.205.180.62 SERVICE=a4a-sms ./deployment/deploy.sh`

## Decisions already taken — do not relitigate

From `modules.md` § "Requested 2026-08-24":

- **Categories become an optional tag on upload.** Audience is the list you just
  uploaded. Tagging stays available so cross-campaign rollups remain possible; it is
  never required. `campaigns.category_id` is already nullable and the UI already renders
  "—" for it.
- **Top-up sends go out and count.** Contacts added to an already-sent campaign receive
  the same message and fold into that campaign's totals.
- **Opt-outs stay global and permanent**, the delivery-failure blocklist applies to every
  upload, and the contacts table stays underneath uploads so per-person history
  accumulates. None of these are negotiable and none of them change here.

---

# Part A — agent work

## A1. Upload a list as step one of creating a campaign

Today: Contacts → Import CSV → pick a category, then separately Compose → pick an
audience. Two screens, and the campaign has no memory of which upload fed it.

Wanted: the campaign creation flow starts with an upload. The list it creates is named
for the campaign, so a report on 8/25 reads "Italian restaurants" and not a list id.

- Reuse `POST /api/contacts/import/preview` and `/import` — do not write a second
  importer. The preview counts are the whole reason the existing flow is trustworthy.
- The created list's `name` derives from the campaign name; collisions get a suffix
  rather than an error (`ContactList.name` is unique).
- The campaign is created with `audience = "list:<id>"`.
- Selecting an existing list, or `all`, or a category selector must all still work. This
  adds a path; it does not remove one.

## A2. Optional category tag on the upload

One optional control. Untagged is a first-class outcome, not a warning state.

`contact_lists.category_id` already exists precisely for this and carries a comment
explaining that undo has to reverse exactly one category's tags — respect it.

## A3. Add a single contact from the UI

`POST /api/contacts` exists (`contacts.py:91`). There is no form.

The client will hit this the first time somebody phones the auction house and asks to be
added, and today the answer is "build a one-row CSV". Name, phone, optional email,
optional company, optional list/category. Same normalisation and blocklist checks as an
import — a manually added number that is on the blocklist must not silently become
sendable.

## A4. Top-up send on an already-sent campaign

Add contacts to a completed campaign, send them that campaign's message, and count them
in its totals.

- Runs the same pre-flight as any send — capacity, degraded provider, blocklist,
  suppression. No shortcuts because the campaign already ran once.
- Guard the obvious hazard: a top-up must never re-send to someone the campaign already
  reached. Test that explicitly.
- The campaign's totals move; a distinguishable record of the top-up is kept so a report
  can show "1,200 + 5 added 26 Aug" rather than a silently different number.
- Only for a campaign that actually sent. A draft is edited, not topped up. An aborted
  campaign stays aborted — nothing in this codebase moves a campaign back from aborted
  and this must not become the first thing that does.

## A5. The suppression window becomes a Settings field

`RECENT_CONTACT_SUPPRESSION_DAYS` is in `.env` and invisible.

Shipped at 3 days, it withheld 6,856 of 6,857 recipients across two consecutive campaigns
and took a SQL query to diagnose. It is currently **0 in production** at Jordan's
instruction — read the current value as the default, do not assume 3.

Surface it in Settings with a plain-language explanation of what it does. A rule that can
silently withhold an entire audience belongs where the person sending can see it.

## A6. The composer shows suppression before you queue

Not after. `POST /api/campaigns/preview` already returns `suppressed`
(`campaigns.py:114`) — the number exists and isn't shown at the moment of decision.

Show what is held back and when it clears: *"1,204 held back — texted in the last 3 days,
clears 10:11am."* The clearing time is computable from `max(last_messaged_at)` among the
suppressed set plus the window.

If the window is 0, say nothing. An explanation of a rule that isn't running is noise.

## A7. A campaign that sends zero aborts loudly

Two campaigns reported `completed` with `sent_count = 0`, and the campaign rail showed
them exactly like campaigns that worked.

This is the same defect 5d removed one level down — the UI shows a reason only when
`abort_reason` is set, so a blast that reached nobody read like a success. Any send path
that reaches zero recipients ends `aborted` with a reason naming the cause: everyone
suppressed, everyone blocklisted, empty audience.

Do not special-case a deliberate dry run into looking like a failure.

---

## Part A acceptance

Demonstrate each in the transcript. Self-declared completion does not count.

1. `agent/gate.sh` passes all six checks, twice.
2. Create a campaign from a CSV upload end to end: the list is named for the campaign,
   the campaign's audience is that list, preview counts match the file.
3. The three pre-existing audience paths — existing list, `all`, category selector —
   still create working campaigns. Show all three.
4. An untagged upload produces a campaign with `category_id` NULL that sends normally.
5. A contact added through the new form is normalised, blocklist-checked, and appears in
   its list. A blocklisted number added by hand does not become sendable.
6. A top-up send reaches only the new contacts, never the already-reached ones, and the
   campaign's totals reflect both. Show the guard failing when reverted.
7. The suppression window is settable in Settings and takes effect without a restart.
8. With the window > 0, the composer names the held-back count and the clearing time
   before the campaign is queued. With it at 0, nothing is shown.
9. A campaign whose audience fully suppresses ends `aborted` with a reason on the record,
   and the campaign rail renders that reason.
10. New tests go red against the pre-fix tree — and per `CLAUDE.md`, that means a
    **behavioural mutation run** in the current API, not an import failure. Follow
    `agent/mutate-5g.py`; wire it in as check 8b.
11. After deploy: seven screens 200 over HTTPS, no carrier name or raw provider payload
    in any rendered page or API response.

Wire into a `/goal` stop condition with a turn cap, then one **synchronous** fresh-context
review. Do not fan out background reviewers — in 5g three of four produced nothing across
a dozen idle cycles while the synchronous one found every defect.

## Constraints

- Do not touch `.env`, `.env.production`, `agent/gate.sh`, `agent.config.sh`.
- **Do not send SMS. Do not modify, delete or re-import contact data.**
- Do not weaken the pre-flight checks. A4, A6 and A7 all touch code near them; every one
  of those changes strengthens or surfaces, none relaxes.
- No source file over 500 lines. `campaign_service.py` has crossed it twice already —
  `campaign_dispatch.py` and `factory.py` are where things went, follow that grain.
- `app/sms/` stays DB-free.
- No hardcoded commercials.

## Explicitly out of scope

- Short links, click stats, per-campaign reports, history screens — all 5f.
- Line-type screening at import. Endorsed in decision 003, still unscheduled, and big
  enough to deserve its own session.
- Re-adjudicating blocklist rows.
- The prospecting engine and data streams.
