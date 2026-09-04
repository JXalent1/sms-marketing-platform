"""Session 5j A3-A6 — archive, rename, and the refusal that protects a report.

Nothing in the product could rename or hide a list, so every test upload became
a permanent entry in the composer's dropdown and ten pieces of debris from one
evening were deleted by hand on the live box. These are the three verbs that
replace that, and the property that holds all of them together:

**Hidden from the picker, still resolving for history.** The same ruling
categories got in 5i. An archived list leaves `/api/campaigns/audiences`,
`/api/lists` and the dashboard cards; `resolve_audience()`, `audience_count()`
and `_term_label()` never look at the flag, so a campaign that targeted list 20
still returns its contacts and still renders that list's *name* on the rail, in
history and in its report. A helpful cleanup must not turn a report label into
the raw string `list:20`.

Every test sets the state it depends on rather than inheriting it from its
neighbours or from the fixture. 5i's mutation run found the opposite: a test
that read a row an earlier test in the same file had rewritten, and so passed
under the exact mutation it was written to catch.

Everything this module creates, it removes.
"""

import os

import pytest
from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.main import app
from app.models.campaign import Campaign
from app.models.category import Category
from app.models.contact import Contact
from app.models.contact_list import ContactList, ContactListMember
from app.services import contact_service, dashboard_service, list_admin

PASSWORD = os.environ["ADMIN_PASSWORD"]

PREFIX = "5j-admin "
USED_LIST = f"{PREFIX}the list a campaign used"
SPARE_LIST = f"{PREFIX}the list nothing used"
COMPOUND_LIST = f"{PREFIX}the list a compound selector names"
CATEGORY_SLUG = "5j_admin_probe"

# 954-555-33xx: this module's slice of the reserved fiction block.
PHONES = [f"+195455533{n:02d}" for n in range(4)]


def _purge(db):
    db.query(Campaign).filter(Campaign.name.like(f"{PREFIX}%")).delete(
        synchronize_session=False)
    list_ids = [row.id for row in
                db.query(ContactList).filter(ContactList.name.like(f"{PREFIX}%"))]
    if list_ids:
        db.query(ContactListMember).filter(
            ContactListMember.list_id.in_(list_ids)).delete(synchronize_session=False)
        db.query(ContactList).filter(ContactList.id.in_(list_ids)).delete(
            synchronize_session=False)
    db.query(Contact).filter(Contact.phone.in_(PHONES)).delete(
        synchronize_session=False)
    db.query(Category).filter(Category.slug == CATEGORY_SLUG).delete(
        synchronize_session=False)
    db.commit()


@pytest.fixture(scope="module")
def seeded():
    """Three lists, one campaign on each of two of them, one category.

    The campaign rows are built the way `campaign_builder.create_campaign()`
    builds them — an audience selector plus `audience_label` rendered from it —
    because `campaigns.audience_label` being a *stored* render is the whole
    subject of A4's test.
    """
    db = SessionLocal()
    try:
        _purge(db)
        db.add(Category(slug=CATEGORY_SLUG, label="5j admin probe",
                        color_token="s1"))
        ids = {}
        for index, name in enumerate((USED_LIST, SPARE_LIST, COMPOUND_LIST)):
            contact = Contact(phone=PHONES[index], full_name=name, source="test",
                              attributes={}, created_at="2026-09-01T09:00:00")
            db.add(contact)
            db.flush()
            listing = ContactList(name=name, source="test",
                                  created_at=f"2026-09-0{index + 1}T09:00:00")
            db.add(listing)
            db.flush()
            db.add(ContactListMember(list_id=listing.id, contact_id=contact.id,
                                     added_at="2026-09-01T09:00:00"))
            ids[name] = listing.id
        db.commit()

        for name, audience in (
                (USED_LIST, f"list:{ids[USED_LIST]}"),
                (COMPOUND_LIST, f"category:{CATEGORY_SLUG}&list:{ids[COMPOUND_LIST]}")):
            db.add(Campaign(
                name=f"{PREFIX}campaign for {name}",
                message_template="Hi {first_name}, the sale is Thursday.",
                audience=audience,
                audience_label=contact_service.audience_label(db, audience),
                status="completed", total_recipients=1, created_at="2026-09-02T09:00:00"))
        db.commit()
        yield ids
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


def _set_archived(list_id, archived):
    """Put one list into a known state, from the test that depends on it."""
    db = SessionLocal()
    try:
        db.query(ContactList).filter(ContactList.id == list_id).update(
            {"archived": 1 if archived else 0}, synchronize_session=False)
        db.commit()
    finally:
        db.close()


def _rename(list_id, name):
    db = SessionLocal()
    try:
        db.query(ContactList).filter(ContactList.id == list_id).update(
            {"name": name}, synchronize_session=False)
        db.commit()
    finally:
        db.close()


def _campaign_for(db, list_name):
    return (db.query(Campaign)
            .filter(Campaign.name == f"{PREFIX}campaign for {list_name}").one())


# ─── A3: hidden from the picker ─────────────────────────────────────────────

def test_an_archived_list_is_absent_from_every_picker(client, seeded):
    """Criterion 5: the dropdown, /api/lists, and the dashboard cards.

    All three, because all three are built from `list_summaries()` and the
    dashboard's inheritance is exactly what a "tidy this up" refactor would
    break — `list_cards()` querying lists directly would put an archived list
    back on the Today screen while the composer no longer offered it.
    """
    list_id = seeded[SPARE_LIST]
    _set_archived(list_id, False)
    selector = f"list:{list_id}"

    offered = [a["selector"] for a in
               client.get("/api/campaigns/audiences").json()["audiences"]]
    assert selector in offered, "the fixture list was not in the picker to begin with"

    _set_archived(list_id, True)
    assert selector not in [a["selector"] for a in
                            client.get("/api/campaigns/audiences").json()["audiences"]]
    assert selector not in [a["selector"] for a in
                            client.get("/api/lists").json()["lists"]]
    assert selector not in [c["selector"] for c in
                            client.get("/api/dashboard").json()["lists"]]


def test_the_picker_payload_carries_no_archived_flag(client, seeded):
    """It says "archived" by absence, and a flag on it invites a second reader.

    `list_summaries()` is defined as what the client may pick. A picker entry
    carrying `archived: false` on every row it can ever contain is a field
    something will eventually render, and then there are two surfaces deciding
    what "archived" means instead of `is_archived()`.
    """
    _set_archived(seeded[SPARE_LIST], False)
    for entry in client.get("/api/campaigns/audiences").json()["audiences"]:
        assert "archived" not in entry, entry


def test_one_definition_of_archived_serves_the_panel_and_the_picker(client, seeded):
    """Lens 4 — two moments, one sentence-maker.

    The panel says archived with a flag and the picker says it by absence. The
    assertion is that they agree *about the same list*, not that either matches
    a literal: exactly the lists the panel marks are the lists the picker drops.
    """
    _set_archived(seeded[SPARE_LIST], True)
    _set_archived(seeded[USED_LIST], False)

    panel = client.get("/api/lists/manage").json()["lists"]
    offered = {a["selector"] for a in
               client.get("/api/campaigns/audiences").json()["audiences"]}

    marked = {row["selector"] for row in panel if row["archived"]}
    missing = {row["selector"] for row in panel} - offered
    assert marked, "no archived list in the panel, so this proves nothing"
    assert marked == missing, (
        f"the panel marks {marked} archived and the picker is missing {missing}")


def test_the_panel_reports_the_numbers_the_picker_reports(client, seeded):
    """A5: same read, so a count cannot differ between the two controls."""
    _set_archived(seeded[USED_LIST], False)
    panel = {row["selector"]: row for row in
             client.get("/api/lists/manage").json()["lists"]}
    for entry in client.get("/api/campaigns/audiences").json()["audiences"]:
        if entry["kind"] != "list":
            continue
        row = panel[entry["selector"]]
        assert (row["count"], row["last_sent_at"], row["days_since_sent"]) == (
            entry["count"], entry["last_sent_at"], entry["days_since_sent"])


def test_unarchiving_puts_it_back(client, seeded):
    list_id = seeded[SPARE_LIST]
    _set_archived(list_id, True)
    assert client.patch(f"/api/lists/{list_id}",
                        json={"archived": False}).json()["archived"] is False
    assert f"list:{list_id}" in [
        a["selector"] for a in client.get("/api/campaigns/audiences").json()["audiences"]]


# ─── A3: still resolving for history ────────────────────────────────────────

def test_an_archived_list_still_resolves_and_still_names_itself(client, seeded):
    """Criterion 4. The half a helpful cleanup deletes.

    Archiving must not empty a campaign already pointed at the list, and must
    not degrade its label to the raw selector — which is what `_term_ids_query()`
    or `_term_label()` learning about the flag would do.
    """
    list_id = seeded[USED_LIST]
    _rename(list_id, USED_LIST)
    _set_archived(list_id, True)
    selector = f"list:{list_id}"

    db = SessionLocal()
    try:
        assert [c.phone for c in contact_service.resolve_audience(db, selector)] == [PHONES[0]]
        assert contact_service.audience_count(db, selector) == 1
        assert contact_service.audience_label(db, selector) == USED_LIST
        campaign = _campaign_for(db, USED_LIST)
    finally:
        db.close()

    report = client.get(f"/api/reports/campaigns/{campaign.id}").json()
    assert report["campaign"]["audience_label"] == USED_LIST
    assert selector not in str(report["campaign"]["audience_label"])

    rows = client.get("/api/reports/campaigns?per_page=200").json()["campaigns"]
    row = next(r for r in rows if r["id"] == campaign.id)
    assert row["audience_label"] == USED_LIST


# ─── A4: rename ─────────────────────────────────────────────────────────────

def test_a_rename_reaches_the_report_of_a_campaign_already_sent(client, seeded):
    """Criterion 6, and the point of the whole feature.

    `campaigns.audience_label` is written once at creation and read back by the
    rail, by history and by the report, so a rename that only touched
    `contact_lists.name` would leave three screens quoting a name the client has
    just replaced. It is a cached render of `audience_label()` and the rename is
    what invalidates it.
    """
    list_id = seeded[USED_LIST]
    _rename(list_id, USED_LIST)
    _set_archived(list_id, False)
    new_name = f"{PREFIX}Main bidder import"

    response = client.patch(f"/api/lists/{list_id}", json={"name": new_name})
    assert response.status_code == 200, response.text
    assert response.json()["campaigns_relabelled"] >= 1

    db = SessionLocal()
    try:
        campaign = _campaign_for(db, USED_LIST)
        campaign_id = campaign.id
    finally:
        db.close()

    assert client.get(f"/api/reports/campaigns/{campaign_id}").json()[
        "campaign"]["audience_label"] == new_name
    rows = client.get("/api/reports/campaigns?per_page=200").json()["campaigns"]
    assert next(r for r in rows if r["id"] == campaign_id)["audience_label"] == new_name
    assert next(c for c in client.get("/api/campaigns?limit=200").json()["campaigns"]
                if c["id"] == campaign_id)["audience_label"] == new_name

    _rename(list_id, USED_LIST)


def test_a_rename_inside_a_compound_selector_relabels_the_whole_sentence(client, seeded):
    """The stored label is rebuilt from `audience_label()`, not patched.

    `category:5j_admin_probe&list:N` renders as "5j admin probe ∩ <list name>".
    Substituting the new name into the old string would work here and break the
    day a category label happens to contain the list's old name.
    """
    list_id = seeded[COMPOUND_LIST]
    _rename(list_id, COMPOUND_LIST)
    new_name = f"{PREFIX}renamed under a compound selector"
    assert client.patch(f"/api/lists/{list_id}", json={"name": new_name}).status_code == 200

    db = SessionLocal()
    try:
        campaign = _campaign_for(db, COMPOUND_LIST)
        assert campaign.audience_label.endswith(new_name)
        assert "∩" in campaign.audience_label
    finally:
        db.close()
    _rename(list_id, COMPOUND_LIST)


def test_a_rename_to_a_taken_name_is_a_400_naming_the_name(client, seeded):
    """Not a 500 from the unique index.

    A refusal owes the client what stopped it, in his words, and the remedy that
    works on this object — decision 006. An IntegrityError reaching the browser
    as "Internal Server Error" is a guard that refuses without explaining, on
    the one screen whose job is to let him tidy up.
    """
    _rename(seeded[USED_LIST], USED_LIST)
    _rename(seeded[SPARE_LIST], SPARE_LIST)

    response = client.patch(f"/api/lists/{seeded[SPARE_LIST]}", json={"name": USED_LIST})
    assert response.status_code == 400, response.text
    assert USED_LIST in response.json()["detail"]

    db = SessionLocal()
    try:
        assert db.get(ContactList, seeded[SPARE_LIST]).name == SPARE_LIST
    finally:
        db.close()


def test_renaming_a_list_to_the_name_it_already_has_is_not_a_collision(client, seeded):
    _rename(seeded[SPARE_LIST], SPARE_LIST)
    assert client.patch(f"/api/lists/{seeded[SPARE_LIST]}",
                        json={"name": SPARE_LIST}).status_code == 200


def test_a_blank_name_is_refused(client, seeded):
    assert client.patch(f"/api/lists/{seeded[SPARE_LIST]}",
                        json={"name": "   "}).status_code == 400


# ─── A6: delete, and what counts as "referenced" ────────────────────────────

def test_delete_refuses_a_list_a_campaign_used(client, seeded):
    """Criterion 7. 409, and the sentence names archiving as the remedy."""
    list_id = seeded[USED_LIST]
    response = client.delete(f"/api/lists/{list_id}")
    assert response.status_code == 409, response.text
    assert "rchiv" in response.json()["detail"]

    db = SessionLocal()
    try:
        assert db.get(ContactList, list_id) is not None
    finally:
        db.close()


def test_delete_refuses_a_list_a_compound_selector_names(client, seeded):
    """Lens 3: `category:estates&list:12` names list 12 as surely as `list:12`.

    Deleting it degrades that campaign's label through the same door. Checked
    against `_split_terms()` rather than assumed.
    """
    assert client.delete(f"/api/lists/{seeded[COMPOUND_LIST]}").status_code == 409


def test_delete_removes_a_list_nothing_referenced(client, seeded):
    """The other half of criterion 7 — the refusal must not be a blanket one."""
    db = SessionLocal()
    try:
        contact = Contact(phone=PHONES[3], full_name="5j spare", source="test",
                          attributes={}, created_at="2026-09-01T09:00:00")
        db.add(contact)
        db.flush()
        listing = ContactList(name=f"{PREFIX}throwaway", source="test",
                              created_at="2026-09-03T09:00:00")
        db.add(listing)
        db.flush()
        db.add(ContactListMember(list_id=listing.id, contact_id=contact.id,
                                 added_at="2026-09-01T09:00:00"))
        db.commit()
        list_id = listing.id
    finally:
        db.close()

    response = client.delete(f"/api/lists/{list_id}")
    assert response.status_code == 200, response.text
    assert response.json()["memberships_removed"] == 1

    db = SessionLocal()
    try:
        assert db.get(ContactList, list_id) is None
        # The contact survives: a list is a view onto the audience.
        assert db.query(Contact).filter(Contact.phone == PHONES[3]).first() is not None
    finally:
        db.close()


def test_a_selector_naming_list_120_does_not_name_list_12():
    """The unanchored-substring mistake, refused before it is made.

    `"list:12" in "list:120"` is True, and this project has now paid for that
    shape three times — a Twilio code matching a Brevard County phone number, a
    carrier name glued to a word, a wholesale rate inside a timestamp.
    """
    assert list_admin.selector_names_list("list:120", 120) is True
    assert list_admin.selector_names_list("list:120", 12) is False
    assert list_admin.selector_names_list("category:a&list:12", 12) is True
    assert list_admin.selector_names_list("all", 12) is False
    # Never raises on a stored selector with a typo in it.
    assert list_admin.selector_names_list("list:not-a-number", 12) is False
    assert list_admin.selector_names_list("a&b&c", 12) is False
    assert list_admin.selector_names_list(None, 12) is False


def test_deleting_a_missing_list_is_still_a_404(client):
    assert client.delete("/api/lists/99999999").status_code == 404
    assert client.patch("/api/lists/99999999", json={"name": "x"}).status_code == 404


# ─── The set before the guard ───────────────────────────────────────────────

def test_the_dashboard_cards_are_the_picker_minus_the_archived(client, seeded):
    """Lens 2 — what is in the set before the filter, and who else reads it.

    `list_cards()` is `list_summaries()` sliced to the five most recent, so it
    inherits the exclusion rather than repeating it. Asserted as a relation
    between the two payloads, not against a literal list, so it stays true as
    the fixture changes.
    """
    _set_archived(seeded[SPARE_LIST], True)
    db = SessionLocal()
    try:
        offered = {e["selector"] for e in contact_service.list_summaries(db)}
        carded = {c["selector"] for c in dashboard_service.list_cards(db)}
    finally:
        db.close()
    assert carded <= offered, carded - offered
    assert f"list:{seeded[SPARE_LIST]}" not in carded


def test_the_collision_message_names_a_remedy_that_works_on_this_object(client, seeded):
    """Decision 006: a refusal owes what stopped it and the remedy that works.

    Which remedy that is depends on where the name is: "archive the other one"
    is useless advice when the other one is already archived and still holding
    the name. Two conflicting states, two sentences, one refusal.
    """
    _rename(seeded[USED_LIST], USED_LIST)
    _rename(seeded[SPARE_LIST], SPARE_LIST)

    _set_archived(seeded[USED_LIST], False)
    visible = client.patch(f"/api/lists/{seeded[SPARE_LIST]}",
                           json={"name": USED_LIST}).json()["detail"]
    _set_archived(seeded[USED_LIST], True)
    hidden = client.patch(f"/api/lists/{seeded[SPARE_LIST]}",
                          json={"name": USED_LIST}).json()["detail"]

    assert USED_LIST in visible and USED_LIST in hidden
    assert "Archive that one first" in visible, visible
    assert "archived" in hidden and "Rename that one first" in hidden, hidden
    assert visible != hidden


def test_a_patch_carrying_both_fields_applies_neither_when_the_name_is_refused(client, seeded):
    """A half-applied PATCH is worse than a refused one.

    The rename is the half that can be refused, so it runs first. Sent together
    with an archive, a name collision must leave the list exactly as it was —
    not archived out of the picker with its old name still on it.
    """
    _rename(seeded[USED_LIST], USED_LIST)
    _rename(seeded[SPARE_LIST], SPARE_LIST)
    _set_archived(seeded[SPARE_LIST], False)

    response = client.patch(f"/api/lists/{seeded[SPARE_LIST]}",
                            json={"name": USED_LIST, "archived": True})
    assert response.status_code == 400

    db = SessionLocal()
    try:
        row = db.get(ContactList, seeded[SPARE_LIST])
        assert row.name == SPARE_LIST
        assert not contact_service.is_archived(row.archived), (
            "the archive half of a refused PATCH was applied anyway")
    finally:
        db.close()


def test_a_patch_carrying_both_fields_applies_both_when_the_name_is_free(client, seeded):
    """The other direction, so the guard above is not passing by refusing everything."""
    _rename(seeded[SPARE_LIST], SPARE_LIST)
    _set_archived(seeded[SPARE_LIST], False)
    new_name = f"{PREFIX}renamed and archived together"

    assert client.patch(f"/api/lists/{seeded[SPARE_LIST]}",
                        json={"name": new_name, "archived": True}).status_code == 200
    db = SessionLocal()
    try:
        row = db.get(ContactList, seeded[SPARE_LIST])
        assert row.name == new_name
        assert contact_service.is_archived(row.archived)
    finally:
        db.close()
    _rename(seeded[SPARE_LIST], SPARE_LIST)
    _set_archived(seeded[SPARE_LIST], False)


def test_the_composer_carries_the_panel_and_its_script(client):
    """A5 is on the screen it is for, and its behaviour is included with it.

    Rendered rather than read: a partial that is written and never `{% include %}`d
    is a feature that exists in the repository and not in the product.
    """
    page = client.get("/campaigns").text
    assert 'id="listPanelBtn"' in page
    assert 'id="listPanel"' in page
    for symbol in ("loadListPanel", "renameList", "setListArchived",
                   "refreshListsEverywhere"):
        assert symbol in page, f"{symbol} is not on the composer page"
    # The panel reads the endpoint that carries archived lists, not the picker's.
    assert "/api/lists/manage" in page
