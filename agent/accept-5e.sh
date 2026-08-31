#!/usr/bin/env bash
# PART A ACCEPTANCE — session 5e, campaign-first flow & quality of life.
#
# The stop condition for this session. `agent/gate.sh` answers "is this repo
# still sound?"; this answers "did session 5e do what it was sent to do?", which
# is a different question and the one RULES.md requires an agent to demonstrate
# rather than declare. Each check below is one numbered criterion from
# `sessions/session-5e.md` -> "Part A acceptance", in order.
#
#   bash agent/accept-5e.sh                 checks 1-10 (all local)
#   bash agent/accept-5e.sh --with-remote   adds check 11, against the deployed box
#
# Check 11 needs the deployed site and a login, so it is opt-in and never runs by
# accident:  A4A_URL=https://... A4A_PASSWORD=... bash agent/accept-5e.sh --with-remote
#
# TURN CAP: 60 assistant turns for Part A. On exhausting it, stop and write
# `decisions/NNN-*.open.md` rather than continuing — the same discipline
# MAX_GATE_ATTEMPTS=4 applies to the gate. A session that has spent sixty turns
# and still cannot make this script exit 0 has found something a human needs to
# look at.
#
# Nothing here sends a message. SMS_PROVIDER is forced to console in every
# subprocess, no live credential is read, and no contact data is imported,
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

SCRATCH="${ACCEPT_SCRATCH:-${TMPDIR:-/tmp}/a4a-accept-5e}"
PY="${PY:-.venv/bin/python}"
[[ -x "$PY" ]] || PY="python3"
ABS_PY="$PY"; [[ "$ABS_PY" == /* ]] || ABS_PY="$PWD/$PY"

mkdir -p "$SCRATCH"

FAIL=0
step() { printf '\n── %s\n' "$1"; }
bad()  { printf 'ACCEPT FAIL: %s\n' "$1"; FAIL=1; }
ok()   { printf '   ok — %s\n' "$1"; }

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

# ── 2-9. The behaviour, exercised ───────────────────────────────────────────
# These live in the suite, where they are run on every future change rather than
# only when someone remembers this script. Naming them individually is what
# makes the acceptance criteria auditable — a criterion satisfied by "the suite
# is green" is a criterion nobody can find again.
step "2-9. the behaviour each criterion names"
declare -a CRITERIA=(
  "2|a campaign created from a CSV upload: the list is named for the campaign, the audience is that list, the preview counts match the file|tests/test_campaign_first_flow.py::test_the_campaigns_audience_is_the_list_its_own_upload_created tests/test_campaign_first_flow.py::test_the_preview_counts_match_the_file tests/test_campaign_first_flow.py::test_a_name_collision_suffixes_rather_than_erroring tests/test_campaign_first_flow.py::test_a_failed_campaign_rolls_its_import_back"
  "3|the three pre-existing audience paths still create working campaigns|tests/test_campaign_first_flow.py::test_the_three_pre_existing_audience_paths_still_create_campaigns tests/test_campaign_first_flow.py::test_the_existing_path_still_demands_a_category_or_an_override"
  "4|an untagged upload produces a campaign with category_id NULL that sends|tests/test_campaign_first_flow.py::test_an_untagged_upload_produces_a_campaign_that_sends tests/test_campaign_first_flow.py::test_a_tagged_upload_still_tags tests/test_campaign_first_flow.py::test_an_untagged_batch_can_still_be_undone tests/test_campaign_first_flow.py::test_an_ordinary_list_is_still_refused_by_undo"
  "5|a hand-added contact is normalised and blocklist-checked; a blocklisted number does not become sendable|tests/test_campaign_first_flow.py::test_a_hand_added_contact_is_normalised_and_lands_in_its_list tests/test_campaign_first_flow.py::test_a_blocklisted_number_added_by_hand_does_not_become_sendable tests/test_campaign_first_flow.py::test_an_unusable_number_added_by_hand_is_refused"
  "6|a top-up reaches only the new contacts and the totals reflect both|tests/test_topup_and_zero_send.py::test_a_top_up_reaches_only_the_new_contacts tests/test_topup_and_zero_send.py::test_the_guard_is_on_the_phone_number_not_the_contact_row tests/test_topup_and_zero_send.py::test_a_top_up_runs_the_same_pre_flight_and_refuses_before_queueing tests/test_topup_and_zero_send.py::test_only_a_campaign_that_actually_sent_can_be_topped_up tests/test_topup_and_zero_send.py::test_a_top_up_holds_back_a_recently_texted_newcomer"
  "7|the suppression window is settable in Settings and takes effect without a restart|tests/test_suppression_window.py::test_the_default_is_what_this_installation_was_deployed_with tests/test_suppression_window.py::test_setting_it_takes_effect_without_a_restart tests/test_suppression_window.py::test_the_window_actually_changes_who_is_held_back tests/test_suppression_window.py::test_the_settings_endpoint_round_trips_the_window tests/test_suppression_window.py::test_the_settings_page_carries_the_field"
  "8|with the window > 0 the composer names the held-back count and the clearing time; at 0 it says nothing|tests/test_suppression_window.py::test_the_preview_names_the_held_back_count_and_when_it_clears tests/test_suppression_window.py::test_at_a_window_of_zero_the_composer_is_told_nothing tests/test_suppression_window.py::test_the_preflight_row_quotes_the_stored_window_not_the_env_one tests/test_suppression_window.py::test_the_checklist_row_and_the_summary_panel_quote_one_window"
  "9|a campaign that reaches nobody ends aborted with a reason on the record|tests/test_topup_and_zero_send.py::test_a_fully_suppressed_campaign_aborts_with_a_reason tests/test_topup_and_zero_send.py::test_a_fully_blocklisted_campaign_aborts_naming_the_opt_out_list tests/test_topup_and_zero_send.py::test_nothing_a_zero_send_campaign_wrote_is_billable tests/test_topup_and_zero_send.py::test_a_dry_run_that_reaches_people_is_still_a_success tests/test_topup_and_zero_send.py::test_a_top_up_that_reaches_nobody_does_not_relabel_the_campaign"
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

# ── 9b. The rail renders the reason ─────────────────────────────────────────
# Criterion 9's second clause. The campaign rail is the entire UI — there is no
# detail screen — so a reason stored and not drawn is the same silence one step
# along. The API half is asserted in the suite; this is the template half, and
# it is a grep because the rail is rendered in the browser from the API payload.
step "9b. the campaign rail draws abort_reason"
if ! grep -q 'c.abort_reason' app/templates/_composer-script.html; then
  bad "the campaign rail no longer renders abort_reason"
elif ! grep -q '"abort_reason": c.abort_reason' app/routers/campaigns.py; then
  bad "the campaign payload no longer carries abort_reason"
else
  ok "the rail reads abort_reason from the payload that carries it"
fi

# ── 10. Each fix reverted on its own, and the suite has to notice ───────────
# Criterion 10, and CLAUDE.md is explicit about why it is not "the new tests
# fail against the pre-fix tree": those modules fail there at *import*, because
# the symbols they test did not exist yet. That is true, cheap and nearly
# meaningless — a new module always fails against a tree that predates it.
#
# agent/mutate-5e.py reverts each fix behaviourally — inside the current API,
# one at a time, in a scratch copy — and requires at least one test to go red for
# each. It costs about a minute and it has already earned it: the first run
# found two mutations no test noticed, and a third that was being "caught" by an
# unrelated test leaking a setting between modules.
step "10. every 5e fix reverted one at a time; each must break a test"
MUTATION_TREE="$SCRATCH/mutation-tree"
rm -rf "$MUTATION_TREE"
mkdir -p "$MUTATION_TREE"
if ! rsync -a --exclude .venv --exclude node_modules --exclude .git --exclude data \
      --exclude '__pycache__' --exclude '*.db' --exclude .pytest_cache \
      ./ "$MUTATION_TREE/" 2>/dev/null; then
  bad "could not stage a scratch tree for the mutation check"
elif ! "$ABS_PY" agent/mutate-5e.py "$MUTATION_TREE" "$ABS_PY" >"$SCRATCH/mutations.log" 2>&1; then
  sed 's/^/   /' "$SCRATCH/mutations.log"
  bad "a fix was reverted and no test noticed"
else
  grep -E '^(M[0-9]|  CAUGHT|[0-9]+ mutations)' "$SCRATCH/mutations.log" | sed 's/^/   /'
  ok "every reverted fix breaks at least one test that names it"
fi
rm -rf "$MUTATION_TREE"

# ── 11. The deployed box (opt-in) ───────────────────────────────────────────
step "11. deployed site over HTTPS, with no carrier name or raw provider payload"
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
      # The carrier's name, and the shape of a raw provider payload. Both are
      # assembled at runtime, which is exactly why a grep of the source cannot
      # find them and this reads the rendered bytes instead.
      if grep -qiE 'telnyx|twilio' "$SCRATCH/page.html"; then
        bad "${path} names the carrier"
      elif grep -qE "\{'errors'|\[\{'code'" "$SCRATCH/page.html"; then
        bad "${path} renders a raw provider payload"
      else
        ok "${path} 200, no carrier name, no raw payload"
      fi
    done

    # The API surfaces the new screens read, scanned the same way.
    for path in /api/settings/suppression /api/campaigns /api/settings/system; do
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
  echo "ACCEPT PASS — session 5e Part A"
  [[ $WITH_REMOTE -eq 0 ]] && echo "  (criterion 11 not run; it needs the deploy)"
else
  echo "ACCEPT FAILED — see above"
fi
exit $FAIL
