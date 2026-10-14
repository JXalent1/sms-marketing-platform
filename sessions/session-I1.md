# Session I1 — Auto-reply loop guard (incident 2026-10-09 to 10-12)

**Module:** I1 · **Priority:** blocks re-enabling the carrier API key · **Do not deploy. Do not touch any carrier key or `.env`.**

## The incident

Our number +19547382462 looped with two automated responders, +19046943673 and
+19048673536, for about 3 days: ~114k outbound, ~56k inbound in 24 hours, $2,818.
They texted "We do not monitor text messages sent to this number. Please call us..."
and we answered every one with the Settings auto-reply.

## Root cause (verified by Cowork, read before writing code)

1. `app/routers/webhooks/telnyx.py:36-47` — every `message.received` calls
   `handle_inbound()` and, if it returns text, sends it at line 43 with
   `get_provider().send()`. No condition besides "reply is non-empty".
2. `app/routers/webhooks/common.py:22-45` — `handle_inbound()` returns a reply for
   EVERY inbound: STOP (33-36), START (38-40), HELP (42-43), and everything else
   gets the auto-reply at line 45. Line 45 can never be empty: an empty setting
   falls back to `compliance.default_auto_reply()`. There is no per-number dedupe,
   no rate limit, no automated-sender check, and no blocklist check.
3. `app/routers/webhooks/twilio.py:27-35` — same unbounded behaviour via inline
   TwiML (line 31-32). Also needs the guard.
4. Delivery events (`telnyx.py:49-71`, `twilio.py:38-62`) only call
   `record_delivery_status()` (`common.py:48+`), which writes rows, may auto-block,
   may raise a config alert. No send path. Keep it that way and prove it.
5. STOP / "2" (`app/sms/compliance.py:21`, exact match) block the number and send a
   confirmation EVERY time, with no dedupe. A responder that sends "STOP" or "2"
   repeatedly would get unlimited confirmations.
6. Auto-replies are sent straight to the provider and never written to
   `sms_messages`: invisible in the app, in reports and in the pre-flight balance
   check. That is why the loop was not seen in the platform.
7. Only other sends in the repo: campaign dispatch (`campaign_service.py:372`) and
   the manual test send (`routers/campaigns.py:343`). Neither is triggered by inbound.

## What to build

A single guard every inbound reply passes through, in a new
`app/services/auto_reply_guard.py` (services layer: it touches the DB; `app/sms/`
must not import models).

1. **Persisted log.** New table (Alembic migration, never `create_all`):
   `auto_reply_log` — phone, kind (auto_reply | stop | start | help), decision
   (sent | suppressed), reason, inbound_excerpt (first 160 chars), created_at.
   Index on (phone, created_at) and on created_at.
2. **Claim before send.** Decide and INSERT the `sent` row and commit BEFORE calling
   the provider, so two concurrent webhooks for the same number cannot both pass.
   If the send then fails, update that row's reason; do not delete it.
3. **Per number: max 1 reply per 24 hours**, across all kinds, read from the table.
   STOP still blocks the number every time (compliance record), but the STOP
   confirmation obeys the cap too.
4. **Automated-sender filter.** One constant, `AUTOMATED_SENDER_PHRASES`, in
   `app/sms/compliance.py` (pure, no DB). Case-insensitive substring match on at
   least: "do not monitor", "not monitored", "do not reply", "no-reply", "noreply",
   "automated message", "unable to receive", "this number does not accept",
   "cannot receive", "auto-reply", "autoreply". Matched inbound gets NO reply of any
   kind. It is still logged.
5. **Never reply to a blocked number** except the first STOP confirmation.
6. **Global circuit breaker.** If `sent` rows in the last rolling 60 minutes reach
   `AUTO_REPLY_HOURLY_CAP` (config, default 50), suppress every reply, log at ERROR,
   and call `monitoring_service.record_config_alert()` so `/health` shows it. It
   resets on its own when the hour clears.
7. **Every suppression logged** with its reason (rule name) in the table and at INFO.
8. Config in `app/core/config.py` + `.env.example`: `AUTO_REPLY_PER_NUMBER_HOURS=24`,
   `AUTO_REPLY_HOURLY_CAP=50`. No hardcoded numbers in the guard.
9. Delivery-status handlers must never reach the guard or the provider.

Wire it into `telnyx.py` (skip the send when suppressed) and `twilio.py` (return
`EMPTY_TWIML` when suppressed). `handle_inbound()` keeps doing the blocklist writes;
the reply decision moves to the guard.

## Out of scope

- Writing auto-replies into `sms_messages` (finding 6). Note it in status.md.
- Any deploy, any `.env`, any carrier key, any Settings UI change.
- The stashed `la-empty-state-wip` work. Do not pop it.

## File list

`app/services/auto_reply_guard.py` (new), `app/models/auto_reply_log.py` (new),
`app/models/__init__.py`, `alembic/versions/` (one new migration),
`app/routers/webhooks/{common,telnyx,twilio}.py`, `app/sms/compliance.py`,
`app/core/config.py`, `.env.example`, `tests/`, `agent/accept-I1.sh`, `status.md`.

## Acceptance (`agent/accept-I1.sh` prints each and exits non-zero on failure)

1. A unit test per rule: per-number 24h cap, phrase filter (each phrase, case
   variants), blocked-number rule, circuit breaker trips at 50 and recovers after
   the hour (frozen clock), claim-before-send (send failure leaves the row).
2. Every Telnyx delivery event type (`message.sent`, `.delivered`, `.failed`,
   `.finalized`) and the Twilio status callback: provider `send` called 0 times.
3. **Simulation:** 1,000 `message.received` webhooks to `/webhooks/telnyx`, rapid,
   alternating +19046943673 / +19048673536, body = the exact responder text above.
   Pass: 0 sends with the phrase filter on. Second run with the phrase list emptied
   (monkeypatch): at most 2 sends total, one per number. Both runs: 1,000 log rows.
4. Same simulation through `/webhooks/twilio/sms`: same limits, suppressed requests
   return empty TwiML.
5. Normal path: first-time "hi, when is the next auction?" gets exactly 1 reply; the
   same number's second message within 24h gets 0.
6. STOP repeated 20 times from one number: number blocked, exactly 1 confirmation.
7. `python -m pytest tests/ -q` exits 0; `alembic upgrade head` from a clean DB;
   `npm run build:css` succeeds (no linter is configured in this repo, say so);
   `bash agent/gate.sh` green twice.

## `/goal`

> Session I1 is complete when `bash agent/accept-I1.sh` exits 0 with every criterion
> printed and `bash agent/gate.sh` is green twice, both shown. Turn cap 40. Show the output.

## Review

Then spawn ONE fresh-context reviewer on the diff with exactly this question: "Find any
remaining path where an inbound message, a delivery event, or a retried webhook can
cause an unbounded number of outbound sends." Fix what it finds within the file list,
re-run acceptance, and record the review in status.md.
