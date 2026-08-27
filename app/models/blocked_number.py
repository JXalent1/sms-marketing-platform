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
    "carrier_opt_out",    # the carrier's own opt-out record, not our inbound STOP
    "delivery_failure",   # carrier says unreachable/landline/invalid
    "carrier_block",      # carrier refuses this destination
    "manual",             # operator added it
)

# Why `carrier_opt_out` is not folded into `stop_keyword`: they are different
# evidence. `stop_keyword` is a message this system received and can produce on
# demand — the record that answers a TCPA complaint. A carrier opt-out is the
# carrier's assertion that someone opted out somewhere we cannot see, and it
# arrives as an error code on a failed send. Collapsing them would weaken a
# compliance record that exists to be audited.
#
# They are combined where the *client* reads them: blocklist_service.
# OPT_OUT_REASONS holds both, because on the Opt-outs screen and the dashboard
# tile the question is "how many people asked us to stop?", and the answer is
# the same either way. Distinct in the record, combined in the metric.


class BlockedNumber(Base):
    __tablename__ = "blocked_numbers"

    id = Column(Integer, primary_key=True, index=True)
    phone = Column(String(20), unique=True, nullable=False, index=True)   # E.164
    reason = Column(String(100), nullable=False)
    source = Column(String(50), nullable=True)    # provider name, 'manual', 'webhook'
    blocked_at = Column(String(50), nullable=True)
    notes = Column(Text, nullable=True)
