"""LiveAuctioneers: his registered bidders, read from his own partner portal.

Ported from the reference scraper's `app/services/scraper.py`. The selectors and
the navigation are theirs and were earned in production — the partial-class
react-select matches, "keep paging until empty", verifying a page actually
changed. What changed in the port:

  - **Nothing here touches the database.** The page is read into
    `BidderRecord`s; `app/services/bidder_scrape.py` does the rest.
  - **Parsing is pure.** `parse_profile()` takes the page's text and returns
    typed values, so every field is tested against fixtures without a browser.
  - **The panel is read only once it has changed.** The reference wait was
    "any profile marker is on the page", and after the first bidder the
    previous bidder's panel satisfies that instantly — so a slow panel was read
    stale and bidder 2 got bidder 1's phone. On A4A's phone-keyed dedup that
    would merge two people into one contact. `_wait_for_panel()` now also
    requires the page text to differ from what it was before the click.
  - **Phone, username, address and location are read from the panel's lines
    only** — the lines the click added. The reference regex ran over the whole
    body, so a ten-digit figure anywhere in the table became every bidder's
    phone. Analytics stay label-anchored over the whole text, because an
    unchanged line ("Card on File / Yes") is still the new bidder's answer.
  - `member_since` is read beside its label, not as the first date on the
    page, which with a sale selected is the sale's own date.
"""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import List, Optional
import asyncio
import logging
import re

from app.core.config import settings
from app.sources.auction_scraper_base import (AuctionScraperBase, BidderRecord,
                                              SelectorDrift)
from app.sources.platforms import LIVEAUCTIONEERS

logger = logging.getLogger("sources.liveauctioneers")

PORTAL_URL = "https://partners.liveauctioneers.com/house/{house_id}/bidders"
OPTION = '[id^="react-select-"][id*="-option-"]'
ALL_UPCOMING = "All Upcoming Auctions"
PROFILE_MARKERS = ("Member Since", "Card on File", "Bidder Analytics",
                   "Auctions Attended")
# The portal's table size. The "X-Y of Z" total is divided by it for progress;
# the paging loop does not trust the result (see `collect()`).
PAGE_SIZE = 120
# Clicks that may legitimately find nothing — the next-page button on the last
# page — get a short timeout. The default is DEFAULT_TIMEOUT_MS, and three
# attempts at two selectors on the last page was six minutes of a 2 GB box
# holding a browser open to learn that the list had ended.
CLICK_MS = 5000
# Consecutive panel misses, before anything was read, that mean the page changed.
EARLY_DRIFT_ROWS = 5

_PHONE = re.compile(r"\+?1?[-.\s]?\(?(\d{3})\)?[-.\s]?(\d{3})[-.\s]?(\d{4})")
_USERNAME = re.compile(r"^[a-z][a-z0-9_]*$|^[A-Z][A-Z0-9]+$|^[a-z]+\d+$")
_ADDRESS = re.compile(r"^\d+\s+[A-Za-z\s]+(Street|St|Avenue|Ave|Road|Rd|Drive|Dr|"
                      r"Lane|Ln|Court|Ct|Way|Boulevard|Blvd|Place|Pl)$", re.I)
_LOCATION = re.compile(r"^[A-Za-z\s]+,\s*[A-Z]{2}\s+\d{5}(-\d{4})?$")
_DATE = re.compile(r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+"
                   r"(\d{1,2}),\s+(\d{4})")
_MONEY = re.compile(r"\$\s*([\d,]+(?:\.\d{1,2})?)(?![\d.]*\s*[KkMm]\b)")
_PERCENT = re.compile(r"^(\d{1,3}(?:\.\d+)?)\s*%$")
_DISPUTES = re.compile(r"(\d+)\s*open\D+(\d+)\s*closed", re.I)


# ── Pure parsing ────────────────────────────────────────────────────────────

def _lines(text: str) -> List[str]:
    return [line.strip() for line in (text or "").split("\n") if line.strip()]


def _count(value: str) -> Optional[int]:
    value = value.replace(",", "")
    return int(value) if value.isdigit() else None


def _after(lines: List[str], label: str, exact: bool = True, span: int = 1,
           start: int = 0, stop: Optional[int] = None) -> List[str]:
    """The `span` lines following the first line that is (or contains) `label`."""
    for i in range(start, len(lines) if stop is None else min(stop, len(lines))):
        if (lines[i] == label) if exact else (label in lines[i]):
            return lines[i + 1:i + 1 + span]
    return []


def _yes_no(lines: List[str], label: str) -> Optional[bool]:
    for value in _after(lines, label, exact=False, span=2):
        if value in ("Yes", "No"):
            return value == "Yes"
    return None


def parse_money_cents(text: str) -> tuple:
    """"$1,234.56" -> (123456, False); "Less than $100" -> (10000, True).

    Anything else — "$1.2K", "N/A" — is (None, None): an average the page did not
    state plainly is not stored as a guess. Decimal, not float, on
    `CLAUDE.md`'s rule for money.
    """
    match = _MONEY.search(text or "")
    if not match:
        return None, None
    try:
        cents = int(Decimal(match.group(1).replace(",", "")) * 100)
    except InvalidOperation:
        return None, None
    return cents, "less" in text.lower()


def parse_member_since(lines: List[str]) -> Optional[date]:
    for i, line in enumerate(lines):
        if "Member Since" in line:
            for candidate in [line] + lines[i + 1:i + 2]:
                match = _DATE.search(candidate)
                if match:
                    try:
                        return datetime.strptime(
                            f"{match.group(1)} {match.group(2)} {match.group(3)}",
                            "%b %d %Y").date()
                    except ValueError:
                        return None
            return None
    return None


def parse_profile(text: str, bidder_name: str, panel_lines: List[str] = None) -> dict:
    """Every field the profile panel shows, typed. Missing means None.

    `text` is the whole page after the panel opened; `panel_lines` the lines
    the click added. Identity-shaped fields come only from `panel_lines`, so a
    figure in the table can never be read as this bidder's — see the module
    docstring for the stale-panel defect this replaced.
    """
    lines = _lines(text)
    panel = panel_lines if panel_lines is not None else lines
    out = {"full_name": bidder_name}

    for line in panel:
        match = _PHONE.search(line)
        if match:
            out["phone"] = f"({match.group(1)}) {match.group(2)}-{match.group(3)}"
            break

    # The name itself is not a panel line — it was already in the table before
    # the click — so the username is the username-shaped line that follows *an*
    # occurrence of the name and is itself new.
    fresh = set(panel)
    for i in (i for i, line in enumerate(lines) if line == bidder_name):
        hit = next((l for l in lines[i + 1:i + 4] if l in fresh
                    and _USERNAME.match(l) and len(l) >= 5), None)
        if hit:
            out["username"] = hit
            break
    # Address and location prefer the panel's new lines and fall back to the
    # whole page. Unlike the phone they are not identity, and two consecutive
    # bidders from one city would otherwise leave the second with no location,
    # because "Fort Lauderdale, FL 33301" was already on screen before the click.
    # The phone has no fallback on purpose: a shared number read as nobody's is
    # a bidder counted as `no_phone`; read as the table's, it is a merged person.
    for field, pattern in (("address", _ADDRESS), ("location", _LOCATION)):
        out[field] = (next((l for l in panel if pattern.search(l)), None)
                      or next((l for l in lines if pattern.search(l)), None))

    out["member_since"] = parse_member_since(lines)
    out["card_on_file"] = _yes_no(lines, "Card on File")
    out["tax_exempt"] = _yes_no(lines, "Tax Exemption")
    attended = _after(lines, "Auctions Attended", exact=False)
    out["auctions_attended"] = _count(attended[0]) if attended else None

    # "Bids Placed" also appears in the registrations block ("Bids Placed: 0"),
    # so it is read only inside the Bidder Analytics section, as the reference
    # scraper learned.
    analytics = next((i for i, l in enumerate(lines) if "Bidder Analytics" in l), None)
    bids = (_after(lines, "Bids Placed", start=analytics, stop=analytics + 20)
            if analytics is not None else [])
    out["bids_placed"] = _count(bids[0]) if bids else None
    won = _after(lines, "Items Won")
    out["items_won"] = _count(won[0]) if won else None

    rate = _after(lines, "Payment Rate")
    rate_match = _PERCENT.match(rate[0]) if rate else None
    out["payment_rate_pct"] = float(rate_match.group(1)) if rate_match else None

    hammer = _after(lines, "Avg Hammer Price", exact=False)
    out["avg_hammer_cents"], out["avg_hammer_is_ceiling"] = (
        parse_money_cents(hammer[0]) if hammer else (None, None))

    disputes = _after(lines, "Dispute History", exact=False)
    match = _DISPUTES.search(disputes[0]) if disputes else None
    out["disputes_open"], out["disputes_closed"] = (
        (int(match.group(1)), int(match.group(2))) if match else (None, None))
    return out


def to_record(profile: dict) -> BidderRecord:
    return BidderRecord(phone=profile.get("phone") or "",
                        full_name=profile.get("full_name"),
                        platform=LIVEAUCTIONEERS,
                        **{k: v for k, v in profile.items()
                           if k not in ("phone", "full_name")})


# ── The browser ─────────────────────────────────────────────────────────────

class LiveAuctioneersSource(AuctionScraperBase):
    name = LIVEAUCTIONEERS
    platform = LIVEAUCTIONEERS
    PLATFORM_LABEL = "LiveAuctioneers"
    description = "Registered bidders from the LiveAuctioneers partner portal"
    # How long a clicked bidder's panel may take. The reference system waited
    # 45s and PHASE-1 cut it to 15: "the profile panel always appears".
    PANEL_WAIT_SECONDS = 15
    PANEL_POLL_MS = 400
    # A breath between bidders, as the reference scraper took: a click storm
    # on his own account's portal is the thing most likely to get it flagged.
    ROW_PAUSE_SECONDS = 0.2

    def __init__(self, launcher=None, profile_root: Optional[str] = None,
                 username: str = None, password: str = None, house_id: str = None):
        super().__init__(launcher=launcher, profile_root=profile_root)
        self.username = username if username is not None else settings.LA_USERNAME
        self.password = password if password is not None else settings.LA_PASSWORD
        self.house_id = house_id if house_id is not None else settings.LA_HOUSE_ID

    def portal_url(self) -> str:
        return PORTAL_URL.format(house_id=self.house_id)

    async def login(self):
        """Log in unless the stored profile already is.

        "Already logged in" is any fragment of the bidders page, not a username
        substring — the reference version broke the day LA_USERNAME changed.
        """
        if not (self.username and self.password and self.house_id):
            raise RuntimeError("LiveAuctioneers credentials are not configured")
        await self._goto_with_retry(self.portal_url())
        try:
            # Not `[class*="control"]`, which the reference also accepted: that
            # matches a login form's own inputs, so a logged-out session would
            # skip the login and fail later as a dropdown error — the wrong cause.
            await self.page.wait_for_selector(
                'tbody tr, [class*="singleValue"]', timeout=5000)
            return
        except Exception:                             # noqa: BLE001
            pass
        try:
            await self.page.get_by_label("Username").fill(self.username)
            await self.page.get_by_label("Password").fill(self.password)
            await self.page.get_by_test_id("button").click()
            await self.page.wait_for_url("**/house/**", timeout=15000)
        except Exception as e:                        # noqa: BLE001
            await self._debug_screenshot("login-failed")
            # The exception's type only. Its text can carry the page URL or a
            # form value, and this lands in a job row.
            raise RuntimeError(f"Login failed ({type(e).__name__})") from None

    async def navigate_to_bidders(self):
        await self._goto_with_retry(self.portal_url())
        await self.dismiss_overlays()

    async def _open_dropdown(self):
        """Open the auction dropdown, trying selectors from most to least stable.

        Partial-class matches, because LA's styled-components hashes change on
        every deploy.
        """
        for selector in ('[class*="control"] [class*="indicatorContainer"]',
                         '[class*="indicatorContainer"]', '[class*="control"]',
                         'input[role="combobox"]'):
            try:
                loc = self.page.locator(selector).first
                if await self._visible_within(loc, 3000):
                    await loc.click()
                    await self.page.wait_for_selector(OPTION, timeout=5000)
                    return
            except Exception:                         # noqa: BLE001
                continue
        await self._debug_screenshot("dropdown-failed")
        raise RuntimeError("Could not open the auction dropdown with any selector")

    async def _pick(self, text: str):
        option = self.page.locator(OPTION).filter(has_text=text)
        if await option.count() == 0:
            # Covers a change to the option id format.
            option = self.page.locator('[class*="option"]').filter(has_text=text)
        if await option.count() == 0:
            await self._debug_screenshot("option-missing")
            raise RuntimeError(f"No dropdown option matched {text!r}")
        await option.first.click()
        await self._safe_network_idle()
        await self.page.wait_for_timeout(1000)

    async def select_auction(self, auction_date: str):
        await self.navigate_to_bidders()
        await self._open_dropdown()
        await self._pick(auction_date)

    async def select_all_upcoming(self):
        """Select "All Upcoming Auctions" — the option node, never the text.

        `get_by_text(..., exact=True)` resolved to three nodes on LA's UI and
        failed the scheduled job on 20 April.
        """
        await self.navigate_to_bidders()
        try:
            current = self.page.locator('[class*="singleValue"]').first
            if (await current.inner_text(timeout=2000)).strip() == ALL_UPCOMING:
                return
        except Exception:                             # noqa: BLE001
            pass
        await self._open_dropdown()
        await self._pick(ALL_UPCOMING)

    async def go_to_registered_tab(self):
        await self.page.get_by_text("Registered").first.click()
        await self.page.wait_for_selector("tbody tr", timeout=10000)
        await self._safe_network_idle()
        await self.page.wait_for_timeout(1000)

    async def get_total_pages(self) -> int:
        """Pages from the "X-Y of Z" footer, else the highest page button, else 1.

        Under-counting is recoverable — the loop keeps going until a page is
        empty — so this is progress, not a limit.
        """
        await self._safe_network_idle()
        await self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        for _ in range(5):
            await self.page.wait_for_timeout(1000)
            try:
                match = re.search(r"\d+-\d+\s+of\s+(\d+)",
                                  await self.page.inner_text("body"))
            except Exception:                         # noqa: BLE001
                continue
            if match:
                self.rows_expected = int(match.group(1))
                return max(1, (self.rows_expected + PAGE_SIZE - 1) // PAGE_SIZE)
        numbers = []
        for button in await self.page.query_selector_all("button"):
            try:
                text = (await button.inner_text()).strip()
            except Exception:                         # noqa: BLE001
                continue
            if text.isdigit() and 1 <= int(text) <= 50:
                numbers.append(int(text))
        return max(numbers) if numbers else 1

    async def _body_text(self) -> str:
        try:
            return await self.page.inner_text("body")
        except Exception:                             # noqa: BLE001
            return ""

    async def _wait_for_panel(self, before: str) -> Optional[str]:
        """The page text once the clicked bidder's panel has rendered, or None.

        Two conditions: a profile marker is present, **and** the text differs
        from the text before the click. The second is the fix — see the module
        docstring. A click that changed nothing is a row skipped, not a row
        read from the previous bidder.
        """
        for _ in range(int(self.PANEL_WAIT_SECONDS * 1000 / self.PANEL_POLL_MS)):
            await self.page.wait_for_timeout(self.PANEL_POLL_MS)
            text = await self._body_text()
            if text != before and any(m in text for m in PROFILE_MARKERS):
                return text
        return None

    async def _row_name(self, row) -> Optional[str]:
        cells = await row.query_selector_all("td")
        if len(cells) < 4:          # star / checkbox columns precede the name
            return None
        name = (await cells[3].inner_text() or "").strip()
        if not name:
            name = (await cells[3].text_content() or "").strip()
        return name or None

    async def scrape_page(self, page_num: int) -> List[BidderRecord]:
        await self.page.evaluate("window.scrollTo(0, 0)")
        await self.page.wait_for_timeout(500)
        rows = await self.page.query_selector_all("tbody tr")
        self.rows_seen += len(rows)
        found = []
        for i, row in enumerate(rows, 1):
            try:
                name = await self._row_name(row)
                if not name:
                    continue
                before = await self._body_text()
                # This row's own name cell first. The reference clicked
                # `get_by_text(name).first`, which is the first row *with that
                # name* — two bidders called John Smith read as one, the second
                # counted as a harmless repeat and never landed.
                try:
                    await (await row.query_selector_all("td"))[3].click(timeout=CLICK_MS)
                except Exception:                     # noqa: BLE001
                    await self.page.get_by_text(name, exact=True).first.click(timeout=CLICK_MS)
                text = await self._wait_for_panel(before)
                if text is None:
                    logger.warning("[%s] page %d row %d: panel did not load",
                                   self.platform, page_num, i)
                    self._panel_misses += 1
                    # A changed panel fails every row, and at real timings each
                    # miss costs PANEL_WAIT_SECONDS: ~425 rows would hold the
                    # browser for the whole deadline and report `timed_out`, not
                    # the cause. Nothing read yet and the first rows all missing
                    # is the page having changed; say so now.
                    if not self.records and not found and \
                            self._panel_misses >= EARLY_DRIFT_ROWS:
                        raise SelectorDrift(
                            f"the first {EARLY_DRIFT_ROWS} bidders' panels never "
                            "showed a profile — the platform's page has probably changed")
                    continue
                seen = set(_lines(before))
                panel = [l for l in _lines(text) if l not in seen]
                found.append(to_record(parse_profile(text, name, panel)))
                await asyncio.sleep(self.ROW_PAUSE_SECONDS)
            except SelectorDrift:
                raise
            except Exception as e:                    # noqa: BLE001
                logger.warning("[%s] page %d row %d failed: %s", self.platform,
                               page_num, i, type(e).__name__)
        logger.info("[%s] page %d: %d rows, %d read", self.platform, page_num,
                    len(rows), len(found))
        return found

    async def _first_names(self, count: int = 5) -> List[str]:
        names = []
        try:
            for row in (await self.page.query_selector_all("tbody tr"))[:count]:
                name = await self._row_name(row)
                if name:
                    names.append(name)
        except Exception:                             # noqa: BLE001
            pass
        return names

    async def go_to_next_page(self, current_page: int) -> bool:
        """Advance and verify the rows actually changed; three attempts.

        Without the check, a click that silently failed re-read the same page,
        every bidder hit dedup, and the next page's bidders were never seen.
        """
        target = current_page + 1
        old = await self._first_names()
        for _ in range(3):
            try:
                await self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await self.page.wait_for_timeout(500)
                try:
                    await self.page.get_by_label(f"Page {target}").click(timeout=CLICK_MS)
                except Exception:                     # noqa: BLE001
                    await self.page.click('button:has-text("›")', timeout=CLICK_MS)
                await self._safe_network_idle()
                await self.page.wait_for_timeout(1000)
                await self.page.evaluate("window.scrollTo(0, 0)")
                await self.page.wait_for_timeout(500)
                new = await self._first_names()
                if new and new != old:
                    return True
            except Exception:                         # noqa: BLE001
                pass
            await self.page.wait_for_timeout(1000)
        return False
