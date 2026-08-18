"""Blocklist — numbers that must never be texted.

This table is the compliance record. It is yours, not the carrier's, so it
survives a provider migration; carrier-side opt-out lists do not.

Never hard-delete rows to "clean up". An unblock is a deliberate act (someone
texted START), and everything else stays forever.
"""

from sqlalchemy import Column, Integer, String, Text
from app.core.database import Base

BLOCK_REASONS = (
    "stop_keyword",       # they texted STOP — legally binding
    "delivery_failure",   # carrier says unreachable/landline/invalid
    "carrier_block",      # carrier refuses this destination
    "manual",             # operator added it
)


class BlockedNumber(Base):
    __tablename__ = "blocked_numbers"

    id = Column(Integer, primary_key=True, index=True)
    phone = Column(String(20), unique=True, nullable=False, index=True)   # E.164
    reason = Column(String(100), nullable=False)
    source = Column(String(50), nullable=True)    # provider name, 'manual', 'webhook'
    blocked_at = Column(String(50), nullable=True)
    notes = Column(Text, nullable=True)
