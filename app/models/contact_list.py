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

    # Set only on an import batch: the category that import tagged. NULL on an
    # ordinary list.
    #
    # Undo has to reverse exactly one category's tags, and the alternative was
    # to parse the category back out of the list's name ("Food Service — 2026-
    # 08-19 upload"). That is the overloaded-string mistake the reference
    # system made with `auction_date`, and it fails the moment someone renames
    # a list. ON DELETE SET NULL: hard-deleting an empty category leaves the
    # historical list intact, just no longer undoable.
    category_id = Column(
        Integer, ForeignKey("categories.id", ondelete="SET NULL"), nullable=True
    )


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

    # Import provenance, written only by import_service.commit(). These are what
    # make an undo subtractive rather than destructive: it reverses what this
    # batch did and nothing else.
    #
    #   created_contact  this import created the contact, so undo may delete it
    #                    (subject to the other two guards — no other category,
    #                    no other list, no message history)
    #   created_tag      this import added the category tag, so undo may remove
    #                    it. 0 when the contact was already in the category, so
    #                    an earlier import's tag is not collateral damage.
    #
    # Both default to 0, which is the honest answer for every membership row
    # added by any other code path.
    created_contact = Column(Integer, nullable=True, default=0)
    created_tag = Column(Integer, nullable=True, default=0)
