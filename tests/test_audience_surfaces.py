"""Session 5i A7: no audience surface names a category, proved at runtime.

A grep cannot prove this. Three separate times in this project a measurement
script has counted **prose describing a rule** as a violation of it — a docstring,
a comment stating the layering rule, a webhook module import in `main.py`. So
this renders the routes and reads what actually comes back, in the shape
`test_whitelabel.py` established for the carrier's name, and reuses that file's
own route discovery rather than writing a second one.

## What this proves, and what it does not

Categories are **hidden, not removed** — that is the ruling this whole session
rests on, and it means some surfaces keep the word on purpose. The exemptions
below are those surfaces, each with the clause of `sessions/session-5i.md` that
retains it. Every one of them is checked for still being *needed*
(`test_no_exemption_has_gone_stale`), so the list cannot quietly outlive its
reasons the way a denylist does.

What is left after the exemptions is exactly the audience surface: the composer
and its two payloads, the Contacts screen, the Today screen and its payload, the
lists endpoint. That is the set the client picks an audience from, and the set
the session exists to change.

`test_whitelabel.py`'s `EXEMPT_PATHS` is the precedent for exempting by name with
a reason attached; the discovery itself is still the app's own route table, so a
route added tomorrow is scanned the moment it exists.
"""

import os
import re

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import contact_service
from tests.test_whitelabel import _fill, _get_routes, PATH_VALUES

PASSWORD = os.environ["ADMIN_PASSWORD"]

# Case-insensitive, and a stem rather than two words: `category`, `categories`,
# `categoryChoices`, `crossCategory`, `category_label` and `data-category-id` are
# all the same leak, and the ones that bit in this codebase were assembled —
# a JS identifier, a JSON key a template reads — rather than written as prose.
CATEGORY_RE = re.compile(r"categor", re.IGNORECASE)

# Routes owned by these modules are not scanned. By module, not by path string,
# so a new route in one of them is covered without editing this list.
EXEMPT_MODULES = {
    # A8. The prospect review queue keeps its category and still requires one to
    # promote — that is P2's taxonomy, not the list model. The client's complaint
    # was about where a list goes, not about the review queue.
    "app.routers.prospects",
    # The taxonomy's own CRUD. `/api/categories` is what the prospect promote
    # dropdown is filled from, so A8 keeps it; A3 and A5 removed every other
    # caller. Retained, not removed — nothing came out of the schema.
    "app.routers.categories",
    # A campaign report renders `category_label` with `audience_label` as its
    # fallback, and `sessions/session-5i.md`'s file list puts `report_service`
    # and `history_service` in scope "for one reason only" — the SENT_STATUSES
    # consolidation — and says explicitly that anything else there is a finding
    # for status.md rather than an edit. So the key stays in the payload and
    # these two routes stay out of the sweep.
    "app.routers.reports",
}

# Individual routes, where the owning module serves more than one screen and so
# cannot be the unit of exemption. Each names the clause that keeps it.
EXEMPT_PATHS = {
    # A8 again. `/prospects` is served by `pages.page`, the shared handler behind
    # six screens, so this one cannot be subtracted by module.
    "/prospects":
        "A8 — the prospect queue keeps its category chip and its promote rule",
    # `contact_query_service` builds the per-row chips and the export column.
    # It is outside this session's file list, and after A5 no screen renders
    # either: the chips column is gone and the tab row with it.
    "/api/contacts":
        "A5 — per-row chip data with no renderer; contact_query_service is out of scope",
    "/api/contacts/export.csv":
        "A5 — the export's `categories` column; contact_query_service is out of scope",
    # The two report screens read `category_label` from the reports payload with
    # `audience_label` behind it. Same clause as `app.routers.reports` above.
    "/history":
        "file list — history.html is not in scope; the fallback already works",
    "/history/{campaign_id}":
        "file list — campaign-report.html is not in scope; the fallback already works",
}


# 954-555-32xx: this module's slice of the reserved fiction block.
PROBE_PHONE = "+19545553201"


@pytest.fixture(scope="module")
def client():
    """Logged in, with the shared PATH_VALUES filled, and with one contact.

    The sample path values are borrowed from `test_whitelabel.py` rather than
    rebuilt: they are what stop a route being scanned on a 404 body and passing
    by containing nothing.

    The contact matters for the same reason. `/api/contacts` carries a
    `categories` key **per row**, so on an empty table it carries nothing at all
    — and this module has to run on its own, because `accept-5i.sh` runs each
    criterion in isolation and that is how they will fail. Without a row here,
    the exemption-staleness check would report the `/api/contacts` exemption as
    unnecessary in isolation and necessary in a full run.
    """
    from app.core.database import SessionLocal
    from app.services import contact_service as cs

    db = SessionLocal()
    try:
        cs.upsert_contact(db, phone=PROBE_PHONE, full_name="Surface probe",
                          source="test")
    finally:
        db.close()

    c = TestClient(app)
    login = c.post("/login", data={"username": "admin", "password": PASSWORD})
    assert login.status_code in (200, 302), (
        f"login failed with {login.status_code} — the sweep would have run "
        f"against 401 bodies and passed vacuously")

    if not PATH_VALUES:
        categories = c.get("/api/categories").json()["categories"]
        PATH_VALUES["category_id"] = categories[0]["id"]
        campaigns = c.get("/api/campaigns?limit=1").json()["campaigns"]
        PATH_VALUES["campaign_id"] = campaigns[0]["id"] if campaigns else 999999
        lists = c.get("/api/lists").json().get("lists") or []
        list_selectors = [a["selector"] for a in lists if a["kind"] == "list"]
        PATH_VALUES["list_id"] = (int(list_selectors[0].split(":", 1)[1])
                                  if list_selectors else 999999)
        contacts = c.get("/api/contacts?page=1").json().get("contacts") or []
        PATH_VALUES["contact_id"] = contacts[0]["id"] if contacts else 999999
        PATH_VALUES["slug"] = "aaaaaaaa"
    yield c

    # The suite shares one database and `test_smoke` asserts an exact
    # `sent_count` against audience "all".
    from app.models.contact import Contact
    db = SessionLocal()
    try:
        db.query(Contact).filter(Contact.phone == PROBE_PHONE).delete(
            synchronize_session=False)
        db.commit()
    finally:
        db.close()


def _module_of(path: str) -> str:
    for route in app.routes:
        if getattr(route, "path", "") == path:
            return getattr(getattr(route, "endpoint", None), "__module__", "")
    return ""


def scanned_routes():
    """Every client-facing GET route the sweep is responsible for."""
    return [p for p in _get_routes()
            if p not in EXEMPT_PATHS and _module_of(p) not in EXEMPT_MODULES]


# ─── The sweep ──────────────────────────────────────────────────────────────

def test_the_sweep_covers_the_audience_surface(client):
    """The sweep is worth nothing if the exemptions have eaten it.

    Named rather than counted: a floor of "at least N routes" drifts, and what
    this session is actually about is these six surfaces. `/api/lists` and
    `/api/campaigns/audiences` are the picker itself; the three pages are where
    he reads it back.
    """
    covered = set(scanned_routes())
    for path in ("/campaigns", "/contacts", "/dashboard", "/",
                 "/api/lists", "/api/campaigns/audiences",
                 "/api/campaigns", "/api/dashboard"):
        assert path in covered, f"{path} fell out of the audience sweep"


def test_no_audience_surface_names_a_category(client):
    """A7. Every remaining client-facing GET route, rendered and read."""
    offenders = []
    for path in scanned_routes():
        response = client.get(_fill(path))
        found = CATEGORY_RE.search(response.text)
        if found:
            start = max(0, found.start() - 60)
            offenders.append(f"{path} -> …{response.text[start:found.end() + 60]!r}")
    assert not offenders, (
        "a category reached a client-facing surface:\n  " + "\n  ".join(offenders))


def test_the_sweep_actually_fires(client):
    """The scan's own first version. Re-run against a case whose answer is known.

    A green check whose script is broken is worse than no check, because it is
    quoted as evidence. `/api/categories` is exempt *because* its body names
    categories — so pointing the same matcher at it must find one.
    """
    body = client.get("/api/categories").text
    assert CATEGORY_RE.search(body), (
        "the matcher found nothing in a body that is entirely about categories "
        "— the sweep above is proving nothing")


def test_no_exemption_has_gone_stale(client):
    """Every exemption still names a surface that needs one.

    An exemption list is a denylist and denylists rot: the route is renamed, the
    reason is fixed elsewhere, and the entry stays behind quietly widening the
    hole. This fails when an exempted route stops carrying the word, which is the
    moment the entry should be deleted rather than the moment somebody notices.
    """
    stale = []
    for path in sorted(EXEMPT_PATHS):
        assert path in _get_routes(), f"{path} is exempt and no longer exists"
        if not CATEGORY_RE.search(client.get(_fill(path)).text):
            stale.append(path)
    assert not stale, (
        f"these exemptions are no longer needed and should be deleted: {stale}")


def test_the_prospects_exemption_is_doing_real_work(client):
    """Take the prospects router out and the sweep must fail.

    An exemption that changes nothing is an exemption that is hiding nothing, and
    a sweep whose exception is inert is a sweep that never looked at that surface.
    """
    covered = [p for p in _get_routes()
               if p not in EXEMPT_PATHS
               and _module_of(p) not in (EXEMPT_MODULES - {"app.routers.prospects"})]
    hits = [p for p in covered if CATEGORY_RE.search(client.get(_fill(p)).text)]
    assert hits, (
        "removing app.routers.prospects from the subtraction changed nothing — "
        "either the sweep is not reaching those routes or they no longer carry "
        "a category, and in the second case the exemption should be deleted")


# ─── A8: the prospect queue is deliberately untouched ───────────────────────

def test_the_prospect_queue_still_has_its_category_chip(client):
    """Criterion 7. Asserted so the next session cannot remove it by accident.

    The category model is retained as the **prospecting taxonomy** — it is what
    P2's per-category radii and `prospect_scoring.py` are keyed on. A prospect
    promoted into Food Service becomes a contact tagged `food_service`, reachable
    through the pinned entry and through any list he is later put on, exactly as
    before. This is not an inconsistency to tidy up.
    """
    page = client.get("/prospects").text
    assert 'id="promoteCategory"' in page, (
        "the prospect promote control lost its category — that is P2's taxonomy, "
        "and removing it is a product decision and a separate session")
    assert "/api/categories" in page, "the promote dropdown has nothing to fill it"


def test_promoting_a_prospect_still_requires_a_category(client):
    """The rule, not just the control. A screen can keep a dropdown it ignores."""
    page = client.get("/prospects").text
    assert "Pick the category these buyers belong to." in page

    response = client.post("/api/prospects/promote", json={"prospect_ids": [1]})
    assert response.status_code in (400, 422), response.text


def test_the_taxonomy_endpoints_are_retained(client):
    """A5: the endpoints stay and simply have no caller in the UI.

    Deleting them is schema-adjacent and this session does not do it. They are
    also what `test_no_exemption_has_gone_stale` above is guarding, from the
    other direction.
    """
    assert client.get("/api/categories").status_code == 200
    assert client.get("/api/contacts/categories").status_code == 200
    # The bulk routes are asserted on the route table rather than on a status
    # code: they answer 404 for a category that does not exist, which is the
    # same code a deleted route would answer with, so a status assertion here
    # would pass whether or not they still existed.
    registered = {getattr(r, "path", "") for r in app.routes}
    assert "/api/contacts/bulk/add-category" in registered
    assert "/api/contacts/bulk/remove-category" in registered


# ─── The pinned wording, on both surfaces ───────────────────────────────────

def test_the_pinned_wording_reaches_both_surfaces_from_one_constant(client):
    """A2. Two surfaces, one sentence-maker.

    `send_mode()` is the pattern and the reason: one module owns the client-safe
    wording so a second surface cannot invent a second spelling. Asserted through
    the constant rather than against the string, so a rename moves both together
    and a second literal anywhere fails here rather than on screen.
    """
    label = contact_service.ALL_BIDDERS_LABEL

    audiences = client.get("/api/campaigns/audiences").json()["audiences"]
    assert audiences[0]["label"] == label

    cards = client.get("/api/dashboard").json()["lists"]
    assert cards[0]["label"] == label

    # And the same words describe the same audience wherever it is resolved.
    from app.core.database import SessionLocal
    db = SessionLocal()
    try:
        assert contact_service.audience_label(db, "all") == label
    finally:
        db.close()
