# Session 5m — the composer's audience panel, and time in Eastern

**Module:** 5m · **Depends on:** 5j · **Priority: ahead of P2b, 5k, B1c.**
Both defects are live, client-facing, and were found by the client's own operator.

(Numbered 5m rather than 5l: `5l` and `51` are indistinguishable in most terminals, and
this project has already lost time to a bare numeric that meant something else.)

---

## Prerequisites

- Read `CLAUDE.md`, `RULES.md`, `modules.md`, `status.md`, every file in `decisions/`.
- Run inside the project venv. `npm run build:css` before serving.
- 788 tests, gate green twice as of B1b.
- **Escalation item 5 is in play for A2**: what time a message goes out decides whether a
  real person gets a text they did not expect. Implement what this spec says; do not tune
  the rule.

---

## A1 — the composer panel describes two audiences at once

**Observed on production, 2026-09-08, by Jordan's father, mid-campaign.**

The audience dropdown has a named list selected — `09/09, 6:00 PM Private Record
Collection … — 443 contacts`. The summary panel beside it reads:

    Audience     ⭐ ALL BIDDERS — MAIN LIST
    Recipients   10,146
    Opted out    3,460
    Segments     443
    Estimated cost  $0.00

Three claims, three different audiences. `Recipients 10,146` is the entire database.
`Segments 443` matches the **selected list**, not 10,146 recipients at one segment each.
The `Audience` row names a third thing. The panel cannot be right.

### What is known, and what is not

`paintAudienceSummary()` reads `dataset.label` off `audience.selectedOptions[0]`, and it
**is** wired to the select's `change` event (`_composer-script.html:228`), so the obvious
explanation — a missing repaint — is not it. Do not stop at that hypothesis.

Two facts to work from rather than around:

- `sumSegments` has **two writers**: `refreshPreview()` (line ~155, from
  `/api/campaigns/preview`) and the pre-flight report path (line ~293, from
  `report.counts.total_segments`). `sumRecipients` has one. **A panel with two writers on
  one row and one on its neighbour is how two audiences appear side by side**, and it is
  the defect this codebase has met repeatedly under a different name.
- `refreshPreview()` is debounced and `await`s. Nothing sequences the responses, so a
  slow reply for a large audience can land after a fast reply for a small one and
  overwrite it. `composerMode === 'upload'` returns early; nothing else guards ordering.

**Find the mechanism before fixing it.** Reproduce it against a real database — the
production shape is ~10,146 active contacts, 3,460 opted out, and a 443-contact list — and
say in `status.md` what actually happened. A fix that makes the symptom go away without an
explanation is the thing `CLAUDE.md` warns about three separate times.

### What must be true afterwards

- **One writer per row**, or one function that owns the whole panel and is the only thing
  that writes any of it. Every figure in it comes from one response about one audience.
- **A stale response can never paint.** Tag each request and discard a reply that is not
  the newest, or serialise them. Assert it with a test that resolves an old response after
  a new one.
- **The panel and the send agree.** The audience the panel describes is the selector
  `POST /api/campaigns` would receive at that instant. This is the criterion that matters:
  the panel exists to be trusted before a blast to ten thousand people.
- **`Segments` and `Recipients` are consistent with each other** for a single-segment
  message: a test that asserts total segments equals recipients × segments-per-message for
  the selected audience catches this class outright.

### Whether a send would have gone to 443 or 10,146

Establish this and put the answer in `status.md`. `create_campaign` takes the audience from
the form at submit time, and the $0.00 cost with 443 segments suggests the money path was
on the list — but *suggests* is not good enough for a question with 10,146 people on the
other side of it. If any path could have sent to the wrong audience, that is a second
defect and it outranks the display.

---

## A2 — the application has no timezone, and the box runs UTC

**A scheduled campaign fires four hours early. This is live.**

`due_campaign_ids()` compares `Campaign.scheduled_at` against `datetime.now().isoformat()`.
`scheduled_at` arrives from an `<input type="datetime-local">`, which submits the wall-clock
the operator typed with **no zone attached** — `2026-09-09T18:00` for six in the evening.
The droplet's clock is UTC: APScheduler prints its job times as UTC, and there is **no
`TZ`, `tzinfo`, `ZoneInfo`, `utcnow` or timezone setting anywhere** in `config.py`,
`main.py` or `campaign_dispatch.py`.

So a campaign the client schedules for **6:00 PM Eastern is dispatched at 18:00 UTC —
2:00 PM Eastern.** Four hours early in EDT, five in EST. The campaign in front of the
operator right now is named `09/09, 6:00 PM Private Record Collection`.

For an auction house whose entire product is *"the sale is tonight"*, a blast that lands
mid-afternoon is worse than one that does not land at all.

### The rule

**The client's timezone is `America/New_York`, and it is configuration, not a constant.**
`BILLING_CYCLE_DAY` is the precedent: a setting with a stated default, read from one place.

Requirements:

1. **One writer, one meaning, written down.** Decide whether `scheduled_at` is stored as
   UTC or as local wall-clock, state it in the column's comment, and make every reader
   agree. `CLAUDE.md` already carries what happens when a column has two writers keeping
   two clocks — `contact_list_members.added_at`, then `contact_lists.created_at`. This is
   the third instance and it is the one that sends messages.
2. **Comparison stays sound.** `due_campaign_ids()` compares ISO strings lexicographically.
   That is correct only while every value carries the same format and the same zone. If
   stored values gain an offset, the comparison must stop being lexicographic.
3. **Existing rows have to be adjudicated, not assumed.** Any `scheduled_at` already in the
   database was typed as Eastern wall-clock and is being read as UTC. The migration
   converts them and its docstring says how it classified them; a row it cannot classify is
   left alone and reported, never guessed at. Check first whether any pending drafts exist
   — if none do, say so and the migration is trivial.
4. **Every client-facing time renders in the client's zone**, from one formatter. The
   campaign rail, history, the per-campaign report, `/usage`'s cycle dates, the hold-back
   "clears at" sentence. `suppression_clears_at()` / `clears_at_clock()` is the pattern
   this project already uses for exactly this: one function computes the moment, one
   renders it, and no surface invents a third.
5. **DST is not optional.** September is EDT (UTC−4) and January is EST (UTC−5). A fixed
   offset is wrong twice a year, and one of those two occasions is a Sunday morning in
   November when a scheduled campaign is an hour out. Use `zoneinfo`, not an integer.
6. **The scheduler ticks every minute** and `due_campaign_ids` filters `status == "draft"`,
   so a campaign cannot double-send. Do not weaken that while changing the comparison.

### What is out of scope for A2

- Per-user or per-tenant timezones. One client, one zone, one setting.
- Changing `sent_at`, `added_at` or `created_at` semantics. They have their own histories
  and `contact_lists.created_at` was settled in 5i. Read them; do not rewrite them.
- The billing cycle anchor. B1b stores it from the subscription and `decisions/012` is
  fresh — if the zone change moves the cycle's boundary dates, that is a **finding for
  `status.md` and an escalation**, not an edit.

---

## Out of scope, both

- Anything in the prospecting or billing tracks. P2b, 5k, B1c are separate and queued.
- `.env` and `.env.production`. If the zone needs a setting there, name it in
  `.env.example` and tell Jordan; do not edit the live file.

## File list

    app/templates/_composer-script.html
    app/templates/campaigns.html
    app/services/campaign_dispatch.py
    app/services/campaign_builder.py
    app/routers/campaigns.py
    app/core/config.py
    app/services/suppression_service.py
    app/services/dashboard_service.py
    app/templates/{today,history,campaign-report}.html
    alembic/versions/
    tests/
    agent/accept-5m.sh
    agent/mutate-5m.py

Widen it where a requirement forces it and record each edit in `status.md` with the
requirement that forced it.

## Acceptance

1. **The panel's mechanism is explained in `status.md`**, reproduced against the production
   shape, before any fix.
2. **Every figure in the summary panel comes from one response about one audience**, and a
   test proves a stale response cannot paint over a newer one.
3. **The panel's audience equals the selector a send would use**, asserted through the
   endpoint rather than through a helper.
4. **Segments and recipients are consistent** for a single-segment message.
5. **A campaign scheduled for 6:00 PM Eastern dispatches at 6:00 PM Eastern**, asserted in
   both EDT and EST — one test in September, one in January.
6. **The migration converts existing rows and reports what it could not classify.**
7. **Every client-facing timestamp renders in the client's zone**, from one formatter,
   proved on the rail, history and the report.
8. **A campaign still cannot double-send** after the comparison changes.
9. `bash agent/gate.sh` green, twice.
10. **Mutation run**, verified-pristine tree, same verdict on two consecutive invocations,
    both shown.

### Mutations

- Paint the panel from a stale response.
- Give `sumSegments` its second writer back.
- Compare `scheduled_at` against a naive `datetime.now()`.
- Use a fixed −4 offset instead of `zoneinfo`.
- Render a client-facing time in UTC.
- Drop the `status == "draft"` filter.

## `/goal`

> Session 5m is complete when `bash agent/accept-5m.sh` exits 0 with every criterion
> printed, `bash agent/gate.sh` is green twice, and the mutation run reports the same result
> on two consecutive invocations with both shown. Turn cap 45. Show the output.

## Review

One synchronous fresh-context review. No spawned reviewers.

1. **How many places write each row of that panel now?** The answer must be one.
2. **What happens at 1:30 AM on the first Sunday in November**, when 1:30 AM Eastern occurs
   twice? Say what the code does; it does not have to be clever, it has to be stated.
3. **Which stored timestamps did this session change the meaning of, and which did it
   leave?** List both, because the next session will assume one rule for all of them.
4. **The scan's own first version.** Run criterion 5 against the pre-fix tree and confirm it
   fails by four hours, not for some other reason.
