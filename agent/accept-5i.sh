#!/usr/bin/env bash
# PART A ACCEPTANCE — session 5i, named lists replace categories.
#
# The stop condition for this session. `agent/gate.sh` answers "is this repo
# still sound?"; this answers "did session 5i do what it was sent to do?", which
# is a different question and the one RULES.md requires an agent to demonstrate
# rather than declare. Checks 1-10 are the numbered criteria from
# `sessions/session-5i.md` -> "Acceptance criteria", in order.
#
#   bash agent/accept-5i.sh
#
# TURN CAP: 60 assistant turns for Part A. On exhausting it, stop and write
# `decisions/NNN-*.open.md` rather than continuing.
#
# Nothing here sends a message and nothing here calls a paid API: SMS_PROVIDER is
# console in every subprocess, and no check reaches the network. Check 2b copies
# the developer's `data/app.db` to scratch and seeds and migrates the **copy**;
# the original is read once and never written.
#
# Note that *starting the app* is a different matter. `app/main.py` runs
# `alembic upgrade head` on import in development, so `./run.sh` or a local
# uvicorn migrates `data/app.db` in place — which is by design, and is why check
# 2b seeds its own server-default row rather than relying on finding one.
# Production does not migrate on startup; `deployment/deploy.sh` does it as a
# deliberate step, after `scripts/backup.sh`.
#
# What it DOES do to your machine: writes scratch files under $ACCEPT_SCRATCH and
# rsyncs a throwaway copy of the tree there for check 10. It writes nothing
# inside the repo.

set -uo pipefail
cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)" || exit 1

SCRATCH="${ACCEPT_SCRATCH:-${TMPDIR:-/tmp}/a4a-accept-5i}"
PY="${PY:-.venv/bin/python}"
[[ -x "$PY" ]] || PY="python3"
ABS_PY="$PY"; [[ "$ABS_PY" == /* ]] || ABS_PY="$PWD/$PY"

mkdir -p "$SCRATCH"

FAIL=0
step() { printf '\n── %s\n' "$1"; }
bad()  { printf 'ACCEPT FAIL: %s\n' "$1"; FAIL=1; }
ok()   { printf '   ok — %s\n' "$1"; }

# ── 1-8. The behaviour each criterion names, one criterion at a time ────────
# Each criterion's tests run in their **own** pytest process. 5e's review found
# two tests that only passed inside a full run — one leaning on contacts another
# module had seeded, one reading a list an earlier test in its own file filled,
# the latter passing in isolation with two assertions comparing 0 == 0. A test
# that needs its neighbours proves nothing about the criterion it is named for.
#
# 5i found the same shape from the other side: the ordering test ran after the
# migration test in its own file, read a row that test had already converted,
# and so passed under a mutation that ordered by the raw string. The isolation
# below is what surfaces that class of thing.
step "1-8. the behaviour each criterion names, each in its own pytest process"
declare -a CRITERIA=(
  "1|the clock has one writer: no server default, one spelling from one clock, and an ordering the pre-migration string comparison gets wrong|tests/test_list_picker.py::test_the_model_carries_no_server_default_on_created_at tests/test_list_picker.py::test_an_insert_that_omits_created_at_still_gets_the_application_clock tests/test_list_picker.py::test_both_insert_paths_write_the_same_spelling_from_the_same_clock tests/test_list_picker.py::test_the_migration_converts_a_space_row_and_leaves_a_t_row_alone tests/test_list_picker.py::test_a_value_matching_neither_spelling_is_left_alone tests/test_list_picker.py::test_an_unparseable_created_at_sorts_last_and_does_not_raise tests/test_list_picker.py::test_the_picker_orders_lists_by_the_parsed_value_not_the_string"
  "3|the picker is the new shape: pinned entry first, lists newest first, no category entry|tests/test_list_picker.py::test_the_pinned_entry_is_first_and_is_the_one_constant tests/test_list_picker.py::test_the_picker_offers_no_category_entry tests/test_list_picker.py::test_every_entry_carries_what_the_dashboard_card_needs tests/test_list_picker.py::test_a_list_with_no_active_members_is_offered_and_reads_zero tests/test_list_picker.py::test_a_deactivated_member_leaves_the_count_at_zero tests/test_categories.py::test_the_picker_offers_no_category_and_the_resolver_still_counts_them"
  "4|historical category selectors still resolve and still render their label|tests/test_list_picker.py::test_a_historical_category_selector_still_resolves_and_labels tests/test_categories.py::test_the_picker_offers_no_category_and_the_resolver_still_counts_them tests/test_categories.py::test_audience_labels_read_like_english tests/test_categories.py::test_audience_label_never_raises_on_a_bad_selector tests/test_import.py::test_a_committed_import_shows_up_in_the_category_count"
  "5|a list campaign and an all campaign need no category; a category selector with neither still refuses|tests/test_campaign_guardrails.py::test_a_list_audience_needs_no_category tests/test_campaign_guardrails.py::test_the_all_audience_needs_no_category tests/test_campaign_guardrails.py::test_a_category_selector_with_no_category_is_still_rejected tests/test_campaign_guardrails.py::test_the_rule_lives_in_the_service_not_the_router tests/test_campaign_first_flow.py::test_the_ordinary_path_takes_a_list_audience_with_no_category tests/test_campaign_first_flow.py::test_a_hand_written_category_selector_still_demands_a_category"
  "6|the render scan passes across every client-facing GET route outside the retained taxonomy|tests/test_audience_surfaces.py::test_the_sweep_covers_the_audience_surface tests/test_audience_surfaces.py::test_no_audience_surface_names_a_category tests/test_audience_surfaces.py::test_the_sweep_actually_fires tests/test_audience_surfaces.py::test_no_exemption_has_gone_stale tests/test_audience_surfaces.py::test_the_prospects_exemption_is_doing_real_work tests/test_contacts_api.py::test_the_contacts_page_names_no_category_and_offers_a_named_import tests/test_dashboard.py::test_the_dashboard_offers_no_category"
  "7|the prospect queue is unchanged: the chip is there and promoting still requires a category|tests/test_audience_surfaces.py::test_the_prospect_queue_still_has_its_category_chip tests/test_audience_surfaces.py::test_promoting_a_prospect_still_requires_a_category tests/test_audience_surfaces.py::test_the_taxonomy_endpoints_are_retained"
  "8|the em-dash rule survives: a list never texted renders — and not 0|tests/test_dashboard.py::test_never_texted_list_renders_an_em_dash_not_a_zero tests/test_dashboard.py::test_days_since_last_send_is_computed_from_actual_sends tests/test_dashboard.py::test_staleness_threshold_flags_only_the_stale_list tests/test_list_picker.py::test_freshness_comes_from_messages_and_not_from_an_audience_string"
)
for entry in "${CRITERIA[@]}"; do
  number="${entry%%|*}"; rest="${entry#*|}"
  label="${rest%%|*}"; targets="${rest#*|}"
  # shellcheck disable=SC2086
  if ! PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "$PY" -m pytest $targets -q \
       >"$SCRATCH/criterion-$number.log" 2>&1; then
    tail -25 "$SCRATCH/criterion-$number.log"
    bad "criterion $number: $label"
  else
    ok "$number. $label"
  fi
done

# ── 1b. The ordering, shown rather than claimed ─────────────────────────────
# A passing test named "the picker orders by date" would also pass on a string
# comparison that happens to agree. This prints both orders side by side on the
# two spellings the live database actually holds, so the disagreement is on the
# transcript rather than asserted out of sight.
step "1b. the two live spellings, ordered both ways"
"$PY" - <<'PY'
import sys
sys.path.insert(0, ".")
from app.models.contact_list import parse_created_at

# Exactly the two shapes in data/app.db, on one day so only the time differs.
rows = [("created tonight",      "2026-08-27 23:30:00"),
        ("created this morning", "2026-08-27T10:00:00.000000")]

by_string = [n for n, v in sorted(rows, key=lambda r: r[1], reverse=True)]
by_parsed = [n for n, v in sorted(rows, key=lambda r: parse_created_at(r[1]),
                                  reverse=True)]
print("     newest-first by raw string:  " + " | ".join(by_string))
print("     newest-first by parsed date: " + " | ".join(by_parsed))
assert by_string != by_parsed, (
    "the two spellings chosen for this check do not actually disagree, so the "
    "ordering test would pass whether or not the fix were present")
assert by_parsed[0] == "created tonight"
print("     the string order is wrong, the parsed order is right")
PY
if [[ $? -ne 0 ]]; then
  bad "the ordering demonstration did not show the defect it exists to fix"
else
  ok "a lexicographic comparison across the two spellings gets it backwards"
fi

# ── 2. Migrations: from a clean database, and against a copy of the live one ─
step "2a. alembic upgrade head from a clean database"
rm -f "$SCRATCH/clean.db"
if ! DATABASE_URL="sqlite:///$SCRATCH/clean.db" "$PY" -m alembic upgrade head \
     >"$SCRATCH/alembic-clean.log" 2>&1; then
  tail -20 "$SCRATCH/alembic-clean.log"
  bad "alembic upgrade head failed from a clean database"
else
  ok "$(grep -c 'Running upgrade' "$SCRATCH/alembic-clean.log") revisions applied"
fi

step "2b. alembic upgrade head against a COPY of the developer database"
# A1's migration rewrites data in a live table, so it is run against real rows
# before it is trusted. The copy is what is migrated; data/app.db is untouched.
#
# The COPY is put back into the state production is in before it is migrated,
# and neither half of that is padding.
#
# `app/main.py` migrates on startup in development, so merely running the app
# once takes `data/app.db` to head and converts every server-default row in it.
# After that this check has nothing to convert AND nothing to run: `upgrade
# head` on a copy already stamped `f4a1c7d90e52` is a no-op, and the check
# printed an identical before and after while reporting "ok". A check whose
# evidence evaporates the first time somebody starts the server is a green light
# wired to nothing — it took a seeded row to notice, and the seeded row was not
# converted either.
#
# So the copy gets a row carrying the exact spelling SQLite's CURRENT_TIMESTAMP
# produces, and its `alembic_version` is rolled back to the revision the live box
# is on. What then runs is the real migration against real rows from the
# revision production will run it from, which is the only arrangement that
# answers the question this check exists to ask.
if [[ ! -f data/app.db ]]; then
  echo "   (skipped — no data/app.db on this machine)"
else
  cp data/app.db "$SCRATCH/live-copy.db"
  "$PY" - "$SCRATCH/live-copy.db" <<'PY'
import sqlite3, sys
PRE_5I = "c8a2e5f14b90"          # the revision the live box is on today

db = sqlite3.connect(sys.argv[1])
db.execute("DELETE FROM contact_lists WHERE name = 'accept-5i server-default probe'")
db.execute("INSERT INTO contact_lists (name, source, created_at) VALUES (?, ?, ?)",
           ("accept-5i server-default probe", "test", "2026-08-20 00:12:30"))
# Rolling the stamp back rather than running `alembic downgrade`: f4a1c7d90e52's
# downgrade is deliberately a no-op — once a row is in isoformat, nothing
# distinguishes "converted from UTC" from "always was local", so a real
# downgrade could only re-shift rows it cannot identify. The migration is
# data-only and idempotent, so re-running it over already-converted rows is
# exactly what production would do if it were applied twice.
db.execute("UPDATE alembic_version SET version_num = ?", (PRE_5I,))
db.commit()
print(f"     copy stamped back to {PRE_5I}, the revision the live box is on")
rows = list(db.execute(
    "select id, name, created_at from contact_lists order by id"))
print("     before:")
for row in rows:
    print(f"       {row}")
assert any(r[2] and " " in r[2] for r in rows), (
    "no server-default row to convert, so the migration below demonstrates "
    "nothing")
PY
  if ! DATABASE_URL="sqlite:///$SCRATCH/live-copy.db" "$PY" -m alembic upgrade head \
       >"$SCRATCH/alembic-live.log" 2>&1; then
    tail -20 "$SCRATCH/alembic-live.log"
    bad "alembic upgrade head failed against a copy of the live database"
  else
    grep 'contact_lists.created_at normalised' "$SCRATCH/alembic-live.log" \
      | sed 's/^.*INFO  \[/     [/' || true
    if ! "$PY" - "$SCRATCH/live-copy.db" <<'PY'
import sqlite3, sys
from datetime import datetime, timezone
rows = list(sqlite3.connect(sys.argv[1]).execute(
    "select id, name, created_at from contact_lists order by id"))
print("     after:")
for row in rows:
    print(f"       {row}")
bad = [r for r in rows if r[2] and " " in r[2]]
assert not bad, f"a space-separated created_at survived the migration: {bad}"
for _, name, value in rows:
    if value:
        datetime.fromisoformat(value)      # every surviving row still parses

# The seeded row specifically, converted rather than merely left alone: it went
# in as 2026-08-20 00:12:30 read as UTC, and must come out naming that instant
# on the local clock.
probe = [r for r in rows if r[1] == "accept-5i server-default probe"]
assert len(probe) == 1, probe
converted = datetime.fromisoformat(probe[0][2])
assert converted.astimezone() == datetime.fromisoformat(
    "2026-08-20 00:12:30").replace(tzinfo=timezone.utc), converted
print(f"     the seeded UTC row became {probe[0][2]} on the local clock")
PY
    then
      bad "the migration left a row the picker cannot order"
    else
      ok "every row is isoformat and parses; the original data/app.db is untouched"
    fi
  fi
fi

# ── 9. The gate, twice in a row ─────────────────────────────────────────────
step "9. agent/gate.sh, twice"
GATE_PATH="$PATH"
[[ -x .venv/bin/python ]] && GATE_PATH="$PWD/.venv/bin:$PATH"
for run in 1 2; do
  if ! PATH="$GATE_PATH" bash agent/gate.sh >"$SCRATCH/gate-$run.log" 2>&1; then
    tail -20 "$SCRATCH/gate-$run.log"
    bad "the gate did not pass on run $run"
  else
    ok "run $run: $(grep -E '^[0-9]+ passed' "$SCRATCH/gate-$run.log" | tail -1)"
  fi
done

# ── 10. Each guarantee reverted on its own, on a verified-pristine tree ─────
step "10. every 5i guarantee reverted one at a time; each must break a test"
MUTATION_TREE="$SCRATCH/mutation-tree"
rm -rf "$MUTATION_TREE"
mkdir -p "$MUTATION_TREE"
if ! rsync -a --exclude .venv --exclude node_modules --exclude .git --exclude data \
      --exclude '__pycache__' --exclude '*.db' --exclude .pytest_cache \
      ./ "$MUTATION_TREE/" 2>/dev/null; then
  bad "could not stage a scratch tree for the mutation check"
elif ! "$ABS_PY" agent/mutate-5i.py "$MUTATION_TREE" "$ABS_PY" >"$SCRATCH/mutations.log" 2>&1; then
  sed 's/^/   /' "$SCRATCH/mutations.log"
  bad "a guarantee was reverted and no test noticed"
else
  grep -E '^(SCRATCH VERIFIED|A[0-9]|  CAUGHT|[0-9]+ mutations)' \
    "$SCRATCH/mutations.log" | sed 's/^/   /'
  ok "every reverted guarantee breaks at least one test that names it"
fi
rm -rf "$MUTATION_TREE"

printf '\n'
if [[ $FAIL -eq 0 ]]; then
  echo "ACCEPT PASS — session 5i Part A"
else
  echo "ACCEPT FAILED — see above"
fi
exit $FAIL
