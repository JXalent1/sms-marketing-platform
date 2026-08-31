"""Building a campaign: the category rule, the draft, and the upload-first flow.

Split out of `campaign_service.py` when 5e pushed that file past the 500-line
rule for the third time. The boundary is *deciding what a campaign is* against
*running it* — nothing here sends anything, reads a provider, or touches the
pre-flight checks, and nothing in `campaign_service`'s send loop decides who a
campaign is for. `campaign_dispatch.py` sits on the other side of the same seam
(*when* a send begins).

`render` is passed in as a callable rather than imported, for the reason
`preflight_service.exact_segment_totals()` gives: the send path's own renderer is
the one whose output is billed, and a second copy of that logic would eventually
measure something the send path does not produce. It is also what keeps this
module free of an import back into `campaign_service`.

Three ways a campaign records who it is for
───────────────────────────────────────────
Module 4 shipped two: a real category, or an explicitly typed cross-category
override. 5e adds the third, and it is not a loosening of the first two.

When a CSV is uploaded as step one of *this* campaign, the list that upload
creates is the campaign's entire audience. The targeting is the list. Requiring a
category there would either be a lie (the message is not going to a niche, it is
going to the 412 restaurants in that file) or would push every upload through the
cross-category override, which is an audit trail that means nothing once everyone
ticks it. So the upload path records `category_id` NULL, `cross_category_override`
0, and `audience = "list:<id>"` — and the audience column is the record of the
decision, which is what the other two cases were always for.

`POST /api/campaigns` is unchanged: an existing list, `all`, and a category
selector all still require a category or a typed override, because for those the
audience does not say which auction the message is about.
"""

import logging
from typing import Any, Callable, Optional, Tuple

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.campaign import Campaign
from app.models.category import Category
from app.models.sms_message import SMSMessage
from app.services import contact_service, import_service, suppression_service
from app.sms.phone import find_risky_links
from app.sms.segments import count_segments
from datetime import datetime

logger = logging.getLogger("campaign")

# Every campaign carries the niche it is for, or a recorded decision not to.
NO_CATEGORY_ERROR = (
    "This campaign has no category. Pick the auction it is for — the category is "
    "what keeps a memorabilia buyer from being texted about a walk-in cooler. If "
    "this really is meant to go to every niche at once, set the cross-category "
    "override explicitly and it will be recorded on the campaign."
)

# What the client is told when a file's usable numbers are all on the blocklist.
# The generic "No contacts matched audience 'list:47'" is true and useless: it
# names an id he never chose and does not say that the file was fine and the
# people in it have opted out.
ALL_OPTED_OUT_ERROR = (
    "Every usable number in that file is on your opt-out list, so this campaign "
    "would reach nobody. Nothing was created and the upload was rolled back."
)

NO_USABLE_NUMBERS_ERROR = (
    "No usable phone numbers in that file, so this campaign would reach nobody. "
    "Nothing was created and the upload was rolled back. Check the file has a "
    "phone column and that it was recognised — the preview reports what it found."
)


class CampaignError(Exception):
    """Raised for problems the operator can fix (empty audience, no funds)."""


def wholesale_estimate(segments: int) -> float:
    """What `segments` costs US, at our blended carrier rate.

    OUR cost, not his. It exists to convert a carrier balance into a capacity
    estimate for the pre-flight check and to fill `campaigns.estimated_cost`,
    which never crosses the API boundary. His number comes from billing_service
    at BILLING_PRICE_PER_SEGMENT and is roughly 40% higher.

    Lives here rather than in `campaign_service` only so that module can import
    this one without a cycle; it is re-exported there under the same name, which
    is where every existing caller reaches for it.
    """
    return round((segments or 0) * settings.WHOLESALE_COST_PER_SEGMENT, 2)


# ─── The category rule ──────────────────────────────────────────────────────

def resolve_category(db: Session, category_id: Optional[int],
                     cross_category_override: bool,
                     list_audience: bool = False) -> Optional[Category]:
    """The category rule, in one place.

    A campaign gets a real category, an explicitly-typed override, or an audience
    that is a list uploaded for it. There is no fourth case and no default — the
    moment "no category" becomes a value the form can submit by accident, the
    guarantee this module exists for is gone.

    `list_audience` is not a way to skip the rule; it is the third way of
    satisfying it, and only the upload flow passes it. See the module docstring.
    """
    if category_id is None:
        if cross_category_override or list_audience:
            return None
        raise CampaignError(NO_CATEGORY_ERROR)

    category = db.get(Category, category_id)
    if category is None:
        raise CampaignError(
            f"No category with id {category_id}. A campaign cannot point at a "
            f"category that does not exist."
        )
    return category


# ─── Creation ───────────────────────────────────────────────────────────────

def create_campaign(db: Session, render: Callable[[str, Any], str], *,
                    name: str, message_template: str, audience: str,
                    batch_size: Optional[int] = None,
                    category_id: Optional[int] = None,
                    cross_category_override: bool = False,
                    scheduled_at: Optional[str] = None,
                    list_audience: bool = False) -> Campaign:
    """Build a campaign and queue one pending SMSMessage per recipient.

    Recipients texted inside the suppression window are queued too, as
    `skipped`. Recording them rather than dropping them is what makes the
    number visible before the send instead of inferable afterwards: the
    composer shows "1,204 will receive this, 37 held back" while there is
    still time to change the audience.
    """
    category = resolve_category(db, category_id, cross_category_override, list_audience)

    recipients = contact_service.resolve_audience(db, audience)
    if not recipients:
        raise CampaignError(f"No contacts matched audience {audience!r}")

    # Suppression before the cap, not after. "Send to the first 50" has to
    # mean fifty people receive it; capping first and suppressing second
    # would silently deliver forty.
    sendable, suppressed = suppression_service.partition_recent(db, recipients)

    if batch_size and batch_size > 0:
        sendable = sendable[:batch_size]

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
        audience_label=contact_service.audience_label(db, audience),
        category_id=category.id if category else None,
        # What was actually asked for, not what can be inferred from a NULL
        # category. Since 5e a campaign can have no category because its audience
        # is its own upload, and recording that as a cross-category override
        # would put a decision nobody made into the audit trail.
        cross_category_override=1 if cross_category_override else 0,
        total_recipients=len(sendable),
        suppressed_count=len(suppressed),
        # Held-back contacts are skipped, exactly like a blocklisted or
        # out-of-region number: queued, never sent, never billed. The send
        # loop adds its own skips to this as it runs.
        skipped_count=len(suppressed),
        scheduled_at=scheduled_at or None,
        status="draft",
        created_at=datetime.now().isoformat(),
    )
    db.add(campaign)
    db.commit()
    db.refresh(campaign)

    estimated_segments = 0
    for contact in sendable:
        body = render(message_template, contact)
        estimated_segments += count_segments(body)
        db.add(SMSMessage(
            campaign_id=campaign.id,
            contact_id=contact.id,
            phone=contact.phone,
            message=body,
            status="pending",
        ))

    held_back = suppression_service.suppression_reason(db)
    for contact in suppressed:
        db.add(SMSMessage(
            campaign_id=campaign.id,
            contact_id=contact.id,
            phone=contact.phone,
            message=render(message_template, contact),
            status="skipped",
            error_message=held_back,
        ))

    campaign.estimated_segments = estimated_segments
    # Wholesale, i.e. what this campaign costs US. It funds the pre-flight
    # capacity check and our own logs, and it is deliberately absent from the
    # campaign API payload — see _campaign_dict in routers/campaigns.py.
    campaign.estimated_cost = wholesale_estimate(estimated_segments)
    db.commit()

    logger.info(
        f"Campaign #{campaign.id} '{name}' created | category "
        f"{category.slug if category else ('CROSS-CATEGORY OVERRIDE' if cross_category_override else 'none (own upload)')} "
        f"| {len(sendable)} recipients ({len(suppressed)} suppressed) "
        f"| ~{estimated_segments} segments | est. wholesale cost "
        f"${campaign.estimated_cost:.2f}"
        + (f" | scheduled for {scheduled_at}" if scheduled_at else "")
    )
    return campaign


# ─── A1: upload first, campaign second ──────────────────────────────────────

def create_campaign_from_upload(db: Session, render: Callable[[str, Any], str], *,
                                name: str, message_template: str, content: bytes,
                                category_id: Optional[int] = None,
                                batch_size: Optional[int] = None,
                                scheduled_at: Optional[str] = None
                                ) -> Tuple[Campaign, dict]:
    """Import a CSV as this campaign's audience, then create the campaign on it.

    The list is named for the campaign, with a numeric suffix on collision, so a
    report three weeks later reads "Italian restaurants" rather than "list 47".
    `ContactList.name` is unique, so the suffix is not a nicety — without it the
    second campaign of the day with the same name is an IntegrityError halfway
    through a commit.

    The importer is the existing one. Its preview counts are the whole reason the
    upload flow is trustworthy, and a second importer would drift from them.

    **The import is rolled back if the campaign cannot be created.** Otherwise a
    rejected campaign leaves a named list and a few hundred new contacts behind,
    and the next attempt collides with the name it just orphaned. `undo()` is the
    existing, tested reversal — subtractive, and it will not delete a contact
    that anything else already references.
    """
    # Validated before the file is touched: an unknown category id must not cost
    # an import that then has to be unwound.
    resolve_category(db, category_id, cross_category_override=False, list_audience=True)

    result = import_service.commit(db, content, category_id, list_name=name)
    list_id = result["list_id"]

    try:
        campaign = create_campaign(
            db, render,
            name=name,
            message_template=message_template,
            audience=f"list:{list_id}",
            batch_size=batch_size,
            category_id=category_id,
            cross_category_override=False,
            scheduled_at=scheduled_at,
            list_audience=True,
        )
    except CampaignError as e:
        import_service.undo(db, list_id)
        logger.error("Campaign from upload '%s' failed; import batch %s rolled back: %s",
                     name, list_id, e)
        raise CampaignError(_upload_failure_message(result, e)) from e
    except Exception:
        import_service.undo(db, list_id)
        logger.exception("Campaign from upload '%s' failed; import batch %s rolled back",
                         name, list_id)
        raise

    return campaign, result


def _upload_failure_message(counts: dict, original: Exception) -> str:
    """Say what went wrong with the *file*, not with the list id it produced.

    Only the empty-audience case is reworded, and only when the counts say why.
    Anything else keeps the original text: inventing an explanation for a failure
    we did not diagnose is how a support call starts with a wrong answer.
    """
    if counts.get("valid_phones", 0) == 0:
        return NO_USABLE_NUMBERS_ERROR
    if counts.get("opted_out", 0) and not (counts.get("new_contacts", 0)
                                           + counts.get("existing_contacts", 0)
                                           + counts.get("already_in_category", 0)):
        return ALL_OPTED_OUT_ERROR
    return str(original)
