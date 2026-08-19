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
read -r SESSION_ID STOP_ACTIVE <<<"$(python3 - <<PY
import json
d = json.loads('''$INPUT''' or '{}')
print(d.get("session_id", "nosession"), str(d.get("stop_hook_active", False)).lower())
PY
)"

# ---- 1. Unanswered escalation? Let the agent stop; a human is the blocker. ----
if compgen -G "$DECISIONS_DIR/*.open.md" >/dev/null 2>&1; then
  PENDING=$(ls -1 "$DECISIONS_DIR"/*.open.md | head -3 | xargs -n1 basename | tr '\n' ' ')
  "$REPO_ROOT/agent/notify.sh" "[${PROJECT_NAME:-agent}] blocked on decision: ${PENDING}" || true
  exit 0
fi

# ---- 2. Loop guard: never bounce forever. ----
ATTEMPT_FILE="/tmp/gate-attempts-${SESSION_ID}"
ATTEMPTS=$(cat "$ATTEMPT_FILE" 2>/dev/null || echo 0)

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
