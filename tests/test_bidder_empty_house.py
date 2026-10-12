"""A house with no upcoming auction is a run of zero, not a broken page.

Production, 2026-10-11: logged in, Bidders page, the auction control already
reading "All Upcoming Auctions", an empty menu, every tab at (0) — and the
daily read failing every morning with "No dropdown option matched" (once
"Could not open the auction dropdown"). Even past the dropdown it would have
failed: the table wait times out on no rows, and the all-phoneless drift
check reads an empty list as a changed phone field.

Each test is arranged so that one condition decides it alone. Read with
`agent/mutate-L1.py` `E1`-`E6`, which revert them one at a time.
"""

import pytest

from app.sources.auction_scraper_base import SelectorDrift
from tests._bidder_setup import db, fixture_contacts, scrape  # noqa: F401
from tests._la_portal import Portal, load_bidders, load_page_state

EMPTY = load_page_state("empty_upcoming")
BIDDERS = load_bidders()


def _completed_with_nobody(run, portal):
    assert run.status == "completed", (run.status, run.error)
    assert run.error is None
    assert (run.rows_seen, run.bidders_read, run.contacts_created) == (0, 0, 0)
    assert portal.browser_closed


def test_the_production_empty_house_completes_with_zero_bidders(db, tmp_path):
    """The screenshot exactly: the label is react-select's placeholder, which
    the singleValue-only check could not see."""
    portal = Portal(page_state=EMPTY)
    run, _, _ = scrape(db, portal=portal, tmp_path=tmp_path)
    _completed_with_nobody(run, portal)
    assert SelectorDrift.__name__ not in (run.error or "")
    assert fixture_contacts(db) == []
    # Nobody went looking for a table that the page said was empty.
    assert not portal.page.on_registered


def test_the_empty_house_completes_when_the_label_is_a_single_value(db, tmp_path):
    portal = Portal(page_state=dict(EMPTY, shown_as="singleValue"))
    run, _, _ = scrape(db, portal=portal, tmp_path=tmp_path)
    _completed_with_nobody(run, portal)


def test_an_empty_menu_with_registered_zero_completes(db, tmp_path):
    """The "Could not open the auction dropdown" morning: the control is not
    showing the label, so the menu is opened, and it has nothing in it."""
    portal = Portal(page_state=dict(EMPTY, shown_as=None, selected=""))
    run, _, _ = scrape(db, portal=portal, tmp_path=tmp_path)
    _completed_with_nobody(run, portal)


def test_options_present_but_no_match_still_fails_even_at_registered_zero(db, tmp_path):
    """Real drift: the house has auctions and none of them reads "All Upcoming
    Auctions". Registered (0) for whichever sale is selected must not excuse
    it — only an empty menu does."""
    state = dict(EMPTY, shown_as="singleValue", selected="Oct 01, 2026 - Marine Sale",
                 options=["Oct 01, 2026 - Marine Sale", "Oct 08, 2026 - Coins"])
    portal = Portal(page_state=state)
    run, _, _ = scrape(db, portal=portal, tmp_path=tmp_path)
    assert run.status == "failed"
    assert "No dropdown option matched 'All Upcoming Auctions'" in run.error
    assert portal.browser_closed


def test_an_empty_menu_with_registered_bidders_still_fails(db, tmp_path):
    """An empty menu alone is not a quiet week: the tab says people are
    registered, so a menu that will not list sales is the page misbehaving."""
    state = dict(EMPTY, shown_as=None, selected="",
                 tabs={"Pending": 0, "Approved": 3, "Registered": 3})
    run, portal, _ = scrape(db, portal=Portal(page_state=state), tmp_path=tmp_path)
    assert run.status == "failed"
    assert "Could not open the auction dropdown" in run.error


def test_a_registered_label_with_no_count_is_not_read_as_zero(db, tmp_path):
    """LA respells the tab. Unknown is not zero: the run goes looking for the
    table, finds none, and fails rather than reporting a quiet week."""
    state = dict(EMPTY, tabs={"Pending": None, "Approved": None, "Registered": None})
    run, portal, _ = scrape(db, portal=Portal(page_state=state), tmp_path=tmp_path)
    # Not `== "failed"`: the fake's table wait raises the builtin TimeoutError,
    # which since 3.11 *is* asyncio.TimeoutError, so the service files it as
    # the deadline. Live Playwright's TimeoutError is not a builtin subclass.
    # The property is that a quiet week is not what gets reported.
    assert run.status != "completed", (run.status, run.error)
    assert run.status in ("failed", "timed_out") and portal.page.on_registered


@pytest.mark.parametrize("shown_as", ["placeholder", "singleValue"])
def test_the_label_already_shown_with_bidders_registered_reads_them_all(db, tmp_path,
                                                                         shown_as):
    """The shown label means "do not touch the dropdown", never "nobody is
    registered". A house with auctions that defaults to All Upcoming is read
    in full."""
    state = {"selected": "All Upcoming Auctions", "shown_as": shown_as}
    run, portal, _ = scrape(db, portal=Portal(page_state=state), tmp_path=tmp_path)
    assert run.status == "completed", (run.status, run.error)
    assert run.rows_seen == len(BIDDERS) and run.contacts_created > 100
    assert not portal.page.dropdown_open
