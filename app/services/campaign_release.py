"""Releasing a hold a campaign froze: which `held_back` rows may go out today.

Split out of `campaign_topup.py` when 5h pushed that file past the 500-line
rule. The seam is *who may be released* against *running a top-up*, which is the
same seam `campaign_builder.py` sits on one module along — deciding who a send
is for, versus sending it. Nothing here writes anything; the flip itself belongs
with the rows a top-up queues, because a partial write is the one outcome that
module exists to prevent.

## What a held-back row is

`campaign_builder.create_campaign()` resolves the audience, applies the
recent-contact suppression window, and writes a real `sms_messages` row for
every contact the window holds back. That row is what puts "1,204 will receive
this, 37 held back" on screen before the send rather than leaving it inferable
from a gap afterwards, and it is correct.

Until session 5h it was written `status="skipped"` — the status whose contract
in `app/models/sms_message.py` says "wrong region", and which is permanent. One
column, two meanings, and nothing downstream could tell them apart: so a hold
expired and no code could act on it, and a buyer the window merely *deferred*
was unreachable inside that campaign for good. The client's only remedy was to
rebuild the campaign and lose the first send's numbers, which is exactly what
the top-up exists to avoid.

## The four rules, from decision 005's riders

  1. **Only a contact whose sole row on this campaign is `held_back`.** A phone
     that also carries `sent`, `failed`, `blocked` or `skipped` has been dealt
     with — reached, or refused for a reason the suppression window knows
     nothing about — and a cleared hold does not reopen either.
  2. **Re-adjudicated against today's window, not the campaign's.** The hold is
     a property of now. Re-using the build-time verdict would answer the
     question with the answer that caused the defect.
  3. **Rows written `skipped` before 5h are invisible here.** They may mean
     either thing and cannot be classified after the fact. That is the
     no-backfill rule working, not an omission —
     `alembic/versions/e2a7c3d15b48_held_back_message_status.py` states it.
  4. **One row per phone.** The flip is a flip, never a second row.

## Why this does not need a `list:` audience

`campaign_topup.NOT_A_LIST_AUDIENCE` refuses a top-up on `all` or `category:`
because "everyone who has joined that audience since" is a set nobody chose —
last month's memorabilia message to tonight's restaurant buyers. A held-back row
is the opposite of that: this campaign resolved that contact itself, counted
them, and showed the number on screen before the send. Refusing to release them
because the audience was not an upload would leave decision 005's defect
standing on every campaign that was not built from one.
"""

import logging
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.contact import Contact
from app.models.sms_message import SMSMessage, HELD_BACK_STATUS
from app.services import suppression_service

logger = logging.getLogger("campaign")


def held_back_rows(db: Session, campaign_id: int) -> List[SMSMessage]:
    """Rows this campaign deferred and has reached no other way.

    "Whose only row for this campaign is `held_back`" — decision 005's own
    wording. The exclusion is by *phone* rather than by contact id, for the
    reason `campaign_topup.already_reached()` gives: a contact deleted and
    re-imported is a new row and the same person holding the same handset.

    One row per phone. Nothing today can write a second `held_back` row for one
    number on one campaign — `already_reached()` sees the first — but a release
    that queued both would be two copies of one message to one handset, which is
    the single outcome the top-up exists to prevent, so it does not depend on
    that remaining true.
    """
    reached_otherwise = {phone for (phone,) in db.query(SMSMessage.phone).filter(
        SMSMessage.campaign_id == campaign_id,
        SMSMessage.status != HELD_BACK_STATUS).all()}

    rows, seen = [], set()
    for row in (db.query(SMSMessage)
                .filter(SMSMessage.campaign_id == campaign_id,
                        SMSMessage.status == HELD_BACK_STATUS)
                .order_by(SMSMessage.id).all()):
        if row.phone in reached_otherwise or row.phone in seen:
            continue
        seen.add(row.phone)
        rows.append(row)
    return rows


def capped_campaign_hold(cap: int, held: int, clears_at: Optional[str] = None) -> str:
    """Why a capped campaign's held-back contacts stay held, with both numbers.

    Here rather than beside the other refusals in `campaign_topup`, because the
    rule it explains is the one above it: this module decides that a capped
    campaign releases nobody, and the layer that owns a decision owns the
    client-safe sentence about it — `send_mode()` in `app/sms/factory.py` is the
    pattern. The 500-line rule asked the question and this was the answer.

    Decision 006 upheld the refusal and rejected the silence: "refusing without
    explaining is how a correct guard reads as a broken tool". So the cap is
    named, because it is the thing he typed and the reason this is happening,
    and the count is named, because 6,806 and 6 call for very different next
    moves and he would otherwise have to go and count them.

    **And the clearing time is named when there is one**, which is a correction
    from this session's review. The remedy — build a campaign without a cap —
    reaches nobody at all while the window still holds these people: the new
    campaign holds them too. Saying "create a new campaign" and nothing else is
    the same defect 006 rejected next door, a remedy that cannot be executed,
    and the earlier version of this function argued its way into it by claiming
    these contacts "are not waiting on the window" — which its own sentence
    contradicts three clauses earlier.
    """
    when = (f" The hold-back window clears at "
            f"{suppression_service.clears_at_clock(clears_at)}, and a new campaign "
            f"built before then holds them again."
            if clears_at else "")
    return (
        f"This campaign was capped at {cap:,}, so it went to the first batch only. "
        f"{held:,} contact{'' if held == 1 else 's'} the hold-back window held back "
        f"{'is' if held == 1 else 'are'} still on it, and a top-up does not send to "
        f"them — that would reach everyone the window held, which is not the number "
        f"you capped this at.{when} Create a new campaign without a cap to reach them."
    )


def hold_facts(db: Session, campaign_id: int) -> Dict[str, object]:
    """The two things a sentence about this campaign's hold needs to be true.

    The window as it stands *now* and the moment the last hold on this campaign
    lifts, in the shape `campaign_outcome.zero_send_reason()` takes them. One
    function because they are one fact in two halves and a caller that fetched
    only one would produce a sentence describing a window it had not read.

    Here rather than in the send loop so that module keeps one job, and so the
    only code that knows what a held-back row is stays in one file.

    **Returns `{}` rather than raising**, for `hold_clears_at()`'s reason and
    with a wider net than it: this runs between the send loop finishing and the
    campaign's status being written, and `suppression_days()` reads a settings
    row of its own, *outside* that function's guard. Guarding only the half that
    looked expensive would have left the campaign strandable in `running` by the
    cheaper half — which is the shape of the defect this whole guard exists for.
    An empty dict is the documented degraded path: the sentence loses both
    clauses and keeps its cause and its remedy.
    """
    try:
        return {
            "suppression_days": suppression_service.suppression_days(db),
            "clears_at": hold_clears_at(db, campaign_id),
        }
    except Exception:
        logger.exception("Campaign #%s: could not read the hold's state; the abort "
                         "reason will name neither the window nor a clearing time",
                         campaign_id)
        return {}


def _contacts_by_phone(db: Session, phones: List[str]) -> Dict[str, Contact]:
    """The live contact behind each of these numbers, keyed by number.

    By phone rather than by `SMSMessage.contact_id` throughout this module: the
    handset is the identity, and a buyer deleted and re-imported between two
    sends is a new contact row holding the same one. A number with no active
    contact is simply absent from the map, and every caller has to decide what
    that means for itself — it is never the same answer as "held by the window".
    """
    return {c.phone: c for c in db.query(Contact).filter(
        Contact.phone.in_(phones), Contact.is_active == 1).all()}


def hold_clears_at(db: Session, campaign_id: int) -> Optional[str]:
    """When the last of this campaign's held-back contacts becomes sendable.

    Decision 006: a campaign the window held *entirely* aborts, and the sentence
    it aborts with has to say when the client can try again. That answer is
    `max(last_messaged_at)` across the held set plus today's window, which is
    exactly what `suppression_service.suppression_clears_at()` computes for the
    composer one screen earlier — so it is the same function, not a second one
    that agrees most of the time.

    Returns None when there is nothing honest to say: no held rows, a window of
    0, contacts that no longer exist, or timestamps that will not parse. The
    caller must then omit the clause rather than guess, because a wrong clearing
    time is worse than none — it is a date the client plans an auction around.

    **It also returns None rather than raising, and that is load-bearing.** Its
    caller is the adjudication between the send loop finishing and the campaign's
    final status being written, which was pure arithmetic over counters until
    decision 006 needed a timestamp. An exception there leaves the campaign
    `running` for ever — the one state the rail cannot explain, and a strictly
    worse outcome than a sentence missing a clause. Same shape and the same
    reasoning as `monitoring_service.active_config_alerts()`, which `/health`
    calls without `Depends(get_db)` so a database it cannot read does not take
    the endpoint down. The exception goes to the log, where it is ours.
    """
    try:
        rows = held_back_rows(db, campaign_id)
        if not rows:
            return None
        contacts = _contacts_by_phone(db, [row.phone for row in rows])
        return suppression_service.suppression_clears_at(
            list(contacts.values()), suppression_service.suppression_days(db))
    except Exception:
        logger.exception("Campaign #%s: could not compute a clearing time; the "
                         "abort reason will omit it", campaign_id)
        return None


def releasable(db: Session, campaign: Campaign) -> Tuple[List[SMSMessage],
                                                         List[SMSMessage],
                                                         List[SMSMessage]]:
    """(release now, still inside the window, no longer on the list).

    Rider 4: the window is re-run against *today's* value, and through
    `suppression_service.partition_recent()` rather than a second comparison
    written here. The rule that decides whether a real person gets a text they
    did not ask for has one implementation, and this is a caller of it.

    Adjudicated on the **contact matching the row's phone**, not on
    `row.contact_id`: `last_messaged_at` lives on the contact, and the phone is
    the identity everything else in this flow keys on.

    **Three buckets, not two.** A held-back row whose phone has no active
    contact is not being held by the window — the person was deleted, most
    likely by an import undo, which `import_service` does routinely. Folding
    those into "still held" made the refusal say they had been "texted recently
    and are being held back", which is a statement about a rule that is not
    running: the same defect as a degraded provider reporting a chosen dry run,
    one level down. They are their own bucket, they are never released, and no
    sentence claims the window is why.
    """
    rows = held_back_rows(db, campaign.id)
    if not rows:
        return [], [], []

    # A campaign the client capped is a campaign he asked to reach a fixed
    # number of people. The cap was applied to the sendable set at build time
    # and never to the held-back rows, which are written for *every* suppressed
    # contact — so releasing them would deliver to a number he never chose, and
    # on this client's list "capped at 50, 6,000 held back" is the ordinary
    # shape rather than a corner. Nothing is released, which is exactly what
    # happened before 5h; the question of what such a campaign *should* do is
    # decisions/006, and it is open. This is the absence of a change, not a
    # ruling on it.
    if campaign.batch_size and campaign.batch_size > 0:
        logger.info("Campaign #%s was capped at %s, so its %s held-back row(s) "
                    "are not released — see decisions/006",
                    campaign.id, campaign.batch_size, len(rows))
        return [], [], []

    phones = [row.phone for row in rows]
    contacts = _contacts_by_phone(db, phones)

    sendable, _ = suppression_service.partition_recent(
        db, [contacts[p] for p in phones if p in contacts])
    clear = {c.phone for c in sendable}

    release = [row for row in rows if row.phone in clear]
    still_held = [row for row in rows
                  if row.phone not in clear and row.phone in contacts]
    departed = [row for row in rows if row.phone not in contacts]

    if release or departed:
        logger.info("Campaign #%s: %s held-back row(s) clear the %s-day window, "
                    "%s still held, %s no longer on the list",
                    campaign.id, len(release),
                    suppression_service.suppression_days(db),
                    len(still_held), len(departed))
    return release, still_held, departed
