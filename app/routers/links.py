"""The public redirect: one hop, from a slug to the auction.

The only unauthenticated route in the application that touches campaign data,
and the only one a recipient's handset ever reaches. Four properties, and each
of them is here because the alternative costs something real.

**No minting.** This resolves slugs and never creates one. Links are minted by
campaign creation, which is behind `require_auth`. An open redirector is found
and abused within weeks, and the price is the branded sending domain landing on
a carrier blocklist — far more than the feature is worth.

**One redirect, never a chain.** A single 302 straight to the stored target.
T-Mobile's code of conduct flags anything that redirects more than once, and
`link_service.validate_target()` refuses a target that would make this the first
hop of two.

**302, not 301, and `Cache-Control: no-store`.** A permanent redirect is cached
by the handset and by every proxy between here and it, so the second click never
arrives and the client's report quietly under-counts everyone who looked twice.

**It does not use `Depends(get_db)`.** Same reasoning as `/health` in
`pages.py`: a dependency that raises means the handler never runs, and there is
nothing left to catch it in. The session is opened here so that a database
problem produces a 404 page rather than a 500 from the framework, and so that a
*click-recording* failure cannot stop somebody reaching the auction —
`link_service.record_click()` cannot raise, and this is the caller that
guarantee exists for.

**No rate limit, deliberately.** Every other write route in this app is
limited; this one is read-mostly and its traffic arrives as a burst of thousands
within a minute of a blast, much of it from one carrier NAT. A limiter here
would drop real buyers at exactly the moment the campaign is working.

Registered last in `main.py` so `/{slug}` cannot shadow a real page. Starlette
matches in registration order, and `SLUG_RE` means a scanner asking for
`/wp-login.php` is refused before it reaches a query.
"""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse, RedirectResponse

from app.core.database import SessionLocal
from app.services import link_service

logger = logging.getLogger("links")
router = APIRouter()

# What somebody sees at a slug that does not resolve. Plain text and no brand:
# this is served on the short domain to whoever typed it, and an unknown slug is
# not an occasion to tell a stranger whose links these are.
UNKNOWN_LINK = "This link has expired or was mistyped."


@router.get("/{slug}", include_in_schema=False)
async def follow(slug: str, request: Request):
    """Resolve one slug and send the person to the auction."""
    # **One `finally`, one `close()`, and every return below it.** The first
    # draft returned early on an unknown slug from inside the try and closed the
    # session on the other two paths only — so 40 requests to a slug that does
    # not resolve left seven connections checked out, reclaimed only when the
    # garbage collector got round to it. This route sits at the root of a domain
    # that *will* be swept, so the leak is on the path a scanner takes and not
    # on the one a buyer takes. Same lesson as the browser contexts the
    # reference system leaked one per daily scrape, one layer over: the cleanup
    # goes in a `finally`, not on each way out.
    db = SessionLocal()
    target = None
    try:
        link = link_service.resolve(db, slug)
        if link is not None:
            target = link.target_url
            # Recording is a *second* try, inside this one and after the
            # destination is in hand. `record_click()` already promises not to
            # raise; this is the layer that does not depend on the promise,
            # because the person is going to the auction whatever the database
            # is doing and the click is worth less than the click-through.
            try:
                link_service.record_click(db, link,
                                          request.headers.get("user-agent"))
            except Exception as e:               # noqa: BLE001
                logger.error("click not recorded for %r: %s", slug, e)
    except Exception as e:                       # noqa: BLE001
        # A database this route cannot reach costs the click and the redirect,
        # and there is nothing better to offer than the same message. It must
        # not be a 500: the person is holding a phone, not a debugger.
        logger.error("short link %r could not be resolved: %s", slug, e)
        target = None
    finally:
        db.close()

    if target is None:
        return PlainTextResponse(UNKNOWN_LINK, status_code=404)

    return RedirectResponse(
        url=target, status_code=302,
        headers={"Cache-Control": "no-store, no-cache, must-revalidate",
                 # A recipient's link is not something to leak to the auction
                 # host's analytics. The slug identifies one buyer.
                 "Referrer-Policy": "no-referrer"},
    )
