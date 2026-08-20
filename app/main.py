"""Application entry point.

Deliberately thin — it wires modules together and holds no business logic, so
adapting this to a new client means editing modules, not untangling this file.
"""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from contextlib import asynccontextmanager
import os
import logging

from app.core.logging_config import setup_logging
setup_logging()

from app.core.config import settings
from app.core.database import engine
from app.sms.factory import get_provider
import app.models                                    # noqa: F401 — registers tables

from app.routers import (pages, dashboard, campaigns, contacts, categories, imports,
                         blocklist, usage, settings as settings_router)
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

scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ────────────────────────────────────────────────────────────
    provider = get_provider()
    logger.info(f"{settings.BRAND_APP_NAME} starting | env={settings.ENVIRONMENT} "
                f"| provider={provider.name}")
    if provider.name == "console":
        logger.warning("SMS provider is 'console' — messages are logged, NOT sent.")

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
    from apscheduler.triggers.interval import IntervalTrigger
    from app.services.campaign_service import run_due_campaigns

    scheduler.add_job(
        run_due_campaigns,
        IntervalTrigger(minutes=1),
        id="scheduled_campaigns", replace_existing=True, max_instances=1,
    )
    scheduler.start()
    logger.info("Scheduler started | scheduled campaigns checked every minute")

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

app.include_router(pages.router)
app.include_router(dashboard.router)
app.include_router(dashboard.api_router)
app.include_router(campaigns.router)
app.include_router(contacts.router)
app.include_router(contacts.lists_router)
app.include_router(categories.router)
app.include_router(imports.router)
app.include_router(blocklist.router)
app.include_router(usage.router)
app.include_router(settings_router.router)
app.include_router(telnyx_webhooks.router)
app.include_router(twilio_webhooks.router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
