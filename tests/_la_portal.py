"""A fake Playwright that serves the LiveAuctioneers bidders page from fixtures.

**Nothing here makes a network call, and nothing here needs Playwright
installed.** `LiveAuctioneersSource(launcher=portal.launcher)` gets this object
wherever it would have got `async_playwright()`, and every page is built from
`tests/fixtures/liveauctioneers/bidders.json`.

It is a state machine for the one page the source drives, shaped to the
selectors the source uses — which means it proves the port's *logic* (paging,
waiting, parsing, teardown, persistence), not that LiveAuctioneers still serves
those selectors. Nobody has recorded the live page; the first live run is
Jordan's (`sessions/session-L1.md` Part B). That is said here so a green suite
is never read as "the selectors are right".

Knobs, each for one criterion or one defect:

  - `hang_on` — "goto": navigation never returns, for the deadline test;
    "next": the click to page 2 never returns, after page 1 was read;
    "nopager": the page-2 control is gone, so paging stops after page 1.
  - `block` — a threading.Event `goto` waits on, for the concurrency test.
  - `login_fails` — the login form never reaches the bidders page.
  - `panel_lag` — body reads that still show the *previous* bidder's panel
    after a click. The reference wait read those as the new bidder.
  - `markers` — False renames the panel's labels, i.e. the platform changed it.
  - `broken_teardown` — `close()` and `stop()` raise; `hang_close` — `close()`
    never returns, for the per-step bound on the teardown.
  - `phones_hidden` — the platform stops printing the number in the panel.
"""

import asyncio
import json
import os
import threading
from typing import List, Optional

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "liveauctioneers",
                       "bidders.json")
PAGE_SIZE = 120
# A ten-digit figure in every table row. The reference scraper's whole-body
# phone regex read the first one of these as every bidder's phone.
DISTRACTOR = "Paddle ref 3055550199"


def load_bidders() -> List[dict]:
    with open(FIXTURE) as fh:
        return json.load(fh)["bidders"]


def panel_lines(b: dict, markers: bool = True) -> List[str]:
    label = (lambda s: s) if markers else (lambda s: s.upper().replace(" ", "_"))
    lines = [b["name"]]
    for key in ("username", "phone", "address", "location"):
        if b.get(key):
            lines.append(b[key])
    for key, text in (("member_since", "Member Since"), ("card_on_file", "Card on File"),
                      ("auctions_attended", "Auctions Attended"),
                      ("tax_exemption", "Tax Exemption")):
        if key in b:
            lines += [label(text), str(b[key])]
    lines += ["Registrations", "Bids Placed: 0"]
    if "analytics" in b:
        lines.append(label("Bidder Analytics"))
        for key, text in (("bids_placed", "Bids Placed"), ("items_won", "Items Won"),
                          ("payment_rate", "Payment Rate"),
                          ("avg_hammer", "Avg Hammer Price"),
                          ("dispute_history", "Dispute History")):
            if key in b["analytics"]:
                lines += [text, str(b["analytics"][key])]
    return lines


class _Element:
    """A row, or a cell of one. A cell click opens *this row's* bidder, so two
    bidders with one name are two panels — as they are on the real page."""

    def __init__(self, page, bidder=None, text=""):
        self._page, self._bidder, self._text = page, bidder, text
        self._name = bidder["name"] if bidder else None

    async def query_selector_all(self, selector):
        cells = ["★", "☐", DISTRACTOR, self._name, "Fort Lauderdale"]
        return [_Element(self._page, self._bidder, c) for c in cells]

    async def inner_text(self):
        return self._text

    async def text_content(self):
        return self._text

    async def click(self, **kw):
        self._page._open(self._bidder)


class _Locator:
    def __init__(self, page, selector, has_text=None):
        self._page, self._sel, self._has_text = page, selector, has_text

    @property
    def first(self):
        return self

    def filter(self, has_text=None, **kw):
        return _Locator(self._page, self._sel, has_text)

    def _matches(self) -> List[str]:
        p = self._page
        if "option" in self._sel:
            opts = p.options if p.dropdown_open else []
            return [o for o in opts if self._has_text is None or self._has_text in o]
        if "singleValue" in self._sel:
            return [p.selected] if p.logged_in else []
        if "control" in self._sel or "indicatorContainer" in self._sel:
            return ["control"] if p.logged_in else []
        return []

    async def count(self):
        return len(self._matches())

    async def is_visible(self, **kw):
        return bool(self._matches())

    async def wait_for(self, state="visible", timeout=None):
        if not self._matches():
            raise TimeoutError(f"{self._sel} not visible")

    async def inner_text(self, **kw):
        found = self._matches()
        if not found:
            raise TimeoutError(self._sel)
        return found[0]

    async def click(self, **kw):
        p = self._page
        if "option" in self._sel:
            p.selected = self._matches()[0]
            p.dropdown_open = False
        elif self._matches():
            p.dropdown_open = True
        else:
            raise TimeoutError(self._sel)

    async def fill(self, value):
        self._page.filled[self._sel] = value


class _ByText:
    def __init__(self, page, text, kind):
        self._page, self._text, self._kind = page, text, kind

    @property
    def first(self):
        return self

    async def click(self, **kw):
        p = self._page
        if self._kind == "text":
            if self._text == "Registered":
                p.on_registered = True
            else:
                p._open(next(b for b in p.rows() if b["name"] == self._text))
        elif self._kind == "label" and self._text.startswith("Page "):
            if p.portal.hang_on == "next":
                await asyncio.sleep(3600)
            if p.portal.hang_on == "nopager":
                raise TimeoutError("the pager changed")
            n = int(self._text.split()[1])
            if n > p.page_count():
                raise TimeoutError(self._text)
            p.page_no = n
            p.open_name = None
        elif self._kind == "testid":
            if not p.portal.login_fails:
                p.logged_in = True

    async def fill(self, value):
        self._page.filled[self._text] = value


class FakePage:
    def __init__(self, portal):
        self.portal = portal
        self.bidders = portal.bidders
        self.options = ["All Upcoming Auctions", "Oct 01, 2026 - Marine Sale"]
        self.logged_in = portal.logged_in
        self.selected = "Oct 01, 2026 - Marine Sale"
        self.dropdown_open = False
        self.on_registered = False
        self.page_no = 1
        self.open_name: Optional[str] = None
        self.prev_name: Optional[str] = None
        self.lag_left = 0
        self.filled = {}
        self.url = "about:blank"

    # ── state ──────────────────────────────────────────────────────────────
    def page_count(self) -> int:
        return max(1, (len(self.bidders) + PAGE_SIZE - 1) // PAGE_SIZE)

    def rows(self) -> List[dict]:
        if not (self.logged_in and self.on_registered):
            return []
        start = (self.page_no - 1) * PAGE_SIZE
        return self.bidders[start:start + PAGE_SIZE]

    def _open(self, bidder):
        self.prev_name, self.open_name = self.open_name, bidder
        self.lag_left = self.portal.panel_lag

    def _body(self) -> str:
        if not self.logged_in:
            return "Sign in\nUsername\nPassword"
        lines = ["Bidders", self.selected, "Approved", "Registered"]
        for b in self.rows():
            lines += ["★", "☐", DISTRACTOR, b["name"], "Fort Lauderdale"]
        rows = self.rows()
        if rows:
            start = (self.page_no - 1) * PAGE_SIZE + 1
            lines.append(f"{start}-{start + len(rows) - 1} of {len(self.bidders)}")
        showing = self.open_name
        if self.lag_left > 0:
            self.lag_left -= 1
            showing = self.prev_name
        if showing:
            lines += panel_lines(showing, self.portal.markers)
        return "\n".join(lines)

    # ── the Playwright surface the source uses ─────────────────────────────
    async def route(self, pattern, handler):
        self.portal.routed = True

    def set_default_timeout(self, ms):
        pass

    def set_default_navigation_timeout(self, ms):
        pass

    async def goto(self, url, **kw):
        self.portal.gotos.append(url)
        if self.portal.block is not None:
            await asyncio.get_running_loop().run_in_executor(
                None, self.portal.block.wait, 30)
        if self.portal.hang_on == "goto":
            await asyncio.sleep(3600)
        self.url = url

    async def wait_for_load_state(self, *a, **kw):
        pass

    async def wait_for_selector(self, selector, timeout=None):
        if "react-select" in selector and self.dropdown_open:
            return
        if selector == "tbody tr" and self.rows():
            return
        if "singleValue" in selector and self.logged_in:
            return
        raise TimeoutError(selector)

    async def wait_for_url(self, pattern, timeout=None):
        if not self.logged_in:
            raise TimeoutError("still on the login form")

    def get_by_label(self, text):
        return _ByText(self, text, "label")

    def get_by_test_id(self, text):
        return _ByText(self, text, "testid")

    def get_by_text(self, text, exact=False):
        return _ByText(self, text, "text")

    def locator(self, selector):
        return _Locator(self, selector)

    async def click(self, selector, **kw):
        raise TimeoutError(selector)            # the "›" fallback: never present

    async def evaluate(self, script):
        return None

    async def inner_text(self, selector):
        return self._body()

    async def query_selector_all(self, selector):
        if selector == "tbody tr":
            return [_Element(self, b) for b in self.rows()]
        if selector == "button":
            return [_Element(self, None, str(n)) for n in range(1, self.page_count() + 1)]
        return []

    async def wait_for_timeout(self, ms):
        await asyncio.sleep(0)

    async def screenshot(self, **kw):
        self.portal.screenshots += 1


class FakeContext:
    def __init__(self, portal):
        self.portal = portal

    async def new_page(self):
        self.portal.page = FakePage(self.portal)
        return self.portal.page

    async def close(self):
        self.portal.closes += 1
        if self.portal.hang_close:
            await asyncio.sleep(3600)
        if self.portal.broken_teardown:
            raise RuntimeError("close failed")
        self.portal.context_open = False


class FakePlaywright:
    def __init__(self, portal):
        self.portal = portal
        self.chromium = self

    async def launch_persistent_context(self, **kwargs):
        self.portal.launch_kwargs = kwargs
        self.portal.context_open = True
        return FakeContext(self.portal)

    async def stop(self):
        self.portal.stops += 1
        if self.portal.broken_teardown:
            raise RuntimeError("stop failed")
        self.portal.driver_running = False

    async def start(self):
        self.portal.driver_running = True
        return self


class Portal:
    """One fake LiveAuctioneers session. Inspect it after a run."""

    def __init__(self, bidders=None, *, logged_in=True, hang_on=None, block=None,
                 login_fails=False, panel_lag=0, markers=True, broken_teardown=False,
                 hang_close=False, phones_hidden=False):
        self.bidders = load_bidders() if bidders is None else bidders
        self.logged_in, self.hang_on, self.block = logged_in, hang_on, block
        self.login_fails, self.panel_lag, self.markers = login_fails, panel_lag, markers
        self.broken_teardown, self.hang_close = broken_teardown, hang_close
        if phones_hidden:
            self.bidders = [dict(b, phone="Phone: tap to reveal") for b in self.bidders]
        self.gotos, self.closes, self.stops, self.screenshots = [], 0, 0, 0
        self.context_open = self.driver_running = False
        self.launch_kwargs, self.page, self.routed = None, None, False

    def launcher(self):
        return FakePlaywright(self)

    @property
    def browser_closed(self) -> bool:
        return self.closes >= 1 and self.stops >= 1 and not self.driver_running


def make_source(portal: Portal, profile_root: str, **kw):
    """A source wired to the fake, with every real-time pause set to zero."""
    from app.sources.liveauctioneers import LiveAuctioneersSource
    source = LiveAuctioneersSource(launcher=portal.launcher, profile_root=profile_root,
                                   username="fixture-user", password="fixture-pass",
                                   house_id="0000", **kw)
    source.RETRY_BACKOFF_SECONDS = 0
    source.ROW_PAUSE_SECONDS = 0
    source.PANEL_POLL_MS = 1
    source.PANEL_WAIT_SECONDS = 0.05
    return source


def blocking_portal():
    event = threading.Event()
    return Portal(block=event), event
