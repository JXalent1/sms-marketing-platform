"""Dead numbers get blocked, and the Opt-outs screen says which is which.

Two defects found by one live campaign: 6,857 recipients, 2,673 undelivered.

**The auto-block never fired.** `AUTO_BLOCK_ERROR_FRAGMENTS` has contained
"not routable" and "landline" since the skeleton, above a comment about keeping
a list from degrading into thousands of guaranteed failures per campaign — but
`should_auto_block()` was called from exactly one place: the *submission* path,
where the provider rejects a send outright. Those 2,673 were accepted at
submission (HTTP 200) and failed later via delivery webhook, which never
consulted the list. Two failure paths, one guard, and the guard was on the one
carrying the smaller share. Cost of leaving it: those numbers stay live and fail
again on every campaign, roughly $24 of wasted sends each time, permanently.

**The headline counted the wrong thing.** After the 2,626 dead numbers were
backfilled, /blocklist showed a single red "2,626" labelled Blocked, on a screen
titled "Opt-outs" under copy reading "Opt-outs are permanent". The real opt-out
count was near zero. An unreachable number is a data-quality fact, not a
compliance event.

Both directions are asserted here, and the second direction is the one that
matters most: a temporary spam block must stay sendable. Blocking on a transient
failure deletes a real buyer from the list permanently, which is far more
expensive than paying for one retry.

Nothing here sends. The webhook path is exercised through the service functions
and the public webhook route; the send loop is not involved.
"""

import os

import pytest
from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.main import app
from app.models.blocked_number import BlockedNumber
from app.models.sms_message import SMSMessage
from app.routers.webhooks.common import record_delivery_status
from app.services.blocklist_service import blocked_counts
from app.sms.compliance import should_auto_block

PASSWORD = os.environ["ADMIN_PASSWORD"]

# 555-06xx is this module's alone. test_smoke asserts an exact recipient count
# over the "all" audience and an exact skipped count against the blocklist, so a
# row left behind here would fail a module further down the run.
NOT_ROUTABLE = "+15555550601"
DEEMED_INVALID = "+15555550602"
TEMPORARY_SPAM = "+15555550603"
DELIVERED_OK = "+15555550604"
RETRIED = "+15555550605"
WEBHOOK_PHONES = (NOT_ROUTABLE, DEEMED_INVALID, TEMPORARY_SPAM, DELIVERED_OK, RETRIED)

# One number per concern rather than one shared pool. Reusing a phone across
# tests meant clearing rows mid-module, and clearing rows the message fixture
# still held detached the session — a failure about SQLAlchemy identity, in a
# file about blocklist reasons.
ROUTE_PHONE = "+15555550606"
COUNT_PHONES = ("+15555550607", "+15555550608", "+15555550609", "+15555550610")

PHONES = (*WEBHOOK_PHONES, ROUTE_PHONE, *COUNT_PHONES)

# Verbatim from the campaign that found this. Paraphrasing them would test our
# paraphrase rather than the carrier's wording, which is the whole defect:
# "deemed invalid" matched none of the existing fragments.
NOT_ROUTABLE_ERROR = (
    "Not routable: the destination number is either a landline or a "
    "non-routable wireless number"
)
DEEMED_INVALID_ERROR = "the destination phone number was deemed invalid by the carrier"
TEMPORARY_SPAM_ERROR = "Blocked as spam - temporary"


@pytest.fixture(scope="module", autouse=True)
def login_budget():
    """Hand back the two logins this module spends.

    POST /login is 10/minute per IP and the whole suite logs in from one address
    inside one window. Same discipline as test_provider_status and
    test_degraded_send_path — without it this is the module that tips the suite
    into 429s, and the failure would surface as `assert "counts" in payload`
    against a rate-limit body rather than as anything to do with rate limits.
    """
    from app.routers import pages as pages_router

    pages_router.limiter.reset()
    yield
    pages_router.limiter.reset()


def _purge(db) -> None:
    db.query(SMSMessage).filter(SMSMessage.phone.in_(PHONES)).delete(
        synchronize_session=False)
    db.query(BlockedNumber).filter(BlockedNumber.phone.in_(PHONES)).delete(
        synchronize_session=False)
    db.commit()


@pytest.fixture(scope="module")
def db():
    session = SessionLocal()
    try:
        _purge(session)
        yield session
        _purge(session)
    finally:
        session.close()


@pytest.fixture
def sent_message(db):
    """One message per phone, in the state a delivery webhook arrives to find:
    accepted by the provider, external id recorded, nothing decided yet."""
    created = {}
    for phone in WEBHOOK_PHONES:
        row = SMSMessage(
            phone=phone, message="body", status="sent",
            external_id=f"webhook-test-{phone}", sent_at="2026-08-26T09:00:00",
        )
        db.add(row)
        created[phone] = row
    db.commit()
    yield created
    for row in created.values():
        db.delete(row)
    db.commit()


def _blocked(db, phone: str):
    return db.query(BlockedNumber).filter(BlockedNumber.phone == phone).first()


# ─── The fragments themselves ───────────────────────────────────────────────

def test_the_carrier_wordings_that_mean_never_again_are_matched():
    assert should_auto_block(NOT_ROUTABLE_ERROR)
    assert should_auto_block(DEEMED_INVALID_ERROR), (
        "the 100 invalid-destination failures match no fragment — the carrier "
        "says 'deemed invalid', not 'is not a valid' or 'invalid phone number'"
    )


def test_a_temporary_failure_is_never_a_permanent_block():
    """47 of these in one campaign, every one of them still reachable.

    Asserted against the whole fragment list rather than the two added here: the
    risk is a *future* fragment that happens to appear in a transient message,
    and the cost of that is real buyers deleted from the list for good.
    """
    assert not should_auto_block(TEMPORARY_SPAM_ERROR)
    assert not should_auto_block("Blocked as spam - temporary, retry later")
    assert not should_auto_block("")
    assert not should_auto_block(None)


# ─── The webhook path ───────────────────────────────────────────────────────

def test_a_not_routable_delivery_webhook_blocks_the_number(db, sent_message):
    record_delivery_status(db, sent_message[NOT_ROUTABLE].external_id,
                           "delivery_failed", NOT_ROUTABLE_ERROR, source="telnyx")

    row = _blocked(db, NOT_ROUTABLE)
    assert row is not None, (
        "a landline reported by the delivery webhook stayed on the list and "
        "will be paid for again on every campaign"
    )
    assert row.reason == "delivery_failure"
    assert row.source == "telnyx"
    assert db.query(SMSMessage).filter(
        SMSMessage.external_id == sent_message[NOT_ROUTABLE].external_id
    ).first().status == "undelivered"


def test_a_deemed_invalid_delivery_webhook_blocks_the_number(db, sent_message):
    record_delivery_status(db, sent_message[DEEMED_INVALID].external_id,
                           "undelivered", DEEMED_INVALID_ERROR, source="telnyx")
    assert _blocked(db, DEEMED_INVALID) is not None


def test_a_temporary_spam_webhook_does_not_block(db, sent_message):
    record_delivery_status(db, sent_message[TEMPORARY_SPAM].external_id,
                           "undelivered", TEMPORARY_SPAM_ERROR, source="telnyx")

    assert _blocked(db, TEMPORARY_SPAM) is None, (
        "a temporary carrier block deleted a reachable buyer from the list"
    )
    # Still recorded as undelivered — not blocking is not the same as ignoring.
    assert db.query(SMSMessage).filter(
        SMSMessage.external_id == sent_message[TEMPORARY_SPAM].external_id
    ).first().status == "undelivered"


def test_a_delivered_webhook_still_does_not_block(db, sent_message):
    record_delivery_status(db, sent_message[DELIVERED_OK].external_id,
                           "delivered", None, source="telnyx")
    assert _blocked(db, DELIVERED_OK) is None
    assert db.query(SMSMessage).filter(
        SMSMessage.external_id == sent_message[DELIVERED_OK].external_id
    ).first().status == "delivered"


def test_the_same_webhook_twice_blocks_once(db, sent_message):
    """Carriers retry webhooks, sometimes for days.

    `record_delivery_status` is documented as idempotent and the auto-block sits
    inside the branch that only runs on the first terminal event, so a retry
    cannot produce a second row. block_number() refusing duplicates is the
    second layer, not the first.
    """
    external_id = sent_message[RETRIED].external_id
    for _ in range(3):
        record_delivery_status(db, external_id, "undelivered",
                               NOT_ROUTABLE_ERROR, source="telnyx")

    rows = db.query(BlockedNumber).filter(BlockedNumber.phone == RETRIED).all()
    assert len(rows) == 1, f"{len(rows)} blocklist rows from one number"


def test_a_delivered_message_is_never_auto_blocked_by_a_later_failure(db, sent_message):
    """A handset receipt is not revocable by a later failure event.

    The status guard admits `msg.status == "delivered"`, so delivered-then-failed
    — a carrier retry, a duplicate, or a race between two of its workers —
    reaches the auto-block for a message that provably arrived. Before session 5d
    that cost a wrong counter. With a block on the same path it would
    permanently delete a buyer who received the text, which is the most
    expensive mistake this file can make.
    """
    external_id = sent_message[DELIVERED_OK].external_id
    record_delivery_status(db, external_id, "delivered", None, source="telnyx")
    assert _blocked(db, DELIVERED_OK) is None

    # Now the contradictory failure the carrier sends afterwards, with wording
    # that WOULD block an undelivered message.
    record_delivery_status(db, external_id, "delivery_failed",
                           NOT_ROUTABLE_ERROR, source="telnyx")

    assert _blocked(db, DELIVERED_OK) is None, (
        "a number that received the message was permanently blocked by a "
        "contradictory failure webhook arriving after the delivery receipt"
    )


def test_the_public_webhook_route_reaches_the_same_guard():
    """The service call above could pass while the route never reaches it.

    Posts the payload shape the carrier actually sends, through the app. Runs on
    its own sessions rather than the module fixture's: the request handler
    commits on a different session, and reading the result back through a
    session that already has an open transaction is how a passing route looks
    like a failing one.
    """
    setup = SessionLocal()
    try:
        setup.add(SMSMessage(phone=ROUTE_PHONE, message="body", status="sent",
                             external_id="webhook-route-test",
                             sent_at="2026-08-26T09:00:00"))
        setup.commit()
    finally:
        setup.close()

    TestClient(app).post("/webhooks/telnyx", json={"data": {
        "event_type": "message.finalized",
        "payload": {
            "id": "webhook-route-test",
            "to": [{"status": "delivery_failed"}],
            "errors": [{"title": "Not routable",
                        "detail": "the destination number is either a landline or a "
                                  "non-routable wireless number"}],
        },
    }})

    check = SessionLocal()
    try:
        assert _blocked(check, ROUTE_PHONE) is not None, (
            "the guard is reachable from the service but not from the route the "
            "carrier actually posts to"
        )
    finally:
        check.close()


# ─── The headline counts the right things ───────────────────────────────────

def test_opt_outs_and_unreachable_numbers_are_counted_separately(db):
    """The figures the Opt-outs screen renders.

    Measured as deltas, not absolutes: the suite shares one database and other
    modules block numbers of their own.
    """
    before = blocked_counts(db)

    unreachable_a, unreachable_b, opt_out, manual = COUNT_PHONES
    db.add_all([
        BlockedNumber(phone=unreachable_a, reason="delivery_failure", source="telnyx",
                      blocked_at="2026-08-26T09:00:00"),
        BlockedNumber(phone=unreachable_b, reason="carrier_block", source="telnyx",
                      blocked_at="2026-08-26T09:00:00"),
        BlockedNumber(phone=opt_out, reason="stop_keyword", source="webhook",
                      blocked_at="2026-08-26T09:00:00"),
        BlockedNumber(phone=manual, reason="manual", source="manual",
                      blocked_at="2026-08-26T09:00:00"),
    ])
    db.commit()

    after = blocked_counts(db)
    assert after["opt_outs"] - before["opt_outs"] == 1, (
        "unreachable numbers are being counted as people who opted out"
    )
    assert after["unreachable"] - before["unreachable"] == 2
    assert after["other"] - before["other"] == 1, (
        "a manual block vanished from the headline instead of being its own figure"
    )
    assert after["total"] - before["total"] == 4, "the buckets do not add up to the total"


def test_the_opt_out_definition_matches_the_dashboard_tile(db):
    """One definition of "opt-out", not two.

    This asserted the literal `("stop_keyword",)` until session 5g added
    `carrier_opt_out` to both. Pinning the tuple pinned the wrong thing: it
    tested the membership list rather than the property the membership list
    exists for, so it went red on an intended change and would have stayed green
    on the change that matters — a second literal filter appearing in
    `dashboard_service`, which is exactly what was there.

    What is asserted now is the invariant claimed at blocklist_service.py:88-91:
    the tile and the Opt-outs headline read the same definition. The count is
    compared row for row in
    tests/test_blocklist_correctness.py::test_the_blocklist_headline_and_the_dashboard_tile_agree.
    """
    import inspect

    from app.services import blocklist_service, dashboard_service

    assert "stop_keyword" in blocklist_service.OPT_OUT_REASONS
    # An unreachable number is a data-quality fact, not a compliance event. That
    # separation is the whole reason this split exists.
    assert "delivery_failure" not in blocklist_service.OPT_OUT_REASONS
    assert "carrier_block" not in blocklist_service.OPT_OUT_REASONS

    # Comments stripped first: this file's own reasoning quotes the literal it
    # is banning, and a check that cannot survive being explained is a check
    # nobody will keep.
    tile_source = "\n".join(line for line in
                            inspect.getsource(dashboard_service.stat_tiles).splitlines()
                            if not line.strip().startswith("#"))
    assert "OPT_OUT_REASONS" in tile_source, (
        "the dashboard tile has its own definition of an opt-out again"
    )
    assert '"stop_keyword"' not in tile_source, (
        "a literal reason string is back in the tile's filter, so the two "
        "screens will disagree the next time the set changes"
    )


def test_the_blocklist_api_returns_the_split_counts():
    """Counted server-side over the whole table.

    `numbers` is capped at 5,000 rows, so a page tallying it client-side would
    under-report a long list — and under-report it as *fewer opt-outs*, which is
    the direction nobody checks.
    """
    client = TestClient(app)
    client.post("/login", data={"username": "admin", "password": PASSWORD})
    payload = client.get("/api/blocklist").json()

    assert "counts" in payload, "the page has no split figures to render"
    for key in ("opt_outs", "unreachable", "other", "total"):
        assert key in payload["counts"], key
    assert payload["counts"]["total"] == payload["total"]


def test_the_opt_outs_page_renders_two_figures():
    """Only the opt-out figure carries the critical treatment."""
    client = TestClient(app)
    client.post("/login", data={"username": "admin", "password": PASSWORD})
    html = client.get("/blocklist").text

    assert 'id="optOutCount"' in html
    assert 'id="unreachableCount"' in html
    assert 'id="blockedCount"' not in html, (
        "the single 'Blocked' headline is still there, so unreachable numbers "
        "still read as people opting out"
    )
    # The red treatment belongs to opt-outs alone.
    opt_out_tile = html[html.index('id="optOutCount"') - 120:html.index('id="optOutCount"')]
    assert "text-crit" in opt_out_tile
    unreachable_tile = html[html.index('id="unreachableCount"') - 120:
                            html.index('id="unreachableCount"')]
    assert "text-crit" not in unreachable_tile, (
        "an unreachable number is a data-quality fact dressed as a compliance event"
    )
