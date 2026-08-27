#!/usr/bin/env bash
# PART A ACCEPTANCE — session 5g, blocklist correctness.
#
# The stop condition for this session. `agent/gate.sh` answers "is this repo
# still sound?"; this answers "did session 5g do what it was sent to do?", which
# is a different question and the one RULES.md requires an agent to demonstrate
# rather than declare. Each check below is one numbered criterion from
# `sessions/session-5g.md` -> "Part A acceptance", in order.
#
#   bash agent/accept-5g.sh                 checks 1-8 (all local)
#   bash agent/accept-5g.sh --with-remote   adds check 9, against the deployed box
#
# Check 9 needs the deployed site and a login, so it is opt-in and never runs by
# accident:  A4A_URL=https://... A4A_PASSWORD=... bash agent/accept-5g.sh --with-remote
#
# TURN CAP: 60 assistant turns for Part A. On exhausting it, stop and write
# `decisions/NNN-*.open.md` rather than continuing — the same discipline
# MAX_GATE_ATTEMPTS=4 applies to the gate. A session that has spent sixty turns
# and still cannot make this script exit 0 has found something a human needs to
# look at.
#
# Nothing here sends a message. SMS_PROVIDER is forced to console in every
# subprocess, no live credential is read, and the classifier probe calls
# `classify_failure()` — a pure function over strings that touches no network
# and no provider.
#
# What it DOES do to your machine: adds and removes a detached git worktree at
# $BASE_REF for checks 3 and 8, and writes scratch files under $ACCEPT_SCRATCH.
# It writes nothing else inside the repo, and it never touches the live box
# unless you pass --with-remote.

set -uo pipefail
cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)" || exit 1

WITH_REMOTE=0
for arg in "$@"; do
  case "$arg" in
    --with-remote) WITH_REMOTE=1 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

# The commit session 5g started from. Checks 3 and 8 run against this tree.
BASE_REF="${BASE_REF:-f16998b}"
SCRATCH="${ACCEPT_SCRATCH:-${TMPDIR:-/tmp}/a4a-accept-5g}"
PY="${PY:-.venv/bin/python}"
[[ -x "$PY" ]] || PY="python3"
ABS_PY="$PY"; [[ "$ABS_PY" == /* ]] || ABS_PY="$PWD/$PY"

mkdir -p "$SCRATCH"

FAIL=0
step() { printf '\n── %s\n' "$1"; }
bad()  { printf 'ACCEPT FAIL: %s\n' "$1"; FAIL=1; }
ok()   { printf '   ok — %s\n' "$1"; }

# The test files session 5g added. Check 8 copies them into the pre-fix
# worktree; keeping the list in one place stops the two from drifting apart.
# `_carrier_failure_setup.py` is the shared half — the 500-line rule split the
# original module along "what a failure does" / "what a failure says".
NEW_TESTS=(tests/test_blocklist_correctness.py tests/test_carrier_error_surfaces.py
           tests/_carrier_failure_setup.py)

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

# ── 2, 4, 5, 6, 7. The behaviour, exercised ─────────────────────────────────
# These live in the suite, where they are run on every future change rather than
# only when someone remembers this script. Naming them individually is what
# makes the acceptance criteria auditable — a criterion satisfied by "the suite
# is green" is a criterion nobody can find again.
step "2,4,5,6,7. the behaviour these criteria name"
declare -a CRITERIA=(
  "2|all four live error strings classify correctly, and the raw-JSON row is no longer a dict repr|tests/test_blocklist_correctness.py::test_the_live_failure_corpus_classifies_the_way_it_did_before tests/test_carrier_error_surfaces.py::test_the_stored_raw_payload_row_is_no_longer_a_dict_repr tests/test_carrier_error_surfaces.py::test_the_provider_assembles_its_error_from_named_fields"
  "4|a phone number quoted in error text does not block on the 21610 fragment|tests/test_blocklist_correctness.py::test_a_quoted_phone_number_is_not_a_carrier_code tests/test_blocklist_correctness.py::test_a_bare_number_in_prose_is_not_a_carrier_code tests/test_blocklist_correctness.py::test_the_removed_codes_were_not_anchored_back_into_the_fragment_list"
  "5|structured code 21610 blocks, as carrier_opt_out|tests/test_blocklist_correctness.py::test_a_structured_opt_out_code_blocks_as_a_carrier_opt_out tests/test_blocklist_correctness.py::test_the_telnyx_webhook_stores_the_code_in_its_own_column tests/test_blocklist_correctness.py::test_the_twilio_status_callback_stores_its_error_code"
  "6|a region-permission error does not block, and raises the operator signal|tests/test_carrier_error_surfaces.py::test_a_region_permission_error_does_not_block_the_recipient tests/test_carrier_error_surfaces.py::test_a_region_permission_error_raises_an_operator_signal tests/test_carrier_error_surfaces.py::test_the_operator_signal_is_never_sent_over_sms tests/test_carrier_error_surfaces.py::test_health_reports_the_configuration_alert_without_naming_a_carrier"
  "7|the blocklist opt-out figure and the dashboard opt-out rate agree|tests/test_blocklist_correctness.py::test_the_blocklist_headline_and_the_dashboard_tile_agree tests/test_blocklist_correctness.py::test_the_opt_outs_api_counts_a_carrier_opt_out_as_an_opt_out tests/test_blocklist_reasons.py::test_the_opt_out_definition_matches_the_dashboard_tile"
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

# ── 3. The classifier, before and after, on the same strings ────────────────
# Criterion 3 asks for the transient case shown blocking before the fix and not
# after. Every hazard is run through both trees rather than just that one: the
# guards are cheap insurance against wordings this account has never seen, and
# a before/after table is the only way to show that each one was a live defect
# rather than a plausible-looking rule.
step "3. the same failures through the pre-fix classifier and this one"
CLASSIFIER_PROBE="$SCRATCH/classifier_probe.py"
cat > "$CLASSIFIER_PROBE" <<'PY'
"""Run one fixed corpus through should_auto_block() and print the verdicts.

Deterministic: fixed strings, no timestamps, no database, no provider. Anything
that differs between two trees differs because the rules changed.

Written against the pre-fix signature — `should_auto_block(text)` with one
argument — so it runs unmodified on both trees. The structured-code half of the
fix cannot be probed this way and is asserted in the suite instead.
"""
import json
import os

os.environ.setdefault("SECRET_KEY", "accept-5g-scratch")
os.environ.setdefault("ADMIN_PASSWORD", "devpassword123")
os.environ["SMS_PROVIDER"] = "console"          # never live, in either tree

from app.sms.compliance import should_auto_block

CORPUS = {
    # The live corpus. These four must not change between the trees.
    "live: not routable (2,847)":
        "Not routable: The destination number is either a landline or a "
        "non-routable wireless number.",
    "live: deemed invalid (112)":
        "Invalid messaging destination number: The destination phone number "
        "was deemed invalid by the carrier.",
    "live: temporary spam (76)":
        "Blocked as spam - temporary: The message was flagged by a SPAM filter "
        "and was not delivered.",
    "live: raw payload (2)":
        "Error code: 400 - {'errors': [{'code': '10002', 'title': 'Invalid "
        "phone number', 'detail': 'Invalid destination number +13215551234.'}]}",
    # A1 — the transient guard. Every row here carries a LIVE block fragment as
    # well as a transient marker, so the guard is the only thing stopping it.
    #
    # This criterion used to be worded with "unreachable". Decision 004 removed
    # that fragment, at which point those rows stopped blocking for a different
    # reason and proved nothing about the guard — rider 1 says re-point the
    # criterion rather than keep a harmful rule to preserve a test. The
    # discriminating property is asserted directly in the suite, by
    # test_every_transient_wording_would_block_without_the_guard.
    "A1 guard: not routable, will retry":
        "Temporary routing failure: destination not routable via this route, will retry",
    "A1 guard: landline, congestion":
        "Network congestion at the landline gateway, try again later",
    "A1 guard: invalid number, retrying":
        "Invalid phone number returned by the upstream lookup; temporary failure, retrying",
    "A1 guard: deemed invalid, temporary":
        "Destination deemed invalid by the carrier - temporary validation outage, retry later",
    # Decision 004 — "unreachable" left the fragment list. These stop blocking
    # because the fragment is gone, NOT because the guard caught them: neither
    # carries a transient marker, so the guard never sees either. Listed apart
    # from the rows above for exactly that reason.
    "004 dropped: twilio 30003 verbatim": "Unreachable destination handset",
    "004 dropped: upstream outage": "SMPP bind failed - SMSC unreachable",
    # Verbatim from decision 003. It blocked at the base ref for a reason worth
    # seeing: not a word in it matched a fragment — "21612" did, inside the
    # destination number the carrier quoted. One string, both defects.
    "A2: congestion + quoted number":
        "Temporary network congestion sending to +13216125555",
    # A2 — a Brevard County number quoted in carrier prose.
    "A2: quoted brevard number": "Delivery to +13216105555 failed at the carrier",
    "A2: code inside a uuid": "Delivery report ref 9f21610a-4c21-4f0e-9d10-4d21612bfa02 pending",
    "A2: number that is not a code": "Failed after 40300 ms",
    # A5 — our own account's geo permission.
    "A5: region not enabled":
        "Permission to send an SMS has not been enabled for the region "
        "indicated by the 'To' number",
    # A4 — a fragment inside a longer word.
    "A4: fragment inside a word": "policy: landlines-are-fine for this profile",
}

print(json.dumps({name: should_auto_block(text) for name, text in CORPUS.items()},
                 indent=2, sort_keys=True))
PY

PROBE_TREE="$SCRATCH/base-tree"
git worktree remove --force "$PROBE_TREE" >/dev/null 2>&1
rm -rf "$PROBE_TREE"
if ! git worktree add --detach "$PROBE_TREE" "$BASE_REF" >/dev/null 2>&1; then
  bad "could not check out $BASE_REF to compare against"
else
  PYTHONPATH="$PWD" DATABASE_URL="sqlite:///$SCRATCH/probe-after.db" \
    "$ABS_PY" "$CLASSIFIER_PROBE" >"$SCRATCH/verdicts-after.json" 2>"$SCRATCH/verdicts-after.err"
  AFTER_RC=$?
  sh -c "cd '$PROBE_TREE' && PYTHONPATH='$PROBE_TREE' \
         DATABASE_URL='sqlite:///$SCRATCH/probe-before.db' '$ABS_PY' '$CLASSIFIER_PROBE'" \
    >"$SCRATCH/verdicts-before.json" 2>"$SCRATCH/verdicts-before.err"
  BEFORE_RC=$?

  if [[ $AFTER_RC -ne 0 || $BEFORE_RC -ne 0 ]]; then
    tail -15 "$SCRATCH/verdicts-before.err" "$SCRATCH/verdicts-after.err"
    bad "the classifier probe did not complete on both trees"
  else
    "$PY" - "$SCRATCH/verdicts-before.json" "$SCRATCH/verdicts-after.json" <<'PY'
import json
import sys

before = json.load(open(sys.argv[1]))
after = json.load(open(sys.argv[2]))

width = max(len(name) for name in before)
print(f"   {'':{width}}   before   after")
for name in sorted(before):
    mark = "  <-- changed" if before[name] != after[name] else ""
    print(f"   {name:{width}}   {str(before[name]):5}    {str(after[name]):5}{mark}")

# What must be true on the PRE-FIX tree, or the fix proves nothing.
must_have_blocked_before = [
    "A1 guard: not routable, will retry", "A1 guard: landline, congestion",
    "A1 guard: invalid number, retrying", "A1 guard: deemed invalid, temporary",
    "004 dropped: twilio 30003 verbatim", "004 dropped: upstream outage",
    "A2: congestion + quoted number", "A2: quoted brevard number",
    "A2: code inside a uuid", "A2: number that is not a code",
    "A5: region not enabled",
]
# What must be true HERE.
must_block_now = [
    "live: not routable (2,847)", "live: deemed invalid (112)",
    "live: raw payload (2)",
]
must_not_block_now = must_have_blocked_before + [
    "live: temporary spam (76)", "A4: fragment inside a word",
]

problems = []
for name in must_have_blocked_before:
    if not before[name]:
        problems.append(f"{name}: did not block at the base ref — the defect did not reproduce")
for name in must_block_now:
    if not after[name]:
        problems.append(f"{name}: stopped blocking, so real dead numbers are back on the list")
for name in must_not_block_now:
    if after[name]:
        problems.append(f"{name}: still blocks a buyer who should have survived")

for problem in problems:
    print(f"   -> {problem}")
sys.exit(1 if problems else 0)
PY
    if [[ $? -ne 0 ]]; then
      bad "the classifier does not behave as decision 003 requires"
    else
      ok "every hazard wording blocked a buyer at $BASE_REF and none does here; "\
"the three live wordings that must block still do"
    fi
  fi
fi

# ── 8. The new tests fail against the pre-fix tree ──────────────────────────
# A regression test that passes on the broken code is decoration.
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
  if [[ $? -eq 0 ]]; then
    bad "the new tests pass against $BASE_REF — they do not test the fix"
  else
    printf '%s\n' "$BEFORE_OUT" | tail -4 | sed 's/^/   /'
    ok "the same tests fail against the pre-fix tree (at import — see check 3)"
  fi

  # Those modules import symbols $BASE_REF does not have, so "they fail there"
  # is true and weak — a new module always fails against a tree that predates
  # it. Check 3 answers that for the *defect* (it runs the pre-fix function on
  # the pre-fix tree and prints the verdict it gave). Check 8b below answers it
  # for the *tests*.
  git worktree remove --force "$PROBE_TREE" >/dev/null 2>&1
fi

# ── 8b. Each fix reverted on its own, and the suite has to notice ───────────
# The question check 8 cannot answer: would these tests catch the bug coming
# back? A test that stays green through the revert of the thing it names is
# decoration, and an import error proves nothing either way.
#
# agent/mutate-5g.py reverts each of the eleven fixes behaviourally — inside the
# current API, one at a time, in a scratch copy of the tree — and requires at
# least one test to go red for each. Nothing is written inside the repo.
step "8b. eleven fixes reverted one at a time; every one must break a test"
MUTATION_TREE="$SCRATCH/mutation-tree"
rm -rf "$MUTATION_TREE"
mkdir -p "$MUTATION_TREE"
if ! rsync -a --exclude .venv --exclude node_modules --exclude .git --exclude data \
      --exclude '__pycache__' --exclude '*.db' --exclude .pytest_cache \
      ./ "$MUTATION_TREE/" 2>/dev/null; then
  bad "could not stage a scratch tree for the mutation check"
elif ! "$ABS_PY" agent/mutate-5g.py "$MUTATION_TREE" "$ABS_PY" >"$SCRATCH/mutations.log" 2>&1; then
  sed 's/^/   /' "$SCRATCH/mutations.log"
  bad "a fix was reverted and no test noticed"
else
  grep -E '^(R[0-9]|  CAUGHT)' "$SCRATCH/mutations.log" | sed 's/^/   /'
  ok "every reverted fix breaks at least one test that names it"
fi
rm -rf "$MUTATION_TREE"

# ── 9. The deployed box (opt-in) ────────────────────────────────────────────
step "9. deployed site over HTTPS, with no carrier name or raw payload"
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
      printf '%s' "$BODY" | grep -qiE "telnyx|twilio" \
        && bad "${path} names the carrier in a rendered page"
    done

    # A raw provider payload in a rendered page or an API body. The Opt-outs
    # page is where it would show: `blocked_numbers.notes` is assembled from
    # carrier error text, and two rows on this box hold a dict-repr'd response
    # body whose `detail` field is where a recipient's number appears.
    for path in /blocklist /api/blocklist; do
      BODY=$(curl -s -b "$JAR" "${A4A_URL}${path}")
      if printf '%s' "$BODY" | grep -qE "\{'errors'|\"errors\": *\[\{|'detail':"; then
        bad "${path} renders a raw provider payload"
      fi
    done
    ok "seven screens 200, no carrier name, no raw payload"

    HEALTH=$(curl -s "${A4A_URL}/health")
    echo "   /health: $HEALTH"
    printf '%s' "$HEALTH" | grep -q '"sending_ok": *true' \
      || bad "/health does not report sending_ok=true"
    printf '%s' "$HEALTH" | grep -q '"config_ok"' \
      || bad "/health does not report config_ok — the operator signal has no channel"
    printf '%s' "$HEALTH" | grep -qiE "telnyx|twilio" && bad "/health names the carrier"

    COUNTS=$(curl -s -b "$JAR" "${A4A_URL}/api/blocklist" \
      | "$PY" -c 'import json,sys; print(json.dumps(json.load(sys.stdin).get("counts")))' 2>/dev/null)
    if [[ -z "$COUNTS" || "$COUNTS" == "null" ]]; then
      bad "the live /api/blocklist returns no split counts"
    else
      echo "   live blocklist counts: $COUNTS"
    fi
    rm -f "$JAR"
  fi
fi

printf '\n'
if [[ $FAIL -eq 0 ]]; then
  echo "PART A ACCEPTANCE PASS"
else
  echo "PART A ACCEPTANCE FAILED — see above"
fi
exit $FAIL
