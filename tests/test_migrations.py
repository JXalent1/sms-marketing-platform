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
