"""What an audience selector resolves to, before anything is sent.

Lifted out of `app/routers/campaigns.py` in session 5m, which put that file over
the 500-line rule. The boundary is the layering rule rather than the line count:
this resolves an audience, partitions the hold-back window and reads the
blocklist, which is business logic, and a router is meant to validate, delegate
and serialize. It had been a private function in the router since module 4.

**Both of the composer's endpoints answer from this one call.** `/preview` runs
on every keystroke and `/preflight` when he presses Run checks, and the summary
panel is painted from whichever reply is newest — so the two have to agree about
the audience, the counts and the cap, or the panel says one thing and then
another about the same send. Session 5m found it saying three things at once.
"""

from typing import Optional

from sqlalchemy.orm import Session

from app.services import contact_service, suppression_service
from app.services.blocklist_service import load_blocked_set


def resolve(db: Session, audience: Optional[str],
            batch_size: Optional[int] = None) -> dict:
    """What an audience selector actually resolves to, before anything is sent.

    Returns the counts the composer's summary panel shows and one sample contact
    for the phone preview. The partition is the same call `create_campaign()`
    makes, so the composer's numbers and the draft's numbers cannot disagree.

    A bad selector reports zeros rather than raising: this runs on every
    keystroke and a half-typed selector is not an error worth a 500.
    """
    days = suppression_service.suppression_days(db)
    # The audience this answer is *about*, carried back with the numbers. The
    # composer's summary panel used to read its Audience row off the dropdown
    # while reading every figure beside it off a response, so the row and the
    # numbers could describe different audiences and did (session 5m). One
    # response now carries both, from the same function `create_campaign()`
    # stores its label with — `contact_service.audience_label()` — so the panel
    # and the draft cannot spell the same selector two ways.
    label = contact_service.audience_label(db, audience) if audience else None
    empty = {"recipients": 0, "suppressed": 0, "opted_out": 0, "sample": None,
             "suppression_days": days, "suppression_clears_at": None,
             "audience": audience or None, "audience_label": label,
             "sendable": []}
    if not audience:
        return empty
    try:
        resolved = contact_service.resolve_audience(db, audience)
    except ValueError:
        return empty

    sendable, suppressed = suppression_service.partition_recent(db, resolved)
    if batch_size and batch_size > 0:
        sendable = sendable[:batch_size]

    # Opted-out numbers are counted, not removed: the send loop is what refuses
    # them, and the count belongs on screen beforehand rather than in the
    # post-mortem. One query for the whole set, not one per contact.
    blocked = load_blocked_set(db)
    return {
        "audience": audience,
        "audience_label": label,
        "recipients": len(sendable),
        "suppressed": len(suppressed),
        "opted_out": sum(1 for c in resolved if c.phone in blocked),
        "sample": sendable[0] if sendable else None,
        # A6: the held-back count already existed and was only ever shown as a
        # bare number. When it clears is the half that decides whether he waits
        # or re-cuts the audience, and it is computable from the set we are
        # already holding — so it is computed here, once, rather than left to a
        # screen to work out from a window it would have to look up separately.
        "suppression_days": days,
        "suppression_clears_at": suppression_service.suppression_clears_at(
            suppressed, days),
        # The resolved audience itself, for pre-flight's per-recipient render.
        # /preview ignores it: that runs on every keystroke and must stay cheap.
        "sendable": sendable,
    }
