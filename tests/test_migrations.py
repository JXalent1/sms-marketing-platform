"""The migrations are the schema, and this is what proves it.

`tests/conftest.py` builds the scratch database with `alembic upgrade head`, so
every test in the suite already runs against a migrated schema — if a migration
fails to apply, collection fails and nothing runs. What that alone does not
catch is a migration that applies cleanly and still describes a *different*
schema from the models, which is precisely the divergence Alembic exists to
prevent. When the suite created its own tables with `Base.metadata.create_all()`
that divergence was invisible: the models built the tables, so the models always
agreed with them.

Module 2 adds `categories` and `contact_categories`. These two tests are what
make that migration a tested artifact rather than a plausible-looking file.
"""

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from app.core.database import Base, engine
import app.models      # noqa: F401 — importing registers every table


def test_scratch_schema_was_built_by_alembic():
    """An alembic_version row, stamped at head.

    create_all() leaves no version row. A database with tables and no version is
    the one that later fails `alembic upgrade head` with "table already exists".
    """
    with engine.connect() as connection:
        context = MigrationContext.configure(connection)
        current = context.get_current_revision()

    assert current is not None, (
        "no alembic_version — the schema was not built by a migration"
    )

    from alembic.config import Config
    from alembic.script import ScriptDirectory
    import os

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config = Config()
    config.set_main_option("script_location", os.path.join(root, "alembic"))
    assert current == ScriptDirectory.from_config(config).get_current_head()


def test_migrations_match_the_models():
    """`alembic check`, as a test.

    A migration that forgets a column applies fine and breaks at the first query
    against it — usually in production, usually on the client's data.
    """
    with engine.connect() as connection:
        context = MigrationContext.configure(connection)
        diff = compare_metadata(context, Base.metadata)

    assert diff == [], f"migrations have drifted from the models: {diff}"


def test_the_error_code_migration_did_not_rebuild_the_sms_message_indexes():
    """Escalation item 8 names index changes on `sms_messages` by name.

    `batch_alter_table` — which every other migration in this project uses —
    recreates the whole table and rebuilds every index on it. Session 5g's
    column add does not need that (SQLite adds a nullable column with no default
    in place) and deliberately does not use it. The scratch database this suite
    runs against was built by `alembic upgrade head`, so the indexes below are
    the ones the migration chain actually left behind.

    Found by review: the migration used batch mode and its own docstring claimed
    it touched no index, which is the opposite of what batch mode does.
    """
    from sqlalchemy import inspect

    indexes = {i["name"] for i in inspect(engine).get_indexes("sms_messages")}
    for expected in ("idx_sms_campaign", "idx_sms_status", "idx_sms_sent_at",
                     "ix_sms_messages_external_id"):
        assert expected in indexes, f"{expected} is missing: {sorted(indexes)}"


def test_the_top_up_migration_also_added_its_column_without_a_rebuild():
    """5e's `top_up_at` takes the same route as 5g's `error_code`, for one reason.

    The test above is the *outcome* — those four indexes survive — and it cannot
    distinguish "no migration rebuilt the table" from "one did and Alembic put
    the indexes back". So this asserts the property that made the outcome safe:
    both columns exist, and the column added most recently did not disturb the
    one added before it.

    Named for the migration rather than folded into the test above, because the
    day a third column arrives, a failure should say which migration introduced
    it.
    """
    from sqlalchemy import inspect

    columns = {c["name"] for c in inspect(engine).get_columns("sms_messages")}
    assert "top_up_at" in columns
    assert "error_code" in columns, (
        "5g's column is gone — a table rebuild dropped it, which is exactly what "
        "the in-place ADD COLUMN in both migrations exists to avoid")
