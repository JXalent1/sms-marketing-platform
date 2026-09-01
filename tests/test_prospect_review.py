"""Session P1 A6-A7: the review queue, the two verbs, and what they refuse.

The white-label and payload assertions are the ones to read first. Every leak
this project has found was assembled at runtime — an f-string, a field copied
out of a third-party payload — and a grep structurally cannot see any of them,
so these run the routes and scan what comes back, including the CSV export,
which is the surface that leaves the building.

Nothing here sends a message and nothing calls a paid API.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.category import Category, ContactCategory
from app.models.contact import Contact
from app.models.prospect import (
    Prospect, ProspectRejection, REJECT_REASONS, WRONG_SIDE_REASONS,
)
from app.models.scrape import PhoneLookup
from app.routers import prospects as prospects_router
from app.services import blocklist_service, lookup_service, prospect_queue
from app.services import prospect_service

from tests import _prospect_setup as setup
from tests import _wholesale_scan as scan

PASSWORD = "devpassword123"

# What must never appear on a prospect surface. The carrier names, our own
# screening spend in the spellings it would arrive in, and the raw third-party
# payload — the one field on these tables nobody has vetted.
FORBIDDEN = ("telnyx", "twilio", "bandwidth", "vonage",
             "wholesale", "lookups_performed", "place_id")


@pytest.fixture(scope="module")
def db():
    yield from setup.purged_db_fixture_body()


@pytest.fixture(scope="module")
def client():
    c = TestClient(app)
    login = c.post("/login", data={"username": "admin", "password": PASSWORD})
    assert login.status_code in (200, 302), (
        f"login failed with {login.status_code} — every scan below would be "
        f"running against a 401 body and passing by containing nothing")
    return c


@pytest.fixture(scope="module")
def food_service(db):
    return db.query(Category).filter(Category.slug == "food_service").one()


def _seed(db, phone, *, line_type="mobile", name=None, term=setup.TERM,
          rationale=setup.RATIONALE, source="fake-a") -> Prospect:
    """One prospect, screened to a known line type. Returns the row.

    Screening is done through `lookup_service` with a fake rather than by
    writing a `phone_lookups` row by hand: the queue and the promote guard both
    read that table through the service, and a test that wrote the row itself
    would stop proving the two agree about how it is read.
    """
    setup.FakeSource([setup.record(phone, name=name, term=term,
                                   rationale=rationale)], name=source).ingest(db)
    lookup_service.screen(db, [phone],
                          provider=setup.CountingLookupProvider(default=line_type))
    row = db.query(Prospect).filter(Prospect.phone == phone).one()
    prospect_service.rescore(db, row, line_type=line_type)
    return row


# ─── A7: promote ────────────────────────────────────────────────────────────

def test_promote_creates_a_contact_tagged_with_the_category_and_links_it(db, food_service):
    """Criterion 2, in all three of its parts.

    The link is the audit trail: six months from now the answer to "where did
    this number come from" is this contact, this prospect, this search, and the
    written claim about why they would bid.
    """
    phone = setup.take(1)[0]
    prospect = _seed(db, phone, name="Sunrise Catering")

    result = prospect_service.promote(db, [prospect.id], food_service.id)
    assert result["refused"] == [] and len(result["promoted"]) == 1

    contact = db.query(Contact).filter(Contact.phone == phone).one()
    db.refresh(prospect)
    assert prospect.status == "promoted"
    assert prospect.promoted_contact_id == contact.id
    assert prospect.promoted_at

    tag = db.query(ContactCategory).filter(
        ContactCategory.contact_id == contact.id,
        ContactCategory.category_id == food_service.id).one()
    # A human read the rationale and chose the category. That is the same act as
    # typing it on the Contacts screen, and `inferred` would store a confidence
    # for a decision nobody inferred.
    assert tag.source == "manual" and tag.confidence is None
    assert contact.source == "prospect"


def test_a_landline_cannot_be_promoted(db, food_service):
    """Criterion 5. The refusal names the cause and what happens to the row.

    Not deleted — recorded, with its line type, so the same number found by a
    second source next month is known instantly and never looked up again.
    """
    phone = setup.take(1)[0]
    prospect = _seed(db, phone, line_type="landline")

    result = prospect_service.promote(db, [prospect.id], food_service.id)
    assert result["promoted"] == []
    assert result["refused"][0]["reason"] == lookup_service.LANDLINE_REFUSAL
    assert db.query(Contact).filter(Contact.phone == phone).first() is None

    db.refresh(prospect)
    assert prospect.status == "pending", "a landline is held, not deleted"
    assert lookup_service.cached_line_types(db, [phone]) == {phone: "landline"}


def test_an_unscreened_number_cannot_be_promoted(db, food_service):
    """A number nobody screened has not passed the gate — it has skipped it.

    This is the state of every prospect on a box with screening switched off,
    which is every box until the first real source ships. Promoting them would
    put the 2,526-landline campaign back one prospect at a time.
    """
    phone = setup.take(1)[0]
    setup.FakeSource([setup.record(phone)]).ingest(db)
    prospect = db.query(Prospect).filter(Prospect.phone == phone).one()

    result = prospect_service.promote(db, [prospect.id], food_service.id)
    assert result["refused"][0]["reason"] == lookup_service.UNSCREENED_REFUSAL
    assert db.query(Contact).filter(Contact.phone == phone).first() is None


def test_the_queues_eligible_filter_and_the_promote_guard_agree(db, food_service):
    """The tile promises what the button delivers, and this asserts the property.

    Pinning a list of "promotable" line types would prove the list. What matters
    is that the count the client reads and the guard the click meets cannot
    disagree — a headline that offers 40 ready-to-promote rows and a promote
    that refuses 12 of them is the "table can be right while the headline lies"
    defect, one screen over.
    """
    phones = setup.take(5)
    kinds = ["mobile", "landline", "voip", "toll_free"]
    rows = [_seed(db, phone, line_type=kind) for phone, kind in zip(phones, kinds)]

    # And one that was never screened at all, which is the state of every
    # prospect on a box with no screening credential — i.e. every box today. A
    # version of this test that only used screened rows passed while the queue
    # offered unscreened numbers the promote guard then refused, which is the
    # exact disagreement it exists to rule out.
    setup.FakeSource([setup.record(phones[4])], name="fake-unscreened").ingest(db)
    rows.append(db.query(Prospect).filter(Prospect.phone == phones[4]).one())
    assert lookup_service.cached_line_types(db, [phones[4]]) == {}, (
        "precondition: this one has no answer in the cache")

    page = prospect_queue.queue_page(db, status="pending", promote_eligible=True,
                                     per_page=prospect_queue.MAX_PAGE_SIZE)
    eligible_ids = {row["id"] for row in page["prospects"]}

    for row in rows:
        offered = row.id in eligible_ids
        result = prospect_service.promote(db, [row.id], food_service.id)
        accepted = bool(result["promoted"])
        assert offered == accepted, (
            f"{row.phone}: the queue says eligible={offered} and promote "
            f"said {accepted}")


def test_a_blocklisted_number_cannot_be_promoted(db, food_service):
    """Criterion 7, first half — and the line type is deliberately `mobile`.

    If the number were a landline the gate would refuse it and this test would
    pass without the blocklist check existing at all. The set a guard runs over
    is as much the test's business as the guard is.
    """
    phone = setup.take(1)[0]
    prospect = _seed(db, phone, line_type="mobile")
    assert lookup_service.refusal_for("mobile") is None, "precondition: the gate would pass it"
    blocklist_service.block_number(db, phone, reason="delivery_failure",
                                   source="webhook")

    result = prospect_service.promote(db, [prospect.id], food_service.id)
    assert result["promoted"] == []
    assert result["refused"][0]["reason"] == prospect_service.UNREACHABLE_REFUSAL
    assert db.query(Contact).filter(Contact.phone == phone).first() is None


def test_an_opted_out_number_cannot_be_promoted_and_is_told_apart(db, food_service):
    """Criterion 7, second half.

    An opt-out and an undeliverable number are both on the blocklist and they
    mean opposite things to the client: one is a person's request, the other is
    a fact about a wire. `OPT_OUT_REASONS` is the one definition of the first in
    this codebase, and this screen reads it rather than inventing a second.
    """
    phone = setup.take(1)[0]
    prospect = _seed(db, phone, line_type="mobile")
    blocklist_service.block_number(db, phone, reason="stop_keyword", source="webhook")

    result = prospect_service.promote(db, [prospect.id], food_service.id)
    assert result["promoted"] == []
    assert result["refused"][0]["reason"] == prospect_service.OPTED_OUT_REFUSAL
    assert result["refused"][0]["reason"] != prospect_service.UNREACHABLE_REFUSAL
    assert db.query(Contact).filter(Contact.phone == phone).first() is None


def test_promote_reports_what_went_in_and_what_did_not(db, food_service):
    """A screenful with three landlines in it is the normal case.

    Refusing the whole batch would make the client re-tick fifty rows to find
    out which three, so both halves come back and each refusal carries its own
    reason.
    """
    good, bad = setup.take(2)
    rows = [_seed(db, good, line_type="mobile"),
            _seed(db, bad, line_type="landline")]

    result = prospect_service.promote(db, [r.id for r in rows] + [999_999],
                                      food_service.id)
    assert [p["phone"] for p in result["promoted"]] == [good]
    assert [r["phone"] for r in result["refused"]] == [bad]
    assert result["not_found"] == [999_999]


# ─── A6: rejection suppresses, permanently and for every source ─────────────

def test_a_rejection_suppresses_the_number_for_every_future_source(db):
    """Criterion 3, and the re-ingest is from a **different** source.

    Re-running the same source would be caught by the sighting constraint and
    would prove nothing about suppression. The claim is about the *number*: a
    business rejected as a consignor comes back next month under a different
    name, a different payload and a different search, and must not resurface.
    """
    phone = setup.take(1)[0]
    prospect = _seed(db, phone, name="Coastal Estate Liquidators", source="fake-a")

    result = prospect_service.reject(db, [prospect.id], "seller_or_consignor",
                                     notes="sells into our sales")
    assert result["rejected"] == [prospect.id]
    db.refresh(prospect)
    assert prospect.status == "rejected" and prospect.rejected_at
    assert db.query(ProspectRejection).filter(
        ProspectRejection.phone == phone).one().reason == "seller_or_consignor"

    # A different source, a different name, a different term, a different
    # payload. Same number.
    reingest = setup.FakeSource(
        [setup.record(phone, name="Coastal Estates LLC",
                      term="estate buyers broward",
                      payload={"place_id": "a-different-id"})],
        name="fake-c")
    outcome = reingest.ingest(db)

    assert outcome.suppressed == 1 and outcome.created == 0
    row = db.query(Prospect).filter(Prospect.phone == phone).one()
    assert row.status == "rejected"
    assert row.business_name == "Coastal Estate Liquidators", (
        "the re-ingest overwrote the rejected row instead of being suppressed")

    page = prospect_queue.queue_page(db, status="pending",
                                     per_page=prospect_queue.MAX_PAGE_SIZE)
    assert phone not in {p["phone"] for p in page["prospects"]}


def test_a_promoted_prospect_cannot_be_rejected_and_the_refusal_says_where_to_go(db,
                                                                                 food_service):
    phone = setup.take(1)[0]
    prospect = _seed(db, phone)
    prospect_service.promote(db, [prospect.id], food_service.id)

    result = prospect_service.reject(db, [prospect.id], "not_a_buyer")
    assert result["rejected"] == []
    assert result["refused"][0]["reason"] == prospect_service.PROMOTED_CANNOT_BE_REJECTED


def test_rejecting_twice_is_unchanged_rather_than_an_error(db):
    """A double-click on a bulk action must not read as a failure."""
    phone = setup.take(1)[0]
    prospect = _seed(db, phone)
    prospect_service.reject(db, [prospect.id], "not_a_buyer")
    again = prospect_service.reject(db, [prospect.id], "not_a_buyer")
    assert again["unchanged"] == [prospect.id] and again["rejected"] == []


def test_a_rejected_prospect_cannot_be_promoted(db, food_service):
    phone = setup.take(1)[0]
    prospect = _seed(db, phone)
    prospect_service.reject(db, [prospect.id], "competitor")

    result = prospect_service.promote(db, [prospect.id], food_service.id)
    assert result["refused"][0]["reason"] == prospect_service.REJECTED_REFUSAL
    assert db.query(Contact).filter(Contact.phone == phone).first() is None


def test_an_unknown_reject_reason_is_refused_rather_than_stored(db):
    phone = setup.take(1)[0]
    prospect = _seed(db, phone)
    with pytest.raises(ValueError):
        prospect_service.reject(db, [prospect.id], "he_looked_shifty")


# ─── A6: the queue and the per-term breakdown ───────────────────────────────

def test_the_queue_renders_the_buyer_rationale_on_every_row(db, client):
    """Criterion 9, first half, and it goes through the endpoint.

    A property proved of the service is not proved of the thing that tells the
    client — 5f shipped a helper that measured the right thing behind an
    endpoint that handed it the wrong argument, and only a test through the
    endpoint would have caught it.
    """
    phones = setup.take(2)
    for phone in phones:
        _seed(db, phone)

    body = client.get("/api/prospects?status=pending&per_page=200").json()
    rows = {row["phone"]: row for row in body["prospects"]}
    for phone in phones:
        assert rows[phone]["buyer_rationale"] == setup.RATIONALE
        assert rows[phone]["search_term"] == setup.TERM
    assert all(row["buyer_rationale"] for row in body["prospects"]), (
        "a row with no rationale reached the reviewer, who is being asked "
        "'is this a real business?' instead of 'would this person bid?'")


def test_the_term_breakdown_tells_sellers_apart_from_other_reasons(db):
    """Criterion 9, second half.

    `seller_or_consignor` and `competitor` mean the search term was wrong in
    kind — it is finding people who sell *to* the auction house. `not_a_buyer`
    means this one business was wrong. Folding them together would lose the only
    signal that says which searches to retire.
    """
    seller_term = "estate liquidators near me"
    phones = setup.take(4)
    rows = [_seed(db, p, term=seller_term, rationale="They deal in estate goods.")
            for p in phones]

    prospect_service.reject(db, [rows[0].id, rows[1].id], "seller_or_consignor")
    prospect_service.reject(db, [rows[2].id], "competitor")
    prospect_service.reject(db, [rows[3].id], "not_a_buyer")

    breakdown = prospect_queue.term_breakdown(db)
    entry = next(t for t in breakdown["terms"] if t["term"] == seller_term)

    assert entry["found"] == 4 and entry["rejected"] == 4
    assert entry["reasons"]["seller_or_consignor"] == 2
    assert entry["reasons"]["competitor"] == 1
    assert entry["reasons"]["not_a_buyer"] == 1
    assert entry["wrong_side"] == 3, (
        "the two reasons that condemn the search rather than the business are "
        "not being counted apart from the one that does not")
    assert set(WRONG_SIDE_REASONS) == {"seller_or_consignor", "competitor"}


def test_a_term_whose_rejections_are_mostly_sellers_is_flagged(db):
    """The system should teach us which searches find the wrong side of the room."""
    good_term, bad_term = "mobile welders broward", "we buy houses fort lauderdale"
    good = [_seed(db, p, term=good_term) for p in setup.take(3)]
    bad = [_seed(db, p, term=bad_term) for p in setup.take(3)]

    with setup.term_flag_rule(min_rejections=3, share=0.5):
        prospect_service.reject(db, [good[0].id], "not_a_buyer")
        prospect_service.reject(db, [good[1].id], "wrong_category")
        prospect_service.reject(db, [good[2].id], "bad_number")
        prospect_service.reject(db, [b.id for b in bad], "seller_or_consignor")

        terms = {t["term"]: t for t in prospect_queue.term_breakdown(db)["terms"]}

    assert terms[bad_term]["flagged"] is True
    assert terms[good_term]["flagged"] is False, (
        "a term with three ordinary rejections is not a term that finds sellers")


def test_a_single_seller_rejection_does_not_condemn_a_term(db):
    """A share is meaningless on a small sample, and the flag triggers a human
    deleting a search that may be working."""
    term = "food truck outfitters"
    row = _seed(db, setup.take(1)[0], term=term)
    with setup.term_flag_rule(min_rejections=5, share=0.5):
        prospect_service.reject(db, [row.id], "competitor")
        terms = {t["term"]: t for t in prospect_queue.term_breakdown(db)["terms"]}
    assert terms[term]["wrong_side"] == 1
    assert terms[term]["flagged"] is False


def test_the_summary_counts_in_the_database_not_in_the_page(db):
    """The queue is capped and the tiles are not. A figure tallied from a page
    under-reports the moment the list overflows, and quietly."""
    phones = setup.take(3)
    for phone, kind in zip(phones, ["mobile", "landline", "mobile"]):
        _seed(db, phone, line_type=kind)

    summary = prospect_queue.summary(db)
    page = prospect_queue.queue_page(db, status="pending", per_page=1)

    assert page["total"] >= 3 and len(page["prospects"]) == 1
    assert summary["pending"] >= 3
    assert summary["promote_eligible"] >= 2


# ─── What must not cross the boundary ───────────────────────────────────────

def test_no_prospect_surface_leaks_a_payload_a_carrier_or_our_cost(db, client, food_service):
    """Run the routes and scan what comes back, rather than grepping the source.

    `raw_payload` is retained on the row on purpose and is the one field on
    these tables nobody has vetted for what it contains or whom it names.
    Screening spend is ours: the client is billed per segment and for nothing
    else, so a per-lookup figure on his screen is the wholesale-rate mistake
    with a different column name.
    """
    phone = setup.take(1)[0]
    _seed(db, phone, name="Payload Test Kitchen")

    for path in ("/prospects", "/api/prospects?per_page=200",
                 "/api/prospects/summary", "/api/prospects/terms",
                 "/api/prospects/export.csv"):
        response = client.get(path)
        assert response.status_code == 200, f"{path} returned {response.status_code}"
        haystack = response.text.lower()
        for needle in FORBIDDEN:
            assert needle not in haystack, f"{path} carries {needle!r}"
        # And the figures themselves, compared as numbers rather than hunted
        # for as substrings — session P1b wired a paid lookup in, so the
        # per-number screening cost is now a second wholesale figure with the
        # same rule attached to it as the per-segment one.
        if response.headers["content-type"].startswith("application/json"):
            scan.assert_no_wholesale_field(response.json(), where=path)
        else:
            scan.assert_no_wholesale_figure(response.text, where=path)


def test_the_carriers_name_on_a_lookup_row_reaches_no_prospect_surface(db, client):
    """`phone_lookups.provider` holds the carrier's name on a screening box.

    P1b is what put it there — before it, that column read `disabled` on every
    row, so the scan above could not have caught this even in principle: it runs
    with a fake whose name is `fake-lookup`, and a surface that rendered the
    column would have passed by carrying a word that is not a carrier's. So the
    row is stamped with the real name here and the surfaces are run again.

    Nothing reads the column today. This is the test that notices the day
    something does — a screen for scrape jobs is the obvious candidate, and
    `status.md` says which three columns must not be on it.
    """
    phone = setup.take(1)[0]
    _seed(db, phone, name="Screened Kitchen")
    row = db.query(PhoneLookup).filter(PhoneLookup.phone == phone).one()
    row.provider = "telnyx"
    db.commit()

    try:
        for path in ("/prospects", "/api/prospects?per_page=200",
                     "/api/prospects/summary", "/api/prospects/terms",
                     "/api/prospects/export.csv"):
            response = client.get(path)
            assert response.status_code == 200, path
            assert "telnyx" not in response.text.lower(), (
                f"{path} renders the line-type provider's name")
    finally:
        # Left as the suite found it: every other module's scan reads this table.
        row.provider = "fake-lookup"
        db.commit()


def test_the_export_carries_the_rationale_and_nothing_it_should_not(db, client):
    phone = setup.take(1)[0]
    _seed(db, phone, name="Export Kitchen")

    csv_text = client.get("/api/prospects/export.csv?status=pending").text
    header = csv_text.splitlines()[0]
    assert header == ",".join(prospects_router.EXPORT_COLUMNS)
    assert "buyer_rationale" in header
    assert "raw_payload" not in header and "cost" not in header
    assert setup.RATIONALE in csv_text


def test_the_reject_dropdown_offers_exactly_the_reasons_reject_accepts(db, client):
    """Two lists for one thing is how a dropdown comes to offer a reason the
    service refuses. Asserted as the property, not as a pinned literal."""
    options = client.get("/api/prospects/summary").json()["reject_reasons"]
    assert [o["value"] for o in options] == list(REJECT_REASONS)
    assert all(o["label"] and o["label"] != o["value"] for o in options), (
        "a reason with no human wording is a dropdown entry nobody can read")
    # And the two that matter most are the two the reviewer meets first.
    assert [o["value"] for o in options[:2]] == list(WRONG_SIDE_REASONS)


def test_the_queue_is_closed_to_unauthenticated_callers(db):
    anonymous = TestClient(app)
    for method, path in (("get", "/api/prospects"),
                         ("get", "/api/prospects/summary"),
                         ("get", "/api/prospects/terms"),
                         ("get", "/api/prospects/export.csv"),
                         ("post", "/api/prospects/promote"),
                         ("post", "/api/prospects/reject")):
        response = getattr(anonymous, method)(path, follow_redirects=False)
        # 401 exactly: `require_auth` 401s anything under /api/ and redirects
        # only page loads. Accepting a 302 here would let a route pass because
        # of a trailing-slash redirect that never reached the dependency at all.
        assert response.status_code == 401, (
            f"{path} answered {response.status_code} without a session")


def test_promote_and_reject_go_through_the_endpoint_too(db, client, food_service):
    """The service is proved above; this proves the thing that tells the client.

    Including the shape of the answer, because the screen reports partial
    outcomes from it and a refusal it cannot find is a refusal it paraphrases.
    """
    good, bad = setup.take(2)
    rows = [_seed(db, good), _seed(db, bad, line_type="landline")]

    body = client.post("/api/prospects/promote", json={
        "prospect_ids": [r.id for r in rows],
        "category_id": food_service.id}).json()
    assert body["success"] is True
    assert [p["phone"] for p in body["promoted"]] == [good]
    assert body["refused"][0]["reason"] == lookup_service.LANDLINE_REFUSAL

    rejected = client.post("/api/prospects/reject", json={
        "prospect_ids": [rows[1].id], "reason": "seller_or_consignor"}).json()
    assert rejected["success"] is True and rejected["rejected"] == [rows[1].id]

    bad_reason = client.post("/api/prospects/reject", json={
        "prospect_ids": [rows[1].id], "reason": "nope"})
    assert bad_reason.status_code == 400
