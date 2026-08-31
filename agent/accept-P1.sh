#!/usr/bin/env bash
# PART A ACCEPTANCE — session P1, the prospect pipeline.
#
# The stop condition for this session. `agent/gate.sh` answers "is this repo
# still sound?"; this answers "did session P1 do what it was sent to do?", which
# is a different question and the one RULES.md requires an agent to demonstrate
# rather than declare. Checks 1-11 are the numbered criteria from
# `sessions/session-P1.md` -> "Part A acceptance", in order.
#
#   bash agent/accept-P1.sh                 checks 1-10 and 8b (all local)
#   bash agent/accept-P1.sh --with-remote   adds check 11, against the deployed box
#
# Check 11 needs the deploy and a login, so it is opt-in and never runs by
# accident:  A4A_URL=https://... A4A_PASSWORD=... bash agent/accept-P1.sh --with-remote
#
# TURN CAP: 60 assistant turns for Part A. On exhausting it, stop and write
# `decisions/NNN-*.open.md` rather than continuing — the same discipline
# MAX_GATE_ATTEMPTS=4 applies to the gate. A session that has spent sixty turns
# and still cannot make this script exit 0 has found something a human needs to
# look at.
#
# Nothing here sends a message and nothing here calls a paid API.
# SMS_PROVIDER is console in every subprocess, PROSPECT_LOOKUP_PROVIDER is never
# changed from its default ("none", which makes no calls at all), and every test
# passes its own counting fake in explicitly. No contact data is imported,
# modified or deleted outside the suite's own scratch database.
#
# What it DOES do to your machine: writes scratch files under $ACCEPT_SCRATCH,
# and for check 10 rsyncs a throwaway copy of the tree there. It writes nothing
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

SCRATCH="${ACCEPT_SCRATCH:-${TMPDIR:-/tmp}/a4a-accept-P1}"
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

# ── 2-9. The behaviour, exercised, one criterion at a time ──────────────────
# Each criterion's tests run in their **own** pytest process. 5e's review found
# two tests that only passed inside a full run — one leaning on contacts another
# module had seeded, one reading a list an earlier test in its own file filled,
# the latter passing in isolation with two assertions comparing 0 == 0. A test
# that needs its neighbours proves nothing about the criterion it is named for.
step "2-9. the behaviour each criterion names"
declare -a CRITERIA=(
  "2|promote creates a Contact tagged with the chosen category and links promoted_contact_id|tests/test_prospect_review.py::test_promote_creates_a_contact_tagged_with_the_category_and_links_it tests/test_prospect_review.py::test_promote_and_reject_go_through_the_endpoint_too tests/test_prospect_review.py::test_promote_reports_what_went_in_and_what_did_not"
  "3|reject suppresses permanently — the same record from a DIFFERENT source does not reappear|tests/test_prospect_review.py::test_a_rejection_suppresses_the_number_for_every_future_source tests/test_prospect_review.py::test_a_rejected_prospect_cannot_be_promoted tests/test_prospect_review.py::test_rejecting_twice_is_unchanged_rather_than_an_error tests/test_prospect_review.py::test_a_promoted_prospect_cannot_be_rejected_and_the_refusal_says_where_to_go"
  "4|a hung job is killed at its deadline and cleanup is ASSERTED to have run|tests/test_prospect_pipeline.py::test_a_hung_job_is_stopped_at_its_deadline_and_its_cleanup_runs tests/test_prospect_pipeline.py::test_a_source_that_raises_still_has_its_cleanup_run tests/test_prospect_pipeline.py::test_a_timed_out_job_keeps_what_it_had_already_produced"
  "5|landlines are excluded from promote-eligible by default|tests/test_prospect_review.py::test_a_landline_cannot_be_promoted tests/test_prospect_review.py::test_an_unscreened_number_cannot_be_promoted tests/test_prospect_review.py::test_the_queues_eligible_filter_and_the_promote_guard_agree tests/test_prospect_pipeline.py::test_the_gate_lets_through_only_what_it_can_prove_is_reachable"
  "6|a repeat lookup hits the cache and makes NO call — asserted on call count|tests/test_prospect_pipeline.py::test_a_repeat_lookup_hits_the_cache_and_makes_no_call tests/test_prospect_pipeline.py::test_a_failed_lookup_is_recorded_but_not_cached_as_an_answer tests/test_prospect_pipeline.py::test_a_second_job_over_the_same_numbers_pays_nothing"
  "7|a blocklisted number and an opted-out number both cannot be promoted|tests/test_prospect_review.py::test_a_blocklisted_number_cannot_be_promoted tests/test_prospect_review.py::test_an_opted_out_number_cannot_be_promoted_and_is_told_apart"
  "8|every prospect retains source_url, scrape timestamp, raw_payload, search term and buyer rationale|tests/test_prospect_pipeline.py::test_every_prospect_retains_its_provenance tests/test_prospect_pipeline.py::test_a_record_with_no_buyer_rationale_never_reaches_the_queue tests/test_prospect_pipeline.py::test_a_record_with_no_search_term_or_source_url_is_refused_too tests/test_prospect_pipeline.py::test_the_two_constraints_that_are_the_guarantees_survived_the_migration"
  "9|the queue renders the buyer rationale and the per-term breakdown tells sellers apart|tests/test_prospect_review.py::test_the_queue_renders_the_buyer_rationale_on_every_row tests/test_prospect_review.py::test_the_term_breakdown_tells_sellers_apart_from_other_reasons tests/test_prospect_review.py::test_a_term_whose_rejections_are_mostly_sellers_is_flagged tests/test_prospect_review.py::test_a_single_seller_rejection_does_not_condemn_a_term"
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

# ── 4b. The timeout, measured rather than claimed ───────────────────────────
# Criterion 4 says the job is killed at timeout. A test asserting
# `status == "timed_out"` is true and easy to misread — it would also pass on a
# runner that waited for the source and labelled the result afterwards. This
# prints the wall clock against the deadline, which is accept-5g check 3's
# "prove it, do not claim it" shape.
step "4b. what the deadline actually costs, in seconds"
ACCEPT_DB="$SCRATCH/accept-P1.db"
rm -f "$ACCEPT_DB"
export DATABASE_URL="sqlite:///${ACCEPT_DB}"
if ! DATABASE_URL="$DATABASE_URL" alembic upgrade head >"$SCRATCH/accept-db.log" 2>&1; then
  tail -10 "$SCRATCH/accept-db.log"
  bad "could not build the scratch database for check 4b"
fi
"$PY" - <<'PY'
import sys, threading, time
sys.path.insert(0, ".")
from app.core.database import SessionLocal
from app.services import scrape_runner
from tests import _prospect_setup as setup

db = SessionLocal()
try:
    source = setup.HangingSource([setup.record("+15555551999", name="Hung Kitchen")])
    before = {t.name for t in threading.enumerate()}
    started = time.monotonic()
    job = scrape_runner.run_job(db, source, search_term="hung",
                                timeout_seconds=2, screen=False)
    elapsed = time.monotonic() - started

    print(f"     deadline 2.0s -> returned after {elapsed:.2f}s "
          f"(the source blocks for 30s)")
    print(f"     job status:  {job.status}")
    print(f"     cleanup_ran: {job.cleanup_ran}  |  source.cleanup() calls: {source.cleaned}")
    print(f"     kept what it already had: records_yielded={job.records_yielded} "
          f"prospects_created={job.prospects_created}")

    for _ in range(100):
        leaked = {t.name for t in threading.enumerate()} - before
        if f"scrape:{source.name}" not in leaked:
            break
        time.sleep(0.02)
    leaked = {t.name for t in threading.enumerate()} - before
    print(f"     worker threads left behind: {sorted(leaked) or 'none'}")

    ok = (job.status == "timed_out" and job.cleanup_ran == 1
          and source.cleaned == 1 and elapsed < 10
          and job.prospects_created == 1
          and f"scrape:{source.name}" not in leaked)
finally:
    db.close()
sys.exit(0 if ok else 1)
PY
if [[ $? -ne 0 ]]; then
  bad "the hung job was not stopped at its deadline with its cleanup run"
else
  ok "stopped at the deadline, cleanup ran, nothing left running"
fi
rm -f "$ACCEPT_DB"
unset DATABASE_URL

# ── 8b. Our screening spend is structurally absent from every client surface ─
# Criterion 11 again, from the other side. The runtime scan in the test suite
# proves the routes as they are today; this proves nothing in a prospect router
# or template *reads* a cost column, so a route added later inherits the
# property instead of needing a new test.
#
# By AST rather than by grep, and accept-5f's first draft is why: a plain grep
# failed on a docstring saying these fields must never be returned. A check that
# counts prose about a rule as a violation of it is a check nobody will keep
# passing. Templates are grepped, because Jinja has no docstrings.
step "8b. no client-facing module reads our screening spend or the raw payload"
"$PY" - <<'PY'
import ast, pathlib, sys

FORBIDDEN = {"PROSPECT_LOOKUP_COST_PER_NUMBER", "cost_per_lookup", "raw_payload",
             "lookups_performed", "lookups_cached"}

found = set()
for path in sorted(pathlib.Path("app/routers").rglob("*.py")):
    stack = []
    class V(ast.NodeVisitor):
        def visit_FunctionDef(self, node):
            stack.append(node.name); self.generic_visit(node); stack.pop()
        visit_AsyncFunctionDef = visit_FunctionDef
        def visit_Attribute(self, node):
            if node.attr in FORBIDDEN:
                found.add((str(path), stack[-1] if stack else "<module>", node.attr))
            self.generic_visit(node)
        def visit_Name(self, node):
            if node.id in FORBIDDEN:
                found.add((str(path), stack[-1] if stack else "<module>", node.id))
            self.generic_visit(node)
        def visit_Constant(self, node):
            # A string literal naming a column is how it reaches a payload key.
            if isinstance(node.value, str) and node.value in FORBIDDEN:
                found.add((str(path), stack[-1] if stack else "<module>", node.value))
    V().visit(ast.parse(path.read_text()))

for path, func, name in sorted(found):
    print(f"     *** {path} {func}() reads {name}")
sys.exit(1 if found else 0)
PY
if [[ $? -ne 0 ]]; then
  bad "a router reads our screening spend or the raw third-party payload"
else
  ok "no router reads a cost column or the raw payload"
fi
HITS=$(grep -rnI --exclude-dir=__pycache__ \
  "raw_payload\|lookups_performed\|PROSPECT_LOOKUP_COST" app/templates/ 2>/dev/null || true)
if [[ -n "$HITS" ]]; then
  printf '%s\n' "$HITS"
  bad "a template names the raw payload or our screening spend"
else
  ok "no template names the payload or a cost figure"
fi

# ── 8c. A source cannot reach the database, and app/sms stays DB-free ───────
# A2's whole claim. The gate checks app/sms; this checks the other half of the
# seam — a discovery source that imported a model would have taken the dedup
# guarantee, the rejection check and the provenance requirement into itself,
# where the next source will not inherit any of them.
step "8c. the source seam holds"
LEAKS=$(grep -rnI --exclude-dir=__pycache__ "^from app\.models\|^import app\.models" \
  app/sources/ 2>/dev/null || true)
if [[ -n "$LEAKS" ]]; then
  printf '%s\n' "$LEAKS"
  bad "a source module imports the model layer directly"
else
  ok "no source module imports app.models"
fi
if grep -rnIq --exclude-dir=__pycache__ "^from app\.\(models\|services\)\|^import app\.\(models\|services\)" \
     app/sms/lookup.py 2>/dev/null; then
  bad "app/sms/lookup.py imports the DB layer"
else
  ok "app/sms/lookup.py is DB-free"
fi

# ── 8d. Nothing in this session can call a paid API by default ──────────────
# The session's own constraint, asserted rather than promised. The default
# provider must make no network call at all, so a box that has not been given a
# screening credential cannot spend money by accident.
step "8d. the default line-type provider makes no call"
"$PY" - <<'PY'
import sys
sys.path.insert(0, ".")
from app.core.config import settings
from app.sms import lookup

assert settings.PROSPECT_LOOKUP_PROVIDER == "none", (
    f"the default provider is {settings.PROSPECT_LOOKUP_PROVIDER!r}; a box with "
    f"no screening credential must not reach a paid API")
provider = lookup.get_lookup_provider(force_reload=True)
result = provider.lookup("+15555551234")
print(f"     default provider: {provider.name} -> {result.line_type}, ok={result.ok}")
assert provider.name == "disabled" and result.line_type == "unknown"
assert "unknown" not in __import__(
    "app.services.lookup_service", fromlist=["x"]).PROMOTABLE_LINE_TYPES, (
    "unknown is promote-eligible, so a box with screening off promotes landlines")
# And there is no registered provider that could call anything.
assert set(lookup.PROVIDERS) == {"none"}, (
    f"a carrier lookup provider is registered ({sorted(lookup.PROVIDERS)}). "
    f"That is a budget decision — RULES.md escalation item 7.")
PY
if [[ $? -ne 0 ]]; then
  bad "the default screening path could reach a paid API, or unknown is promotable"
else
  ok "default is a no-call provider, and unknown cannot be promoted"
fi

# ── 10. Each guard reverted on its own, and the suite has to notice ─────────
step "10. every P1 guard reverted one at a time; each must break a test"
MUTATION_TREE="$SCRATCH/mutation-tree"
rm -rf "$MUTATION_TREE"
mkdir -p "$MUTATION_TREE"
if ! rsync -a --exclude .venv --exclude node_modules --exclude .git --exclude data \
      --exclude '__pycache__' --exclude '*.db' --exclude .pytest_cache \
      ./ "$MUTATION_TREE/" 2>/dev/null; then
  bad "could not stage a scratch tree for the mutation check"
elif ! "$ABS_PY" agent/mutate-P1.py "$MUTATION_TREE" "$ABS_PY" >"$SCRATCH/mutations.log" 2>&1; then
  sed 's/^/   /' "$SCRATCH/mutations.log"
  bad "a guard was reverted and no test noticed"
else
  grep -E '^(R[0-9]|  CAUGHT|[0-9]+ mutations)' "$SCRATCH/mutations.log" | sed 's/^/   /'
  ok "every reverted guard breaks at least one test that names it"
fi
rm -rf "$MUTATION_TREE"

# ── 11. The deployed box (opt-in) ───────────────────────────────────────────
step "11. deployed site over HTTPS: every screen 200, no carrier name, no payload"
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
      else
        ok "${path} 200, no carrier name, no raw payload"
      fi
    done
    for path in /api/prospects /api/prospects/summary /api/prospects/terms \
                /api/prospects/export.csv; do
      BODY=$(curl -s -b "$JAR" "${A4A_URL}${path}")
      if grep -qiE 'telnyx|twilio|raw_payload|place_id|wholesale|lookups_performed' <<<"$BODY"; then
        bad "${path} names a carrier, our spend, or the raw payload"
      else
        ok "${path} is clean"
      fi
    done
  fi
fi

printf '\n'
if [[ $FAIL -eq 0 ]]; then
  echo "ACCEPT PASS — session P1 Part A"
  [[ $WITH_REMOTE -eq 0 ]] && echo "  (criterion 11 not run; it needs the deploy)"
else
  echo "ACCEPT FAILED — see above"
fi
exit $FAIL
