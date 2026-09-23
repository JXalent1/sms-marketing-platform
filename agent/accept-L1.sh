#!/usr/bin/env bash
# PART A ACCEPTANCE — session L1, the LiveAuctioneers bidder source.
#
# Checks 1-11 are the numbered criteria from `sessions/session-L1.md` ->
# "Acceptance", in order; lettered checks show the thing the numbered check
# asserts, so a reader sees the figure rather than a tick.
#
#   bash agent/accept-L1.sh
#
# ── Nothing here reaches LiveAuctioneers ────────────────────────────────────
#
# The suite drives a fake Playwright (`tests/_la_portal.py`). Check 8b drives a
# REAL headless Chromium, but every request it makes is answered by a route the
# probe installs — the portal URL is fulfilled from the fixture and anything
# else is aborted — and the probe fails if any request escaped that route. No
# credential is read: the probe blanks LA_* before importing the app.
#
# What it DOES do to your machine: writes scratch files under $ACCEPT_SCRATCH
# and rsyncs throwaway copies of the tree there. It writes nothing in the repo.

set -uo pipefail
cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)" || exit 1

SCRATCH="${ACCEPT_SCRATCH:-${TMPDIR:-/tmp}/a4a-accept-L1}"
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
  local out
  out=$(SMS_PROVIDER=console PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
        "$PY" -m pytest "$@" -q --no-header -p no:cacheprovider 2>&1)
  local status=$?
  printf '%s\n' "$out" | grep -E "passed|failed|error" | tail -1
  if [[ $status -eq 0 ]]; then ok "$label"; else bad "$label"; fi
}

SRC=tests/test_bidder_source.py
RUNS=tests/test_bidder_scrape_runs.py

fresh_tree() {   # fresh_tree <dir>
  rm -rf "$1"; mkdir -p "$1"
  rsync -a --exclude '.venv' --exclude 'node_modules' --exclude '__pycache__' \
        --exclude '.git' --exclude 'data' --exclude 'logs' ./ "$1/"
}

step "0. the decision reversing 18 Aug is on file before anything built on it"
if [[ -f decisions/014-reading-his-own-bidders-out-of-his-own-account.md ]]; then
  ok "decisions/014 records the reversal and what does not change"
else
  bad "decisions/014 is missing"
fi

step "1. no network call in the suite — Playwright is replaced, every page a fixture"
run_tests "a whole scrape with socket.connect refused, guard proved on a real socket" \
  "$SRC::test_a_whole_scrape_runs_with_the_network_refused"
# An import statement, not the word: the fake's docstring names what it
# replaces, and prose describing a rule is not a violation of it.
if grep -rnE "^\s*(from|import)\s+playwright" tests/test_bidder_*.py tests/_la_portal.py \
     tests/_bidder_setup.py; then
  bad "a bidder test module imports Playwright"
else
  ok "no bidder test module imports Playwright"
fi

step "2. N bidders make N contacts; a second identical run makes 0 and no duplicate"
run_tests "the fixture's distinct numbers, twice over, one row each" \
  "$SRC::test_n_bidders_make_n_contacts_and_a_second_run_makes_none" \
  "$SRC::test_every_row_is_accounted_for"

step "3. app/sources writes nothing to the database — structurally"
run_tests "AST scan of every registered scraper module plus base and registry, and the scan's own known answer" \
  "$SRC::test_the_scan_catches_the_reference_save_profile" \
  "$SRC::test_the_bidder_source_writes_nothing_to_the_database"

step "4. behaviour in its own table with real types; a profile-less contact works"
run_tests "column types, NULL-not-zero, and seven screens for a bare contact" \
  "$SRC::test_no_behavioural_field_is_a_contact_column" \
  "$SRC::test_the_profile_columns_are_real_types" \
  "$SRC::test_the_figures_land_typed_and_absence_is_null_not_zero" \
  "$SRC::test_a_contact_with_no_profile_still_works_everywhere"

step "5. a blocked bidder stays blocked after registering again"
run_tests "not renamed, not re-stamped, not listed, no profile, still blocked" \
  "$RUNS::test_a_blocked_bidder_who_registers_again_stays_blocked" \
  "$RUNS::test_a_blocked_bidder_who_was_never_a_contact_does_not_become_one"

step "6. a scrape already running refuses a second"
for t in test_a_running_scrape_refuses_a_second_rather_than_queueing \
         test_the_lock_refuses_in_the_window_the_row_check_cannot_see \
         test_a_running_row_from_another_process_refuses_and_a_dead_one_does_not; do
  run_tests "$t (alone)" "$RUNS::$t"
done

step "7. the browser closes on success, failure and timeout — each test alone"
for t in test_the_browser_closes_after_a_successful_run \
         test_the_browser_closes_when_the_run_fails \
         test_the_deadline_kills_the_browser_not_just_the_job \
         test_a_teardown_that_fails_is_recorded_not_assumed \
         test_a_teardown_that_never_ran_is_recorded_not_assumed; do
  run_tests "$t (alone)" "$RUNS::$t"
done

step "7b. the timeout test against a deliberately broken teardown goes red, by its own assertion"
BROKEN="$SCRATCH/broken-teardown"
fresh_tree "$BROKEN"
"$PY" - "$BROKEN/app/sources/auction_scraper_base.py" <<'EOF'
import sys
p = sys.argv[1]; s = open(p).read()
old = ("        finally:\n"
       "            # Every way out, including cancellation by `run()`'s deadline.\n"
       "            await self.shutdown()\n")
assert s.count(old) == 1, "anchor moved"
s = s.replace("            return self.records\n", "            await self.shutdown()\n"
              "            return self.records\n", 1).replace(old, "")
s = s.replace("            await self._debug_screenshot(\"scrape-fail\")\n            raise\n",
              "            await self._debug_screenshot(\"scrape-fail\")\n"
              "            await self.shutdown()\n            raise\n", 1)
open(p, "w").write(s)
EOF
OUT=$(cd "$BROKEN" && SMS_PROVIDER=console PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "$ABS_PY" -m pytest \
      "$RUNS::test_the_deadline_kills_the_browser_not_just_the_job" -q --no-header \
      -p no:cacheprovider 2>&1)
if printf '%s' "$OUT" | grep -q "^>       assert portal.closes == 1" \
   && printf '%s' "$OUT" | grep -qE "where 0 = .*\.closes"; then
  ok "red on the tree it exists to reject, at 'assert portal.closes == 1' (0 closes)"
else
  printf '%s\n' "$OUT" | tail -15
  bad "the deadline test did not fail for the reason it is named for"
fi
OUT=$(cd "$BROKEN" && SMS_PROVIDER=console PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "$ABS_PY" -m pytest \
      "$RUNS::test_the_browser_closes_after_a_successful_run" -q --no-header -p no:cacheprovider 2>&1)
if printf '%s' "$OUT" | grep -q "1 passed"; then
  ok "and the success path is still green there: only the cancellation path leaks"
else
  bad "the broken-teardown tree broke more than the path it was meant to"
fi

step "8. peak RSS from a fixture run is recorded in status.md"
if grep -q "Peak RSS, real Chromium, fixture run" status.md; then
  grep -A8 "Peak RSS, real Chromium, fixture run" status.md | sed 's/^/   /'
  ok "recorded"
else
  bad "status.md has no peak-RSS record"
fi

step "8b. measured again, now: real headless Chromium, the unchanged source, the fixture"
if "$PY" -c "import playwright" 2>/dev/null; then
  if "$PY" -m tests._la_rss_probe >"$SCRATCH/rss.json" 2>"$SCRATCH/rss.log"; then
    grep -E '"(status|contacts_created|cleanup_ran|seconds|baseline_mb|peak_tree_mb|peak_browser_mb|peak_processes|processes_after|fulfilled|aborted|requests)"' \
      "$SCRATCH/rss.json" | sed 's/^/   /'
    ok "completed, browser closed, process tree back to one, no request escaped the route"
  else
    cat "$SCRATCH/rss.json" "$SCRATCH/rss.log" | tail -20
    bad "the real-browser fixture run did not complete cleanly"
  fi
else
  bad "playwright is not installed in $PY — pip install -r requirements.txt"
fi

step "9. the schedule reads the app's timezone setting, not its own"
run_tests "three zones through trigger(), and the registration in main.py under Denver" \
  "$RUNS::test_the_trigger_follows_the_apps_zone_whatever_it_is" \
  "$RUNS::test_the_daily_job_registers_in_the_apps_zone_and_one_at_a_time" \
  "$RUNS::test_blank_credentials_register_nothing"

step "10. the gate, twice"
for n in 1 2; do
  if PATH="$PWD/.venv/bin:$PATH" bash agent/gate.sh >"$SCRATCH/gate-$n.log" 2>&1; then
    ok "gate run $n: $(grep -E '[0-9]+ passed' "$SCRATCH/gate-$n.log" | tail -1) — GATE PASS"
  else
    tail -20 "$SCRATCH/gate-$n.log"
    bad "gate run $n is red"
  fi
done

step "11. mutations, on a verified-pristine tree, twice — the verdicts must match"
for n in 1 2; do
  TREE="$SCRATCH/tree-$n"
  fresh_tree "$TREE"
  "$PY" agent/mutate-L1.py "$TREE" "$ABS_PY" >"$SCRATCH/mutate-$n.log" 2>&1
  echo "   run $n (exit $?):"
  grep -E "PRISTINE|ANCHORS|NOT CAUGHT|mutations," "$SCRATCH/mutate-$n.log" | sed 's/^/     /'
  grep -E "^[MS][0-9]+[a-z]? |^  (CAUGHT|\*\*\*)" "$SCRATCH/mutate-$n.log" \
    | paste - - | awk -F'\t' '{split($1,a," "); split($2,b,"("); print a[1], b[1]}' \
    > "$SCRATCH/verdicts-$n.txt"
done
if cmp -s "$SCRATCH/verdicts-1.txt" "$SCRATCH/verdicts-2.txt" \
   && ! grep -q "NOT CAUGHT" "$SCRATCH/verdicts-1.txt" \
   && [[ $(wc -l <"$SCRATCH/verdicts-1.txt") -ge 27 ]]; then
  ok "identical per-mutation verdicts on both runs, none survived (logs: $SCRATCH/mutate-{1,2}.log)"
else
  diff "$SCRATCH/verdicts-1.txt" "$SCRATCH/verdicts-2.txt"
  bad "the two mutation runs disagree, a mutation survived, or the harness refused"
fi

printf '\n'
if [[ $FAIL -eq 0 ]]; then echo "ACCEPT L1: PASS"; else echo "ACCEPT L1: FAILED — see above"; fi
exit $FAIL
