#!/usr/bin/env bash
# ACCEPTANCE — session LT1, lighting buyers, national, for the 9/29 sale.
#
# Criteria 1-11 from `sessions/session-LT1.md` -> "Acceptance", in order. Each
# criterion's tests run in their own pytest process (accept-5e's rule: a test
# that needs its neighbours proves nothing about the criterion it is named for).
#
# NOTHING HERE CALLS A PAID API. The Places client and the line-type provider
# are fakes inside the tests. The CLI runs below point at a scratch database
# under $SCRATCH with GOOGLE_PLACES_API_KEY blank and PROSPECT_LOOKUP_PROVIDER
# none, so `--dry-run` reads empty meters and `--live` refuses before it spends.
#
#   bash agent/accept-LT1.sh

set -uo pipefail
cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)" || exit 1
export PATH="$PWD/.venv/bin:$PATH"          # decisions/001: the venv, not whatever is first
SCRATCH="${ACCEPT_SCRATCH:-$(mktemp -d -t accept-LT1)}"
FAILED=0

pass() { echo "  PASS  $1"; }
fail() { echo "  FAIL  $1"; FAILED=1; }
head_() { echo; echo "== $1"; }
t() {   # t "label" pytest-args...
  local label="$1"; shift
  if python -m pytest -q -p no:cacheprovider "$@" >"$SCRATCH/pytest.out" 2>&1; then
    pass "$label ($(tail -1 "$SCRATCH/pytest.out" | sed 's/=//g' | xargs))"
  else
    fail "$label"; tail -25 "$SCRATCH/pytest.out"
  fi
}

LT=tests/test_lighting_taxonomy.py
PR=tests/test_prospect_run.py

head_ "1. retail + trade = 8 terms x 50 metros = 400 searches, priority order, metro-centred"
t "criterion 1" "$LT::test_retail_and_trade_plan_400_metro_centred_searches_in_priority_order"
python - <<'PY'
from app.sources import taxonomy
plan = list(taxonomy.searches(group_slugs=["lighting_retail", "lighting_trade"]))
print(f"        searches={len(plan)} terms={len({s.term for s in plan})} "
      f"centred={sum(1 for s in plan if s.sweep.center)} first={plan[0].ledger_key}")
PY

head_ "2. lighting_decor does not appear unless named"
t "criterion 2" "$LT" -k "lighting_decor"

head_ "3. non-empty buyer_rationale and radius_for_group() is None for all three"
t "criterion 3" "$LT" -k "rationale_and_is_national"

head_ "4. METRO_SWEEPS: 50 entries, unique names, all inside CONUS/AK/HI"
t "criterion 4" "$LT" -k "fifty_metros or bounding_box or shares_a_sweep_name"

head_ "5. same place from two metros -> one prospect (two sightings when the terms differ)"
t "criterion 5" "$PR" -k "same_store"
echo "        NOTE: on ONE term, two metros give one prospect and ONE sighting —"
echo "        sightings are unique on (prospect, source, term). See status.md."

head_ "6. chains rejected; Capitol / Lighting Showcase / ABC Distributors accepted; 009 guard"
t "criterion 6" "$LT" -k "chain or independent"
t "criterion 6 (existing guard)" \
  "tests/test_taxonomy.py::test_no_exclusion_phrase_names_a_wholesaler_importer_or_distributor"

# A scratch database at head, for the CLI runs below.
export DATABASE_URL="sqlite:///$SCRATCH/lt1.db"
alembic upgrade head >"$SCRATCH/alembic-cli.out" 2>&1 || { fail "scratch db"; tail "$SCRATCH/alembic-cli.out"; }

head_ "7. --dry-run prints every figure, zero network calls"
t "criterion 7 (network patched to fail)" "$PR" -k "dry_run or network_guard"
GOOGLE_PLACES_API_KEY= PROSPECT_LOOKUP_PROVIDER=none \
  python -m scripts.prospect_run --groups lighting_retail,lighting_trade --dry-run \
  >"$SCRATCH/dry.out" 2>&1
DRY=$?
sed 's/^/        /' "$SCRATCH/dry.out"
missing=0
for label in "Searches planned" "Worst-case Places requests" \
             "Places requests already used this month" \
             "GOOGLE_PLACES_MONTHLY_REQUEST_CAP" \
             "Worst-case Places cost beyond the free allowance" \
             "PROSPECT_LOOKUP_MONTHLY_CAP" "spent this month"; do
  grep -q "$label" "$SCRATCH/dry.out" || { echo "        missing: $label"; missing=1; }
done
[ "$DRY" -eq 0 ] && [ "$missing" -eq 0 ] && pass "criterion 7 (CLI, every figure)" \
  || fail "criterion 7 (CLI exit $DRY)"

head_ "8. --live with no key refuses in one line, exit != 0"
t "criterion 8" "$PR" -k "live_without_a_key or screening_off or no_lookup_room"
GOOGLE_PLACES_API_KEY= PROSPECT_LOOKUP_PROVIDER=none \
  python -m scripts.prospect_run --groups lighting_retail,lighting_trade --live \
  >"$SCRATCH/live.out" 2>&1
LIVE=$?
sed 's/^/        /' "$SCRATCH/live.out"
LINES=$(grep -c . "$SCRATCH/live.out")
[ "$LIVE" -ne 0 ] && [ "$LINES" -eq 1 ] && grep -q GOOGLE_PLACES_API_KEY "$SCRATCH/live.out" \
  && pass "criterion 8 (CLI exit $LIVE, $LINES line)" || fail "criterion 8 (CLI exit $LIVE, $LINES lines)"

head_ "9. --live end to end on fakes: queued, breakdown printed, promotable = mobile + voip"
t "criterion 9" "$PR" -k "live_queues"

head_ "10. white-label: /prospects, /api/prospects and the script's output"
t "criterion 10" "$PR" -k "carrier or scrubbing or scrubbed_line or interrupt"
# Known answer first: the pattern has to fire on the glued spelling.
echo "TelnyxError" | grep -qiE "telnyx|twilio" || fail "criterion 10 self-check"
if cat "$SCRATCH/dry.out" "$SCRATCH/live.out" | grep -qiE "telnyx|twilio"; then
  fail "criterion 10 (CLI output names a carrier)"
else
  pass "criterion 10 (CLI output clean)"
fi

head_ "11. full suite; alembic from clean; gate green twice"
if python -m pytest tests/ -q -p no:cacheprovider >"$SCRATCH/full.out" 2>&1; then
  pass "pytest tests/ -q ($(tail -1 "$SCRATCH/full.out" | sed 's/=//g' | xargs))"
else
  fail "pytest tests/ -q"; tail -25 "$SCRATCH/full.out"
fi
rm -f "$SCRATCH/clean.db"
if DATABASE_URL="sqlite:///$SCRATCH/clean.db" alembic upgrade head >"$SCRATCH/alembic.out" 2>&1; then
  pass "alembic upgrade head from clean ($(DATABASE_URL="sqlite:///$SCRATCH/clean.db" alembic current 2>/dev/null | tail -1))"
else
  fail "alembic upgrade head"; tail "$SCRATCH/alembic.out"
fi
unset DATABASE_URL
for run in 1 2; do
  if bash agent/gate.sh >"$SCRATCH/gate$run.out" 2>&1; then
    pass "gate run $run"; tail -4 "$SCRATCH/gate$run.out" | sed 's/^/        /'
  else
    fail "gate run $run"; tail -30 "$SCRATCH/gate$run.out"
  fi
done

echo
if [ "$FAILED" -eq 0 ]; then echo "ACCEPT-LT1: ALL CRITERIA PASS"; else echo "ACCEPT-LT1: FAILED"; fi
exit "$FAILED"
