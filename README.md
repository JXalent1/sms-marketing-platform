# SMS Marketing Platform — skeleton

A white-label SMS marketing platform: contacts, lists, campaigns, opt-out
compliance, delivery tracking, and usage-based billing. FastAPI + SQLAlchemy +
Jinja/Tailwind, deployable on one small VPS.

This is a **skeleton for building client-specific tools**, extracted from a
system that sent ~280,000 messages across 86 campaigns over eight months. The
generic parts are complete and working; the two client-specific seams — where
contacts come from, and which carrier sends — are pluggable.

## Start here

```bash
cp .env.example .env

python3 -m venv .venv                # everything below runs inside this venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt

npm install                          # build-time only: Tailwind + the Inter font files
npm run build:css                    # writes app/static/app.css

alembic upgrade head                 # create/upgrade the database schema
uvicorn app.main:app --reload        # http://localhost:8000

python scripts/seed_demo_data.py     # optional sample contacts
python -m pytest tests/ -v
```

Default login is whatever you set as `ADMIN_PASSWORD` in `.env`.

**Use the venv for everything, including pytest.** The suite is not pinned to
whatever `pytest` happens to be on `PATH`: a global conda environment with an
unrelated broken plugin has already taken this suite down once, for a reason
that had nothing to do with the code. `requirements-dev.txt` pins the test
dependencies; `requirements.txt` deliberately does not carry them, so production
images stay free of a test runner.

### Front-end build

Tailwind is compiled at build time into `app/static/app.css`, which is
**gitignored** — it is an artifact, not source. Inter is self-hosted from
`app/static/fonts/`. Nothing is fetched from a CDN at runtime, because with the
Tailwind Play CDN unreachable the entire app rendered as raw unstyled HTML.

| Command | What it does |
|---|---|
| `npm run build:css` | One-off compile. Run after editing templates or `app/assets/tailwind.css` |
| `npm run watch:css` | Recompile on save while working on templates |
| `npm run fonts:sync` | Re-copy the four Inter weights into `app/static/fonts/` |

`deployment/deploy.sh` runs `build:css` before syncing, so a deploy cannot ship
a dashboard with no stylesheet.

Colors are CSS custom properties defined in `app/assets/tailwind.css` and
exposed to Tailwind as utilities (`bg-brand`, `text-ink`, `border-line`). **Dark
is the default theme**; light is opt-in via `data-theme="light"` on `<html>`.
Never write a hex value into a template — set `BRAND_COLOR_HEX` in `.env`.

### Migrations

Alembic owns the schema. Nothing else creates a table.

Schema changes go through `alembic revision --autogenerate -m "..."` then
`alembic upgrade head`. Outside production the app runs `alembic upgrade head`
itself at startup, so a fresh clone just works; production never migrates on
startup — `deployment/deploy.sh` runs it explicitly before the restart, because
two workers coming up together would race each other.

`Base.metadata.create_all()` used to run at import. It built every table and
wrote no `alembic_version` row, which is why the app would start fine and
`alembic upgrade head` would then fail with *table app\_settings already
exists*. Worse, once a new model existed, merely starting the app created its
table and the migration meant to create it never ran.

**If you have a database from before that change** — anything the app started
against prior to session 1b — it has the tables and no version row. Stamp it
once, then upgrade normally forever after:

```bash
alembic stamp head                   # ONLY for a pre-existing, unversioned database
alembic upgrade head
```

Startup detects this case and logs exactly that instruction rather than
guessing; it will not touch the schema until you have stamped it. Do not run
`stamp` on a fresh database — that would mark migrations as applied when they
have not been, and the tables would never be created.

The test suite builds its scratch database with `alembic upgrade head` too, so
a migration that does not apply, or that disagrees with the models, fails the
suite (`tests/test_migrations.py`) rather than waiting for production.

`SMS_PROVIDER` defaults to `console`, which logs messages instead of sending
them. **Nothing can reach a real phone until you change that**, which is
deliberate — a fresh checkout or a deploy with broken credentials cannot text
6,000 strangers.

Then follow [docs/NEW_CLIENT_CHECKLIST.md](docs/NEW_CLIENT_CHECKLIST.md).

## Documentation

| Doc | Read it when |
|---|---|
| [NEW_CLIENT_CHECKLIST.md](docs/NEW_CLIENT_CHECKLIST.md) | Standing this up for a client — start to launch |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | Before changing anything structural |
| [SMS_LESSONS.md](docs/SMS_LESSONS.md) | **Before you remove a safety check you think is redundant** |
| [API.md](docs/API.md) | Wiring a frontend or integrating |

## What's included

**Contacts** — phone-keyed and deduplicated, arbitrary per-client attributes,
named lists, CSV import with forgiving column matching and a pre-import preview.

**Campaigns** — merge tags, audience selectors, batch limits, background sending,
per-message status tracking, grouped failure analysis.

**SMS layer** — provider-agnostic (Telnyx, Twilio, console dry-run included),
encoding-aware segment counting, E.164 normalization, out-of-region filtering,
shortener-link detection.

**Compliance** — STOP/START/HELP keywords, a blocklist you own independently of
the carrier, auto-blocking on hard delivery failures.

**Delivery tracking** — webhooks record what the carrier actually did, so
"sent" counts stay honest.

**Billing** — cycle-based usage metering: a configurable monthly fee, an
included-segment allowance and a flat per-segment rate above it, with
carrier-reported segment counts as the billing basis.

**Safety** — auth on every route, rate limits, pre-flight balance check, low-balance
alerts, dry-run by default.

## Deliberately not included

The scraper. The original system's contact ingestion was ~930 lines of Playwright
automation for one specific auction site, wired directly into the models and the
dashboard. It has been replaced by the `ContactSource` interface
(`app/sources/`), with a working CSV implementation and a documented API example.
Write your client's ingestion behind that interface and nothing downstream needs
to know.

Also absent: multi-tenancy, MMS, per-user roles, A/B testing, link tracking. All
of them fit the existing structure; none were needed by the system this came from.

## Layout

```
app/
  core/          config, database, auth, logging, branding
  models/        contact, contact_list, campaign, sms_message, blocked_number
  sms/           ★ carrier abstraction — segments, phone, compliance
    providers/     telnyx, twilio, console
  services/      campaign, contact, blocklist, billing
  sources/       ★ pluggable contact ingestion
  routers/       HTTP layer + webhooks
  templates/     white-labeled Jinja pages
deployment/      nginx, systemd, bootstrap + deploy scripts
scripts/         backup, balance alert, password hashing, demo seed
tests/           end-to-end smoke suite
```

The two ★ directories are the seams you'll customize. Everything else should
work unchanged.

## Configuration

Every client-specific value lives in `.env` — brand strings, credentials,
pricing, sending behaviour. No client name appears in any `.py` or `.html` file.
Rebranding is an env edit and a restart.

```
BRAND_NAME=Acme Auto Group
BRAND_COLOR_HEX=#123A6B
SMS_PROVIDER=console
BILLING_MONTHLY_FEE=0
BILLING_SEGMENTS_INCLUDED=10000
BILLING_PRICE_PER_SEGMENT=0.015
```

## Deployment

One small VPS. Ubuntu, nginx in front, systemd keeping uvicorn up, SQLite on
local disk with a nightly off-box backup.

Both scripts take `--dry-run`, which prints every step and touches nothing. Read
the dry run before you point either at a real box.

### First time: bring up the server

```bash
# on the server, as a sudo-capable user
./deployment/bootstrap.sh --dry-run --domain sms.example.com   # read it first
./deployment/bootstrap.sh --domain sms.example.com
```

That installs Python 3.12, Node, nginx and certbot; creates a non-root service
user; renders `nginx.conf.template` and `app.service.template` with the real
domain and paths; and installs the nightly backup cron entry.

It deliberately stops short of writing `.env`, running migrations, or issuing a
certificate. It prints those as remaining steps. `.env` carries the sending
credentials and `SMS_PROVIDER` stays `console` until a human changes it.

### Every time after: deploy

```bash
./deployment/deploy.sh --dry-run                               # read it first
SERVER=appuser@host SERVICE=a4a-sms ./deployment/deploy.sh
```

Deploys build the stylesheet locally and ship it as a file — the server carries
no Node toolchain at runtime. The sync excludes `data/` and `.env` on purpose:
those belong to the server, and overwriting either from a laptop is how you lose
a client's contact list.

Migrations run from the deploy script before the restart, never from the app on
startup, so two workers restarting together cannot race into the same migration.

### Backups

```bash
./scripts/backup.sh --verify        # back up, then restore it and check it
./scripts/backup.sh --verify-only   # check the newest existing archive
```

Backups use SQLite's online backup API rather than copying the file, so an
archive taken mid-campaign is still consistent. `--verify` gunzips the archive,
runs `PRAGMA integrity_check`, and asserts the restored database actually has
tables — integrity_check alone passes on an empty file, which is exactly the
failure worth catching. Cron runs `--verify` nightly for that reason.

Set `BACKUP_REMOTE=user@host:/path` to copy each archive off-box. A backup on
the same disk as the database survives a bad deploy but not a dead droplet.

### Rollback

**To stop a campaign in flight:** stop the service. `sudo systemctl stop
a4a-sms`. Sending is in-process, so nothing further goes out. Restart when you
have worked out what went wrong; a stopped campaign does not resume itself.

**To restore yesterday's database:**

```bash
sudo systemctl stop a4a-sms
cp data/app.db data/app.db.before-restore          # keep the bad one
gunzip -c backups/app-YYYYmmdd-HHMMSS.db.gz > data/app.db
./scripts/backup.sh --verify-only                   # confirm before restarting
sudo systemctl start a4a-sms
```

Keep the database you are replacing. Restoring the wrong archive is recoverable;
restoring over the only copy is not.

**To roll back code:** deploy from the previous commit. Migrations are the
exception — if the bad deploy applied one, check whether it has a working
`downgrade` before assuming a code rollback is enough.

## Before you ship

Run the tests, then walk the checklist at the end of
[NEW_CLIENT_CHECKLIST.md](docs/NEW_CLIENT_CHECKLIST.md). The two that matter
most: confirm the dashboard actually requires a login, and confirm the carrier
webhook is configured. The first has already caused one real incident; the second
silently disables both opt-out handling and delivery tracking.
