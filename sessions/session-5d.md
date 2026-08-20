# Session 5d — Refuse to send from a degraded box

## Objective

Session 5c made a failed carrier *visible*. It did not change what happens if someone
sends anyway. Close that: a box whose provider fell back to console must refuse to start a
campaign, and must never bill for one.

This is the last thing between Jordan and handing the client a login.

## Prerequisites

- Read `CLAUDE.md`, `RULES.md`, `status.md`, `modules.md`, and both resolved decision
  files in `decisions/` before touching anything.
- 5c is merged, gate green, 145 tests passing.
- Production is live at https://app.onlineauctions.co with a working Telnyx provider.
  By the time you run, Jordan may have imported ~1,223 contacts and sent real messages.
  **Treat the production database as containing real client data.**
- Server: `ssh -i ~/.ssh/a4a_deploy appuser@67.205.180.62`. `appuser` has sudo for
  `systemctl restart a4a-sms` only.
- Deploy: `SERVER=appuser@67.205.180.62 SERVICE=a4a-sms ./deployment/deploy.sh`.
- **Jordan has already applied the `agent/gate.sh` venv pin by hand** (decision 001). If
  `agent/gate.sh:18` does not have the `.venv/bin` PATH line, stop and tell him — do not
  add it yourself, that file is human-only.

## Background — read decisions/002 in full

The short version, verified end to end in that file: on a degraded box,
`campaign_service.py:64` holds the console fallback, `console.py:43` answers the pre-flight
balance question with `999_999.0`, every send returns success, rows are written `sent`,
and `sent` is in `BILLABLE_STATUSES`. The client can send to every contact, watch every
row go green, and be invoiced for messages nobody received — with the pre-flight check
unable to fire, because the provider it interrogates is a stub with a bottomless balance.

The decision is Option 1 **and** Option 2, as two layers. Not either/or.

---

# Part A — agent work

## A1. Refuse to start a campaign while the send path is degraded

A pre-flight row of its own, rendered in the composer before the send, not an error after
it. Refuse — do not warn. The operator is the client, and a warning row is something he
clicks through on his way to tonight's auction.

`dry_run` is **explicitly excluded**. A chosen console run must behave exactly as it does
today; the demo flow is how this product gets sold. The distinction is `send_mode()`
returning `unavailable` (fell back) versus `dry_run` (chosen), which 5c already
established — use it, do not re-derive it.

This touches the pre-flight check, which is on the do-not-weaken list. You are
strengthening it. Do not refactor anything else in that file while you are in there.

## A2. A degraded row is never billable

Rows written while the provider is degraded get a status outside `BILLABLE_STATUSES`, so
`billing_service.py:124` cannot count them.

This is a backstop for A1, not a substitute. If A1 holds, no row should ever be written in
this state — add the status anyway, and add a test that asserts a degraded-state row is
excluded from a billing cycle count.

A segment that never reached a carrier is not billable. That is not a pricing concession,
so do not treat it as one or add configuration for it.

## A3. An unknown provider degrades instead of 500-ing

`get_provider()` currently raises on an unrecognised `SMS_PROVIDER`, three lines above a
docstring arguing for the opposite, so every page returns 500. The 5c review left this as
a note because it was pre-existing and arguably correct.

Decision 002 changes that: now that degraded state is visible and blocks sending, degrade
on unknown too. `.env` is hand-edited over ssh on a live client box, and a typo like
`telnix` must not take the client's dashboard down when it could show a degraded pill and
refuse to send. Same code path, same treatment as a provider that failed to construct.

Keep the raise for the *programmer* error case if you can distinguish it cleanly. If you
can't, degrade — a live dashboard beats a correct exception.

## A4. `/health` reports degraded state

An external uptime monitor is the only alert channel that still works when the carrier
doesn't. Surface degraded state in `/health` so a monitor can catch it.

**Do not route this over SMS.** An SMS alert about being unable to send SMS is
self-defeating, and `agent/notify.sh` reads the same carrier credential it would be
warning about. Do not touch `notify.sh` this session.

Keep `/health` white-label — no carrier name, no exception text. A boolean and a short
neutral reason.

## A5. Fix `.claude/hooks/verify-gate.sh`

Approved in decision 001. Not human-only, so this is yours.

1. `json.loads` at line 26 rejects a legal control character in the hook's stdin
   (`JSONDecodeError: Invalid control character at: line 1 column 567`). Use
   `strict=False`. Until this works `STOP_ACTIVE` is never `true`, the
   `MAX_GATE_ATTEMPTS` branch at line 63 is unreachable, and a gate failure bounces
   forever instead of escalating after four attempts.
2. The attempt counter is a fixed path because the session id never parses, which is how
   a fresh session was told "Attempt 22 of 4". Key it on the session id, and treat a
   missing id as a fresh run rather than falling back to the shared path.

Add a smoke check that feeds the hook a payload containing a control character and asserts
the loop guard still arms. This bug survived because the only path that exercises it is
the one nobody wants to hit.

## A6. `docs/API.md`

Lines 280-283 don't merely omit 5c's new fields — they tell the reader to find the webhook
URL there, which was removed on white-label grounds and has a test pinning its absence.
Fix the section to match reality and document the degraded fields from A1–A4.

---

## Part A acceptance

Demonstrate each in the transcript. Self-declared completion does not count.

1. `agent/gate.sh` passes all six checks, twice in a row.
2. With the provider forced into fallback, starting a campaign is **refused**, and the
   composer shows the reason before the send. No rows written, no segments counted.
3. With `SMS_PROVIDER=console` chosen deliberately, the dry-run flow is byte-for-byte
   unchanged — show a before/after of the same campaign through the console path.
4. A degraded-state row is excluded from a billing cycle count.
5. An unrecognised `SMS_PROVIDER` renders a degraded dashboard, not a 500. Show all seven
   pages returning 200 in that state.
6. `/health` reports degraded, with no carrier name in the payload.
7. The hook smoke check passes: a control character in stdin no longer disarms the loop
   guard.
8. New tests fail against the pre-fix tree — show both directions.
9. After deploy: seven screens 200 over HTTPS, fonts load, no carrier name in any rendered
   page or API response, and the pill correctly reports a *working* provider.

Wire this into a `/goal` stop condition with a turn cap, then run a fresh-context review
pass before declaring done.

---

## Constraints

- Do not touch `.env`, `.env.production`, `agent/gate.sh` or `agent.config.sh`.
- **Do not send any SMS. Do not modify, delete or re-import contact data.** By now the
  production database holds the client's real list.
- No source file exceeds 500 lines.
- `app/sms/` must not import `app.models` or `app.services`.
- No runtime CDN. Every asset same-origin.
- No hardcoded commercials.

## Explicitly out of scope

- `agent/notify.sh` and its credential — B6 item, separate.
- The `SECRET_KEY` guard — still a `status.md` note, still post-launch.
- History and Categories screens, quiet hours, prospecting.
- Any change to what the client is charged per segment. A2 changes *whether* a
  non-delivered row counts, not the price of one that did.
