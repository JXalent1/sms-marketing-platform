# Production lessons

Everything here was learned by running a live SMS marketing platform: ~280,000
messages, 86 campaigns, ~51,000 contacts, across eight months and two carriers.
Each item cost real money, real deliverability, or real client trust. The
skeleton already implements the fixes; this file explains why, so you don't
"simplify" one of them away.

---

## 1. An emoji can halve your margin

**What happened.** Messages were billed to the client on a flat
`ceil(len(text) / 160)` basis. The carrier billed real segments. Because the
template opened with a 🚨, every message was UCS-2 encoded: 67 characters per
segment instead of 153.

One month: 35,568 segments billed to the client, 83,880 segments billed by the
carrier. A 2.36x multiplier absorbed entirely by the operator. Gross margin on
that cycle was 20% instead of roughly 60%.

**Why.** One non-GSM-7 character re-encodes the *entire* message. It is not a
per-character surcharge — it changes the segment size for everything.

**The fix here.** `app/sms/segments.py` counts encoding-aware segments;
`SendResult.parts` carries the carrier's own count and the send loop prefers it;
`SMSMessage.segments` stores it as the billing basis. The campaign composer shows
the encoding and what the message would cost without the emoji, before sending.

**Watch for.** Curly quotes and em dashes from a client who drafted copy in Word
are also non-GSM-7. The composer will flag them.

---

## 2. Link shorteners get carrier-spam-blocked

**What happened.** Campaigns were accepted by the provider (HTTP 200, dashboard
showed "sent") and then silently dropped by carriers with error 40002 "Blocked as
spam". Diagnosed with an A/B/C test to one number: the same message with a
`short.gy` link failed, with a plain first-party domain delivered, with no link
delivered.

**Why.** Public shorteners (`bit.ly`, `tinyurl`, `short.gy`, `t.co`) are shared
infrastructure. Carrier filters score the domain's aggregate reputation, and you
inherit every spammer using it.

**The fix here.** `find_risky_links()` in `app/sms/phone.py` flags them; the
composer shows a red warning and the send loop logs one.

**Rule.** Use a domain the client owns — either a path on their main site or a
dedicated short domain used only by them. Never a shared shortener.

---

## 3. "Sent" does not mean delivered

**What happened.** Messages were marked `sent` on provider acceptance. Nothing
ever recorded what the carrier did next. Carrier spam-blocks were therefore
completely invisible, and the client was shown a "5,000 sent" number that was
partly fiction.

**Why.** Provider acceptance and carrier delivery are two different events,
minutes apart. Only the second one is real.

**The fix here.** `record_delivery_status()` in
`app/routers/webhooks/common.py` processes delivery webhooks, moves messages to
`delivered` or `undelivered`, and corrects the campaign counters. It is
idempotent because carriers retry webhooks for hours.

**Setup requirement.** Configure the webhook URL in the carrier portal. Find it
at Settings → System in the dashboard. If it is left blank, delivery tracking and
STOP handling both silently do nothing.

---

## 4. Bill on `('sent', 'delivered')`, not `'sent'`

**What happened.** After delivery tracking was added (lesson 3), the usage meter
appeared to freeze. Campaigns would count for a few minutes, then vanish from the
total.

**Why.** The usage query filtered on `status == 'sent'`. Delivery webhooks flip
`sent` → `delivered`, so every successfully-delivered message dropped out of the
billing query. The better the delivery, the less the client was billed.

**The fix here.** `BILLABLE_STATUSES = ('sent', 'delivered')` in
`app/models/sms_message.py`, used by `billing_service.compute_usage()`.

**Decide explicitly** whether `undelivered` (carrier-dropped) is billable. This
skeleton treats it as non-billable to the client — but note the carrier still
charges *you* for those attempts.

---

## 5. A campaign that outruns the balance destroys itself

**What happened.** Five separate times, a large blast drained the carrier balance
to zero mid-send. The account went inactive and every remaining message failed
with `403 / 20012 "Account inactive"`. Across those incidents about 13,000
messages were lost. The worst single case lost 4,623 of 6,771.

**Why auto-recharge didn't save it.** Auto-recharge was enabled — at $10 per
top-up via PayPal, with a $0 credit limit. A 6,000-recipient blast at ~5 segments
each sends 6–7 messages/second and spends $10 in about ninety seconds, far faster
than a PayPal recharge posts. The balance flatlines at zero with no credit buffer.

**The fix here.** `CampaignService.preflight()` checks the balance covers the
estimated cost (with 1.5x headroom) and aborts the campaign *before* sending
anything. An aborted campaign can simply be re-run; a half-sent one leaves you
unable to tell who received the message.

**Also do this.** Set auto-recharge to cover a full campaign (a few hundred
dollars, not ten), use a card rather than PayPal, and run
`scripts/balance_alert.py` hourly from cron.

---

## 6. Filter undeliverable numbers before paying to send them

**What happened.** Every campaign logged 250–400 failures from Canadian and
Caribbean numbers that the US-only messaging profile could never deliver to. The
system paid for each attempt and recorded each as a failure, which also made real
problems harder to spot in the noise.

**The fix here.** `is_non_us_region()` runs before the send call, and those
messages get status `skipped` — non-billable, and not counted as failures.

**Note.** The area-code list is US-safe by construction: it contains no US
mainland codes, so it cannot filter out a real US recipient. If a client's profile
covers Canada, set `SKIP_NON_US_NUMBERS=false` instead of editing the list.

---

## 7. No auth is not a risk, it's an incident waiting for a date

**What happened.** The dashboard shipped with zero authentication — every page
and every API route was public. A stranger found it, browsed the contact list,
changed the auto-reply to advertise their Discord, changed the outbound link to
their own site, and sent two campaigns to the client's entire 5,535-person list.
9,360 unauthorized messages in eleven minutes, from the client's own number.

Those messages were also billable, so the client was likely charged for the
attacker's spam.

**The fix here.** `require_auth` on every page and every `/api/` route. Only
`/health`, `/login`, `/logout` and the carrier webhooks are public. FastAPI's
auto-generated docs are disabled — they published a complete map of every
endpoint, including the send API. Rate limits cap the blast radius of a stolen
session. `tests/test_smoke.py` asserts all of this and will fail if you regress it.

**Also.** Credentials come from `.env`, never from source. Run
`scripts/hash_password.py` and set `ADMIN_PASSWORD_HASH`.

---

## 8. Keep your own opt-out list

Carriers maintain their own STOP list, but it does not travel with you. The day
you migrate carriers, everyone who ever opted out becomes reachable again — and
texting them is both a compliance violation and the fastest way to get a number
blocked.

`blocked_numbers` is the source of truth here. Both webhook handlers write to it,
the send loop reads it, and nothing hard-deletes from it.

---

## 9. Operational notes

**bcrypt must stay pinned to 4.0.x.** passlib 1.7.4 breaks against bcrypt 4.1+.
The failure is an unhelpful backend error at import time.

**Load the blocklist once per campaign, not once per recipient.** At 6,000
recipients the per-row version is 6,000 extra queries, and on SQLite it makes a
campaign look hung.

**Background tasks need their own DB session.** A request-scoped session is
closed when the response returns; a background job holding one fails partway
through with a confusing detached-instance error.

**Give every scheduled job an explicit `id` and `replace_existing=True.**
Otherwise a reload stacks duplicate jobs that all fire at once.

**Close browser automation in a `finally`.** If you build a scraping
`ContactSource`, a leaked Playwright driver per run adds up — the reference box
accumulated 17 orphaned processes and 1.6 GB of RSS between restarts.

**SQLite is fine longer than you expect.** 280,000 messages and 51,000 contacts
ran on it without trouble. Move to PostgreSQL when you need concurrent writers,
not because the row count looks big.

---

## 10. Commercial lessons

**Persist a billing snapshot at the end of each cycle.** Computing usage live
from the message table means a past invoice silently changes whenever the
counting logic does. The reference system had no snapshot and no invoicing
records, so nobody could reconstruct what a given month had actually billed.

**Never change the billing basis retroactively.** When segment tracking was
added, legacy rows kept the old formula so closed cycles were not re-priced.
`compute_usage()` still does this. Preserve that behaviour.

**Keep the price in one place.** In the reference system the $400 base fee
existed only as a hardcoded string in an HTML template, while the tiers lived in
Python. They disagreed, and nobody noticed for months. Here everything comes from
`.env` and the UI renders `/api/usage/pricing`.

**Know your real unit cost before quoting.** Blended carrier rate was ~$0.008–0.009
per segment including carrier fees, plus fixed monthly costs for numbers and
10DLC registration. Undelivered messages still cost you and earn nothing.
