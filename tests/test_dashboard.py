"""The Today screen: freshness, the hero, and the two charts.

The assertion this file exists for is the em dash. "Days since last send" is the
number the client schedules his week against, and a category that has never been
texted must not render as `0` — that reads as "texted today", it is the exact
opposite of the truth, and it would keep a whole niche from ever being picked.
`None` in the service, `—` on the page, verified end to end.

Everything this module creates, it removes. The suite runs against one database
with no rollback between tests, and `test_smoke` sends a campaign to audience
"all" and asserts an exact `sent_count` — a contact left behind here fails a
test three files away for a reason that looks nothing like this one.
"""

import html as html_module
import os
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient          # noqa: E402

from app.main import app                           # noqa: E402
from app.core.database import SessionLocal         # noqa: E402
from app.models.campaign import Campaign           # noqa: E402
from app.models.category import Category, ContactCategory   # noqa: E402
from app.models.contact import Contact             # noqa: E402
from app.models.sms_message import SMSMessage      # noqa: E402
from app.services import dashboard_service         # noqa: E402

PASSWORD = os.environ["ADMIN_PASSWORD"]

# 555-01xx is the reserved fiction block; 9545552xxx is this module's slice of
# it, distinct from every other test module's so they cannot collide.
PHONES = {slug: f"+195455520{n:02d}" for n, slug in enumerate(
    ["food_service", "equipment", "estates", "memorabilia", "general"], start=1)}

# Days ago each category was last texted, and the status that send landed in.
# `general`'s message failed, so it does not count as a send: a failed blast is
# not contact, and treating it as one is how a niche goes quiet unnoticed.
SEND_PLAN = {
    "food_service": (2, "delivered"),
    "equipment": (21, "sent"),
    "estates": (0, "delivered"),
    "memorabilia": None,                 # never texted → em dash
    "general": (3, "failed"),            # failed → also never texted
}

CAMPAIGN_NAME = "module-3b hero campaign"


def _purge(db):
    ids = [row.id for row in db.query(Contact).filter(Contact.phone.in_(PHONES.values()))]
    if ids:
        db.query(SMSMessage).filter(SMSMessage.contact_id.in_(ids)).delete(
            synchronize_session=False)
        db.query(ContactCategory).filter(ContactCategory.contact_id.in_(ids)).delete(
            synchronize_session=False)
        db.query(Contact).filter(Contact.id.in_(ids)).delete(synchronize_session=False)
    db.query(Campaign).filter(Campaign.name == CAMPAIGN_NAME).delete(
        synchronize_session=False)
    db.commit()


@pytest.fixture(scope="module")
def seeded():
    db = SessionLocal()
    try:
        _purge(db)
        categories = {c.slug: c for c in db.query(Category).all()}

        for slug, phone in PHONES.items():
            contact = Contact(phone=phone, full_name=f"Dashboard {slug}",
                              source="test", attributes={},
                              created_at=date.today().isoformat())
            db.add(contact)
            db.flush()
            db.add(ContactCategory(contact_id=contact.id,
                                   category_id=categories[slug].id,
                                   source="manual"))

            plan = SEND_PLAN[slug]
            if plan is None:
                continue
            days_ago, status = plan
            sent_at = (date.today() - timedelta(days=days_ago)).isoformat() + "T10:00:00"
            db.add(SMSMessage(campaign_id=None, contact_id=contact.id, phone=phone,
                              message="Tomorrow's sale, 10am.", status=status,
                              segments=1, sent_at=sent_at))

        db.add(Campaign(name=CAMPAIGN_NAME, message_template="Preview lot list",
                        audience="category:food_service", audience_label="Food Service",
                        status="draft", created_at=date.today().isoformat() + "T08:00:00"))
        db.commit()
        yield
    finally:
        _purge(db)
        db.close()


@pytest.fixture(scope="module")
def client(seeded):
    """One logged-in session for the module — POST /login is rate limited."""
    test_client = TestClient(app)
    response = test_client.post("/login", data={"username": "admin", "password": PASSWORD},
                                follow_redirects=False)
    assert response.status_code == 302, "login failed — every later assertion would be a 401"
    return test_client


def _cards_by_slug(payload):
    return {card["slug"]: card for card in payload["categories"]}


# ─── Days since last send ───────────────────────────────────────────────────

def test_days_since_last_send_is_computed_from_actual_sends(client):
    cards = _cards_by_slug(client.get("/api/dashboard").json())

    assert cards["food_service"]["days_since_last_send"] == 2
    assert cards["food_service"]["days_label"] == "2"
    assert cards["estates"]["days_since_last_send"] == 0
    assert cards["estates"]["days_label"] == "0"
    assert cards["equipment"]["days_since_last_send"] == 21


def test_never_texted_category_renders_an_em_dash_not_a_zero(client):
    cards = _cards_by_slug(client.get("/api/dashboard").json())

    # Never texted at all.
    assert cards["memorabilia"]["days_since_last_send"] is None
    assert cards["memorabilia"]["days_label"] == "—"
    assert cards["memorabilia"]["days_caption"] == "never texted"

    # Texted, but the send failed. Same answer, and for the same reason: nobody
    # in this category has actually heard from us.
    assert cards["general"]["days_since_last_send"] is None
    assert cards["general"]["days_label"] == "—"

    # And the distinction survives to the page, which is where it matters:
    # "never texted" is the caption only a card with no send can carry, and the
    # em dash is the figure printed above it.
    html = client.get("/dashboard").text
    assert "never texted" in html
    assert "—" in html


def test_staleness_threshold_flags_only_the_stale_category(client):
    payload = client.get("/api/dashboard").json()
    cards = _cards_by_slug(payload)

    assert payload["stale_days"] == 14
    assert cards["equipment"]["stale"] is True      # 21 days
    assert cards["food_service"]["stale"] is False  # 2 days
    # Never texted is not "stale" — it is a different state with a different fix,
    # and colouring it red would say we let it go quiet rather than never started.
    assert cards["memorabilia"]["stale"] is False


# ─── The page ───────────────────────────────────────────────────────────────

def test_dashboard_page_renders_every_category_label(client):
    for path in ("/", "/dashboard"):
        response = client.get(path)
        assert response.status_code == 200
        db = SessionLocal()
        try:
            labels = [row.label for row in db.query(Category).filter(Category.is_active == 1)]
        finally:
            db.close()
        assert len(labels) == 5
        for label in labels:
            # Escaped, because "Equipment & Machinery" reaches the page as
            # "Equipment &amp; Machinery" — the label is there and Jinja is
            # doing its job. Asserting the raw string would fail on the one
            # category whose name contains markup.
            assert html_module.escape(label) in response.text, \
                f"{label!r} missing from {path}"


def test_hero_falls_back_to_the_newest_draft(client):
    hero = client.get("/api/dashboard").json()["next_up"]
    assert hero is not None

    db = SessionLocal()
    try:
        # Asserted against the rule rather than against this module's fixture:
        # any other test file may leave a draft behind, and a hero test that
        # only passes when it runs alone is not testing the hero.
        newest = (db.query(Campaign).filter(Campaign.status == "draft")
                  .order_by(Campaign.created_at.desc(), Campaign.id.desc()).first())
        assert hero["name"] == newest.name

        ours = db.query(Campaign).filter(Campaign.name == CAMPAIGN_NAME).one()
        cards = dashboard_service.category_cards(db)
        card = dashboard_service._category_for_campaign(db, ours, cards)
        # No campaigns.category_id column yet in this worktree, so the category
        # comes out of the audience selector — the fallback the spec asked for.
        assert card["slug"] == "food_service"
        assert card["days_label"] == "2"
        assert dashboard_service.next_up(db, cards)["audience_count"] >= 1
    finally:
        db.close()


def test_chart_covers_fourteen_days_and_keeps_empty_days(client):
    chart = client.get("/api/dashboard").json()["chart"]

    assert chart["days"] == 14
    assert len(chart["bars"]) == 14
    assert chart["bars"][-1]["date"] == date.today().isoformat()

    # A day with no send is a bar of height 0 — present, not missing. The
    # template draws those as a faint rule so a gap and a quiet day differ.
    empty = [bar for bar in chart["bars"] if bar["segments"] == 0]
    assert empty, "expected at least one quiet day in the seed"
    assert all(bar["pct"] == 0 for bar in empty)
    assert any(bar["pct"] > 0 for bar in chart["bars"]), "no bar has height"


def test_tiles_report_segments_and_cost_without_naming_a_rate(client):
    tiles = {tile["key"]: tile for tile in client.get("/api/dashboard").json()["tiles"]}

    assert set(tiles) == {"delivered", "opt_outs", "segments", "cost"}
    assert tiles["cost"]["value"].startswith("$")
    assert "included" in tiles["segments"]["sub"]
    # The wholesale rate is ours, not his. It must not reach the screen in any
    # form — see the note on WHOLESALE_COST_PER_SEGMENT in config.
    assert "0.009" not in str(tiles)


def test_service_days_since_helper_never_returns_zero_for_never():
    assert dashboard_service._days_since(None) is None
    assert dashboard_service._days_since("") is None
    assert dashboard_service._days_since(date.today().isoformat() + "T09:00:00") == 0
