"""Contact — one person you can text.

Deliberately generic. The reference system modelled this as an auction "Bidder"
with columns like bids_placed and avg_hammer_price, which made every query
auction-shaped and every reuse a rewrite. Here, domain-specific attributes go in
`attributes` (JSON) and the columns stay universal.

`phone` is the identity. It is normalized to E.164 on write and uniquely indexed,
which is what stops the same person entering the list five times from five imports.
"""

from sqlalchemy import Column, Integer, String, Text, Index, JSON
from sqlalchemy.sql import func
from app.core.database import Base


class Contact(Base):
    __tablename__ = "contacts"

    id = Column(Integer, primary_key=True, index=True)

    # Identity
    phone = Column(String(20), unique=True, nullable=False, index=True)   # E.164
    full_name = Column(String(255), nullable=True)
    email = Column(String(255), nullable=True)

    # Provenance — which ContactSource produced this, and its ID over there.
    source = Column(String(50), nullable=True)          # 'csv', 'manual', 'api', ...
    external_ref = Column(String(255), nullable=True)   # ID in the upstream system

    # Client-specific fields live here so the schema never needs a migration
    # to track something new: {"customer_tier": "gold", "last_order": "2026-08-01"}
    attributes = Column(JSON, nullable=True, default=dict)

    # Lifecycle
    is_active = Column(Integer, default=1)              # 0 = suppressed, not deleted
    created_at = Column(String(50), server_default=func.now())
    updated_at = Column(String(50), nullable=True)
    last_messaged_at = Column(String(50), nullable=True)

    __table_args__ = (
        Index("idx_contacts_source", "source"),
        Index("idx_contacts_active", "is_active"),
    )

    def display_name(self) -> str:
        return self.full_name or self.phone
