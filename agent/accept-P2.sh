#!/usr/bin/env bash
# PART A ACCEPTANCE — session P2, the Google Places source.
#
# The stop condition for this session. `agent/gate.sh` answers "is this repo
# still sound?"; this answers "did session P2 do what it was sent to do?", which
# is a different question and the one RULES.md requires an agent to demonstrate
# rather than declare. Checks 1-10 are the numbered criteria from
# `sessions/session-P2.md` -> "Part A acceptance", in order, plus 6b.
#
#   bash agent/accept-P2.sh                 checks 1-9 (all local)
#   bash agent/accept-P2.sh --with-remote   adds check 10, against the deployed box
#
# Check 10 needs the deploy and a login, so it is opt-in and never runs by
# accident:  A4A_URL=https://... A4A_PASSWORD=... bash agent/accept-P2.sh --with-remote
#
# TURN CAP: 80 assistant turns for Part A. On exhausting it, stop and write
# `decisions/NNN-*.open.md` rather than continuing.
#
# **NOTHING HERE CALLS A PAID API — either of them.** That is the constraint
# this session could most easily break, so it is asserted rather than promised:
# `GOOGLE_PLACES_API_KEY` is never read from a live environment, the Places
# client is a replay of a fixture file that counts its calls,
# `PROSPECT_LOOKUP_PROVIDER` stays at its default ("none", which makes no call),
# and every screening test passes its own counting fake in explicitly. Check 2b
# greps the suite for a real client construction. No contact data is imported,
# modified or deleted outside the suite's own scratch database.
#
# What it DOES do to your machine: writes scratch files under $ACCEPT_SCRATCH,
# and for checks 8 and 9 rsyncs throwaway copies of the tree there. It writes
# nothing inside the repo and never touches the live box without --with-remote.

set -uo pipefail
cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)" || exit 1

WITH_REMOTE=0
for arg in "$@"; do
  case "$arg" in
    --with-remote) WITH_REMOTE=1 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

SCRATCH="${ACCEPT_SCRATCH:-${TMPDIR:-/tmp}/a4a-accept-P2}"
PY="${PY:-.venv/bin/python}"
[[ -x "$PY" ]] || PY="python3"
ABS_PY="$PY"; [[ "$ABS_PY" == /* ]] || ABS_PY="$PWD/$PY"

mkdir -p "$SCRATCH"

FAIL=0
step() { printf '\n── %s\n' "$1"; }
bad()  { printf 'ACCEPT FAIL: %s\n' "$1"; FAIL=1; }
ok()   { printf '   ok — %s\n' "$1"; }

stage_tree() {   # $1 = destination
  rm -rf "$1"; mkdir -p "$1"
  rsync -a --exclude .venv --exclude node_modules --exclude .git --exclude data \
    --exclude '__pycache__' --exclude '*.db' --exclude .pytest_cache ./ "$1/"
}

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

# ── 2-7. The behaviour, exercised, one criterion at a time ──────────────────
# Each criterion's tests run in their **own** pytest process. 5e's review found
# two tests that only passed inside a full run — one leaning on contacts another
# module had seeded, one reading a list an earlier test in its own file filled,
# the latter passing in isolation with two assertions comparing 0 == 0. A test
# that needs its neighbours proves nothing about the criterion it is named for.
step "2-7. the behaviour each criterion names"
declare -a CRITERIA=(
  "2|N API results produce N prospects; a second run produces 0 new and makes 0 paid calls of either kind|tests/test_google_places.py::test_n_api_results_produce_n_prospects tests/test_google_places.py::test_the_source_pages_until_the_token_runs_out tests/test_google_places.py::test_max_pages_stops_the_paging_before_the_token_does tests/test_google_places.py::test_a_second_identical_run_creates_nothing_and_makes_no_paid_call tests/test_google_places.py::test_a_search_the_cap_interrupted_is_not_treated_as_already_searched tests/test_google_places.py::test_a_failed_search_is_not_treated_as_already_searched tests/test_google_places.py::test_a_repeat_window_of_zero_switches_the_ledger_off"
  "3|the radius rule is asserted per category, and the seashell groups run their sweeps|tests/test_taxonomy.py::test_the_configured_radius_for_each_category tests/test_taxonomy.py::test_every_category_the_taxonomy_names_has_a_configured_radius tests/test_taxonomy.py::test_the_seashell_groups_run_the_sweeps_the_decision_describes tests/test_google_places.py::test_a_regional_group_drops_a_business_outside_its_radius tests/test_google_places.py::test_a_national_group_keeps_the_same_distant_business tests/test_google_places.py::test_distance_is_measured_from_the_auction_house_not_the_sweep"
  "4|exceeding the Google request cap stops the job cleanly and logs what was skipped|tests/test_google_places.py::test_the_cap_refuses_before_the_call_and_the_job_stays_clean tests/test_google_places.py::test_the_plan_stops_without_opening_a_job_it_cannot_run tests/test_google_places.py::test_a_cap_of_zero_switches_discovery_off_rather_than_making_it_unlimited tests/test_google_places.py::test_the_budget_counts_what_earlier_jobs_this_month_already_spent tests/test_google_places.py::test_last_months_requests_do_not_count_against_this_month tests/test_google_places.py::test_the_free_allowance_is_a_property_of_the_month_not_of_the_run"
  "5|every prospect carries its search term and that term's rationale, and the queue renders both|tests/test_google_places.py::test_every_prospect_carries_its_term_and_that_terms_rationale tests/test_google_places.py::test_the_review_queue_renders_both tests/test_taxonomy.py::test_every_group_carries_a_written_buyer_rationale tests/test_taxonomy.py::test_every_search_carries_its_groups_rationale_and_a_term tests/test_taxonomy.py::test_no_term_appears_in_two_groups"
  "6|every excluded business type is rejected by name, and a shell wholesaler is ACCEPTED|tests/test_taxonomy.py::test_an_auction_house_is_excluded tests/test_taxonomy.py::test_an_estate_sale_company_is_excluded tests/test_taxonomy.py::test_an_estate_liquidator_is_excluded tests/test_taxonomy.py::test_an_appraiser_is_excluded tests/test_taxonomy.py::test_a_consignment_gallery_is_excluded tests/test_taxonomy.py::test_a_we_buy_houses_operator_is_excluded tests/test_taxonomy.py::test_every_exclusion_is_reachable_by_at_least_one_name tests/test_taxonomy.py::test_a_shell_wholesaler_importer_or_distributor_is_accepted tests/test_taxonomy.py::test_no_exclusion_phrase_names_a_wholesaler_importer_or_distributor tests/test_taxonomy.py::test_the_priority_group_is_in_the_taxonomy_and_is_first tests/test_taxonomy.py::test_a_liquidation_store_is_not_an_estate_liquidator tests/test_google_places.py::test_an_excluded_business_is_stopped_before_any_spend"
  "6b|the radius applied to each seashell group, asserted individually|tests/test_taxonomy.py::test_the_three_seashell_groups_have_three_different_radius_rules tests/test_taxonomy.py::test_no_single_value_satisfies_all_three_seashell_groups tests/test_taxonomy.py::test_the_two_national_seashell_groups_do_not_inherit_their_radius"
  "7|a place already a Contact, already rejected, or already looked up is skipped before any spend|tests/test_google_places.py::test_a_business_already_a_contact_never_becomes_a_prospect tests/test_google_places.py::test_a_rejected_number_never_returns_and_is_never_paid_for tests/test_google_places.py::test_a_number_already_looked_up_is_not_looked_up_again"
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

# ── 2b. Nothing in the suite can reach either paid API ──────────────────────
# Asserted rather than promised. `PlacesClient()` with no arguments is the only
# construction that reads the live key and opens a socket; every test builds its
# source with a replay client instead.
#
# **Both patterns below were wrong on their first run, in this script's own
# output**, which is the fourth and fifth time a measurement script in this repo
# has counted something that was not a violation. `PlacesClient\(\)` matched
# `ReplayPlacesClient()` — an unanchored substring matching a name that merely
# ends the same way, the `21610` defect one column over — and
# `PROSPECT_LOOKUP_PROVIDER *=` matched both a context manager that restores the
# setting and an `== "none"` assertion. A failing check is a hypothesis, not a
# verdict.
step "2b. no test can reach a paid API"
# `\b` before the class name, so `ReplayPlacesClient(` is not a match. The one
# legitimate construction is the negative test that asserts it refuses with no
# key, and it is recognised by its `pytest.raises` two lines above.
LIVE=$(grep -rn --include='*.py' -B2 -E '\bPlacesClient\(' tests/ 2>/dev/null \
       | grep -E '\bPlacesClient\(' | while IFS= read -r line; do
         file="${line%%:*}"; rest="${line#*:}"; lineno="${rest%%:*}"
         start=$(( lineno > 3 ? lineno - 3 : 1 ))
         if ! sed -n "${start},${lineno}p" "$file" | grep -q "pytest.raises"; then
           printf '%s\n' "$line"
         fi
       done)
if [[ -n "$LIVE" ]]; then
  printf '%s\n' "$LIVE"
  bad "a test constructs a live Places client outside a refusal assertion"
else
  ok "the only live PlacesClient construction is the one asserted to refuse"
fi
# The danger is a test naming a real carrier, not a test touching the setting:
# `_prospect_setup` and P1b's helper both set it to fakes and restore it.
PROVIDER=$(grep -rn --include='*.py' -E \
           "PROSPECT_LOOKUP_PROVIDER[[:space:]]*=[[:space:]]*[\"'](telnyx|twilio)" \
           tests/ 2>/dev/null || true)
# A key literal in a test must say what it is. `"AIza-TEST-NOT-A-REAL-KEY"` is
# the redaction test's fixture and is fine; anything that does not name itself
# as fake is a credential somebody pasted in.
KEY=$(grep -rn --include='*.py' -E \
      "GOOGLE_PLACES_API_KEY[[:space:]]*=[[:space:]]*[\"'][^\"']+" tests/ 2>/dev/null \
      | grep -viE "test|fake|not-a-real|placeholder" || true)
if [[ -n "$PROVIDER" || -n "$KEY" ]]; then
  printf '%s\n' "$PROVIDER" "$KEY"
  bad "a test points a paid provider at a real credential"
else
  ok "no test names a real carrier provider or sets a live Places key"
fi
# And the check itself, run against a case whose answer is known. A green check
# whose script is broken is worse than no check.
if grep -qE '\bPlacesClient\(' <<<"    client = ReplayPlacesClient()"; then
  bad "the live-client pattern still matches the replay fake"
elif ! grep -qE '\bPlacesClient\(' <<<"    client = PlacesClient()"; then
  bad "the live-client pattern does not match a live construction"
elif grep -qviE "test|fake|not-a-real|placeholder" \
     <<<'settings.GOOGLE_PLACES_API_KEY = "AIzaSyDpasted00000000000000000000000"' \
     >/dev/null; then
  ok "both patterns fire on the case they exist for and not on the fakes (checked)"
else
  bad "the key pattern does not flag a pasted credential"
fi

# ── 4b. The cap refusing mid-run, shown rather than claimed ─────────────────
# accept-5g check 3's shape: a passing test named "the cap refuses" would also
# pass on a cap that refused for the wrong reason, or after the money was gone.
# This prints the call count, the job row and the operator's log line.
step "4b. the request cap, refusing mid-run, with the numbers"
"$PY" - <<'PY'
import logging, os, sys, tempfile
sys.path.insert(0, ".")
_fd, _db = tempfile.mkstemp(prefix="accept-P2-", suffix=".db"); os.close(_fd); os.unlink(_db)
os.environ["DATABASE_URL"] = f"sqlite:///{_db}"
os.environ["SMS_PROVIDER"] = "console"
os.environ.setdefault("SECRET_KEY", "accept-secret")
os.environ.setdefault("ADMIN_PASSWORD", "devpassword123")

from alembic import command
from alembic.config import Config
# No alembic.ini, for conftest.py's reason: it sets config_file_name, env.py
# then calls fileConfig(), and every existing logger is disabled — including the
# one this check reads the refusal off.
cfg = Config(); cfg.set_main_option("script_location", "alembic")
command.upgrade(cfg, "head")

from app.core.database import SessionLocal
from app.models.prospect import Prospect
from app.services import scrape_runner
from tests import _places_setup as places
from tests._prospect_setup import CountingLookupProvider

lines = []
class _Capture(logging.Handler):
    def emit(self, record): lines.append(record.getMessage())
logging.getLogger("prospects").addHandler(_Capture())
logging.getLogger("prospects").setLevel(logging.ERROR)

db = SessionLocal()
search = places.search_for("shell_wholesale", sweep_name="national")
provider = CountingLookupProvider()

# Room for exactly one of the search's two pages.
with places.caps(requests=1, max_pages=3):
    source = places.source(pages={None: "shell_wholesale_page_1",
                                  "A4A_TEST_PAGE_TOKEN_2": "shell_wholesale_page_2"})
    client = source.client
    job = scrape_runner.run_job(db, source, search_term=search.ledger_key,
                                search=search, timeout_seconds=20,
                                provider=provider)

created = db.query(Prospect).count()
print(f"     cap 1 request | the search has 2 pages")
print(f"     requests made:     {len(client.calls)}")
print(f"     job:               status={job.status} cleanup_ran={job.cleanup_ran}")
print(f"     job meters:        api_requests={job.api_requests} "
      f"api_requests_skipped={job.api_requests_skipped} api_cost={job.api_cost}")
print(f"     produced:          {created} prospect(s) kept, "
      f"{job.records_excluded} excluded by name")
for line in lines:
    print(f"     LOG: {line}")

assert len(client.calls) == 1, f"{len(client.calls)} requests under a 1-request cap"
assert job.status == "completed" and job.cleanup_ran == 1, "the job did not stop cleanly"
assert job.api_requests == 1 and job.api_requests_skipped == 1
assert created == 3, f"the first page's businesses were lost ({created})"
assert any("request cap" in line for line in lines), "the refusal was silent"

# And the interrupted search is NOT retired: the ledger must let it run again.
assert search.ledger_key not in scrape_runner.searched_recently(db, "google_places"), \
    "the cap retired a search it had only half-run"
print("     the interrupted search is still pending in the ledger")
db.close(); os.unlink(_db)
PY
if [[ $? -ne 0 ]]; then
  bad "the request cap did not refuse cleanly before spending"
else
  ok "the cap refused before the call, kept what it found, and logged the skip"
fi

# ── 6c. decisions/009, shown against the names it names ─────────────────────
step "6c. the exclusion list, run over the businesses the decision names"
"$PY" - <<'PY'
import sys
sys.path.insert(0, ".")
from app.sources.exclusions import excluded_reason

MUST_BE_EXCLUDED = [
    "Tidewater Auction House", "Palm Beach Auctioneers LLC",
    "Sunrise Estate Sales of Broward", "Sanibel Estate Liquidators LLC",
    "Beacon Hill Appraisers", "Coastline Consignment Gallery",
    "We Buy Houses Miami",
]
MUST_BE_ACCEPTED = [
    "Atlantic Coral Enterprise", "US Shell Inc", "Worldwide Wildlife Products",
    "California Seashell Company", "Blue Seas Trading",
    "Broward Liquidation Outlet", "Second Chance Resale Shop",
]
bad = 0
for name in MUST_BE_EXCLUDED:
    reason = excluded_reason(name)
    print(f"     EXCLUDED  {name:36} {(reason or '*** NOT EXCLUDED ***')[:44]}")
    bad += reason is None
for name in MUST_BE_ACCEPTED:
    reason = excluded_reason(name)
    print(f"     ACCEPTED  {name:36} {reason or 'ok'}")
    bad += reason is not None
if bad:
    raise SystemExit(f"{bad} business name(s) on the wrong side of the list")
print("     decisions/009: shell wholesalers, importers and distributors are BUYERS")
PY
if [[ $? -ne 0 ]]; then
  bad "the exclusion list puts a business on the wrong side"
else
  ok "every excluded type is caught and every buyer decisions/009 names is kept"
fi

# ── 6d. The radius each seashell group actually applies ─────────────────────
step "6d. the radius applied to each seashell group, printed"
"$PY" - <<'PY'
import sys
sys.path.insert(0, ".")
from app.sources import taxonomy

EXPECTED = {"shell_wholesale": None, "shell_makers": None,
            "shell_aggregate": 150, "shell_retail": None}
applied = {}
for slug, expected in EXPECTED.items():
    group = taxonomy.group(slug)
    radius = taxonomy.radius_for_group(group)
    sweeps = ",".join(s.name for s in taxonomy.sweeps_for(group))
    label = "national" if radius is None else f"{radius} miles"
    print(f"     {slug:18} priority={group.priority}  {label:12} sweeps={sweeps}")
    assert radius == expected, f"{slug}: {radius} != {expected}"
    applied[slug] = radius
three = {applied[s] for s in ("shell_wholesale", "shell_makers", "shell_aggregate")}
assert len(three) > 1, "one value satisfied all three groups — radius is back on the category"
print("     two national and one regional, inside one category")
PY
if [[ $? -ne 0 ]]; then
  bad "the seashell groups do not carry three separate radius rules"
else
  ok "national, national, regional — asserted individually"
fi

# ── 8. Every harness verifies its own scratch tree ─────────────────────────
# A documented guarantee that is true in two places out of seven is worse than
# none, because it gets quoted. Two halves, and the second is the one with
# teeth: the block is present in every harness, and it actually FIRES.
#
# Proved by handing every harness the *same* dirty tree. A harness that refuses
# exits before applying a single patch, so it leaves the tree untouched and the
# next one can use it — no restore, and no need for this script to know which
# files any given harness patches. (The first version of this check parsed each
# harness's MUTATIONS with `ast.literal_eval` to pick a victim file, and broke
# on `mutate-5j.py`, which names its path through a variable. A check that has
# to understand the thing it is checking is a check with its own bugs.)
step "8. every mutation harness verifies its scratch tree"
HARNESSES=$(ls agent/mutate-*.py | sort)
for harness in $HARNESSES; do
  if ! grep -q "SCRATCH VERIFIED PRISTINE" "$harness"; then
    bad "$harness does not verify its scratch tree"
  fi
done
DIRTY_TREE="$SCRATCH/dirty-tree"
stage_tree "$DIRTY_TREE"
DIRTIED=$(find "$DIRTY_TREE/app" "$DIRTY_TREE/tests" "$DIRTY_TREE/alembic" \
            -name '*.py' -not -path '*/__pycache__/*' 2>/dev/null \
          | tee "$SCRATCH/dirtied.txt" | wc -l | tr -d ' ')
while IFS= read -r f; do
  printf '\n# dirtied by accept-P2 check 8\n' >>"$f"
done <"$SCRATCH/dirtied.txt"
echo "   ($DIRTIED files in the scratch tree differ from the repo)"
for harness in $HARNESSES; do
  OUT=$("$ABS_PY" "$harness" "$DIRTY_TREE" "$ABS_PY" 2>&1); CODE=$?
  NAMED=$(grep -c 'differs from the repo' <<<"$OUT")
  if [[ $CODE -ne 2 ]] || ! grep -q "SCRATCH TREE IS NOT PRISTINE" <<<"$OUT"; then
    printf '%s\n' "$OUT" | head -5
    bad "$(basename "$harness") did not refuse a dirty scratch tree (exit $CODE)"
  else
    ok "$(basename "$harness") refuses a dirty tree — $NAMED file(s) named"
  fi
done
rm -rf "$DIRTY_TREE"
# The other direction, so the check above is not merely a check that everything
# always exits 2. One full run of the smallest back-ported harness, on a clean
# tree, which must print the line and go green. `mutate-P2.py` proves it again
# in check 9.
step "8b. a back-ported harness accepts a clean tree and runs"
CLEAN_TREE="$SCRATCH/clean-tree"
stage_tree "$CLEAN_TREE"
if "$ABS_PY" agent/mutate-5g.py "$CLEAN_TREE" "$ABS_PY" >"$SCRATCH/mutate-5g.log" 2>&1; then
  head -1 "$SCRATCH/mutate-5g.log" | sed 's/^/   /'
  echo "   $(grep -c '  CAUGHT' "$SCRATCH/mutate-5g.log") mutation(s) caught, 0 survived"
  ok "mutate-5g.py verified a clean tree and completed"
else
  tail -10 "$SCRATCH/mutate-5g.log"
  bad "mutate-5g.py did not complete on a clean tree"
fi
rm -rf "$CLEAN_TREE"

# ── 9. Each guard reverted on its own, on a verified-pristine tree ──────────
step "9. every P2 guard reverted one at a time; each must break a test"
MUTATION_TREE="$SCRATCH/mutation-tree"
stage_tree "$MUTATION_TREE"
if ! "$ABS_PY" agent/mutate-P2.py "$MUTATION_TREE" "$ABS_PY" >"$SCRATCH/mutations.log" 2>&1; then
  sed 's/^/   /' "$SCRATCH/mutations.log"
  bad "a guard was reverted and no test noticed"
else
  grep -E '^(SCRATCH VERIFIED|[XRDCPE][0-9]|  CAUGHT|[0-9]+ mutations)' \
    "$SCRATCH/mutations.log" | sed 's/^/   /'
  ok "every reverted guard breaks at least one test that names it"
fi
rm -rf "$MUTATION_TREE"

# ── 10. The deployed box (opt-in) ───────────────────────────────────────────
step "10. deployed site over HTTPS: every screen 200, no carrier name, no raw payload, no spend of ours"
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
      elif grep -qE "googleapis\.com|X-Goog|place_id|raw_payload" "$SCRATCH/page.html"; then
        bad "${path} renders a raw provider payload or an API endpoint"
      elif ! "$PY" - "$SCRATCH/page.html" "$path" <<'PY'
import sys
sys.path.insert(0, ".")
from tests import _wholesale_scan as scan
scan.assert_no_wholesale_figure(open(sys.argv[1]).read(), where=sys.argv[2])
PY
      then
        bad "${path} renders one of our own costs"
      else
        ok "${path} 200, no carrier name, no payload, no cost of ours"
      fi
    done
    for path in /api/prospects /api/prospects/summary /api/prospects/terms \
                /api/prospects/export.csv; do
      curl -s -b "$JAR" "${A4A_URL}${path}" >"$SCRATCH/api.txt"
      if grep -qiE 'telnyx|twilio|raw_payload|place_id|googleapis|api_cost|api_requests|lookups_performed' "$SCRATCH/api.txt"; then
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
  echo "ACCEPT PASS — session P2 Part A"
  [[ $WITH_REMOTE -eq 0 ]] && echo "  (criterion 10 not run; it needs the deploy)"
else
  echo "ACCEPT FAILED — see above"
fi
exit $FAIL
