"""Application entry point.

Deliberately thin — it wires modules together and holds no business logic, so
adapting this to a new client means editing modules, not untangling this file.
"""

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from contextlib import asynccontextmanager
import os
import logging

# The process zone first, and before `setup_logging()`: `logging_config` names
# the day's log file from `datetime.now()`, so on a UTC droplet a boot between
# 8 PM and midnight Eastern would open tomorrow's file. Importing config is what
# applies the zone — it is the one module every entry point imports, which is why
# it owns that side effect.
from app.core import clock                            # noqa: F401 — applies the zone
from app.core.config import settings

from app.core.logging_config import setup_logging
setup_logging()
from app.core.database import engine
from app.services import link_service
from app.sms.factory import get_provider
import app.models                                    # noqa: F401 — registers tables

from app.routers import (pages, dashboard, campaigns, campaign_preview,
                         campaign_uploads, contacts, categories, imports,
                         blocklist, usage, reports, links, prospects, billing,
                         settings as settings_router)
from app.routers.webhooks import telnyx as telnyx_webhooks, twilio as twilio_webhooks

logger = logging.getLogger("app")

for directory in ("data", "logs", "exports"):
    os.makedirs(directory, exist_ok=True)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _alembic_config():
    """An Alembic config that does NOT reconfigure logging.

    Built without alembic.ini on purpose: `Config("alembic.ini")` sets
    config_file_name, which makes alembic/env.py call fileConfig() — and
    fileConfig disables every existing logger, silently killing the handlers
    setup_logging() just installed. Passing script_location directly gives the
    same migrations with none of that; env.py takes the URL from settings.
    """
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", os.path.join(PROJECT_ROOT, "alembic"))
    return cfg


def _ensure_schema() -> None:
    """Bring the database up to head. Alembic owns the schema; nothing else does.

    This used to be `Base.metadata.create_all()`, which builds every table but
    writes no `alembic_version` row. The damage shows up later, not here: any
    database the app had ever started against then failed `alembic upgrade head`
    with "table app_settings already exists", and — the expensive part — the
    moment a new model lands, merely *starting the app* creates its table
    silently, so the migration meant to create it never runs and every later
    migration inherits a schema Alembic has no record of. Whichever order the
    developer happened to use decided whether it worked.

    Production does not migrate on startup: two workers restarting together
    would race, and a schema change there should be a deliberate step. deploy.sh
    runs `alembic upgrade head` explicitly before the restart.
    """
    from sqlalchemy import inspect
    from alembic import command

    tables = set(inspect(engine).get_table_names())

    if tables - {"alembic_version"} and "alembic_version" not in tables:
        # Pre-Alembic database, i.e. one built by the old create_all(). Upgrading
        # would try to re-create tables that exist and fail; guessing on its
        # behalf could drop data. Tell the operator the one command that fixes it.
        logger.error(
            "Database has tables but no Alembic version. It predates migrations: "
            "run `alembic stamp head` once (then `alembic upgrade head`). "
            "Schema left untouched."
        )
        return

    if settings.ENVIRONMENT == "production":
        if "alembic_version" not in tables:
            logger.error("Database is not migrated. Run `alembic upgrade head`.")
        return

    command.upgrade(_alembic_config(), "head")


_ensure_schema()

# The client's zone, passed rather than inherited — and this is **not** a guard,
# which the mutation harness is what established. `app.core.config` sets the
# process timezone at import, long before this line runs, so a bare
# `AsyncIOScheduler()` would infer the same zone and no test can tell the two
# apart. It is here for two smaller reasons, and the comment says so rather than
# reading as a rule somebody would later defend: a reader of this file can see
# what zone the scheduler keeps without knowing about a side effect in another
# module, and `apply_process_timezone()` is a no-op on a platform without
# `time.tzset()`, where this argument would be the only thing left saying it.
# What the zone fixes here is the log — every job time printed as UTC.
scheduler = AsyncIOScheduler(timezone=clock.ZONE)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ────────────────────────────────────────────────────────────
    provider = get_provider()
    logger.info(f"{settings.BRAND_APP_NAME} starting | env={settings.ENVIRONMENT} "
                f"| provider={provider.name}")
    if provider.name == "console":
        logger.warning("SMS provider is 'console' — messages are logged, NOT sent.")

    # One line about the host guard on every boot, because its two failure modes
    # are both silent on the screen. Either the short domain is unset and the
    # {link} tag is refused at compose time, or it is set and that name serves
    # nothing but redirects — and a reader of the log should not have to infer
    # which from the absence of a message.
    if link_service.short_domain_conflicts():
        logger.error(
            "SHORT_LINK_DOMAIN is the same host as PUBLIC_BASE_URL (%s). The "
            "short-link host guard is DISABLED, because enabling it would 404 "
            "every page of the admin panel. Set SHORT_LINK_DOMAIN to the "
            "dedicated short domain, or leave it blank.",
            link_service.primary_host(),
        )
    elif link_service.configured():
        logger.info("Short-link host guard active | %s serves the redirect "
                    "route only", link_service.domain())
    else:
        logger.info("SHORT_LINK_DOMAIN is unset | short links are off and the "
                    "host guard is a no-op")

    # One line about Stripe on every boot, for the host guard's reason: its bad
    # state is silent on every screen. A box with a secret key and no webhook
    # secret takes payments and stores **nothing** — `verify_webhook()` fails
    # closed, deliberately, because an unsigned payload writes the customer id
    # this client's segments are billed to. That is the right refusal and the
    # wrong thing to discover from an invoice, so it is loud here.
    from app.services import stripe_billing
    if stripe_billing.configured() and not settings.STRIPE_WEBHOOK_SECRET:
        logger.error(
            "STRIPE_SECRET_KEY is set and STRIPE_WEBHOOK_SECRET is not. Every "
            "Stripe webhook will be IGNORED, so a completed checkout records no "
            "customer and no usage is ever metered. Set the whsec_… value from "
            "Developers -> Webhooks and restart."
        )
    elif stripe_billing.configured():
        logger.info("Stripe billing active | metered price %s | webhook "
                    "signature required", settings.STRIPE_PRICE_METERED)
    else:
        logger.info("Stripe is not configured | /subscribe says so and the "
                    "checkout endpoint answers 503")

    # Register scheduled jobs here. Give every job an explicit id and
    # replace_existing=True, or a reload quietly stacks duplicates that all fire
    # at once.
    #
    # Scheduled campaigns. The job only *finds* what is due — it hands each
    # campaign to the same `send_campaign()` the Send button reaches, so a
    # scheduled blast gets the capacity pre-flight, the blocklist, the region
    # filter and recent-contact suppression exactly as an on-demand one does.
    # Nothing about scheduling may become a second, thinner send path.
    #
    # A minute is the resolution: he schedules "Thursday 9am", not "09:00:07",
    # and a tighter tick would spend a query a second on an empty table.
    # max_instances=1 so a long blast cannot have a second copy of itself
    # started underneath it.
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger
    from app.services.campaign_dispatch import run_due_campaigns
    from app.services import monitoring_service
    from app.services import stripe_tiers as billing_tiers

    scheduler.add_job(
        run_due_campaigns,
        IntervalTrigger(minutes=1),
        id="scheduled_campaigns", replace_existing=True, max_instances=1,
    )

    # Monitoring. Both alert through agent/notify.sh, which is the operator's
    # pager and not a client-facing surface; neither can send a campaign
    # message. See app/services/monitoring_service.py for why the low-balance
    # warning does not go over the carrier account it is warning about.
    #
    # Hourly, not per-minute: a balance moves at the speed of a campaign, and
    # the threshold is set high enough that an hour of warning is still hours of
    # headroom. coalesce so a laptop waking from sleep fires one check, not the
    # twelve it slept through.
    scheduler.add_job(
        monitoring_service.low_balance_job,
        IntervalTrigger(hours=1),
        id="low_balance_alert", replace_existing=True, max_instances=1,
        coalesce=True,
    )

    # 07:00 local, so yesterday is complete and the digest is on screen before
    # the day's first campaign is written rather than after it.
    scheduler.add_job(
        monitoring_service.failure_digest_job,
        CronTrigger(hour=7, minute=0),
        id="daily_failure_digest", replace_existing=True, max_instances=1,
        coalesce=True,
    )

    # The billing tier, once a day. The allowance lives in two systems — this
    # repo and a tier in Stripe's dashboard — and a disagreement is a mispriced
    # invoice, which is the one artefact the client audits.
    #
    # Session B1 first ran this check only at checkout, which meant a tier
    # edited six months later was never detected: the exact failure the check
    # exists to prevent, arrived at through the check rather than through its
    # absence. Daily rather than hourly because a price in a dashboard moves at
    # the speed of a person, and `tier_verdict()` ages an unrefreshed agreement
    # into `stale` on its own, so a box whose scheduler died says so rather than
    # quoting the answer it got in September.
    #
    # 06:00, before the failure digest, so an operator reading one signal at
    # breakfast has both. coalesce so a laptop waking from sleep runs one check.
    scheduler.add_job(
        billing_tiers.daily_tier_check,
        CronTrigger(hour=6, minute=0),
        id="daily_tier_check", replace_existing=True, max_instances=1,
        coalesce=True,
    )

    # Usage metering, hourly. The one place a segment reaches the billing
    # meter. It is a scheduled adjudication and not a call in the send path:
    # a row is reported only once its delivery status has settled, and it is
    # marked in the database so that no path — this job, a redeploy, a
    # backfill — can report it twice. `decisions/011` is the reasoning.
    #
    # A sync function, so APScheduler runs it on its executor thread rather
    # than on the event loop the send path shares. Hourly because the settle
    # window is measured in hours and the event is stamped with the send time,
    # so nothing about *when* this runs moves usage between cycles. It never
    # raises: a job that raises is a job APScheduler stops running, and a
    # meter that quietly stopped is the failure nobody's screen would show.
    from app.services import stripe_meter as billing_meter

    scheduler.add_job(
        billing_meter.metering_pass_job,
        IntervalTrigger(hours=1),
        id="usage_metering", replace_existing=True, max_instances=1,
        coalesce=True,
    )

    scheduler.start()
    for job in scheduler.get_jobs():
        # One line per job, by id. "Scheduler started" told us a scheduler
        # exists; it did not tell us which jobs actually registered, and a job
        # that silently failed to register looks exactly like a quiet night.
        logger.info(f"Scheduled job registered | id={job.id} | trigger={job.trigger}")
    logger.info(f"Scheduler started | {len(scheduler.get_jobs())} jobs registered")

    yield

    # ── Shutdown ───────────────────────────────────────────────────────────
    scheduler.shutdown(wait=False)


app = FastAPI(
    title=settings.BRAND_APP_NAME,
    version="1.0.0",
    debug=settings.DEBUG,
    lifespan=lifespan,
    # API docs disabled: they published a complete map of every endpoint,
    # which is exactly what an unauthenticated visitor used to find the send API.
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

# Compiled stylesheet and self-hosted fonts. app/static/app.css is a build
# artifact (`npm run build:css`) and is gitignored, so a deploy that skips the
# build serves a 404 here rather than silently falling back to a CDN.
app.mount("/static", StaticFiles(directory="app/static"), name="static")

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.middleware("http")
async def short_link_host_guard(request, call_next):
    """On the short-link domain, only the redirect route exists.

    The short domain and the admin panel are one process behind one nginx, so
    without this every route answers on both and `bida4a.com/login` is the
    client's whole contact list on the domain printed in every text message.

    **Why here and not an nginx path denylist.** A denylist has to be updated
    every time a route is added, in a file that knows nothing about the routes —
    it is wrong the first time somebody adds a page and nobody notices, because
    the failure is silent and on the wrong host. This asks the opposite
    question: is the path positively a link? Anything new is excluded by
    default. `link_service` owns both halves of the answer because it already
    owns the domain and the slug shape; main.py stays wiring, as its own
    docstring requires.

    Middleware rather than a dependency: it has to run *before* routing, or the
    404 would come from a route that had already matched, and a mount like
    `/static` has no dependency to hang one on.

    The body is `links.UNKNOWN_LINK` — the same 404 an expired slug gets. A
    probe of `bida4a.com/dashboard` and a probe of `bida4a.com/aaaaaaaa` are
    then indistinguishable, so scanning the short domain reveals nothing about
    what else is behind the process.

    A no-op when `SHORT_LINK_DOMAIN` is unset, which is every box without a
    short domain, a fresh clone, and the suite.
    """
    if (link_service.is_short_link_host(request.headers.get("host"))
            and not link_service.is_slug_path(request.url.path)):
        return PlainTextResponse(links.UNKNOWN_LINK, status_code=404)
    return await call_next(request)

app.include_router(pages.router)
app.include_router(dashboard.router)
app.include_router(dashboard.api_router)
app.include_router(campaigns.router)
# The campaign-first flow shares /api/campaigns and is registered after it, so
# `POST /{campaign_id}/top-up` cannot shadow anything campaigns.py already owns.
app.include_router(campaign_uploads.router)
# The keystroke quote, `POST /preview`, split out in 5n for the 500-line rule.
# A fixed path, so its position relative to the two above is immaterial.
app.include_router(campaign_preview.router)
app.include_router(contacts.router)
app.include_router(contacts.lists_router)
app.include_router(categories.router)
app.include_router(imports.router)
app.include_router(blocklist.router)
app.include_router(usage.router)
# Subscribe, the checkout return and Stripe's webhook. Registered before
# `links.router` like every other page — `/subscribe` is a page, not a slug.
app.include_router(billing.router)
app.include_router(reports.router)
app.include_router(prospects.router)
app.include_router(settings_router.router)
app.include_router(telnyx_webhooks.router)
app.include_router(twilio_webhooks.router)

# LAST, and it has to stay last. `links.router` owns `GET /{slug}` at the root
# of the short domain, which is what makes "a4a.bz/a7k9x2pq" ten characters
# shorter than a subdomain would be — worth a whole segment on a tight message.
# Starlette matches routes in registration order, so anything registered after
# this would be unreachable, and anything registered before it wins as it
# should: /login, /health, /campaigns and the rest are pages, not slugs.
app.include_router(links.router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
