"""Campaign — one blast.

Counters are maintained incrementally during the send loop and adjusted again
when delivery webhooks arrive, so the numbers on screen stay honest as carriers
report back over the following minutes.
"""

from sqlalchemy import Column, Integer, String, Text, Float
from sqlalchemy.sql import func
from app.core.database import Base

# draft     — built, not sent
# running   — send loop in flight
# completed — send loop finished (individual messages may still be in flight)
# aborted   — stopped before sending (e.g. pre-flight balance check failed)
CAMPAIGN_STATUSES = ("draft", "running", "completed", "aborted", "failed")


class Campaign(Base):
    __tablename__ = "campaigns"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    message_template = Column(Text, nullable=False)

    # How the audience was chosen: "list:12", "all", "source:csv"
    audience = Column(String(255), nullable=False)
    audience_label = Column(String(255), nullable=True)   # human-readable, for the UI

    status = Column(String(20), default="draft")

    total_recipients = Column(Integer, default=0)
    sent_count = Column(Integer, default=0)
    failed_count = Column(Integer, default=0)
    skipped_count = Column(Integer, default=0)

    # Estimated carrier cost at creation time, so you can see the damage before
    # committing and reconcile against the invoice afterwards.
    estimated_segments = Column(Integer, nullable=True)
    estimated_cost = Column(Float, nullable=True)

    created_at = Column(String(50), server_default=func.now())
    started_at = Column(String(50), nullable=True)
    completed_at = Column(String(50), nullable=True)
    abort_reason = Column(Text, nullable=True)
