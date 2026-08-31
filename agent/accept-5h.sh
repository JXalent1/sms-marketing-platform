#!/usr/bin/env bash
# PART A ACCEPTANCE — session 5h, held-back rows and the capacity floor.
#
# The stop condition for this session. `agent/gate.sh` answers "is this repo
# still sound?"; this answers "did session 5h do what it was sent to do?", which
# is a different question and the one RULES.md requires an agent to demonstrate
# rather than declare. Each check below is one numbered criterion from
# `sessions/session-5h.md` -> "Part A acceptance", in order.
#
#   bash agent/accept-5h.sh                 checks 1-8b (all local)
#   bash agent/accept-5h.sh --with-remote   adds check 9, against the deployed box
#
# Check 9 needs the deployed site and a login, so it is opt-in and never runs by
# accident:  A4A_URL=https://... A4A_PASSWORD=... bash agent/accept-5h.sh --with-remote
#
# TURN CAP: 60 assistant turns for Part A. On exhausting it, stop and write
# `decisions/NNN-*.open.md` rather than continuing — the same discipline
# MAX_GATE_ATTEMPTS=4 applies to the gate. A session that has spent sixty turns
# and still cannot make this script exit 0 has found something a human needs to
# look at.
#
# Nothing here sends a message. SMS_PROVIDER is console in every subprocess, no
# live credential is read, and no contact data is imported, modified or deleted
# outside the suite's own scratch database.
#
# What it DOES do to your machine: writes scratch files under $ACCEPT_SCRATCH,
# and for check 8b rsyncs a throwaway copy of the tree there. It writes nothing
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

SCRATCH="${ACCEPT_SCRATCH:-${TMPDIR:-/tmp}/a4a-accept-5h}"
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
  "2|a campaign built with the window > 0 writes held_back rows; a region-filtered contact still writes skipped|tests/test_held_back_release.py::test_the_window_writes_held_back_and_the_region_filter_still_writes_skipped tests/test_campaign_guardrails.py::test_suppression_excludes_two_days_and_includes_five"
  "3|decision 005 end to end: 4-person list, 2 texted yesterday, window 3 -> send -> window 0 -> the top-up reaches exactly those 2 by flipping their rows|tests/test_held_back_release.py::test_the_hold_clears_and_a_top_up_reaches_exactly_the_people_it_held tests/test_held_back_release.py::test_a_hold_that_has_not_cleared_is_not_released tests/test_held_back_release.py::test_a_widened_window_does_not_un_send_anybody tests/test_held_back_release.py::test_a_phone_the_campaign_reached_another_way_is_never_released tests/test_held_back_release.py::test_two_held_back_rows_for_one_phone_release_as_one_message tests/test_held_back_release.py::test_a_capped_campaign_releases_nobody tests/test_held_back_release.py::test_a_held_back_contact_who_left_the_list_is_not_called_held_back tests/test_held_back_release.py::test_a_top_up_that_both_releases_and_holds_back_counts_only_what_it_sent"
  "4|held_back rows are excluded from billing, shown against a cycle count with them present|tests/test_held_back_release.py::test_a_held_back_row_is_not_counted_in_the_billing_cycle tests/test_campaign_guardrails.py::test_a_suppressed_message_is_never_billed"
  "5|pre-existing skipped rows on a campaign built before this change are untouched by a top-up|tests/test_held_back_release.py::test_a_pre_existing_skipped_row_is_never_re_adjudicated"
  "6|a one-segment send on a zero balance is refused|tests/test_capacity_rounding.py"
  "006|decision 006's wording: the abort reason names the window and when it clears, in A6's phrasing; the capped refusal names the cap and the remedy|tests/test_hold_wording.py"
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

# ── 6b. The capacity guard, before and after, printed ───────────────────────
# Criterion 6 asks for it "passing before the fix and refusing after", and the
# before/after has to be *run* rather than asserted from memory — the shape
# accept-5g check 3 established. The pre-fix arithmetic is three tokens
# (`round(n*rate, 2) * 1.5`, compared as floats), so it is reproduced here
# rather than checked out.
#
# The row that matters most is the first one, and it is the one the session spec
# gets wrong: at this box's own 0.009 a one-segment send was ALREADY refused,
# because the estimate rounds up. The defect the spec describes needs a blended
# rate under half a cent. What is wrong at 0.009 is the six-segment row.
step "6b. the capacity verdict before and after the fix"
"$PY" - <<'PY'
import asyncio, sys
sys.path.insert(0, ".")
from app.core.config import settings
from app.core.database import SessionLocal
from app.services.campaign_builder import wholesale_estimate
from app.services.campaign_service import CampaignService

class _Balance:
    name = "console"
    def __init__(self, b): self.b = b
    async def get_balance(self): return self.b
    async def send(self, to, text): raise AssertionError("pre-flight, not a send")

def before(balance, segments, rate):
    return balance >= round(segments * rate, 2) * 1.5

def after(balance, segments, rate):
    db = SessionLocal()
    try:
        service = CampaignService(db)
        service.provider = _Balance(balance)
        original = settings.WHOLESALE_COST_PER_SEGMENT
        settings.WHOLESALE_COST_PER_SEGMENT = rate
        try:
            return asyncio.run(service.capacity_assessment(
                segments, wholesale_estimate(segments)))["ok"]
        finally:
            settings.WHOLESALE_COST_PER_SEGMENT = original
    finally:
        db.close()

CASES = [
    ("1 segment, $0.00 balance, rate 0.004", 0.0, 1, 0.004, False),
    ("2 segments, $0.00 balance, rate 0.004", 0.0, 2, 0.004, False),
    ("1 segment, $0.00 balance, rate 0.009", 0.0, 1, 0.009, False),
    ("6 segments, $0.078 balance, rate 0.009", 0.078, 6, 0.009, False),
    ("6 segments, $0.088 balance, rate 0.009", 0.088, 6, 0.009, True),
    ("6857 segments, $200 balance, rate 0.009", 200.0, 6857, 0.009, True),
]
print(f"   {'':44}{'before':>8}{'after':>8}")
failures = 0
for label, balance, segments, rate, expected in CASES:
    b, a = before(balance, segments, rate), after(balance, segments, rate)
    mark = "  <-- changed" if b != a else ""
    print(f"   {label:44}{str(b):>8}{str(a):>8}{mark}")
    if a is not expected:
        print(f"       *** expected the fix to answer {expected} here")
        failures += 1
sys.exit(1 if failures else 0)
PY
if [[ $? -ne 0 ]]; then
  bad "the capacity guard does not answer as criterion 6 requires"
else
  ok "a send the balance cannot cover is refused; one it can cover still starts"
fi

# ── 7. The money-rounding audit ─────────────────────────────────────────────
# Criterion 7. The audit's finding is that `wholesale_estimate()` was the only
# site where a money value was rounded before a comparison, and it is fixed —
# so what this check has to do is make the *next* one loud. Every `round()` in
# `app/` is listed below with what it rounds; a new or changed one fails, which
# forces whoever adds it to say which kind it is.
step "7. every round() in app/ is a known, audited site"
"$PY" - <<'PY'
import ast, pathlib, sys

# Every `round()` call in app/, by the function it sits in, with what it rounds
# and why that is not a comparison. Found by walking the AST rather than by
# grepping, because three of the eight lines matching /round\(/ in this tree are
# prose inside docstrings *about* rounding, and a check that counts those is a
# check nobody will keep passing.
AUDITED = {
  ("app/services/billing_service.py", "current_usage"):
    "percentage_used — a percentage of an allowance, not money",
  ("app/services/dashboard_service.py", "stat_tiles"):
    "delivered-vs-prior percentage change",
  ("app/services/dashboard_service.py", "segment_chart"):
    "bar height as a percentage of the peak",
  ("app/services/dashboard_service.py", "last_send_outcomes"):
    "outcome bar as a percentage of the total",
  ("app/services/campaign_service.py", "capacity_assessment"):
    "required_segments — a SEGMENT count for the client-facing wording, "
    "computed after the comparison and never fed back into it",
}

found = {}
for path in sorted(pathlib.Path("app").rglob("*.py")):
    stack = []
    class V(ast.NodeVisitor):
        def visit_FunctionDef(self, node):
            stack.append(node.name); self.generic_visit(node); stack.pop()
        visit_AsyncFunctionDef = visit_FunctionDef
        def visit_Call(self, node):
            if isinstance(node.func, ast.Name) and node.func.id == "round":
                found[(str(path), stack[-1] if stack else "<module>")] = node.lineno
            self.generic_visit(node)
    V().visit(ast.parse(path.read_text()))

new = sorted(set(found) - set(AUDITED))
gone = sorted(set(AUDITED) - set(found))
for (path, func), why in sorted(AUDITED.items()):
    if (path, func) in found:
        print(f"     {path}:{found[(path, func)]} {func}()  ->  {why}")
for path, func in new:
    print(f"     *** UNAUDITED: {path}:{found[(path, func)]} {func}()")
for path, func in gone:
    print(f"     (audited site no longer present: {path} {func}())")
# A site that has moved or vanished is not a failure — the audit is a list of
# what is there, and it is allowed to shrink. A site that is *new* is, because
# nobody has said which kind of rounding it is.
sys.exit(1 if new else 0)
PY
if [[ $? -ne 0 ]]; then
  bad "a round() in app/ is not on the audited list — say which kind it is and update this check"
else
  ok "no money value in app/ is rounded before it is compared"
fi

# The one that was: the capacity check must not reach for the rounded figure.
step "7b. the capacity check compares the exact cost, not the rounded estimate"
# By AST again, and for the same reason as check 7: `wholesale_estimate` appears
# in `campaign_service.py` twice as text — once in the re-export list and once in
# a comment explaining why it is not used — and a grep counts both. What matters
# is whether it is *called*.
"$PY" - <<'PY'
import ast, pathlib, sys
tree = ast.parse(pathlib.Path("app/services/campaign_service.py").read_text())
calls = [n.lineno for n in ast.walk(tree)
         if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
         and n.func.id == "wholesale_estimate"]
if calls:
    print(f"     wholesale_estimate() is CALLED at line(s) {calls}")
    sys.exit(1)
print("     wholesale_estimate is re-exported and never called here")
sys.exit(0)
PY
if [[ $? -ne 0 ]]; then
  bad "campaign_service calls the rounded estimate — the comparison must use wholesale_cost()"
elif ! grep -q 'if Decimal(str(balance)) < exact_required:' app/services/campaign_service.py; then
  bad "the balance comparison is no longer an exact Decimal one"
else
  ok "wholesale_cost() feeds the comparison; wholesale_estimate() only storage and logs"
fi

# ── 8. The new tests are red against the pre-5h behaviour ───────────────────
# Weak on its own and kept for the same reason 5g kept it: it is cheap, and it
# fails loudly if a test module is accidentally testing nothing. Check 8b is the
# one with teeth.
step "8. the new modules fail when the fixes are reverted (see 8b for why this is weak)"
ok "superseded by 8b — a behavioural mutation run, not an import failure"

# ── 8b. Each fix reverted on its own, and the suite has to notice ───────────
step "8b. every 5h fix reverted one at a time; each must break a test"
MUTATION_TREE="$SCRATCH/mutation-tree"
rm -rf "$MUTATION_TREE"
mkdir -p "$MUTATION_TREE"
if ! rsync -a --exclude .venv --exclude node_modules --exclude .git --exclude data \
      --exclude '__pycache__' --exclude '*.db' --exclude .pytest_cache \
      ./ "$MUTATION_TREE/" 2>/dev/null; then
  bad "could not stage a scratch tree for the mutation check"
elif ! "$ABS_PY" agent/mutate-5h.py "$MUTATION_TREE" "$ABS_PY" >"$SCRATCH/mutations.log" 2>&1; then
  sed 's/^/   /' "$SCRATCH/mutations.log"
  bad "a fix was reverted and no test noticed"
else
  grep -E '^(R[0-9]|  CAUGHT|[0-9]+ mutations)' "$SCRATCH/mutations.log" | sed 's/^/   /'
  ok "every reverted fix breaks at least one test that names it"
fi
rm -rf "$MUTATION_TREE"

# ── 9. The deployed box (opt-in) ────────────────────────────────────────────
step "9. deployed site over HTTPS, with no carrier name or raw provider payload"
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
    for path in / /campaigns /contacts /blocklist /usage /settings /health; do
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
    for path in /api/campaigns /api/settings/suppression /api/settings/system; do
      BODY=$(curl -s -b "$JAR" "${A4A_URL}${path}")
      if grep -qiE 'telnyx|twilio' <<<"$BODY"; then
        bad "${path} names the carrier"
      else
        ok "${path} names no carrier"
      fi
    done
  fi
fi

printf '\n'
if [[ $FAIL -eq 0 ]]; then
  echo "ACCEPT PASS — session 5h Part A"
  [[ $WITH_REMOTE -eq 0 ]] && echo "  (criterion 9 not run; it needs the deploy)"
else
  echo "ACCEPT FAILED — see above"
fi
exit $FAIL
