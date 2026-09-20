#!/usr/bin/env bash
# PART A ACCEPTANCE — session 5n, upload-mode template metrics and cancelling a
# scheduled campaign.
#
# The stop condition for this session. `agent/gate.sh` answers "is this repo
# still sound?"; this answers "did session 5n do what it was sent to do?", which
# RULES.md requires an agent to demonstrate rather than declare. Checks 1-10 are
# the numbered criteria from `sessions/session-5n.md` -> "Acceptance", in order;
# the lettered checks *show* the thing the numbered check asserts, so a reader
# sees the figure rather than a tick.
#
#   bash agent/accept-5n.sh
#
# ── Nothing here sends ──────────────────────────────────────────────────────
#
# SMS_PROVIDER is console in every subprocess, and `tests/conftest.py` forces it
# for the whole suite. Nothing here reaches a paid API.
#
# What it DOES do to your machine: writes scratch files under $ACCEPT_SCRATCH,
# and for checks 7b and 10 rsyncs a throwaway copy of the tree there. It writes
# nothing inside the repo.
#
# TURN CAP: 40 assistant turns. On exhausting it, stop and write
# `decisions/NNN-*.open.md` rather than continuing.

set -uo pipefail
cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)" || exit 1

SCRATCH="${ACCEPT_SCRATCH:-${TMPDIR:-/tmp}/a4a-accept-5n}"
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

PANEL=tests/test_composer_panel.py
CANCEL=tests/test_cancel_scheduled.py

# ── 0. node is present, because half of this session is JavaScript ──────────
step "0. the composer's message row is JavaScript, so node has to be here"
if command -v node >/dev/null 2>&1; then
  ok "node $(node --version) — npm is already a build dependency (npm run build:css)"
else
  bad "node is missing; the panel tests cannot run and nothing else proves A1"
fi

# ── 1. Characters, Encoding, Segments/msg populate in upload mode ──────────
step "1. the three message-derived figures are live under the upload tab"
run_tests "driven through the real partials in node, with no audience anywhere" \
  $PANEL::test_upload_mode_measures_the_template_and_admits_what_it_does_not_know \
  $PANEL::test_carrying_a_message_to_the_upload_tab_asks_about_it_again

# ── 2. Recipients, Total segments, Estimated cost read as unknown, not 0 ───
step "2. the three audience-derived figures say they do not know"
run_tests "an em dash on both surfaces, and null rather than 0 over the API" \
  $PANEL::test_upload_mode_measures_the_template_and_admits_what_it_does_not_know \
  $PANEL::test_switching_to_upload_retires_the_replies_still_in_flight \
  $PANEL::test_preview_without_an_audience_measures_the_message_and_admits_the_rest

# ── 3. The UCS-2 warning fires in upload mode ──────────────────────────────
step "3. one emoji, upload tab, and the warning is on screen"
run_tests "shown, worded per recipient, with no recipient count or dollar figure invented" \
  $PANEL::test_the_unicode_warning_fires_under_the_upload_tab

# ── 4. No second segment calculation ───────────────────────────────────────
step "4. the figure on screen is count_sms_segments(), via the server"
run_tests "asserted against the fixture reply, the counter itself, and the naive figure" \
  $PANEL::test_the_segment_figure_on_screen_is_count_sms_segments_via_the_server

step "4b. shown: the two counters disagree on the fixture message, so the screen can only match one"
SMS_PROVIDER=console "$PY" - <<'PY'
import sys; sys.path.insert(0, ".")
from app.sms.segments import count_segments
from app.services import link_service
from tests.test_composer_panel import EMOJI_MESSAGE
text = link_service.for_counting(EMOJI_MESSAGE)
print(f"   message        : {EMOJI_MESSAGE!r}")
print(f"   characters     : {len(text)}")
print(f"   count_segments : {count_segments(text)}   <- the server's, and what the row shows")
print(f"   len / 160      : {-(-len(text) // 160)}   <- what a browser-side count would print")
PY

# ── 5. Cancel from the rail; not due afterwards ────────────────────────────
step "5. cancel from the campaign rail, and the scheduler cannot pick it up"
run_tests "the control on the scheduled draft only, posting to its own route; then never due" \
  $PANEL::test_the_rail_offers_cancel_on_a_scheduled_draft_and_posts_to_its_route \
  $CANCEL::test_cancel_clears_the_schedule_and_leaves_an_editable_draft \
  $CANCEL::test_a_cancelled_campaign_is_never_due

# ── 6. Anything that has started refuses, naming the state ─────────────────
step "6. running, completed, aborted, failed — each refused with its own sentence"
run_tests "409 with the state named in his units; a never-scheduled draft too" \
  $CANCEL::test_a_campaign_that_has_started_refuses_cancellation \
  $CANCEL::test_a_draft_that_was_never_scheduled_has_nothing_to_cancel \
  $CANCEL::test_an_unknown_campaign_is_a_404

step "6b. shown: the sentences"
SMS_PROVIDER=console "$PY" - <<'PY'
import sys; sys.path.insert(0, ".")
from app.services.campaign_dispatch import CANCEL_STATE_ERRORS, NOT_SCHEDULED
for state, wording in CANCEL_STATE_ERRORS.items():
    print(f"   {state:<10} {wording.format(status=state, sent=1, total=443)}")
print(f"   {'unscheduled':<10} {NOT_SCHEDULED}")
PY

# ── 7. The race ────────────────────────────────────────────────────────────
step "7. cancelled between selection and dispatch: nothing sends"
run_tests "injected at the seam, and concurrently on the loop while an earlier campaign sends" \
  $CANCEL::test_a_cancel_between_selection_and_dispatch_does_not_send \
  $CANCEL::test_a_cancel_during_an_earlier_campaigns_send_does_not_send_the_later_one \
  $CANCEL::test_cancel_decides_on_the_row_as_it_stands_not_the_object_it_read

step "7a. and in the gap after the re-check: a cancel during the campaign's own pre-flight"
# Each criterion-7 test also passes on its own: a test that needs its
# neighbours to have behaved proves nothing about the criterion it is named for.
run_tests "the flip to running is a claim — from the loop, from a thread, and on a double click" \
  $CANCEL::test_a_cancel_during_the_campaigns_own_preflight_is_honoured \
  $CANCEL::test_two_runs_that_both_read_a_draft_cannot_both_send_it
for t in test_a_cancel_between_selection_and_dispatch_does_not_send \
         test_a_cancel_during_an_earlier_campaigns_send_does_not_send_the_later_one \
         test_run_due_campaigns_reports_only_what_it_dispatched; do
  if SMS_PROVIDER=console PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "$PY" -m pytest "$CANCEL::$t" -q --no-header >/dev/null 2>&1; then
    ok "$t passes in isolation"
  else
    bad "$t fails when run alone"
  fi
done

step "7b. and the criterion-7 tests go red on the tree they exist to reject"
# The pre-fix arrangement, reproduced inside the current API: the post-selection
# re-check removed from a scratch copy. Judged by the sentence each test names,
# never by the exit code alone — an import error or a fixture failing its own
# precondition is also a non-zero exit, and "the new test fails against the old
# tree" proves nothing when it fails for a reason other than the defect
# (CLAUDE.md; the 5n review found the second race test doing exactly that when
# run after the first). Each test runs alone.
PRE="$SCRATCH/prefix"
rm -rf "$PRE"; mkdir -p "$PRE"
rsync -a --exclude '.venv' --exclude 'node_modules' --exclude '__pycache__' \
      --exclude '.git' --exclude 'data' --exclude 'logs' ./ "$PRE/"
"$PY" - "$PRE" <<'PY'
import pathlib, sys
f = pathlib.Path(sys.argv[1]) / "app/services/campaign_dispatch.py"
s = f.read_text()
old = "            if not still_scheduled(db, campaign_id):\n"
assert s.count(old) == 1, "the re-check moved; fix this check"
f.write_text(s.replace(old, "            if False:\n"))
print("   re-check removed from the scratch copy")
PY
red_for_the_named_reason() {   # red_for_the_named_reason <tree> <test> <sentence>
  local tree="$1" test="$2" sentence="$3" out
  out=$(cd "$tree" && SMS_PROVIDER=console PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
        "$ABS_PY" -m pytest "$test" -q --no-header -p no:cacheprovider --tb=short 2>&1)
  if grep -q "1 passed" <<<"$out"; then
    bad "$test passes without the guard — it proves nothing"
  elif grep -qF "$sentence" <<<"$out"; then
    ok "$(basename "$test") red for the named reason: \"$sentence\""
  else
    printf '%s\n' "$out" | tail -8
    bad "$test is red, but not for the reason it names"
  fi
}
red_for_the_named_reason "$PRE" "$CANCEL::test_a_cancel_between_selection_and_dispatch_does_not_send" \
  "the scheduler reported sending a cancelled campaign"
red_for_the_named_reason "$PRE" "$CANCEL::test_a_cancel_during_an_earlier_campaigns_send_does_not_send_the_later_one" \
  "the cancelled campaign was dispatched"

step "7c. and the claim tests go red with the flip made unconditional"
CLAIMLESS="$SCRATCH/claimless"
rm -rf "$CLAIMLESS"; mkdir -p "$CLAIMLESS"
rsync -a --exclude '.venv' --exclude 'node_modules' --exclude '__pycache__' \
      --exclude '.git' --exclude 'data' --exclude 'logs' ./ "$CLAIMLESS/"
"$PY" - "$CLAIMLESS" <<'PY'
import pathlib, sys
f = pathlib.Path(sys.argv[1]) / "app/services/campaign_claim.py"
s = f.read_text()
start = s.index("    loaded_schedule = campaign.scheduled_at\n")
end = s.index("    if taken != 1:\n")
f.write_text(s[:start] + '    campaign.status = "running"\n'
             '    campaign.started_at = datetime.now().isoformat()\n'
             '    db.commit()\n    taken = 1\n' + s[end:])
print("   flip made unconditional in the scratch copy")
PY
red_for_the_named_reason "$CLAIMLESS" \
  "$CANCEL::test_a_cancel_during_the_campaigns_own_preflight_is_honoured[False-True]" \
  "sent after a cancel"
red_for_the_named_reason "$CLAIMLESS" \
  "$CANCEL::test_two_runs_that_both_read_a_draft_cannot_both_send_it" \
  "the second run relabelled a completed campaign as aborted"

# ── 8. Cancelling is not deleting ──────────────────────────────────────────
step "8. the campaign, its name, its audience and its rows are all still there"
run_tests "asserted field by field after the cancel" \
  $CANCEL::test_cancel_clears_the_schedule_and_leaves_an_editable_draft \
  $CANCEL::test_run_due_campaigns_reports_only_what_it_dispatched

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
if "$PY" agent/mutate-5n.py "$TREE" "$ABS_PY" 2>&1 | tee "$SCRATCH/mutate.log" \
   | grep -E "PRISTINE|ANCHORS|NOT CAUGHT|survived|did not apply"; then
  ok "no guard reverted without a test noticing (full log: $SCRATCH/mutate.log)"
else
  bad "a mutation survived, or the harness refused to run — see $SCRATCH/mutate.log"
fi

printf '\n'
if [[ $FAIL -eq 0 ]]; then
  echo "ACCEPT 5n: PASS"
else
  echo "ACCEPT 5n: FAILED — see above"
fi
exit $FAIL
