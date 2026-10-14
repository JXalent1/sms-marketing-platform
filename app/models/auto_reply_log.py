"""auto_reply_log — every decision about replying to an inbound text.

Incident 2026-10-09 to 10-12: our number answered two businesses'
auto-responders for three days, ~114k outbound, $2,818. Auto-replies went
straight to the provider and were written nowhere, so the platform could not
see the loop it was in. This table is where it becomes visible, and it is also
the guard's own memory: the per-number cap and the hourly circuit breaker are
both counts over it (`app/services/auto_reply_guard.py`).

One row per inbound message that reached the reply decision, whatever was
decided — a suppression is as much a record as a send, and a loop that the
guard is holding shows up here as thousands of `suppressed` rows.

`decision` is `sent` from the moment the guard claims the reply, which is
before the provider is called. A send that then fails keeps `sent` and gets a
`reason`: the claim is what the cap counts, and an attempt we cannot prove did
not reach the handset is the one we must not repeat.

`created_at` has one writer, the guard, keeping the application's clock
(`app.core.clock.now()`). No server default — that is the second writer
`contact_lists.created_at` cost a migration to remove.
"""

from sqlalchemy import Column, DateTime, Index, Integer, String

from app.core.database import Base

AUTO_REPLY_KINDS = ("auto_reply", "stop", "start", "help")
AUTO_REPLY_DECISIONS = ("sent", "suppressed")


class AutoReplyLog(Base):
    __tablename__ = "auto_reply_log"

    id = Column(Integer, primary_key=True)
    phone = Column(String(32), nullable=False)
    kind = Column(String(16), nullable=False)          # AUTO_REPLY_KINDS
    decision = Column(String(16), nullable=False)      # AUTO_REPLY_DECISIONS
    # The rule that suppressed it, or why a claimed send failed. Ours, not the
    # client's: carrier text reaching here goes through scrub_provider_text().
    reason = Column(String(200), nullable=True)
    inbound_excerpt = Column(String(160), nullable=True)
    created_at = Column(DateTime, nullable=False)

    __table_args__ = (
        # The per-number cap: "has this number had a reply in the window?"
        Index("idx_auto_reply_phone_created", "phone", "created_at"),
        # The circuit breaker: "how many replies in the last hour, any number?"
        Index("idx_auto_reply_created", "created_at"),
    )
