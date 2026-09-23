"""Shared setup for the bidder-source tests: a clean slate, and a run.

Everything these tests create is removed before and after each test, because
the suite shares one database and `test_smoke` sends to audience "all" and
asserts an exact count. 954-700-xxxx is the fixture's own number block.
"""

import socket
from contextlib import contextmanager

import pytest

from app.core.database import SessionLocal
from app.models.bidder_profile import BidderProfile, BidderScrapeRun
from app.models.blocked_number import BlockedNumber
from app.models.contact import Contact
from app.models.contact_list import ContactList, ContactListMember
from app.models.scrape import PhoneLookup
from app.services import bidder_scrape
from app.sources import platforms
from tests._la_portal import Portal, make_source

PREFIX = "+1954700"
LIST_NAME = platforms.list_name(platforms.LIVEAUCTIONEERS)


def purge(db) -> None:
    ids = [cid for (cid,) in db.query(Contact.id).filter(Contact.phone.like(f"{PREFIX}%"))]
    db.query(BidderProfile).delete(synchronize_session=False)
    db.query(BidderScrapeRun).delete(synchronize_session=False)
    lists = [lid for (lid,) in db.query(ContactList.id).filter(ContactList.name == LIST_NAME)]
    if lists or ids:
        db.query(ContactListMember).filter(
            (ContactListMember.list_id.in_(lists or [-1]))
            | (ContactListMember.contact_id.in_(ids or [-1]))).delete(synchronize_session=False)
    db.query(ContactList).filter(ContactList.name == LIST_NAME).delete(synchronize_session=False)
    db.query(Contact).filter(Contact.phone.like(f"{PREFIX}%")).delete(synchronize_session=False)
    db.query(BlockedNumber).filter(BlockedNumber.phone.like(f"{PREFIX}%")).delete(
        synchronize_session=False)
    db.query(PhoneLookup).filter(PhoneLookup.phone.like(f"{PREFIX}%")).delete(
        synchronize_session=False)
    db.commit()


@pytest.fixture
def db():
    session = SessionLocal()
    purge(session)
    try:
        yield session
    finally:
        session.rollback()
        purge(session)
        session.close()


def scrape(db, portal: Portal = None, tmp_path=None, timeout: float = 30, **kw):
    """One run against the fake portal. Returns (run, portal, source)."""
    portal = portal or Portal()
    source = make_source(portal, str(tmp_path))
    run = bidder_scrape.run_scrape(db, source, timeout_seconds=timeout, **kw)
    return run, portal, source


def fixture_contacts(db):
    return db.query(Contact).filter(Contact.phone.like(f"{PREFIX}%")).all()


class NetworkAttempted(AssertionError):
    pass


@contextmanager
def no_network():
    """Refuse every outbound connection for the duration.

    Patches the two entry points a client library actually reaches —
    `socket.socket.connect` (what an HTTP client or a browser driver's own
    socket calls) and `socket.create_connection`. The caller proves the guard
    with a known-answer probe of a *real* socket rather than by calling the
    function it just replaced, which `accept-B1.sh` learned cannot fail.
    """
    original_connect = socket.socket.connect
    original_create = socket.create_connection

    def refuse(*args, **kwargs):
        raise NetworkAttempted(f"network call attempted: {args[1:]!r}")

    socket.socket.connect = refuse
    socket.create_connection = refuse
    try:
        yield
    finally:
        socket.socket.connect = original_connect
        socket.create_connection = original_create
