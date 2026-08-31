"""The send loop, and the two refusals that can stop it.

This is the module you will modify most for a new client, and the one where
mistakes are expensive — every branch here is a decision about someone else's
money and someone else's phone.

Campaign *creation* moved to `campaign_builder.py` in 5e, when this file crossed
the 500-line rule for the third time. The seam is deciding what a campaign is
against running it; `create_campaign()` and `resolve_category()` remain here as
thin delegates so every existing caller, including the API and the suite, keeps
the same entry points.

Ordering in the send loop is deliberate. Filters that cost nothing run before
the ones that cost money:

    1. blocklist      free, and legally required
    2. region filter  free; these are guaranteed-undeliverable
    3. send path      free; a degraded box reaches no carrier, so nothing here
                      can succeed and nothing may be billed for trying
    4. carrier send   costs money

The reference system ran the region check after the send and simply logged the
rejections as failures — it paid for thousands of attempts it knew would fail.
"""

from sqlalchemy.orm import Session
from app.core.config import settings
from app.models.campaign import Campaign
from app.models.category import Category
from app.models.sms_message import SMSMessage
from app.models.contact import Contact
from app.services import campaign_outcome, campaign_release, message_render
from app.services import cost_reconciliation
from app.services.blocklist_service import load_blocked_set, block_number
# Re-exported deliberately: these three names were defined here before 5e split
# creation out, and `routers/campaigns.py`, `campaign_dispatch.py` and the suite
# all reach for them at this address. Moving the definitions without keeping the
# names would be a rename dressed up as a refactor.
from app.services.campaign_builder import (        # noqa: F401  (re-export)
    CampaignError, NO_CATEGORY_ERROR, create_campaign as _build_campaign,
    resolve_category as _resolve_category, wholesale_cost, wholesale_estimate,
)
from app.sms.factory import (
    get_provider, provider_fallback, send_mode, send_path_assessment,
)
from app.sms.segments import count_segments
from app.sms.phone import is_non_us_region, scrub_provider_text
from app.sms.compliance import should_auto_block
from datetime import datetime
from decimal import Decimal
from typing import Optional
import asyncio
import logging

logger = logging.getLogger("campaign")

# What a row gets when it was queued but the send path could not reach a carrier.
# Deliberately outside BILLABLE_STATUSES — see app/models/sms_message.py. A DB
# status, so it stays on this side of the layering boundary; the *wording* the
# client reads for the same fault lives in app/sms/factory.py next to the rest
# of it.
DEGRADED_STATUS = "not_sent"


class CampaignService:
    def __init__(self, db: Session):
        self.db = db
        self.provider = get_provider()

    # ─── Template rendering ─────────────────────────────────────────────────

    def render(self, template: str, contact: Contact,
               link_url: Optional[str] = None) -> str:
        """One recipient's message — see `message_render.render()`.

        Kept as a method: it is the address the builder, the top-up and
        pre-flight all reach for, so the renderer whose output is billed stays
        the one that measured it.
        """
        return message_render.render(template, contact, link_url)

    # ─── Creation ───────────────────────────────────────────────────────────

    def resolve_category(self, category_id: Optional[int],
                         cross_category_override: bool,
                         list_audience: bool = False) -> Optional[Category]:
        """The category rule — see `campaign_builder.resolve_category()`.

        Kept as a method because that is where the rule has been enforced since
        module 4, and because "the rule lives in the service, not the router" is
        the property a test pins. The rule itself did not move layers; it moved
        file, and the delegation is what makes that true rather than claimed.
        """
        return _resolve_category(self.db, category_id, cross_category_override,
                                 list_audience)

    def create_campaign(self, name: str, message_template: str, audience: str,
                        batch_size: Optional[int] = None,
                        category_id: Optional[int] = None,
                        cross_category_override: bool = False,
                        scheduled_at: Optional[str] = None,
                        link_target_url: Optional[str] = None,
                        list_audience: bool = False) -> Campaign:
        """Build a draft — see `campaign_builder.create_campaign()`.

        `self.render` is handed over rather than re-implemented there: the
        renderer whose output is billed has to be the one that measured it, and
        since 5f it is also the one that fills in each recipient's own short
        link.
        """
        return _build_campaign(
            self.db, self.render,
            name=name, message_template=message_template, audience=audience,
            batch_size=batch_size, category_id=category_id,
            cross_category_override=cross_category_override,
            scheduled_at=scheduled_at, link_target_url=link_target_url,
            list_audience=list_audience,
        )

    # ─── Pre-flight ─────────────────────────────────────────────────────────

    async def capacity_assessment(self, estimated_segments: int,
                                  estimated_cost: float) -> dict:
        """Can the account fund `estimated_segments`? The arithmetic, on its own.

        Split out of `preflight()` below so the composer's pre-flight endpoint
        can show the same verdict the send path enforces, from the same numbers.
        The threshold, the wording and the order of the branches are unchanged —
        this is where they live now, not what they say.

        `balance` and `required` are in dollars at OUR wholesale rate and are
        returned for the caller's *log*. Only `ok` and `detail` are safe to show
        the client, and `detail` is deliberately denominated in segments.

        **The comparison is exact and the rounding happens after it** (5h A2).
        `estimated_cost` arrives rounded to cents — it fills
        `campaigns.estimated_cost` and the log line below — and a guard fed a
        rounded requirement has a threshold nobody chose. Below half a cent a
        segment it rounds to `$0.00`, and an empty account satisfies "must hold
        at least nothing"; at the 0.009 this box runs it is looser rather than
        inert, six segments asking $0.075 against a true $0.081. CLAUDE.md has
        carried the rule since module 1b: Decimal end to end, round at the edge,
        never before a comparison.
        """
        if not settings.PREFLIGHT_BALANCE_CHECK:
            return {"ok": True, "detail": "pre-flight disabled", "checked": False}

        balance = await self.provider.get_balance()
        if balance is None:
            return {"ok": True, "detail": "provider does not expose a balance",
                    "checked": False}

        # The larger of what the segments exactly cost and what the caller says
        # they cost. Normally the same figure to within a rounding step, since
        # `estimated_cost` is `wholesale_estimate(estimated_segments)` — taking
        # the max is what guarantees this change can only ever *tighten* the
        # guard, including for a future caller whose two arguments disagree.
        exact_needed = max(wholesale_cost(estimated_segments),
                           Decimal(str(estimated_cost or 0)))
        # Ask for headroom — the estimate uses a blended rate, and real per-carrier
        # rates vary above it.
        exact_required = exact_needed * Decimal("1.5")
        needed = float(exact_needed)
        required = float(exact_required)

        # The threshold above is unchanged and deliberately so. What changed is
        # only how the result is *worded*: this string is stored on the campaign
        # as abort_reason and rendered straight into the client's campaign list,
        # and it used to quote our carrier account's dollar balance. He is billed
        # in segments and should read the answer in segments; the money view goes
        # to our own log, where only we see it.
        rate = settings.WHOLESALE_COST_PER_SEGMENT
        capacity_segments = int(balance / rate) if rate > 0 else 0
        required_segments = int(round((estimated_segments or 0) * 1.5))

        # Decimal on both sides, `Decimal(str(balance))` for `cost_for_segments()`'s
        # reason: the provider hands back a float and str() keeps the figure the
        # carrier reported. This line is hygiene rather than the fix — see where
        # R15 would have been in `agent/mutate-5h.py`. Comparing these as floats
        # orders identically at every magnitude a balance can hold; the defect
        # was rounding the requirement before it got here. Decimal keeps the
        # chain exact so a later edit cannot slip `n * rate` back into it.
        if Decimal(str(balance)) < exact_required:
            return {
                "ok": False, "checked": True, "balance": balance,
                "required": required, "needed": needed,
                "detail": (
                    f"Not enough sending capacity to start this campaign. It needs about "
                    f"{required_segments:,} segments including a safety margin, and about "
                    f"{capacity_segments:,} remain. Nothing was sent."
                ),
            }

        return {
            "ok": True, "checked": True, "balance": balance,
            "required": required, "needed": needed,
            "detail": (f"capacity covers the estimated {estimated_segments or 0:,} "
                       f"segments"),
        }

    async def preflight(self, campaign: Campaign, segments: Optional[int] = None,
                        cost: Optional[float] = None) -> tuple[bool, str]:
        """Check the account can fund the whole campaign before sending any of it.

        This exists because of a failure that recurred five times in the
        reference deployment: a large blast burns the provider balance to zero
        partway through, the account goes inactive, and every remaining message
        fails with "Account inactive / out of funds". One campaign lost 4,623 of
        6,771 messages that way. Auto-recharge did not save it — a $10 top-up
        cannot post faster than a 6-message-per-second blast spends it.

        Refusing to start is always cheaper than stopping halfway: a campaign
        that never ran can simply be re-run, while a half-sent one leaves you
        unable to tell who got the message.

        Two refusals now, in this order. The send path is checked before
        capacity because a box that cannot reach a carrier has no capacity
        question to answer — and because the answer it *would* give is the
        console stub's 999,999, which is exactly how a degraded box sailed
        through the check that exists to stop this.

        `segments`/`cost` override the campaign's stored figures, and exist for
        one caller: a top-up, whose capacity question is about the handful of
        recipients being added rather than the thousands the campaign already
        reached and paid for. Charging a top-up against the original blast's
        estimate would refuse a five-message top-up on the grounds that a
        6,857-message campaign is unaffordable. Nothing about the check itself
        changes — same threshold, same margin, same wording, applied to the
        segments this run will actually queue.
        """
        send_path = send_path_assessment()
        if not send_path["ok"]:
            # The cause stays here. provider_fallback() carries raw SDK text and
            # the carrier's name; the client gets send_mode()'s wording and this
            # gets the line that lets someone fix it.
            fallback = provider_fallback()
            logger.error(
                f"Campaign #{campaign.id} pre-flight FAILED | send path degraded | "
                f"requested={fallback.requested if fallback else '?'} "
                f"{fallback.error_type if fallback else '?'}: "
                f"{fallback.error if fallback else 'no fallback recorded'}"
            )
            # The abort wording: this is stored as the campaign's abort_reason
            # and read back after the fact, not shown to someone still typing.
            return False, send_path["abort_detail"]

        assessment = await self.capacity_assessment(
            campaign.estimated_segments if segments is None else segments,
            campaign.estimated_cost if cost is None else cost,
        )

        # The money view of the same verdict, for our log only.
        if assessment["checked"] and not assessment["ok"]:
            logger.error(
                f"Campaign #{campaign.id} pre-flight FAILED | "
                f"balance ${assessment['balance']:.2f} < required "
                f"${assessment['required']:.2f} (estimate ${assessment['needed']:.2f})"
            )
        elif assessment["checked"]:
            logger.info(
                f"Campaign #{campaign.id} pre-flight OK | "
                f"balance ${assessment['balance']:.2f} covers estimate "
                f"${assessment['needed']:.2f}"
            )

        return assessment["ok"], assessment["detail"]

    # ─── Send ───────────────────────────────────────────────────────────────

    async def send_campaign(self, campaign_id: int) -> Campaign:
        campaign = self.db.get(Campaign, campaign_id)
        if not campaign:
            raise CampaignError(f"Campaign {campaign_id} not found")
        if campaign.status not in ("draft",):
            raise CampaignError(f"Campaign {campaign_id} is {campaign.status}, not draft")

        ok, detail = await self.preflight(campaign)
        if not ok:
            campaign.status = "aborted"
            campaign.abort_reason = detail
            campaign.completed_at = datetime.now().isoformat()
            self.db.commit()
            logger.error(f"Campaign #{campaign_id} ABORTED pre-flight: {detail}")
            return campaign

        logger.info(f"Campaign #{campaign_id} pre-flight OK: {detail}")
        return await self.run_send_loop(campaign)

    async def run_send_loop(self, campaign: Campaign, *, top_up: bool = False,
                            suppressed_this_run: int = 0) -> Campaign:
        """Hand every pending row to the carrier, then adjudicate the run.

        Split out of `send_campaign()` so a top-up runs the *same* loop rather
        than a second, thinner one — the argument `campaign_dispatch.py` makes
        about the scheduler, one level down. Everything that decides whether a
        message goes out is in here and nowhere else: the blocklist, the region
        filter, the degraded-path backstop, and the order they run in.

        Callers are responsible for the pre-flight refusal before this is
        reached. This is not a second place to skip it — it is what runs once it
        has passed.
        """
        campaign_id = campaign.id
        previously_sent = campaign.sent_count or 0

        campaign.status = "running"
        if not top_up:
            campaign.started_at = datetime.now().isoformat()
        self.db.commit()

        messages = self.db.query(SMSMessage).filter(
            SMSMessage.campaign_id == campaign_id,
            SMSMessage.status == "pending",
        ).all()

        # One query, not one per recipient.
        blocked = load_blocked_set(self.db)
        total = len(messages)
        degraded_rows = 0
        # This run's own outcomes. The campaign's lifetime counters cannot answer
        # "did this run reach anybody" once a campaign can be sent to more than
        # once, and a top-up that reached nobody sitting under a campaign that
        # reached 1,200 is exactly the case the counters would hide.
        run = {"sent": 0, "blocked": 0, "region_skipped": 0, "failed": 0}
        logger.info(f"Campaign #{campaign_id} sending {total} messages"
                    + (" (top-up)" if top_up else ""))

        for i, msg in enumerate(messages, 1):
            try:
                # 1. Opt-outs — free, and non-negotiable.
                if msg.phone in blocked:
                    msg.status = "blocked"
                    msg.error_message = "Number is on the blocklist"
                    campaign.skipped_count += 1
                    run["blocked"] += 1
                    self.db.commit()
                    continue

                # 2. Undeliverable regions — free to skip, costly to attempt.
                if settings.SKIP_NON_US_NUMBERS and is_non_us_region(msg.phone):
                    msg.status = "skipped"
                    msg.error_message = "Destination region not enabled for sending"
                    campaign.skipped_count += 1
                    run["region_skipped"] += 1
                    self.db.commit()
                    continue

                # 3. The send path itself — the backstop under the pre-flight
                #    refusal above, not a substitute for it. The refusal aborts
                #    the campaign before the loop starts, and `_fallback` only
                #    changes on an explicit force_reload, which nothing in
                #    production calls — so today this branch is unreachable
                #    rather than merely rare. It is here because "unreachable"
                #    is what a row marked `sent` and invoiced for a message
                #    nobody received was until session 5d, and because the first
                #    "reload the provider" admin button makes it reachable.
                #    Checked per message so a provider that degrades mid-blast
                #    cannot leave the first thousand rows honest and the rest
                #    billable. The status is outside BILLABLE_STATUSES and the
                #    counter it moves is skipped, not sent: a segment that never
                #    reached a carrier is not billable, which is what the word
                #    means rather than a concession. See decision 002.
                if send_mode().key == "unavailable":
                    msg.status = DEGRADED_STATUS
                    msg.error_message = send_path_assessment()["abort_detail"]
                    campaign.skipped_count += 1
                    degraded_rows += 1
                    self.db.commit()
                    continue

                # 4. Money is spent past this line.
                result = await self.provider.send(msg.phone, msg.message)

                if result.success:
                    msg.status = "sent"
                    msg.sent_at = datetime.now().isoformat()
                    msg.external_id = result.message_id
                    # Trust the carrier's count; fall back to our estimate.
                    msg.segments = result.parts or count_segments(msg.message)
                    # What it actually cost US (5f A4). Written by the module
                    # that reads it back, so the wholesale figure has one owner
                    # and never crosses the API boundary.
                    cost_reconciliation.record(msg, result)
                    campaign.sent_count += 1
                    run["sent"] += 1

                    if msg.contact_id:
                        contact = self.db.get(Contact, msg.contact_id)
                        if contact:
                            contact.last_messaged_at = msg.sent_at
                else:
                    error = result.error or "Unknown error"
                    if should_auto_block(error):
                        block_number(
                            self.db, msg.phone,
                            reason="delivery_failure",
                            source=self.provider.name,
                            notes=f"Auto-blocked: {scrub_provider_text(error)[:200]}",
                        )
                    msg.status = "failed"
                    msg.error_message = scrub_provider_text(error)
                    campaign.failed_count += 1
                    run["failed"] += 1
                    logger.error(f"  [{i}/{total}] FAILED {msg.phone}: {msg.error_message}")

                self.db.commit()
                await asyncio.sleep(settings.SEND_DELAY_SECONDS)

            except Exception as e:
                logger.error(f"  [{i}/{total}] ERROR {msg.phone}: {e}")
                msg.status = "failed"
                # Scrubbed at write, like the handled-failure path above: an
                # SDK exception carries the carrier's name in its class name and
                # its message, and this text is read back into the client's
                # campaign detail view.
                msg.error_message = scrub_provider_text(str(e))
                campaign.failed_count += 1
                run["failed"] += 1
                self.db.commit()

        # ─── Did this run reach anybody? ────────────────────────────────────
        #
        # A campaign the backstop above caught did not complete — it reached
        # nobody. Saying "completed" with no abort reason is the same lie one
        # level down that session 5d exists to remove: the campaign rail is the
        # entire UI (there is no detail screen), it renders the status badge and
        # shows a reason only when abort_reason is set, so a blast that went
        # nowhere would read exactly like one that worked. The per-message error
        # is already correct; nothing displays it.
        #
        # 5e A7 generalises that. A degraded send path is one way to reach
        # nobody; a fully-suppressed audience, a list that is entirely opted out
        # and an audience that resolved to zero are the ways it actually
        # happened in production — twice, both reported `completed` with
        # sent_count 0.
        reason = None
        if degraded_rows:
            reason = send_path_assessment()["abort_detail"]
            logger.error(
                f"Campaign #{campaign_id} reached nobody: the send path degraded "
                f"after pre-flight passed; {degraded_rows} message(s) written "
                f"{DEGRADED_STATUS} and none billed"
            )
        elif run["sent"] == 0:
            suppressed = (suppressed_this_run if top_up
                          else (campaign.suppressed_count or 0))
            # Decision 006: when the window took everyone, the reason has to name
            # the window and when it lifts. Read *after* the loop, so the
            # clearing time reflects the rows as they stand, and only on the
            # branch that uses it — the blocked, region and all-failed reasons
            # never mention a hold, and queries on their path would be new ways
            # for an adjudication that used to be pure arithmetic to fail.
            window = (campaign_release.hold_facts(self.db, campaign_id)
                      if total == 0 and suppressed else {})
            reason = campaign_outcome.zero_send_reason(
                queued=total,
                blocked=run["blocked"],
                region_skipped=run["region_skipped"],
                failed=run["failed"],
                suppressed=suppressed,
                **window,
            )
            logger.error(f"Campaign #{campaign_id} reached nobody: {reason}")

        if reason and not top_up:
            campaign.status = "aborted"
            campaign.abort_reason = reason
        elif reason:
            # A top-up keeps `completed`, and that is not a softening of the rule
            # above. The original blast reached `previously_sent` people and one
            # later event cannot revoke that — the same argument that stopped a
            # late failure webhook from un-delivering a message in 5d. What must
            # not happen is silence, so the reason is still stored and the rail
            # still renders it; `top_up_reason()` fronts it with the fact that it
            # describes the top-up rather than the campaign, because the badge
            # beside it says "completed" and the sentence has to survive that.
            campaign.status = "completed"
            campaign.abort_reason = campaign_outcome.top_up_reason(total, reason)
        else:
            campaign.status = "completed"
            if top_up:
                # A successful top-up clears a reason an earlier one left behind.
                # A stale warning under a run that worked is its own small lie.
                campaign.abort_reason = None

        campaign.completed_at = datetime.now().isoformat()
        self.db.commit()

        logger.info(
            f"Campaign #{campaign_id} {campaign.status}: {campaign.sent_count} sent, "
            f"{campaign.failed_count} failed, {campaign.skipped_count} skipped"
            + (f" (top-up added {run['sent']} of {total}; "
               f"{previously_sent} already sent)" if top_up else "")
        )
        # Our cost against our estimate, in our log and nowhere the client can
        # reach — the other half of what `campaigns.estimated_cost` has always
        # said it was for.
        cost_reconciliation.log_reconciliation(self.db, campaign_id)
        return campaign

