"""Subscribe, come back from checkout, and receive Stripe's webhook.

HTTP only, as every router here is: validate, delegate, serialise. Every
decision about what a session means lives in `app/services/stripe_billing.py`,
including — especially — the guard that decides whether a completed checkout
belongs to this client at all.

Three surfaces take an identifier from outside this process:

  * `POST /webhooks/stripe` is unauthenticated by necessity, because Stripe has
    no login. Its defence is the signature, and it **fails closed**: with no
    signing secret configured the payload is ignored rather than trusted.
  * `GET /billing/success` is a plain URL anybody can type with any session id.
    It gets the identical `session_is_ours()` guard, because a guard on one
    path is a guard on one path.
  * `POST /api/billing/checkout` is authenticated like the rest of the app.

Stripe's name appears here on purpose. The white-label rule covers the SMS
carrier — see `sessions/session-B1.md` A8 — and hiding a payment processor from
the person typing his card into it would be a defect, not a policy.
"""

import logging
import os

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core import branding
from app.core.auth import require_auth
from app.core.config import settings
from app.core.database import get_db
from app.services import stripe_billing, stripe_tiers

logger = logging.getLogger("billing")

router = APIRouter(tags=["billing"])

templates = Jinja2Templates(
    directory=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "templates")
)
branding.install(templates)

SUCCESS_PATH = "/billing/success"
CANCEL_PATH = "/subscribe"

# What the success page says once the guard has run. Three outcomes, three
# sentences: a refusal that does not say what happened reads as a broken tool,
# and this one is read by a client who has just typed his card in.
CONFIRMED = ("Thank you — your card is on file and your account is set up. "
             "Your first invoice settles the outstanding balance; after that "
             "you are billed once a month for what you actually send.")
UNRECOGNISED = ("We could not match that checkout to this account. If you have "
                "just paid, nothing is wrong with the payment — refresh this "
                "page in a minute, or check your email for the receipt.")


def _shell(db: Session) -> dict:
    # Imported here rather than at module scope: `pages` imports this module's
    # sibling services for /health, and a top-level import in both directions is
    # a cycle waiting for the next edit.
    from app.routers.pages import shell_context
    return shell_context(db)


# ─── The page ───────────────────────────────────────────────────────────────


@router.get("/subscribe", response_class=HTMLResponse)
async def subscribe_page(request: Request, db: Session = Depends(get_db),
                         user: str = Depends(require_auth)):
    """Safe to deploy before Stripe exists.

    With no keys the page renders a plain notice and no button. That is the
    order these two things actually happen in — the app ships, then the Stripe
    account is set up — and a page that 500s until somebody edits `.env` would
    make the deploy the blocker instead of the setup.
    """
    return templates.TemplateResponse(
        request, "subscribe.html",
        {"active_page": "subscribe", "billing": stripe_billing.account_status(db),
         **_shell(db)})


@router.get("/api/billing/status")
async def billing_status(db: Session = Depends(get_db),
                         user: str = Depends(require_auth)):
    status = stripe_billing.account_status(db)
    status["pricing"] = stripe_tiers.tier_verdict()
    return status


@router.post("/api/billing/checkout")
async def start_checkout(request: Request, db: Session = Depends(get_db),
                         user: str = Depends(require_auth)):
    """Open a Checkout Session, or say plainly that this box has no Stripe.

    503 and not 500: "the service is not configured here" is a different fact
    from "the service broke", and the page renders them differently. The client
    on a box with no keys is not looking at a bug.
    """
    if not stripe_billing.configured():
        return JSONResponse({"error": stripe_billing.NOT_CONNECTED}, status_code=503)

    base = settings.PUBLIC_BASE_URL.rstrip("/")
    try:
        session = stripe_billing.create_checkout_session(
            success_url=f"{base}{SUCCESS_PATH}?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{base}{CANCEL_PATH}",
        )
    except Exception as exc:
        # The SDK's text stays in the log. It carries request ids and parameter
        # names, and the client can act on none of it.
        logger.error("Could not create a Checkout Session: %s", exc)
        return JSONResponse({"error": stripe_billing.CHECKOUT_FAILED}, status_code=502)

    url = session.get("url") if isinstance(session, dict) else getattr(session, "url", None)
    session_id = (session.get("id") if isinstance(session, dict)
                  else getattr(session, "id", None))
    logger.info("Checkout session %s opened", session_id)
    return {"url": url, "session_id": session_id}


@router.post("/api/billing/tier-check")
async def tier_check(db: Session = Depends(get_db), user: str = Depends(require_auth)):
    """Run A2's comparison now and store the verdict `/health` reports."""
    return stripe_tiers.check_tier_drift(db)


# ─── Coming back from checkout ──────────────────────────────────────────────


@router.get(SUCCESS_PATH, response_class=HTMLResponse)
async def checkout_success(request: Request, session_id: str = "",
                           db: Session = Depends(get_db),
                           user: str = Depends(require_auth)):
    """The redirect target, and it goes through the same handler the webhook does.

    Anybody can hit this URL with any session id, so nothing may be stored on
    the strength of one. The decision is made in exactly one place —
    `handle_checkout_completed()`, which applies `session_is_ours()` itself — and
    the tempting edit here is to skip it: we already hold the session object, so
    why not write the customer straight out? That edit is the whole of A5.

    **The `session_is_ours()` call in the condition below is not the guard.** It
    saves a `retrieve_checkout_session()` round-trip for an id that is not ours,
    and it says on the face of the route what the route requires. The guard is
    one layer down and stays there, because two copies of a rule about whose
    card gets charged is one copy too many. Naming that here rather than leaving
    it to look like belt and braces: a comment that outlives its reason gets
    defended as a rule.

    This path exists at all because a webhook can be late, and a client staring
    at "not set up yet" thirty seconds after paying will not wait for it.
    """
    stored = {"stored": False, "reason": "no session id"}
    if session_id and stripe_billing.session_is_ours(session_id):
        try:
            session = stripe_billing.api().retrieve_checkout_session(session_id)
            stored = stripe_billing.handle_checkout_completed(
                db, {"data": {"object": session}})
        except Exception as exc:
            logger.error("Could not confirm checkout session %s: %s", session_id, exc)
            stored = {"stored": False, "reason": "could not read the session"}
    elif session_id:
        logger.info("Checkout session %s on the success page does not carry this "
                    "product's metered price — nothing stored", session_id)
        stored = {"stored": False, "reason": "not this product"}

    return templates.TemplateResponse(
        request, "subscribe.html",
        {"active_page": "subscribe",
         "billing": stripe_billing.account_status(db),
         "checkout_message": CONFIRMED if stored.get("stored") else UNRECOGNISED,
         "checkout_confirmed": bool(stored.get("stored")),
         **_shell(db)})


# ─── The webhook ────────────────────────────────────────────────────────────


@router.post("/webhooks/stripe")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    """`checkout.session.completed`, verified and then filtered.

    Status codes are chosen for what Stripe does with them. A rejected signature
    is 400 and is not retried, because a retry of an unsigned payload is a retry
    of the same refusal. An event for another product is 200 — it is not ours,
    there is nothing to retry, and asking Stripe to redeliver it forever would
    be noise on somebody else's integration. An internal failure is 500 so the
    redelivery actually helps.
    """
    payload = await request.body()
    event = stripe_billing.verify_webhook(
        payload, request.headers.get("stripe-signature"))
    if event is None:
        return JSONResponse({"error": "signature not verified"}, status_code=400)

    event_type = (event.get("type") if isinstance(event, dict)
                  else getattr(event, "type", None))
    if event_type != "checkout.session.completed":
        return {"status": "ignored", "type": event_type}

    try:
        outcome = stripe_billing.handle_checkout_completed(db, event)
    except Exception as exc:
        logger.error("checkout.session.completed handler failed: %s", exc)
        return JSONResponse({"error": "handler failed"}, status_code=500)

    if outcome.get("stored"):
        # The price this subscription bills against is now live, so verify the
        # tier the moment there is something to verify. It is the one point in
        # the flow where the check is guaranteed to matter and guaranteed not to
        # be on a hot path.
        try:
            stripe_tiers.check_tier_drift(db)
        except Exception as exc:                # pragma: no cover — defensive
            logger.error("Tier check after checkout failed: %s", exc)
    return {"status": "ok", **{k: v for k, v in outcome.items() if k != "stored"},
            "stored": bool(outcome.get("stored"))}
