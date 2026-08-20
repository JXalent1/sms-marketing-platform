"""The Today screen: the page and the JSON behind it.

HTTP only. Every figure comes from dashboard_service — there is no query and no
arithmetic in this file, which is what keeps "what the screen says" answerable
by reading one module instead of three.

The page is rendered server-side rather than fetched. Today is the first screen
of the morning and the one he refreshes between lots; a client-side fetch means
the category cards — the whole point of the screen — arrive after a blank frame,
and arrive not at all if the request fails.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.core.auth import require_auth
from app.core.database import get_db
from app.routers.pages import templates, shell_context
from app.services import dashboard_service

router = APIRouter()

# Both paths render the same screen: "/" is where a bookmark lands and
# "/dashboard" is where the login redirect and the nav point.
PATHS = ("/", "/dashboard")


async def today_page(request: Request, db: Session = Depends(get_db),
                     user: str = Depends(require_auth)):
    return templates.TemplateResponse(
        request, "today.html",
        {"active_page": "dashboard", **shell_context(db),
         **dashboard_service.dashboard(db)},
    )


for path in PATHS:
    router.add_api_route(path, today_page, methods=["GET"],
                         response_class=HTMLResponse)


api_router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@api_router.get("")
async def dashboard_data(db: Session = Depends(get_db),
                         user: str = Depends(require_auth)):
    """The same payload the page renders, for polling and for tests."""
    return dashboard_service.dashboard(db)
