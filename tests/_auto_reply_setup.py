"""Shared setup for the session I1 auto-reply guard tests.

Two of the guard's rules are global — the circuit breaker counts every reply in
the last hour, whoever it went to — so a test that measures one cannot share a
database with the rest of the suite and mean anything: the count would include
whatever the tests before it happened to send. `isolated_db` gives each such
test a database of its own, and `routed_to()` points the webhook routes at it.

The tests that go through the suite's shared database (the normal path, STOP)
do so on purpose and inject nothing — no clock, no session — because that is
the path production takes.
"""

import contextlib
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base, get_db
from app.sms.base import SendResult
import app.models  # noqa: F401  — every table on Base.metadata

# The incident, verbatim: what +19046943673 and +19048673536 sent us for three
# days, and what we answered every time.
RESPONDER_TEXT = ("We do not monitor text messages sent to this number. "
                  "Please call us...")
RESPONDERS = ("+19046943673", "+19048673536")

# A frozen instant well in the past. Rows written at it sit below every
# real-clock window the rest of the suite reads.
T0 = datetime(2026, 3, 10, 9, 0, 0)


@pytest.fixture
def isolated_db(tmp_path):
    """A fresh schema in a file of its own. A file, not :memory:, because the
    concurrency test opens several connections and each must see one database.

    Built with create_all because this is a throwaway test schema, not a live
    table; the migration itself is exercised by conftest's `alembic upgrade
    head` and test_migrations' drift check."""
    engine = create_engine(f"sqlite:///{tmp_path / 'guard.db'}",
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    yield factory
    engine.dispose()


@contextlib.contextmanager
def routed_to(app, factory):
    """Point every route's `Depends(get_db)` at `factory` for the duration."""
    def _get_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _get_db
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_db, None)


class CountingProvider:
    """Stands in for the carrier. Records every send; never touches a network."""
    name = "counting"

    def __init__(self, result: SendResult = None, raises: Exception = None, on_send=None):
        self.sent = []
        self._result = result or SendResult(success=True, message_id="fake")
        self._raises = raises
        self._on_send = on_send

    async def send(self, to, text):
        self.sent.append((to, text))
        if self._on_send:
            self._on_send(to, text)
        if self._raises:
            raise self._raises
        return self._result


def use_provider(monkeypatch, provider):
    """The Telnyx route resolves its provider per request through this name."""
    monkeypatch.setattr("app.routers.webhooks.telnyx.get_provider", lambda *a, **k: provider)
    return provider


def telnyx_inbound(phone: str, text: str) -> dict:
    return {"data": {"event_type": "message.received",
                     "payload": {"from": {"phone_number": phone}, "text": text}}}
