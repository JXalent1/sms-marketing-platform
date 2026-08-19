"""Category — the auction niche a buyer cares about.

This is the table the client's actual problem lives in. He runs a different-niche
auction almost every day, and a Memorabilia collector must never get a text about
a walk-in cooler. The reference system had no such concept: niche was inferred by
re-parsing an overloaded `auction_date` string at every call site, which is why
"send to the restaurant people" was a manual spreadsheet job there.

`color_token`, not a hex. The four category colors in the stylesheet were chosen
by running candidate palettes through a colorblind-separation and contrast
validator; they pass all-pairs in both light and dark, and four distinct hues is
the proven ceiling. Storing a token that maps to the `--s1`…`--s4` CSS variables
keeps that guarantee intact. A hex column would let someone pick a fifth colour
that fails the validator, and nothing in the app would notice.
"""

from sqlalchemy import (
    Column, Integer, String, Float, ForeignKey, UniqueConstraint, Index,
)
from sqlalchemy.sql import func
from app.core.database import Base

# The four validated hues plus the fallback. A sixth category gets `neutral`;
# it does not get a new hue. Changing this set is an escalation, not a tweak.
COLOR_TOKENS = ("s1", "s2", "s3", "s4", "neutral")

# How a contact came to carry a category.
#   upload    a CSV import tagged it — reversible by import_service.undo()
#   manual    a human said so — survives an undo, never machine-removed
#   inferred  scored by a future prospecting pass; carries a confidence
TAG_SOURCES = ("upload", "manual", "inferred")


class Category(Base):
    __tablename__ = "categories"

    id = Column(Integer, primary_key=True, index=True)
    slug = Column(String(50), unique=True, nullable=False, index=True)
    label = Column(String(100), nullable=False)
    color_token = Column(String(20), nullable=False)
    sort_order = Column(Integer, nullable=False, default=0)

    # Soft delete only. Deactivating hides a category from the pickers; it does
    # not drop the tagging history, and a campaign already pointed at it keeps
    # resolving. Hard deletion cascades to contact_categories and is refused
    # while the category has members.
    is_active = Column(Integer, nullable=False, default=1)
    created_at = Column(String(50), server_default=func.now())


class ContactCategory(Base):
    """Which buyers care about which niche. Many-to-many, one row per pairing."""

    __tablename__ = "contact_categories"
    __table_args__ = (
        # The dedup guarantee for tagging, mirroring what ix_contacts_phone does
        # for contacts: re-importing the same file cannot tag the same person
        # twice, so a per-category count is always a headcount.
        UniqueConstraint("contact_id", "category_id", name="uq_contact_category"),
        Index("idx_contact_category_category", "category_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    contact_id = Column(
        Integer, ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False
    )
    category_id = Column(
        Integer, ForeignKey("categories.id", ondelete="CASCADE"), nullable=False
    )
    source = Column(String(20), nullable=False)     # one of TAG_SOURCES

    # NULL means certain — a human said so. A score only appears on `inferred`
    # rows. Storing 1.0 for a human decision would make "certain" and "the model
    # was very confident" indistinguishable, and they are not the same thing.
    confidence = Column(Float, nullable=True)

    added_at = Column(String(50), server_default=func.now())
