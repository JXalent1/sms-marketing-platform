# Session 5n — template metrics in upload mode, and cancelling a scheduled campaign

**Module:** 5n · **Depends on:** 5m · **Priority: ahead of P2b, 5k, B1c.**
Both reported by the client's operator while using the platform.

## Prerequisites

- Read `CLAUDE.md`, `RULES.md`, `modules.md`, `status.md`, every file in `decisions/`.
- Run inside the project venv. `npm run build:css` before serving.
- 835 tests, gate green twice as of 5m (`493ebf4`).
- **Escalation item 5 applies to A2**: cancelling decides whether a real person gets a text.

## A1 — the message metrics never populate in upload mode

**Observed on production.** A full message body typed, and the row beneath reads
`Characters 0 · Encoding — · Segments/msg 0 · Recipients 0 · Total segments 0 ·
Estimated cost —`.

Cause: `refreshPreview()` returns at `_composer-script.html:135` whenever
`composerMode === 'upload'`, so nothing in that row is ever written.

**The guard is right and too broad.** Its reason — not showing one audience's figures while
the operator commits to another — applies only to the three that depend on an audience:
**Recipients, Total segments, Estimated cost**. The other three are measured on the
template alone and are correct in any mode: **Characters, Encoding, Segments per message**.
Encoding especially: an emoji flips GSM-7 to UCS-2 and roughly triples the cost, and
`CLAUDE.md` says the UI must warn loudly. Today that warning is silent for the entire
upload flow, which is the primary flow.

Required:

- Template-derived metrics render in **both** modes, live as he types.
- Audience-derived metrics render **only** when an audience is resolved. In upload mode
  they must read as *not yet known* — not as `0`. A zero is a claim, and "0 recipients" on
  a screen where he is about to upload 4,000 contacts is a false one. Use the em dash the
  panel already uses for an unknown, and say so in a comment.
- The UCS-2 warning fires in upload mode.
- No second endpoint and no second segment calculation. `count_sms_segments()` is the
  authority and is escalation item 2 — if the template metrics need to be computed without
  an audience, get them from the same call the panel already makes, or split the endpoint's
  response, but do not reimplement the count in JavaScript.
- 5m's staleness rule still holds: `panelStale(token)` guards the audience half, and a
  stale response still cannot paint.

## A2 — a scheduled campaign cannot be cancelled

There is no cancel route, no service function and no control. A campaign scheduled for the
wrong day, or for an auction that moved, can only be stopped by editing the database.

`due_campaign_ids()` dispatches drafts whose `scheduled_at` has passed, and that status
filter is what stops a double send — so cancelling has to move the campaign out of that
set without weakening the filter.

Required:

- **Cancel a scheduled campaign from the campaign rail**, where he already sees
  `Scheduled — N recipients, <date>`.
- **Decide what cancelling means and write it down**: does it clear `scheduled_at` and
  leave an editable draft, or move the campaign to a terminal state? An editable draft is
  the more useful answer — the usual reason to cancel is a wrong time — but state the
  choice and its consequence in the service function's docstring.
- **A campaign that has started cannot be cancelled.** `sending`, `completed` and `aborted`
  are out. The refusal says which, in his units, per `decisions/006`: what stopped it, and
  what he can do instead.
- **The race is the whole risk.** The scheduler ticks every minute. Cancelling at the
  moment `run_due_campaigns` has selected the campaign must not produce a half-send.
  Re-check the campaign's state inside the dispatch path after selection, not only in
  `due_campaign_ids()`, and assert it with a test that cancels between selection and
  dispatch.
- Cancelling is not deleting. The campaign, its name and its audience stay.

## Out of scope

- The prospecting and billing tracks. P2b, 5k, B1c are queued separately.
- Editing a scheduled campaign's message or audience in place. Cancel returns it to a
  state where the existing composer can be used; a full edit flow is its own session.
- `.env` and `.env.production`.

## File list

    app/templates/_composer-script.html
    app/templates/campaigns.html
    app/routers/campaigns.py
    app/services/campaign_service.py
    app/services/campaign_dispatch.py
    tests/
    agent/accept-5n.sh
    agent/mutate-5n.py

Widen where a requirement forces it; record each in `status.md` with the requirement.

## Acceptance

1. **Characters, Encoding and Segments/msg populate in upload mode**, live, with no
   audience selected.
2. **Recipients, Total segments and Estimated cost read as unknown, not `0`**, until an
   audience resolves.
3. **The UCS-2 warning fires in upload mode** on an emoji.
4. **No second segment calculation exists.** A test asserts the figure on screen came from
   `count_sms_segments()` via the server.
5. **A scheduled campaign can be cancelled from the rail**, and afterwards
   `due_campaign_ids()` does not return it.
6. **A campaign already sending, completed or aborted refuses cancellation**, with a
   sentence naming the state.
7. **Cancelling between selection and dispatch does not send.** Assert the race directly.
8. **A cancelled campaign still exists** with its name and audience intact.
9. `bash agent/gate.sh` green, twice.
10. **Mutation run**, verified-pristine tree, same verdict on two consecutive invocations,
    both shown.

### Mutations

- Restore the blanket early return in upload mode.
- Render `0` instead of unknown for the audience metrics.
- Skip the UCS-2 check in upload mode.
- Drop the post-selection state re-check in the dispatch path.
- Let cancel apply to a `sending` campaign.
- Leave `scheduled_at` set after cancelling.

## `/goal`

> Session 5n is complete when `bash agent/accept-5n.sh` exits 0 with every criterion
> printed, `bash agent/gate.sh` is green twice, and the mutation run reports the same result
> on two consecutive invocations with both shown. Turn cap 40. Show the output.

## Review

One synchronous fresh-context review. No spawned reviewers.

1. **Which metrics depend on an audience and which do not?** List both and check the code
   agrees with the list.
2. **What does the panel claim when it does not know?** A zero and an unknown are different
   claims; find every place that conflates them.
3. **Walk the cancel race by hand** — selected, then cancelled, then dispatched — and say
   what each step reads and writes.
4. **The scan's own first version.** Run criterion 7 against the pre-fix tree and confirm it
   sends, which is the behaviour it exists to stop.
