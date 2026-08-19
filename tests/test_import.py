"""Category-first import: preview counts, commit actuals, and a safe undo.

The fixture CSV is deliberately what the client's real exports look like: the
phone column is called `Cell`, a second one is called `Contact #`, there is a
`Company` column nothing maps to, and the file contains a repeated row, a
number that is not a number, a row with no number at all, a number already on
the blocklist and a number we already hold. A file of clean `phone` headers
would prove nothing — the header mapping is the part that fails in production.

Every count below is asserted exactly. A preview that is roughly right is a
preview the client stops reading.

Everything this module creates, it removes: `test_smoke` sends a campaign to
audience "all" and asserts an exact `sent_count`, and the suite shares one
database with no rollback between tests.
"""

import os
import pytest

from app.core.database import SessionLocal                       # noqa: E402
from app.models.blocked_number import BlockedNumber              # noqa: E402
from app.models.category import Category, ContactCategory        # noqa: E402
from app.models.contact import Contact                           # noqa: E402
from app.models.contact_list import ContactList, ContactListMember   # noqa: E402
from app.models.sms_message import SMSMessage                    # noqa: E402
from app.services import (blocklist_service, category_service,   # noqa: E402
                          contact_service, import_service)

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "fixtures", "contacts_messy.csv")

# Every number the fixture can produce, in file order.
P = {n: f"+1954555{n}" for n in
     ("0201", "0202", "0203", "0204", "0207", "0208", "0209", "0211")}

ALREADY_TAGGED = P["0207"]      # exists and is already in Food Service
OPTED_OUT = P["0208"]           # on the blocklist — will be skipped entirely
EXISTING_UNTAGGED = P["0209"]   # we hold the number; it has no category yet
MESSAGED = P["0203"]            # created by the import, then texted
HAND_TAGGED = P["0202"]         # created by the import, then hand-tagged Estates

# rows = valid_phones + unusable + duplicates
# valid_phones = opted_out + already_in_category + existing_contacts + new_contacts
EXPECTED_COUNTS = {
    "rows": 12,
    "valid_phones": 8,
    "unusable": 3,              # "not-a-number", "5", and a row with no phone cell
    "duplicates": 1,            # Ana Reyes appears twice
    "opted_out": 1,
    "already_in_category": 1,
    "existing_contacts": 1,
    "new_contacts": 5,
}


def _content():
    with open(FIXTURE, "rb") as handle:
        return handle.read()


def _purge(db):
    ids = [row.id for row in db.query(Contact).filter(Contact.phone.in_(tuple(P.values())))]
    if ids:
        db.query(SMSMessage).filter(SMSMessage.contact_id.in_(ids)).delete(
            synchronize_session=False)
        db.query(ContactCategory).filter(ContactCategory.contact_id.in_(ids)).delete(
            synchronize_session=False)
        db.query(ContactListMember).filter(ContactListMember.contact_id.in_(ids)).delete(
            synchronize_session=False)
        db.query(Contact).filter(Contact.id.in_(ids)).delete(synchronize_session=False)
    for row in db.query(ContactList).filter(ContactList.category_id.isnot(None)):
        db.query(ContactListMember).filter(ContactListMember.list_id == row.id).delete(
            synchronize_session=False)
        db.delete(row)
    db.query(BlockedNumber).filter(BlockedNumber.phone == OPTED_OUT).delete(
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
def prior_state(db):
    """What the database already knows before the file arrives.

    Three of the fixture's numbers are not new, and each lands in a different
    bucket. Without them the preview's whole reason for existing — "how much of
    this do I already have?" — is untested.
    """
    _purge(db)
    food = category_service.get_by_slug(db, "food_service")

    tagged = contact_service.upsert_contact(
        db, phone=ALREADY_TAGGED, full_name="Gina Park", source="test")
    # source="manual": a human put this one in Food Service. It has to survive
    # an undo of the import that also contains her.
    category_service.tag_contact(db, tagged.id, food.id, source="manual")

    contact_service.upsert_contact(db, phone=EXISTING_UNTAGGED,
                                   full_name="Iris Chen", source="test")
    blocklist_service.block_number(db, OPTED_OUT, reason="stop_keyword", source="manual")

    yield {"food": food}

    _purge(db)


# ─── Preview ────────────────────────────────────────────────────────────────

def test_preview_counts_are_exact(db, prior_state):
    result = import_service.preview(db, _content(), prior_state["food"].id)

    actual = {key: result[key] for key in EXPECTED_COUNTS}
    assert actual == EXPECTED_COUNTS

    assert result["rows"] == (result["valid_phones"] + result["unusable"]
                              + result["duplicates"])
    assert result["valid_phones"] == (result["opted_out"] + result["already_in_category"]
                                      + result["existing_contacts"] + result["new_contacts"])


def test_preview_reports_the_messy_header_mapping(db, prior_state):
    result = import_service.preview(db, _content(), prior_state["food"].id)
    assert result["mapped"] == {"Buyer": "name", "Cell": "phone",
                                "Contact #": "phone", "Email": "email"}
    assert result["unmapped"] == ["Company"]
    # Dan Kim's number is only in `Contact #`; `Cell` is blank for him.
    assert result["sample"][3]["phone"] == "(954) 555-0204"


def test_preview_writes_nothing(db, prior_state):
    before = db.query(Contact).count(), db.query(ContactCategory).count()
    import_service.preview(db, _content(), prior_state["food"].id)
    db.expire_all()
    assert (db.query(Contact).count(), db.query(ContactCategory).count()) == before


def test_a_category_is_required(db):
    with pytest.raises(ValueError, match="Choose a category"):
        import_service.preview(db, _content(), None)
    with pytest.raises(ValueError, match="Choose a category"):
        import_service.commit(db, _content(), None)
    with pytest.raises(LookupError):
        import_service.preview(db, _content(), 99999)


# ─── Commit ─────────────────────────────────────────────────────────────────

def test_commit_returns_the_same_counts_as_actuals(db, prior_state):
    result = import_service.commit(db, _content(), prior_state["food"].id)
    db.expire_all()

    assert {key: result[key] for key in EXPECTED_COUNTS} == EXPECTED_COUNTS
    pytest.batch_list_id = result["list_id"]

    assert result["list_name"].startswith("Food Service — ")
    assert result["list_name"].endswith(" upload")


def test_commit_tags_every_valid_row_and_skips_the_opt_out(db, prior_state):
    food_id = prior_state["food"].id
    tagged = {row.phone for row in db.query(Contact)
              .join(ContactCategory, ContactCategory.contact_id == Contact.id)
              .filter(ContactCategory.category_id == food_id)}

    # Everything usable except the blocklisted number, which is not imported at
    # all — an opt-out is not a send-time filter.
    assert tagged == set(P.values()) - {OPTED_OUT}
    assert db.query(Contact).filter(Contact.phone == OPTED_OUT).first() is None


def test_commit_creates_a_batch_list_pointing_at_the_category(db, prior_state):
    batch = db.get(ContactList, pytest.batch_list_id)
    assert batch.category_id == prior_state["food"].id
    members = db.query(ContactListMember).filter(
        ContactListMember.list_id == batch.id).all()

    assert len(members) == 7                                    # 8 valid − 1 opted out
    assert sum(m.created_contact or 0 for m in members) == 5    # new_contacts
    # 6, not 7: Gina was already in Food Service, so this import did not tag her.
    assert sum(m.created_tag or 0 for m in members) == 6


def test_a_second_upload_the_same_day_gets_its_own_list(db, prior_state):
    second = import_service.commit(db, _content(), prior_state["food"].id)
    assert second["list_id"] != pytest.batch_list_id
    assert second["list_name"].endswith(" (2)")
    # Nothing new to do — the first import already created and tagged everyone.
    assert second["new_contacts"] == 0
    assert second["already_in_category"] == 7

    import_service.undo(db, second["list_id"])
    db.expire_all()


def test_a_committed_import_shows_up_in_the_category_count(db, prior_state):
    summaries = {s["selector"]: s for s in contact_service.list_summaries(db)}
    assert summaries["category:food_service"]["count"] == 7
    assert len(contact_service.resolve_audience(db, "category:food_service")) == 7


# ─── Undo ───────────────────────────────────────────────────────────────────

def test_undo_reverses_the_batch_and_nothing_else(db, prior_state):
    """The three things undo must refuse to destroy.

    A hand-added tag, a contact with message history, and the blocklist. Each
    is something a client cannot get back, and each is something a naive
    "delete everything this import touched" would take.
    """
    food_id = prior_state["food"].id
    estates = category_service.get_by_slug(db, "estates")

    # A human tags one of the imported buyers into a second category, and one of
    # them gets texted. Both happen after the import, before the undo — which is
    # exactly when a client realises he picked the wrong category.
    hand_tagged = db.query(Contact).filter(Contact.phone == HAND_TAGGED).first()
    category_service.tag_contact(db, hand_tagged.id, estates.id, source="manual")

    messaged = db.query(Contact).filter(Contact.phone == MESSAGED).first()
    db.add(SMSMessage(contact_id=messaged.id, phone=messaged.phone,
                      message="Preview night is Thursday.", status="delivered",
                      segments=1))
    db.commit()

    blocked_before = db.query(BlockedNumber).count()
    result = import_service.undo(db, pytest.batch_list_id)
    db.expire_all()

    assert result["memberships_removed"] == 7
    assert result["tags_removed"] == 6
    assert result["contacts_deleted"] == 3      # 0201, 0204, 0211
    assert result["contacts_kept"] == 2         # hand-tagged and messaged

    assert db.get(ContactList, pytest.batch_list_id) is None
    assert db.query(ContactListMember).filter(
        ContactListMember.list_id == pytest.batch_list_id).count() == 0

    # The hand-added tag survives, and so does the contact carrying it.
    survivor = db.query(Contact).filter(Contact.phone == HAND_TAGGED).first()
    assert survivor is not None
    assert {row.category_id for row in db.query(ContactCategory).filter(
        ContactCategory.contact_id == survivor.id)} == {estates.id}

    # The messaged contact survives even though it is now orphaned. An orphan
    # contact is recoverable; a deleted one with message history is not.
    kept = db.query(Contact).filter(Contact.phone == MESSAGED).first()
    assert kept is not None
    assert db.query(SMSMessage).filter(SMSMessage.contact_id == kept.id).count() == 1

    # Gina's manual Food Service tag predates the import and is untouched.
    gina = db.query(Contact).filter(Contact.phone == ALREADY_TAGGED).first()
    gina_tag = db.query(ContactCategory).filter(
        ContactCategory.contact_id == gina.id,
        ContactCategory.category_id == food_id).first()
    assert gina_tag is not None and gina_tag.source == "manual"

    # Iris predates the import too; only the tag it added is gone.
    iris = db.query(Contact).filter(Contact.phone == EXISTING_UNTAGGED).first()
    assert iris is not None
    assert db.query(ContactCategory).filter(
        ContactCategory.contact_id == iris.id).count() == 0

    # An opt-out outlives any import, including the one that surfaced it.
    assert db.query(BlockedNumber).count() == blocked_before
    assert blocklist_service.is_blocked(db, OPTED_OUT)


def test_undo_refuses_a_list_that_is_not_an_import_batch(db):
    plain = contact_service.get_or_create_list(db, "module-2 not a batch", source="test")
    try:
        with pytest.raises(ValueError, match="not an import batch"):
            import_service.undo(db, plain.id)
    finally:
        db.delete(plain)
        db.commit()

    with pytest.raises(LookupError):
        import_service.undo(db, 99999)
