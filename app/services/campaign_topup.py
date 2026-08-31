"""Top-up: send an already-sent campaign's message to contacts added since.

The client works campaign-by-campaign off a fresh list, and the list keeps
growing after the blast goes out — somebody phones in, a second sheet arrives.
Rebuilding the campaign to reach five people loses the first campaign's numbers;
sending them "the same message" from a new campaign splits one auction's results
across two rows. So the addition folds into the campaign it belongs to
(modules.md, decisions taken 2026-08-24).

Three things make that safe, and they are the whole module.

**"Added since" means added since, and is read from the data rather than
inferred.** The candidate set is the members of the campaign's own list whose
`added_at` is later than the campaign's `created_at` — not "everyone the audience
selector resolves to now, minus everyone with a message row". Those two are not
the same set, and the difference is dangerous in two directions found by review:

  - A campaign capped with `batch_size` deliberately withheld the rest of its
    list. The cap is the "send to the first fifty as a test" control. Under the
    subtract-what-was-sent rule, one click on Top up delivered to all of them.
  - A campaign on `all` or `category:` has no list of its own, so every contact
    imported afterwards for a different auction resolved as "new". Last month's
    memorabilia message to tonight's restaurant buyers, one click, no warning.

So a top-up needs a `list:` audience, and it says so rather than guessing. That is
also the only audience the flow this session built ever produces.

**Nobody the campaign already reached is texted twice.** The exclusion is by
*phone*, taken from the campaign's own message rows, not by contact id: a contact
deleted and re-imported is a new row with the same number and the same person
holding the handset. It is defence in depth behind the `added_at` window rather
than the primary rule — a number can be added to a list twice.

**A top-up is a send, so it runs the send path's own refusals.** The degraded
provider check and the capacity check run before anything is written, in that
order, exactly as `preflight()` runs them for a first send — then the same
`run_send_loop()` a button press reaches. There is no thinner path here; that is
the property `campaign_dispatch.py` protects one level up for the scheduler.

**Refused before anything is queued.** 5d's lesson from `POST /{id}/send`: a
refusal that happens after the rows are written leaves a completed campaign
carrying orphan `pending` messages and a status that has to be walked back.
Nothing is written until both checks pass.
"""

import logging
from datetime import datetime
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.contact import Contact
from app.models.contact_list import ContactListMember
from app.models.sms_message import SMSMessage
from app.services import suppression_service
from app.services.campaign_builder import CampaignError, wholesale_estimate
from app.services.campaign_service import CampaignService
from app.sms.segments import count_segments

logger = logging.getLogger("campaign")

# Only a campaign that actually sent can be topped up. Each of the other states
# has its own answer, and none of them is "queue more messages onto it".
TOP_UP_STATE_ERRORS = {
    "draft": ("This campaign has not been sent yet, so there is nothing to top up. "
              "Edit the draft and send it."),
    "running": ("This campaign is still sending. Wait for it to finish, then top it "
                "up."),
    # Nothing in this codebase moves a campaign back from `aborted`, and this must
    # not become the first thing that does — decision 002's justification for
    # refusing before queueing is that a campaign which never ran can simply be
    # re-run, and a top-up would quietly turn a refused blast into a sent one.
    "aborted": ("This campaign was stopped before it sent and cannot be topped up. "
                "Create a new campaign for these contacts."),
    "failed": ("This campaign failed and cannot be topped up. Create a new campaign "
               "for these contacts."),
}

NOTHING_NEW = (
    "Nobody has been added to this campaign's list since it went out, so there is "
    "nothing to send. Upload the new contacts to that list, or add them one at a "
    "time from Contacts, and top up again."
)

# Deliberately not "everyone here has already been through this campaign". That
# reads as a statement about the people, and it is false whenever some of them
# were held back by the window and never texted at all — which is the state a
# client is most likely to be in when he reaches for this button. It is a
# statement about the *list*, so it says so.

NOT_A_LIST_AUDIENCE = (
    "This campaign was sent to a saved audience rather than to a list uploaded for "
    "it, so there is no list to add anybody to. Topping it up would text everyone "
    "who has joined that audience since — including people imported for a different "
    "auction. Create a new campaign for the new contacts instead."
)

ALL_SUPPRESSED = (
    "Every contact added since this campaign sent was texted recently and is being "
    "held back, so a top-up would reach nobody right now. Nothing was queued."
)


def already_reached(db: Session, campaign_id: int) -> set:
    """Every phone number this campaign already has a row for, in any status.

    By number, not by contact id — see the module docstring. `pending` counts
    too: a row waiting to go out is one the campaign is about to reach, and
    queueing a second copy of the same message to it is the duplicate this guard
    exists to prevent.
    """
    return {phone for (phone,) in
            db.query(SMSMessage.phone).filter(SMSMessage.campaign_id == campaign_id).all()}


def campaign_list_id(campaign: Campaign) -> Optional[int]:
    """The id of the list this campaign was sent to, or None if it was not one.

    A single `list:<id>` selector and nothing else. An intersection like
    `category:food_service&list:12` is deliberately excluded: the campaign went to
    part of that list, and "added to the list since" is not the same set as
    "added to the list since *and* in that category", so a top-up over it would
    reach people the original send filtered out.
    """
    selector = (campaign.audience or "").strip()
    if "&" in selector or "," in selector or not selector.startswith("list:"):
        return None
    try:
        return int(selector.split(":", 1)[1])
    except ValueError:
        return None


def _added_since(db: Session, list_id: int, since: Optional[str]) -> List:
    """Contacts put on this list after `since`. The definition of "added since".

    Timestamps are parsed rather than compared as strings. Both spellings are in
    this column — `import_service` writes `datetime.now().isoformat()` and
    `ContactListMember.added_at`'s server default writes SQLite's
    `CURRENT_TIMESTAMP`, which uses a space where isoformat uses a `T`. A
    lexicographic comparison across the two is wrong in a way that always errs
    the same direction (`T` > ` `), so every hand-added contact would read as
    newer than every imported one. `fromisoformat` accepts both.

    A row with a missing or unparseable `added_at` is treated as **not** added
    since. That is the safe direction: the cost is a contact he has to add again,
    against texting somebody the campaign was never meant to reach.
    """
    if not since:
        return []
    try:
        cutoff = datetime.fromisoformat(since)
    except (TypeError, ValueError):
        logger.warning("campaign created_at %r is unparseable; no top-up candidates",
                       since)
        return []

    rows = (db.query(Contact, ContactListMember.added_at)
            .join(ContactListMember, ContactListMember.contact_id == Contact.id)
            .filter(ContactListMember.list_id == list_id, Contact.is_active == 1)
            .order_by(Contact.id)
            .all())

    fresh = []
    for contact, added_at in rows:
        if not added_at:
            continue
        try:
            if datetime.fromisoformat(added_at) > cutoff:
                fresh.append(contact)
        except (TypeError, ValueError):
            continue
    return fresh


def new_recipients(db: Session, campaign: Campaign) -> Tuple[List, List]:
    """(sendable, held back) among contacts added to this campaign's list since.

    Suppression is applied here rather than left to the send loop for the same
    reason `create_campaign()` applies it at build time: the count has to be
    visible before anything is queued, and a held-back contact gets a `skipped`
    row so the number is on the record rather than inferable from a gap.
    """
    list_id = campaign_list_id(campaign)
    if list_id is None:
        return [], []

    reached = already_reached(db, campaign.id)
    fresh = [c for c in _added_since(db, list_id, campaign.created_at)
             if c.phone not in reached]
    return suppression_service.partition_recent(db, fresh)


async def assess(db: Session, campaign: Campaign) -> dict:
    """May this campaign be topped up right now, and with whom? Writes nothing.

    One function so the HTTP layer and the send path cannot reach different
    verdicts. The router calls it to refuse *before* queueing a background task,
    and `top_up()` calls it again on the path that does the work — an additional
    layer, not a replacement, exactly as `POST /{id}/send` and `preflight()`
    stand in relation to each other.

    `refusal` is None when the top-up may proceed. `code` is the HTTP status the
    router should use, kept here so the wording and the status stay together:
    409 for "the campaign is not in a state for this", 400 for a selector that no
    longer resolves.
    """
    empty = {"code": 409, "sendable": [], "suppressed": [], "bodies": [],
             "segments": 0}

    if campaign.status != "completed":
        return {**empty, "refusal": TOP_UP_STATE_ERRORS.get(
            campaign.status,
            f"This campaign is {campaign.status} and cannot be topped up.")}

    # Before anything is resolved: a campaign without a list of its own has no
    # "added since" to compute, and guessing one is how a top-up reaches an
    # audience nobody chose. See the module docstring.
    if campaign_list_id(campaign) is None:
        return {**empty, "refusal": NOT_A_LIST_AUDIENCE}

    service = CampaignService(db)
    sendable, suppressed = new_recipients(db, campaign)
    if not sendable:
        return {"refusal": ALL_SUPPRESSED if suppressed else NOTHING_NEW,
                "code": 409, "sendable": [], "suppressed": suppressed,
                "bodies": [], "segments": 0}

    # Rendered once, up front: the bodies are needed for the capacity estimate
    # and again for the rows, and rendering twice risks measuring one message and
    # queueing another.
    bodies = [(c, service.render(campaign.message_template, c)) for c in sendable]
    segments = sum(count_segments(body) for _, body in bodies)

    # The same two refusals a first send gets, in the same order, from the same
    # function — but denominated in what *this* run will queue. Charging five
    # messages against a 6,857-message campaign's original estimate would refuse
    # a top-up on the grounds that the campaign it belongs to was expensive.
    ok, detail = await service.preflight(campaign, segments=segments,
                                         cost=wholesale_estimate(segments))
    return {"refusal": None if ok else detail, "code": 409,
            "sendable": sendable, "suppressed": suppressed,
            "bodies": bodies, "segments": segments}


async def top_up(db: Session, campaign_id: int) -> Campaign:
    """Queue and send this campaign's message to everyone new in its audience.

    Raises `CampaignError` — before writing anything — when the campaign is in
    the wrong state, when there is nobody new, or when either pre-flight check
    refuses. The campaign is left exactly as it was in all three cases.
    """
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        raise CampaignError(f"Campaign {campaign_id} not found")

    verdict = await assess(db, campaign)
    if verdict["refusal"]:
        logger.error(f"Campaign #{campaign_id} top-up REFUSED: {verdict['refusal']}")
        raise CampaignError(verdict["refusal"])

    service = CampaignService(db)
    sendable, suppressed = verdict["sendable"], verdict["suppressed"]
    bodies, segments = verdict["bodies"], verdict["segments"]

    stamp = datetime.now().isoformat()
    held_back = suppression_service.suppression_reason(db)
    for contact, body in bodies:
        db.add(SMSMessage(
            campaign_id=campaign.id, contact_id=contact.id, phone=contact.phone,
            message=body, status="pending", top_up_at=stamp,
        ))
    for contact in suppressed:
        db.add(SMSMessage(
            campaign_id=campaign.id, contact_id=contact.id, phone=contact.phone,
            message=service.render(campaign.message_template, contact),
            status="skipped", error_message=held_back, top_up_at=stamp,
        ))

    # The campaign's totals move, which is what "fold into that campaign" means.
    # `estimated_segments` accumulates for the same reason: it is the campaign's
    # running estimate of what it cost, and leaving it at the first send's figure
    # would under-state a campaign that has been topped up four times.
    campaign.total_recipients = (campaign.total_recipients or 0) + len(sendable)
    campaign.suppressed_count = (campaign.suppressed_count or 0) + len(suppressed)
    campaign.skipped_count = (campaign.skipped_count or 0) + len(suppressed)
    campaign.estimated_segments = (campaign.estimated_segments or 0) + segments
    campaign.estimated_cost = wholesale_estimate(campaign.estimated_segments)
    db.commit()

    logger.info(
        f"Campaign #{campaign_id} top-up: {len(sendable)} new recipient(s), "
        f"{len(suppressed)} held back, ~{segments} segments"
    )
    return await service.run_send_loop(campaign, top_up=True,
                                       suppressed_this_run=len(suppressed))


async def top_up_background(campaign_id: int) -> None:
    """Entry point for BackgroundTasks — owns its own DB session.

    Same shape and the same reason as `campaign_dispatch.send_campaign_background`:
    a request-scoped session is closed the moment the response is returned. The
    refusals have already run synchronously by the time this is queued, so what
    reaches here is a top-up that passed pre-flight.
    """
    from app.core.database import SessionLocal
    db = SessionLocal()
    try:
        await top_up(db, campaign_id)
    except Exception as e:
        logger.error(f"Campaign {campaign_id} background top-up failed: {e}")
    finally:
        db.close()


def top_up_history(db: Session, campaign_ids: List[int]) -> dict:
    """{campaign_id: [{added_at, recipients}, …]} — "1,200 + 5 added 26 Aug".

    Counted in the database rather than in the page. A screen that tallied this
    from a capped list of message rows would under-report the day a campaign
    outgrew the cap, and it would under-report the *original* send, which is the
    direction nobody sanity-checks.

    One grouped query for the whole page, not one per campaign: the campaign rail
    renders up to fifty rows and a per-row lookup here would leave every other
    assertion in the suite passing while the list quietly became fifty queries.
    The original send (`top_up_at IS NULL`) is excluded — it is the campaign's own
    `total_recipients` minus these, and returning it as a "top-up" would have
    every caller filter it back out.
    """
    from sqlalchemy import func

    if not campaign_ids:
        return {}

    rows = (db.query(SMSMessage.campaign_id, SMSMessage.top_up_at,
                     func.count(SMSMessage.id))
            .filter(SMSMessage.campaign_id.in_(campaign_ids),
                    SMSMessage.top_up_at.isnot(None))
            .group_by(SMSMessage.campaign_id, SMSMessage.top_up_at)
            .order_by(SMSMessage.campaign_id, SMSMessage.top_up_at)
            .all())

    history = {campaign_id: [] for campaign_id in campaign_ids}
    for campaign_id, stamp, count in rows:
        history.setdefault(campaign_id, []).append(
            {"added_at": stamp, "recipients": count})
    return history
