"""Import every model here so Base.metadata carries all tables.

Alembic's autogenerate diffs against this, so a model that is not imported here
is a model Alembic will cheerfully propose dropping.
"""

from app.models.contact import Contact
from app.models.contact_list import ContactList, ContactListMember
from app.models.campaign import Campaign
from app.models.sms_message import SMSMessage
from app.models.blocked_number import BlockedNumber
from app.models.app_setting import AppSetting

__all__ = [
    "Contact",
    "ContactList",
    "ContactListMember",
    "Campaign",
    "SMSMessage",
    "BlockedNumber",
    "AppSetting",
]
