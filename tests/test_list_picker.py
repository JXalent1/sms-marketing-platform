"""Session 5i: one clock on `contact_lists.created_at`, and the picker it feeds.

Three things, and the first is the reason the other two are trustworthy.

**A1 — the clock.** `contact_lists.created_at` had two writers keeping two
clocks and two spellings: `contact_service.get_or_create_list()` omitted the
column and got SQLite's `CURRENT_TIMESTAMP` (UTC, `YYYY-MM-DD HH:MM:SS`), and
`import_service.commit()` wrote `datetime.now().isoformat()` (local, `T`,
microseconds). Both spellings are in the live database. Nothing compared them
until 5i made recency the sort order of the picker, the dashboard cards and the
composer — and a lexicographic comparison across the two is wrong in the
direction nobody checks, because a space (0x20) sorts before a `T` (0x54).

**A2 — the picker.** The pinned entry first, then every list newest first, and no
category entries. The resolver keeps every `category:` branch it ever had, which
is the half a helpful cleanup would delete.

The ordering assertions go through `GET /api/campaigns/audiences` rather than
through the sort itself. A property proved of a helper is not proved of its
caller: if this file only drove `list_summaries()`, the endpoint could be
reverted to any order at all and the suite would stay green.

Everything this module creates, it removes. The suite runs against one database
with no rollback between tests.
"""

import importlib.util
import os
import pathlib
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.main import app
from app.models.contact import Contact
from app.models.contact_list import (
    ContactList, ContactListMember, parse_created_at,
)
from app.models.sms_message import SMSMessage
from app.services import contact_service, import_service

PASSWORD = os.environ["ADMIN_PASSWORD"]

PREFIX = "5i-picker "

# The two spellings the live database actually holds, on the same day, arranged
# so the true order and the string order disagree.
#
#   server default  "2026-08-27 23:30:00"        <- newer, and sorts FIRST as a
#   application     "2026-08-27T10:00:00.000000"    string under `< `, because
#                                                   " " < "T"
#
# Descending by string therefore puts the OLDER row first, which is exactly the
# defect: a list uploaded this morning would outrank one created tonight. Real
# spellings, not synthetic ones — a fixture nobody would write proves nothing
# about a defect that is in production today.
SERVER_DEFAULT_SPELLING = "2026-08-27 23:30:00"
APPLICATION_SPELLING = "2026-08-27T10:00:00.000000"

NEWER_LIST = f"{PREFIX}created tonight"      # the server-default spelling
OLDER_LIST = f"{PREFIX}created this morning"  # the application spelling

# 954-555-31xx: this module's slice of the reserved fiction block.
PHONES = [f"+195455531{n:02d}" for n in range(3)]


def _load_migration():
    """The 5i migration module, imported by path.

    Driven directly rather than re-implemented, because a test that rewrites the
    arithmetic checks its own arithmetic. `alembic/versions/` is not a package.
    """
    path = (pathlib.Path(__file__).resolve().parent.parent / "alembic" / "versions"
            / "f4a1c7d90e52_normalise_contact_list_created_at.py")
    spec = importlib.util.spec_from_file_location("migration_5i", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _restore_spellings(db):
    """Put the two raw spellings back on the fixture rows.

    Called by every test that depends on them, rather than trusted from the
    fixture, because `test_the_migration_converts_a_space_row_and_leaves_a_t_row
    _alone` **rewrites one of them** — that is its job. The ordering test used to
    run after it and read two isoformat values, where a string comparison
    happens to give the right answer, so the mutation harness reported the
    string-ordering mutation as surviving. A test that needs its neighbours to
    have run, or not to have run, proves nothing about the criterion it is named
    for.
    """
    for name, spelling in ((NEWER_LIST, SERVER_DEFAULT_SPELLING),
                           (OLDER_LIST, APPLICATION_SPELLING)):
        db.query(ContactList).filter(ContactList.name == name).update(
            {"created_at": spelling}, synchronize_session=False)
    db.commit()


def _purge(db):
    ids = [row.id for row in db.query(Contact).filter(Contact.phone.in_(PHONES))]
    if ids:
        db.query(SMSMessage).filter(SMSMessage.contact_id.in_(ids)).delete(
            synchronize_session=False)
        db.query(Contact).filter(Contact.id.in_(ids)).delete(synchronize_session=False)
    list_ids = [row.id for row in
                db.query(ContactList).filter(ContactList.name.like(f"{PREFIX}%"))]
    if list_ids:
        db.query(ContactListMember).filter(
            ContactListMember.list_id.in_(list_ids)).delete(synchronize_session=False)
        db.query(ContactList).filter(ContactList.id.in_(list_ids)).delete(
            synchronize_session=False)
    db.commit()


@pytest.fixture(scope="module")
def seeded():
    """One list per spelling, each with one contact so the counts are non-zero."""
    db = SessionLocal()
    try:
        _purge(db)
        ids = {}
        for name, spelling, phone in (
                (NEWER_LIST, SERVER_DEFAULT_SPELLING, PHONES[0]),
                (OLDER_LIST, APPLICATION_SPELLING, PHONES[1])):
            contact = Contact(phone=phone, full_name=name, source="test",
                              attributes={}, created_at="2026-08-27T09:00:00")
            db.add(contact)
            db.flush()
            listing = ContactList(name=name, source="test", created_at=spelling)
            db.add(listing)
            db.flush()
            db.add(ContactListMember(list_id=listing.id, contact_id=contact.id,
                                     added_at="2026-08-27T09:00:00"))
            ids[name] = listing.id
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


# ─── A1: one writer, one clock, one spelling ────────────────────────────────

def test_the_model_carries_no_server_default_on_created_at():
    """The column that made this a two-writer column.

    Asserted on the mapped column rather than on the source, because the defect
    was never visible in a grep: the default fired on the inserts that *omitted*
    the value, which is the shape a reader does not notice.

    Both halves. Removing the server default only moves the problem if an insert
    can still omit the column — then the value is NULL and the list sorts last
    forever — so the Python-side default is what makes "one writer" a property of
    the model rather than a convention every future call site has to remember.
    """
    column = ContactList.__table__.c.created_at
    assert column.server_default is None, (
        "contact_lists.created_at has a server default again — SQLite's "
        "CURRENT_TIMESTAMP is UTC and every other timestamp here is local")
    assert column.default is not None, (
        "an insert that omits created_at now writes NULL or falls through to "
        "the table's own DDL default, which is the UTC one")


def test_an_insert_that_omits_created_at_still_gets_the_application_clock(db_session):
    """The DDL default is still on the table and cannot be removed here.

    Rebuilding a SQLite table to drop a column default is escalation item 8, so
    `DEFAULT (CURRENT_TIMESTAMP)` is still in the schema. What stops it firing is
    the model's Python-side default — this is the test that says so, by doing the
    thing that used to trigger it: constructing a row without the column.
    """
    row = ContactList(name=f"{PREFIX}omitted the column", source="test")
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    try:
        assert "T" in row.created_at, (
            f"an omitted created_at was written {row.created_at!r} — that is "
            f"SQLite's CURRENT_TIMESTAMP, in UTC")
        assert abs((parse_created_at(row.created_at)
                    - datetime.now()).total_seconds()) < 300
    finally:
        db_session.delete(row)
        db_session.commit()


def test_both_insert_paths_write_the_same_spelling_from_the_same_clock(db_session):
    """Criterion 1. The two writers that disagreed, agreeing.

    Both are checked against the same two properties — a `T` separator, and a
    value within a few minutes of the *local* clock — because the defect had two
    halves and fixing only the spelling would leave every hand-created list five
    hours in the future.
    """
    now = datetime.now()

    listing = contact_service.get_or_create_list(
        db_session, f"{PREFIX}written by the service", source="test")
    imported = import_service.commit(
        db_session, b"phone\n9545553199\n", None,
        list_name=f"{PREFIX}written by the importer")
    from_import = db_session.get(ContactList, imported["list_id"])

    try:
        for row in (listing, from_import):
            assert row.created_at, f"{row.name} was written with no created_at"
            assert "T" in row.created_at, (row.name, row.created_at)
            assert " " not in row.created_at, (row.name, row.created_at)
            written = parse_created_at(row.created_at)
            assert abs((written - now).total_seconds()) < 300, (
                f"{row.name} was written at {row.created_at!r}, which is "
                f"{(written - now).total_seconds() / 3600:.1f} hours from the "
                f"local clock — that is the UTC server default, not us")
    finally:
        import_service.undo(db_session, imported["list_id"])
        db_session.query(ContactList).filter(ContactList.id == listing.id).delete(
            synchronize_session=False)
        db_session.commit()


def test_the_migration_converts_a_space_row_and_leaves_a_t_row_alone(db_session, seeded):
    """Criterion 1's other half, driving the migration's own `upgrade()`.

    Not the helper underneath it: the classification — "a space means the server
    default wrote it, means UTC, means convert" — lives in the loop's branch, and
    a test that only called the converter would pass with that branch inverted.
    """
    migration = _load_migration()

    # A row that is already converted proves nothing about the conversion.
    _restore_spellings(db_session)

    class _Op:
        """Stands in for alembic's `op`, which needs a migration context."""

        @staticmethod
        def get_bind():
            return db_session.connection()

    original_op = migration.op
    migration.op = _Op
    try:
        migration.upgrade()
        db_session.commit()
    finally:
        migration.op = original_op

    db_session.expire_all()
    converted = db_session.query(ContactList).filter(
        ContactList.name == NEWER_LIST).one().created_at
    untouched = db_session.query(ContactList).filter(
        ContactList.name == OLDER_LIST).one().created_at

    # The T row is byte-identical. It was written by the application clock and
    # there is nothing to convert; shifting it would move a correct row.
    assert untouched == APPLICATION_SPELLING

    # The space row is now isoformat, and it names the same *instant* it named
    # before — read as UTC then, read as local now. Asserted as an instant
    # rather than by re-deriving the offset, so this checks the migration's
    # arithmetic instead of restating it.
    assert "T" in converted
    assert (datetime.fromisoformat(converted).astimezone()
            == datetime.fromisoformat(SERVER_DEFAULT_SPELLING).replace(
                tzinfo=timezone.utc))

    # Idempotent: a second run finds a T row and leaves it.
    migration.op = _Op
    try:
        migration.upgrade()
        db_session.commit()
    finally:
        migration.op = original_op
    db_session.expire_all()
    assert db_session.query(ContactList).filter(
        ContactList.name == NEWER_LIST).one().created_at == converted


def test_a_value_matching_neither_spelling_is_left_alone():
    """The migration converts what it can prove and touches nothing else.

    A bare date is not either writer's output. "Not a T, therefore the server
    default" would say otherwise and shift it by the local offset, which is a
    rewrite of a row nobody can classify.
    """
    migration = _load_migration()
    assert migration.utc_text_to_local_iso(SERVER_DEFAULT_SPELLING) is not None
    assert migration.utc_text_to_local_iso(APPLICATION_SPELLING) is None
    assert migration.utc_text_to_local_iso("2026-08-27") is None
    assert migration.utc_text_to_local_iso("") is None
    assert migration.utc_text_to_local_iso(None) is None


def test_an_unparseable_created_at_sorts_last_and_does_not_raise():
    """It renders in a picker and in page headers. A helper that throws turns one
    bad row into a 500 on a screen that was only trying to sort."""
    assert parse_created_at("nonsense") < parse_created_at("2000-01-01T00:00:00")
    assert parse_created_at(None) < parse_created_at("2000-01-01T00:00:00")
    assert parse_created_at("") < parse_created_at("2000-01-01T00:00:00")


# ─── A2: the picker's order and shape, through the endpoint ─────────────────

def test_the_picker_orders_lists_by_the_parsed_value_not_the_string(
        client, seeded, db_session):
    """Criterion 1's visible half, on the endpoint the composer actually calls.

    The two fixtures carry the two real spellings on the same day, arranged so
    the string order is the reverse of the true one. A `ORDER BY created_at DESC`
    — or a Python `sorted(..., key=str, reverse=True)` — puts this morning's list
    above tonight's.

    The spellings are restored first rather than assumed: the migration test in
    this file rewrites one of them, and reading two isoformat values here would
    make a string comparison give the right answer for the wrong reason.
    """
    _restore_spellings(db_session)
    audiences = client.get("/api/campaigns/audiences").json()["audiences"]
    order = [a["label"] for a in audiences]
    assert NEWER_LIST in order and OLDER_LIST in order, order
    assert order.index(NEWER_LIST) < order.index(OLDER_LIST), (
        "the picker put this morning's list above tonight's — that is a "
        "lexicographic comparison across the two spellings, not a date order")

    # And the premise, so this test cannot pass for the wrong reason: the string
    # comparison really does disagree with the true order on these two values.
    assert SERVER_DEFAULT_SPELLING < APPLICATION_SPELLING
    assert parse_created_at(SERVER_DEFAULT_SPELLING) > parse_created_at(
        APPLICATION_SPELLING)


def test_the_pinned_entry_is_first_and_is_the_one_constant(client):
    """Criterion 3. The star, then everything else."""
    audiences = client.get("/api/campaigns/audiences").json()["audiences"]
    assert audiences[0]["selector"] == "all"
    assert audiences[0]["label"] == contact_service.ALL_BIDDERS_LABEL
    assert audiences[0]["kind"] == "all"


def test_the_picker_offers_no_category_entry(client):
    """Criterion 3. The picker stops producing them; the resolver still eats them."""
    audiences = client.get("/api/campaigns/audiences").json()["audiences"]
    assert not [a for a in audiences if a["kind"] == "category"]
    assert not [a for a in audiences if a["selector"].startswith("category:")]
    # Every remaining entry is one of the two kinds the client now picks from.
    assert {a["kind"] for a in audiences} <= {"all", "list"}


def test_every_entry_carries_what_the_dashboard_card_needs(client, seeded):
    """A2: the picker and the cards are one query, so they cannot disagree."""
    audiences = client.get("/api/campaigns/audiences").json()["audiences"]
    for entry in audiences:
        assert set(entry) >= {"selector", "label", "count", "kind",
                              "last_sent_at", "days_since_sent"}, entry


def test_a_list_with_no_active_members_is_offered_and_reads_zero(db_session):
    """A list nobody is on is information; a missing row is confusing.

    And the count is *active* contacts, matching what a send would reach and
    matching `audience_count()` on the same selector — a picker that promised 40
    people a campaign could never text would be worse than one that said 0.
    """
    empty = contact_service.get_or_create_list(
        db_session, f"{PREFIX}nobody on it", source="test")
    try:
        entry = {s["selector"]: s for s in contact_service.list_summaries(db_session)}
        assert entry[f"list:{empty.id}"]["count"] == 0
        assert entry[f"list:{empty.id}"]["count"] == contact_service.audience_count(
            db_session, f"list:{empty.id}")
        assert entry[f"list:{empty.id}"]["days_since_sent"] is None
    finally:
        db_session.query(ContactList).filter(ContactList.id == empty.id).delete(
            synchronize_session=False)
        db_session.commit()


def test_a_deactivated_member_leaves_the_count_at_zero(db_session):
    """The picker's count is what a send would reach, not what the table holds."""
    listing = contact_service.get_or_create_list(
        db_session, f"{PREFIX}all deactivated", source="test")
    contact = contact_service.upsert_contact(
        db_session, phone=PHONES[2], full_name="Gone", source="test")
    contact_service.add_to_list(db_session, listing.id, contact.id)
    contact.is_active = 0
    db_session.commit()
    try:
        entry = {s["selector"]: s for s in contact_service.list_summaries(db_session)}
        assert entry[f"list:{listing.id}"]["count"] == 0
        assert entry[f"list:{listing.id}"]["count"] == contact_service.audience_count(
            db_session, f"list:{listing.id}")
    finally:
        db_session.query(ContactListMember).filter(
            ContactListMember.list_id == listing.id).delete(synchronize_session=False)
        db_session.query(ContactList).filter(ContactList.id == listing.id).delete(
            synchronize_session=False)
        db_session.query(Contact).filter(Contact.id == contact.id).delete(
            synchronize_session=False)
        db_session.commit()


# ─── Criterion 4: history still resolves ────────────────────────────────────

def test_a_historical_category_selector_still_resolves_and_labels(db_session):
    """Criterion 4 — the one that stops a helpful cleanup breaking every report.

    Every campaign this client has run so far stores `category:<slug>` in
    `campaigns.audience`, and `audience_label()` renders it in the campaign rail,
    in history and in every per-campaign report. The picker stops *producing*
    these; the resolver must keep *consuming* them.

    All four consumers, because they are four separate branches and a cleanup
    would take them one at a time.
    """
    from app.models.category import Category

    slug = db_session.query(Category).filter(Category.is_active == 1).first().slug
    selector = f"category:{slug}"

    # _term_ids_query, through both of its callers.
    resolved = contact_service.resolve_audience(db_session, selector)
    assert contact_service.audience_count(db_session, selector) == len(resolved)

    # _term_label, through audience_label.
    label = contact_service.audience_label(db_session, selector)
    assert label and label != selector, (
        f"a stored {selector!r} rendered as itself — the label branch is gone "
        f"and every report older than 5i now reads as a raw selector")

    # And the intersection grammar, which is a second path into the same branch.
    listing = contact_service.get_or_create_list(
        db_session, f"{PREFIX}history intersection", source="test")
    try:
        combined = f"{selector}&list:{listing.id}"
        assert contact_service.audience_count(db_session, combined) == 0
        assert "∩" in contact_service.audience_label(db_session, combined)
    finally:
        db_session.query(ContactList).filter(ContactList.id == listing.id).delete(
            synchronize_session=False)
        db_session.commit()


# ─── Freshness ──────────────────────────────────────────────────────────────

def test_freshness_comes_from_messages_and_not_from_an_audience_string(db_session):
    """A campaign records who it *meant* to text; `sms_messages` records who was.

    Also the em-dash rule at its source: a list nobody has been texted on reports
    `None`, never 0. `days_since_sent` is what the dashboard turns into `—`, and
    "0" there would read as "texted today".
    """
    listing = contact_service.get_or_create_list(
        db_session, f"{PREFIX}freshness", source="test")
    contact = contact_service.upsert_contact(
        db_session, phone=PHONES[2], full_name="Freshness", source="test")
    contact_service.add_to_list(db_session, listing.id, contact.id)
    db_session.commit()

    def entry():
        return {s["selector"]: s for s in
                contact_service.list_summaries(db_session)}[f"list:{listing.id}"]

    try:
        assert entry()["last_sent_at"] is None
        assert entry()["days_since_sent"] is None

        # A failed message is not contact. This is the assertion that keeps a
        # list from looking texted because a blast to it went nowhere.
        db_session.add(SMSMessage(contact_id=contact.id, phone=contact.phone,
                                  message="x", status="failed", segments=1,
                                  sent_at=datetime.now().isoformat()))
        db_session.commit()
        assert entry()["days_since_sent"] is None

        two_days = (datetime.now() - timedelta(days=2)).isoformat()
        db_session.add(SMSMessage(contact_id=contact.id, phone=contact.phone,
                                  message="x", status="delivered", segments=1,
                                  sent_at=two_days))
        db_session.commit()
        assert entry()["days_since_sent"] == 2
    finally:
        db_session.query(SMSMessage).filter(
            SMSMessage.contact_id == contact.id).delete(synchronize_session=False)
        db_session.query(ContactListMember).filter(
            ContactListMember.list_id == listing.id).delete(synchronize_session=False)
        db_session.query(ContactList).filter(ContactList.id == listing.id).delete(
            synchronize_session=False)
        db_session.query(Contact).filter(Contact.id == contact.id).delete(
            synchronize_session=False)
        db_session.commit()


def test_the_four_modules_read_one_sent_status_set():
    """The property the constant exists for, not the tuple's contents.

    A test pinning `("sent", "delivered")` goes red on the intended change and
    green on the dangerous one — a second literal appearing in one of these
    modules. What matters is that the dashboard's freshness, the report's
    delivered figure, the history rail and the picker's recency all mean the same
    thing by "texted", and identity is what says they read one definition rather
    than four that happen to agree.
    """
    from app.models import sms_message
    from app.services import dashboard_service, history_service, report_service

    assert (dashboard_service.SENT_STATUSES
            is report_service.SENT_STATUSES
            is history_service.SENT_STATUSES
            is contact_service.SENT_STATUSES
            is sms_message.SENT_STATUSES)


def test_freshness_is_bound_to_the_sent_set_and_not_to_the_billable_one(
        db_session, monkeypatch):
    """The two sets have the same members today, so identity cannot tell them
    apart — CPython folds equal literal tuples in one module to one object, and
    an `is not` assertion here passes or fails for a reason that has nothing to
    do with this codebase.

    What can be told apart is the *binding*. Change what "texted" means and the
    freshness figure must follow; a query reaching for `BILLABLE_STATUSES`
    instead would not move, and a commercial change to the billable set would
    then silently rewrite the figures the client schedules his auctions against.
    """
    listing = contact_service.get_or_create_list(
        db_session, f"{PREFIX}status binding", source="test")
    contact = contact_service.upsert_contact(
        db_session, phone=PHONES[2], full_name="Binding", source="test")
    contact_service.add_to_list(db_session, listing.id, contact.id)
    db_session.add(SMSMessage(contact_id=contact.id, phone=contact.phone,
                              message="x", status="sent", segments=1,
                              sent_at=datetime.now().isoformat()))
    db_session.commit()

    def freshness():
        return {s["selector"]: s for s in
                contact_service.list_summaries(db_session)
                }[f"list:{listing.id}"]["days_since_sent"]

    try:
        assert freshness() == 0, "the seed itself is not being counted"

        # "Texted" now excludes a message the carrier merely accepted.
        monkeypatch.setattr(contact_service, "SENT_STATUSES", ("delivered",))
        assert freshness() is None, (
            "the freshness query did not follow SENT_STATUSES — it is reading "
            "some other set, and the billable set is the one it must not read")
    finally:
        monkeypatch.undo()
        db_session.query(SMSMessage).filter(
            SMSMessage.contact_id == contact.id).delete(synchronize_session=False)
        db_session.query(ContactListMember).filter(
            ContactListMember.list_id == listing.id).delete(synchronize_session=False)
        db_session.query(ContactList).filter(ContactList.id == listing.id).delete(
            synchronize_session=False)
        db_session.query(Contact).filter(Contact.id == contact.id).delete(
            synchronize_session=False)
        db_session.commit()

    # And the module does not hold the billable set at all, which is the other
    # shape the same mistake arrives in: an import rather than a literal.
    assert not hasattr(contact_service, "BILLABLE_STATUSES"), (
        "contact_service imported BILLABLE_STATUSES — freshness and billing are "
        "separate questions that happen to share an answer today")


@pytest.fixture
def db_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
