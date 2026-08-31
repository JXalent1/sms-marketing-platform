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

**A top-up also releases the people the hold-back window held back** (5h A1,
decisions/005). Those two sets are reached by different routes and it matters
which is which. Somebody *added since* is new to the campaign and needs a row.
Somebody *held back* was in the campaign's audience from the start, was resolved
into it, and already has a row — the window simply deferred them, and until 5h
that deferral was written under the same status as "wrong region", which is
permanent. So the hold expired and nothing could act on it: the buyer stayed
unreachable inside that campaign for good, and the client's only remedy was to
rebuild the campaign and lose the first send's numbers.

Four properties, all from decision 005's riders:

  - the window is re-run **against today's value**, not the campaign's. A hold
    is a property of now.
  - the existing row is **flipped** to `pending`, never duplicated. One person
    held back once and reached later is one row with a history.
  - only a contact whose *only* row on this campaign is `held_back` is
    eligible. A phone that also has a `sent`, `failed` or `blocked` row was
    reached, or was refused for a reason the window knows nothing about.
  - rows written `skipped` before 5h are never touched. They may mean either
    thing and cannot be classified after the fact.

The release does **not** require a `list:` audience, and the "added since" half
still does. The refusal above exists because "everyone who has joined that
audience since" is a set nobody chose; a held-back row is the opposite of that —
it names a contact this campaign itself resolved, counted and showed on screen
before the send.

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
from app.models.sms_message import SMSMessage, HELD_BACK_STATUS
from app.services import link_service, suppression_service
from app.services.campaign_builder import (
    CampaignError, resolve_link_target, wholesale_estimate,
)
# `releasable()` and `held_back_rows()` moved to their own module when 5h pushed
# this file past the 500-line rule. The seam is *who may be released* against
# *running a top-up* — the same one campaign_builder sits on. Re-exported
# because the release rule is the interesting half of decision 005 and a reader
# following the top-up here should not have to guess where it went.
from app.services.campaign_release import (   # noqa: F401  (re-export)
    capped_campaign_hold, held_back_rows, hold_clears_at, releasable,
)
from app.services.campaign_service import CampaignService
# Re-exported: `top_up_history()` is a *reporting* query — the "1,200 + 5 added
# 26 Aug" line — rather than part of running a top-up, and 5f gave reporting its
# own module. It moved there when this file crossed the 500-line rule again;
# `routers/campaigns.py` reaches for it at this address and keeps doing so.
from app.services.report_service import top_up_history   # noqa: F401  (re-export)
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
    "Nobody has been added to this campaign's list since it went out, and nobody it "
    "held back is clear yet, so there is nothing to send. Upload the new contacts to "
    "that list, or add them one at a time from Contacts, and top up again."
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

# Covers both halves deliberately. Since 5h a top-up can be holding back somebody
# added since *and* somebody it held back at build time who is still inside the
# window, and a sentence naming only the first would be false about a campaign
# whose entire hold is the second.
ALL_SUPPRESSED = (
    "Everyone this top-up would reach was texted recently and is being held back, so "
    "it would reach nobody right now. Nothing was queued."
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
    visible before anything is queued, and a held-back contact gets a
    `held_back` row so the number is on the record rather than inferable from a
    gap.
    """
    list_id = campaign_list_id(campaign)
    if list_id is None:
        return [], []

    reached = already_reached(db, campaign.id)
    fresh = [c for c in _added_since(db, list_id, campaign.created_at)
             if c.phone not in reached]
    return suppression_service.partition_recent(db, fresh)


def top_up_summary(added: int, released: int) -> str:
    """What the client is told a top-up is about to do.

    One function so the endpoint and any later surface cannot describe the same
    run differently — the pattern `send_mode()` established. The two halves are
    named separately because they are different news: "3 new" is a list that
    grew, "2 held back earlier" is a hold that has expired, and a client reading
    the second as the first will go looking for an upload he did not make.
    """
    def people(n: int) -> str:
        return f"{n:,} contact{'' if n == 1 else 's'}"

    if added and released:
        return (f"Top-up sending to {people(added + released)}: {added:,} added since "
                f"this campaign went out, {released:,} held back earlier and now clear")
    if released:
        return (f"Top-up sending to {people(released)} this campaign held back "
                f"earlier, now that the hold has cleared")
    return f"Top-up sending to {people(added)} added since this campaign went out"


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

    Two candidate sets, and they are gathered under different rules — see the
    module docstring. `sendable` is people added to the campaign's list since it
    went out and needs a `list:` audience; `released` is rows this campaign
    itself held back and needs nothing, because those contacts were resolved into
    the campaign before it sent.
    """
    empty = {"code": 409, "sendable": [], "released": [], "suppressed": [],
             "still_held": [], "bodies": [], "segments": 0, "link_target": None}

    if campaign.status != "completed":
        return {**empty, "refusal": TOP_UP_STATE_ERRORS.get(
            campaign.status,
            f"This campaign is {campaign.status} and cannot be topped up.")}

    # Re-adjudicated against today's window, before the audience question below:
    # a held-back row belongs to this campaign whatever its audience selector
    # says, so a campaign on `all` can still release one.
    released, still_held, departed = releasable(db, campaign)

    # A campaign without a list of its own has no "added since" to compute, and
    # guessing one is how a top-up reaches an audience nobody chose. That is a
    # refusal only when there is nothing else to do — see below.
    is_list_audience = campaign_list_id(campaign) is not None
    sendable, suppressed = new_recipients(db, campaign) if is_list_audience else ([], [])

    if not sendable and not released:
        # Ordered by what the client can act on. The cap is a decision he made
        # and can undo by building the campaign again; the window is a setting;
        # the audience is neither. A row whose contact has left the list falls
        # through to the last two, which say there is nothing to send without
        # blaming a rule — see `releasable()`'s third bucket.
        held = held_back_rows(db, campaign.id) if campaign.batch_size else []
        if held:
            # The clearing time is read here and not inside the sentence, on the
            # refusal path only: it is three queries, and the other four refusals
            # have no use for it.
            refusal = capped_campaign_hold(campaign.batch_size, len(held),
                                           hold_clears_at(db, campaign.id))
        elif suppressed or still_held:
            refusal = ALL_SUPPRESSED
        elif not is_list_audience:
            refusal = NOT_A_LIST_AUDIENCE
        else:
            refusal = NOTHING_NEW
        return {**empty, "refusal": refusal, "suppressed": suppressed,
                "still_held": still_held}

    # A campaign carrying `{link}` needs a mintable destination for the new rows
    # too, and the refusal is the same sentence the composer showed — a short
    # domain that was removed, or a target that was cleared, must stop the
    # top-up rather than queue messages with a literal `{link}` in them.
    # Checked here, after the "is there anybody" question, so a campaign with
    # nothing to send still gets the refusal that tells him something useful.
    try:
        link_target = resolve_link_target(campaign.message_template,
                                          campaign.link_target_url)
    except CampaignError as e:
        return {**empty, "refusal": str(e), "code": 400}

    service = CampaignService(db)
    # Rendered once, up front: the bodies are needed for the capacity estimate
    # and again for the rows, and rendering twice risks measuring one message and
    # queueing another.
    #
    # The exception is `{link}`, which cannot be minted here — `assess()` writes
    # nothing, and it runs twice per top-up. It is rendered with
    # `placeholder_url()`, which is the same length as a real link *by
    # construction* rather than by coincidence (both are the domain plus
    # SLUG_LENGTH characters, and a test pins it), so the segment total this
    # capacity check is given is the one the queued messages will cost.
    #
    # A released row is *not* re-rendered. It already carries the body this
    # campaign composed for that contact, and that body is what will be sent —
    # measuring anything else here would quote one message and queue another,
    # which is the defect `exact_segment_totals()` exists to prevent one layer up.
    placeholder = link_service.placeholder_url() if link_target else None
    bodies = [(c, service.render(campaign.message_template, c, placeholder))
              for c in sendable]
    segments = (sum(count_segments(body) for _, body in bodies)
                + sum(count_segments(row.message or "") for row in released))

    # The same two refusals a first send gets, in the same order, from the same
    # function — but denominated in what *this* run will queue. Charging five
    # messages against a 6,857-message campaign's original estimate would refuse
    # a top-up on the grounds that the campaign it belongs to was expensive.
    ok, detail = await service.preflight(campaign, segments=segments,
                                         cost=wholesale_estimate(segments))
    return {"refusal": None if ok else detail, "code": 409,
            "sendable": sendable, "released": released,
            "suppressed": suppressed, "still_held": still_held,
            "bodies": bodies, "segments": segments, "link_target": link_target}


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
    released = verdict["released"]
    bodies, segments = verdict["bodies"], verdict["segments"]

    stamp = datetime.now().isoformat()
    held_back = suppression_service.suppression_reason(db)

    # One link each for the rows this run creates, held back or not — a
    # held-back row carries the body it will be sent with, and a later release
    # sends that body rather than re-rendering it. Released rows are NOT minted
    # for: they already carry their own link from the original send, and minting
    # a second would mean the message quoted on screen and the message queued
    # were different. This is the first write of the run, so a mint failure
    # raises before any message row exists.
    link_target = verdict["link_target"]
    links = {}
    if link_target and (sendable or suppressed):
        links = {contact.id: (link, link_service.url_for(link.slug))
                 for contact, link in link_service.mint(
                     db, campaign_id=campaign.id, target_url=link_target,
                     contacts=list(sendable) + list(suppressed))}

    queued = []
    for contact, body in bodies:
        link, url = links.get(contact.id, (None, None))
        # Re-rendered with the real link. The placeholder `assess()` measured is
        # the same length, so the capacity verdict above still describes this
        # message; what is queued is what is stored, which is the property
        # `exact_segment_totals()` exists to protect one layer up.
        message = SMSMessage(
            campaign_id=campaign.id, contact_id=contact.id, phone=contact.phone,
            message=service.render(campaign.message_template, contact, url) if url
            else body,
            status="pending", top_up_at=stamp,
        )
        db.add(message)
        queued.append((link, message))
    for contact in suppressed:
        link, url = links.get(contact.id, (None, None))
        message = SMSMessage(
            campaign_id=campaign.id, contact_id=contact.id, phone=contact.phone,
            message=service.render(campaign.message_template, contact, url),
            status=HELD_BACK_STATUS, error_message=held_back, top_up_at=stamp,
        )
        db.add(message)
        queued.append((link, message))

    if links:
        # Ids exist only after a flush; a link has to know its message so a
        # click can be dated against that message's send.
        db.flush()
        for link, message in queued:
            if link is not None:
                link.message_id = message.id

    # Decision 005 rider 3: flip the row, do not write a second one. A person
    # held back once and reached later is one row with a history, not two rows a
    # report has to reconcile — and two rows is also how the same handset gets
    # two copies of one message the day something reads them as separate
    # recipients. `error_message` is cleared because it explains a hold that is
    # over; the row is about to be sent, and a sent message carrying "held back
    # so nobody gets two messages in a row" is a lie in the client's own log.
    for row in released:
        row.status = "pending"
        row.error_message = None
        row.top_up_at = stamp

    # The campaign's totals move, which is what "fold into that campaign" means.
    # `estimated_segments` accumulates for the same reason: it is the campaign's
    # running estimate of what it cost, and leaving it at the first send's figure
    # would under-state a campaign that has been topped up four times.
    #
    # A released row is *subtracted* from the two hold counters as it is added to
    # the recipients: it was counted as held back at build time and it is not
    # held back any more. Leaving it in would have the rail read "6 recipients ·
    # 2 held back" about a campaign that reached all six.
    campaign.total_recipients = ((campaign.total_recipients or 0)
                                 + len(sendable) + len(released))
    campaign.suppressed_count = max(0, (campaign.suppressed_count or 0)
                                    - len(released)) + len(suppressed)
    campaign.skipped_count = max(0, (campaign.skipped_count or 0)
                                 - len(released)) + len(suppressed)
    campaign.estimated_segments = (campaign.estimated_segments or 0) + segments
    campaign.estimated_cost = wholesale_estimate(campaign.estimated_segments)
    db.commit()

    logger.info(
        f"Campaign #{campaign_id} top-up: {len(sendable)} new recipient(s), "
        f"{len(released)} released from the hold-back window, "
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
