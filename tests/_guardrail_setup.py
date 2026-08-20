"""Shared setup for the two module-4 test files.

Not a test module — the leading underscore keeps pytest from collecting it.
`test_campaign_guardrails.py` and `test_campaign_preflight.py` were one file
until it crossed the 500-line rule; splitting them along the checklist boundary
left the fixture body as the only thing they both needed, and duplicating it
would have meant two purge routines that could drift apart.

The rules both files inherit are documented at the top of
`test_campaign_guardrails.py`: leave no rows behind, and give back the
rate-limit budget you spend.
"""

from datetime import datetime, timedelta

from app.core.database import SessionLocal
from app.models.campaign import Campaign
from app.models.contact import Contact
from app.models.contact_list import ContactList, ContactListMember
from app.models.sms_message import SMSMessage
from app.services import category_service, contact_service

# 555-04xx: distinct from test_smoke's 555-01xx and test_categories'
# 954-555-100x, so no module's counts can move another's.
FRESH_PHONE = "+15555550401"        # never texted
RECENT_PHONE = "+15555550402"       # texted 2 days ago — inside the window
OLD_PHONE = "+15555550403"          # texted 5 days ago — outside it
PHONES = (FRESH_PHONE, RECENT_PHONE, OLD_PHONE)

LIST_NAME = "module-4 guardrail list"
CAMPAIGN_PREFIX = "guardrail:"


def iso_days_ago(days: int) -> str:
    return (datetime.now() - timedelta(days=days)).isoformat()


def purge(db) -> None:
    """Remove every row these modules create. Runs before and after each."""
    campaign_ids = [c.id for c in db.query(Campaign)
                    .filter(Campaign.name.like(f"{CAMPAIGN_PREFIX}%"))]
    if campaign_ids:
        db.query(SMSMessage).filter(SMSMessage.campaign_id.in_(campaign_ids)).delete(
            synchronize_session=False)
        db.query(Campaign).filter(Campaign.id.in_(campaign_ids)).delete(
            synchronize_session=False)

    contact_ids = [c.id for c in db.query(Contact).filter(Contact.phone.in_(PHONES))]
    if contact_ids:
        db.query(ContactListMember).filter(
            ContactListMember.contact_id.in_(contact_ids)).delete(synchronize_session=False)
        db.query(Contact).filter(Contact.id.in_(contact_ids)).delete(
            synchronize_session=False)

    for row in db.query(ContactList).filter(ContactList.name == LIST_NAME):
        db.query(ContactListMember).filter(ContactListMember.list_id == row.id).delete(
            synchronize_session=False)
        db.delete(row)
    db.commit()


def seed(db) -> dict:
    """Three contacts on one list, with the three last-texted states.

    On a list rather than in a category: the campaigns still carry a category,
    but tagging real contacts into a seeded category would move the
    per-category counts `test_categories` asserts on.
    """
    contact_list = contact_service.get_or_create_list(db, LIST_NAME)
    for phone, name, last in (
        (FRESH_PHONE, "Never Texted", None),
        (RECENT_PHONE, "Two Days Ago", iso_days_ago(2)),
        (OLD_PHONE, "Five Days Ago", iso_days_ago(5)),
    ):
        contact = contact_service.upsert_contact(
            db, phone=phone, full_name=name, source="guardrail-test")
        contact.last_messaged_at = last
        contact_service.add_to_list(db, contact_list.id, contact.id)
    db.commit()

    category = category_service.get_by_slug(db, "food_service")
    assert category is not None, "the module-2 seed should have created this"
    return {"category_id": category.id, "audience": f"list:{contact_list.id}"}


def seeded_fixture_body():
    """Generator body for a module-scoped `seeded` fixture. Purges both ends."""
    db = SessionLocal()
    try:
        purge(db)
        yield seed(db)
        purge(db)
    finally:
        db.close()


def rate_limit_fixture_body():
    """Generator body for the limiter reset — see test_campaign_guardrails.py."""
    from app.routers import campaigns as campaigns_router

    campaigns_router.limiter.reset()
    yield
    campaigns_router.limiter.reset()
