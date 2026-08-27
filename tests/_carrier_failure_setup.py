"""Shared setup for the two session-5g test files.

Not a test module — the leading underscore keeps pytest from collecting it.
`test_blocklist_correctness.py` and `test_carrier_error_surfaces.py` were one
file until it crossed the 500-line rule. The boundary the split landed on is a
real one: whether a failure **blocks a buyer**, and what a failure **shows a
client or an operator**. The live error corpus and the webhook scaffolding are
the only things both need, and duplicating the purge routine would have left two
that could drift apart. Same reasoning as `_guardrail_setup.py`.

The rules both files inherit: leave no rows behind (the suite shares one
database and later modules assert exact counts), and give back the rate-limit
budget you spend.
"""

from app.core.database import SessionLocal
from app.models.app_setting import AppSetting
from app.models.blocked_number import BlockedNumber
from app.models.sms_message import SMSMessage
from app.services import monitoring_service

# ─── The whole live failure corpus ──────────────────────────────────────────
#
# Verbatim from the 3,037 delivery failures on the live box. Four distinct
# strings; this is all of them. Paraphrasing would test our paraphrase rather
# than the carrier's wording, which is the entire defect class — "deemed
# invalid" matched no fragment for exactly that reason.

LIVE_NOT_ROUTABLE = (
    "Not routable: The destination number is either a landline or a "
    "non-routable wireless number."
)
LIVE_DEEMED_INVALID = (
    "Invalid messaging destination number: The destination phone number was "
    "deemed invalid by the carrier."
)
LIVE_TEMPORARY_SPAM = (
    "Blocked as spam - temporary: The message was flagged by a SPAM filter and "
    "was not delivered."
)
# Row 4 as it is actually stored: the SDK's __str__ of an API error, which is
# the response body dict-repr'd into the message. 2 rows on the live box, and
# since 5d that column is the source of `blocked_numbers.notes`, which the
# client reads on the Opt-outs page.
LIVE_RAW_PAYLOAD = (
    "Error code: 400 - {'errors': [{'code': '10002', 'title': 'Invalid phone "
    "number', 'detail': 'Invalid destination number +13215551234.'}]}"
)

# A geo permission on the *sending* account (Twilio 21408). The destination was
# never the problem.
REGION_ERROR = (
    "Permission to send an SMS has not been enabled for the region indicated "
    "by the 'To' number"
)

# 555-11xx and 555-12xx belong to these two modules. test_smoke asserts an exact
# recipient count over the "all" audience and an exact skipped count against the
# blocklist, so a row left behind here fails a module further down the run.
DECISION_PHONES = {
    "not_routable": "+15555551101",
    "deemed_invalid": "+15555551102",
    "temporary_spam": "+15555551103",
    "raw_payload": "+15555551104",
    "switched_off": "+15555551105",
    "quoted_number": "+15555551106",
    "coded_opt_out": "+15555551107",
}
DECISION_EXTRA = ("+15555551110", "+15555551111", "+15555551112", "+15555551113",
                  "+15555551114", "+15555551115")

SURFACE_PHONES = {
    "raw_payload": "+15555551201",
    "region_refused": "+15555551202",
}


def purge(db, phones) -> None:
    """Remove every row these modules create. Runs before and after each."""
    db.query(SMSMessage).filter(SMSMessage.phone.in_(phones)).delete(
        synchronize_session=False)
    db.query(BlockedNumber).filter(BlockedNumber.phone.in_(phones)).delete(
        synchronize_session=False)
    clear_config_alerts(db)


def clear_config_alerts(db) -> None:
    """A config alert is one row per key, so one left behind is one every later
    /health assertion in the suite sees."""
    db.query(AppSetting).filter(
        AppSetting.key.like(f"{monitoring_service.CONFIG_ALERT_PREFIX}%")).delete(
        synchronize_session=False)
    db.commit()


def db_fixture_body(phones):
    """One module-scoped session, purged at both ends."""
    session = SessionLocal()
    try:
        purge(session, phones)
        yield session
        purge(session, phones)
    finally:
        session.close()


def sent_message_fixture_body(db, phones, prefix: str):
    """One message per phone, in the state a delivery webhook arrives to find:
    accepted by the provider, external id recorded, nothing decided yet."""
    created = {}
    for phone in phones:
        row = SMSMessage(phone=phone, message="body", status="sent",
                         external_id=f"{prefix}-{phone}",
                         sent_at="2026-08-26T09:00:00")
        db.add(row)
        created[phone] = row
    db.commit()
    yield created
    for row in created.values():
        db.delete(row)
    db.commit()


def login_budget_fixture_body():
    """Hand back the logins a module spends.

    POST /login is 10/minute per IP and the whole suite logs in from one address
    inside one window. Same discipline as test_provider_status and
    test_blocklist_reasons — without it these are the modules that tip the suite
    into 429s, and the failure surfaces as a missing JSON key rather than as
    anything to do with rate limits.
    """
    from app.routers import pages as pages_router

    pages_router.limiter.reset()
    yield
    pages_router.limiter.reset()


def blocked(session, phone: str):
    return session.query(BlockedNumber).filter(BlockedNumber.phone == phone).first()
