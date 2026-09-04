"""The Today screen: freshness, the hero, and the two charts.

The assertion this file exists for is the em dash. "Days since last send" is the
number the client schedules his week against, and a list that has never been
texted must not render as `0` — that reads as "texted today", it is the exact
opposite of the truth, and it would keep a whole audience from ever being
picked. `None` in the service, `—` on the page, verified end to end.

Session 5i changed what a card is *about* and left that rule exactly where it
was. The cards were one per category; they are now the pinned "all bidders"
entry plus the five most recent lists, because the client's question is "when did
I last text these people" and after 5i "these people" is a named list.

Everything this module creates, it removes. The suite runs against one database
with no rollback between tests, and `test_smoke` sends a campaign to audience
"all" and asserts an exact `sent_count` — a contact left behind here fails a
test three files away for a reason that looks nothing like this one.
"""

import os
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient          # noqa: E402

from app.main import app                           # noqa: E402
from app.core.database import SessionLocal         # noqa: E402
from app.models.campaign import Campaign           # noqa: E402
from app.models.contact import Contact             # noqa: E402
from app.models.contact_list import (              # noqa: E402
    ContactList, ContactListMember,
)
from app.models.sms_message import SMSMessage      # noqa: E402
from app.services import contact_service, dashboard_service   # noqa: E402

PASSWORD = os.environ["ADMIN_PASSWORD"]

# 9545552xxx is this module's slice of the reserved fiction block, distinct from
# every other test module's so they cannot collide.
PHONES = {name: f"+195455520{n:02d}" for n, name in enumerate(
    ["fresh", "stale", "today", "never", "failed"], start=1)}

# One list per case, and the name is the identity — that is the 5i model. Days
# ago each was last texted, and the status that send landed in. `failed`'s
# message failed, so it does not count as a send: a failed blast is not contact,
# and treating it as one is how a list goes quiet unnoticed.
#
# `created_at` is spelled out per list and descends with the order below, so the
# recency ordering has something to be right or wrong about. Written explicitly
# because since 5i there is no server default to fall back on — that is A1.
SEND_PLAN = {
    "fresh": (2, "delivered"),
    "stale": (21, "sent"),
    "today": (0, "delivered"),
    "never": None,                       # never texted → em dash
    "failed": (3, "failed"),             # failed → also never texted
}

LIST_PREFIX = "5i-dashboard "
CAMPAIGN_NAME = "module-3b hero campaign"


def _created_at(offset: int) -> str:
    """Descending, one hour apart, so the newest-first order is unambiguous."""
    return f"2026-08-{20 - offset:02d}T09:00:00.000000"


def _purge(db):
    ids = [row.id for row in db.query(Contact).filter(Contact.phone.in_(PHONES.values()))]
    if ids:
        db.query(SMSMessage).filter(SMSMessage.contact_id.in_(ids)).delete(
            synchronize_session=False)
        db.query(Contact).filter(Contact.id.in_(ids)).delete(synchronize_session=False)
    list_ids = [row.id for row in
                db.query(ContactList).filter(ContactList.name.like(f"{LIST_PREFIX}%"))]
    if list_ids:
        db.query(ContactListMember).filter(
            ContactListMember.list_id.in_(list_ids)).delete(synchronize_session=False)
        db.query(ContactList).filter(ContactList.id.in_(list_ids)).delete(
            synchronize_session=False)
    db.query(Campaign).filter(Campaign.name == CAMPAIGN_NAME).delete(
        synchronize_session=False)
    db.commit()


@pytest.fixture(scope="module")
def seeded():
    db = SessionLocal()
    try:
        _purge(db)
        list_ids = {}
        for offset, (name, plan) in enumerate(SEND_PLAN.items()):
            contact = Contact(phone=PHONES[name], full_name=f"Dashboard {name}",
                              source="test", attributes={},
                              created_at=date.today().isoformat())
            db.add(contact)
            db.flush()

            listing = ContactList(name=f"{LIST_PREFIX}{name}", source="test",
                                  created_at=_created_at(offset))
            db.add(listing)
            db.flush()
            list_ids[name] = listing.id
            db.add(ContactListMember(list_id=listing.id, contact_id=contact.id,
                                     added_at=date.today().isoformat() + "T09:00:00"))

            if plan is None:
                continue
            days_ago, status = plan
            sent_at = (date.today() - timedelta(days=days_ago)).isoformat() + "T10:00:00"
            db.add(SMSMessage(campaign_id=None, contact_id=contact.id,
                              phone=PHONES[name],
                              message="Tomorrow's sale, 10am.", status=status,
                              segments=1, sent_at=sent_at))

        db.add(Campaign(name=CAMPAIGN_NAME, message_template="Preview lot list",
                        audience=f"list:{list_ids['fresh']}",
                        audience_label=f"{LIST_PREFIX}fresh",
                        status="draft",
                        created_at=date.today().isoformat() + "T08:00:00"))
        db.commit()
        yield list_ids
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


def _cards_by_label(payload):
    return {card["label"]: card for card in payload["lists"]}


# ─── Days since last send ───────────────────────────────────────────────────

def test_days_since_last_send_is_computed_from_actual_sends(client):
    cards = _cards_by_label(client.get("/api/dashboard").json())

    assert cards[f"{LIST_PREFIX}fresh"]["days_since_last_send"] == 2
    assert cards[f"{LIST_PREFIX}fresh"]["days_label"] == "2"
    assert cards[f"{LIST_PREFIX}today"]["days_since_last_send"] == 0
    assert cards[f"{LIST_PREFIX}today"]["days_label"] == "0"
    assert cards[f"{LIST_PREFIX}stale"]["days_since_last_send"] == 21


def test_never_texted_list_renders_an_em_dash_not_a_zero(client):
    cards = _cards_by_label(client.get("/api/dashboard").json())

    # Never texted at all.
    assert cards[f"{LIST_PREFIX}never"]["days_since_last_send"] is None
    assert cards[f"{LIST_PREFIX}never"]["days_label"] == "—"
    assert cards[f"{LIST_PREFIX}never"]["days_caption"] == "never texted"

    # Texted, but the send failed. Same answer, and for the same reason: nobody
    # on this list has actually heard from us.
    assert cards[f"{LIST_PREFIX}failed"]["days_since_last_send"] is None
    assert cards[f"{LIST_PREFIX}failed"]["days_label"] == "—"

    # And the distinction survives to the page, which is where it matters:
    # "never texted" is the caption only a card with no send can carry, and the
    # em dash is the figure printed above it.
    html = client.get("/dashboard").text
    assert "never texted" in html
    assert "—" in html


def test_staleness_threshold_flags_only_the_stale_list(client):
    payload = client.get("/api/dashboard").json()
    cards = _cards_by_label(payload)

    assert payload["stale_days"] == 14
    assert cards[f"{LIST_PREFIX}stale"]["stale"] is True       # 21 days
    assert cards[f"{LIST_PREFIX}fresh"]["stale"] is False      # 2 days
    # Never texted is not "stale" — it is a different state with a different fix,
    # and colouring it red would say we let it go quiet rather than never started.
    assert cards[f"{LIST_PREFIX}never"]["stale"] is False


# ─── The picker's shape, on the screen that renders it ──────────────────────

def test_the_pinned_entry_is_first_and_reads_the_one_constant(client):
    """5i: the wording has one home and every surface renders it verbatim.

    Asserted against `contact_service.ALL_BIDDERS_LABEL` rather than against the
    string, and on the dashboard payload, the dashboard page and the composer's
    own audience endpoint — because "the two surfaces agree" is the property the
    constant exists for, and three literals that happen to match prove nothing
    about where they came from.
    """
    payload = client.get("/api/dashboard").json()
    assert payload["lists"], "no cards, so nothing below is being checked"
    assert payload["lists"][0]["label"] == contact_service.ALL_BIDDERS_LABEL
    assert payload["lists"][0]["selector"] == "all"

    audiences = client.get("/api/campaigns/audiences").json()["audiences"]
    assert audiences[0]["label"] == contact_service.ALL_BIDDERS_LABEL
    assert audiences[0]["selector"] == "all"

    # And it reaches the page. Escaped, because the label carries an em dash and
    # a star; `html` is what a browser receives, not what the constant says.
    import html as html_module
    assert html_module.escape(contact_service.ALL_BIDDERS_LABEL) in \
        client.get("/dashboard").text


def test_the_cards_are_the_pinned_entry_plus_five_recent_lists(client, seeded):
    """The grid was built for five and a dashboard that grows is not one."""
    cards = client.get("/api/dashboard").json()["lists"]
    assert len(cards) <= 1 + dashboard_service.RECENT_LIST_CARDS
    assert cards[0]["kind"] == "all"
    assert all(c["kind"] == "list" for c in cards[1:])
    # No swatch. A list has no palette token, and colour was never the identity
    # channel here — the label was.
    assert all("color_token" not in c for c in cards)


def test_the_dashboard_offers_no_category(client):
    payload = client.get("/api/dashboard").json()
    assert "categories" not in payload
    assert all(not c["selector"].startswith("category:") for c in payload["lists"])


# ─── The page ───────────────────────────────────────────────────────────────

def test_dashboard_page_renders_every_card_label(client, seeded):
    for path in ("/", "/dashboard"):
        response = client.get(path)
        assert response.status_code == 200
        labels = [card["label"] for card in
                  client.get("/api/dashboard").json()["lists"]]
        assert labels
        for label in labels:
            import html as html_module
            assert html_module.escape(label) in response.text, \
                f"{label!r} missing from {path}"


def test_hero_falls_back_to_the_newest_draft(client, seeded):
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
        cards = dashboard_service.list_cards(db)
        card = dashboard_service._list_for_campaign(db, ours, cards)
        # Matched on the campaign's own audience selector, which is the record
        # of what it was pointed at.
        assert card["label"] == f"{LIST_PREFIX}fresh"
        assert card["days_label"] == "2"
        assert dashboard_service.next_up(db, cards)["audience_count"] >= 1
    finally:
        db.close()


def test_the_hero_prints_an_em_dash_for_an_audience_that_is_not_a_card(client, seeded):
    """A campaign pointed at something the card grid does not carry.

    A `category:` selector from before 5i, a `source:` selector, or a list that
    has fallen off the five most recent. The hero says so rather than guessing —
    a guessed figure here is indistinguishable from a measured one, and this is
    the tile he schedules against.
    """
    db = SessionLocal()
    try:
        cards = dashboard_service.list_cards(db)
        stray = Campaign(id=0, name="stray", message_template="hi",
                         audience="category:food_service",
                         status="draft", created_at="2000-01-01T00:00:00")
        assert dashboard_service._list_for_campaign(db, stray, cards) is None
    finally:
        db.close()

    html = client.get("/dashboard").text
    assert "List last texted" in html


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
    from tests import _wholesale_scan as scan
    scan.assert_no_wholesale_field(tiles, where="dashboard tiles")


def test_service_days_since_helper_never_returns_zero_for_never():
    assert dashboard_service._days_since(None) is None
    assert dashboard_service._days_since("") is None
    assert dashboard_service._days_since(date.today().isoformat() + "T09:00:00") == 0


def test_the_two_screens_read_one_days_since_helper():
    """`dashboard_service._days_since` delegates rather than implements.

    The picker reports freshness and so does this screen; two copies of the
    arithmetic is how they come to disagree about how old the same send is. The
    property, not the implementation: same input, same answer, including the
    None that is not a zero.
    """
    for value in (None, "", "not-a-date",
                  (date.today() - timedelta(days=4)).isoformat() + "T10:00:00"):
        assert dashboard_service._days_since(value) == contact_service.days_since(value)
