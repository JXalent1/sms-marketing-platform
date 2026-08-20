# Should a campaign be allowed to run while the send path is degraded?

**Blocks:** nothing today — but it is live on the box that is about to send for real
**Why this is not mine to decide:** escalation items 1 (what counts as a billable
segment), 3 (the pre-flight capacity check) and 6 (anything that could send a real
message). Any fix changes at least one of them.

## Context

Session 5c made a failed carrier visible on the pill, the Settings page and
`/api/settings/system`. It did not change what happens if the client sends anyway, and
nothing on the send path consults `send_mode()`. Traced end to end:

| where | what happens |
|---|---|
| `app/services/campaign_service.py:64` | `self.provider = get_provider()` — the console fallback |
| `app/services/campaign_service.py:227` | pre-flight asks that provider for its balance |
| `app/sms/providers/console.py:43` | console returns `999_999.0`, commented "never trips the pre-flight check" |
| `app/sms/providers/console.py:35-40` | every send returns `success=True` |
| `app/services/campaign_service.py:356` | the row is written `status="sent"` |
| `app/models/sms_message.py:24` | `BILLABLE_STATUSES = ("sent", "delivered")` |
| `app/services/billing_service.py:124` | those rows are counted for the cycle |

On a live box whose carrier failed to start, the client can send to all 1,223 contacts,
watch every row go green, and have those segments counted against his 10,000 included
and invoiced at $0.015 for messages nobody received. The pre-flight check — the single
most valuable safeguard in the codebase, the one that exists to turn "we lost 4,623
messages mid-blast" into "the campaign refused to start" — cannot fire, because the
provider it asks is a stub with a bottomless balance.

This is not a regression. It behaved identically before 5c, and in a *chosen* dry run it
is correct and deliberate: console exists so nothing costs money and nothing reaches a
handset. What 5c changed is that the two cases are now distinguishable, so acting on the
difference has become possible for the first time.

Found by the fresh-context review of 5c, verified against the source above.

## Options

1. **Refuse to start a campaign while `send_mode()` is `unavailable`** — one more
   pre-flight check, alongside capacity. Cost: touches the pre-flight check, which is on
   the do-not-weaken list; this strengthens it, but it is still that file. A client whose
   carrier is misconfigured can queue nothing at all, which is the point, but it must not
   be possible to reach this state from a chosen dry run — the demo/dry-run flow has to
   keep working exactly as it does now.
2. **Let it send but do not bill it** — mark the rows with a status outside
   `BILLABLE_STATUSES` when the provider is degraded. Cost: changes what counts as
   billable, which is a commercial decision, and it leaves the client with a screen full
   of green rows for messages that never left the building. Solves the money and not the
   lie.
3. **Make the console provider refuse to report a balance when it is a fallback rather
   than a choice** — `get_balance()` returns `None`, which the pre-flight already treats
   as "provider does not expose a balance" and passes. So this alone changes nothing;
   it would need option 1 anyway. Recorded because it looks like the small fix and is not.
4. **Leave it and document it in `docs/RUNBOOK.md`.** Cost: the runbook is read after
   something has gone wrong, and the failure mode here is one where nothing looks wrong.

## Recommendation

Option 1, with the degraded check written as its own pre-flight row so the composer shows
the reason before the send rather than after it, and with `dry_run` explicitly excluded
so the console flow is untouched. It is the option that makes the product refuse rather
than apologise, which is the same argument that put the capacity check there.

Worth deciding before the first blast, not after: the window where this matters is
exactly the window the box is in now — provider recently switched to live, nobody yet
certain it took.

---

# Decision — Option 1, and Option 2 underneath it

**Decided by:** Jordan (via Cowork), 2026-08-20
**Status:** resolved

Refuse the send. Options 1 and 2 are not alternatives — they are two layers, and both go in.

## Layer 1 — refuse to start (primary)

A degraded pre-flight row of its own, shown in the composer before the send, with
`dry_run` explicitly excluded so the chosen console flow behaves exactly as it does today.
Same argument that justifies the capacity check: the product should refuse rather than
apologise.

Refuse rather than warn, and the reason is who is holding the mouse. The operator is the
client, not us. A warning row assumes a reader who knows what "degraded" means and will
stop; he will read it as one more thing between him and tonight's auction and click
through. There is no legitimate reason to send from a degraded box — every message goes
nowhere — so there is nothing to preserve by leaving the door open.

## Layer 2 — never billable (backstop)

Rows written while the provider is degraded get a status outside `BILLABLE_STATUSES`.

The file frames this as a commercial decision. It isn't. A segment that never reached a
carrier is not a segment we can bill for — that is what the word means, not a concession
we are making. There is no version of the pricing conversation where the client owes
$0.015 for a message that never left the building.

It goes in as a backstop, not a solution. If layer 1 ever fails to hold, this is what
stops a silent invoice.

## Why this beats the money framing

The decision file leads with billing. The billing is the small half. 1,223 contacts at one
segment is about $18 — an embarrassing invoice line, refundable in one email.

The real damage is that A4A runs a different-niche auction nearly every day. A blast that
silently doesn't go out means an empty room at a sale he has already paid to stage, and he
finds out at the sale, not at the send. That loss is not $18 and it is not refundable.
Refuse-to-send is justified by the empty room, not the invoice.

## What is NOT in this decision

**Do not route the degraded alert over SMS.** An SMS alert about being unable to send SMS
is self-defeating, and `agent/notify.sh` currently reads the same carrier credential it
would be warning about. Instead: surface degraded state in `/health` so an external
uptime monitor catches it. That is the one channel that still works when the carrier
doesn't. Independent alerting is a separate item — see the `notify.sh` credential entry
in 5b's B6 list.

## Sequencing — this does not block the first live send

The box is not degraded right now; the provider constructs and reports the correct sender
number. So:

- Jordan's own test send, the contact import, and the 50-contact category send all
  proceed now. If the provider degrades in that window Jordan is watching the handset and
  will notice within a minute.
- **This must land before the client gets the login.** The client is the one who cannot
  tell the difference between "sent" and "sent", and he is the reason the fix exists.

That is the line: Jordan may operate a box without this guard. The client may not.

## Related — finding 3 gets folded in

The review left "unknown `SMS_PROVIDER` raises, so every page 500s" as a `status.md` note,
on the grounds that it is pre-existing and possibly correct. 5c changed that calculus and
it should now be changed.

Before 5c, degrading on an unknown provider would have been invisible, so failing loudly
was the safer of two bad options. Now that degraded state is visible on the pill and — per
this decision — blocks sending, degrading is strictly better than 500-ing. `.env` is
hand-edited over ssh on a live client box; a typo like `telnix` should not take the
client's whole dashboard down when it could instead show a degraded pill and refuse to
send. Same code path, same treatment.
