#!/usr/bin/env bash
# PART A ACCEPTANCE — session 5m, the composer's audience panel and time in Eastern.
#
# The stop condition for this session. `agent/gate.sh` answers "is this repo
# still sound?"; this answers "did session 5m do what it was sent to do?", which
# RULES.md requires an agent to demonstrate rather than declare. Checks 1-10 are
# the numbered criteria from `sessions/session-5m.md` -> "Acceptance", in order;
# the lettered checks *show* the thing the numbered check asserts, so a reader
# sees the figure rather than a tick.
#
#   bash agent/accept-5m.sh
#
# ── Nothing here sends ──────────────────────────────────────────────────────
#
# SMS_PROVIDER is console in every subprocess, and `tests/conftest.py` forces it
# for the whole suite. Nothing here reaches a paid API.
#
# What it DOES do to your machine: writes scratch files under $ACCEPT_SCRATCH,
# and for check 10 rsyncs a throwaway copy of the tree there. It writes nothing
# inside the repo — check 6 in particular builds its OWN database copy rather
# than reading `data/app.db`, because starting the app in development migrates
# that file and a check whose evidence lives in it evaporates (CLAUDE.md).
#
# TURN CAP: 45 assistant turns. On exhausting it, stop and write
# `decisions/NNN-*.open.md` rather than continuing.

set -uo pipefail
cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)" || exit 1

SCRATCH="${ACCEPT_SCRATCH:-${TMPDIR:-/tmp}/a4a-accept-5m}"
PY="${PY:-.venv/bin/python}"
[[ -x "$PY" ]] || PY="python3"
ABS_PY="$PY"; [[ "$ABS_PY" == /* ]] || ABS_PY="$PWD/$PY"

mkdir -p "$SCRATCH"

FAIL=0
step() { printf '\n── %s\n' "$1"; }
bad()  { printf 'ACCEPT FAIL: %s\n' "$1"; FAIL=1; }
ok()   { printf '   ok — %s\n' "$1"; }

run_tests() {   # run_tests <label> <pytest args...>
  local label="$1"; shift
  if SMS_PROVIDER=console PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
     "$PY" -m pytest "$@" -q --no-header 2>&1 | tail -3; then
    ok "$label"
  else
    bad "$label"
  fi
}

# ── 0. node is present, because half of this session is JavaScript ──────────
step "0. the composer's panel is JavaScript, so node has to be here"
if command -v node >/dev/null 2>&1; then
  ok "node $(node --version) — npm is already a build dependency (npm run build:css)"
else
  bad "node is missing; the panel tests cannot run and nothing else proves A1"
fi

# ── 1. The panel's mechanism is explained in status.md, before the fix ──────
step "1. the mechanism is written down, reproduced against the production shape"
# Newlines collapsed first. `grep -F` is line-based and status.md is wrapped at
# 80 columns, so the first phrase below straddles a line break and a line-based
# check reports it missing — a measurement script wrong about prose, for the
# seventh time in this project. The claim is about content, not line wrapping.
FLAT="$SCRATCH/status.flat"
tr '\n' ' ' < status.md | tr -s ' ' > "$FLAT"
missing=0
for phrase in "10,146 active contacts, 3,460 on the blocklist" \
              "fires no \`change\` event" \
              "twelve times slower" \
              "second writer" \
              "would have gone to 443"; do
  grep -Fq "$phrase" "$FLAT" || { echo "   missing from status.md: $phrase"; missing=1; }
done
# And the check fires: a phrase that is deliberately not there must not be found.
if grep -Fq "the panel was correct all along" "$FLAT"; then
  bad "check 1's matcher is broken — it finds text that is not in status.md"
fi
[[ $missing -eq 0 ]] && ok "status.md carries the measured shape, the three mechanisms, the timings and the answer" \
                     || bad "status.md does not explain the mechanism"

# ── 2. Every figure comes from one response; a stale reply cannot paint ─────
step "2. one response per panel, and a stale reply is discarded"
run_tests "the panel's rows, driven through the real partials in node" \
  tests/test_composer_panel.py::test_a_stale_reply_cannot_paint_over_a_newer_one \
  tests/test_composer_panel.py::test_a_stale_reply_does_not_repaint_the_phone_preview \
  tests/test_composer_panel.py::test_every_row_of_the_panel_comes_from_one_response \
  tests/test_composer_panel.py::test_preflight_moves_every_row_or_none \
  tests/test_composer_panel.py::test_switching_to_upload_retires_the_replies_still_in_flight

step "2b. and the harness fails on purpose when the gate is removed"
run_tests "the reverted guard lets the stale reply win, in a scratch copy" \
  tests/test_composer_panel.py::test_the_harness_reproduces_the_defect_it_was_written_for

# ── 3. The panel's audience is the selector a send would use ───────────────
step "3. the panel names the selector POST /api/campaigns receives"
run_tests "asserted through the endpoints, not through audience_label()" \
  tests/test_composer_panel.py::test_the_panel_names_the_selector_the_send_will_use \
  tests/test_composer_panel.py::test_the_panel_and_the_draft_name_the_same_audience

# ── 4. Segments and recipients are consistent ──────────────────────────────
step "4. total segments = recipients x segments per message"
run_tests "on both responses the panel is painted from, capped and uncapped" \
  tests/test_composer_panel.py::test_total_segments_is_recipients_times_segments_per_message \
  tests/test_composer_panel.py::test_a_capped_send_is_quoted_at_the_cap_on_both_surfaces \
  tests/test_composer_panel.py::test_an_empty_message_is_zero_segments_and_the_audience_is_still_named

# ── 5. 6:00 PM Eastern dispatches at 6:00 PM Eastern, in EDT and in EST ────
step "5. the hour a campaign goes out, in September and in January"
run_tests "both zones, both changeover Sundays" \
  tests/test_timezone.py::test_six_pm_eastern_dispatches_at_six_pm_eastern_in_edt \
  tests/test_timezone.py::test_six_pm_eastern_dispatches_at_six_pm_eastern_in_est \
  tests/test_timezone.py::test_the_same_wall_clock_is_two_instants_in_summer_and_winter \
  tests/test_timezone.py::test_the_repeated_hour_dispatches_once \
  tests/test_timezone.py::test_the_hour_that_does_not_exist_dispatches_late_not_early

step "5b. shown: the same campaign against the clock that used to dispatch it"
SMS_PROVIDER=console "$PY" - <<'PY'
import sys; sys.path.insert(0, ".")
from datetime import datetime, timezone
from app.core import clock

# The campaign in front of the operator: "09/09, 6:00 PM Private Record Collection".
stored = "2026-09-09T18:00:00"
for label, moment in (("18:00 UTC (what the droplet called 'now' at 2:00 PM ET)",
                       datetime(2026, 9, 9, 18, 0, tzinfo=timezone.utc)),
                      ("22:00 UTC (six in the evening in Fort Lauderdale)",
                       datetime(2026, 9, 9, 22, 0, tzinfo=timezone.utc))):
    cutoff = clock.wall_clock(moment).isoformat()
    print(f"   {label:<48} cutoff={cutoff}  due={stored <= cutoff}")
PY

# ── 6. The migration converts what it can and reports what it cannot ───────
step "6. the migration, against a copy in the state production is in"
DB="$SCRATCH/scheduled_at.db"
rm -f "$DB"
DATABASE_URL="sqlite:///${DB}" "$PY" - <<'PY'
import os, sys; sys.path.insert(0, ".")
from alembic import command
from alembic.config import Config
cfg = Config(); cfg.set_main_option("script_location", "alembic")
# The revision the live box is on before this session's migration, so the
# upgrade below actually runs. `upgrade head` against a copy already stamped at
# head runs nothing at all and looks calm doing it — CLAUDE.md, three times.
command.upgrade(cfg, "d7e2a91c4f36")
from sqlalchemy import create_engine, text
engine = create_engine(os.environ["DATABASE_URL"])
with engine.begin() as conn:
    rows = [("09/09, 6:00 PM Private Record Collection", "draft", "2026-09-09T18:00:00"),
            ("An API caller's instant",                  "draft", "2026-09-09T22:00:00+00:00"),
            ("A row nobody can read",                    "draft", "tonight")]
    for i, (name, status, when) in enumerate(rows, 1):
        conn.execute(text("INSERT INTO campaigns (id, name, message_template, audience, "
                          "status, scheduled_at) VALUES (:i, :n, 'x', 'all', :s, :w)"),
                     {"i": 900 + i, "n": name, "s": status, "w": when})
    print("   before:")
    for r in conn.execute(text("SELECT id, scheduled_at FROM campaigns WHERE id > 900")):
        print(f"      {r[0]}  {r[1]!r}")
PY
DATABASE_URL="sqlite:///${DB}" "$PY" -m alembic upgrade head >/dev/null 2>"$SCRATCH/migrate.log"
grep -E "adjudicated|unreadable" "$SCRATCH/migrate.log" | sed 's/^/   /'
DATABASE_URL="sqlite:///${DB}" "$PY" - <<'PY'
import os, sys; sys.path.insert(0, ".")
from sqlalchemy import create_engine, text
engine = create_engine(os.environ["DATABASE_URL"])
with engine.begin() as conn:
    print("   after:")
    seen = {}
    for r in conn.execute(text("SELECT id, scheduled_at FROM campaigns WHERE id > 900")):
        print(f"      {r[0]}  {r[1]!r}")
        seen[r[0]] = r[1]
    assert seen[901] == "2026-09-09T18:00:00", "the operator's wall clock must not move"
    assert seen[902] == "2026-09-09T18:00:00", "an instant becomes the client's wall clock"
    assert seen[903] == "tonight", "an unreadable row is left alone"
    print("   ok — one left alone, one converted, one reported and untouched")
PY
[[ ${PIPESTATUS[0]:-0} -eq 0 ]] || bad "the migration did not adjudicate as documented"
# The criterion is "converts existing rows AND reports what it could not
# classify". The row values are asserted above; without this the migration
# could stop naming the unreadable one and this check would still pass —
# the half that was printed and never checked.
if grep -q "campaign 903 has an unreadable scheduled_at" "$SCRATCH/migrate.log" \
   && grep -q "1 converted from an offset" "$SCRATCH/migrate.log"; then
  ok "and it reported the row it could not classify, by id"
else
  bad "the migration did not report what it could not classify"
fi
run_tests "and the migration's own functions, driven directly" \
  tests/test_timezone.py::test_the_migration_leaves_a_wall_clock_alone_and_converts_an_instant

# ── 7. Every client-facing timestamp renders in the client's zone ──────────
step "7. one formatter, on the rail, history and the report"
run_tests "run in node under three viewer zones, and swept for a second one" \
  tests/test_timezone.py -k "renders_in_the_clients_zone or shared_formatter or \
builds_a_date or sweep_fires or template_global or goes_red_on_the_formatter"

# ── 8. A campaign still cannot double-send ────────────────────────────────
step "8. the draft filter, after the comparison changed"
run_tests "every non-draft status, and the repeated hour" \
  tests/test_timezone.py::test_a_campaign_that_has_run_is_never_due_again \
  tests/test_timezone.py::test_the_repeated_hour_dispatches_once \
  tests/test_campaign_guardrails.py

# ── 9. The gate ───────────────────────────────────────────────────────────
step "9. the gate"
if PATH="$PWD/.venv/bin:$PATH" bash agent/gate.sh >"$SCRATCH/gate.log" 2>&1; then
  ok "GATE PASS (full log: $SCRATCH/gate.log)"
else
  tail -20 "$SCRATCH/gate.log"
  bad "gate is red"
fi

# ── 10. The mutation run, on a verified-pristine tree ─────────────────────
step "10. mutations"
TREE="$SCRATCH/tree"
rm -rf "$TREE"; mkdir -p "$TREE"
rsync -a --exclude '.venv' --exclude 'node_modules' --exclude '__pycache__' \
      --exclude '.git' --exclude 'data' --exclude 'logs' ./ "$TREE/"
if "$PY" agent/mutate-5m.py "$TREE" "$ABS_PY" 2>&1 | tee "$SCRATCH/mutate.log" \
   | grep -E "PRISTINE|ANCHORS|NOT CAUGHT|survived|did not apply"; then
  ok "no guard reverted without a test noticing (full log: $SCRATCH/mutate.log)"
else
  bad "a mutation survived, or the harness refused to run — see $SCRATCH/mutate.log"
fi

printf '\n'
if [[ $FAIL -eq 0 ]]; then
  echo "ACCEPT 5m: PASS"
else
  echo "ACCEPT 5m: FAILED — see above"
fi
exit $FAIL
