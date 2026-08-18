"""Import every model here so Base.metadata.create_all() sees all tables."""

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
