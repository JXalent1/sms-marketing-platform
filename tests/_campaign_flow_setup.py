"""Shared setup for the three 5e test files.

Not a test module — the leading underscore keeps pytest from collecting it. Same
pattern and the same reasons as `_guardrail_setup.py` and
`_carrier_failure_setup.py`: one purge routine rather than three that can drift.

The two rules every module here inherits, both learned the hard way:

**Leave no rows behind.** The suite shares one database and is deliberately one
end-to-end story — `test_smoke` asserts an exact `sent_count` against audience
"all", so a contact this file forgets to remove fails a module it has never
heard of.

**Give back the rate-limit budget you spend.** `POST /api/campaigns`,
`POST /api/campaigns/from-upload` and `POST /{id}/top-up` are 5/minute per IP,
`POST /login` is 10/minute, and the whole suite runs inside one window from one
address. A module that spends without resetting fails a later one on a 429 that
reads like a bug in the code under test.
"""

import csv
import io
from datetime import datetime, timedelta

from app.core.database import SessionLocal
from app.models.blocked_number import BlockedNumber
from app.models.campaign import Campaign
from app.models.category import ContactCategory
from app.models.contact import Contact
from app.models.contact_list import ContactList, ContactListMember
from app.models.sms_message import SMSMessage
from app.services import category_service

# 555-06xx. Distinct from test_smoke's 555-01xx, _guardrail_setup's 555-04xx and
# test_categories' 954-555-100x, so no module's counts can move another's.
# 200 of them. That is far more than the tests need today and deliberately so:
# running out is a `take()` RuntimeError in the middle of an unrelated
# assertion, and the alternative — reusing numbers — is the failure this pool
# exists to prevent. Widening the range costs nothing.
POOL = [f"+1555555{n:04d}" for n in range(700, 900)]             # …0700–0899
UPLOAD_PHONES = POOL[:5]
BLOCKED_PHONE = "+15555550690"      # outside the pool: `take()` must never issue it
MANUAL_PHONE = "+15555550691"

# Numbers already handed out this run. A test that reuses another's contacts
# inherits their `last_messaged_at`, and with a non-zero hold-back window that
# turns into "everybody is suppressed" three tests later — a failure that reads
# like a bug in the code under test and is a bug in the fixture. Allocating
# rather than sharing is the fix; `take()` is the only way to get a number.
_allocated = []


def take(count: int) -> list:
    """`count` phone numbers no other test in this run has used."""
    start = len(_allocated)
    if start + count > len(POOL):
        raise RuntimeError(
            f"the 5e phone pool is exhausted ({len(POOL)} numbers). Widen POOL — "
            f"do not reuse, or a later test inherits an earlier one's send history."
        )
    chosen = POOL[start:start + count]
    _allocated.extend(chosen)
    return chosen

# Every list, campaign and contact these modules create carries one of these, so
# the purge can find them without guessing.
NAME_PREFIX = "5e-flow:"
CONTACT_SOURCE = "5e-flow-test"

# A message that passes the pre-flight content checks, so a test about the
# audience is never actually failing on the copy.
MESSAGE = "Auctions4America: sale Thursday 10am. Reply STOP to opt out."


def iso_days_ago(days: int) -> str:
    return (datetime.now() - timedelta(days=days)).isoformat()


def csv_bytes(rows) -> bytes:
    """A CSV in the shape a client actually exports: Name, Cell, Company.

    Deliberately not `phone` — `csv_source`'s header matching is the reason the
    import flow works on real files, and a fixture that uses the canonical
    header tests our own spelling rather than his.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Name", "Cell", "Company"])
    for name, phone, company in rows:
        writer.writerow([name, phone, company])
    return buffer.getvalue().encode()


def default_csv(phones=None) -> bytes:
    phones = phones if phones is not None else UPLOAD_PHONES[:3]
    return csv_bytes([(f"Buyer {i}", phone, f"Company {i}")
                      for i, phone in enumerate(phones)])


def purge(db) -> None:
    """Remove every row these modules create. Runs before and after each."""
    campaign_ids = [c.id for c in db.query(Campaign)
                    .filter(Campaign.name.like(f"{NAME_PREFIX}%"))]
    if campaign_ids:
        db.query(SMSMessage).filter(SMSMessage.campaign_id.in_(campaign_ids)).delete(
            synchronize_session=False)
        db.query(Campaign).filter(Campaign.id.in_(campaign_ids)).delete(
            synchronize_session=False)

    phones = POOL + [BLOCKED_PHONE, MANUAL_PHONE]
    contact_ids = [c.id for c in db.query(Contact).filter(Contact.phone.in_(phones))]
    if contact_ids:
        for model, column in ((ContactListMember, ContactListMember.contact_id),
                              (ContactCategory, ContactCategory.contact_id),
                              (SMSMessage, SMSMessage.contact_id)):
            db.query(model).filter(column.in_(contact_ids)).delete(
                synchronize_session=False)
        db.query(Contact).filter(Contact.id.in_(contact_ids)).delete(
            synchronize_session=False)

    # Lists named for a campaign, plus any suffixed duplicate the collision
    # handling produced. LIKE rather than an equality test for that reason.
    for row in db.query(ContactList).filter(ContactList.name.like(f"{NAME_PREFIX}%")):
        db.query(ContactListMember).filter(ContactListMember.list_id == row.id).delete(
            synchronize_session=False)
        db.delete(row)

    db.query(BlockedNumber).filter(BlockedNumber.phone == BLOCKED_PHONE).delete(
        synchronize_session=False)
    db.commit()


def food_service_id(db) -> int:
    category = category_service.get_by_slug(db, "food_service")
    assert category is not None, "the module-2 seed should have created this"
    return category.id


def purged_db_fixture_body():
    """Generator body for a module-scoped `db` fixture that purges both ends."""
    db = SessionLocal()
    try:
        purge(db)
        yield db
        purge(db)
    finally:
        db.close()


def rate_limit_fixture_body():
    """Hand back every limiter budget these modules spend.

    Three routers, because 5e's endpoints live in two of them and the login
    limiter is in a third. Resetting only the one a module happens to use is how
    the next module inherits a debt it cannot see.
    """
    from app.routers import (campaigns as campaigns_router,
                             campaign_uploads as uploads_router,
                             pages as pages_router)

    for router in (campaigns_router, uploads_router, pages_router):
        router.limiter.reset()
    yield
    for router in (campaigns_router, uploads_router, pages_router):
        router.limiter.reset()
