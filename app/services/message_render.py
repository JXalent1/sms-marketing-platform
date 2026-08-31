"""Turning one campaign template into one recipient's message.

Split out of `campaign_service.py` when 5f pushed that file past the 500-line
rule for the fourth time. The seam is *what a message says* against *whether and
how it is sent* — the same grain as `campaign_builder` (what a campaign is) and
`campaign_dispatch` (when a send begins). Nothing here reads a provider, a
setting or a status, and nothing in the send loop decides what a message says.

`CampaignService.render` stays as a delegate, because every caller reaches for
it there: the builder, the top-up and pre-flight are all handed `service.render`
as a callable so that the renderer whose output is billed is the one that
measured it. That property is the reason this is a function and not a second
implementation.
"""

from typing import Any, Optional


def render(template: str, contact: Any, link_url: Optional[str] = None) -> str:
    """Substitute {placeholders} for one contact.

    Available: {name}, {first_name}, {phone}, plus any key in
    `contact.attributes` — so a client-specific {last_order_date} needs no code
    change, just data on the contact.

    Unknown placeholders are left as-is rather than blanked, so a typo shows up
    in the preview instead of shipping an awkward gap to 6,000 people.

    `{link}` is the one tag not filled from the contact, because it is a
    different URL for every recipient and has to be minted. It is substituted
    only when `link_url` is supplied and left as the literal `{link}` otherwise
    — the same rule as any unknown placeholder, and for the same reason. A
    caller that forgets ships a visibly broken message rather than a link that
    quietly points somewhere wrong, and both callers that matter pass it:
    `campaign_builder` passes each recipient's minted URL, and pre-flight passes
    `link_service.placeholder_url()`, which is the same length by construction
    so the segment count is the real one.
    """
    name = contact.full_name or ""
    values = {
        "name": name,
        "first_name": name.split(" ")[0] if name else "",
        "phone": contact.phone,
        **{k: str(v) for k, v in (contact.attributes or {}).items()},
    }
    if link_url is not None:
        # After the attributes, so a stray `link` column in a client's CSV
        # cannot shadow the campaign's own link.
        values["link"] = link_url

    message = template
    for key, value in values.items():
        message = message.replace(f"{{{key}}}", value)
    return message
