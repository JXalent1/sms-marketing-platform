"""Session 5j A2 — a guard that fails on cost, not on behaviour.

The 5i freshness join took **10 minutes 2 seconds** on production and under a
millisecond here. Nothing in this project could have caught that: the gate has
no timing check, and mutation testing proves what code does and says nothing
about how long it takes. This module is the first check in the repo about what a
query costs.

## What it asserts, and why not the obvious thing

The obvious assertion — "no full scan of `sms_messages` or
`contact_list_members`" — is wrong in **both** directions, and it was checked
before it was rejected rather than after. The outage plan, at production row
counts, is:

    SCAN contact_list_members USING COVERING INDEX sqlite_autoindex_...
    SEARCH sms_messages USING INDEX idx_sms_status (status=?)

There is no scan of `sms_messages` in it, so "no full scan of `sms_messages`" is
**green on the 39-second plan**: `status` has four distinct values, so each
"search" walks a quarter of the table. And the plan that *ended* the outage
still scans `contact_list_members` outright, so "no full scan of
`contact_list_members`" is **red on the schema that fixed production**.

What separates the two plans is the column the index is keyed on. So the
assertion is: the join runs through an index **on `contact_id`** —
`join_key_searches()` in `tests/_query_plan.py` — plus a schema assertion that
**both** sides of the join key carry one. Both, because which side SQLite
searches is a statistics decision that flips with row counts: at this suite's
scale it searches `contact_list_members`, and at production's it searches
`sms_messages`. An index on only today's favoured side is one `ANALYZE` away
from being the wrong one, and the plan check alone would not notice.

No wall-clock threshold. A timing assertion is flaky on a shared machine,
meaningless on twelve rows, and skipped within a month.
"""

import importlib.util
import pathlib

import pytest

from app.core.database import SessionLocal
from app.models.contact import Contact
from app.services import contact_service

_spec = importlib.util.spec_from_file_location(
    "_query_plan", pathlib.Path(__file__).parent / "_query_plan.py")
query_plan = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(query_plan)

# The two tables the freshness join reads, and the column it joins them on.
JOIN_TABLES = ("sms_messages", "contact_list_members")
JOIN_KEY = "contact_id"


def _freshness_plans(session):
    """Every plan for every statement `_last_sent_by_list()` actually issues."""
    return query_plan.plans_for(
        session, lambda: contact_service._last_sent_by_list(session))


def test_the_freshness_join_runs_through_an_index_on_the_join_key():
    """The plan, for the statement the service issues, on this schema."""
    session = SessionLocal()
    try:
        reason = query_plan.requires_sqlite(session)
        if reason:
            pytest.skip(reason)

        plans = _freshness_plans(session)
        assert len(plans) == 1, (
            f"_last_sent_by_list() issued {len(plans)} SELECTs; this guard "
            "describes one grouped join")
        sql, plan = plans[0]
        assert "contact_list_members" in sql and "sms_messages" in sql, sql
        assert query_plan.join_key_searches(plan), (
            "the freshness join reaches neither table through an index on "
            f"{JOIN_KEY}, which is the 10m02s plan:\n  " + "\n  ".join(plan))
    finally:
        session.close()


def test_both_sides_of_the_join_key_are_indexed():
    """Not just the side today's statistics happen to favour.

    `contact_list_members` looked indexed before 5j and was not: `idx_member_list`
    is on `list_id`, and the unique `(list_id, contact_id)` index leads with
    `list_id`, so neither could serve a lookup by `contact_id`. Hence *leading*
    column, not "mentions the column".
    """
    session = SessionLocal()
    try:
        bind = session.get_bind()
        for table in JOIN_TABLES:
            assert query_plan.indexes_leading_on(bind, table, JOIN_KEY), (
                f"{table} has no index leading on {JOIN_KEY}. Which side SQLite "
                "searches flips with row counts, so the unindexed side is one "
                "ANALYZE away from being the one it picks.")
    finally:
        session.close()


def test_the_guard_reads_the_statement_the_service_issued():
    """Not a hand-written copy of it.

    5f: a property proved of a helper is not proved of its only caller. A guard
    that explains its own SQL passes forever while the service's query degrades
    underneath it. The proof is that pointing the capture at a *different*
    runner yields a different statement — which a hard-coded copy could not do.
    `agent/mutate-5j.py`'s A2b is this test's reason for existing.
    """
    session = SessionLocal()
    try:
        if query_plan.requires_sqlite(session):
            pytest.skip(query_plan.requires_sqlite(session))

        freshness = query_plan.statements_issued_by(
            session, lambda: contact_service._last_sent_by_list(session))
        unrelated = query_plan.statements_issued_by(
            session, lambda: session.query(Contact.id).limit(1).all())

        assert freshness, "the capture recorded nothing at all"
        assert unrelated, "the capture recorded nothing for the control query"
        assert [s for s, _ in freshness] != [s for s, _ in unrelated], (
            "the capture returns the same SQL whatever it is asked to run, so "
            "it is quoting a copy rather than reading the service")
        assert "contact_list_members" in freshness[0][0]
    finally:
        session.close()


def test_the_guard_goes_red_on_the_schema_it_exists_to_reject():
    """The scan's own first version, run before it is quoted as evidence.

    Both assertions are exercised against a copy of this database's schema with
    the indexes removed — the schema alembic produced, minus the fix — so a
    green tick above is a measurement rather than a hope.
    """
    session = SessionLocal()
    try:
        if query_plan.requires_sqlite(session):
            pytest.skip(query_plan.requires_sqlite(session))

        # Neither side indexed: the outage. The plan check must fail.
        scratch, close = query_plan.scratch_schema_copy(
            session, drop_indexes=("idx_sms_contact", "idx_member_contact"))
        try:
            _sql, plan = query_plan.plans_for(
                scratch, lambda: contact_service._last_sent_by_list(scratch))[0]
            assert not query_plan.join_key_searches(plan), (
                "an unindexed schema still produced a join-key search, so the "
                "plan assertion above proves nothing:\n  " + "\n  ".join(plan))
            # And the assertion the session spec proposed, on that same plan:
            # green, on the plan that cost ten minutes. Kept as a live
            # demonstration rather than a comment, because it is the reason
            # this module asserts on the join key instead.
            assert not any(line.startswith("SCAN sms_messages") for line in plan), (
                "no full scan of sms_messages' would have caught the outage "
                "plan after all — re-read this module's docstring")
        finally:
            close()

        # One side indexed: still fine, and the *other* side's absence is what
        # only the schema assertion can see.
        scratch, close = query_plan.scratch_schema_copy(
            session, drop_indexes=("idx_member_contact",))
        try:
            _sql, plan = query_plan.plans_for(
                scratch, lambda: contact_service._last_sent_by_list(scratch))[0]
            assert query_plan.join_key_searches(plan) == ["sms_messages"], plan
            assert not query_plan.indexes_leading_on(
                scratch.get_bind(), "contact_list_members", JOIN_KEY)
        finally:
            close()
    finally:
        session.close()
