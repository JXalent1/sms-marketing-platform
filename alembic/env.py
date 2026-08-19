"""Alembic environment.

Two deliberate departures from the generated template:

  1. The database URL comes from `app.core.config.settings`, not from
     `alembic.ini`. A URL duplicated in an ini file is a URL that will one day
     disagree with the app's, and the migration will then apply cleanly to the
     wrong database.

  2. `target_metadata` is the app's declarative Base, so `alembic revision
     --autogenerate` diffs against the real model layer.

`render_as_batch` is on because SQLite cannot ALTER a column in place; without
it, any future migration that alters or drops a column fails on the client's
actual database while passing on Postgres.
"""

from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

from app.core.config import settings
from app.core.database import Base
import app.models  # noqa: F401 — importing registers every table on Base.metadata

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Config wins over alembic.ini. `settings` already reads DATABASE_URL from the
# environment first and .env second, so `DATABASE_URL=... alembic upgrade head`
# works the way the gate expects.
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

target_metadata = Base.metadata

_is_sqlite = settings.DATABASE_URL.startswith("sqlite")


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of executing it."""
    context.configure(
        url=settings.DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=_is_sqlite,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=_is_sqlite,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
