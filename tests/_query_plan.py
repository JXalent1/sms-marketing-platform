"""What a query costs, asked of the statement the service actually issues.

This project had no check that a query is affordable. The gate times nothing,
and the mutation harness proves behaviour and is silent about how long the
behaviour takes — which is how session 5i's freshness join shipped green and
then took **10 minutes 2 seconds** on production while running in under a
millisecond on the suite's twelve rows.

`EXPLAIN QUERY PLAN` is the durable statement of the property. A wall-clock
threshold is not: it is flaky on a shared machine, it is meaningless on a
twelve-row fixture, and it turns into a skipped test within a month.

## Why this captures rather than quotes

Every function here takes a **callable that runs the service**, and reads the
SQL out of a `before_cursor_execute` listener while it runs. A test that
hand-writes its own copy of the query passes forever while the service's query
degrades underneath it — 5f's lesson, where a property proved of a helper was
not proved of its only caller, and reverting the endpoint broke nothing.
`test_the_guard_reads_the_statement_the_service_issued` is what keeps this
honest, and `agent/mutate-5j.py`'s `A2b` is what proves that test earns its
place.

SQLite only. `EXPLAIN QUERY PLAN` is SQLite's spelling; a Postgres bind needs
`EXPLAIN` and a different vocabulary, and a check that silently passes on the
wrong engine is worse than one that says it did not run. `requires_sqlite()`
returns the skip reason.
"""

import re

from sqlalchemy import event

# `SEARCH <table> USING [COVERING] INDEX <name> (contact_id=?)` — an inner-loop
# lookup keyed on the join column. The captured group is the table.
#
# The column is in the pattern on purpose, and it is the whole assertion. The
# outage plan was:
#
#     SCAN contact_list_members USING COVERING INDEX sqlite_autoindex_...
#     SEARCH sms_messages USING INDEX idx_sms_status (status=?)
#
# — a SEARCH, on an index, of the big table, and 39 seconds at production row
# counts, because `status` has four distinct values so each "search" walks a
# quarter of the table. A rule reading "no full scan of sms_messages" is green
# on that plan. And the *fixed* production plan scans `contact_list_members`
# outright, so a rule reading "no full scan of contact_list_members" is red on
# the schema that ended the outage. Both halves of the obvious assertion are
# wrong; what separates the two plans is whether the join key is what the index
# is keyed on.
SEARCH_ON_JOIN_KEY = re.compile(
    r"SEARCH (\w+) USING (?:COVERING )?INDEX \S+ \(contact_id=", re.I)


def requires_sqlite(session):
    """None when this bind can be explained, else the reason it cannot."""
    name = session.get_bind().dialect.name
    if name != "sqlite":
        return (f"EXPLAIN QUERY PLAN is SQLite's; this bind is {name!r}, which "
                "needs EXPLAIN and a different assertion. Not run.")
    return None


def statements_issued_by(session, run, only_selects=True):
    """Every SQL statement `run()` puts on the wire, as (sql, parameters).

    `only_selects=False` keeps the DDL too, which is how
    `tests/test_index_convergence.py` checks that a migration creates the new
    index **before** dropping the old one. That ordering leaves no instant in
    which the join is unindexed, and it is invisible in the end state — the two
    orders converge on the same schema, so nothing but the statement order can
    tell them apart.

    The listener is attached to the session's own bind and removed in a
    `finally`: an event listener left on the engine outlives the test and
    quietly instruments every query the rest of the suite makes.
    """
    captured = []
    bind = session.get_bind()

    def before(conn, cursor, statement, parameters, context, executemany):
        captured.append((statement, parameters))

    event.listen(bind, "before_cursor_execute", before)
    try:
        run()
    finally:
        event.remove(bind, "before_cursor_execute", before)
    if not only_selects:
        return captured
    return [(sql, params) for sql, params in captured
            if sql.lstrip().upper().startswith("SELECT")]


def plan_for(session, sql, parameters):
    """EXPLAIN QUERY PLAN's `detail` column, one string per step.

    Explained through the raw DBAPI cursor rather than `session.execute(text())`
    because the captured statement carries SQLite's qmark parameters and a
    positional sequence to bind to them, which `text()` cannot take.
    """
    cursor = session.connection().connection.cursor()
    try:
        cursor.execute("EXPLAIN QUERY PLAN " + sql, parameters or ())
        return [row[-1] for row in cursor.fetchall()]
    finally:
        cursor.close()


def plans_for(session, run):
    """(sql, plan) for every SELECT `run()` issues."""
    return [(sql, plan_for(session, sql, params))
            for sql, params in statements_issued_by(session, run)]


def join_key_searches(plan):
    """The tables this plan reaches through an index **keyed on `contact_id`**.

    Empty means the join key is not what any index in the plan is keyed on,
    which is the outage: SQLite still uses an index, on `status`, and walks a
    quarter of `sms_messages` for every membership row.
    """
    return [match.group(1) for line in plan
            for match in [SEARCH_ON_JOIN_KEY.search(line)] if match]


def indexes_leading_on(bind, table, column):
    """Names of indexes on `table` whose **leading** column is `column`.

    Leading, because a composite index only serves a lookup on its first
    column — which is the whole reason `uq_list_contact` on
    `(list_id, contact_id)` did nothing for a lookup by `contact_id`, and why
    `contact_list_members` looked indexed and was not.

    Unique constraints are counted alongside indexes, because in SQLite a
    unique constraint **is** an index and the planner uses it as one — but
    SQLAlchemy reports the implicit one under `get_unique_constraints()` rather
    than `get_indexes()`. Leaving them out would mean this function could not
    see the exact composite that caused the incident, so the "leading column"
    rule would be a sentence in a docstring with nothing behind it.
    """
    from sqlalchemy import inspect
    inspector = inspect(bind)
    covering = ([(i["name"], i["column_names"]) for i in inspector.get_indexes(table)]
                + [(u["name"], u["column_names"])
                   for u in inspector.get_unique_constraints(table)])
    return [name for name, columns in covering
            if columns and columns[0] == column]


def scratch_schema_copy(session, drop_indexes=()):
    """A throwaway SQLite database carrying this database's exact schema.

    Copied out of `sqlite_master` rather than rebuilt from
    `Base.metadata.create_all()`, so the negative case below is testing the
    schema **alembic** produced. Rebuilding from the models would make the
    proof circular: a mutation that removed an index declaration would also
    remove it from the control, and "the guard fails on an unindexed schema"
    would go on passing while proving nothing.

    Returns `(session, close)`. No rows: `EXPLAIN QUERY PLAN` reads the schema,
    not the data.
    """
    import os
    import tempfile

    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    ddl = [row[0] for row in session.execute(text(
        "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL")).all()]

    handle, path = tempfile.mkstemp(prefix="query-plan-", suffix=".db")
    os.close(handle)
    os.unlink(path)
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as connection:
        for statement in ddl:
            connection.execute(text(statement))
        for name in drop_indexes:
            connection.execute(text(f"DROP INDEX {name}"))
    scratch = sessionmaker(bind=engine)()

    def close():
        scratch.close()
        engine.dispose()
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(path + suffix)
            except FileNotFoundError:
                pass

    return scratch, close
