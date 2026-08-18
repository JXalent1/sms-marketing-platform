"""Contact lists — named, reusable audiences.

A list is just a named bag of contacts (many-to-many). The reference system
overloaded a single `auction_date` string to mean "a scraped auction", "a custom
uploaded list", or the magic value "ALL BIDDERS - MAIN LIST", and every consumer
had to re-parse that string. Explicit lists cost one extra table and remove a
whole category of bug.

The "everyone" audience is not a row here — it is a selector handled in
contact_service.resolve_audience().
"""

from sqlalchemy import Column, Integer, String, Text, ForeignKey, UniqueConstraint, Index
from sqlalchemy.sql import func
from app.core.database import Base


class ContactList(Base):
    __tablename__ = "contact_lists"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), unique=True, nullable=False, index=True)
    description = Column(Text, nullable=True)
    source = Column(String(50), nullable=True)      # which ContactSource built it
    created_at = Column(String(50), server_default=func.now())


class ContactListMember(Base):
    __tablename__ = "contact_list_members"
    __table_args__ = (
        UniqueConstraint("list_id", "contact_id", name="uq_list_contact"),
        Index("idx_member_list", "list_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    list_id = Column(Integer, ForeignKey("contact_lists.id", ondelete="CASCADE"), nullable=False)
    contact_id = Column(Integer, ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False)
    added_at = Column(String(50), server_default=func.now())
