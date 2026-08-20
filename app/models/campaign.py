"""Campaign — one blast.

Counters are maintained incrementally during the send loop and adjusted again
when delivery webhooks arrive, so the numbers on screen stay honest as carriers
report back over the following minutes.
"""

from sqlalchemy import Column, Integer, String, Text, Float, ForeignKey, Index
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

    # Which auction niche this campaign is for.
    #
    # Nullable in the schema and required by the API, and those two are not in
    # conflict. Campaigns created before module 4 predate the concept and there
    # is no honest value to backfill them with — a guess here would be worse
    # than a blank, because "Food Service" on a campaign nobody categorised is
    # indistinguishable from one somebody did. They keep NULL and the UI shows
    # "—". Everything created from now on carries a real category or an
    # explicit, recorded override.
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True)

    # The one escape from the category requirement, and it has to be asked for
    # by name. A default of "off" is what makes it an audit trail: a 1 here is
    # a human having typed the override, not a field that drifted.
    cross_category_override = Column(Integer, nullable=False, default=0)

    # How the audience was chosen: "list:12", "all", "source:csv"
    audience = Column(String(255), nullable=False)
    audience_label = Column(String(255), nullable=True)   # human-readable, for the UI

    status = Column(String(20), default="draft")

    total_recipients = Column(Integer, default=0)
    sent_count = Column(Integer, default=0)
    failed_count = Column(Integer, default=0)
    skipped_count = Column(Integer, default=0)

    # Contacts held back because they were texted inside the suppression window.
    # Counted separately from skipped_count, which the send loop also uses for
    # blocklist and region skips — "we held 37 people back so they aren't texted
    # twice this week" and "37 numbers were undeliverable" are different news.
    suppressed_count = Column(Integer, nullable=False, default=0)

    # ISO timestamp a scheduled campaign becomes due. NULL = send on demand.
    # The scheduler hands a due campaign to the same send path a button press
    # does, pre-flight included; this column only decides *when* that happens.
    scheduled_at = Column(String(50), nullable=True)

    # Estimated carrier cost at creation time, so you can see the damage before
    # committing and reconcile against the invoice afterwards.
    estimated_segments = Column(Integer, nullable=True)
    estimated_cost = Column(Float, nullable=True)

    created_at = Column(String(50), server_default=func.now())
    started_at = Column(String(50), nullable=True)
    completed_at = Column(String(50), nullable=True)
    abort_reason = Column(Text, nullable=True)

    __table_args__ = (
        # The scheduler's only query: drafts whose time has come. Runs once a
        # minute forever, so it should not be a table scan.
        Index("idx_campaigns_scheduled_at", "scheduled_at"),
        Index("idx_campaigns_category", "category_id"),
    )
