# Anyone on the internet can post a fake inbound text to our webhooks and make us reply. Should the webhooks verify the carrier's signature, and what happens to a request that fails?

**Blocks:** nothing in I1. The loop guard is complete and its gate is green.
This is the session I1 fresh-context reviewer's finding F1, and it is a gap
that existed before I1: the webhooks have never been authenticated.
**Why this is not mine to decide:** escalation items 5, 6, 7 and 10. Verifying
a Telnyx signature needs an ed25519 library that is not in
`requirements.txt` (item 7) and a new carrier secret in `.env`, which an agent
must not write (item 6). And the main choice is about opt-outs (item 5): a
webhook that refuses an unsigned request also refuses an unsigned **STOP**, so
a wrong or missing key would silently stop recording opt-outs.

## Context

`app/routers/webhooks/telnyx.py:28` and `app/routers/webhooks/twilio.py:27`
accept any POST. Neither checks Telnyx's `telnyx-signature-ed25519` header or
Twilio's `X-Twilio-Signature`. `deployment/nginx.conf.template` proxies every
path, and no rate limit applies to either webhook.

Since I1, a forged `message.received` gets at most one reply per number per
24 hours, and at most `AUTO_REPLY_HOURLY_CAP` (50) replies an hour across all
numbers. The reviewer measured what that still allows:

- 50 replies an hour to numbers an attacker picks. That is about 1,200 a day,
  at our cost, from our number. This is the shape of SMS pumping fraud.
- I1 now refuses non-US numbers under `SKIP_NON_US_NUMBERS`, which removes the
  premium international ranges. US numbers are still reachable.
- 50 forged inbounds an hour trip the breaker, which silences every genuine
  reply, including a real buyer's first STOP confirmation.
- A forged STOP blocks a real buyer. A forged START (or "YES") lifts a real
  opt-out. P6 below makes the second worse.

## Options

1. **Verify signatures, and refuse when the key is missing.**
   - What it does: add Telnyx ed25519 verification (`TELNYX_PUBLIC_KEY`, plus
     `PyNaCl` or the `cryptography` package) and Twilio HMAC verification (the
     `twilio` package already ships `RequestValidator`, using
     `TWILIO_AUTH_TOKEN`). A request that fails gets 403 and nothing is written.
   - Cost: a box deployed without the public key stops recording STOPs. That
     needs a `/health` field and a deploy check so the failure is loud, not
     silent.
2. **Verify signatures, and fail open with an alarm when the key is missing.**
   - What it does: verify whenever a key is configured. With no key, accept the
     request as today and set a `/health` field (`webhook_auth_ok: false`).
     This follows the `SHORT_LINK_DOMAIN` precedent: a guard keyed on
     configuration must not take the product down when the configuration is
     wrong.
   - Cost: a box that never gets the key stays open, with only a field on
     `/health` to say so.
3. **Leave the webhooks open and rely on the I1 caps.**
   - What it does: nothing new. Spend from forged traffic stays bounded at 50
     replies an hour, about 1,200 a day.
   - Cost: forged STOP and START messages still edit the compliance record, and
     there is no way to tell them from real ones.

## Recommendation

Option 2. It closes the fraud and compliance-record holes on any box that has
the key, which should be every box. It also keeps inbound STOP working on a
misconfigured box, where losing opt-outs silently is worse than the fraud
exposure. That is the same reasoning that made the short-link host guard fail
open.

## Also raised by the same review (bounded, recorded in status.md)

- **P6, before I1:** `unblock_number()` on START deletes every kind of block,
  including `delivery_failure` and `carrier_block`. A START from a landline's
  number, real or forged, puts that number back into every campaign. The fix is
  to unblock only rows whose reason is in `OPT_OUT_REASONS`. That is opt-out
  behaviour (item 5), so it is not decided here.
- **F3:** the breaker limits the rate, not the total: 50 an hour, every hour.
  The I1 spec says it "resets on its own when the hour clears". A daily cap or
  a latch would contradict the spec, so that is a human's call.
