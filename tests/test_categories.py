"""Categories: the seed, the selector grammar, and the CRUD guardrails.

The selector tests are the point of this file. `resolve_audience()` is what
stands between "tonight's auction is walk-in coolers" and a Memorabilia
collector's handset, and every one of its failure modes is silent: a typo
resolves to nobody, a bad precedence rule resolves to the wrong people, and a
join instead of a sub-select texts the same person twice. None of those raise.

Everything this module creates, it removes — see `_purge`. The suite runs
against one database with no rollback between tests, and `test_smoke` sends a
campaign to audience "all" and asserts an exact `sent_count`.
"""

import os
import pytest

from fastapi.testclient import TestClient          # noqa: E402
from app.main import app                           # noqa: E402
from app.core.database import SessionLocal         # noqa: E402
from app.models.category import Category, ContactCategory   # noqa: E402
from app.models.contact import Contact             # noqa: E402
from app.models.contact_list import ContactList, ContactListMember   # noqa: E402
from app.services import category_service, contact_service           # noqa: E402

PASSWORD = os.environ["ADMIN_PASSWORD"]

# 954 is Fort Lauderdale; 555-01xx is the reserved fiction block. This range is
# distinct from test_smoke's and test_import's so the modules cannot collide.
A, B, C, D, E = (f"+1954555100{n}" for n in range(1, 6))
AUDIENCE_PHONES = (A, B, C, D, E)

LIST_NAME = "module-2 selector list"

# slug, label, color_token, sort_order — the seed, exactly as the spec states it.
EXPECTED_SEED = [
    ("food_service", "Food Service", "s1", 1),
    ("equipment", "Equipment & Machinery", "s2", 2),
    ("estates", "Estates", "s3", 3),
    ("memorabilia", "Memorabilia", "s4", 4),
    ("general", "General Merchandise", "neutral", 5),
]


def _purge(db):
    ids = [row.id for row in db.query(Contact).filter(Contact.phone.in_(AUDIENCE_PHONES))]
    if ids:
        db.query(ContactCategory).filter(ContactCategory.contact_id.in_(ids)).delete(
            synchronize_session=False)
        db.query(ContactListMember).filter(ContactListMember.contact_id.in_(ids)).delete(
            synchronize_session=False)
        db.query(Contact).filter(Contact.id.in_(ids)).delete(synchronize_session=False)
    for row in db.query(ContactList).filter(ContactList.name == LIST_NAME):
        db.query(ContactListMember).filter(ContactListMember.list_id == row.id).delete(
            synchronize_session=False)
        db.delete(row)
    # Categories this module created; the five seeded ones stay.
    db.query(Category).filter(Category.slug.like("module2_%")).delete(
        synchronize_session=False)
    db.commit()


@pytest.fixture(scope="module")
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(scope="module", autouse=True)
def audience(db):
    """Five contacts across two categories and one list.

    C is in both categories and on the list — it is the contact that proves
    dedup, and the one an intersection has to keep.
    """
    _purge(db)

    food = category_service.get_by_slug(db, "food_service")
    equipment = category_service.get_by_slug(db, "equipment")

    contacts = {}
    for phone in AUDIENCE_PHONES:
        contacts[phone] = contact_service.upsert_contact(
            db, phone=phone, full_name=f"Buyer {phone[-4:]}", source="test")

    for phone, category in ((A, food), (B, equipment), (C, food),
                            (C, equipment), (D, equipment)):
        category_service.tag_contact(db, contacts[phone].id, category.id, source="manual")

    target = contact_service.get_or_create_list(db, LIST_NAME, source="test")
    for phone in (C, D, E):
        contact_service.add_to_list(db, target.id, contacts[phone].id)

    yield {"list_id": target.id, "contacts": contacts,
           "food": food, "equipment": equipment}

    _purge(db)


@pytest.fixture(scope="module")
def client(audience):
    """One logged-in session for the module — POST /login is 10/minute per IP."""
    c = TestClient(app)
    r = c.post("/login", data={"username": "admin", "password": PASSWORD})
    assert r.status_code in (200, 302), "login failed; every assertion below would be a 401"
    return c


def phones(contacts):
    return [c.phone for c in contacts]


# ─── The seed ───────────────────────────────────────────────────────────────

def test_migration_seeds_exactly_five_categories(db):
    """`alembic upgrade head` from empty produces these five, in this order.

    The scratch database this runs against was built by `alembic upgrade head`
    (tests/conftest.py), so this is that migration's output, not a fixture's.
    """
    rows = db.query(Category).order_by(Category.sort_order).all()
    assert len(rows) == 5, f"expected 5 seeded categories, got {len(rows)}"
    assert [(r.slug, r.label, r.color_token, r.sort_order) for r in rows] == EXPECTED_SEED


def test_seeding_twice_is_idempotent(db):
    """Run the migration's own seed function a second time; nothing changes.

    Calls the real `_seed_categories()` out of the migration module rather than
    reimplementing it — a test that reimplements the thing it is testing agrees
    with itself and nothing else.
    """
    from alembic.config import Config
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from alembic.script import ScriptDirectory
    from app.core.database import engine

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config = Config()
    config.set_main_option("script_location", os.path.join(root, "alembic"))

    seeders = [rev.module for rev in ScriptDirectory.from_config(config).walk_revisions()
               if hasattr(rev.module, "_seed_categories")]
    assert len(seeders) == 1, "expected exactly one migration to own the category seed"

    before = db.query(Category).count()
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            seeders[0]._seed_categories()

    db.expire_all()
    assert db.query(Category).count() == before == 5


# ─── Selector grammar ───────────────────────────────────────────────────────

def test_single_category_selector(db):
    assert set(phones(contact_service.resolve_audience(db, "category:food_service"))) == {A, C}


def test_comma_is_union(db):
    result = contact_service.resolve_audience(db, "category:food_service,equipment")
    assert set(phones(result)) == {A, B, C, D}


def test_contact_in_two_categories_resolves_exactly_once(db):
    """C is in both. A join would return it twice and text the same person twice."""
    result = phones(contact_service.resolve_audience(db, "category:food_service,equipment"))
    assert result.count(C) == 1
    assert len(result) == len(set(result))


def test_ampersand_is_intersection(db, audience):
    result = contact_service.resolve_audience(
        db, f"category:equipment&list:{audience['list_id']}")
    assert set(phones(result)) == {C, D}


def test_comma_binds_tighter_than_ampersand(db, audience):
    """`category:a,b&list:N` is (a ∪ b) ∩ listN, not a ∪ (b ∩ listN).

    The wrong precedence would add A here — a Food Service buyer who is not on
    the list, in an audience the client built to exclude exactly that.
    """
    selector = f"category:food_service,equipment&list:{audience['list_id']}"
    assert set(phones(contact_service.resolve_audience(db, selector))) == {C, D}


def test_existing_selectors_still_work(db, audience):
    assert {A, B, C, D, E} <= set(phones(contact_service.resolve_audience(db, "all")))
    assert set(phones(contact_service.resolve_audience(
        db, f"list:{audience['list_id']}"))) == {C, D, E}
    assert {A, B, C, D, E} <= set(phones(contact_service.resolve_audience(db, "source:test")))


def test_inactive_contacts_are_excluded(db, audience):
    contact = audience["contacts"][A]
    contact.is_active = 0
    db.commit()
    try:
        assert set(phones(contact_service.resolve_audience(db, "category:food_service"))) == {C}
    finally:
        contact.is_active = 1
        db.commit()


def test_unknown_slug_raises_and_names_it(db):
    """Never a silent empty audience — that is how a campaign 'sends' to nobody."""
    with pytest.raises(ValueError, match="food_serivce"):
        contact_service.resolve_audience(db, "category:food_serivce")


def test_two_ampersands_raise(db, audience):
    with pytest.raises(ValueError, match="more than one"):
        contact_service.resolve_audience(
            db, f"category:food_service&list:{audience['list_id']}&source:test")


# ─── Labels and summaries ───────────────────────────────────────────────────

def test_audience_labels_read_like_english(db, audience):
    label = contact_service.audience_label
    assert label(db, "category:food_service") == "Food Service"
    assert label(db, "category:food_service,equipment") == "Food Service + Equipment & Machinery"
    assert label(db, f"category:equipment&list:{audience['list_id']}") == \
        f"Equipment & Machinery ∩ {LIST_NAME}"
    assert label(db, "all") == "All contacts"


def test_audience_label_never_raises_on_a_bad_selector(db):
    """It renders in page headers. A label helper that throws is a 500 on a
    screen that was only trying to describe a typo."""
    assert contact_service.audience_label(db, "category:a&list:1&source:x")
    assert contact_service.audience_label(db, "list:not-a-number")


def test_list_summaries_include_every_active_category(db):
    summaries = contact_service.list_summaries(db)
    by_selector = {s["selector"]: s for s in summaries}
    for slug, label, _token, _order in EXPECTED_SEED:
        assert by_selector[f"category:{slug}"]["label"] == label
    assert by_selector["category:food_service"]["count"] == 2
    assert by_selector["category:equipment"]["count"] == 3
    assert by_selector["category:estates"]["count"] == 0


# ─── CRUD API ───────────────────────────────────────────────────────────────

def test_create_rejects_an_unvalidated_color(client):
    r = client.post("/api/categories", json={
        "slug": "module2_bad_color", "label": "Bad", "color_token": "s5"})
    assert r.status_code == 400
    assert "s5" in r.json()["detail"]


def test_update_rejects_an_unvalidated_color(client, db):
    created = client.post("/api/categories", json={
        "slug": "module2_recolor", "label": "Recolor", "color_token": "neutral"}).json()
    category_id = created["category"]["id"]

    assert client.patch(f"/api/categories/{category_id}",
                        json={"color_token": "#ff0000"}).status_code == 400
    assert client.patch(f"/api/categories/{category_id}",
                        json={"color_token": "s3", "label": "Recolored"}).status_code == 200

    db.expire_all()
    row = db.get(Category, category_id)
    assert (row.color_token, row.label) == ("s3", "Recolored")


def test_hard_delete_is_refused_while_a_category_has_members(client, db, audience):
    """The FK cascades. A hard delete here would take the tagging history with
    it and say nothing."""
    equipment_id = audience["equipment"].id
    r = client.delete(f"/api/categories/{equipment_id}?hard=true")
    assert r.status_code == 409
    assert "eactivate" in r.json()["detail"]

    db.expire_all()
    assert db.get(Category, equipment_id) is not None
    assert category_service.member_count(db, equipment_id) == 3


def test_hard_delete_is_allowed_for_a_category_nobody_is_in(client, db):
    created = client.post("/api/categories", json={
        "slug": "module2_empty", "label": "Empty", "color_token": "neutral"}).json()
    category_id = created["category"]["id"]
    assert client.delete(f"/api/categories/{category_id}?hard=true").status_code == 200
    db.expire_all()
    assert db.get(Category, category_id) is None


def test_deactivation_hides_a_category_without_losing_its_tags(client, db, audience):
    equipment_id = audience["equipment"].id
    r = client.delete(f"/api/categories/{equipment_id}")
    assert r.status_code == 200
    assert r.json()["category"]["is_active"] is False

    try:
        listed = client.get("/api/categories").json()["categories"]
        assert "equipment" not in {c["slug"] for c in listed}
        assert "equipment" in {c["slug"] for c in
                               client.get("/api/categories?include_inactive=true").json()["categories"]}

        db.expire_all()
        # Retiring a category from the pickers must not empty a campaign already
        # pointed at it.
        assert len(contact_service.resolve_audience(db, "category:equipment")) == 3
        assert "category:equipment" not in {
            s["selector"] for s in contact_service.list_summaries(db)}
    finally:
        client.patch(f"/api/categories/{equipment_id}", json={"is_active": True})
