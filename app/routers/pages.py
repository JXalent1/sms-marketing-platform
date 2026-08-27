"""HTML page routes and the login flow.

Every page below sits behind require_auth. The only unauthenticated routes in
the entire app are /login, /logout, /health and the provider webhooks.
"""

from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session
from app.core.auth import (
    require_auth, verify_login, create_session_token, get_current_user,
    get_client_ip, SESSION_COOKIE_NAME, SESSION_MAX_AGE,
)
from app.core.config import settings
from app.core.database import get_db
from app.core import branding
from app.services import billing_service, contact_query_service, monitoring_service
from app.sms.factory import send_mode, active_sender_number
import os
import logging
import re

logger = logging.getLogger("pages")

router = APIRouter()
limiter = Limiter(key_func=get_remote_address)

templates = Jinja2Templates(
    directory=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates")
)
branding.install(templates)

# "/" and "/dashboard" are not here: the Today screen renders server-side from
# dashboard_service and lives in routers/dashboard.py.
PAGES = [
    ("/campaigns", "campaigns.html", "campaigns"),
    ("/contacts", "contacts.html", "contacts"),
    ("/usage", "usage.html", "usage"),
    ("/blocklist", "blocklist.html", "blocklist"),
    ("/settings", "settings.html", "settings"),
]


# ─── Auth (unprotected) ─────────────────────────────────────────────────────

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if get_current_user(request):
        return RedirectResponse(url="/dashboard", status_code=302)
    # Request-first signature — required by Starlette >= 0.29.
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login", response_class=HTMLResponse)
@limiter.limit("10/minute")
async def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    if not verify_login(username, password):
        # Log the attempt but never the attempted password.
        logger.warning(f"Failed login from {get_client_ip(request)} (username: {username})")
        return templates.TemplateResponse(
            request, "login.html",
            {"error": "Invalid username or password"},
            status_code=401,
        )

    response = RedirectResponse(url="/dashboard", status_code=302)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=create_session_token(username),
        httponly=True,                       # JS can't read it
        secure=settings.COOKIE_SECURE,       # HTTPS only in production
        samesite="lax",
        max_age=SESSION_MAX_AGE,
    )
    logger.info(f"Login OK: {username} from {get_client_ip(request)}")
    return response


@router.get("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response


@router.get("/health")
async def health():
    """Unauthenticated on purpose — uptime monitors need it.

    It now answers "can this box send?" as well as "is it up?", because an
    external uptime monitor is the only alert channel that still works when the
    carrier does not. `agent/notify.sh` reads the same credential it would be
    warning about, and an SMS alert about being unable to send SMS is
    self-defeating — decision 002 rules that out explicitly.

    **Still HTTP 200 while degraded, deliberately.** `deployment/deploy.sh`
    health-checks this endpoint after the restart and rolls the release back on
    a non-200. Returning 503 for a bad carrier credential would therefore roll
    back every deploy on a degraded box, including the deploy that fixes it.
    The app is up; it is sending that is broken, and the two are different
    facts. Point the monitor at the `sending_ok` field — see docs/API.md.

    `config_ok` is the second thing worth a monitor. A carrier can refuse a
    destination for a setting on our own sending account — a region that was
    never enabled — and until session 5g the product's only response was to
    block the recipient forever for a problem on our side. It cannot be paged
    over SMS for the reason above, and it cannot be paged per event either:
    these arrive on the delivery webhook, thousands at a time. So it is raised
    as a row and reported here, alongside the send state a monitor is already
    watching. See app/services/monitoring_service.py.

    It reads that row without `Depends(get_db)`, deliberately. A dependency that
    raises means this handler never runs — the same rollback trap as a 503, one
    layer up. `active_config_alerts()` opens its own session and returns [] on
    anything going wrong, so a database this endpoint cannot reach costs the
    configuration signal and nothing else.

    White-label: `reason` is send_mode().detail, `config_issues` are wordings
    owned by `compliance.CONFIGURATION_ALERT_DETAIL`, and both name no carrier.
    The SDK exception behind either stays in the log.
    """
    mode = send_mode()
    degraded = mode.key == "unavailable"
    alerts = monitoring_service.active_config_alerts()
    return {
        "status": "degraded" if degraded else "healthy",
        "sending_ok": not degraded,
        "send_mode": mode.key,
        "reason": mode.detail if degraded else None,
        "config_ok": not alerts,
        "config_issues": alerts,
    }


# ─── The application shell ──────────────────────────────────────────────────

_NON_DIGITS = re.compile(r"\D")


def mask_sender_number(raw: str) -> str | None:
    """"+19545554120" -> "+1 954 ••• 4120". None when no number is assigned.

    Masked because the shell renders it on every page, and the client's own
    screen is shoulder-surfed in a saleroom. The area code stays because it is
    how he recognises the number as his; the last four because it is what he
    reads out to anyone asking. Nothing here identifies who carries the number,
    and nothing may be added that does.

    Anything that isn't a phone number — the console provider's "(dry run)",
    an unset value — returns None so the template shows its own empty state
    rather than a mangled string.
    """
    digits = _NON_DIGITS.sub("", raw or "")
    if len(digits) < 10:
        return None
    country = digits[:-10] or "1"
    return f"+{country} {digits[-10:-7]} ••• {digits[-4:]}"


def shell_context(db: Session) -> dict:
    """The values base.html renders on every authenticated page.

    Here rather than in each handler: six routes duplicating this query is six
    places to update when the shell gains a figure, and the reason the prior
    build's nav counts disagreed with its own dashboard.
    """
    cycle_start, cycle_end, _, _ = billing_service.get_billing_cycle()
    _, segments = billing_service.compute_usage(db, cycle_start, cycle_end)

    # Read from the live provider, not from settings: get_provider() falls back
    # to console when a carrier can't initialise, and the pill has to say what
    # is actually happening rather than what .env intended.
    #
    # Three states, not two. Deriving the label from `name != "console"` made a
    # failed carrier and a chosen dry run render the same amber pill, which is
    # how a production box sat unable to send while every screen looked normal.
    # The wording comes from send_mode() so this pill and the Settings page
    # cannot disagree, and the provider's name never reaches the template.
    mode = send_mode()

    return {
        "segments_this_month": segments,
        "sender_number": mask_sender_number(active_sender_number()),
        "send_mode": mode.key,
        "send_mode_label": mode.label,
        "send_mode_detail": mode.detail,
        "send_mode_live": mode.key == "live",
        "send_mode_degraded": mode.key == "unavailable",
    }


# ─── Pages (all authenticated) ──────────────────────────────────────────────

# Extra server-rendered context, per page. Contacts renders its category tabs on
# the first paint rather than after a fetch: the tabs are how the screen is
# navigated, and a row of empty buttons that fills in a moment later is the
# thing he clicks before it is ready.
PAGE_CONTEXT = {
    "contacts": lambda db: {"category_tabs": contact_query_service.category_tabs(db)},
}


def _make_page(template: str, active: str):
    async def page(request: Request, db: Session = Depends(get_db),
                   user: str = Depends(require_auth)):
        extra = PAGE_CONTEXT.get(active)
        return templates.TemplateResponse(
            request, template,
            {"active_page": active, **shell_context(db),
             **(extra(db) if extra else {})},
        )
    return page


for path, template_name, active_page in PAGES:
    router.add_api_route(path, _make_page(template_name, active_page),
                         methods=["GET"], response_class=HTMLResponse)
