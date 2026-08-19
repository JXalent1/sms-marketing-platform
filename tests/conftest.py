"""Test-wide environment setup.

Everything in this file runs before the first `from app.main import app` in any
test module, and that ordering is the entire point. `Settings` is instantiated
at import of `app.core.config`, and the schema is built at import of `app.main`,
so a DATABASE_URL set any later than this is simply ignored.

Why a fresh database file per run rather than a rollback between tests: the
suite is deliberately one end-to-end story — seed contacts, block a number, run
a campaign, bill it, then process an inbound STOP — and the later assertions
lean on the earlier ones. What it must not lean on is the *previous run*.
`test_inbound_stop_is_persisted` blocklists +15555550102 permanently; on a
reused database `test_campaign_honors_blocklist_and_bills_correctly` then finds
two blocked numbers and asserts `skipped_count == 1` where the truth is 2. The
suite was green exactly once on a fresh database and red forever after.

DATABASE_URL is overridden unconditionally rather than with setdefault: a suite
that runs against whatever the developer happened to export is the bug being
fixed here, and the developer's own database is the last thing it should touch.
"""

import os
import tempfile

_db_fd, _DB_PATH = tempfile.mkstemp(prefix="sms-test-", suffix=".db")
os.close(_db_fd)
os.unlink(_DB_PATH)          # SQLAlchemy creates it; it must not pre-exist as a 0-byte file

os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

# Forced, not defaulted: a stray live carrier credential in the environment must
# never let the suite reach a real handset. Dry run is the only acceptable mode.
os.environ["SMS_PROVIDER"] = "console"

os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_PASSWORD", "devpassword123")
os.environ.setdefault("COOKIE_SECURE", "false")


_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _create_schema() -> None:
    """Build the schema here rather than leaning on app.main's import-time
    bootstrap.

    That side effect only fired because test_smoke.py imports app.main and
    happened to be collected; `pytest tests/test_billing.py` on its own ran
    against a database with no tables in it. Any single test file must be
    runnable on its own — that is how a failure gets isolated.

    Built by `alembic upgrade head`, not `Base.metadata.create_all()`. The whole
    job of a migration is to end up at the same schema the models describe, and
    a suite that creates its tables from the models can never notice when one
    doesn't — the exact failure Alembic exists to prevent was invisible to a
    green suite. Every migration is now a tested artifact: if it doesn't apply,
    or applies to something the code can't use, the entire suite fails at
    collection.
    """
    from alembic import command
    from alembic.config import Config

    # No alembic.ini: that would set config_file_name, and alembic/env.py then
    # calls fileConfig(), which disables every existing logger. pytest's caplog
    # and our own handlers are casualties. script_location is all env.py needs.
    cfg = Config()
    cfg.set_main_option("script_location", os.path.join(_PROJECT_ROOT, "alembic"))
    command.upgrade(cfg, "head")


_create_schema()


def pytest_sessionfinish(session, exitstatus):
    """Remove the scratch database, pass or fail."""
    for suffix in ("", "-wal", "-shm"):
        try:
            os.unlink(_DB_PATH + suffix)
        except FileNotFoundError:
            pass
