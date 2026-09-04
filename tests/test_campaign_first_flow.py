"""5e A1–A3: a campaign built on an upload, an optional tag, one contact by hand.

What these assert, in one line each:

  A1  the campaign's audience is the list its own upload created, that list is
      named for the campaign, and the three pre-existing audience paths still work
  A2  an untagged upload is a first-class outcome — `category_id` NULL, and it sends
  A3  a hand-added contact is normalised and blocklist-checked, exactly as an
      import is, and a blocklisted number does not become sendable by being typed

The rules this file inherits are in `_campaign_flow_setup.py`: leave no rows
behind, and give back the rate-limit budget you spend.
"""

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.main import app
from app.models.campaign import Campaign
from app.models.contact import Contact
from app.models.contact_list import ContactList, ContactListMember
from app.models.sms_message import SMSMessage
from app.services import blocklist_service, campaign_builder, import_service
from app.services.campaign_service import CampaignError, CampaignService
from tests._campaign_flow_setup import (
    BLOCKED_PHONE, MANUAL_PHONE, MESSAGE, NAME_PREFIX, csv_bytes, default_csv,
    food_service_id, purged_db_fixture_body, rate_limit_fixture_body, take,
)

PASSWORD = "devpassword123"


@pytest.fixture(scope="module", autouse=True)
def rate_limits():
    yield from rate_limit_fixture_body()


@pytest.fixture(scope="module")
def db():
    yield from purged_db_fixture_body()


@pytest.fixture(scope="module")
def client():
    c = TestClient(app)
    login = c.post("/login", data={"username": "admin", "password": PASSWORD})
    assert login.status_code in (200, 302), (
        f"login failed with {login.status_code} — every assertion below would "
        f"have run against a 401 body and passed by containing nothing"
    )
    return c


def _render(db):
    return CampaignService(db).render


# ─── A1: the upload is step one ─────────────────────────────────────────────

def test_the_campaigns_audience_is_the_list_its_own_upload_created(db):
    """The whole of A1 in one assertion chain, end to end.

    Three separate facts, and the chain is the point: the list carries the
    campaign's name, the campaign's audience selector points at *that* list's id,
    and the people on it are the people in the file. Assert only the first and a
    campaign could be named correctly while pointing somewhere else.
    """
    phones = take(3)
    name = f"{NAME_PREFIX}Italian restaurants"
    campaign, imported = campaign_builder.create_campaign_from_upload(
        db, _render(db), name=name, message_template=MESSAGE,
        content=default_csv(phones),
    )

    assert imported["list_name"] == name, "the list is named for the campaign"
    assert campaign.audience == f"list:{imported['list_id']}"

    members = {row.phone for row in db.query(Contact).join(
        ContactListMember, ContactListMember.contact_id == Contact.id
    ).filter(ContactListMember.list_id == imported["list_id"])}
    assert members == set(phones)

    # And the queued messages are those three people, not a count that happens
    # to match.
    queued = {m.phone for m in db.query(SMSMessage).filter(
        SMSMessage.campaign_id == campaign.id)}
    assert queued == set(phones)


def test_the_preview_counts_match_the_file(db, client):
    """The importer's own preview, reached through the composer's endpoint.

    Reusing `import_service.preview()` rather than writing a second counter is
    what makes the composer's numbers and the commit's numbers the same numbers.
    This asserts the identity holds through the new route, on a file with one of
    everything: a repeat, and a row whose number is not a number.
    """
    phones = take(2)
    content = csv_bytes([
        ("Buyer 0", phones[0],      "A"),
        ("Buyer 1", phones[1],      "B"),
        ("Repeat",  phones[1],      "B"),
        ("Junk",    "not-a-number", "C"),
    ])
    db.expire_all()
    before = db.query(Contact).count()

    response = client.post("/api/campaigns/upload-preview",
                           files={"file": ("list.csv", content, "text/csv")})
    assert response.status_code == 200, response.text
    counts = response.json()

    assert counts["rows"] == 4
    assert counts["valid_phones"] == 2
    assert counts["duplicates"] == 1
    assert counts["unusable"] == 1
    # The identity the import module documents. If this drifts, the report stops
    # reconciling and the client is left guessing which bucket absorbed it.
    assert counts["rows"] == counts["valid_phones"] + counts["unusable"] + counts["duplicates"]

    # Writes nothing. A preview that created contacts would make "check the file"
    # the same action as "import it". Compared against a count rather than
    # asserting the rows are absent: earlier tests in this module legitimately
    # created some of these contacts, and a test that only passes first in the
    # file is a test that will be deleted the day somebody reorders it.
    db.expire_all()
    assert db.query(Contact).count() == before


def test_a_name_collision_suffixes_rather_than_erroring(db):
    """`ContactList.name` is unique, so this is not a nicety.

    Two campaigns of the same name on the same day is the ordinary case — he
    re-runs "Italian restaurants" next month. Without the suffix the second one
    is an IntegrityError partway through a commit, with a list already written.
    """
    name = f"{NAME_PREFIX}Same name twice"
    first, first_import = campaign_builder.create_campaign_from_upload(
        db, _render(db), name=name, message_template=MESSAGE,
        content=default_csv(take(2)))
    second, second_import = campaign_builder.create_campaign_from_upload(
        db, _render(db), name=name, message_template=MESSAGE,
        content=default_csv(take(2)))

    assert first_import["list_name"] == name
    assert second_import["list_name"] == f"{name} (2)"
    assert first_import["list_id"] != second_import["list_id"]
    # Two campaigns, two audiences. The second must not have inherited the first.
    assert first.audience != second.audience


def test_a_failed_campaign_rolls_its_import_back(db):
    """No orphan list, no orphan contacts, and the name is free to reuse.

    The failure mode this prevents is subtle and compounding: a rejected campaign
    that left its list behind means the *next* attempt at the same name collides
    with the list the last attempt orphaned, and the client is looking at
    "Italian restaurants (4)" wondering what happened to one through three.

    Driven with a file whose every usable number is opted out, which is the
    realistic way this happens rather than a contrived error.
    """
    blocklist_service.block_number(db, BLOCKED_PHONE, reason="stop_keyword",
                                   source="manual")
    name = f"{NAME_PREFIX}All opted out"
    lists_before = db.query(ContactList).count()

    with pytest.raises(CampaignError) as raised:
        campaign_builder.create_campaign_from_upload(
            db, _render(db), name=name, message_template=MESSAGE,
            content=csv_bytes([("Opted Out", BLOCKED_PHONE, "X")]))

    # The message names the cause. "No contacts matched audience 'list:47'" is
    # true and useless — it quotes an id he never chose.
    assert "opt-out list" in str(raised.value)
    db.expire_all()
    assert db.query(ContactList).count() == lists_before
    assert db.query(ContactList).filter(ContactList.name == name).count() == 0
    assert db.query(Campaign).filter(Campaign.name == name).count() == 0


@pytest.mark.parametrize("selector", ["all", "category", "list"])
def test_the_three_pre_existing_audience_paths_still_create_campaigns(db, selector):
    """A1 adds a path; it must not remove one. Criterion 3.

    All three go through `POST /api/campaigns`'s own service call with a category,
    which is the behaviour those paths have had since module 4 and which 5e
    deliberately did not relax — for them the audience does not say which auction
    the message is about, so the category still has to.
    """
    category_id = food_service_id(db)

    # Every branch seeds its own contacts, including "all". Leaning on whatever
    # other modules happen to have left in the shared database is how a test
    # passes in a full run and fails the moment somebody runs it on its own —
    # which is exactly what this one did until the acceptance script, which runs
    # each criterion's tests in isolation, caught it.
    if selector == "list":
        _, imported = campaign_builder.create_campaign_from_upload(
            db, _render(db), name=f"{NAME_PREFIX}source for list path",
            message_template=MESSAGE, content=default_csv(take(3)))
        audience = f"list:{imported['list_id']}"
    elif selector == "category":
        # Tagged through the upload flow rather than by hand, so this module
        # owns every row it puts in `contact_categories` and the purge takes
        # them back out. Tagging real contacts into a seeded category and
        # leaving them there would move the counts `test_categories` asserts on.
        campaign_builder.create_campaign_from_upload(
            db, _render(db), name=f"{NAME_PREFIX}source for category path",
            message_template=MESSAGE, content=default_csv(take(3)),
            category_id=category_id)
        audience = "category:food_service"
    else:
        campaign_builder.create_campaign_from_upload(
            db, _render(db), name=f"{NAME_PREFIX}source for all path",
            message_template=MESSAGE, content=default_csv(take(3)))
        audience = "all"

    campaign = CampaignService(db).create_campaign(
        name=f"{NAME_PREFIX}existing path {selector}",
        message_template=MESSAGE, audience=audience, category_id=category_id)

    assert campaign.status == "draft"
    assert campaign.audience == audience
    assert campaign.category_id == category_id
    assert campaign.total_recipients > 0, (
        f"the {selector} path resolved to nobody — it is no longer a working path")


def test_the_ordinary_path_takes_a_list_audience_with_no_category(db):
    """5i supersedes 5e's rule for this case, deliberately.

    This test used to assert the opposite: that the ordinary create path with a
    list audience still refused without a category, so `list_audience` could not
    become a flag somebody passes to make an inconvenient error go away. That
    was right while the composer still asked for a category. Session 5i took the
    category picker off the screen, so a campaign pointed at a list has no
    category to give and no way to type an override — and a rule nothing can
    satisfy is not a guard, it is a dead end.

    What did **not** change is asserted next door, in
    `test_a_hand_written_category_selector_still_demands_a_category`: the rule
    still holds for the one selector that names a niche instead of an audience.
    """
    _, imported = campaign_builder.create_campaign_from_upload(
        db, _render(db), name=f"{NAME_PREFIX}source for rule check",
        message_template=MESSAGE, content=default_csv(take(2)))

    campaign = CampaignService(db).create_campaign(
        name=f"{NAME_PREFIX}list audience no category", message_template=MESSAGE,
        audience=f"list:{imported['list_id']}")

    assert campaign.status == "draft"
    assert campaign.category_id is None
    # Both halves. Recording this as a cross-category override would put a
    # decision nobody made into the audit trail — and since 5i there is no
    # screen that can make it, so a 1 in that column would be a fiction.
    assert not campaign.cross_category_override


def test_a_hand_written_category_selector_still_demands_a_category(db):
    """The one case 5i left the module-4 rule standing on.

    No UI path can produce a `category:` selector any more, so a caller writing
    one is doing something deliberate — and for that selector the audience
    genuinely does not say which auction the message is about, which is the
    whole content of the original rule.
    """
    with pytest.raises(CampaignError, match="no category"):
        CampaignService(db).create_campaign(
            name=f"{NAME_PREFIX}hand-written category selector",
            message_template=MESSAGE, audience="category:food_service")


# ─── A2: the category tag is optional ───────────────────────────────────────

def test_an_untagged_upload_produces_a_campaign_that_sends(db):
    """Criterion 4. Untagged is an outcome, not a warning state.

    `category_id` NULL **and** `cross_category_override` 0 — both halves matter.
    Recording a lone uploaded list as a cross-category override would put a
    decision nobody made into the audit trail, and would make the override
    column useless as evidence of anything.
    """
    campaign, imported = campaign_builder.create_campaign_from_upload(
        db, _render(db), name=f"{NAME_PREFIX}untagged",
        message_template=MESSAGE, content=default_csv(take(3)))

    assert campaign.category_id is None
    assert campaign.cross_category_override == 0

    batch = db.get(ContactList, imported["list_id"])
    assert batch.category_id is None
    assert imported["category_label"] is None
    assert imported["already_in_category"] == 0, (
        "nothing can already be in a category the upload is not tagging")

    # And it sends. The dry-run provider is what "sends normally" means here —
    # SMS_PROVIDER is console in conftest and nothing in this suite reaches a
    # carrier.
    sent = asyncio.run(CampaignService(db).send_campaign(campaign.id))
    assert sent.status == "completed", sent.abort_reason
    assert sent.sent_count == 3


def test_a_tagged_upload_still_tags(db):
    """The optional control is optional in both directions.

    A2 would be half-implemented if untagged worked and tagging quietly stopped:
    the tag is what makes cross-campaign rollups possible, which is the reason it
    survived at all.
    """
    category_id = food_service_id(db)
    campaign, imported = campaign_builder.create_campaign_from_upload(
        db, _render(db), name=f"{NAME_PREFIX}tagged", message_template=MESSAGE,
        content=default_csv(take(3)), category_id=category_id)

    assert campaign.category_id == category_id
    assert db.get(ContactList, imported["list_id"]).category_id == category_id
    assert imported["category_label"]


def test_an_untagged_batch_can_still_be_undone(db):
    """Undo's marker had to stop being `category_id`, and this is why.

    The guard used to read "no category means this is not an import batch". The
    moment an upload could legitimately carry no category, that sentence became
    false and an untagged campaign upload would have been told it was not an
    import — with no way to reverse it.
    """
    _, imported = campaign_builder.create_campaign_from_upload(
        db, _render(db), name=f"{NAME_PREFIX}undoable", message_template=MESSAGE,
        content=default_csv(take(2)))

    result = import_service.undo(db, imported["list_id"])
    assert result["memberships_removed"] == 2
    assert result["tags_removed"] == 0, "an untagged batch added no tags to remove"
    db.expire_all()
    assert db.get(ContactList, imported["list_id"]) is None


def test_an_ordinary_list_is_still_refused_by_undo(db):
    """The guard changed field, not meaning. A hand-made list is not a batch."""
    from app.services import contact_service

    plain = contact_service.get_or_create_list(
        db, f"{NAME_PREFIX}hand-made", source="manual")
    with pytest.raises(ValueError, match="not an import batch"):
        import_service.undo(db, plain.id)


# ─── A3: one contact, by hand ───────────────────────────────────────────────

def test_a_hand_added_contact_is_normalised_and_lands_in_its_list(db, client):
    """Criterion 5, first half.

    The number is typed the way it appears on the card in his hand. Storing it
    that way would make it a second person the next time the same number arrives
    in a CSV as +1…, which is precisely what the unique index on `contacts.phone`
    exists to prevent — and the index cannot help if the two spellings differ.
    """
    response = client.post("/api/contacts", json={
        "phone": "(555) 555-0691",
        "full_name": "Phoned In",
        "company": "Ricardo's Trattoria",
        "list_name": f"{NAME_PREFIX}phone-ins",
        "category_id": food_service_id(db),
    })
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["phone"] == MANUAL_PHONE, "stored in E.164, not as typed"
    assert payload["tagged"] is True

    db.expire_all()
    contact = db.query(Contact).filter(Contact.phone == MANUAL_PHONE).one()
    assert contact.full_name == "Phoned In"
    # Company goes where the CSV import puts it and where the Contacts search
    # already looks, so a hand-added contact is findable the same way.
    assert (contact.attributes or {}).get("company") == "Ricardo's Trattoria"

    memberships = db.query(ContactListMember).filter(
        ContactListMember.contact_id == contact.id).count()
    assert memberships == 1


def test_a_blocklisted_number_added_by_hand_does_not_become_sendable(db, client):
    """Criterion 5, second half — the assertion this endpoint exists for.

    An import skips an opted-out number outright. Typing it by hand must not be
    the way round that, or every STOP on the box is provisional. Three things are
    asserted, because refusing the request is not the same as having written
    nothing: the status, the absence of a contact row, and the blocklist row
    still standing.
    """
    blocklist_service.block_number(db, BLOCKED_PHONE, reason="stop_keyword",
                                   source="manual")

    response = client.post("/api/contacts", json={
        "phone": BLOCKED_PHONE, "full_name": "Asked To Stop"})
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert "opt-out list" in detail
    assert "Opt-outs" in detail, "it has to say where to change this"

    db.expire_all()
    assert db.query(Contact).filter(Contact.phone == BLOCKED_PHONE).count() == 0
    assert blocklist_service.is_blocked(db, BLOCKED_PHONE), (
        "the refusal must not have quietly consumed the opt-out")


def test_an_unusable_number_added_by_hand_is_refused(db, client):
    """The other guard an import has. A list of junk becomes paid-for failures."""
    response = client.post("/api/contacts", json={"phone": "12"})
    assert response.status_code == 400
    assert "Invalid phone number" in response.json()["detail"]


# ─── What the fresh-context review found ────────────────────────────────────

def test_a_category_id_that_names_nothing_is_refused_rather_than_tagged(db, client):
    """A3 says "same checks as an import", and this is one of them.

    `tag_contact()` validates the tag's *source* and never the category, and
    SQLite does not enforce the foreign key here — so before this the endpoint
    wrote a dangling `contact_categories` row pointing at category 99999 and
    answered `{"success": true, "tagged": true}`. The import path resolves the id
    precisely so a typo cannot become a quietly mis-tagged contact.

    The dangling row also had a second life: `_still_referenced()` counts it, so
    an undo would keep that contact forever.
    """
    from app.models.category import ContactCategory

    response = client.post("/api/contacts", json={
        "phone": "+15555550692", "full_name": "Typo", "category_id": 99999})
    assert response.status_code == 404, response.text
    assert "99999" in response.json()["detail"]

    db.expire_all()
    assert db.query(ContactCategory).filter(
        ContactCategory.category_id == 99999).count() == 0
    assert db.query(Contact).filter(Contact.phone == "+15555550692").count() == 0, (
        "the contact was written before the category was checked")


def test_the_composer_does_not_run_preflight_against_the_wrong_audience():
    """Criterion 8's silent neighbour, and the review's third finding.

    `refreshPreview()` guards on upload mode with a comment explaining that the
    audience dropdown still holds the other tab's value. `runPreflight()` read
    the same value and had no guard — and the dropdown's first entry is always
    "All contacts", so clicking "Run checks" with a CSV selected drew a full
    checklist, capacity verdict included, computed over every contact in the
    database.

    Asserted as a property of the source rather than through a browser: this is
    the one screen whose job is to stop a bad send, and a guard that exists in
    one of the two functions that need it is the shape of defect this project
    keeps finding (`should_auto_block()` had exactly one call site for two
    paths).
    """
    import pathlib

    script = pathlib.Path("app/templates/_composer-script.html").read_text()
    for function in ("async function refreshPreview()", "async function runPreflight()"):
        start = script.index(function)
        body = script[start:start + 1400]
        assert "composerMode === 'upload'" in body, (
            f"{function} does not guard on upload mode, so it answers about "
            f"whatever audience the other tab was pointing at")
