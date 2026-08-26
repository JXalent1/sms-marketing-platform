#!/usr/bin/env bash
# Stop hook. Fires when the agent believes it is finished.
#
# Replaces the manual "review, then tell it to continue" round trip.
# Gate passes -> the run ends. Gate fails -> exit 2 feeds stderr back to the
# agent and it keeps working. Exit 1 does NOT block; only exit 2 does.

set -uo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
# shellcheck source=/dev/null
[[ -f "$REPO_ROOT/agent.config.sh" ]] && source "$REPO_ROOT/agent.config.sh"

GATE_CMD="${GATE_CMD:-true}"
MAX_GATE_ATTEMPTS="${MAX_GATE_ATTEMPTS:-4}"
DECISIONS_DIR="${DECISIONS_DIR:-$REPO_ROOT/decisions}"

INPUT="$(cat)"

# Parsed via the environment, not by pasting stdin into a Python literal.
# The old form was `json.loads('''$INPUT''')` inside an unquoted heredoc, which
# had three ways to fail on input we do not control: a literal ''' or a
# backslash in the payload broke the quoting, `$` expanded, and — the one that
# actually bit — json.loads rejects raw control characters, so a transcript
# containing one raised JSONDecodeError. The `read` then assigned nothing,
# STOP_ACTIVE stayed empty rather than "true", and the MAX_GATE_ATTEMPTS branch
# below became unreachable: every gate failure bounced forever instead of
# escalating after four attempts. strict=False accepts the control character;
# passing the payload as an env var removes the quoting problem underneath it.
# See decisions/001-gate-interpreter-path.md.
read -r SESSION_ID STOP_ACTIVE <<<"$(HOOK_INPUT="$INPUT" python3 <<'PY'
import json
import os
import re

try:
    payload = json.loads(os.environ.get("HOOK_INPUT") or "{}", strict=False)
    if not isinstance(payload, dict):
        payload = {}
except Exception:
    payload = {}

# Whitespace would split the read below into the wrong fields, and the id
# becomes a path component. Anything unexpected is treated as no id at all.
raw_id = str(payload.get("session_id") or "")
session_id = raw_id if re.fullmatch(r"[A-Za-z0-9._-]{1,128}", raw_id) else ""

print(session_id or "-", str(payload.get("stop_hook_active", False)).lower())
PY
)"

# If python3 itself is missing or dies before printing, `read` assigns nothing
# and SESSION_ID is empty — which would make ATTEMPT_FILE a bare directory path
# and the counter write fail. Fall back to the same "no id" handling a payload
# without a session id gets.
[[ -n "${SESSION_ID:-}" ]] || SESSION_ID="-"
[[ -n "${STOP_ACTIVE:-}" ]] || STOP_ACTIVE="false"

# ---- 1. Unanswered escalation? Let the agent stop; a human is the blocker. ----
if compgen -G "$DECISIONS_DIR/*.open.md" >/dev/null 2>&1; then
  PENDING=$(ls -1 "$DECISIONS_DIR"/*.open.md | head -3 | xargs -n1 basename | tr '\n' ' ')
  "$REPO_ROOT/agent/notify.sh" "[${PROJECT_NAME:-agent}] blocked on decision: ${PENDING}" || true
  exit 0
fi

# ---- 2. Loop guard: never bounce forever. ----
# Keyed on the session id. It used to be a fixed path, because the parse above
# always failed and SESSION_ID was empty — so the counter survived across
# sessions and a fresh run was told "Attempt 22 of 4".
#
# A missing id is a fresh run, not the shared path: falling back to a common
# file is what caused that, and inheriting a stranger's count is the worse of
# the two failures. $$ is unique per invocation, so an unidentified run simply
# never accumulates.
ATTEMPT_DIR="${TMPDIR:-/tmp}/gate-attempts-${PROJECT_NAME:-agent}"
mkdir -p "$ATTEMPT_DIR"
if [[ "$SESSION_ID" == "-" ]]; then
  ATTEMPT_FILE="$ATTEMPT_DIR/anon-$$"
else
  ATTEMPT_FILE="$ATTEMPT_DIR/$SESSION_ID"
fi
ATTEMPTS=$(cat "$ATTEMPT_FILE" 2>/dev/null || echo 0)
[[ "$ATTEMPTS" =~ ^[0-9]+$ ]] || ATTEMPTS=0

# ---- 3. Run the gate. ----
GATE_OUT=$(cd "$REPO_ROOT" && eval "$GATE_CMD" 2>&1)
GATE_RC=$?

if [[ $GATE_RC -eq 0 ]]; then
  rm -f "$ATTEMPT_FILE"
  exit 0
fi

ATTEMPTS=$((ATTEMPTS + 1))
echo "$ATTEMPTS" > "$ATTEMPT_FILE"

if [[ "$STOP_ACTIVE" == "true" && $ATTEMPTS -ge $MAX_GATE_ATTEMPTS ]]; then
  rm -f "$ATTEMPT_FILE"
  mkdir -p "$DECISIONS_DIR"
  SLUG="gate-stuck-$(date +%Y%m%d-%H%M%S)"
  {
    echo "# Gate failing after ${MAX_GATE_ATTEMPTS} agent attempts"
    echo
    echo "Session: $SESSION_ID"
    echo "Command: \`$GATE_CMD\`"
    echo
    echo '```'
    printf '%s\n' "$GATE_OUT" | tail -60
    echo '```'
  } > "$DECISIONS_DIR/${SLUG}.open.md"
  "$REPO_ROOT/agent/notify.sh" "[${PROJECT_NAME:-agent}] gate stuck after ${MAX_GATE_ATTEMPTS} tries — see ${SLUG}.open.md" || true
  exit 0
fi

# ---- 4. Block the stop. stderr is what the agent reads. ----
{
  echo "The verification gate failed. You are not done."
  echo "Command: $GATE_CMD"
  echo "Attempt ${ATTEMPTS} of ${MAX_GATE_ATTEMPTS}."
  echo
  echo "Fix the root cause. Do not weaken tests, skip cases, or loosen checks to make this pass."
  echo "If the failure reveals a decision outside your authority (see CLAUDE.md), open an escalation instead."
  echo
  printf '%s\n' "$GATE_OUT" | tail -80
} >&2
exit 2
