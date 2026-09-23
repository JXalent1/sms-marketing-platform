"""Auction platform registry — which of his selling accounts we read bidders from.

Ported from the reference scraper's `app/platforms.py`, and kept a registry on
purpose rather than collapsed to a constant. He sells on Proxibid and AuctionZip
as well, and the point of this file is that a second site is **one entry here
plus one `AuctionScraperBase` subclass** — nothing in the service, the model or
the schedule changes to take it.

Every bidder profile row carries a slug from here (`bidder_profiles.platform`),
so "won three items *on LiveAuctioneers*" stays answerable the day a second
platform lands, and one person's two platform histories never overwrite each
other.

What was dropped in the port, and why:

  - `active_platforms()` / `active_labels()` drove the reference app's billing
    base fee. A4A bills per segment and nothing else, so a platform's existence
    must not be able to move an invoice.
  - `DEFAULT_PLATFORM` / `normalize()` backfilled rows written before the
    reference app went multi-platform, and coerced an unknown slug to the
    default. Silently re-labelling an unknown platform as LiveAuctioneers is how
    one site's bidders get filed under another's; here an unknown slug raises.

Only **his own partner/seller account** on each site belongs in this registry —
`decisions/014`. Reading another house's public bidder lists is still out.
"""

LIVEAUCTIONEERS = "liveauctioneers"

PLATFORMS = {
    LIVEAUCTIONEERS: {
        "slug": LIVEAUCTIONEERS,
        "label": "LiveAuctioneers",
        "domain": "liveauctioneers.com",
        # "module:Class" rather than the class itself: the scraper module pulls
        # in browser machinery, and listing the platforms must not pay for it.
        "scraper": "app.sources.liveauctioneers:LiveAuctioneersSource",
    },
}


def is_valid(slug: str) -> bool:
    return slug in PLATFORMS


def get(slug: str) -> dict:
    """The registry entry. An unknown slug is a ValueError, never a default."""
    if slug not in PLATFORMS:
        raise ValueError(f"Unknown auction platform {slug!r}. "
                         f"Registered: {', '.join(PLATFORMS)}")
    return PLATFORMS[slug]


def label_for(slug: str) -> str:
    """Human-readable name for a slug; falls back to the slug itself."""
    platform = PLATFORMS.get(slug)
    return platform["label"] if platform else (slug or "Unknown")


def list_name(slug: str) -> str:
    """The contact list a platform's bidders are added to.

    Named from the registry's own label so the picker entry and the platform
    cannot drift apart, and one list per platform rather than one per run: the
    daily read re-finds the same registered bidders every morning, and a list
    per morning would bury the picker in thirty identical entries a month.
    """
    return f"{label_for(slug)} bidders"


def scraper_class(slug: str):
    """Import and return the platform's scraper class, lazily."""
    module_path, class_name = get(slug)["scraper"].split(":")
    module = __import__(module_path, fromlist=[class_name])
    return getattr(module, class_name)
