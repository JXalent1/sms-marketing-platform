"""Shared setup for the session-5f test modules.

Not a test module — the leading underscore keeps pytest from collecting it. Same
pattern and the same two rules as `_campaign_flow_setup.py`:

**Leave no rows behind.** The suite shares one database and is deliberately one
end-to-end story. `test_smoke` asserts an exact `sent_count` against audience
"all", so a contact this file forgets to remove fails a module it has never
heard of.

**Give back the rate-limit budget you spend.** Every campaign-creating endpoint
is 5/minute per IP and the whole suite runs from one address inside one window.

One rule of its own: **always restore `SHORT_LINK_DOMAIN`.** It is read live on
every call (`link_service.domain()`), which is what makes "the domain is not
registered yet" a supported state rather than a restart — and it also means a
context left open would give every later module a link domain the rest of the
suite was written without.
"""

import csv
import io
from contextlib import contextmanager
from datetime import datetime, timedelta

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.blocked_number import BlockedNumber
from app.models.campaign import Campaign
from app.models.category import ContactCategory
from app.models.contact import Contact
from app.models.contact_list import ContactList, ContactListMember
from app.models.short_link import LinkClick, ShortLink
from app.models.sms_message import SMSMessage
from app.sms.base import SendResult

# 555-555-09xx. Distinct from test_smoke's 555-01xx, _guardrail_setup's 555-04xx,
# _campaign_flow_setup's 555-0700–0899 and test_categories' 954-555-100x, so no
# module's counts can move another's.
POOL = [f"+1555555{n:04d}" for n in range(900, 1000)]        # …0900–0999
_allocated = []

NAME_PREFIX = "5f-links:"
CONTACT_SOURCE = "5f-link-test"

# A domain that cannot be mistaken for the real one and cannot be registered.
# Eight characters, which is what makes the segment arithmetic in the tests
# concrete: a link is len(domain) + 1 + SLUG_LENGTH = 17 characters.
TEST_DOMAIN = "a4a.test"
TARGET_URL = "https://auctions4america.test/thursday-restaurant-equipment"

# Passes the pre-flight content checks, so a test about links is never actually
# failing on the copy.
MESSAGE = "Auctions4America: sale Thursday 10am. Reply STOP to opt out."


def take(count: int) -> list:
    """`count` phone numbers no other test in this run has used."""
    start = len(_allocated)
    if start + count > len(POOL):
        raise RuntimeError(
            f"the 5f phone pool is exhausted ({len(POOL)} numbers). Widen POOL — "
            f"do not reuse, or a later test inherits an earlier one's send history."
        )
    chosen = POOL[start:start + count]
    _allocated.extend(chosen)
    return chosen


@contextmanager
def short_link_domain(domain: str = TEST_DOMAIN):
    """Point the app at a short-link domain, then put it back. Always."""
    previous = settings.SHORT_LINK_DOMAIN
    settings.SHORT_LINK_DOMAIN = domain
    try:
        yield domain
    finally:
        settings.SHORT_LINK_DOMAIN = previous


@contextmanager
def click_window(seconds: int):
    """Override the "arrived implausibly soon" threshold for one test."""
    previous = settings.CLICK_MIN_HUMAN_SECONDS
    settings.CLICK_MIN_HUMAN_SECONDS = seconds
    try:
        yield seconds
    finally:
        settings.CLICK_MIN_HUMAN_SECONDS = previous


class CostingProvider:
    """A carrier that reports what it charged, as the real one does.

    The console provider deliberately reports no cost — a dry run spends nothing
    and a fabricated figure would look exactly like a measurement inside the
    reconciliation. So the only way to exercise A4 is a stub that prices, and
    this is it. It sends nothing: `send()` returns a result and touches no
    network, and `SMS_PROVIDER` stays `console` throughout.
    """

    name = "console"

    def __init__(self, amount="0.0043", rate="0.0035", fee="0.0008"):
        self.amount, self.rate, self.fee = amount, rate, fee
        self.sent = []

    async def send(self, to, text):
        self.sent.append((to, text))
        return SendResult(success=True, message_id=f"stub-{len(self.sent):05d}",
                          parts=1, cost=self.amount, cost_rate=self.rate,
                          cost_carrier_fee=self.fee, cost_currency="USD")

    async def get_balance(self):
        return 999_999.0

    async def get_message_status(self, message_id):
        return "delivered"


def iso_minutes_ago(minutes: int) -> str:
    return (datetime.now() - timedelta(minutes=minutes)).isoformat()


def csv_bytes(rows) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Name", "Cell", "Company"])
    for name, phone, company in rows:
        writer.writerow([name, phone, company])
    return buffer.getvalue().encode()


def seed_contacts(db, phones, prefix="Link Buyer", source=CONTACT_SOURCE) -> list:
    """Contacts under a source of this test's own.

    `source` is per test, not per module, and that is not tidiness. The audience
    selector `source:<name>` resolves to *every* contact carrying it, so a
    shared source makes each test's campaign include the previous tests'
    contacts — which showed up here as "one link per recipient" quietly passing
    with nine links for six people, and as a click attributed to the wrong
    buyer. The same shape as 5e's "a guard on the wrong set is not a guard".
    """
    contacts = []
    for i, phone in enumerate(phones):
        contact = Contact(phone=phone, full_name=f"{prefix} {i}",
                          source=source, is_active=1, attributes={})
        db.add(contact)
        contacts.append(contact)
    db.commit()
    for contact in contacts:
        db.refresh(contact)
    return contacts


def purge(db) -> None:
    """Remove every row these modules create. Runs before and after each."""
    campaign_ids = [c.id for c in db.query(Campaign)
                    .filter(Campaign.name.like(f"{NAME_PREFIX}%"))]
    link_ids = [row.id for row in db.query(ShortLink)
                .filter(ShortLink.campaign_id.in_(campaign_ids))] if campaign_ids else []

    contact_ids = [c.id for c in db.query(Contact).filter(Contact.phone.in_(POOL))]
    if contact_ids:
        link_ids += [row.id for row in db.query(ShortLink)
                     .filter(ShortLink.contact_id.in_(contact_ids))]

    if link_ids:
        db.query(LinkClick).filter(LinkClick.short_link_id.in_(link_ids)).delete(
            synchronize_session=False)
        db.query(ShortLink).filter(ShortLink.id.in_(link_ids)).delete(
            synchronize_session=False)

    if campaign_ids:
        db.query(SMSMessage).filter(SMSMessage.campaign_id.in_(campaign_ids)).delete(
            synchronize_session=False)
        db.query(Campaign).filter(Campaign.id.in_(campaign_ids)).delete(
            synchronize_session=False)

    if contact_ids:
        for model, column in ((ContactListMember, ContactListMember.contact_id),
                              (ContactCategory, ContactCategory.contact_id),
                              (SMSMessage, SMSMessage.contact_id)):
            db.query(model).filter(column.in_(contact_ids)).delete(
                synchronize_session=False)
        db.query(Contact).filter(Contact.id.in_(contact_ids)).delete(
            synchronize_session=False)

    for row in db.query(ContactList).filter(ContactList.name.like(f"{NAME_PREFIX}%")):
        db.query(ContactListMember).filter(ContactListMember.list_id == row.id).delete(
            synchronize_session=False)
        db.delete(row)

    db.query(BlockedNumber).filter(BlockedNumber.phone.in_(POOL)).delete(
        synchronize_session=False)
    db.commit()


def purged_db_fixture_body():
    db = SessionLocal()
    try:
        purge(db)
        yield db
        purge(db)
    finally:
        db.close()


def rate_limit_fixture_body():
    """Hand back every limiter budget these modules spend."""
    from app.routers import (campaigns as campaigns_router,
                             campaign_uploads as uploads_router,
                             pages as pages_router)

    for router in (campaigns_router, uploads_router, pages_router):
        router.limiter.reset()
    yield
    for router in (campaigns_router, uploads_router, pages_router):
        router.limiter.reset()
