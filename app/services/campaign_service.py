"""Campaign creation and the send loop.

This is the module you will modify most for a new client, and the one where
mistakes are expensive — every branch here is a decision about someone else's
money and someone else's phone.

Ordering in the send loop is deliberate. Filters that cost nothing run before
the ones that cost money:

    1. blocklist      free, and legally required
    2. region filter  free; these are guaranteed-undeliverable
    3. carrier send   costs money

The reference system ran the region check after the send and simply logged the
rejections as failures — it paid for thousands of attempts it knew would fail.
"""

from sqlalchemy.orm import Session
from app.core.config import settings
from app.models.campaign import Campaign
from app.models.sms_message import SMSMessage
from app.models.contact import Contact
from app.services import contact_service
from app.services.blocklist_service import load_blocked_set, block_number
from app.sms.factory import get_provider
from app.sms.segments import count_segments
from app.sms.phone import is_non_us_region, scrub_provider_text, find_risky_links
from app.sms.compliance import should_auto_block
from datetime import datetime
from typing import Optional
import asyncio
import logging

logger = logging.getLogger("campaign")


class CampaignError(Exception):
    """Raised for problems the operator can fix (empty audience, no funds)."""


class CampaignService:
    def __init__(self, db: Session):
        self.db = db
        self.provider = get_provider()

    # ─── Template rendering ─────────────────────────────────────────────────

    def render(self, template: str, contact: Contact) -> str:
        """Substitute {placeholders} for one contact.

        Available: {name}, {first_name}, {phone}, plus any key in
        contact.attributes — so a client-specific {last_order_date} needs no
        code change, just data on the contact.

        Unknown placeholders are left as-is rather than blanked, so a typo shows
        up in the preview instead of shipping an awkward gap to 6,000 people.
        """
        name = contact.full_name or ""
        values = {
            "name": name,
            "first_name": name.split(" ")[0] if name else "",
            "phone": contact.phone,
            **{k: str(v) for k, v in (contact.attributes or {}).items()},
        }
        message = template
        for key, value in values.items():
            message = message.replace(f"{{{key}}}", value)
        return message

    # ─── Creation ───────────────────────────────────────────────────────────

    def create_campaign(self, name: str, message_template: str, audience: str,
                        batch_size: Optional[int] = None) -> Campaign:
        """Build a campaign and queue one pending SMSMessage per recipient."""
        recipients = contact_service.resolve_audience(self.db, audience)
        if not recipients:
            raise CampaignError(f"No contacts matched audience {audience!r}")

        if batch_size and batch_size > 0:
            recipients = recipients[:batch_size]

        # Warn loudly about links carriers will silently eat (see phone.py).
        risky = find_risky_links(message_template)
        if risky:
            logger.warning(
                f"Campaign '{name}' contains public shortener link(s) {risky}. "
                f"Carriers commonly spam-block these; use a first-party domain."
            )

        campaign = Campaign(
            name=name,
            message_template=message_template,
            audience=audience,
            audience_label=contact_service.audience_label(self.db, audience),
            total_recipients=len(recipients),
            status="draft",
            created_at=datetime.now().isoformat(),
        )
        self.db.add(campaign)
        self.db.commit()
        self.db.refresh(campaign)

        estimated_segments = 0
        for contact in recipients:
            body = self.render(message_template, contact)
            estimated_segments += count_segments(body)
            self.db.add(SMSMessage(
                campaign_id=campaign.id,
                contact_id=contact.id,
                phone=contact.phone,
                message=body,
                status="pending",
            ))

        campaign.estimated_segments = estimated_segments
        # Wholesale, i.e. what this campaign costs US. It funds the pre-flight
        # capacity check below and our own logs, and it is deliberately absent
        # from the campaign API payload — see _campaign_dict in routers/campaigns.py.
        campaign.estimated_cost = round(estimated_segments * settings.WHOLESALE_COST_PER_SEGMENT, 2)
        self.db.commit()

        logger.info(
            f"Campaign #{campaign.id} '{name}' created | {len(recipients)} recipients "
            f"| ~{estimated_segments} segments | est. wholesale cost "
            f"${campaign.estimated_cost:.2f}"
        )
        return campaign

    # ─── Pre-flight ─────────────────────────────────────────────────────────

    async def preflight(self, campaign: Campaign) -> tuple[bool, str]:
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
        """
        if not settings.PREFLIGHT_BALANCE_CHECK:
            return True, "pre-flight disabled"

        balance = await self.provider.get_balance()
        if balance is None:
            return True, "provider does not expose a balance"

        needed = (campaign.estimated_cost or 0)
        # Ask for headroom — the estimate uses a blended rate, and real per-carrier
        # rates vary above it.
        required = needed * 1.5

        # The threshold above is unchanged and deliberately so. What changed is
        # only how the result is *worded*: this string is stored on the campaign
        # as abort_reason and rendered straight into the client's campaign list,
        # and it used to quote our carrier account's dollar balance. He is billed
        # in segments and should read the answer in segments; the money view goes
        # to our own log, below, where only we see it.
        rate = settings.WHOLESALE_COST_PER_SEGMENT
        capacity_segments = int(balance / rate) if rate > 0 else 0
        required_segments = int(round((campaign.estimated_segments or 0) * 1.5))

        if balance < required:
            logger.error(
                f"Campaign #{campaign.id} pre-flight FAILED | balance ${balance:.2f} "
                f"< required ${required:.2f} (estimate ${needed:.2f})"
            )
            return False, (
                f"Not enough sending capacity to start this campaign. It needs about "
                f"{required_segments:,} segments including a safety margin, and about "
                f"{capacity_segments:,} remain. Nothing was sent."
            )

        logger.info(
            f"Campaign #{campaign.id} pre-flight OK | balance ${balance:.2f} "
            f"covers estimate ${needed:.2f}"
        )
        return True, (f"capacity covers the estimated {campaign.estimated_segments or 0:,} "
                      f"segments")

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

        campaign.status = "running"
        campaign.started_at = datetime.now().isoformat()
        self.db.commit()

        messages = self.db.query(SMSMessage).filter(
            SMSMessage.campaign_id == campaign_id,
            SMSMessage.status == "pending",
        ).all()

        # One query, not one per recipient.
        blocked = load_blocked_set(self.db)
        total = len(messages)
        logger.info(f"Campaign #{campaign_id} sending {total} messages")

        for i, msg in enumerate(messages, 1):
            try:
                # 1. Opt-outs — free, and non-negotiable.
                if msg.phone in blocked:
                    msg.status = "blocked"
                    msg.error_message = "Number is on the blocklist"
                    campaign.skipped_count += 1
                    self.db.commit()
                    continue

                # 2. Undeliverable regions — free to skip, costly to attempt.
                if settings.SKIP_NON_US_NUMBERS and is_non_us_region(msg.phone):
                    msg.status = "skipped"
                    msg.error_message = "Destination region not enabled for sending"
                    campaign.skipped_count += 1
                    self.db.commit()
                    continue

                # 3. Money is spent past this line.
                result = await self.provider.send(msg.phone, msg.message)

                if result.success:
                    msg.status = "sent"
                    msg.sent_at = datetime.now().isoformat()
                    msg.external_id = result.message_id
                    # Trust the carrier's count; fall back to our estimate.
                    msg.segments = result.parts or count_segments(msg.message)
                    campaign.sent_count += 1

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
                self.db.commit()

        campaign.status = "completed"
        campaign.completed_at = datetime.now().isoformat()
        self.db.commit()

        logger.info(
            f"Campaign #{campaign_id} complete: {campaign.sent_count} sent, "
            f"{campaign.failed_count} failed, {campaign.skipped_count} skipped"
        )
        return campaign


async def send_campaign_background(campaign_id: int):
    """Entry point for BackgroundTasks — owns its own DB session.

    A request-scoped session is closed the moment the HTTP response is returned,
    so a background job must never borrow one.
    """
    from app.core.database import SessionLocal
    db = SessionLocal()
    try:
        await CampaignService(db).send_campaign(campaign_id)
    except Exception as e:
        logger.error(f"Campaign {campaign_id} background send failed: {e}")
    finally:
        db.close()
