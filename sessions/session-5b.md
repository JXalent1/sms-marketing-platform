# Session 5b — Go live

## Objective

The build is finished: 119 tests, three migrations, gate green. This session puts it on a
server, loads his real contacts, and gets one real message delivered to one real handset.

**This session is half agent work and half human work, and the split is deliberate.**
The agent prepares everything in dry-run. A human creates the server, writes the real
credentials, flips the provider, and sends the first message. Nothing in the agent's half
may cause a message to be sent.

## Prerequisites

- `CLAUDE.md` read in full.
- Everything merged. `bash agent/gate.sh` green — **119 tests**.
- `source .venv/bin/activate` first. A gate run under conda is not a gate run.

---

# Part A — agent work

## A1. Finish the 5a gaps

Session 5a built without its spec (it wasn't committed when the worktree was cut), so it
worked from the `modules.md` row and missed items the spec named. Read
`sessions/session-5a.md` and close these:

**`docs/RUNBOOK.md`** — doesn't exist. Operational, for us, not the client. Real commands:
deploy · roll back a bad deploy · restore a backup · **stop a campaign mid-send** ·
rotate the carrier credential · read the logs · check the certificate · disk full.

**`deployment/deploy.sh`** — five hardening items:
- Run `npm run fonts:sync` alongside `build:css`. The existing `rsync --delete` will strip
  server-side fonts if they aren't rebuilt locally first, and the UI silently drops to
  `system-ui`.
- Run `alembic upgrade head` **before** restarting, and abort the deploy if it fails.
- Back up the database immediately before migrating.
- Health-check `/health` after restart; roll the restart back if it fails.
- Refuse to deploy from a dirty working tree.

## A2. Local environment

`.env` sets `ENVIRONMENT=production`, so a fresh local `./run.sh` 500s on every page until
someone runs `alembic upgrade head`. Wrong default for a dev machine and a bad first
impression for the next person who clones this. Fix `.env.example`, and document the two
commands in `README.md`.

## A3. Monitoring

Register in the `lifespan` block in `app/main.py`:

- **Low-credit alert.** Below the configured threshold, notify. This is the guard against
  the failure that cost the previous client 19,375 messages across eight campaigns.
- **Daily failure digest.** Yesterday's sends grouped by failure reason. Grouped, not a
  list of every failed number — the point is spotting a pattern.

Both go through `agent/notify.sh`. Neither may name the carrier in anything the client
could see.

## A4. Production configuration checklist

`deployment/PRODUCTION_CHECKLIST.md`: every environment variable that must be set for
real, what it does, and what breaks if it's wrong. Placeholders only — **no real
credentials, numbers or hostnames in any committed file.**

Call out explicitly:
- `SMS_PROVIDER` — stays `console` through deploy; a human changes it
- `PUBLIC_BASE_URL` — the webhook URLs are built from it; wrong value means no delivery
  receipts and, worse, no inbound STOP
- `BRAND_*`, `BILLING_*` — his terms
- `ALERT_PHONE`, `BALANCE_ALERT_THRESHOLD`

## A5. Documentation cleanup

`docs/API.md` still documents the uncategorised import endpoints that 3b retired. Module 8
owned that file and module 8 is deferred, so it falls here. Update it to the
`/api/imports/preview` → `/api/imports/commit` flow.

Then re-read `docs/CLIENT_GUIDE.md` against what actually shipped — 5a wrote it before
Today, Contacts and the composer existed. Fill the `[SCREENSHOT: ...]` markers with real
captures. No carrier name.

## A6. The three screens that are still light cards on a dark page

Settings, Opt-outs and Usage still carry the skeleton's `bg-white` / `text-gray-*`
classes. Module 8 owned their redesign and module 8 is deferred — but all three are in the
nav, so the client will click them on day one. They look broken.

`settings.html` also renders a stray `<h1>Settings</h1>` **below** the shell's top-bar
heading, so the word appears twice. 3a changed `{% block title %}` from the document title
to the top-bar heading; that page kept its own heading as well.

Port all three onto the dark tokens (`bg-surface`, `text-ink`, `border-line`,
`bg-surface-2`) and delete the duplicate headings. This is a mechanical class swap, not a
redesign — do not restructure the pages.

## A7. The character counter measures the wrong string

`preflight_service.build_report()` calls `describe(message_template)` — the **raw
template**, with `{first_name}` counted as 12 literal characters. The message that
actually reaches a handset has a name there instead.

Usually this over-counts and the estimate is merely pessimistic. The failure case is a
template sitting just under a segment boundary: 158 characters including `{first_name}`
reports 1 segment per message, but a recipient called Christopher renders to 167 and costs
2. Every such contact is billed at double the quoted rate, and the first anyone knows is
the invoice.

- Keep the live keystroke counter on the template — it must stay cheap — but label it as
  an estimate and state that merge tags change the final length.
- **Pre-flight must be exact.** It is a deliberate action against a resolved audience, so
  render the template per recipient and sum the real segments. Report the true total, and
  flag when rendering pushes any recipient over a segment boundary the template didn't
  predict.
- Test it: a template at 158 characters with `{first_name}`, an audience containing both a
  2-character and an 11-character name, asserting pre-flight reports the higher true total
  rather than the template's.

This is billing accuracy, not billing policy — the rate, allowance and billable-status set
are untouched, so it is in scope rather than an escalation.

## Part A acceptance

- [ ] `bash agent/gate.sh` green at start (119) and end — shown both times
- [ ] Suite twice in a row, green both
- [ ] `bash -n` clean on every script; `deploy.sh --dry-run` and `bootstrap.sh --dry-run`
      both exit 0 with no side effects
- [ ] `deploy.sh --dry-run` output shows the migrate-before-restart order, the pre-migrate
      backup, the fonts sync, the health check and the dirty-tree refusal
- [ ] Scheduler registers both jobs on startup — log line shown
- [ ] A simulated low balance triggers the alert path — shown, without sending anything
- [ ] `grep -rniE "telnyx|twilio" docs/CLIENT_GUIDE.md docs/RUNBOOK.md` → nothing
- [ ] `grep -rn "SMS_PROVIDER" deployment/` shows it is never set to a live provider
- [ ] Settings, Opt-outs and Usage render on dark tokens — screenshots of all three
- [ ] "Settings" appears once on the settings page, not twice
- [ ] Pre-flight segment test passes with the 158-character / long-name case — shown
- [ ] `status.md` and `handoff.md` updated

---

# Part B — human work (Jordan, not the agent)

Do these in order. **No skipping**, particularly B5.

### B1. Server
Create the droplet (Ubuntu 24.04, 2 GB). Point the DNS A record. Run `bootstrap.sh`.
Confirm the certbot renewal timer is **active** — the previous client's certificate sat 45
days from expiry with nobody watching.

### B2. Configure
Write the production `.env` from `PRODUCTION_CHECKLIST.md`. **`SMS_PROVIDER=console`.**
Run `alembic upgrade head` against the empty production database.

### B3. Deploy in dry run
`deploy.sh`. Open the live domain and click every screen. It should behave exactly as it
does locally, and the top-bar pill should read **Dry run**.

### B4. Load the real contacts
Import his CSVs, one per category, through the category-first flow. Check the preview
counts against the source files *before* committing each one. If a count looks wrong, stop
— that's the moment to catch it, not after a blast.

### B5. The send sequence — in this order

1. Set the live provider. Confirm the pill flips to **Live**.
2. Send **one** message to your own phone. Confirm it arrives *and* that the delivery
   webhook recorded it. If the webhook doesn't land, `PUBLIC_BASE_URL` is wrong — fix it
   before going further.
3. Reply **STOP** from that handset. Confirm it lands in the blocklist and that a
   follow-up send to that number is skipped, not sent.
4. Send to **one category, capped at 50**. Check the delivery rate and that the billed
   segment count matches what pre-flight predicted.
5. Only now, hand him the login.

Step 3 before step 4 is not negotiable. Opt-out handling is the one thing that must work
on day one, and it's cheaper to prove it with your own phone than with fifty of his buyers.

### B6. Before the first real blast
Confirm carrier auto-recharge: **$200–300 per top-up on a card, ~$100 threshold.** The
previous client had $10 top-ups via PayPal, which drained mid-blast eight separate times
and cost 19,375 messages. Pre-fund before anything large.

---

## Constraints

- Nothing in Part A may send a message. `SMS_PROVIDER` stays `console` in every file the
  agent touches.
- No real credential, phone number, hostname or IP in a committed file.
- `agent/gate.sh` and `agent.config.sh` are human-only.
- Scripts must be safe to re-run. A deploy script that works once fails at 2am.
- If the spec is wrong, verify before following it.
