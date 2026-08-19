# Session 5a — Deploy scaffolding

## Objective

Everything needed to put this on a server, written and tested *before* launch day, so
go-live (session 5b) is "run the script, import the contacts, send a test" rather than
"start building a deployment."

**No application code.** You will not touch anything under `app/`.

**You are running in a git worktree, in parallel with session 2 (categories backend).**
Session 2 owns `app/`, `alembic/` and `tests/`. Stay out of those and there is nothing to
merge-conflict on.

## Prerequisites

- `CLAUDE.md` read in full.
- Modules 1 and 1b complete. Run `bash agent/gate.sh` first and show it green — 46 tests.
  Your work must leave it green; since you are not touching `app/`, that should be free.

## Target

A single fresh **Ubuntu 24.04 DigitalOcean droplet**, 2 GB / 1 vCPU. SQLite on local disk
— the prior client's deployment ran 280,000 messages and 50,000 contacts on SQLite without
trouble, and a managed database is cost we do not need. nginx terminates TLS and proxies
to uvicorn under systemd.

`deployment/nginx.conf.template` and `deployment/app.service.template` already exist from
the skeleton. **Finish those rather than inventing a new shape.**

## Scope

### 1. `deployment/bootstrap.sh` — one-time server bring-up

Idempotent. Safe to re-run. Takes the domain and service user as arguments; hardcodes
nothing client-specific.

- Create a non-root service user; the app never runs as root
- System packages: Python 3.12 + venv, Node (for the Tailwind build), nginx, certbot, git
- Clone the repo, create `.venv`, install `requirements.txt`
- `npm install && npm run build:css && npm run fonts:sync`
- Render the nginx and systemd templates with the real domain and paths, install, enable
- Obtain a certificate via certbot; confirm the renewal timer is **active** — the prior
  client's certificate was 45 days from expiry with nobody watching
- Create `data/`, `logs/`, `exports/` owned by the service user
- Do **not** write `.env`, and do **not** start the app with a live carrier. Print the
  next steps instead.

### 2. Harden `deployment/deploy.sh` for updates

It already refuses to guess about stamping a pre-Alembic database — keep that. Add:

- `git pull`, install deps, `npm run build:css` **and** `npm run fonts:sync` (the existing
  script builds CSS but not fonts, and the `rsync --delete` that follows would strip
  server-side fonts, silently dropping the UI back to `system-ui`)
- Run `alembic upgrade head` **before** restarting, and abort the deploy if it fails
- Back up the database immediately before migrating
- Restart via systemd, then health-check `/health` and roll back the restart if it fails
- Refuse to deploy if the working tree is dirty

### 3. `scripts/backup.sh` — nightly, off-box

The prior client's 181 MB production database had **no backup at all.**

- `sqlite3 .backup` (not `cp` — a live SQLite file copied mid-write is not restorable)
- Timestamped, gzipped, 30-day local retention
- Off-box copy: support an `rsync`/`scp` target and an S3-compatible target, whichever is
  configured; skip with a loud warning if neither is
- A `--verify` mode that restores the newest backup to a temp file and runs
  `PRAGMA integrity_check` plus a row count on `contacts`. **A backup nobody has restored
  is not a backup.**
- A cron line, documented, not installed

### 4. `docs/RUNBOOK.md` — for us

Short and operational. How to: deploy, roll back, restore a backup, stop a campaign
mid-send, rotate the carrier credential, read the logs, check the certificate, what to do
when the disk fills. Real commands, no prose padding.

### 5. `docs/CLIENT_GUIDE.md` — for him

How to use the product, in his language. Uploading a list per category, drafting a
campaign, reading the results, what opt-outs mean, how billing works (10,000 segments
included, then $0.015 each, no monthly fee).

**No carrier name anywhere in it**, no server or deployment detail, no jargon. Leave
`[SCREENSHOT: ...]` markers where screens should go — the screens land in module 3 and
we'll fill them at 5b.

### 6. `README.md` — deployment section

Fresh-server bring-up, the update path, and the environment variables that must be set in
production, each with what it does and what breaks if it is wrong.

---

## Acceptance criteria (demonstrate each in the transcript)

- [ ] `bash agent/gate.sh` green at start and end — output shown
- [ ] `bash -n` clean on every shell script; `shellcheck` clean if available
- [ ] `bootstrap.sh` and `deploy.sh` both run correctly with `--dry-run` (add the flag if
      it doesn't exist) — output shown, no side effects
- [ ] `scripts/backup.sh` run against the local dev database produces a gzipped file, and
      `--verify` restores it and passes `PRAGMA integrity_check` — output shown
- [ ] `grep -rniE "telnyx|twilio" docs/CLIENT_GUIDE.md` returns nothing
- [ ] `git diff --stat` shows **no files under `app/`, `alembic/` or `tests/`**
- [ ] `status.md` and `handoff.md` updated — append, don't rewrite; session 2 is editing
      them in parallel and yours will be merged alongside

## Constraints

- **Touch nothing under `app/`, `alembic/` or `tests/`.** If a change there seems
  necessary, stop and note it in `status.md` under "Found while working" for 5b.
- `agent/gate.sh` and `agent.config.sh` are human-only.
- No real credentials, hostnames, IPs or phone numbers in any committed file. Placeholders
  and a documented checklist only.
- Nothing in this session may cause a message to be sent. `SMS_PROVIDER` stays `console`
  everywhere you touch.
- Scripts must be safe to re-run. A deploy script that only works once is a deploy script
  that fails at 2am.
