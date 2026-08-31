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

    # The cap the client typed, if he typed one: "send to the first 50 as a
    # test". NULL means no cap was asked for, which is the ordinary case.
    #
    # Recorded rather than discarded since 5h, and this is a *record of an
    # input*, not a new rule — the cap is still applied once, at build time, in
    # campaign_builder. It is here because a later run has to know a cap
    # existed. 5e found the first consequence of not knowing (a top-up
    # delivering to the remainder the cap withheld) and fixed it by changing
    # what "added since" means; 5h found the second (a released hold ignoring
    # the cap) and can only refuse if the column says a cap was asked for.
    # Additive and nullable: campaigns built before it keep NULL, and NULL is
    # the honest value — we do not know, and none of them can reach the release
    # path anyway, because no campaign built before 5h has a `held_back` row.
    batch_size = Column(Integer, nullable=True)

    # Where this campaign's {link} merge tag points. NULL means the campaign
    # carries no link, which is the ordinary case for a message that just says
    # "the sale is Thursday".
    #
    # Stored on the campaign as well as on every minted link, and the two are
    # not redundant. This is the *current* destination, which is what a top-up
    # mints against; `short_links.target_url` is where each already-sent
    # message's link actually pointed, which is what a report has to show after
    # the auction page is gone.
    link_target_url = Column(Text, nullable=True)

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
