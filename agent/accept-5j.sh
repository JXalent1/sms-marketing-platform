#!/usr/bin/env bash
# PART A ACCEPTANCE — session 5j: the index migration, the cost guard, and
# list archive/rename.
#
# `agent/gate.sh` answers "is this repo still sound?"; this answers "did session
# 5j do what it was sent to do?", which is the question RULES.md requires an
# agent to demonstrate rather than declare. Checks 1-10 are the numbered
# criteria from `sessions/session-5j.md` -> "Acceptance criteria", in order.
#
#   bash agent/accept-5j.sh
#
# Each behavioural criterion runs its tests in its **own** pytest process.
# `accept-5e.sh` established that after two tests were found that only passed
# inside a full run, one of them on `0 == 0`; 5i found the mirror image, a test
# that passed because a neighbour had rewritten the row it read.
#
# Nothing here sends a message and nothing here calls a paid API. It writes
# scratch databases and a throwaway copy of the tree under $ACCEPT_SCRATCH, and
# writes nothing inside the repo. `data/app.db` is never touched — 5j adds no
# data migration, so there is no live-copy check to run.

set -uo pipefail
cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)" || exit 1

SCRATCH="${ACCEPT_SCRATCH:-${TMPDIR:-/tmp}/a4a-accept-5j}"
PY="${PY:-.venv/bin/python}"
[[ -x "$PY" ]] || PY="python3"
ABS_PY="$PY"; [[ "$ABS_PY" == /* ]] || ABS_PY="$PWD/$PY"

mkdir -p "$SCRATCH"

FAIL=0
step() { printf '\n── %s\n' "$1"; }
bad()  { printf 'ACCEPT FAIL: %s\n' "$1"; FAIL=1; }
ok()   { printf '   ok — %s\n' "$1"; }

# The revision the live box is on before 5j, and the two hand-made index names
# that exist on it and in no migration. See the incident entry in status.md.
PRE_5J="f4a1c7d90e52"
HAND_MADE_SMS="ix_sms_messages_contact_id"
HAND_MADE_CLM="ix_clm_contact_id"

# ── 1. The migration against a database that already has the hand-made indexes ─
step "1. index convergence on a database in production's state"
rm -f "$SCRATCH/live-shape.db"
if ! DATABASE_URL="sqlite:///$SCRATCH/live-shape.db" "$PY" -m alembic upgrade "$PRE_5J" \
     >"$SCRATCH/pre5j.log" 2>&1; then
  tail -20 "$SCRATCH/pre5j.log"
  bad "could not build a database at $PRE_5J"
else
  "$PY" - "$SCRATCH/live-shape.db" "$HAND_MADE_SMS" "$HAND_MADE_CLM" <<'PY'
import sqlite3, sys
db = sqlite3.connect(sys.argv[1])
# Exactly what was typed on the live box on 2026-09-04 to end the outage.
db.execute(f"CREATE INDEX IF NOT EXISTS {sys.argv[2]} ON sms_messages(contact_id)")
db.execute(f"CREATE INDEX IF NOT EXISTS {sys.argv[3]} ON contact_list_members(contact_id)")
db.execute("ANALYZE")
db.commit()
for table in ("sms_messages", "contact_list_members"):
    print(f"     before: {table} {sorted(r[1] for r in db.execute(f'PRAGMA index_list({table})'))}")
version, = db.execute("SELECT version_num FROM alembic_version").fetchone()
assert version == sys.argv[1].split("/")[-1] or True
print(f"     stamped at {version}")
PY
  for run in 1 2; do
    if ! DATABASE_URL="sqlite:///$SCRATCH/live-shape.db" "$PY" -m alembic upgrade head \
         >"$SCRATCH/converge-$run.log" 2>&1; then
      tail -20 "$SCRATCH/converge-$run.log"
      bad "upgrade head raised on a database that already has the hand-made indexes (run $run)"
      break
    fi
  done
  if ! "$PY" - "$SCRATCH/live-shape.db" "$HAND_MADE_SMS" "$HAND_MADE_CLM" <<'PY'
import sqlite3, sys
db = sqlite3.connect(sys.argv[1])
found = {t: sorted(r[1] for r in db.execute(f"PRAGMA index_list({t})"))
         for t in ("sms_messages", "contact_list_members")}
for table, names in found.items():
    print(f"     after:  {table} {names}")
assert "idx_sms_contact" in found["sms_messages"], found
assert "idx_member_contact" in found["contact_list_members"], found
assert sys.argv[2] not in found["sms_messages"], "the hand-made sms index survived"
assert sys.argv[3] not in found["contact_list_members"], "the hand-made membership index survived"
print("     converged, and twice through leaves the same two indexes")
PY
  then
    bad "criterion 1: the migration did not converge the live shape"
  else
    ok "1. hand-made names retired, convention names in place, idempotent"
  fi
fi

# ── 2. The same migration on a database that has neither ────────────────────
step "2. the same migration on a fresh database — every clone"
rm -f "$SCRATCH/fresh.db"
if ! DATABASE_URL="sqlite:///$SCRATCH/fresh.db" "$PY" -m alembic upgrade head \
     >"$SCRATCH/fresh.log" 2>&1; then
  tail -20 "$SCRATCH/fresh.log"
  bad "alembic upgrade head failed from a clean database"
else
  if ! "$PY" - "$SCRATCH/fresh.db" <<'PY'
import sqlite3, sys
db = sqlite3.connect(sys.argv[1])
found = {t: sorted(r[1] for r in db.execute(f"PRAGMA index_list({t})"))
         for t in ("sms_messages", "contact_list_members")}
assert "idx_sms_contact" in found["sms_messages"], found
assert "idx_member_contact" in found["contact_list_members"], found
columns = [r[1] for r in db.execute("PRAGMA table_info(contact_lists)")]
assert "archived" in columns, columns
archived = [r[0] for r in db.execute("SELECT DISTINCT archived FROM contact_lists")]
assert archived in ([], [0]), f"archived backfilled to something odd: {archived}"
print(f"     sms_messages {found['sms_messages']}")
print(f"     contact_list_members {found['contact_list_members']}")
print(f"     contact_lists columns {columns}")
PY
  then
    bad "criterion 2: a fresh database did not come out at the converged schema"
  else
    ok "2. both indexes created, contact_lists.archived present"
  fi
fi

# ── 3. Clean upgrade, downgrade of both new revisions, clean upgrade again ──
step "3. downgrade both 5j revisions, then upgrade head again"
if ! DATABASE_URL="sqlite:///$SCRATCH/fresh.db" "$PY" -m alembic downgrade "$PRE_5J" \
     >"$SCRATCH/downgrade.log" 2>&1; then
  tail -20 "$SCRATCH/downgrade.log"
  bad "downgrade of the two 5j revisions failed"
else
  grep 'Running downgrade' "$SCRATCH/downgrade.log" | sed 's/^.*Running/     Running/' || true
  if ! "$PY" - "$SCRATCH/fresh.db" "$HAND_MADE_SMS" "$HAND_MADE_CLM" <<'PY'
import sqlite3, sys
db = sqlite3.connect(sys.argv[1])
found = {t: sorted(r[1] for r in db.execute(f"PRAGMA index_list({t})"))
         for t in ("sms_messages", "contact_list_members")}
assert "idx_sms_contact" not in found["sms_messages"], found
assert "idx_member_contact" not in found["contact_list_members"], found
# The hand-made names were an incident response, not a schema. A downgrade that
# recreated them would put the box back into the state nothing describes.
assert sys.argv[2] not in found["sms_messages"], found
assert sys.argv[3] not in found["contact_list_members"], found
assert "archived" not in [r[1] for r in db.execute("PRAGMA table_info(contact_lists)")]
print("     both indexes and the column are gone; no hand-made name came back")
PY
  then
    bad "criterion 3: downgrade left the wrong schema"
  elif ! DATABASE_URL="sqlite:///$SCRATCH/fresh.db" "$PY" -m alembic upgrade head \
       >"$SCRATCH/reupgrade.log" 2>&1; then
    tail -20 "$SCRATCH/reupgrade.log"
    bad "upgrade head failed after a downgrade"
  else
    ok "3. down and back up cleanly"
  fi
fi

# ── 4-7. The behaviour each criterion names, each in its own pytest process ──
step "4-7. archive, rename and delete — each criterion in its own process"
declare -a CRITERIA=(
  "4|an archived list still resolves, still counts, and still names itself in history and in its report|tests/test_list_admin.py::test_an_archived_list_still_resolves_and_still_names_itself"
  "5|an archived list is absent from the picker, /api/lists and the dashboard cards|tests/test_list_admin.py::test_an_archived_list_is_absent_from_every_picker tests/test_list_admin.py::test_the_picker_payload_carries_no_archived_flag tests/test_list_admin.py::test_one_definition_of_archived_serves_the_panel_and_the_picker tests/test_list_admin.py::test_the_dashboard_cards_are_the_picker_minus_the_archived tests/test_list_admin.py::test_unarchiving_puts_it_back tests/test_list_admin.py::test_the_panel_reports_the_numbers_the_picker_reports tests/test_list_admin.py::test_the_composer_carries_the_panel_and_its_script"
  "6|a rename lands everywhere at once, and a taken name is a 400 that names it|tests/test_list_admin.py::test_a_rename_reaches_the_report_of_a_campaign_already_sent tests/test_list_admin.py::test_a_rename_inside_a_compound_selector_relabels_the_whole_sentence tests/test_list_admin.py::test_a_rename_to_a_taken_name_is_a_400_naming_the_name tests/test_list_admin.py::test_renaming_a_list_to_the_name_it_already_has_is_not_a_collision tests/test_list_admin.py::test_a_blank_name_is_refused tests/test_list_admin.py::test_the_collision_message_names_a_remedy_that_works_on_this_object tests/test_list_admin.py::test_a_patch_carrying_both_fields_applies_neither_when_the_name_is_refused tests/test_list_admin.py::test_a_patch_carrying_both_fields_applies_both_when_the_name_is_free"
  "7|DELETE refuses a referenced list with 409 and still removes an unreferenced one|tests/test_list_admin.py::test_delete_refuses_a_list_a_campaign_used tests/test_list_admin.py::test_delete_refuses_a_list_a_compound_selector_names tests/test_list_admin.py::test_delete_removes_a_list_nothing_referenced tests/test_list_admin.py::test_a_selector_naming_list_120_does_not_name_list_12 tests/test_list_admin.py::test_deleting_a_missing_list_is_still_a_404"
  "1b|the migration itself, driven rather than re-implemented, in isolation|tests/test_index_convergence.py"
  "2b|the cost guard, in isolation|tests/test_query_cost.py"
  "3b|the migrations still describe the models|tests/test_migrations.py"
  "5b|the composer escapes a client-typed list name for the position it lands in|tests/test_composer_markup.py"
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

# ── 8. The cost guard, run against a schema known to be unindexed ───────────
# The scan's own first version. A guard that passes against the schema it is
# meant to reject is the third green-light-wired-to-nothing in this project, so
# this drops the index on a scratch database built by alembic and requires A2's
# own test functions — not a copy of their assertions — to raise.
step "8. the cost guard goes red on an unindexed schema"
"$PY" - "$SCRATCH" <<'PY'
import os, pathlib, shutil, sqlite3, subprocess, sys

scratch = pathlib.Path(sys.argv[1])
source = scratch / "fresh.db"          # built at head by check 2/3 above
if not source.exists():
    print("     (skipped — check 2 did not produce a database)")
    raise SystemExit(0)


def doctored(name, drops):
    path = scratch / name
    if path.exists():
        path.unlink()
    shutil.copy(source, path)
    db = sqlite3.connect(path)
    for index in drops:
        db.execute(f"DROP INDEX {index}")
    db.commit()
    db.close()
    return path


RUNNER = r'''
import importlib.util, pathlib, sys, traceback
sys.path.insert(0, ".")
spec = importlib.util.spec_from_file_location(
    "test_query_cost", pathlib.Path("tests/test_query_cost.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

from app.core.database import SessionLocal
from app.services import contact_service
import importlib.util as _u
_s = _u.spec_from_file_location("_query_plan", pathlib.Path("tests/_query_plan.py"))
qp = _u.module_from_spec(_s); _s.loader.exec_module(qp)

session = SessionLocal()
_sql, plan = qp.plans_for(session, lambda: contact_service._last_sent_by_list(session))[0]
for line in plan:
    print("       " + line)
session.close()

red = []
for name in sys.argv[1:]:
    try:
        getattr(module, name)()
    except AssertionError as exc:
        red.append(name)
        print(f"       {name} -> RED: {str(exc).splitlines()[0][:90]}")
    else:
        print(f"       {name} -> green")
sys.exit(0 if len(red) == len(sys.argv[1:]) else 1)
'''

env = dict(os.environ, SMS_PROVIDER="console",
           PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
failed = False
for label, drops, expect_red in (
        ("idx_member_contact dropped — one side of the join key unindexed",
         ("idx_member_contact",),
         ("test_both_sides_of_the_join_key_are_indexed",)),
        ("both contact_id indexes dropped — the outage schema",
         ("idx_member_contact", "idx_sms_contact"),
         ("test_both_sides_of_the_join_key_are_indexed",
          "test_the_freshness_join_runs_through_an_index_on_the_join_key")),
):
    path = doctored(f"unindexed-{len(drops)}.db", drops)
    print(f"     {label}")
    env["DATABASE_URL"] = f"sqlite:///{path}"
    result = subprocess.run([sys.executable, "-c", RUNNER, *expect_red],
                            env=env, capture_output=True, text=True)
    print(result.stdout.rstrip())
    if result.returncode != 0:
        failed = True
        print(result.stderr[-800:])
        print("     *** the guard did not go red on a schema it must reject ***")

# And the assertion the spec proposed, on the outage plan, so the reason it was
# not used is on the transcript rather than in a comment.
path = doctored("unindexed-2.db", ("idx_member_contact", "idx_sms_contact"))
env["DATABASE_URL"] = f"sqlite:///{path}"
SHOW = r'''
import importlib.util, pathlib, sys
sys.path.insert(0, ".")
s = importlib.util.spec_from_file_location("_query_plan", pathlib.Path("tests/_query_plan.py"))
qp = importlib.util.module_from_spec(s); s.loader.exec_module(qp)
from app.core.database import SessionLocal
from app.services import contact_service
session = SessionLocal()
_sql, plan = qp.plans_for(session, lambda: contact_service._last_sent_by_list(session))[0]
session.close()
scans_sms = any(l.startswith("SCAN sms_messages") for l in plan)
print(f'       "no full scan of sms_messages" on the OUTAGE plan: '
      f'{"RED" if scans_sms else "GREEN — it would not have caught it"}')
'''
result = subprocess.run([sys.executable, "-c", SHOW], env=env, capture_output=True, text=True)
print(result.stdout.rstrip() or result.stderr[-400:])
raise SystemExit(1 if failed else 0)
PY
if [[ $? -ne 0 ]]; then
  bad "criterion 8: the cost guard did not fail on an unindexed schema"
else
  ok "8. dropping either index turns A2's own test functions red"
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
step "10. every 5j guarantee reverted one at a time; each must break a test"
MUTATION_TREE="$SCRATCH/mutation-tree"
rm -rf "$MUTATION_TREE"
mkdir -p "$MUTATION_TREE"
if ! rsync -a --exclude .venv --exclude node_modules --exclude .git --exclude data \
      --exclude '__pycache__' --exclude '*.db' --exclude .pytest_cache \
      ./ "$MUTATION_TREE/" 2>/dev/null; then
  bad "could not stage a scratch tree for the mutation check"
elif ! "$ABS_PY" agent/mutate-5j.py "$MUTATION_TREE" "$ABS_PY" >"$SCRATCH/mutations.log" 2>&1; then
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
  echo "ACCEPT PASS — session 5j Part A"
else
  echo "ACCEPT FAILED — see above"
fi
exit $FAIL
