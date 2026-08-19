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
from app.core.database import engine, Base
from app.sms.factory import get_provider
import app.models                                    # noqa: F401 — registers tables

from app.routers import pages, campaigns, contacts, blocklist, usage, settings as settings_router
from app.routers.webhooks import telnyx as telnyx_webhooks, twilio as twilio_webhooks

logger = logging.getLogger("app")

for directory in ("data", "logs", "exports"):
    os.makedirs(directory, exist_ok=True)

Base.metadata.create_all(bind=engine)

scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ────────────────────────────────────────────────────────────
    provider = get_provider()
    logger.info(f"{settings.BRAND_APP_NAME} starting | env={settings.ENVIRONMENT} "
                f"| provider={provider.name}")
    if provider.name == "console":
        logger.warning("SMS provider is 'console' — messages are logged, NOT sent.")

    # Register scheduled jobs here. Example — a nightly contact sync:
    #
    #   from apscheduler.triggers.cron import CronTrigger
    #   import pytz
    #   scheduler.add_job(
    #       sync_contacts_job,
    #       CronTrigger(hour=9, minute=0, timezone=pytz.timezone("US/Eastern")),
    #       id="daily_sync", replace_existing=True,
    #   )
    #
    # Give every job an explicit id and replace_existing=True, or a reload
    # quietly stacks duplicates that all fire at once.
    scheduler.start()

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
app.include_router(campaigns.router)
app.include_router(contacts.router)
app.include_router(contacts.lists_router)
app.include_router(blocklist.router)
app.include_router(usage.router)
app.include_router(settings_router.router)
app.include_router(telnyx_webhooks.router)
app.include_router(twilio_webhooks.router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
