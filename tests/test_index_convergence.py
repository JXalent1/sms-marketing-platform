"""Session 5j A1 — the migration that has to be right on two different databases.

Production's schema **leads the migration history by two indexes**. They were
created by hand on the live box on 2026-09-04 to end a 10-minute query, they
exist in no migration and in no model, and they are named
`ix_sms_messages_contact_id` and `ix_clm_contact_id` — not this project's
`idx_<table-ish>_<column>` convention.

So `a3f1e08c5d47` has to converge two states onto one schema: a fresh clone with
neither index, and a live box with both under the wrong names. A bare
`op.create_index()` raises `index ... already exists` on the second, and
`deployment/deploy.sh` aborts the deploy without restarting the service when a
migration fails — which would make the fix for the outage the one thing that
cannot ship.

The migration's own `upgrade()` and `downgrade()` are driven here, against a
scratch copy of this database's schema, rather than re-implemented. A test that
rewrites the logic tests its own rewrite — the discipline 5i established when it
drove `utc_text_to_local_iso()` directly.
"""

import importlib.util
import pathlib

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text

from app.core.database import SessionLocal

_spec = importlib.util.spec_from_file_location(
    "_query_plan", pathlib.Path(__file__).parent / "_query_plan.py")
query_plan = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(query_plan)

CONVENTION = {"sms_messages": "idx_sms_contact",
              "contact_list_members": "idx_member_contact"}
HAND_MADE = {"sms_messages": "ix_sms_messages_contact_id",
             "contact_list_members": "ix_clm_contact_id"}


def _load_migration():
    path = (pathlib.Path(__file__).resolve().parent.parent / "alembic" / "versions"
            / "a3f1e08c5d47_contact_id_indexes_for_the_freshness_join.py")
    spec = importlib.util.spec_from_file_location("migration_5j_indexes", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(session, which):
    """Drive the real `upgrade()`/`downgrade()` against this session's bind.

    `Operations.context()` is what installs the `alembic.op` proxy the migration
    calls, so the code under test is byte-for-byte the code that runs on the
    live box — including its inspector check, which is the whole point.
    """
    connection = session.connection()
    context = MigrationContext.configure(connection)
    migration = _load_migration()
    with Operations.context(context):
        getattr(migration, which)()
    session.commit()


def _names(session, table):
    return {index["name"] for index in inspect(session.get_bind()).get_indexes(table)}


def _scratch(state):
    """A scratch database in one of the two states this migration must handle.

    "production" — the hand-made indexes, under their hand-made names, and the
    convention-named ones absent, which is exactly the live box today.
    "fresh" — neither, which is every clone of this repo.
    """
    session, close = query_plan.scratch_schema_copy(
        SessionLocal(), drop_indexes=tuple(CONVENTION.values()))
    if state == "production":
        for table, name in HAND_MADE.items():
            session.execute(text(f"CREATE INDEX {name} ON {table}(contact_id)"))
        session.commit()
    return session, close


def test_the_migration_converges_a_database_that_already_has_the_hand_made_indexes():
    """Criterion 1. The state production is in, and the one that raises."""
    session, close = _scratch("production")
    try:
        for table, name in HAND_MADE.items():
            assert name in _names(session, table), "the fixture is not production's state"

        _run(session, "upgrade")

        for table in CONVENTION:
            names = _names(session, table)
            assert CONVENTION[table] in names, names
            assert HAND_MADE[table] not in names, (
                f"{HAND_MADE[table]} survived — four indexes where there should "
                "be two, and the duplicate costs write time on every send")
    finally:
        close()


def test_the_migration_is_idempotent_on_the_database_it_just_converged():
    """Criterion 1's second half: run it twice, and it neither raises nor doubles."""
    session, close = _scratch("production")
    try:
        _run(session, "upgrade")
        before = {t: _names(session, t) for t in CONVENTION}
        _run(session, "upgrade")
        assert {t: _names(session, t) for t in CONVENTION} == before
    finally:
        close()


def test_the_migration_creates_both_indexes_on_a_database_that_has_neither():
    """Criterion 2. Every fresh clone, and the gate's own scratch database."""
    session, close = _scratch("fresh")
    try:
        for table in CONVENTION:
            assert CONVENTION[table] not in _names(session, table)
            assert HAND_MADE[table] not in _names(session, table)

        _run(session, "upgrade")

        for table in CONVENTION:
            assert CONVENTION[table] in _names(session, table)
    finally:
        close()


def test_downgrade_drops_the_convention_names_and_restores_no_hand_made_ones():
    """They were an incident response, not a schema.

    Recreating them would put the database back into the undescribed state this
    revision exists to end — which is how this session happened.
    """
    session, close = _scratch("production")
    try:
        _run(session, "upgrade")
        _run(session, "downgrade")
        for table in CONVENTION:
            names = _names(session, table)
            assert CONVENTION[table] not in names, names
            assert HAND_MADE[table] not in names, (
                "downgrade recreated a hand-made index, so a downgraded box is "
                "back to carrying two index names nothing describes")
    finally:
        close()


def test_downgrade_is_a_no_op_on_a_database_that_never_had_them():
    """The inspector guard has to hold in both directions, or a partially
    applied box cannot be rolled back."""
    session, close = _scratch("fresh")
    try:
        _run(session, "downgrade")      # must not raise
        for table in CONVENTION:
            assert CONVENTION[table] not in _names(session, table)
    finally:
        close()


def test_the_convergence_is_declared_on_the_models_as_well():
    """Criterion 8's other half, and what keeps `alembic check` honest.

    An index that exists in the migration and not in the models is drift:
    `test_migrations_match_the_models` reports it, and the next autogenerated
    revision would propose dropping it.
    """
    from app.models.contact_list import ContactListMember
    from app.models.sms_message import SMSMessage

    for model, name in ((SMSMessage, CONVENTION["sms_messages"]),
                        (ContactListMember, CONVENTION["contact_list_members"])):
        declared = {index.name: [c.name for c in index.columns]
                    for index in model.__table__.indexes}
        assert declared.get(name) == ["contact_id"], declared


def test_the_migration_uses_the_inspector_rather_than_a_sqlite_spelling():
    """`IF NOT EXISTS` is SQLite's, and this project is Postgres-ready.

    A source assertion, deliberately: the behavioural tests above pass on
    SQLite either way, so the portability property has nowhere else to live.
    """
    source = (pathlib.Path(__file__).resolve().parent.parent / "alembic" / "versions"
              / "a3f1e08c5d47_contact_id_indexes_for_the_freshness_join.py").read_text()
    body = source.split('"""', 2)[-1]
    assert "IF NOT EXISTS" not in body.upper(), (
        "the migration reached for SQLite's spelling of the idempotence check")
    assert "get_indexes" in body


def test_the_new_index_is_created_before_the_hand_made_one_is_dropped():
    """Create, then drop — the order that leaves no unindexed instant.

    Both orders converge on the same schema, so no assertion about the *result*
    can tell them apart; the DDL the migration issues is the only place this
    property is visible. The box is live during business hours with a composer
    that polls, and the window the wrong order opens is one in which the join
    that caused the outage runs unindexed.
    """
    session, close = _scratch("production")
    try:
        # Stripped: alembic emits its DDL with a leading newline.
        issued = [sql.strip() for sql, _params in query_plan.statements_issued_by(
            session, lambda: _run(session, "upgrade"), only_selects=False)]
        for table in CONVENTION:
            created = next(i for i, sql in enumerate(issued)
                           if sql.startswith("CREATE INDEX")
                           and CONVENTION[table] in sql)
            dropped = next(i for i, sql in enumerate(issued)
                           if sql.startswith("DROP INDEX") and HAND_MADE[table] in sql)
            assert created < dropped, (
                f"{table}: {HAND_MADE[table]} was dropped before "
                f"{CONVENTION[table]} existed, so the join ran unindexed in "
                "between — on a live box, during business hours")
    finally:
        close()
