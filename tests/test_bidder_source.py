"""Session L1 A1/A2: his registered bidders become contacts, and their behaviour
lands beside them. Criteria 1-4 of `sessions/session-L1.md`.

Every page is `tests/_la_portal.py`. That proves the port's logic — paging,
waiting, parsing, persistence — and not that LiveAuctioneers still serves these
selectors; nobody has recorded the live page.
"""

import ast
import os
import socket

import pytest
from sqlalchemy import Boolean, Date, Float, Integer, func

from app.models.bidder_profile import BidderProfile
from app.models.contact import Contact
from app.services import contact_service
from app.sources import SOURCES, liveauctioneers as la, platforms
from tests._bidder_setup import (LIST_NAME, PREFIX, NetworkAttempted, db,  # noqa: F401
                                 fixture_contacts, no_network, scrape)
from tests._la_portal import DISTRACTOR, Portal, load_bidders, panel_lines

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Derived from the registry, not listed: the day a Proxibid scraper is
# registered, its module is scanned without anybody remembering to add it.
L1_SOURCE_FILES = tuple(sorted(
    {"app/sources/auction_scraper_base.py", "app/sources/platforms.py"}
    | {p["scraper"].split(":")[0].replace(".", "/") + ".py"
       for p in platforms.PLATFORMS.values()}))

# What the fixture holds, derived from it rather than typed in. Its own
# `_comment` lists the edge cases row by row.
BIDDERS = load_bidders()
DISTINCT = len({b["phone"][-4:] for b in BIDDERS if b["phone"]})

BEHAVIOUR = {"card_on_file", "auctions_attended", "bids_placed", "items_won",
             "payment_rate", "payment_rate_pct", "avg_hammer_price",
             "avg_hammer_cents", "dispute_history", "disputes_open",
             "disputes_closed", "member_since", "tax_exempt", "tax_exemption"}


# ─── Criterion 1: no network call in the suite ──────────────────────────────

def test_a_whole_scrape_runs_with_the_network_refused(db, tmp_path):
    with no_network():
        # Known answer first: the guard must trip on a real socket, or a green
        # run below proves nothing about the network.
        probe = socket.socket()
        try:
            with pytest.raises(NetworkAttempted):
                probe.connect(("127.0.0.1", 9))
        finally:
            probe.close()
        run, portal, _ = scrape(db, tmp_path=tmp_path)
    assert run.status == "completed", run.error
    assert portal.gotos and all("partners.liveauctioneers.com" in u for u in portal.gotos)
    assert portal.launch_kwargs["user_data_dir"].startswith(str(tmp_path))


# ─── Criterion 2: N bidders, N contacts; the second run makes none ──────────

def test_n_bidders_make_n_contacts_and_a_second_run_makes_none(db, tmp_path):
    first, _, _ = scrape(db, tmp_path=tmp_path)
    assert first.status == "completed", first.error
    assert first.bidders_read == len(BIDDERS)
    assert first.contacts_created == DISTINCT
    assert len(fixture_contacts(db)) == DISTINCT

    second, _, _ = scrape(db, tmp_path=tmp_path)
    assert second.status == "completed", second.error
    assert second.contacts_created == 0
    assert second.contacts_updated == DISTINCT
    assert len(fixture_contacts(db)) == DISTINCT
    dupes = (db.query(Contact.phone, func.count(Contact.id))
             .filter(Contact.phone.like(f"{PREFIX}%"))
             .group_by(Contact.phone).having(func.count(Contact.id) > 1).all())
    assert dupes == []
    assert db.query(BidderProfile).count() == DISTINCT


def test_every_row_is_accounted_for(db, tmp_path):
    """The run row's counts add up to what was read — the reference system's
    job counters did not, which is how 425 registered became 166 scraped with
    nothing saying where the rest went."""
    run, _, _ = scrape(db, tmp_path=tmp_path)
    # no_phone is 2, not 1: rows 48 and 49 share a number on adjacent rows, so
    # 49's number was already on screen when its panel opened and is read as
    # nobody's. That is the safe direction by design — see `parse_profile()`.
    assert (run.no_phone, run.repeats, run.phone_conflicts) == (2, 1, 1)
    assert run.bidders_read == (run.no_phone + run.invalid + run.repeats
                                + run.phone_conflicts + run.opted_out
                                + run.screened_out + run.contacts_created
                                + run.contacts_updated)


def test_bidders_land_on_one_list_per_platform_under_his_source(db, tmp_path):
    scrape(db, tmp_path=tmp_path)
    scrape(db, tmp_path=tmp_path)
    labels = [a["label"] for a in contact_service.list_summaries(db)]
    assert labels.count(LIST_NAME) == 1
    assert contact_service.audience_count(db, "source:liveauctioneers") == DISTINCT
    assert SOURCES["liveauctioneers"] is la.LiveAuctioneersSource


def test_the_distractor_in_the_table_is_nobodys_phone(db, tmp_path):
    """The reference regex ran over the whole page and read the table's first
    ten-digit figure. The phoneless bidder must stay phoneless, not become a
    contact under the table's number."""
    scrape(db, tmp_path=tmp_path)
    phoneless = next(b for b in BIDDERS if not b["phone"])
    assert db.query(Contact).filter(Contact.full_name == phoneless["name"]).count() == 0
    distractor = "+1" + "".join(ch for ch in DISTRACTOR if ch.isdigit())
    assert db.query(Contact).filter(Contact.phone == distractor).count() == 0


def test_one_number_for_two_names_keeps_the_first(db, tmp_path):
    scrape(db, tmp_path=tmp_path)
    shared = next(b for b in BIDDERS if b["name"].endswith(" 61"))
    contact = db.query(Contact).filter(Contact.phone == "+19547000061").one()
    assert contact.full_name == shared["name"]


# ─── Criterion 3: app/sources writes nothing to the database ────────────────

FORBIDDEN_MODULES = ("app.models", "app.services", "app.core.database", "sqlalchemy",
                     "sqlite3", "psycopg2", "importlib")
# `ingest` too: a source calling `ingest()` on itself or on another source is a
# write through the sanctioned seam from the wrong side of it.
FORBIDDEN_CALLS = {"commit", "flush", "add_all", "execute", "executemany", "query",
                   "merge", "bulk_save_objects", "bulk_insert_mappings", "refresh",
                   "ingest"}


def db_violations(source: str) -> list:
    """Every import of the DB layer and every session-shaped call, by line.

    Structural, like the gate's app/sms check, and wider than it: an import can
    be deferred into a function body, and a session can arrive as an argument
    without any import at all, so both are looked for anywhere in the tree.
    Comments and docstrings are not code and are never read — prose describing
    the rule has been counted as a violation of it six times in this project.
    """
    found = []
    for node in ast.walk(ast.parse(source)):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
        for name in names:
            if name.startswith(FORBIDDEN_MODULES) or name == "app.services":
                found.append((node.lineno, f"import {name}"))
        if isinstance(node, ast.ImportFrom) and node.module == "app" and any(
                a.name in ("models", "services") for a in node.names):
            found.append((node.lineno, "from app import models/services"))
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in FORBIDDEN_CALLS):
            found.append((node.lineno, f".{node.func.attr}()"))
        if isinstance(node, ast.arg) and node.arg in ("db", "session"):
            found.append((node.lineno, f"parameter {node.arg}"))
        if isinstance(node, ast.Attribute) and node.attr in ("db", "session"):
            found.append((node.lineno, f"attribute .{node.attr}"))
    return found


def test_the_scan_catches_the_reference_save_profile():
    """Known answer: the shape `_save_profile` had must be flagged, including a
    deferred import and a session held on self."""
    reference = (
        "def save_profile(self, data):\n"
        "    from app.models.contact import Contact\n"
        "    self.db.add(Contact(phone=data['phone']))\n"
        "    self.db.commit()\n")
    kinds = {v for _, v in db_violations(reference)}
    assert "import app.models.contact" in kinds and ".commit()" in kinds
    assert "attribute .db" in kinds
    assert db_violations('"""We never self.db.commit() here."""\n# db.query()\n') == []
    assert db_violations("import sqlite3\n") and db_violations("x.ingest(s)\n")


def test_the_scan_covers_every_registered_platform():
    assert "app/sources/liveauctioneers.py" in L1_SOURCE_FILES


@pytest.mark.parametrize("path", L1_SOURCE_FILES)
def test_the_bidder_source_writes_nothing_to_the_database(path):
    with open(os.path.join(ROOT, path)) as fh:
        assert db_violations(fh.read()) == [], path


def test_the_registry_is_a_registry_not_a_constant():
    assert platforms.get("liveauctioneers")["label"] == "LiveAuctioneers"
    assert platforms.scraper_class("liveauctioneers") is la.LiveAuctioneersSource
    with pytest.raises(ValueError):
        platforms.get("proxibid")


# ─── Criterion 4: behaviour in its own table, with real types ───────────────

def test_no_behavioural_field_is_a_contact_column():
    assert set(Contact.__table__.columns.keys()) & BEHAVIOUR == set()


def test_the_profile_columns_are_real_types():
    cols = BidderProfile.__table__.columns
    for name in ("auctions_attended", "bids_placed", "items_won", "avg_hammer_cents",
                 "disputes_open", "disputes_closed", "contact_id"):
        assert isinstance(cols[name].type, Integer), name
    for name in ("card_on_file", "tax_exempt", "avg_hammer_is_ceiling"):
        assert isinstance(cols[name].type, Boolean), name
    assert isinstance(cols["member_since"].type, Date)
    assert isinstance(cols["payment_rate_pct"].type, Float)


def test_the_figures_land_typed_and_absence_is_null_not_zero(db, tmp_path):
    scrape(db, tmp_path=tmp_path)

    def profile(i):
        return (db.query(BidderProfile).join(Contact, Contact.id == BidderProfile.contact_id)
                .filter(Contact.phone == f"+1954700{i:04d}").one())

    p = profile(8)
    assert (p.items_won, p.bids_placed, p.avg_hammer_cents) == (8, 24, 10608)
    assert p.card_on_file is True and p.tax_exempt is False
    assert p.payment_rate_pct == 88.0 and (p.disputes_open, p.disputes_closed) == (0, 0)
    assert p.member_since.isoformat() == "2018-09-09" and p.platform == "liveauctioneers"
    assert p.platform_username == "bidder008"
    assert (profile(10).avg_hammer_cents, profile(10).avg_hammer_is_ceiling) == (10000, True)
    never = profile(15)
    assert (never.items_won, never.bids_placed, never.avg_hammer_cents) == (None, None, None)
    assert profile(20).avg_hammer_cents is None
    # "Won at least three items" is now a query, which is the point of the table.
    assert db.query(BidderProfile).filter(BidderProfile.items_won >= 3).count() > 0


def test_a_contact_with_no_profile_still_works_everywhere(db, tmp_path):
    from fastapi.testclient import TestClient
    from app.main import app

    scrape(db, tmp_path=tmp_path)
    bare = Contact(phone=f"{PREFIX}9999", full_name="CSV Only", source="csv",
                   attributes={}, created_at="2026-09-22T09:00:00")
    db.add(bare)
    db.commit()
    assert bare.id in {c.id for c in contact_service.resolve_audience(db, "all")}

    client = TestClient(app)
    client.post("/login", data={"username": "admin",
                                "password": os.environ["ADMIN_PASSWORD"]})
    for path in ("/contacts", "/dashboard", "/campaigns", "/api/dashboard",
                 "/api/campaigns/audiences", "/api/lists", "/api/contacts/export.csv"):
        response = client.get(path)
        assert response.status_code == 200, (path, response.status_code)
    listed = client.get(f"/api/contacts?search=CSV Only")
    assert listed.status_code == 200 and "CSV Only" in listed.text


# ─── Parsing, without a browser ─────────────────────────────────────────────

def test_parse_profile_reads_the_panel_not_the_table():
    b = BIDDERS[2]
    table = ["★", DISTRACTOR, b["name"], "Fort Lauderdale"]
    panel = panel_lines(b)
    parsed = la.parse_profile("\n".join(table + panel), b["name"], panel)
    assert parsed["phone"] == b["phone"] and parsed["username"] == b["username"]
    no_phone = dict(b, phone="")
    panel = panel_lines(no_phone)
    parsed = la.parse_profile("\n".join(table + panel), b["name"], panel)
    assert "phone" not in parsed


@pytest.mark.parametrize("text, expected", [
    ("$1,234.56", (123456, False)), ("$250", (25000, False)),
    ("Less than $100", (10000, True)), ("$1.2K", (None, None)), ("N/A", (None, None)),
])
def test_hammer_prices_are_integer_cents(text, expected):
    assert la.parse_money_cents(text) == expected


def test_member_since_is_read_beside_its_label_not_the_sale_date():
    lines = ["Oct 01, 2026 - Marine Sale", "Member Since", "Mar 4, 2015"]
    assert la.parse_member_since(lines).isoformat() == "2015-03-04"
