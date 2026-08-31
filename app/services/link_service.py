"""Minting short links, resolving them, and recording what comes back.

Four requirements shape everything here, and all four are about not getting the
sending domain filtered or the feature abused.

**One redirect, never a chain.** T-Mobile's code of conduct flags anything that
redirects more than once, and a chain is what gets a domain blocked. So a target
is validated at mint time — it must be a plain http(s) URL, it must not be a
public shortener, and it must not be a link on our own short domain — and the
redirect route sends exactly one 302 to the stored target.

**Closed minting.** There is no endpoint that creates a link. The only caller is
campaign creation, which is behind `require_auth`, and the public route resolves
slugs and never creates one. An open redirector is found and abused within
weeks, and then the branded domain is the one on the carrier blocklist, which
costs far more than this feature is worth.

**Slugs are short, unguessable and unambiguous.** Sequential ids would leak this
client's send volume to anyone who receives one message. The alphabet drops the
characters that are misread aloud or in a saleroom — 0/O, 1/l/I — because the
one thing a client does with a short link is read it to somebody.

**Recording a click must never delay or break the redirect.** `record_click()`
cannot raise. The person is going to the auction whatever the database is doing.

## The placeholder, and why it exists

`{link}` renders to a different string for every recipient, and the segment
count that matters is the count of what actually reaches a handset (the 5b A7
lesson). Pre-flight cannot mint 4,200 rows every time somebody presses "Run
checks", so it renders with `placeholder_url()` instead — a URL built from the
same domain and a slug of exactly `SLUG_LENGTH` characters, so it is the same
length as the real thing by construction, not by coincidence. A test pins that.
"""

import logging
import re
import secrets
from datetime import datetime
from typing import Iterable, List, Optional, Sequence, Tuple

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.short_link import LinkClick, ShortLink
from app.services import click_classifier
from app.sms.phone import SHARED_SHORTENER_DOMAINS

logger = logging.getLogger("links")

# The merge tag. One spelling, in one place — two literals is how the composer
# button and the renderer come to disagree about what the tag is called.
LINK_TAG = "{link}"

# No 0/O, no 1/l/I. A slug gets read out loud.
SLUG_ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"

# 32**8 ≈ 1.1e12. At this client's volume — roughly 4,200 links a campaign,
# most days — a batch of that size has about a one-in-two-hundred chance of
# colliding with anything already stored after a year of sending, and the mint
# below checks the batch against the table anyway. Eight characters is 16 in the
# message including the domain: a tenth of a GSM-7 segment.
SLUG_LENGTH = 8

SLUG_RE = re.compile(rf"^[{SLUG_ALPHABET}]{{{SLUG_LENGTH}}}$")

# Slugs a page already owns. `GET /{slug}` is registered last in `main.py`, so a
# root page always wins the match — which means a slug that spells one is a link
# nobody can follow, and the recipient lands on a login screen instead of the
# auction. Today exactly one page name is eight characters of this alphabet:
# "settings". The odds of drawing it are about one in 10^12, and closing the
# class costs two lines, so it is closed rather than reasoned about.
#
# `favicon` and `robots` are here because browsers and crawlers ask for them by
# name at the root of the short domain; they are not eight characters and cannot
# be drawn, and they are listed so the intent survives a change to SLUG_LENGTH.
#
# The list is kept honest by a test rather than by discipline:
# `test_no_slug_can_be_minted_that_a_page_already_owns` reads the app's own route
# table and fails if a new root page could be drawn as a slug. Asserting the
# property beats pinning the literal — the 5g `OPT_OUT_REASONS` lesson.
RESERVED_SLUGS = frozenset({
    "login", "logout", "health", "dashboard", "campaigns", "contacts",
    "usage", "blocklist", "settings", "history", "static", "favicon", "robots",
})

# What the composer says when the tag is used and the domain is not configured.
# Named cause, named remedy — the shape decision 006 requires of any refusal.
# It is operator-facing wording on a client-facing screen, so it names a setting
# and not a carrier.
NO_DOMAIN_ERROR = (
    "Short links are not switched on yet: no link domain is configured, so "
    f"{LINK_TAG} has nothing to point at. Nothing was created and no links were "
    "made. Ask for the short domain to be set up, or take the link tag out of "
    "the message and paste the full web address instead."
)

NO_TARGET_ERROR = (
    f"The message uses {LINK_TAG} but no web address was given for it to open. "
    "Add the link destination above, or take the tag out of the message."
)


class LinkError(Exception):
    """A link that cannot be minted — an unusable target, or no domain."""


# ─── Configuration ──────────────────────────────────────────────────────────

def domain() -> str:
    """The configured short domain, e.g. "a4a.bz". Empty when unset."""
    return (settings.SHORT_LINK_DOMAIN or "").strip().strip("/").lower()


def configured() -> bool:
    return bool(domain())


def url_for(slug: str) -> str:
    """The link as it appears in a message.

    Bare domain by default — see `SHORT_LINK_INCLUDE_SCHEME`. Every character
    here is charged for at 4,200 recipients.
    """
    prefix = "https://" if settings.SHORT_LINK_INCLUDE_SCHEME else ""
    return f"{prefix}{domain()}/{slug}"


def placeholder_url() -> str:
    """A link of exactly the length a real one will be, for counting only.

    Deliberately not a valid slug shape: it is all one character, so a
    placeholder that ever reached a message is visible at a glance rather than
    being a link that silently 404s. Same length, different look — the length is
    what the segment count needs and the only thing this promises.
    """
    return url_for(SLUG_ALPHABET[0] * SLUG_LENGTH)


def has_link_tag(template: Optional[str]) -> bool:
    return LINK_TAG in (template or "")


# ─── Which host is this, and what may it serve? ─────────────────────────────
#
# The short domain and the admin panel are one process. Without a guard every
# route resolves on both, so `bida4a.com/login` serves the client's entire
# contact list behind a password on a domain whose only job is to redirect —
# and the domain that appears in every text message is the one an attacker is
# handed for free.
#
# The authority lives here rather than in an nginx path denylist, and that was
# the choice: a denylist drifts the moment a route is added, and it puts "what
# may be served" in a file that knows nothing about the routes. Here it is one
# question — does the path match the slug shape this module already defines —
# so a new route is excluded by default rather than by remembering to add it.

def normalize_host(raw: Optional[str]) -> str:
    """A Host header or a configured domain, reduced to a comparable name.

    Lowercased and port-stripped, so `A4A.BZ:8000` and `a4a.bz` are the same
    host. Both sides of the comparison go through this: `SHORT_LINK_DOMAIN` may
    legitimately carry a port in development (`localhost:8000`), and stripping
    it from only one side would silently switch the guard off there — which is
    the environment where it is easiest not to notice.

    IPv6 literals keep their brackets: `[::1]:8000` is `[::1]`, not `[`.
    """
    host = (raw or "").strip().lower()
    if host.startswith("["):
        closing = host.find("]")
        return host[:closing + 1] if closing != -1 else host
    head, separator, tail = host.rpartition(":")
    return head if separator and tail.isdigit() else host


def primary_host() -> str:
    """The host the admin panel is served on, from `PUBLIC_BASE_URL`."""
    base = (settings.PUBLIC_BASE_URL or "").strip()
    return normalize_host(re.sub(r"^https?://", "", base, flags=re.IGNORECASE)
                          .split("/")[0])


def short_domain_conflicts() -> bool:
    """Is `SHORT_LINK_DOMAIN` the same host the admin panel is served on?

    A copy-paste away, and the symptom is the worst kind: the guard would match
    every request, so *every* page of the product answers 404 with "This link
    has expired or was mistyped." — no login, no dashboard, nothing, and nothing
    on screen connecting it to a setting. A client would report the product as
    down and nobody would think to look here.

    There is no configuration in which blocking is the right answer when the two
    names are the same, because then the short domain *is* the admin domain. So
    the guard fails open and `main.py` logs it loudly at startup. That is the
    cheap error: the wrong outcome is the pre-existing one (both surfaces on one
    name), against a total outage with a misleading message.
    """
    configured = domain()
    return bool(configured) and normalize_host(configured) == primary_host()


def is_short_link_host(raw_host: Optional[str]) -> bool:
    """Is this request addressed to the short-link domain?

    False when `SHORT_LINK_DOMAIN` is unset, which makes the guard a no-op on
    every box that has not been given a short domain — including a fresh clone
    and the test suite.

    Reads the `Host` header only, never `X-Forwarded-Host`. nginx sets `Host`
    from `$host`, i.e. from the server block that matched, and a client-supplied
    forwarding header is not evidence of anything. There is no bypass in
    ignoring it either: a request that lies about its Host is routed by nginx to
    the *other* server block, which is the admin panel it would have reached
    anyway.
    """
    configured_domain = domain()
    if not configured_domain or short_domain_conflicts():
        return False
    return normalize_host(raw_host) == normalize_host(configured_domain)


def is_slug_path(path: Optional[str]) -> bool:
    """Is this the one route the short domain exists to serve?

    `/{slug}` and nothing else — not `/health`, not `/static`, not a trailing
    slash. Deliberately strict: the point of the guard is that a path is served
    on the short host only if it is positively a link, so anything new is
    excluded without anyone remembering to exclude it.

    **`RESERVED_SLUGS` is subtracted, and leaving it out was a real hole.**
    `settings` is eight characters of the slug alphabet, so the shape test says
    yes — and `/settings` is registered before `/{slug}`, so routing then hands
    it to the admin page. Measured before this line existed: `/settings` on the
    short host answered 302 to the login form while every other admin path
    answered 404. That is the same collision `RESERVED_SLUGS` closes on the
    minting side, arriving on the serving side, and it costs nothing to close
    here because no slug is ever minted from that set.
    """
    if not path or not path.startswith("/"):
        return False
    candidate = path[1:]
    return bool(SLUG_RE.match(candidate)) and candidate not in RESERVED_SLUGS


def for_counting(template: Optional[str]) -> str:
    """The template as it will be *measured*, with the tag at its rendered width.

    `{link}` is eight septets in GSM-7 — `{` and `}` are extended characters —
    and it renders to seventeen on an eight-character domain. Counting the tag
    therefore under-quotes every recipient by nine characters, always in the same
    direction, and it puts a "Segments / msg" figure on screen that disagrees
    with the phone preview an inch to its right. That is the shape of 5d's
    Opt-outs headline: the rows were right and the number above them was not.

    Returned unchanged when the tag is absent or no domain is configured. In the
    second case the literal `{link}` really is what the template measures — and
    the campaign is refused at creation anyway, so there is nothing to quote.

    For counting only. Never send this; `mint()` and `render()` produce the real
    thing, and this is the same length by construction.
    """
    if not has_link_tag(template) or not configured():
        return template or ""
    return template.replace(LINK_TAG, placeholder_url())


# ─── The one-hop guarantee ──────────────────────────────────────────────────

_URL_START = re.compile(r"^https?://", re.IGNORECASE)


def validate_target(url: Optional[str]) -> str:
    """Return the target to store, or raise `LinkError` saying why not.

    Three refusals, and all three are the same rule: our link is the only hop.

      - not an http(s) URL at all — nothing to redirect to
      - a public shortener — following it is a second hop, and the carriers
        that filter those (see `phone.SHARED_SHORTENER_DOMAINS`) would be
        filtering *our* domain for pointing at one
      - a link on our own short domain — that is a chain by definition, and it
        is also how a loop gets built by accident
    """
    target = (url or "").strip()
    if not target:
        raise LinkError(NO_TARGET_ERROR)
    if not _URL_START.match(target):
        raise LinkError(
            "The link destination has to be a full web address starting with "
            "https:// — for example https://auctions4america.com/thursday."
        )

    host = re.sub(r"^https?://", "", target, flags=re.IGNORECASE)
    host = host.split("/")[0].split("?")[0].lower()
    host = host[4:] if host.startswith("www.") else host

    if any(host == d or host.endswith("." + d) for d in SHARED_SHORTENER_DOMAINS):
        raise LinkError(
            f"{host} is a shared link shortener, so the message would redirect "
            f"twice. Carriers filter chained links hard. Point the link at the "
            f"page itself."
        )
    if configured() and (host == domain() or host.endswith("." + domain())):
        raise LinkError(
            "The link destination is itself a short link, which would make the "
            "message redirect twice. Point it at the page the buyer should land on."
        )
    return target


# ─── Minting ────────────────────────────────────────────────────────────────

def _fresh_slugs(db: Session, count: int) -> List[str]:
    """`count` slugs that collide with nothing in the batch or in the table.

    Checked in one `IN` query per attempt rather than one per slug: a full send
    mints about 4,200 of these and a per-row existence check is the N+1 that
    makes a campaign take minutes to create.
    """
    chosen: set = set()
    for _ in range(5):
        while len(chosen) < count:
            candidate = "".join(secrets.choice(SLUG_ALPHABET)
                                for _ in range(SLUG_LENGTH))
            if candidate not in RESERVED_SLUGS:
                chosen.add(candidate)
        taken = {slug for (slug,) in
                 db.query(ShortLink.slug).filter(ShortLink.slug.in_(list(chosen))).all()}
        if not taken:
            return list(chosen)
        logger.warning("short-link slug collision on %d of %d; regenerating",
                       len(taken), count)
        chosen -= taken
    # Five rounds of collisions on a 1.1e12 keyspace is not bad luck, it is a
    # broken generator. Fail loudly rather than mint something ambiguous.
    raise LinkError("Could not generate unique short links. Nothing was created.")


def mint(db: Session, *, campaign_id: int, target_url: str,
         contacts: Sequence) -> List[Tuple[object, ShortLink]]:
    """One link per contact, unsaved-but-added, paired with its contact.

    Returns `[(contact, link), ...]` in the order given, so the caller can
    render each recipient's own URL into their own message. Nothing is committed
    here — the caller owns the transaction, because a campaign whose creation
    fails must not leave links behind pointing at a target nobody chose.

    `target_url` is validated by the caller before this is reached;
    `validate_target()` is where the refusals live and the wording with them.
    """
    if not configured():
        raise LinkError(NO_DOMAIN_ERROR)
    if not contacts:
        return []

    stamp = datetime.now().isoformat()
    slugs = _fresh_slugs(db, len(contacts))
    pairs = []
    for contact, slug in zip(contacts, slugs):
        link = ShortLink(
            slug=slug,
            campaign_id=campaign_id,
            contact_id=getattr(contact, "id", None),
            target_url=target_url,
            created_at=stamp,
        )
        db.add(link)
        pairs.append((contact, link))
    return pairs


# ─── Resolving and recording ────────────────────────────────────────────────

def resolve(db: Session, slug: str) -> Optional[ShortLink]:
    """The link for this slug, or None. Never creates one.

    The shape check happens before the query on purpose: the route is
    unauthenticated and sits at the root of a domain that will be scanned, and
    there is no reason to put `/wp-login.php` through an indexed lookup.
    """
    if not slug or not SLUG_RE.match(slug):
        return None
    return db.query(ShortLink).filter(ShortLink.slug == slug).first()


def _seconds_after_send(db: Session, link: ShortLink,
                        now: datetime) -> Optional[int]:
    """How long after the carrier accepted the message this click arrived.

    None whenever it cannot be known — no message row, no `sent_at`, a stamp
    that will not parse. None is not evidence of anything and the timing rule
    does not run on it; guessing a zero here would file every click on a draft
    link as a robot.
    """
    if not link.message_id:
        return None
    from app.models.sms_message import SMSMessage      # local: keeps this module
    message = db.get(SMSMessage, link.message_id)      # importable from tests
    if not message or not message.sent_at:
        return None
    try:
        sent = datetime.fromisoformat(message.sent_at)
    except (TypeError, ValueError):
        return None
    return int((now - sent).total_seconds())


def record_click(db: Session, link: ShortLink, user_agent: Optional[str],
                 now: Optional[datetime] = None) -> Optional[LinkClick]:
    """Log one arrival. Returns the row, or None if anything went wrong.

    **This function does not raise.** It is called from the redirect route, and
    a failed write must not stop somebody reaching the auction — the click data
    is worth less than the click. Everything it can throw is caught here rather
    than at the call site, so a future second caller inherits the guarantee
    instead of having to remember it.
    """
    now = now or datetime.now()
    try:
        after = _seconds_after_send(db, link, now)
        is_bot, reason = click_classifier.classify(user_agent, after)

        stamp = now.isoformat()
        click = LinkClick(
            short_link_id=link.id,
            clicked_at=stamp,
            # Bounded: a user agent is attacker-controlled free text on an
            # unauthenticated route, and this column is read back onto a screen.
            user_agent=(user_agent or "")[:500] or None,
            seconds_after_send=after,
            is_bot=1 if is_bot else 0,
            bot_reason=reason,
        )
        db.add(click)

        if is_bot:
            link.bot_click_count = (link.bot_click_count or 0) + 1
        else:
            link.click_count = (link.click_count or 0) + 1
            # First/last are human-click stamps. A scanner opening the link
            # before the handset rang is not "when this buyer looked at it",
            # which is the only thing anyone reads these for.
            if not link.first_clicked_at:
                link.first_clicked_at = stamp
            link.last_clicked_at = stamp

        db.commit()
        return click
    except Exception as e:                      # noqa: BLE001 — see the docstring
        logger.error("click not recorded for link %s: %s",
                     getattr(link, "slug", "?"), e)
        try:
            db.rollback()
        except Exception:                       # noqa: BLE001
            pass
        return None


def clicks_for_messages(db: Session, message_ids: Iterable[int]) -> dict:
    """{message_id: {clicks, bot_clicks, last_clicked_at}} for a page of rows.

    One query for the whole page. The per-contact history screen renders 50
    messages and a lookup per row is the N+1 that leaves every assertion in the
    suite passing while the page becomes fifty round-trips.
    """
    ids = [i for i in message_ids if i]
    if not ids:
        return {}
    rows = (db.query(ShortLink.message_id, ShortLink.click_count,
                     ShortLink.bot_click_count, ShortLink.last_clicked_at)
            .filter(ShortLink.message_id.in_(ids)).all())
    out = {}
    for message_id, clicks, bots, last in rows:
        bucket = out.setdefault(message_id, {"clicks": 0, "bot_clicks": 0,
                                             "last_clicked_at": None})
        bucket["clicks"] += clicks or 0
        bucket["bot_clicks"] += bots or 0
        if last and (bucket["last_clicked_at"] or "") < last:
            bucket["last_clicked_at"] = last
    return out
