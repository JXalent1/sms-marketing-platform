"""Contact lists — named, reusable audiences.

A list is just a named bag of contacts (many-to-many). The reference system
overloaded a single `auction_date` string to mean "a scraped auction", "a custom
uploaded list", or the magic value "ALL BIDDERS - MAIN LIST", and every consumer
had to re-parse that string. Explicit lists cost one extra table and remove a
whole category of bug.

The "everyone" audience is not a row here — it is a selector handled in
contact_service.resolve_audience().
"""

from datetime import datetime

from sqlalchemy import Column, Integer, String, Text, ForeignKey, UniqueConstraint, Index
from sqlalchemy.sql import func
from app.core.database import Base

# A value that sorts before every real timestamp, so an unparseable `created_at`
# sorts LAST under a newest-first ordering instead of raising. Same reasoning as
# `audience_label()`: this renders in a picker and in page headers, and a helper
# that throws turns one bad row into a 500 on a screen that was only trying to
# sort. See `parse_created_at()`.
_SORTS_LAST = datetime.min


def now_iso() -> str:
    """The one clock and the one spelling every `created_at` is written with."""
    return datetime.now().isoformat()


def parse_created_at(value) -> datetime:
    """`contact_lists.created_at` as a datetime, for ordering. Never raises.

    **Order by this, never by the string.** Until session 5i the column had two
    writers keeping two clocks and two spellings, and both are in the live
    database today:

        contact_service.get_or_create_list()  omitted the column, so SQLite's
            CURRENT_TIMESTAMP server default wrote it — UTC, "YYYY-MM-DD HH:MM:SS"
        import_service.commit()               datetime.now().isoformat() — local,
            "YYYY-MM-DDTHH:MM:SS.ffffff"

    Nothing compared these rows until 5i made recency the sort order of the
    picker, the dashboard and the composer. A lexicographic comparison across the
    two spellings is wrong independently of the clock, and in the direction
    nobody checks: a space (0x20) sorts before a "T" (0x54), so a server-defaulted
    row always loses to a same-day isoformat row however much later it was
    written.

    5i removed the server default (one writer, one clock, one spelling) and
    migration `f4a1c7d90e52` rewrote the rows already stored. This function is the
    reading half of the same fix: it parses rather than compares, so a row that
    predates the migration — or arrives from a hand-written INSERT — still sorts
    by its real instant.

    It deliberately does NOT shift a space-separated value from UTC to local.
    Converting is the migration's job and it has run; a reader that also
    converted would be a second writer of meaning, and it would double-shift
    every row the migration already fixed.
    """
    text = (value or "").strip()
    if not text:
        return _SORTS_LAST
    try:
        return datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return _SORTS_LAST


class ContactList(Base):
    __tablename__ = "contact_lists"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), unique=True, nullable=False, index=True)
    description = Column(Text, nullable=True)
    source = Column(String(50), nullable=True)      # which ContactSource built it

    # No server default, and a Python-side one instead. SQLite's
    # CURRENT_TIMESTAMP is **UTC** and spells itself with a space; every other
    # timestamp this application writes is `datetime.now().isoformat()`, which is
    # local and spells itself with a "T". Leaving the server default here made
    # this a two-writer, two-clock, two-spelling column — the same defect
    # `contact_list_members.added_at` had — and 5i made it visible by sorting the
    # audience picker on it.
    #
    # `default=now_iso` rather than "every caller remembers to pass it": a rule
    # that depends on each insert site remembering is a guard on one path, and
    # this column has two writers today and will have a third. Both existing
    # sites pass the value explicitly as well, which is belt and braces, not a
    # contradiction — whichever fires, the spelling and the clock are the same.
    #
    # **The table's own DDL still says `DEFAULT (CURRENT_TIMESTAMP)`.** Changing
    # that in SQLite means rebuilding the table, which is escalation item 8, and
    # the value it would write is now unreachable through the ORM. It is still
    # reachable by a raw `INSERT` that names no `created_at`, which is the other
    # reason `parse_created_at()` above must keep understanding the space-
    # separated spelling rather than assuming it away. See migration
    # `f4a1c7d90e52`.
    created_at = Column(String(50), nullable=True, default=now_iso)

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
