#!/usr/bin/env bash
# PART A ACCEPTANCE — session P1b, the lookup provider and a gate that flakes.
#
# The stop condition for this session. `agent/gate.sh` answers "is this repo
# still sound?"; this answers "did session P1b do what it was sent to do?",
# which is a different question and the one RULES.md requires an agent to
# demonstrate rather than declare. Checks 1-10 are the numbered criteria from
# `sessions/session-P1b.md` -> "Part A acceptance", in order.
#
#   bash agent/accept-P1b.sh                 checks 1-9 (all local)
#   bash agent/accept-P1b.sh --with-remote   adds check 10, against the deployed box
#
# Check 10 needs the deploy and a login, so it is opt-in and never runs by
# accident:  A4A_URL=https://... A4A_PASSWORD=... bash agent/accept-P1b.sh --with-remote
#
# TURN CAP: 60 assistant turns for Part A. On exhausting it, stop and write
# `decisions/NNN-*.open.md` rather than continuing — the same discipline
# MAX_GATE_ATTEMPTS=4 applies to the gate. A session that has spent sixty turns
# and still cannot make this script exit 0 has found something a human needs to
# look at.
#
# Nothing here sends a message and NOTHING HERE CALLS A PAID API. That is the
# constraint this session could most easily break, so it is asserted rather than
# promised: SMS_PROVIDER is console in every subprocess, PROSPECT_LOOKUP_PROVIDER
# is never changed from its default ("none", which makes no call at all), the
# carrier lookup provider is only ever built with a visibly fake key and has its
# client replaced before use, and every screening test passes its own counting
# fake in explicitly. No contact data is imported, modified or deleted outside
# the suite's own scratch database.
#
# What it DOES do to your machine: writes scratch files under $ACCEPT_SCRATCH,
# and for check 9 rsyncs a throwaway copy of the tree there. It writes nothing
# inside the repo, and it never touches the live box unless you pass
# --with-remote.

set -uo pipefail
cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)" || exit 1

WITH_REMOTE=0
for arg in "$@"; do
  case "$arg" in
    --with-remote) WITH_REMOTE=1 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

SCRATCH="${ACCEPT_SCRATCH:-${TMPDIR:-/tmp}/a4a-accept-P1b}"
PY="${PY:-.venv/bin/python}"
[[ -x "$PY" ]] || PY="python3"
ABS_PY="$PY"; [[ "$ABS_PY" == /* ]] || ABS_PY="$PWD/$PY"

mkdir -p "$SCRATCH"

FAIL=0
step() { printf '\n── %s\n' "$1"; }
bad()  { printf 'ACCEPT FAIL: %s\n' "$1"; FAIL=1; }
ok()   { printf '   ok — %s\n' "$1"; }

# ── 1. The gate, twice in a row ─────────────────────────────────────────────
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

# ── 2-6. The behaviour, exercised, one criterion at a time ──────────────────
# Each criterion's tests run in their **own** pytest process. 5e's review found
# two tests that only passed inside a full run — one leaning on contacts another
# module had seeded, one reading a list an earlier test in its own file filled,
# the latter passing in isolation with two assertions comparing 0 == 0. A test
# that needs its neighbours proves nothing about the criterion it is named for.
step "2-6. the behaviour each criterion names"
declare -a CRITERIA=(
  "2|the lookup provider returns a line type against a recorded fixture, and the default still makes no call|tests/test_lookup_provider.py::test_the_sdk_still_exposes_the_call_this_provider_drives tests/test_lookup_provider.py::test_every_recorded_response_still_parses_as_the_sdk_declares_it tests/test_lookup_provider.py::test_the_provider_returns_a_line_type_from_a_recorded_response tests/test_lookup_provider.py::test_every_line_type_the_sdk_declares_is_mapped_and_the_rest_are_unknown tests/test_lookup_provider.py::test_the_default_provider_is_still_the_one_that_makes_no_call tests/test_lookup_provider.py::test_the_carrier_provider_is_reachable_once_it_is_configured tests/test_lookup_provider.py::test_a_misconfigured_screening_provider_degrades_instead_of_raising"
  "3|the spend cap refuses BEFORE calling, logs what was skipped, and does not drain past the ceiling|tests/test_lookup_provider.py::test_the_cap_refuses_before_the_call_and_logs_what_went_unscreened tests/test_lookup_provider.py::test_the_single_number_path_enforces_the_cap_too tests/test_lookup_provider.py::test_a_cap_of_zero_switches_screening_off_rather_than_making_it_unlimited tests/test_lookup_provider.py::test_the_budget_counts_what_earlier_runs_already_spent tests/test_lookup_provider.py::test_a_second_billed_attempt_in_the_same_month_is_counted tests/test_lookup_provider.py::test_last_months_spend_does_not_count_against_this_month tests/test_lookup_provider.py::test_the_month_the_cap_counts_is_the_month_the_writer_stamps"
  "4|a repeat lookup hits the cache and makes NO call — asserted on call count|tests/test_prospect_pipeline.py::test_a_repeat_lookup_hits_the_cache_and_makes_no_call tests/test_prospect_pipeline.py::test_the_single_number_path_reads_the_cache_too tests/test_prospect_pipeline.py::test_a_partly_cached_batch_pays_only_for_the_misses tests/test_lookup_provider.py::test_a_number_whose_line_type_is_already_known_is_never_looked_up_again"
  "5|rejected, blocklisted and already-known numbers are never looked up|tests/test_lookup_provider.py::test_a_rejected_number_is_never_looked_up_by_either_entry_point tests/test_lookup_provider.py::test_a_blocklisted_number_is_never_looked_up_by_either_entry_point tests/test_lookup_provider.py::test_the_unusable_set_agrees_with_the_two_services_that_own_the_rules tests/test_prospect_pipeline.py::test_a_rejected_number_is_never_paid_to_screen"
  "6|no wholesale figure reaches a client-facing page, response or export|tests/test_prospect_review.py::test_no_prospect_surface_leaks_a_payload_a_carrier_or_our_cost tests/test_prospect_review.py::test_the_carriers_name_on_a_lookup_row_reaches_no_prospect_surface tests/test_campaign_reports.py::test_no_new_surface_leaks_the_carrier_or_our_cost tests/test_whitelabel.py::test_no_response_quotes_our_wholesale_rate tests/test_campaign_preflight.py::test_preflight_response_never_quotes_our_wholesale_rate"
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

# ── 3b. The cap refusing, shown rather than claimed ─────────────────────────
# accept-5g check 3's shape: a passing test named "the cap refuses" would also
# pass on a cap that refused for the wrong reason, or after the money was gone.
# This prints the call count, the ledger and the log line the operator gets.
step "3b. the spend cap, refusing, with the numbers"
"$PY" - <<'PY'
import logging, sys
sys.path.insert(0, ".")
import tempfile, os
_fd, _db = tempfile.mkstemp(prefix="accept-P1b-", suffix=".db"); os.close(_fd); os.unlink(_db)
os.environ["DATABASE_URL"] = f"sqlite:///{_db}"
os.environ["SMS_PROVIDER"] = "console"
os.environ.setdefault("SECRET_KEY", "accept-secret")
os.environ.setdefault("ADMIN_PASSWORD", "devpassword123")

from alembic import command
from alembic.config import Config
# No alembic.ini, for conftest.py's reason: it sets config_file_name, env.py
# then calls fileConfig(), and every existing logger is disabled — including
# the one this check reads the refusal off.
cfg = Config(); cfg.set_main_option("script_location", "alembic")
command.upgrade(cfg, "head")

from app.core.config import settings
from app.core.database import SessionLocal
from app.services import lookup_service
from tests._prospect_setup import CountingLookupProvider

records = []
class _Capture(logging.Handler):
    def emit(self, record): records.append(record.getMessage())
logging.getLogger("lookup").addHandler(_Capture())
logging.getLogger("lookup").setLevel(logging.ERROR)

db = SessionLocal()
phones = [f"+1555555{n:04d}" for n in range(9000, 9010)]
provider = CountingLookupProvider()

# Room for exactly three of the ten.
settings.PROSPECT_LOOKUP_MONTHLY_CAP = float(lookup_service.cost_per_lookup() * 3)
outcome = lookup_service.screen(db, phones, provider=provider)
spent = lookup_service.spend_this_month(db)
cap = lookup_service.monthly_cap()

print(f"     cap ${cap} | 10 numbers offered")
print(f"     calls made:      {len(provider.calls)}")
print(f"     performed:       {outcome['performed']}   skipped: {outcome['skipped']}")
print(f"     spent this month ${spent}   (ceiling ${cap})")
for line in records:
    print(f"     LOG: {line}")

assert len(provider.calls) == 3, f"{len(provider.calls)} calls under a 3-call cap"
assert spent <= cap, f"drained ${spent} past a ${cap} ceiling"
assert outcome["skipped"]["spend_cap"] == 7
assert any("went unscreened" in line for line in records), "the skip was silent"
assert not any("wholesale" in line.lower() for line in records)

# And a second pass makes no call at all: the ceiling is monthly, not per run.
before = len(provider.calls)
lookup_service.screen(db, [f"+1555555{n:04d}" for n in range(9010, 9015)],
                      provider=provider)
assert len(provider.calls) == before, "a second pass started spending again"
print(f"     second pass:     {len(provider.calls) - before} calls")
db.close(); os.unlink(_db)
PY
if [[ $? -ne 0 ]]; then
  bad "the spend cap did not refuse cleanly before spending"
else
  ok "the cap refused before the call, logged the skip, and stopped at the ceiling"
fi

# ── 7. The rewritten assertion, both directions ─────────────────────────────
step "7a. the white-label assertion, 20 consecutive runs"
RUNS_FAILED=0
for run in $(seq 1 20); do
  if ! PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "$PY" -m pytest \
       "tests/test_campaign_reports.py::test_no_new_surface_leaks_the_carrier_or_our_cost" \
       "tests/test_whitelabel.py::test_no_response_quotes_our_wholesale_rate" \
       -q >"$SCRATCH/flake-$run.log" 2>&1; then
    RUNS_FAILED=$((RUNS_FAILED + 1))
    tail -15 "$SCRATCH/flake-$run.log"
  fi
done
if [[ $RUNS_FAILED -ne 0 ]]; then
  bad "the white-label assertion failed $RUNS_FAILED of 20 runs"
else
  ok "20/20 clean"
fi

# 20 samples is weak evidence about a one-in-several-hundred event, so the
# determinism is proved exhaustively as well: every timestamp that *contains*
# the old assertion's substring, and none of them is a wholesale figure.
step "7b. every spelling of the timestamp that broke it, and the injection that must fail"
"$PY" - <<'PY'
import sys
sys.path.insert(0, ".")
from decimal import Decimal
from tests import _wholesale_scan as scan

rate = "0.009"
checked = 0
for second in range(0, 60, 10):          # the seconds that end in a zero
    for micro in range(0, 1000):         # every microsecond starting 009
        stamp = f"2026-08-31T14:23:{second:02d}.009{micro:03d}"
        assert rate in stamp, stamp      # precondition: the old test would see it
        scan.assert_no_wholesale_figure(stamp, where=stamp)
        checked += 1
print(f"     {checked} timestamps containing {rate!r}: none read as a price")

# The other direction. A scanner that never fires is not a scanner.
for label, figure in scan.wholesale_figures().items():
    for body in (f"<td>${figure} / segment</td>", f'{{"rate": {figure}}}'):
        try:
            scan.assert_no_wholesale_figure(body, where=label)
        except AssertionError:
            pass
        else:
            raise SystemExit(f"the scan missed {figure} in {body!r} ({label})")
print(f"     every figure the scan knows about is caught in both a page and a payload")
PY
if [[ $? -ne 0 ]]; then
  bad "the rewritten assertion is not both quiet on timestamps and loud on figures"
else
  ok "quiet on every timestamp spelling, loud on every wholesale figure"
fi

# ── 8. The bare-numeric audit ───────────────────────────────────────────────
# The defect class, not the instance: a figure of ours tested as a *substring*
# against a whole body or a whole sentence. Three of these have each cost a
# session. This is the check that says the fourth is gone and names anything
# left.
step "8. no bare-numeric substring assertion of our own costs remains"
# Anchored to an `assert` statement, and `--include='*.py'`. The first version
# of this check flagged a *comment* quoting the old assertion and three
# `__pycache__` binaries — the fourth time in this repo that a measurement
# script has counted prose describing a rule as a violation of it. A failing
# check is a hypothesis, not a verdict.
LEFTOVERS=$(grep -rn --include='*.py' -E \
            "^[[:space:]]*assert.*(str\(settings\.[A-Z_]*COST[A-Z_]*\)|\"0\.0043\"|'0\.0043').*not in" \
            tests/ 2>/dev/null | grep -v "_wholesale_scan.py" || true)
if [[ -n "$LEFTOVERS" ]]; then
  printf '%s\n' "$LEFTOVERS"
  bad "a bare-numeric substring assertion is still in the suite"
else
  ok "none remain — every site now compares parsed numbers (see status.md for the audit)"
fi
CONVERTED=$(grep -rln --include='*.py' "_wholesale_scan" tests/ | grep -v "_wholesale_scan.py" | sort)
echo "   sites now using the parsed-number scan:"
printf '     %s\n' $CONVERTED

# ── 9. Each guard reverted on its own, on a verified-pristine tree ──────────
step "9. every P1b guard reverted one at a time; each must break a test"
MUTATION_TREE="$SCRATCH/mutation-tree"
rm -rf "$MUTATION_TREE"
mkdir -p "$MUTATION_TREE"
if ! rsync -a --exclude .venv --exclude node_modules --exclude .git --exclude data \
      --exclude '__pycache__' --exclude '*.db' --exclude .pytest_cache \
      ./ "$MUTATION_TREE/" 2>/dev/null; then
  bad "could not stage a scratch tree for the mutation check"
elif ! "$ABS_PY" agent/mutate-P1b.py "$MUTATION_TREE" "$ABS_PY" >"$SCRATCH/mutations.log" 2>&1; then
  sed 's/^/   /' "$SCRATCH/mutations.log"
  bad "a guard was reverted and no test noticed"
else
  grep -E '^(SCRATCH VERIFIED|[CUMPS][0-9]|  CAUGHT|[0-9]+ mutations)' \
    "$SCRATCH/mutations.log" | sed 's/^/   /'
  ok "every reverted guard breaks at least one test that names it"
fi
rm -rf "$MUTATION_TREE"

# ── 10. The deployed box (opt-in) ───────────────────────────────────────────
step "10. deployed site over HTTPS: every screen 200, no carrier name, no wholesale figure"
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
  if [[ "$LOGIN_CODE" != "200" && "$LOGIN_CODE" != "302" ]]; then
    bad "could not log in to ${A4A_URL} (HTTP $LOGIN_CODE)"
  else
    for path in / /campaigns /contacts /prospects /history /blocklist /usage /settings /health; do
      CODE=$(curl -s -o "$SCRATCH/page.html" -w "%{http_code}" -b "$JAR" "${A4A_URL}${path}")
      if [[ "$CODE" != "200" ]]; then
        bad "${path} returned $CODE"
        continue
      fi
      if grep -qiE 'telnyx|twilio' "$SCRATCH/page.html"; then
        bad "${path} names the carrier"
      elif grep -qE "\{'errors'|\[\{'code'" "$SCRATCH/page.html"; then
        bad "${path} renders a raw provider payload"
      elif ! "$PY" - "$SCRATCH/page.html" "$path" <<'PY'
import sys
sys.path.insert(0, ".")
from tests import _wholesale_scan as scan
scan.assert_no_wholesale_figure(open(sys.argv[1]).read(), where=sys.argv[2])
PY
      then
        bad "${path} renders one of our own costs"
      else
        ok "${path} 200, no carrier name, no payload, no wholesale figure"
      fi
    done
    for path in /api/prospects /api/prospects/summary /api/prospects/terms \
                /api/prospects/export.csv; do
      curl -s -b "$JAR" "${A4A_URL}${path}" >"$SCRATCH/api.txt"
      if grep -qiE 'telnyx|twilio|raw_payload|place_id|wholesale|lookups_performed' "$SCRATCH/api.txt"; then
        bad "${path} names a carrier, our spend, or the raw payload"
      elif ! "$PY" - "$SCRATCH/api.txt" "$path" <<'PY'
import sys
sys.path.insert(0, ".")
from tests import _wholesale_scan as scan
scan.assert_no_wholesale_figure(open(sys.argv[1]).read(), where=sys.argv[2])
PY
      then
        bad "${path} carries one of our own costs"
      else
        ok "${path} is clean"
      fi
    done
  fi
fi

printf '\n'
if [[ $FAIL -eq 0 ]]; then
  echo "ACCEPT PASS — session P1b Part A"
  [[ $WITH_REMOTE -eq 0 ]] && echo "  (criterion 10 not run; it needs the deploy)"
else
  echo "ACCEPT FAILED — see above"
fi
exit $FAIL
