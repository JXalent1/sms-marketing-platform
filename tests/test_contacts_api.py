"""The Contacts screen's API: paging, filtering, search, bulk actions, export.

The load-bearing test here is `test_page_one_is_bounded_work`. Server-side
paging is easy to write and easy to lose: someone adds a per-row lookup, the
page still returns 50 rows, every assertion still passes, and the screen quietly
becomes 50 extra round-trips that nobody notices until the client's list is
large enough to make it a five-second page. Counting queries is the only
assertion that catches it, so it counts them rather than timing them — a timing
assertion on 1,000 rows is a flake on a busy machine.

Everything this module creates, it removes: the suite shares one database and
`test_smoke` sends to audience "all" and asserts an exact `sent_count`.
"""

import html as html_module
import os
from contextlib import contextmanager

import pytest
from sqlalchemy import event
from fastapi.testclient import TestClient          # noqa: E402

from app.main import app                           # noqa: E402
from app.core.database import SessionLocal, engine # noqa: E402
from app.models.category import Category, ContactCategory   # noqa: E402
from app.models.contact import Contact             # noqa: E402
from app.services import contact_query_service     # noqa: E402

PASSWORD = os.environ["ADMIN_PASSWORD"]

SEED_COUNT = 1_000
# 954-600xxxx: this module's own block, distinct from every other test file's.
FIRST_NUMBER = 9546000000
PHONE_PREFIX = "+19546"

# Every third contact is tagged, so the category filter has a count that is not
# the total and not zero — the two numbers a broken filter accidentally returns.
TAGGED_EVERY = 3
TAGGED_SLUG = "food_service"

NEEDLE_NAME = "Wanda Kowalczyk"
NEEDLE_COMPANY = "Hialeah Restaurant Supply"
NEEDLE_PHONE = "+19546000777"


def _purge(db):
    ids = [row.id for row in
           db.query(Contact.id).filter(Contact.phone.like(f"{PHONE_PREFIX}%"))]
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        db.query(ContactCategory).filter(ContactCategory.contact_id.in_(chunk)).delete(
            synchronize_session=False)
        db.query(Contact).filter(Contact.id.in_(chunk)).delete(synchronize_session=False)
    db.commit()


@pytest.fixture(scope="module")
def seeded():
    db = SessionLocal()
    try:
        _purge(db)
        category = db.query(Category).filter(Category.slug == TAGGED_SLUG).one()

        # bulk_insert_mappings, not 1,000 upserts: the seed is not what is under
        # test, and a minute of setup makes the suite something people skip.
        rows = []
        for n in range(SEED_COUNT):
            phone = f"+1{FIRST_NUMBER + n + 1}"
            rows.append({
                "phone": phone,
                "full_name": NEEDLE_NAME if phone == NEEDLE_PHONE else f"Bidder {n:04d}",
                "source": "test-seed",
                "is_active": 1,
                "attributes": {"company": NEEDLE_COMPANY if phone == NEEDLE_PHONE
                               else f"Yard {n % 40}"},
                "created_at": "2026-08-01T09:00:00",
            })
        db.bulk_insert_mappings(Contact, rows)
        db.commit()

        seeded_ids = [row.id for row in db.query(Contact.id)
                      .filter(Contact.phone.like(f"{PHONE_PREFIX}%"))
                      .order_by(Contact.id).all()]
        assert len(seeded_ids) == SEED_COUNT
        db.bulk_insert_mappings(ContactCategory, [
            {"contact_id": contact_id, "category_id": category.id, "source": "upload"}
            for i, contact_id in enumerate(seeded_ids) if i % TAGGED_EVERY == 0
        ])
        db.commit()
        yield {"category_id": category.id, "ids": seeded_ids}
    finally:
        _purge(db)
        db.close()


@pytest.fixture(scope="module")
def client(seeded):
    test_client = TestClient(app)
    response = test_client.post("/login", data={"username": "admin", "password": PASSWORD},
                                follow_redirects=False)
    assert response.status_code == 302, "login failed — every later assertion would be a 401"
    return test_client


@contextmanager
def counted_queries():
    """Count SQL statements executed against the app's engine."""
    counter = {"n": 0, "statements": []}

    def before(conn, cursor, statement, parameters, context, executemany):
        counter["n"] += 1
        counter["statements"].append(statement.split("\n")[0][:80])

    event.listen(engine, "before_cursor_execute", before)
    try:
        yield counter
    finally:
        event.remove(engine, "before_cursor_execute", before)


# ─── Paging ─────────────────────────────────────────────────────────────────

def test_page_one_is_bounded_work(client, seeded):
    """50 rows out of 1,000, in a query count that does not grow with the page."""
    with counted_queries() as counter:
        payload = client.get("/api/contacts?page=1").json()

    assert payload["per_page"] == 50
    assert len(payload["contacts"]) == 50
    assert payload["total"] >= SEED_COUNT
    assert payload["pages"] == -(-payload["total"] // 50)

    # count + page + chips + send counts. The bound is what matters: a per-row
    # lookup would put this in the fifties and every other assertion above
    # would still pass.
    print(f"\nqueries for page 1 of {payload['total']:,} contacts: {counter['n']}")
    for statement in counter["statements"]:
        print(f"   {statement}")
    assert counter["n"] <= 6, counter["statements"]


def test_paging_walks_the_list_without_repeating_a_row(client):
    first = client.get("/api/contacts?page=1").json()
    second = client.get("/api/contacts?page=2").json()

    assert len(second["contacts"]) == 50
    assert not ({c["id"] for c in first["contacts"]} & {c["id"] for c in second["contacts"]})


def test_a_page_past_the_end_lands_on_the_last_page(client):
    payload = client.get("/api/contacts?page=9999").json()
    # Not an empty table with no explanation: he clicked Next once too often.
    assert payload["page"] == payload["pages"]
    assert payload["contacts"]


# ─── Filtering and search ───────────────────────────────────────────────────

def test_category_filter_count_matches_a_direct_count(client, seeded):
    payload = client.get(f"/api/contacts?category_id={seeded['category_id']}").json()

    db = SessionLocal()
    try:
        direct = (db.query(ContactCategory)
                  .join(Contact, Contact.id == ContactCategory.contact_id)
                  .filter(ContactCategory.category_id == seeded["category_id"],
                          Contact.is_active == 1)
                  .count())
    finally:
        db.close()

    assert payload["total"] == direct
    assert direct >= SEED_COUNT // TAGGED_EVERY
    # And the tab row agrees with the table it filters.
    tabs = {tab["id"]: tab for tab in client.get("/api/contacts/categories").json()["tabs"]}
    assert tabs[seeded["category_id"]]["count"] == direct


def test_search_finds_a_known_contact_by_name_company_and_number(client):
    by_name = client.get("/api/contacts?q=Kowalczyk").json()
    assert by_name["total"] == 1
    assert by_name["contacts"][0]["phone"] == NEEDLE_PHONE

    by_company = client.get(f"/api/contacts?q=Hialeah").json()
    assert NEEDLE_PHONE in [c["phone"] for c in by_company["contacts"]]
    assert by_company["contacts"][0]["company"] == NEEDLE_COMPANY

    # As he would type it off a business card, not as it is stored.
    by_number = client.get("/api/contacts?q=(954) 600-0777").json()
    assert [c["phone"] for c in by_number["contacts"]] == [NEEDLE_PHONE]


def test_rows_carry_what_the_table_renders(client, seeded):
    row = client.get(f"/api/contacts?category_id={seeded['category_id']}").json()["contacts"][0]
    assert set(row) == {"id", "full_name", "company", "phone", "source",
                        "last_texted", "send_count", "categories"}
    assert row["categories"][0]["color_token"] in ("s1", "s2", "s3", "s4", "neutral")


# ─── Bulk actions ───────────────────────────────────────────────────────────

def test_bulk_add_then_remove_a_category(client, seeded):
    db = SessionLocal()
    try:
        other = db.query(Category).filter(Category.slug == "estates").one().id
    finally:
        db.close()

    ids = seeded["ids"][:5]
    added = client.post("/api/contacts/bulk/add-category",
                        json={"contact_ids": ids, "category_id": other}).json()
    assert added == {"added": 5, "already_tagged": 0}

    # Re-tagging is a no-op and says so, rather than reporting five changes.
    again = client.post("/api/contacts/bulk/add-category",
                        json={"contact_ids": ids, "category_id": other}).json()
    assert again == {"added": 0, "already_tagged": 5}

    removed = client.post("/api/contacts/bulk/remove-category",
                          json={"contact_ids": ids, "category_id": other}).json()
    assert removed == {"removed": 5}


def test_bulk_action_against_a_missing_category_is_a_404(client, seeded):
    response = client.post("/api/contacts/bulk/add-category",
                           json={"contact_ids": seeded["ids"][:1], "category_id": 999_999})
    assert response.status_code == 404


# ─── Export ─────────────────────────────────────────────────────────────────

def test_export_streams_the_selection_and_names_no_carrier(client, seeded):
    response = client.get("/api/contacts/export.csv?q=Kowalczyk")
    assert response.status_code == 200
    assert "text/csv" in response.headers["content-type"]

    body = response.text
    assert body.splitlines()[0] == ",".join(contact_query_service.EXPORT_COLUMNS)
    assert NEEDLE_COMPANY in body
    # The carrier is our implementation detail and an export is a client
    # deliverable — it is the surface a white-label leak survives longest on.
    assert "telnyx" not in body.lower()
    assert "twilio" not in body.lower()


# ─── The retired import endpoints ───────────────────────────────────────────

def test_the_retired_import_is_rejected_and_names_its_replacement(client):
    """"Gone" without "go here instead" is how an integration is rebuilt wrong.

    The sentence used to say "every import is tagged with a category". It says
    the list now, because after 5i that is both what is true and what a client
    can act on — and because a refusal the client reads is a client-facing
    string, which is the surface `tests/test_audience_surfaces.py` sweeps.
    """
    for path in ("/api/contacts/import", "/api/contacts/import/preview"):
        response = client.post(path, files={"file": ("list.csv", b"phone\n9545551234\n",
                                                     "text/csv")})
        assert response.status_code == 400, path
        detail = response.json()["detail"]
        assert "/api/imports/" in detail, detail
        assert "list" in detail.lower(), detail
        assert "categor" not in detail.lower(), detail


def test_the_import_takes_a_list_name_and_no_category(client):
    """5i A5. The requirement moved from the tag to the name.

    This test used to assert the opposite — that `/api/imports/*` refused an
    upload with no `category_id`, on the grounds that a standalone import
    produces contacts and no campaign, so an untagged one is a pile nobody can
    safely text. 5i replaced the tag with the thing that was carrying the
    meaning: the import lands in a list the **client names**, and that list is
    what a campaign points at. An import with a name is not untagged.

    Both endpoints, because the preview is where he decides and the commit is
    what writes, and a rule enforced on only the first is a rule a script routes
    around. Here both must simply *work*.
    """
    csv = b"phone\n9545551234\n"
    preview = client.post("/api/imports/preview",
                          files={"file": ("list.csv", csv, "text/csv")})
    assert preview.status_code == 200, preview.text
    assert preview.json()["valid_phones"] == 1

    name = "contacts-api 5i named import"
    commit = client.post("/api/imports/commit",
                         files={"file": ("list.csv", csv, "text/csv")},
                         data={"list_name": name})
    assert commit.status_code == 200, commit.text
    body = commit.json()
    assert body["list_name"] == name

    # The list it created is immediately offerable as an audience, which is the
    # only reason the name matters. Cleaned up: this module's neighbours assert
    # exact counts against one shared database.
    selectors = {s["selector"] for s in
                 client.get("/api/lists").json()["lists"]}
    assert f"list:{body['list_id']}" in selectors

    undone = client.post(f"/api/imports/{body['list_id']}/undo")
    assert undone.status_code == 200, undone.text


# ─── The page ───────────────────────────────────────────────────────────────

def test_the_contacts_page_names_no_category_and_offers_a_named_import(client):
    """What 5i took off this screen, and what it put there instead.

    The tab row, the per-row chips and the bulk tagging controls are gone; the
    import asks for a list name. Asserted on the rendered page rather than on the
    template source, because the tabs were server-rendered on the first paint and
    a grep would not have been able to tell the difference.
    """
    html = client.get("/contacts").text
    db = SessionLocal()
    try:
        labels = [row.label for row in db.query(Category).filter(Category.is_active == 1)]
    finally:
        db.close()
    assert labels, "no active categories, so the assertion below scans nothing"
    for label in labels:
        # Escaped: "Equipment & Machinery" reaches the page as
        # "Equipment &amp; Machinery", which is Jinja doing its job.
        assert html_module.escape(label) not in html, label

    assert 'id="importListName"' in html
    assert "Import a CSV into a new list" in html

    # No line-type column: we hold no line-type data and will not imply we do.
    assert "Landline" not in html
    assert "VoIP" not in html
