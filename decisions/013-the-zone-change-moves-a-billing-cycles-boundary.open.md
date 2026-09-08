# Moving the application into Eastern moves which cycle an evening send is billed in. Which cycle is right, and what happens to the rows already stored?

**Blocks:** nothing in 5m. A1 and A2 are complete, the gate is green and no
billing file was edited. `sessions/session-5m.md` names this outcome in advance:
"if the zone change moves the cycle's boundary dates, that is a **finding for
`status.md` and an escalation**, not an edit."
**Why this is not mine to decide:** escalation item 1. Which segments fall in
which cycle is what appears on an invoice, and `decisions/012` on the same
subject is still open.

## Context

Session 5m sets the process timezone from `APP_TIMEZONE` at startup
(`app/core/config.py::apply_process_timezone`), so `datetime.now()` returns the
client's wall clock on a droplet whose own clock is UTC. That is what makes
`sent_at`, `created_at` and every other naive column mean what their comments
say they mean, and it is what lets a client-facing timestamp render in the
client's zone at all. Those columns are out of 5m's scope by name and none of
them was edited.

The cycle is a **date**, derived from those columns. So the zone moves it.
Measured on a scratch database, one real send at 8:00 PM Eastern on 31 August
2026 — the last evening of a cycle, and the hour this client's auctions run:

```
stored by a UTC box (before)   sent_at=2026-09-01T00:00:00  ->  cycle September 2026
stored now (after)             sent_at=2026-08-31T20:00:00  ->  cycle August 2026
```

Three consequences, all in `app/services/`, none touched:

1. **`billing_service.compute_usage()`** filters `sent_at` between cycle dates.
   An evening send on the last day of a cycle moves one cycle earlier. The
   affected window is 8:00 PM to midnight Eastern (7:00 PM in winter) on a cycle
   boundary — which is exactly when this client sends.
2. **`stripe_billing._local_date()`** is `datetime.fromtimestamp()`, so the cycle
   anchor derived from Stripe's subscription changes with the process zone. A
   subscription created at 02:00 UTC on the 1st anchors to day **1** on a UTC box
   and day **31** on an Eastern one. Nothing is stored yet — B1 Part B has not
   run and no customer exists — so this is a question about the *first* anchor
   rather than a correction to a stored one. That is the cheap moment to answer
   it.
3. **`stripe_meter`'s event timestamp** is `datetime.fromisoformat(sent_at)
   .timestamp()`, which applies the process offset. Rows written under UTC and
   metered under Eastern are stamped four hours earlier than they were before,
   which can move an event across a Stripe period boundary. Nothing is metered
   yet, for the same reason.

**And the stored rows are of two kinds.** Every `sent_at` written before this
deploy is UTC wall clock; every one after is Eastern. They are not comparable
across the boundary, and the error is one-directional — an old row reads up to
five hours later than it happened. This is the third instance in this project of
one column with two clocks (`contact_list_members.added_at`,
`contact_lists.created_at`), and unlike those two it is a column an invoice is
computed from.

## Options

1. **Leave the history alone; the new clock applies from here.** / cost: rows
   either side of the deploy are not comparable, and one already-settled cycle
   (August, invoiced by hand under B1) was computed on UTC dates. Nothing is
   re-billed, and the residue is a handful of evening sends attributed to the
   cycle next door — permanently, in an unknown direction.
2. **Convert `sms_messages.sent_at` for every row written before the deploy**,
   the way `f4a1c7d90e52` converted `contact_lists.created_at`. / cost: the
   classification cannot be read off the format — both writers spell it
   identically, so the only discriminator is "written before the deploy", which
   is a fact nothing in the row records. It would have to be a timestamp cut-off
   chosen by hand, and a wrong one silently moves real money.
3. **Pin the billing clock to UTC and leave the display clock Eastern.** Give
   `billing_service` and the Stripe modules their own converter rather than the
   process zone. / cost: a second clock, deliberately, in the two places this
   project has least appetite for one — and the client's invoice would then be
   cut at 8:00 PM his time, which is unexplainable to him.

## Recommendation

**Option 1, with the anchor question answered before B1 Part B runs.** The
history is not worth a hand-picked cut-off: the affected set is evening sends on
a cycle boundary, the money is cents, and option 2 trades a bounded known error
for an unbounded unknown one. What genuinely needs an answer first is item 2 —
the anchor Stripe will be given is computed once, and it is computed after this
change, so "which day is the cycle anchor" should be decided deliberately rather
than inherited from whichever zone the box happened to be in on subscription day.
