#!/usr/bin/env bash
# PART A ACCEPTANCE — session 5d.
#
# The stop condition for this session. `agent/gate.sh` answers "is this repo
# still sound?"; this answers "did session 5d do what it was sent to do?", which
# is a different question and the one RULES.md requires an agent to demonstrate
# rather than declare. Each check below is one numbered criterion from
# `sessions/session-5d.md` -> "Part A acceptance", in order.
#
#   bash agent/accept-5d.sh                 checks 1-8 and 10-11 (all local)
#   bash agent/accept-5d.sh --with-remote   adds check 9 and the live half of 11
#
# Check 9 needs the deployed site and a login, so it is opt-in and never runs by
# accident:  A4A_URL=https://... A4A_PASSWORD=... bash agent/accept-5d.sh --with-remote
#
# TURN CAP: 60 assistant turns for Part A. On exhausting it, stop and write
# `decisions/NNN-*.open.md` rather than continuing — the same discipline
# MAX_GATE_ATTEMPTS=4 applies to the gate. A session that has spent sixty turns
# and still cannot make this script exit 0 has found something a human needs to
# look at.
#
# Nothing here sends a message. SMS_PROVIDER is forced to console in every
# subprocess, the degraded states are reached through tests/_provider_setup.py
# (which never calls send()), and no live credential is read.
#
# What it DOES do to your machine: builds nothing, but adds and removes a
# detached git worktree at $BASE_REF for checks 3 and 8, and writes a scratch
# SQLite database under $ACCEPT_SCRATCH. It writes nothing else inside the repo.

set -uo pipefail
cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)" || exit 1

WITH_REMOTE=0
for arg in "$@"; do
  case "$arg" in
    --with-remote) WITH_REMOTE=1 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

# The commit session 5d started from. Checks 3 and 8 run against this tree.
BASE_REF="${BASE_REF:-c2cd775}"
SCRATCH="${ACCEPT_SCRATCH:-${TMPDIR:-/tmp}/a4a-accept-5d}"
PY="${PY:-.venv/bin/python}"
[[ -x "$PY" ]] || PY="python3"

mkdir -p "$SCRATCH"

FAIL=0
step() { printf '\n── %s\n' "$1"; }
bad()  { printf 'ACCEPT FAIL: %s\n' "$1"; FAIL=1; }
ok()   { printf '   ok — %s\n' "$1"; }

# The three files session 5d added. Checks 3 and 8 copy them into the pre-fix
# worktree; keeping the list in one place stops the two from drifting apart.
NEW_TESTS=(tests/test_degraded_send_path.py tests/test_blocklist_reasons.py
           tests/test_hook_loop_guard.py)

# ── 1. The gate, twice in a row ─────────────────────────────────────────────
# Twice because this suite shares one database and has been green exactly once
# per database before — the gate points at a scratch DB per run precisely so
# that "green" means "green again tomorrow".
step "1. agent/gate.sh, twice"
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

# ── 2, 4, 5, 6, 7, 10, 11. The behaviour, exercised ─────────────────────────
# These live in the suite, where they are run on every future change rather than
# only when someone remembers this script. Naming them individually is what
# makes the acceptance criteria auditable — a criterion satisfied by "the suite
# is green" is a criterion nobody can find again.
step "2,4,5,6,7,10,11. the behaviour these criteria name"
declare -a CRITERIA=(
  "2|a degraded box refuses to start a campaign, and says so in the composer first|tests/test_degraded_send_path.py::test_a_degraded_campaign_is_refused_and_nothing_is_sent tests/test_degraded_send_path.py::test_the_composer_shows_a_degraded_row_before_the_send tests/test_degraded_send_path.py::test_the_degraded_row_is_first_in_the_checklist"
  "3|a chosen dry run is unchanged|tests/test_degraded_send_path.py::test_a_chosen_dry_run_passes_the_send_path_check tests/test_degraded_send_path.py::test_the_same_campaign_sends_normally_in_a_chosen_dry_run"
  "4|a degraded-state row is excluded from a billing cycle count|tests/test_degraded_send_path.py::test_a_degraded_row_is_excluded_from_a_billing_cycle_count tests/test_degraded_send_path.py::test_the_degraded_status_is_outside_the_billable_set tests/test_degraded_send_path.py::test_the_send_loop_writes_the_degraded_status_rather_than_sent"
  "5|an unrecognised SMS_PROVIDER renders a degraded dashboard, not a 500|tests/test_degraded_send_path.py::test_an_unrecognised_provider_degrades_rather_than_raising tests/test_degraded_send_path.py::test_every_page_still_renders_with_an_unrecognised_provider"
  "6|/health reports degraded, with no carrier name|tests/test_degraded_send_path.py::test_health_reports_degraded_state_without_naming_a_carrier"
  "7|a control character in the hook's stdin no longer disarms the loop guard|tests/test_hook_loop_guard.py"
  "10|delivery-failure webhooks block once, and temporary ones never|tests/test_blocklist_reasons.py"
  "11|opt-outs and unreachable numbers are separate figures|tests/test_blocklist_reasons.py::test_opt_outs_and_unreachable_numbers_are_counted_separately tests/test_blocklist_reasons.py::test_the_opt_outs_page_renders_two_figures tests/test_blocklist_reasons.py::test_the_opt_out_definition_matches_the_dashboard_tile"
)
for entry in "${CRITERIA[@]}"; do
  number="${entry%%|*}"; rest="${entry#*|}"
  label="${rest%%|*}"; targets="${rest#*|}"
  # shellcheck disable=SC2086
  if ! PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "$PY" -m pytest $targets -q \
       >"$SCRATCH/criterion-$number.log" 2>&1; then
    tail -20 "$SCRATCH/criterion-$number.log"
    bad "criterion $number: $label"
  else
    ok "$number. $label"
  fi
done

# ── 3. The console path, byte for byte ──────────────────────────────────────
# A before/after of the same campaign through the console provider, run against
# the pre-fix tree and against this one. The tests above assert the dry run
# still works; this asserts it produces the *same* result it did before session
# 5d touched the send loop, which is the thing the demo flow depends on.
step "3. the same campaign through the console path, before and after"
CONSOLE_PROBE="$SCRATCH/console_probe.py"
cat > "$CONSOLE_PROBE" <<'PY'
"""Run one campaign end to end on the console provider and print what happened.

Deterministic by construction: fixed phones, fixed body, no timestamps in the
output, statuses sorted. Anything that differs between two trees differs because
the send path changed.
"""
import asyncio
import json
import os
import sys

os.environ["DATABASE_URL"] = f"sqlite:///{os.environ['PROBE_DB']}"
os.environ["SMS_PROVIDER"] = "console"          # never live, in either tree
os.environ.setdefault("SECRET_KEY", "accept-5d-scratch")
os.environ.setdefault("ADMIN_PASSWORD", "devpassword123")
os.environ["COOKIE_SECURE"] = "false"

for suffix in ("", "-wal", "-shm"):
    try:
        os.unlink(os.environ["PROBE_DB"] + suffix)
    except FileNotFoundError:
        pass

from alembic import command
from alembic.config import Config

cfg = Config()
cfg.set_main_option("script_location", os.path.join(os.getcwd(), "alembic"))
command.upgrade(cfg, "head")

from app.core.database import SessionLocal
from app.models.sms_message import SMSMessage
from app.services import contact_service
from app.services.campaign_service import CampaignService

db = SessionLocal()
contact_list = contact_service.get_or_create_list(db, "console probe")
for n in range(3):
    contact = contact_service.upsert_contact(
        db, phone=f"+155555507{n:02d}", full_name=f"Probe {n}", source="probe")
    contact_service.add_to_list(db, contact_list.id, contact.id)
db.commit()

service = CampaignService(db)
campaign = service.create_campaign(
    name="console probe",
    message_template="Auctions4America: probe. Reply STOP to opt out.",
    audience=f"list:{contact_list.id}",
    cross_category_override=True,
)
result = asyncio.run(service.send_campaign(campaign.id))

rows = db.query(SMSMessage).filter(SMSMessage.campaign_id == campaign.id).all()
print(json.dumps({
    "status": result.status,
    "sent_count": result.sent_count,
    "failed_count": result.failed_count,
    "skipped_count": result.skipped_count,
    "total_recipients": result.total_recipients,
    "estimated_segments": result.estimated_segments,
    "abort_reason": result.abort_reason,
    "row_statuses": sorted(r.status for r in rows),
    "row_segments": sorted(r.segments or 0 for r in rows),
    "rows_with_sent_at": sum(1 for r in rows if r.sent_at),
    "messages": sorted(r.message for r in rows),
}, indent=2, sort_keys=True))
db.close()
PY

PROBE_TREE="$SCRATCH/base-tree"
git worktree remove --force "$PROBE_TREE" >/dev/null 2>&1
rm -rf "$PROBE_TREE"
if ! git worktree add --detach "$PROBE_TREE" "$BASE_REF" >/dev/null 2>&1; then
  bad "could not check out $BASE_REF to compare against"
else
  ABS_PY="$PY"; [[ "$ABS_PY" == /* ]] || ABS_PY="$PWD/$PY"

  PROBE_DB="$SCRATCH/probe-after.db" PYTHONPATH="$PWD" \
    "$ABS_PY" "$CONSOLE_PROBE" >"$SCRATCH/probe-after.json" 2>"$SCRATCH/probe-after.err"
  AFTER_RC=$?
  PROBE_DB="$SCRATCH/probe-before.db" PYTHONPATH="$PROBE_TREE" \
    sh -c "cd '$PROBE_TREE' && '$ABS_PY' '$CONSOLE_PROBE'" \
    >"$SCRATCH/probe-before.json" 2>"$SCRATCH/probe-before.err"
  BEFORE_RC=$?

  if [[ $AFTER_RC -ne 0 || $BEFORE_RC -ne 0 ]]; then
    tail -15 "$SCRATCH/probe-after.err" "$SCRATCH/probe-before.err"
    bad "the console probe did not complete on both trees"
  elif ! diff -u "$SCRATCH/probe-before.json" "$SCRATCH/probe-after.json"; then
    bad "the chosen dry run behaves differently after 5d — the demo flow changed"
  else
    sed 's/^/   /' "$SCRATCH/probe-after.json"
    ok "identical console-path result at $BASE_REF and here"
  fi
fi

# ── 8. The new tests fail against the pre-fix tree ──────────────────────────
# A regression test that passes on the broken code is decoration. The hook tests
# run against the pre-fix hook too, since the hook is half of what 5d fixed.
step "8. new tests pass here and fail at $BASE_REF"
if ! PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "$PY" -m pytest "${NEW_TESTS[@]}" -q \
     >"$SCRATCH/new-tests-here.log" 2>&1; then
  tail -20 "$SCRATCH/new-tests-here.log"
  bad "the new tests do not pass against the current tree"
else
  ok "new tests pass here: $(grep -E '^[0-9]+ passed' "$SCRATCH/new-tests-here.log" | tail -1)"
fi

if [[ -d "$PROBE_TREE" ]]; then
  cp "${NEW_TESTS[@]}" "$PROBE_TREE/tests/"
  BEFORE_OUT=$(cd "$PROBE_TREE" && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
    "$ABS_PY" -m pytest "${NEW_TESTS[@]}" -q --tb=no 2>&1)
  BEFORE_RC=$?
  if [[ $BEFORE_RC -eq 0 ]]; then
    bad "the new tests pass against $BASE_REF — they do not test the fix"
  else
    printf '%s\n' "$BEFORE_OUT" | tail -4 | sed 's/^/   /'
    ok "the same tests fail against the pre-fix tree"
  fi

  # Two of those three modules fail at *import* against $BASE_REF, because the
  # symbols they test did not exist yet. True, and weak: "the module is new" is
  # not "the bug was real". So the same degraded campaign is run on both trees
  # using only what $BASE_REF already had, and the two outcomes are printed side
  # by side. Before: completed, every row `sent`, segments counted for the
  # cycle. After: refused, nothing written, nothing billable.
  step "8b. the bug itself, before and after"
  DEGRADED_PROBE="$SCRATCH/degraded_probe.py"
  cat > "$DEGRADED_PROBE" <<'PY'
"""Send a campaign on a box whose carrier failed to start. Report what happened.

Uses only what the pre-fix tree already had: tests/_provider_setup.py landed in
session 5c, and nothing here imports a symbol session 5d introduced.

Nothing is sent. degraded_provider() replaces the carrier class with one whose
constructor raises, so the app falls back to the console provider and no carrier
object is ever built.
"""
import asyncio
import json
import os

os.environ["DATABASE_URL"] = f"sqlite:///{os.environ['PROBE_DB']}"
os.environ["SMS_PROVIDER"] = "console"
os.environ.setdefault("SECRET_KEY", "accept-5d-scratch")
os.environ.setdefault("ADMIN_PASSWORD", "devpassword123")
os.environ["COOKIE_SECURE"] = "false"

for suffix in ("", "-wal", "-shm"):
    try:
        os.unlink(os.environ["PROBE_DB"] + suffix)
    except FileNotFoundError:
        pass

from alembic import command
from alembic.config import Config

cfg = Config()
cfg.set_main_option("script_location", os.path.join(os.getcwd(), "alembic"))
command.upgrade(cfg, "head")

from app.core.database import SessionLocal
from app.models.sms_message import SMSMessage, BILLABLE_STATUSES
from app.services import billing_service, contact_service
from app.services.campaign_service import CampaignService
from tests._provider_setup import degraded_provider

db = SessionLocal()
contact_list = contact_service.get_or_create_list(db, "degraded probe")
for n in range(3):
    contact = contact_service.upsert_contact(
        db, phone=f"+155555508{n:02d}", full_name=f"Probe {n}", source="probe")
    contact_service.add_to_list(db, contact_list.id, contact.id)
db.commit()

with degraded_provider():
    service = CampaignService(db)
    campaign = service.create_campaign(
        name="degraded probe",
        message_template="Auctions4America: probe. Reply STOP to opt out.",
        audience=f"list:{contact_list.id}",
        cross_category_override=True,
    )
    result = asyncio.run(service.send_campaign(campaign.id))

rows = db.query(SMSMessage).filter(SMSMessage.campaign_id == campaign.id).all()
cycle_start, cycle_end, _, _ = billing_service.get_billing_cycle()
_, billed_segments = billing_service.compute_usage(db, cycle_start, cycle_end)

print(json.dumps({
    "campaign_status": result.status,
    "sent_count": result.sent_count,
    "row_statuses": sorted(r.status for r in rows),
    "rows_in_the_billable_set": sum(1 for r in rows if r.status in BILLABLE_STATUSES),
    "segments_billed_this_cycle": billed_segments,
    "refused_with": result.abort_reason,
}, indent=2, sort_keys=True))
db.close()
PY

  # Its own worktree: the one above had the new tests copied into it, and this
  # probe must run against $BASE_REF exactly as it shipped. `_provider_setup.py`
  # is not copied over — it landed in 5c, so the pre-fix tree has its own, and
  # using ours would mean the harness under test was not the one that shipped.
  git worktree add --detach "$PROBE_TREE-b" "$BASE_REF" >/dev/null 2>&1

  PROBE_DB="$SCRATCH/degraded-after.db" PYTHONPATH="$PWD" \
    "$ABS_PY" "$DEGRADED_PROBE" >"$SCRATCH/degraded-after.json" 2>"$SCRATCH/degraded-after.err"
  AFTER_RC=$?
  sh -c "cd '$PROBE_TREE-b' && PROBE_DB='$SCRATCH/degraded-before.db' \
         PYTHONPATH='$PROBE_TREE-b' '$ABS_PY' '$DEGRADED_PROBE'" \
    >"$SCRATCH/degraded-before.json" 2>"$SCRATCH/degraded-before.err"
  BEFORE_PROBE_RC=$?

  if [[ $AFTER_RC -ne 0 || $BEFORE_PROBE_RC -ne 0 ]]; then
    tail -12 "$SCRATCH/degraded-before.err" "$SCRATCH/degraded-after.err"
    bad "the degraded probe did not complete on both trees"
  else
    echo "   --- at $BASE_REF (the bug) ---"
    sed 's/^/   /' "$SCRATCH/degraded-before.json"
    echo "   --- here (the fix) ---"
    sed 's/^/   /' "$SCRATCH/degraded-after.json"

    "$PY" - "$SCRATCH/degraded-before.json" "$SCRATCH/degraded-after.json" <<'PY'
import json
import sys

before = json.load(open(sys.argv[1]))
after = json.load(open(sys.argv[2]))
problems = []

# The bug has to actually reproduce, or the "after" proves nothing.
if before["campaign_status"] != "completed":
    problems.append("pre-fix tree did not complete the campaign — the bug did not reproduce")
if before["rows_in_the_billable_set"] == 0:
    problems.append("pre-fix tree billed nothing — the bug did not reproduce")

if after["campaign_status"] != "aborted":
    problems.append(f"campaign is {after['campaign_status']!r}, not aborted")
if after["sent_count"] != 0:
    problems.append(f"{after['sent_count']} messages 'sent' from a degraded box")
if after["rows_in_the_billable_set"] != 0:
    problems.append(f"{after['rows_in_the_billable_set']} billable rows written while degraded")
if after["segments_billed_this_cycle"] != 0:
    problems.append(f"{after['segments_billed_this_cycle']} segments billed while degraded")
if not after["refused_with"]:
    problems.append("refused with no reason recorded on the campaign")

for problem in problems:
    print(f"   -> {problem}")
sys.exit(1 if problems else 0)
PY
    if [[ $? -ne 0 ]]; then
      bad "the degraded campaign does not behave as decision 002 requires"
    else
      ok "before: completed, rows billable. after: refused, nothing written, nothing billed"
    fi
  fi
  git worktree remove --force "$PROBE_TREE-b" >/dev/null 2>&1
  git worktree remove --force "$PROBE_TREE" >/dev/null 2>&1
fi

# ── 9 + live 11. The deployed site (opt-in) ─────────────────────────────────
step "9. deployed site over HTTPS, and 11 against the live blocklist"
if [[ $WITH_REMOTE -eq 0 ]]; then
  echo "   (not run — pass --with-remote with A4A_URL and A4A_PASSWORD set)"
elif [[ -z "${A4A_URL:-}" || -z "${A4A_PASSWORD:-}" ]]; then
  bad "--with-remote needs A4A_URL and A4A_PASSWORD"
else
  JAR="$SCRATCH/cookies.txt"
  rm -f "$JAR"
  LOGIN_CODE=$(curl -s -o /dev/null -w "%{http_code}" -c "$JAR" \
    --data-urlencode "username=${A4A_USER:-admin}" \
    --data-urlencode "password=${A4A_PASSWORD}" "${A4A_URL}/login")
  if [[ "$LOGIN_CODE" != "302" && "$LOGIN_CODE" != "200" ]]; then
    bad "login to ${A4A_URL} returned ${LOGIN_CODE} — the scan below would be vacuous"
  else
    # /login is the seventh screen: unauthenticated, and the only one a stranger
    # can reach. The other six are the nav.
    for path in /login /dashboard /campaigns /contacts /blocklist /usage /settings; do
      BODY=$(curl -s -b "$JAR" -w '\n%{http_code}' "${A4A_URL}${path}")
      CODE=$(printf '%s' "$BODY" | tail -1)
      [[ "$CODE" == "200" ]] || bad "${path} returned ${CODE} over HTTPS"
      if printf '%s' "$BODY" | grep -qiE "telnyx|twilio"; then
        bad "${path} names the carrier in a rendered page"
      fi
    done
    for asset in /static/app.css \
                 /static/fonts/inter-latin-400-normal.woff2 \
                 /static/fonts/inter-latin-500-normal.woff2 \
                 /static/fonts/inter-latin-600-normal.woff2 \
                 /static/fonts/inter-latin-700-normal.woff2; do
      CODE=$(curl -s -o /dev/null -w "%{http_code}" -b "$JAR" "${A4A_URL}${asset}")
      [[ "$CODE" == "200" ]] || bad "${asset} returned ${CODE} — fonts or styles are not loading"
    done

    SYSTEM=$(curl -s -b "$JAR" "${A4A_URL}/api/settings/system")
    if printf '%s' "$SYSTEM" | grep -qiE "telnyx|twilio"; then
      bad "/api/settings/system names the carrier"
    fi
    # The pill must report a WORKING provider. A green run of everything above
    # is equally true of a box that cannot send, which is the failure this
    # session exists to make impossible to mistake for health.
    if ! printf '%s' "$SYSTEM" | grep -q '"send_mode": *"live"'; then
      bad "the live box does not report send_mode=live: $(printf '%s' "$SYSTEM" | head -c 200)"
    else
      ok "the live box reports a working provider"
    fi

    HEALTH=$(curl -s "${A4A_URL}/health")
    printf '%s' "$HEALTH" | grep -q '"sending_ok": *true' \
      || bad "/health does not report sending_ok=true: $HEALTH"
    if printf '%s' "$HEALTH" | grep -qiE "telnyx|twilio"; then
      bad "/health names the carrier"
    fi

    # Criterion 11 against the real backfilled rows.
    COUNTS=$(curl -s -b "$JAR" "${A4A_URL}/api/blocklist" \
      | "$PY" -c 'import json,sys; print(json.dumps(json.load(sys.stdin).get("counts")))' 2>/dev/null)
    if [[ -z "$COUNTS" || "$COUNTS" == "null" ]]; then
      bad "the live /api/blocklist returns no split counts"
    else
      echo "   live blocklist counts: $COUNTS"
      ok "opt-outs and unreachable numbers reported separately on the live box"
    fi

    rm -f "$JAR"
    [[ $FAIL -eq 0 ]] && ok "seven screens, styles, all four font weights, no carrier name"
  fi
fi

printf '\n'
if [[ $FAIL -eq 0 ]]; then
  echo "PART A ACCEPTANCE PASS"
else
  echo "PART A ACCEPTANCE FAILED — see above"
fi
exit $FAIL
