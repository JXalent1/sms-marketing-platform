"""What a carrier failure shows the client, and what it shows the operator.

Two halves of session 5g, and the same shape twice: a failure carries
information that has to reach exactly one audience and not the other.

**The client must never see the provider's payload.** Row 4 of the live corpus
is the SDK's `__str__` of an API error — the whole response body, dict-repr'd
into `sms_messages.error_message`. Since 5d that column is the source of
`blocked_numbers.notes`, which renders on the Opt-outs page. Two rows on the
live box today, and their visible prefix happens to carry no brand and no
personal data; nothing makes that true of the next one, and `detail` on a
destination error is exactly where a recipient's phone number appears.

**The operator must see our own misconfiguration.** A region the messaging
account was never enabled for is a fault on our side. Until 5g the product's
whole response was to block the recipient forever for it. It now raises a
signal — and not over SMS, because `agent/notify.sh` texts over the carrier
account and 5d ruled an alert must not travel over the thing it warns about.

Its sibling `test_blocklist_correctness.py` covers what a failure *does* to a
buyer. This file covers what it *says*.

Nothing here sends. `notify()` is asserted not to be called at all.
"""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.sms_message import SMSMessage
from app.routers.webhooks.common import record_delivery_status
from app.services import monitoring_service
from app.sms import compliance
from app.sms.phone import scrub_provider_text, strip_payload
from app.sms.providers.telnyx import describe_send_error
from tests import _carrier_failure_setup as setup
from tests._carrier_failure_setup import (
    LIVE_DEEMED_INVALID, LIVE_NOT_ROUTABLE, LIVE_RAW_PAYLOAD, LIVE_TEMPORARY_SPAM,
    REGION_ERROR, blocked,
)

RAW_PAYLOAD = setup.SURFACE_PHONES["raw_payload"]
REGION_REFUSED = setup.SURFACE_PHONES["region_refused"]
PHONES = tuple(setup.SURFACE_PHONES.values())


@pytest.fixture(scope="module", autouse=True)
def login_budget():
    yield from setup.login_budget_fixture_body()


@pytest.fixture(scope="module")
def db():
    yield from setup.db_fixture_body(PHONES)


@pytest.fixture(autouse=True)
def clean_config_alerts(db):
    """One row per alert key, so one left behind is one every later test sees.

    Function-scoped rather than module-scoped: several tests here raise the same
    alert, and "/health went red at some point during this module" is not the
    claim any of them is making.
    """
    setup.clear_config_alerts(db)
    yield
    setup.clear_config_alerts(db)


@pytest.fixture
def sent_message(db):
    yield from setup.sent_message_fixture_body(db, PHONES, "5g-surface")


@pytest.fixture
def no_pager(monkeypatch):
    """Record every notify() call instead of making one.

    A5 says the operator signal must not be texted. Asserting the call count is
    the only way to state that as a property rather than a hope.
    """
    calls = []
    monkeypatch.setattr(monitoring_service, "notify",
                        lambda message: calls.append(message) or True)
    return calls


# ─── A3. A raw provider payload never reaches the client ────────────────────

def test_the_stored_raw_payload_row_is_no_longer_a_dict_repr(db, sent_message):
    """The end of the path, through the webhook and into the column the client
    reads."""
    record_delivery_status(db, sent_message[RAW_PAYLOAD].external_id,
                           "delivery_failed", LIVE_RAW_PAYLOAD, source="telnyx")

    row = blocked(db, RAW_PAYLOAD)
    assert row is not None, (
        "the probe produced no blocklist row, so it scans nothing — parsing the "
        "error must not change what it means"
    )
    assert "{'" not in row.notes and '{"' not in row.notes, f"raw payload reached the client: {row.notes}"
    assert "'code'" not in row.notes and "'detail'" not in row.notes, row.notes
    assert "+13215551234" not in row.notes, (
        "a third party's phone number, quoted by the carrier in a detail field, "
        "rendered on the client's Opt-outs page"
    )


def test_the_provider_assembles_its_error_from_named_fields():
    """The source of the defect: `str(exc)` on an SDK API error is the response
    body. Read the fields by name instead."""
    class FakeAPIError(Exception):
        body = {"errors": [{"code": "10002", "title": "Invalid phone number",
                            "detail": "Invalid destination number +13215551234."}]}

        def __str__(self):
            return LIVE_RAW_PAYLOAD

    described = describe_send_error(FakeAPIError())
    assert described == "Invalid phone number: Invalid destination number +13215551234."
    assert "{" not in described


@pytest.mark.parametrize("body,expected", [
    ({"errors": [{"code": "40300", "title": "Blocked"}]}, "Blocked"),
    ({"errors": [{"code": "40300"}]}, "Carrier error 40300"),
    ({"errors": []}, None),
    ({}, None),
    (None, None),
    ("not a dict", None),
])
def test_a_partial_error_body_still_yields_a_line(body, expected):
    """Carriers omit fields. Falling back to `str(exc)` is correct when there is
    no structured body to read; returning a half-built string is not."""
    class PartialError(Exception):
        def __str__(self):
            return "Request failed"

    error = PartialError()
    error.body = body
    assert describe_send_error(error) == (expected or "Request failed")


def test_a_provider_error_with_no_structured_body_still_yields_a_line():
    assert describe_send_error(TimeoutError("Request timed out")) == "Request timed out"
    assert describe_send_error(TimeoutError()) == "TimeoutError"


@pytest.mark.parametrize("text,expected", [
    (LIVE_RAW_PAYLOAD, "Error code: 400"),
    ("Failed - [{'code': 30007}]", "Failed"),
    ('Rejected: {"detail": "+13215551234"}', "Rejected"),
    ("Not routable: the destination is a landline", None),   # untouched
    # Nested structure, and a single-quote inside a double-quoted value.
    ("detail: {'a': {'b': 'c'}} and then more prose",
     "detail: and then more prose"),
    ("""{'detail': "it's here"} trailing""", "trailing"),
    # Truncated by the column length, which is how row 4 is actually stored.
    ("Error code: 400 - {'errors': [{'code': '10002', 'detail': 'Invalid destinatio",
     "Error code: 400"),
])
def test_scrub_provider_text_strips_an_embedded_payload(text, expected):
    """The backstop for every provider and SDK version we do not parse.

    Same defect class as the branding this function was written for, one level
    up: there the carrier's *name* survived a bad regex, here its entire JSON
    structure travels intact into a column the client reads.
    """
    scrubbed = scrub_provider_text(text)
    assert "{" not in scrubbed and "[" not in scrubbed
    if expected is not None:
        assert scrubbed == expected
    else:
        assert scrubbed == text


def test_the_prose_after_a_payload_survives():
    """The payload is not always last, and what follows it is often the half
    the client needs.

    The first version of `strip_payload()` cut at the opening brace and threw
    away the rest of the string. Found by review: a carrier that appends its
    remediation advice after the structure lost the advice, and a message that
    was mostly structure lost everything.
    """
    assert scrub_provider_text(
        "Unable to create record: The 'To' number is not a valid phone number "
        "[{'code': 21211}] - see the error reference for how to fix this"
    ) == ("Unable to create record: The 'To' number is not a valid phone number "
          "- see the error reference for how to fix this")

    assert scrub_provider_text(
        "Message rejected. Retry with a valid destination. Payload: {'to': '+1555'}"
    ) == "Message rejected. Retry with a valid destination. Payload"


def test_an_error_that_was_nothing_but_payload_still_says_something():
    """`blocked_numbers.notes` is `f"Auto-blocked: {scrub_provider_text(...)}"`.

    Returning "" put a blocked number on the client's Opt-outs page under a
    reason that stopped at the colon, which reads as a bug in the product rather
    than as a carrier that said nothing useful. Reachable from
    `campaign_service.py:449`, where `str(e)` on an SDK error *is* the body.
    """
    from app.sms.phone import NO_READABLE_DETAIL

    scrubbed = scrub_provider_text("{'errors': [{'code':'10002'}]}")
    assert scrubbed == NO_READABLE_DETAIL
    assert f"Auto-blocked: {scrubbed}"[:200].strip() != "Auto-blocked:"


@pytest.mark.parametrize("text", [
    LIVE_NOT_ROUTABLE,
    LIVE_DEEMED_INVALID,
    LIVE_TEMPORARY_SPAM,
    "Campaigns cannot go out right now. Contact support.",
    "Message body {first_name} was rejected",       # a merge tag, not a payload
    "Rate limited {see docs}",                      # one brace, no quoted key
    "Error 30007 {carrier}",
])
def test_stripping_a_payload_leaves_ordinary_error_prose_alone(text):
    """`strip_payload()` runs inside `scrub_provider_text()`, which is on every
    client-facing error string in the app. Cutting one of them short would be a
    worse bug than the one it was added for — so it takes two structural
    characters in a row, and a lone brace is not enough."""
    assert strip_payload(text) == text
    assert scrub_provider_text(text) == text


# ─── A5. The operator signal ────────────────────────────────────────────────

def test_a_region_permission_error_does_not_block_the_recipient(db, sent_message, no_pager):
    """Twilio 21408 is a geo permission on the *sending* account. The
    destination was never the problem, enabling the region fixes it, and until
    now the number stayed blocked forever for a setting on our side."""
    record_delivery_status(db, sent_message[REGION_REFUSED].external_id,
                           "delivery_failed", REGION_ERROR, source="twilio")

    assert blocked(db, REGION_REFUSED) is None, (
        "a reachable buyer was permanently blocked because of our own account "
        "configuration"
    )
    # Still recorded as a failure — not blocking is not the same as ignoring.
    assert db.query(SMSMessage).filter(
        SMSMessage.external_id == sent_message[REGION_REFUSED].external_id
    ).first().status == "undelivered"


def test_a_region_permission_error_raises_an_operator_signal(db, sent_message, no_pager):
    """Criterion 6's second half. This is our configuration error; we should
    hear about it, and the recipient should not pay for it."""
    record_delivery_status(db, sent_message[REGION_REFUSED].external_id,
                           "delivery_failed", REGION_ERROR, source="twilio")

    alerts = monitoring_service.active_config_alerts(db)
    assert [a["key"] for a in alerts] == [compliance.REGION_NOT_ENABLED]
    assert "enable the region" in alerts[0]["detail"].lower()


def test_the_operator_signal_is_never_sent_over_sms(db, sent_message, no_pager):
    """`agent/notify.sh` sends an SMS over the carrier account. 5d A4 ruled that
    an alert must not depend on the thing it is warning about — and these arrive
    thousands at a time on a webhook that must answer promptly, so a subprocess
    per event would turn a carrier's retries into a fork bomb."""
    for _ in range(3):
        record_delivery_status(db, sent_message[REGION_REFUSED].external_id,
                               "delivery_failed", REGION_ERROR, source="twilio")

    assert no_pager == [], f"the operator alert was texted: {no_pager}"


def test_health_reports_the_configuration_alert_without_naming_a_carrier(db, sent_message,
                                                                        no_pager):
    """/health is the channel CLAUDE.md already names as the one that survives a
    dead carrier, and the one `deployment/deploy.sh` and the uptime monitor
    already read. It stays HTTP 200: a 503 here rolls back every deploy,
    including the one that fixes the configuration."""
    client = TestClient(app)
    assert client.get("/health").json()["config_ok"] is True

    record_delivery_status(db, sent_message[REGION_REFUSED].external_id,
                           "delivery_failed", REGION_ERROR, source="twilio")

    response = client.get("/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["config_ok"] is False
    assert payload["config_issues"][0]["key"] == compliance.REGION_NOT_ENABLED
    for carrier in ("telnyx", "twilio"):
        assert carrier not in response.text.lower(), response.text


def test_a_burst_of_the_same_failure_writes_the_row_once(db, monkeypatch, no_pager):
    """A misconfigured region fails *every* recipient in it.

    The live corpus is 2,847 events in one campaign, and each write is a SELECT
    + UPDATE + commit inside a handler that has to return 200 promptly. All but
    the first say what the row already says. This is the same reasoning that
    ruled out a subprocess per event, one order of magnitude down.
    """
    import app.models.app_setting as app_setting

    writes = []
    real_set = app_setting.set_setting

    def counted(session, key, value, description=None):
        writes.append(key)
        return real_set(session, key, value, description)

    monkeypatch.setattr(app_setting, "set_setting", counted)

    for _ in range(50):
        monitoring_service.record_config_alert(db, compliance.REGION_NOT_ENABLED,
                                               "detail")

    assert len(writes) == 1, f"{len(writes)} writes for one standing condition"
    assert monitoring_service.active_config_alerts(db), (
        "throttling the write also lost the alert"
    )


def test_a_stale_configuration_alert_stops_being_reported(db):
    """A /health field stuck red forever is a field people learn to ignore.
    Anything still misconfigured re-raises on the next failure."""
    stale = datetime.now() - timedelta(days=monitoring_service.CONFIG_ALERT_WINDOW_DAYS + 1)
    monitoring_service.record_config_alert(db, compliance.REGION_NOT_ENABLED,
                                           "detail", now=stale)

    assert monitoring_service.active_config_alerts(db) == []
    assert TestClient(app).get("/health").json()["config_ok"] is True


def test_a_corrupt_alert_row_does_not_take_health_down(db):
    """/health has to answer even when its inputs are nonsense — it is what the
    deploy script health-checks."""
    from app.models.app_setting import set_setting

    set_setting(db, f"{monitoring_service.CONFIG_ALERT_PREFIX}broken", "not json")
    assert monitoring_service.active_config_alerts(db) == []
    assert TestClient(app).get("/health").status_code == 200


def test_health_survives_a_database_it_cannot_read(monkeypatch):
    """The new field must not become a new way to roll back a deploy.

    `deployment/deploy.sh` restores the previous release on a non-200 here, so
    every path this endpoint gained has to fail closed. It is also why /health
    reads the alert without `Depends(get_db)` — a dependency that raises means
    the handler never runs, and there is nothing left to catch it in.
    """
    def unavailable():
        raise RuntimeError("unable to open database file")

    monkeypatch.setattr(monitoring_service, "SessionLocal", unavailable)

    response = TestClient(app).get("/health")
    assert response.status_code == 200, response.text
    assert response.json()["config_issues"] == []


def test_a_failed_alert_write_does_not_cost_the_blocklist_row(db, sent_message,
                                                              monkeypatch, no_pager):
    """The alert is written before the block decision on the same session. A
    monitoring write that fails must hand the caller a usable transaction, not a
    poisoned one — a missed alert is cheaper than a lost auto-block."""
    import app.models.app_setting as app_setting

    def explode(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(app_setting, "set_setting", explode)

    # Two independent facts in one event: a code that raises the alert (21408,
    # our account's geo permission) and prose that condemns the number on its
    # own merits. The alert write fails; the block still has to happen.
    record_delivery_status(
        db, sent_message[RAW_PAYLOAD].external_id, "delivery_failed",
        LIVE_NOT_ROUTABLE, source="twilio", error_code="21408")

    assert blocked(db, RAW_PAYLOAD) is not None, (
        "a failed monitoring write swallowed the auto-block on the same session"
    )
