#!/usr/bin/env bash
# ACCEPTANCE — session I1, the auto-reply loop guard (incident 2026-10-09 to 10-12).
#
# Criteria 1-7 from `sessions/session-I1.md` -> "Acceptance", in order, each in
# its own pytest process (accept-5e's rule: a test that needs its neighbours
# proves nothing about the criterion it is named for). Criterion 8 is the
# behavioural mutation run: each rule reverted, one at a time, in a scratch copy
# verified pristine first, and the suite must go red for that rule — and the
# tests that go red must be the ones that name it.
#
# NOTHING HERE SENDS A MESSAGE. The provider is a counting fake in every test,
# conftest forces SMS_PROVIDER=console, and no .env is read into the scratch copy.
#
#   bash agent/accept-I1.sh

set -uo pipefail
cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)" || exit 1
export PATH="$PWD/.venv/bin:$PATH"          # decisions/001: the venv, not whatever is first
SCRATCH="${ACCEPT_SCRATCH:-$(mktemp -d -t accept-I1)}"
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

G=tests/test_auto_reply_guard.py
W=tests/test_auto_reply_webhooks.py

head_ "1. one unit test per rule"
t "1a per-number 24h cap"          "$G" -k "per_number or suppressed_reply_does_not_extend"
t "1b phrase filter (each phrase x 4 spellings)" "$G" -k "phrase or automated or incident_text"
t "1c blocked-number rule"         "$G" -k "blocked or opt_out_after"
t "1d circuit breaker: trips at 50, recovers after the hour (frozen clock)" "$G" -k "circuit_breaker"
t "1e claim before send; failure leaves the row; concurrent claims" "$G" -k "claim"
t "1f review F2/F4: invalid and non-US senders, config bounds" "$G" -k "senders_a_campaign or region_rule or refuses_to_boot or cap_of_zero"

head_ "2. delivery events: provider send called 0 times, guard never reached"
t "2a telnyx message.sent/.delivered/.failed/.finalized" "$W" -k "telnyx_delivery_events"
t "2b twilio status callback"      "$W" -k "twilio_status_callback"
t "2c known answer: the counters do count an inbound send" "$W" -k "no_send_fixture"

head_ "3. 1,000 x message.received to /webhooks/telnyx, alternating the two responders"
t "3a phrase filter on: 0 sends, 1,000 log rows"   "$W" -k "telnyx_replay_with_the_phrase"
t "3b phrase list emptied: 2 sends (one per number), 1,000 log rows" "$W" -k "telnyx_replay_without"

head_ "4. same replay through /webhooks/twilio/sms"
t "4a phrase filter on: 0 replies, 1,000 empty TwiML, 1,000 log rows" "$W" -k "twilio_replay_with_the_phrase"
t "4b phrase list emptied: 2 replies, 998 empty TwiML" "$W" -k "twilio_replay_without"

head_ "5. normal path: first question 1 reply, second within 24h 0"
t "criterion 5" "$W" -k "first_time_question"

head_ "6. STOP x20 from one number: blocked, exactly 1 confirmation"
t "criterion 6 (telnyx, STOP)" "$W" -k "stop_twenty_times"
t "criterion 6 (twilio, the client's opt-out word 2)" "$W" -k "opt_out_word_2"

head_ "7. full suite; alembic from clean; css build; gate green twice"
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
if npm run build:css >"$SCRATCH/css.out" 2>&1; then
  pass "npm run build:css"
else
  fail "npm run build:css"; tail "$SCRATCH/css.out"
fi
echo "        (no linter is configured in this repo — CLAUDE.md, 'How to verify work')"
for run in 1 2; do
  if bash agent/gate.sh >"$SCRATCH/gate$run.out" 2>&1; then
    pass "gate run $run"; tail -3 "$SCRATCH/gate$run.out" | sed 's/^/        /'
  else
    fail "gate run $run"; tail -30 "$SCRATCH/gate$run.out"
  fi
done

head_ "8. mutation run: each rule reverted, the suite must go red for it"
python - "$SCRATCH" <<'PY' || FAILED=1
import filecmp, os, pathlib, re, shutil, subprocess, sys

repo = pathlib.Path.cwd()
tree = pathlib.Path(sys.argv[1]) / "mutate-tree"
shutil.rmtree(tree, ignore_errors=True)
skip = shutil.ignore_patterns(".git", ".venv", "node_modules", "data", "__pycache__",
                              ".env", "*.db", "logs")
shutil.copytree(repo, tree, ignore=skip)

# Precondition 1: the scratch tree is byte-identical to the repo for every
# source file a mutation or a test can read. A leftover edit here makes every
# verdict below a verdict about that edit (P1's 38/0 that measured nothing).
dirty = []
for sub in ("app", "tests", "alembic"):
    for src in (repo / sub).rglob("*.py"):
        if "__pycache__" in src.parts:
            continue
        dst = tree / src.relative_to(repo)
        if not dst.exists() or not filecmp.cmp(src, dst, shallow=False):
            dirty.append(str(src.relative_to(repo)))
if dirty:
    print("  FAIL  scratch tree is not pristine:", dirty[:5]); sys.exit(1)
print("  SCRATCH VERIFIED PRISTINE")

G, W = "tests/test_auto_reply_guard.py", "tests/test_auto_reply_webhooks.py"
GUARD, TEL, TWI = ("app/services/auto_reply_guard.py", "app/routers/webhooks/telnyx.py",
                   "app/routers/webhooks/twilio.py")
COMMON, COMP = "app/routers/webhooks/common.py", "app/sms/compliance.py"

# (id, file, old, new, pytest selection, a test name that MUST be among the failures)
M = [
 ("M1 telnyx sends whatever the guard says", TEL,
  "            if decision.allowed:\n", "            if True:\n",
  [W, "-k", "replay or first_time or stop_twenty"], "telnyx_replay_with_the_phrase"),
 # Not `if False:` — that hands _twiml() a None, which raises into the route's
 # except and returns empty TwiML anyway: an equivalent mutation, not a revert.
 ("M2 twilio replies whatever the guard says", TWI,
  "        if not decision.allowed:\n            return Response(content=EMPTY_TWIML, media_type=\"application/xml\")\n        return Response(content=_twiml(decision.reply)",
  "        return Response(content=_twiml(decision.reply or 'the proposed reply')",
  [W, "-k", "twilio_replay or opt_out_word_2"], "twilio_replay_with_the_phrase"),
 ("M3 phrase filter off", COMP,
  "    return any(phrase.casefold() in text for phrase in AUTOMATED_SENDER_PHRASES)",
  "    return False", [G, W, "-k", "phrase or automated or replay_with"],
  "each_automated_phrase"),
 ("M4 per-number cap removed", GUARD,
  "        return PER_NUMBER_CAP\n", "        pass\n",
  [G, W, "-k", "per_number or replay_without or first_time"], "per_number_cap_allows"),
 ("M5 per-number cap counts suppressions too", GUARD,
  "                      AutoReplyLog.decision == \"sent\",\n                      AutoReplyLog.created_at > now - window",
  "                      AutoReplyLog.created_at > now - window",
  [G, "-k", "per_number or suppressed_reply"], "suppressed_reply_does_not_extend"),
 ("M6 per-number window hardcoded", GUARD,
  "timedelta(hours=settings.AUTO_REPLY_PER_NUMBER_HOURS)", "timedelta(hours=24)",
  [G, "-k", "per_number"], "per_number_window_is_read_from_config"),
 ("M7 blocked numbers answered", GUARD,
  "            return BLOCKED_NUMBER\n", "            pass\n",
  [G, W, "-k", "blocked or stop_twenty or opt_out_word"], "blocked_number_gets_no_reply"),
 ("M8 every STOP to a blocked number confirmed", GUARD,
  "if kind != \"stop\" or _stop_already_confirmed(earlier, row.phone):",
  "if kind != \"stop\":", [G, "-k", "blocked or opt_out"], "blocked_number_gets_no_reply"),
 ("M9 STOP confirmation 'ever', not since last START", GUARD,
  "    if last_start:\n", "    if False:\n", [G, "-k", "opt_out_after"], "opt_out_after_resubscribing"),
 ("M10 circuit breaker removed", GUARD,
  "        return CIRCUIT_BREAKER\n", "        pass\n", [G, "-k", "circuit_breaker"],
  "circuit_breaker_trips"),
 ("M11 breaker trips silently (no ERROR, no /health)", GUARD,
  "    if reason == CIRCUIT_BREAKER:\n        _trip(db, row)\n    elif reason:",
  "    if reason:", [G, "-k", "circuit_breaker"], "circuit_breaker_trips"),
 ("M12 breaker cap hardcoded", GUARD,
  "if sent_this_hour >= settings.AUTO_REPLY_HOURLY_CAP:", "if sent_this_hour >= 50:",
  [G, "-k", "circuit_breaker"], "circuit_breaker_cap_is_read_from_config"),
 ("M13 decide before the INSERT (no write lock)", GUARD,
  "        db.add(row)\n        db.flush()                      # INSERT: the write lock is ours until commit\n        reason = _refusal(db, row, kind)",
  "        reason = _refusal(db, row, kind)\n        db.add(row)\n        db.flush()",
  [G, "-k", "concurrent"], "concurrent_claims"),
 ("M14 claim not committed before the send", COMMON,
  "    return auto_reply_guard.claim(db, from_number, body, kind, reply)",
  "    from unittest import mock\n    with mock.patch.object(db, 'commit', db.flush):\n        return auto_reply_guard.claim(db, from_number, body, kind, reply)",
  [G, "-k", "committed_before_the_send"], "committed_before_the_send"),
 ("M15 send failure deletes the claim", GUARD,
  "            row.reason = f\"send_failed: {scrub_provider_text(detail or 'unknown')}\"[:200]",
  "            db.delete(row)", [G, "-k", "committed_before_the_send"], "committed_before_the_send"),
 ("M16 send failure not recorded", TEL,
  "                        record_send_failure(db, decision.log_id, result.error)\n", "",
  [G, "-k", "committed_before_the_send"], "reports_failure"),
 ("M18 non-US senders answered (review F2)", GUARD,
  "        reason = NON_US_SENDER\n", "        pass\n",
  [G, "-k", "senders_a_campaign or region_rule"], "senders_a_campaign_would_not_text"),
 ("M19 short codes and sender IDs answered (review F2)", GUARD,
  "        reason = INVALID_SENDER\n", "        pass\n",
  [G, "-k", "senders_a_campaign or region_rule"], "senders_a_campaign_would_not_text"),
 ("M20 per-number window may be 0 (review F4)", "app/core/config.py",
  "AUTO_REPLY_PER_NUMBER_HOURS: int = Field(24, ge=1)", "AUTO_REPLY_PER_NUMBER_HOURS: int = Field(24)",
  [G, "-k", "refuses_to_boot"], "refuses_to_boot"),
 ("M17 delivery events reach the guard", TEL,
  "        if event_type in DELIVERY_EVENTS:\n",
  "        if event_type in DELIVERY_EVENTS:\n            handle_inbound(db, payload.get('id', ''), '')\n",
  [W, "-k", "delivery_events"], "telnyx_delivery_events"),
]

# Precondition 2: every anchor resolves exactly once before the first patch is
# applied (B1's lesson — a stale anchor discovered lazily is a slow drip, and a
# patch that does not apply must never read as a pass).
bad = [m[0] for m in M if (tree / m[1]).read_text().count(m[2]) != 1]
if bad:
    print("  FAIL  anchors that do not resolve exactly once:", bad); sys.exit(1)
print(f"  ANCHORS VERIFIED ({len(M)})")

py = str(repo / ".venv" / "bin" / "python")
env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
ok = True
for name, rel, old, new, sel, must in M:
    path = tree / rel
    original = path.read_text()
    path.write_text(original.replace(old, new, 1))
    runs = 3 if "concurrent" in must else 1      # a race may need more than one try
    failed = set()
    for _ in range(runs):
        r = subprocess.run([py, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-rf", *sel],
                           cwd=tree, env=env, capture_output=True, text=True)
        failed |= set(re.findall(r"^FAILED \S+::(\S+)", r.stdout, re.M))
        if failed:
            break
    path.write_text(original)
    if failed and any(must in f for f in failed):
        print(f"  CAUGHT    {name}  <- {len(failed)} test(s), incl. {must}")
    elif failed:
        ok = False
        print(f"  WRONG     {name}  <- red, but not for the test that names it: {sorted(failed)[:3]}")
    else:
        ok = False
        print(f"  SURVIVED  {name}")
sys.exit(0 if ok else 1)
PY

echo
if [ "$FAILED" -eq 0 ]; then echo "ACCEPT-I1: ALL CRITERIA PASS"; else echo "ACCEPT-I1: FAILED"; fi
exit "$FAILED"
