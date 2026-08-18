"""HTML page routes and the login flow.

Every page below sits behind require_auth. The only unauthenticated routes in
the entire app are /login, /logout, /health and the provider webhooks.
"""

from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from slowapi import Limiter
from slowapi.util import get_remote_address
from app.core.auth import (
    require_auth, verify_login, create_session_token, get_current_user,
    get_client_ip, SESSION_COOKIE_NAME, SESSION_MAX_AGE,
)
from app.core.config import settings
from app.core import branding
import os
import logging

logger = logging.getLogger("pages")

router = APIRouter()
limiter = Limiter(key_func=get_remote_address)

templates = Jinja2Templates(
    directory=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates")
)
branding.install(templates)

PAGES = [
    ("/", "dashboard.html", "dashboard"),
    ("/dashboard", "dashboard.html", "dashboard"),
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
    """Unauthenticated on purpose — uptime monitors need it. Returns no data."""
    return {"status": "healthy"}


# ─── Pages (all authenticated) ──────────────────────────────────────────────

def _make_page(template: str, active: str):
    async def page(request: Request, user: str = Depends(require_auth)):
        return templates.TemplateResponse(request, template, {"active_page": active})
    return page


for path, template_name, active_page in PAGES:
    router.add_api_route(path, _make_page(template_name, active_page),
                         methods=["GET"], response_class=HTMLResponse)
