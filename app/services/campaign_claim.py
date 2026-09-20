"""The one transition to `running`, and on a first send it is a claim.

Session 5n. `run_send_loop()` used to flip `campaign.status = "running"` through
the ORM and commit — an unconditional `UPDATE … WHERE id = ?`, written some
time after `send_campaign()` read the row as a draft. Between that read and
that write sits the pre-flight, which awaits the provider's balance call. A
cancel (`campaign_dispatch.cancel_scheduled()`) that lands in that gap clears
`scheduled_at`, is told it succeeded, and the flip then goes ahead on a
campaign the client has just stopped: the blast sends, and the rail says
"cancelled" over it.

Whether anything *can* land in that gap depends on three things none of which
is this module's to control: whether the cancel route runs on the event loop
or in FastAPI's threadpool, whether the provider's calls block the loop or
truly await, and how many workers uvicorn runs. The 5n review measured all
four provider/handler arrangements and found the window open in three of
them — including a `def` cancel route with the deployed provider, because
`urllib` releases the GIL and a threadpool thread runs in the meantime. And a
`def` route is exactly the edit this project's own 5j lesson ("sync SQLAlchemy
on an `async def` route blocks the loop") pushes the next session toward.

So the flip is made **conditional at the database** instead of resting on the
loop: `UPDATE campaigns SET status = 'running', started_at = ? WHERE id = ?
AND status = 'draft' AND scheduled_at IS <exactly what the caller loaded>`.
One row matched means this run owns the send. Zero means the row changed
under it — a cancel cleared the schedule, or another path already took it —
and the run stands down having written nothing, leaving the row exactly as
whoever won left it. The cancel's own clear is conditional the same way
(`status = 'draft' AND scheduled_at IS NOT NULL`), so between the two
statements the database decides the race, whichever thread, loop or worker
either arrives on. This also closes the button path's double-click for the
same reason: two background tasks that both read `draft` cannot both match.

A top-up is not a claim. It runs on a `completed` campaign whose state checks
live in `campaign_topup.assess()`, and the flip there is the plain one it
always was — kept in this module only so that the transition to `running` has
one address.
"""

from datetime import datetime

from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.services.campaign_builder import CampaignError


class SendClaimLost(CampaignError):
    """The campaign changed between being read as a draft and being started.

    Raised before anything is sent, with the row untouched by this run. The
    scheduler logs it as a stand-down rather than a failure, and does not
    count the campaign as dispatched.
    """


def take(db: Session, campaign: Campaign, *, top_up: bool = False) -> None:
    """Move `campaign` to `running`, atomically on a first send.

    `campaign` is the instance the caller loaded and decided to send; its
    `scheduled_at` as loaded is part of the claim's predicate, which is what
    makes a cancel in the meantime lose the race here rather than win it. On
    success the instance is refreshed from the row. On a lost claim
    `SendClaimLost` is raised and nothing has been written.
    """
    if top_up:
        campaign.status = "running"
        db.commit()
        return

    loaded_schedule = campaign.scheduled_at
    predicate = (Campaign.scheduled_at.is_(None) if loaded_schedule is None
                 else Campaign.scheduled_at == loaded_schedule)
    taken = (db.query(Campaign)
             .filter(Campaign.id == campaign.id,
                     Campaign.status == "draft",
                     predicate)
             .update({"status": "running",
                      "started_at": datetime.now().isoformat()},
                     synchronize_session=False))
    db.commit()
    db.refresh(campaign)
    if taken != 1:
        raise SendClaimLost(
            f"Campaign {campaign.id} changed before sending started "
            f"(now {campaign.status}, scheduled_at={campaign.scheduled_at!r}); "
            f"nothing was sent")
