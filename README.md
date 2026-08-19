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

Schema changes go through Alembic (`alembic revision --autogenerate -m "..."`,
then `alembic upgrade head`). `Base.metadata.create_all()` still runs at import
for local convenience, but it cannot alter an existing table, so it is not a
substitute for a migration.

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
deployment/      nginx, systemd, deploy script
scripts/         balance alert, password hashing, demo seed
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

## Before you ship

Run the tests, then walk the checklist at the end of
[NEW_CLIENT_CHECKLIST.md](docs/NEW_CLIENT_CHECKLIST.md). The two that matter
most: confirm the dashboard actually requires a login, and confirm the carrier
webhook is configured. The first has already caused one real incident; the second
silently disables both opt-out handling and delivery tracking.
