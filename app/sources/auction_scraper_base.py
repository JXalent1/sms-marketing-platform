"""The browser half of reading his registered bidders out of an auction platform.

Ported from the reference scraper's `app/services/base_scraper.py`: browser
lifecycle, retrying navigation, overlay dismissal, debug screenshots and the
paging loop. A platform subclasses `AuctionScraperBase`, sets `platform`, and
implements the seven site-specific hooks at the bottom — selectors, not
machinery. `app/sources/platforms.py` is the registry that names them.

## What this module does not do, and why that is the point of the port

**It never touches the database.** The reference `save_profile()` built a
`Bidder` row and committed it from inside the scrape loop, so the browser code
decided identity (a `profile_hash` of phone + auction date), dedup and
persistence. Two dedup keys on one path is how a duplicate appears: A4A's
identity is `contacts.phone` and its unique index (escalation item 4), and
a second key computed up here would disagree with it on the first bidder who
registered for two sales. So `collect()` only *produces* `BidderRecord`s, and
`app/services/bidder_scrape.py` hands them to `ContactSource.ingest()` — the one
path every source takes into `contacts` — and writes the behaviour beside them.
`tests/test_bidder_source.py` asserts the absence structurally.

## The browser closes on every path

The reference box leaked one Playwright driver per daily scrape — 17 orphans,
1.6 GB RSS on a 3.9 GB box. This one has 2 GB and already runs the app.
`collect()` ends in a `finally` that awaits `shutdown()`, and `run()` enforces
the deadline with `asyncio.wait_for`, which **cancels** the scrape: the
cancellation unwinds through that same `finally`, so a timeout closes the
context and stops the driver exactly as success and failure do. Each teardown
step has its own bound, because a close that hangs under a cancelled task would
hang the deadline that was meant to end it.

`run()` spins its own event loop with `asyncio.run()` and is called from the
scheduler's worker thread, never from the application's loop. A scrape is an
hour of clicks and synchronous text parsing; on the loop that `run_due_campaigns`
shares, it would delay the send path the way 5i's freshness query did.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable, List, Optional
import asyncio
import logging
import os

from app.core.config import settings
from app.sources.base import ContactRecord, ContactSource

logger = logging.getLogger("sources.auction")

# Bumped from 30s in the reference system: auction portals and heavy React
# tables routinely take 30-45s on a cold load, and a 30s default once caused
# four consecutive days of scheduled-job timeouts.
DEFAULT_TIMEOUT_MS = 60000

# How long each teardown step may take before it is abandoned. Bounded because
# a hung `context.close()` inside a cancelled task would otherwise hold the
# deadline open indefinitely — the leak, moved one layer down.
SHUTDOWN_STEP_SECONDS = 15

# Failure screenshots are page renders of his bidders' details: operator-only
# diagnostics, kept under logs/ (not served, not archived by backup.sh).
DEBUG_SCREENSHOT_DIR = os.path.join("logs", "scraper-screenshots")


@dataclass
class BidderRecord(ContactRecord):
    """One registered bidder: a contact record plus the platform's behaviour.

    Typed here rather than carried as the page's strings, so the service stores
    real columns: "average hammer over $250" is a comparison on an integer, not
    a regex over "$1,234.56". `None` always means "the page did not say", never
    zero — a bidder with no analytics block has not won zero items.
    """
    platform: str = ""
    username: Optional[str] = None
    address: Optional[str] = None
    location: Optional[str] = None
    member_since: Optional[date] = None
    card_on_file: Optional[bool] = None
    tax_exempt: Optional[bool] = None
    auctions_attended: Optional[int] = None
    bids_placed: Optional[int] = None
    items_won: Optional[int] = None
    payment_rate_pct: Optional[float] = None
    avg_hammer_cents: Optional[int] = None
    # The portal prints "Less than $100" for small averages. The figure is then
    # a ceiling, not a value, and an audience of "average hammer over $50" must
    # not count it as $100.
    avg_hammer_is_ceiling: Optional[bool] = None
    disputes_open: Optional[int] = None
    disputes_closed: Optional[int] = None


class SelectorDrift(RuntimeError):
    """The page had bidder rows and the selectors extracted nothing from them.

    The platform owns this page and changes it without notice. The reference
    scraper answered a changed panel by skipping every row and reporting a
    *successful* scrape of zero bidders — indistinguishable from a quiet week.
    A run that saw rows and produced nothing is a failed run.
    """


def profile_dir_problem(path: str, project_root: str) -> Optional[str]:
    """Why a browser profile directory is unsafe, or None.

    The profile is a live logged-in session for his auction account. Inside
    the project it is rsynced over (`deploy.sh` runs `--delete` on app/),
    one mount away from being served, and one glob away from a backup. Outside
    it, none of that can happen by accident.
    """
    resolved = os.path.realpath(os.path.expanduser(path))
    root = os.path.realpath(project_root)
    if resolved == root or resolved.startswith(root + os.sep):
        return (f"BROWSER_PROFILE_ROOT resolves inside the project ({resolved}); "
                "it holds a live account session and must live outside it")
    return None


class AuctionScraperBase(ContactSource):
    """Reads registered bidders from one auction platform's partner portal."""

    # A slug from `platforms.py`. A subclass sets `name` to the same value: it
    # is what `contacts.source` records, so `source:liveauctioneers` resolves.
    platform: str = ""
    PLATFORM_LABEL: str = "Auction platform"
    # Playwright's default headless UA advertises "HeadlessChrome", which CDN
    # bot protection scores heavily against. None keeps the default.
    USER_AGENT: Optional[str] = None
    # Refused resource types. Deliberately never stylesheet or script: the hooks
    # use visibility checks on a React page, and blocking CSS/JS changes what
    # "visible" means.
    BLOCK_RESOURCE_TYPES: tuple = ("image", "media", "font")
    RETRY_BACKOFF_SECONDS: float = 15
    SHUTDOWN_STEP_SECONDS: float = SHUTDOWN_STEP_SECONDS
    # Failure screenshots show bidders' personal details. Kept long enough to
    # diagnose a broken morning, not forever.
    SCREENSHOT_KEEP_DAYS: int = 14

    def __init__(self, launcher=None, profile_root: Optional[str] = None):
        # `launcher` is the test seam: a zero-argument callable returning an
        # object with an async `start()`, i.e. `async_playwright`. Imported
        # lazily so the app, the scheduler and the suite run without Playwright.
        self._launcher = launcher
        self._profile_root = profile_root or settings.BROWSER_PROFILE_ROOT
        self.playwright = None
        self.context = None
        self.page = None
        self.records: List[BidderRecord] = []
        self.rows_seen = 0
        # The platform's own total ("1-120 of 425"), when it states one. A run
        # that saw fewer rows than this stopped paging early.
        self.rows_expected: Optional[int] = None
        self._panel_misses = 0
        self.pages_scraped = 0
        self.shutdown_clean: Optional[bool] = None

    def browser_open(self) -> bool:
        """Whether a context or driver is still held. After `collect()` this is
        the leak, and the run row records it rather than assuming the teardown
        ran because nothing said otherwise."""
        return self.context is not None or self.playwright is not None

    def profile_dir(self) -> str:
        # One directory per platform. A shared profile cross-contaminates
        # cookies and logs you out of whichever site ran last.
        return os.path.join(os.path.expanduser(self._profile_root), self.platform)

    # ── ContactSource ───────────────────────────────────────────────────────

    def fetch(self, records: Iterable[BidderRecord] = None, **kwargs):
        """Hand already-collected records to `ingest()`. No browser here.

        `ContactSource.fetch()` is synchronous and the browser is not; the
        browser half is `collect()`. Splitting them keeps `ingest()` — the one
        path from any source into `contacts` — unchanged for this source.
        """
        yield from (records if records is not None else self.records)

    def run(self, timeout_seconds: float, **kwargs) -> List[BidderRecord]:
        """Collect under a hard deadline. Raises `asyncio.TimeoutError` on it.

        Records produced before the deadline stay on `self.records`: a timeout
        is not a rollback, on `scrape_runner`'s precedent.
        """
        return asyncio.run(asyncio.wait_for(self.collect(**kwargs),
                                            timeout=timeout_seconds))

    # ── Browser lifecycle ───────────────────────────────────────────────────

    def _start_playwright(self):
        if self._launcher is not None:
            return self._launcher()
        from playwright.async_api import async_playwright
        return async_playwright()

    async def initialize(self):
        logger.info("[%s] starting browser", self.platform)
        profile = self.profile_dir()
        os.makedirs(profile, mode=0o700, exist_ok=True)
        # Held on self: `.start()` spawns a node driver that exits only on
        # `.stop()`. A local here leaked one driver per scrape on the old box.
        self.playwright = await self._start_playwright().start()
        launch = dict(user_data_dir=profile, headless=settings.SCRAPER_HEADLESS,
                      args=["--no-sandbox", "--disable-setuid-sandbox",
                            # Small /dev/shm on a droplet crashes tabs mid-run.
                            "--disable-dev-shm-usage"])
        if self.USER_AGENT:
            launch["user_agent"] = self.USER_AGENT
        self.context = await self.playwright.chromium.launch_persistent_context(**launch)
        self.page = await self.context.new_page()
        if self.BLOCK_RESOURCE_TYPES:
            blocked = set(self.BLOCK_RESOURCE_TYPES)

            async def _drop(route, request):
                if request.resource_type in blocked:
                    await route.abort()
                else:
                    await route.continue_()

            await self.page.route("**/*", _drop)
        self.page.set_default_timeout(DEFAULT_TIMEOUT_MS)
        self.page.set_default_navigation_timeout(DEFAULT_TIMEOUT_MS)

    async def shutdown(self):
        """Close the context AND stop the driver, each bounded, each attempted.

        `shutdown_clean` is what the run row's `cleanup_ran` records: True only
        if both steps finished. A step that timed out or raised leaves it False,
        which is the state somebody should be able to query for.
        """
        clean = True
        for label, handle, method in (("context", "context", "close"),
                                      ("driver", "playwright", "stop")):
            target = getattr(self, handle)
            if target is None:
                continue
            try:
                await asyncio.wait_for(getattr(target, method)(),
                                       timeout=self.SHUTDOWN_STEP_SECONDS)
            except BaseException as e:               # noqa: BLE001
                # BaseException: under a cancelled task a step can itself be
                # cancelled, and the driver must still get its turn.
                clean = False
                logger.error("[%s] %s teardown failed: %s", self.platform,
                             label, type(e).__name__)
            setattr(self, handle, None)
        self.page = None
        self.shutdown_clean = clean

    # ── Navigation helpers ──────────────────────────────────────────────────

    async def _visible_within(self, locator, timeout_ms: int) -> bool:
        """Whether `locator` becomes visible within the timeout.

        Replaces the reference `is_visible(timeout=…)`, whose timeout the pinned
        SDK documents as ignored: it returned immediately, so every "wait up to
        three seconds for the dropdown" in the old scraper waited for nothing.
        """
        try:
            await locator.wait_for(state="visible", timeout=timeout_ms)
            return True
        except Exception:                             # noqa: BLE001
            return False

    async def _safe_network_idle(self, timeout_ms: int = 15000):
        """Wait for networkidle but never hang: portals long-poll, so it may
        never fire, and content selectors gate progress anyway."""
        try:
            await self.page.wait_for_load_state("networkidle", timeout=timeout_ms)
        except Exception:                             # noqa: BLE001
            logger.debug("networkidle timed out after %sms (non-fatal)", timeout_ms)

    async def _goto_with_retry(self, url: str, attempts: int = 3):
        """goto with backoff. Four days of the reference system's timeouts were
        all on the initial load, so one blip must not kill the job."""
        last_err = None
        for attempt in range(1, attempts + 1):
            try:
                await self.page.goto(url, wait_until="domcontentloaded",
                                     timeout=DEFAULT_TIMEOUT_MS)
                await self._safe_network_idle(20000)
                return
            except Exception as e:                    # noqa: BLE001
                # An auth redirect mid-navigation is the site working normally.
                if "interrupted by another navigation" in str(e):
                    await self._safe_network_idle(20000)
                    return
                last_err = e
                logger.warning("[%s] goto attempt %d/%d failed", self.platform,
                               attempt, attempts)
                if attempt < attempts:
                    await asyncio.sleep(self.RETRY_BACKOFF_SECONDS)
        await self._debug_screenshot("goto-failed")
        raise last_err

    def _prune_screenshots(self) -> None:
        cutoff = datetime.now().timestamp() - self.SCREENSHOT_KEEP_DAYS * 86400
        try:
            for entry in os.scandir(DEBUG_SCREENSHOT_DIR):
                if entry.name.endswith(".png") and entry.stat().st_mtime < cutoff:
                    os.remove(entry.path)
        except OSError:
            pass

    async def _debug_screenshot(self, label: str) -> Optional[str]:
        """Screenshot the page for post-mortem: what did the site actually serve?"""
        try:
            if not self.page:
                return None
            os.makedirs(DEBUG_SCREENSHOT_DIR, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            path = os.path.join(DEBUG_SCREENSHOT_DIR, f"{stamp}-{self.platform}-{label}.png")
            await self.page.screenshot(path=path, full_page=True)
            logger.info("[%s] debug screenshot: %s", self.platform, path)
            return path
        except Exception:                             # noqa: BLE001
            return None

    async def dismiss_overlays(self):
        """Best-effort click through cookie/consent/modal overlays."""
        for sel in ('button:has-text("Accept all")', 'button:has-text("Accept")',
                    'button:has-text("I agree")', 'button:has-text("Got it")',
                    '[aria-label="Close"]', '[class*="cookie"] button',
                    '[class*="consent"] button'):
            try:
                loc = self.page.locator(sel).first
                if await loc.is_visible():
                    await loc.click(timeout=1500)
                    await self.page.wait_for_timeout(300)
            except Exception:                         # noqa: BLE001
                continue

    # ── The scrape ──────────────────────────────────────────────────────────

    async def collect(self, auto_mode: bool = True,
                      auction_date: Optional[str] = None) -> List[BidderRecord]:
        """Log in, select, page through, and return every bidder read.

        `auto_mode` reads "All Upcoming Auctions", which is what the daily job
        does; `auction_date` selects one sale by its dropdown label instead.
        """
        self.records = []
        self.rows_seen = 0
        self.rows_expected = None
        self._panel_misses = 0
        self._prune_screenshots()
        try:
            await self.initialize()
            await self.login()
            await self.dismiss_overlays()
            if auto_mode:
                await self.select_all_upcoming()
            else:
                await self.select_auction(auction_date)
            await self.go_to_registered_tab()
            total_pages = await self.get_total_pages()

            page_num = 1
            while True:
                found = await self.scrape_page(page_num)
                self.pages_scraped = page_num
                # Keep going past the detected total until a page is empty: an
                # under-counted total lost 240 bidders at a time on the old box.
                if not found and page_num > total_pages:
                    break
                self.records.extend(found)
                if not await self.go_to_next_page(page_num):
                    if page_num < total_pages:
                        logger.warning("[%s] navigation failed at page %d/%d",
                                       self.platform, page_num, total_pages)
                    break
                page_num += 1

            if self.rows_seen and not self.records:
                await self._debug_screenshot("selector-drift")
                raise SelectorDrift(
                    f"{self.rows_seen} bidder rows were on the page and none could "
                    "be read — the platform's page has probably changed")
            # Records with no phone at all are the same failure one field over:
            # a changed phone line reads every bidder as phoneless, and that was
            # a "completed" run creating nobody. Failing a genuinely phoneless
            # list costs nothing — none of it could become a contact anyway.
            if not any((r.phone or "").strip() for r in self.records):
                await self._debug_screenshot("selector-drift-phone")
                raise SelectorDrift(
                    f"{len(self.records)} bidders were read and not one had a phone "
                    "number — the platform's phone field has probably changed")
            return self.records
        except Exception:
            await self._debug_screenshot("scrape-fail")
            raise
        finally:
            # Every way out, including cancellation by `run()`'s deadline.
            await self.shutdown()

    # ── Site-specific hooks — every platform implements these ───────────────

    async def login(self):
        raise NotImplementedError

    async def select_auction(self, auction_date: str):
        raise NotImplementedError

    async def select_all_upcoming(self):
        raise NotImplementedError

    async def go_to_registered_tab(self):
        raise NotImplementedError

    async def get_total_pages(self) -> int:
        raise NotImplementedError

    async def scrape_page(self, page_num: int) -> List[BidderRecord]:
        """Every bidder on the current page. Must add to `self.rows_seen`."""
        raise NotImplementedError

    async def go_to_next_page(self, current_page: int) -> bool:
        raise NotImplementedError
