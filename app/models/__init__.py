"""Import every model here so Base.metadata carries all tables.

Alembic's autogenerate diffs against this, so a model that is not imported here
is a model Alembic will cheerfully propose dropping.
"""

from app.models.contact import Contact
from app.models.category import Category, ContactCategory
from app.models.contact_list import ContactList, ContactListMember
from app.models.campaign import Campaign
from app.models.sms_message import SMSMessage
from app.models.blocked_number import BlockedNumber
from app.models.app_setting import AppSetting
from app.models.short_link import ShortLink, LinkClick
from app.models.prospect import Prospect, ProspectSighting, ProspectRejection
from app.models.scrape import ScrapeJob, PhoneLookup

__all__ = [
    "ShortLink",
    "LinkClick",
    "Prospect",
    "ProspectSighting",
    "ProspectRejection",
    "ScrapeJob",
    "PhoneLookup",
    "Contact",
    "Category",
    "ContactCategory",
    "ContactList",
    "ContactListMember",
    "Campaign",
    "SMSMessage",
    "BlockedNumber",
    "AppSetting",
]
