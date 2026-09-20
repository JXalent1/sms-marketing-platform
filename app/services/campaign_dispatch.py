"""The two ways a campaign starts that are not a direct call — and the one way a
scheduled start is called off.

Split out of `campaign_service.py` when session 5d pushed that file past the
500-line rule. The boundary is *when a send begins*, not *how it runs*: nothing
here decides anything about a campaign, and both entry points hand the work to
the same `CampaignService.send_campaign()` a button press reaches. That is
deliberate and load-bearing — a scheduled blast gets the send-path refusal, the
capacity pre-flight, the blocklist, the region filter and recent-contact
suppression exactly as an on-demand one does. Scheduling decides *when*, never
*whether*, and nothing in this file may become a second, thinner send path.

`cancel_scheduled()` (5n) is the same boundary from the other side: it removes
the *when* and touches nothing else. It lives here rather than in
`campaign_service` because the only thing that makes cancelling hard is the
scheduler — the race in `run_due_campaigns()` below — and the two halves of that
race belong in one file.

Both entry points own their own DB session. A request-scoped session is closed
the moment the HTTP response is returned, and an APScheduler job has no request
to borrow one from.

**Nothing here meters usage, and nothing here may.** Session B1 reported a
campaign's segments to the billing meter as the last thing each entry point
did, when every row was still `sent`; the delivery webhook then moved rows out
of the billable set and the figure could never be corrected (`decisions/011`).
B1b moved metering to a scheduled pass in `app/services/stripe_meter.py` that
reports rows once they have *settled* and marks them in the database. The rows
this file's sends write are unmarked, so the next pass takes them — there is no
hook to call, and adding one back would make the send path a second place a
segment can be metered, which is the defect `tests/test_metering_pass.py`
counts to exactly one.
"""

from datetime import datetime
from typing import List, Optional

from sqlalchemy.orm import Session

from app.core import clock
from app.models.campaign import Campaign
from app.services.campaign_claim import SendClaimLost
from app.services.campaign_service import CampaignError, CampaignService
import logging

logger = logging.getLogger("campaign")


async def send_campaign_background(campaign_id: int):
    """Entry point for BackgroundTasks — owns its own DB session."""
    from app.core.database import SessionLocal
    db = SessionLocal()
    try:
        await CampaignService(db).send_campaign(campaign_id)
    except Exception as e:
        logger.error(f"Campaign {campaign_id} background send failed: {e}")
    finally:
        db.close()


# ─── Scheduled send ─────────────────────────────────────────────────────────

def due_campaign_ids(db: Session, now: Optional[datetime] = None) -> List[int]:
    """Drafts whose scheduled time has arrived, in the client's zone.

    Drafts only. A campaign that is already running, completed or aborted is not
    due however old its timestamp is, and filtering on status is what stops the
    minute-by-minute tick from dispatching the same campaign twice. It is also
    what makes the repeated hour on the first Sunday in November safe: 1:30 AM
    Eastern happens twice, the campaign is dispatched on the first one, and by
    the second it is no longer a draft.

    **`clock.now()`, never `datetime.now()`.** `scheduled_at` is the wall clock
    the operator typed, in `APP_TIMEZONE`; the droplet's own clock is UTC, so
    comparing against the box's idea of now dispatched a 6:00 PM Eastern
    campaign at 2:00 PM. `clock.now()` asks `zoneinfo` and is therefore right
    whatever the box is set to, and right in both EDT and EST — a fixed −4 is
    wrong for four months of the year.

    `now` may be passed as an aware instant, which is how the tests assert the
    zone rather than the machine they run on; `clock.wall_clock()` converts it.

    Comparison is lexicographic on ISO strings, which is chronological while
    every value carries one format and one zone. Both sides now do: this is the
    only producer of the right-hand side, and `scheduled_at` gains no offset —
    see `app/core/clock.py` for why it stays wall clock.
    """
    cutoff = clock.wall_clock(now).isoformat()
    rows = (db.query(Campaign.id)
            .filter(Campaign.status == "draft",
                    Campaign.scheduled_at.isnot(None),
                    Campaign.scheduled_at <= cutoff)
            .order_by(Campaign.scheduled_at)
            .all())
    return [row_id for (row_id,) in rows]


def still_scheduled(db: Session, campaign_id: int) -> bool:
    """Is this campaign, right now, still the scheduled draft it was selected as?

    `due_campaign_ids()` answers about a *moment*, and `run_due_campaigns()`
    then works through its answer one campaign at a time — and a campaign at
    the back of the queue waits behind every blast in front of it, minutes at
    this client's list sizes, with the send loop yielding after every message.
    A cancel that lands in that gap clears `scheduled_at` on a campaign the
    tick has already decided to send, and `send_campaign()` cannot tell: it
    checks `status`, which a cancel does not touch, because cancelling leaves an
    editable draft on purpose. Measured on the pre-fix tree: selected, then
    cancelled, then dispatched anyway — `completed`, one row `sent`.

    So the two conditions a cancel can change are asked again at the last
    moment before dispatch, against the row as it stands: still a draft, still
    carrying a schedule. **Not the time.** Selection already judged that, and
    between selection and dispatch the only thing that can move
    `scheduled_at` is a cancel, which clears it; re-comparing the wall clock
    would add nothing except a once-a-year wrong answer — a campaign selected
    in the first 1 AM hour on the first Sunday in November and reached after
    the clocks fall back reads as "not yet due" again, and would be skipped
    with a log line blaming a cancel that never happened. (The 5n review.)

    A column query rather than `db.get()` so the answer is the row's whatever
    this session happens to hold — not a guard in its own right: at the one
    call site nothing has loaded the campaign before this runs and every send
    commits, so the identity map cannot be stale there today. It is the
    cheaper read, and the one whose correctness does not depend on that
    staying true.

    This is not the last word. From here to the `running` commit sits the
    pre-flight, which awaits the provider — and whether a cancel can land in
    that gap depends on the cancel route being on the loop, the provider
    blocking it, and uvicorn running one worker, none of which this function
    controls. The 5n review measured the gap open in three of four
    provider/handler arrangements. So the flip itself is the last check:
    `campaign_claim.take()` moves a first send to `running` with a conditional
    UPDATE that requires the row to still be the draft that was loaded, with
    the same `scheduled_at`, and stands down if it is not. This function's job
    is the common case — a cancel that landed while an earlier campaign was
    sending — answered cheaply and logged plainly, so a stood-down campaign
    shows up in the scheduler's log by name rather than as a lost claim.
    """
    row = (db.query(Campaign.status, Campaign.scheduled_at)
           .filter(Campaign.id == campaign_id)
           .first())
    if row is None:
        return False
    status, scheduled_at = row
    return status == "draft" and scheduled_at is not None


async def run_due_campaigns() -> List[int]:
    """Send every campaign whose time has come. Returns the ids actually dispatched.

    A campaign selected and then cancelled before its turn is skipped, logged,
    and left as the draft the cancel made it — it is not in the returned list.
    """
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        campaign_ids = due_campaign_ids(db)
        if not campaign_ids:
            return []
        logger.info(f"Scheduler: {len(campaign_ids)} campaign(s) due — {campaign_ids}")
        service = CampaignService(db)
        dispatched: List[int] = []
        for campaign_id in campaign_ids:
            # Re-asked here, not only at selection — see `still_scheduled()`.
            if not still_scheduled(db, campaign_id):
                logger.info(f"Scheduler: campaign {campaign_id} was cancelled or "
                            f"taken after selection; not sent")
                continue
            try:
                await service.send_campaign(campaign_id)
                dispatched.append(campaign_id)
            except SendClaimLost as e:
                # Cancelled during its own pre-flight, or taken by another
                # path: nothing sent, nothing written, and not a failure.
                logger.info(f"Scheduler: campaign {campaign_id} stood down — {e}")
            except Exception as e:
                # One bad campaign must not stop the rest of tonight's schedule.
                logger.error(f"Scheduled campaign {campaign_id} failed: {e}")
        return dispatched
    finally:
        db.close()


# ─── Cancelling a scheduled send ────────────────────────────────────────────

# Why a scheduled campaign cannot be cancelled, by the state it is in. Each
# sentence carries what stopped it in his units and what he can do instead
# (decisions/006) — a refusal that explains nothing reads as a broken tool, and
# "cancel" is the button he reaches for when he already thinks something is
# wrong. `running` is the one that matters: a send that has started is not
# stopped by this, and the sentence must not let him believe it was.
CANCEL_STATE_ERRORS = {
    "running": ("This campaign is already sending — {sent:,} of {total:,} sent so "
                "far — and a send that has started cannot be stopped. Anyone it "
                "has not reached yet will still be texted."),
    "completed": ("This campaign has already been sent ({sent:,} sent), so there "
                  "is nothing to cancel. Its report is under History."),
    "aborted": ("This campaign was stopped before it sent, so there is nothing to "
                "cancel. The reason is shown on the campaign; create it again to "
                "send it."),
    "failed": ("This campaign failed and was not sent, so there is nothing to "
               "cancel. Create it again to send it."),
}

NOT_SCHEDULED = ("This campaign is not scheduled — it is a draft you send by hand "
                 "from the rail — so there is nothing to cancel.")


def cancel_refusal(campaign: Campaign) -> Optional[str]:
    """The sentence that stops a cancel, or None when it may go ahead."""
    if campaign.status != "draft":
        wording = CANCEL_STATE_ERRORS.get(
            campaign.status, "This campaign is {status} and cannot be cancelled.")
        return wording.format(status=campaign.status,
                              sent=campaign.sent_count or 0,
                              total=campaign.total_recipients or 0)
    if campaign.scheduled_at is None:
        return NOT_SCHEDULED
    return None


def cancel_scheduled(db: Session, campaign_id: int) -> Campaign:
    """Call off a scheduled send. The campaign stays; only its time goes.

    **What cancelling means, decided here and nowhere else:** `scheduled_at` is
    cleared and the campaign is left an ordinary **draft** — its name, its
    audience, its message and every `pending` row it was built with are
    untouched. It is not moved to a terminal state. The usual reason to cancel
    is a wrong time, and a draft can still be sent by hand from the rail with
    the same audience it was built for; an `aborted` campaign could not, and
    nothing in this codebase moves a campaign back from `aborted` (decision
    002's reason for refusing before queueing, and decision 006's for leaving a
    refused campaign a draft).

    The consequence he should know: the draft cannot be given a *new* time from
    here. Scheduling is chosen at creation, and editing a campaign in place is
    its own session — so "wrong day" is cancel, then create again with the
    right one. The cancelled draft is indistinguishable from one he never
    scheduled, which is the intended end state rather than a loss of record:
    the campaign never ran, and there is nothing about it to keep.

    **Refused, never forced, on anything that has started.** `running`,
    `completed`, `aborted` and `failed` each get their own sentence
    (`CANCEL_STATE_ERRORS`), as does a draft that was never scheduled.

    **The clear is conditional at the database**, not decided from the row this
    function read a moment ago: `UPDATE … WHERE status = 'draft' AND
    scheduled_at IS NOT NULL`. A dispatch that flipped the campaign to
    `running` between the read above and the write below leaves that update
    matching nothing, and the refusal is re-read from the row as it stands —
    so a cancel can lose the race and *say so*, and can never clear the
    schedule on a blast that is already going out. The other direction — a
    cancel that lands after the tick has chosen the campaign — is answered by
    `still_scheduled()` before dispatch and, for the gap after it, by the flip to
    `running` itself being a conditional claim (`campaign_claim.take()`): a
    cancel that clears the schedule during a campaign's own pre-flight makes
    that claim match nothing, and the send stands down. Between the two
    conditional statements the database decides, whatever thread or loop
    either arrives on.

    Raises `CampaignError` carrying the sentence; the router turns it into a
    409. Returns the campaign as it now stands.
    """
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise CampaignError(f"Campaign {campaign_id} not found")

    refusal = cancel_refusal(campaign)
    if refusal:
        raise CampaignError(refusal)

    cleared = (db.query(Campaign)
               .filter(Campaign.id == campaign_id,
                       Campaign.status == "draft",
                       Campaign.scheduled_at.isnot(None))
               .update({"scheduled_at": None}, synchronize_session=False))
    db.commit()
    db.refresh(campaign)
    if cleared != 1:
        # Somebody got there first — the scheduler, or a second click. Whatever
        # the row says now is the truth, and it is the sentence he gets.
        logger.warning(f"Campaign #{campaign_id} cancel lost the race: "
                       f"status={campaign.status} scheduled_at={campaign.scheduled_at!r}")
        raise CampaignError(cancel_refusal(campaign)
                            or "This campaign changed while you were cancelling it; "
                               "look at the rail and try again.")

    logger.info(f"Campaign #{campaign_id} cancelled: schedule cleared, left as a draft")
    return campaign
