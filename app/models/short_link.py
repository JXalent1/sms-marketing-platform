"""Short links, and the clicks that come back through them.

One link per recipient per campaign, not one per campaign. That is the whole
reason the feature is worth building: "340 clicks" is a statistic, and "these
340 people" is a phone list — for an auction house the buyers who opened the
message about tonight's sale are the highest-intent audience it will ever have.
The cost is about 4,200 rows on a full send, which is nothing against a table
that already carries a message row per recipient.

Two tables rather than one, and the split is deliberate.

`short_links` is the thing that was minted: a slug, who it was for, and where it
pointed. `target_url` is stored rather than looked up from the campaign because
a report read three weeks later has to be able to say where a link went after
the auction page is gone — and because a campaign's link can be edited while its
already-sent messages cannot.

`link_clicks` is one row per arrival, and it keeps the ones we believe are
automated. SMS links are fetched by carrier scanners, handset link previews and
security appliances before any human sees them, so a bare click count is not the
number the client thinks it is. Discarding the suspected robots would leave a
number nobody can audit; keeping them and *labelling* them means the reports can
show "340 clicks (12 filtered as automated)", which is honest about being a
heuristic. See app/services/click_classifier.py.

The counters on `short_links` are denormalised on purpose. A campaign report
sums them for 4,200 links in one query; counting `link_clicks` rows per link
instead is the N+1 that looks fine on seeded data and is unusable on a real
campaign. They are maintained in one place — `link_service.record_click()`.
"""

from sqlalchemy import Column, Integer, String, Text, ForeignKey, Index
from app.core.database import Base


class ShortLink(Base):
    __tablename__ = "short_links"

    id = Column(Integer, primary_key=True, index=True)

    # The public half of the URL. Unique because it is the lookup key on an
    # unauthenticated route, and non-sequential because a sequential id in a
    # message tells anyone who receives one how much this client sends.
    slug = Column(String(32), unique=True, nullable=False, index=True)

    campaign_id = Column(Integer, ForeignKey("campaigns.id"), nullable=True)
    contact_id = Column(Integer, ForeignKey("contacts.id"), nullable=True)

    # The message this link was rendered into. Carrying it is what lets a click
    # be dated against the send without a second query shape: the "arrived
    # implausibly soon" half of the bot heuristic needs `sent_at`, and the
    # per-contact message history needs "did this row get clicked".
    message_id = Column(Integer, ForeignKey("sms_messages.id"), nullable=True)

    # Where it points, resolved at mint time. One hop, always — see
    # link_service.validate_target(); a chain is what gets a sending domain
    # filtered, and T-Mobile's code of conduct names it explicitly.
    target_url = Column(Text, nullable=False)

    created_at = Column(String(50), nullable=True)

    # Maintained by link_service.record_click(), never anywhere else.
    # `click_count` is human clicks and is the number the reports lead with;
    # `bot_click_count` is what the classifier filtered and is shown beside it.
    click_count = Column(Integer, nullable=False, default=0)
    bot_click_count = Column(Integer, nullable=False, default=0)
    first_clicked_at = Column(String(50), nullable=True)
    last_clicked_at = Column(String(50), nullable=True)

    __table_args__ = (
        # The campaign report's only query against this table.
        Index("idx_short_links_campaign", "campaign_id"),
        # "has this buyer ever clicked" — the per-contact history screen.
        Index("idx_short_links_contact", "contact_id"),
        Index("idx_short_links_message", "message_id"),
    )


class LinkClick(Base):
    __tablename__ = "link_clicks"

    id = Column(Integer, primary_key=True, index=True)
    short_link_id = Column(Integer, ForeignKey("short_links.id"), nullable=False,
                           index=True)

    clicked_at = Column(String(50), nullable=False)

    # Kept because it is the evidence for the classification. A count that
    # excludes things without recording what it excluded cannot be argued with,
    # and this number goes to a client who will make decisions with it.
    user_agent = Column(Text, nullable=True)

    # Seconds between the message being sent and this arrival, when both are
    # known. NULL is honest: a link on a draft, an unparseable timestamp, or a
    # message that has no sent_at yet.
    seconds_after_send = Column(Integer, nullable=True)

    is_bot = Column(Integer, nullable=False, default=0)
    # Which rule fired, not a boolean's worth of explanation. "ua:googlebot" and
    # "timing:too_soon" are different enough that one being wrong is a different
    # fix from the other.
    bot_reason = Column(String(40), nullable=True)

    __table_args__ = (
        Index("idx_link_clicks_clicked_at", "clicked_at"),
    )
