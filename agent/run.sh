#!/usr/bin/env bash
# Driver. Runs queued modules as headless Claude Code sessions, each in its own
# git worktree, and only involves you when the gate is stuck or a decision is
# outside the agent's authority.
#
#   ./agent/run.sh              # run everything in the queue
#   ./agent/run.sh auth-refresh # run one module
#
# Auth for unattended runs: `claude setup-token` once, then export the token.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=/dev/null
source "$REPO_ROOT/agent.config.sh"

mkdir -p "$LOG_DIR" "$DECISIONS_DIR" "$WORKTREE_BASE"

notify() { "$SCRIPT_DIR/notify.sh" "$1" || true; }

# ---- Refuse to start work that is already blocked on a human. ----
if compgen -G "$DECISIONS_DIR/*.open.md" >/dev/null 2>&1; then
  OPEN=$(ls -1 "$DECISIONS_DIR"/*.open.md | xargs -n1 basename | tr '\n' ' ')
  echo "Open decisions block this run: $OPEN"
  echo "Answer them with: ./agent/decide.sh <file> '<your answer>'"
  exit 1
fi

if [[ $# -gt 0 ]]; then
  MODULES=("$@")
else
  mapfile -t MODULES < <(grep -vE '^\s*(#|$)' "$QUEUE_FILE" 2>/dev/null || true)
fi

if [[ ${#MODULES[@]} -eq 0 ]]; then
  echo "Queue is empty ($QUEUE_FILE)."
  exit 0
fi

PASSED=(); FAILED=(); ESCALATED=()

for M in "${MODULES[@]}"; do
  PROMPT_FILE="$SESSIONS_DIR/${M}.md"
  if [[ ! -f "$PROMPT_FILE" ]]; then
    echo "!! no session prompt at $PROMPT_FILE — skipping"
    FAILED+=("$M (no prompt)")
    continue
  fi

  WT="$WORKTREE_BASE/$M"
  BRANCH="agent/$M"

  echo "=== $M ==="
  git -C "$REPO_ROOT" worktree add -B "$BRANCH" "$WT" >/dev/null 2>&1 \
    || { echo "!! worktree failed"; FAILED+=("$M (worktree)"); continue; }

  # Config and hooks travel with the worktree.
  cp "$REPO_ROOT/agent.config.sh" "$WT/agent.config.sh" 2>/dev/null || true

  MODEL_ARG=(); [[ -n "${MODEL:-}" ]] && MODEL_ARG=(--model "$MODEL")

  ( cd "$WT" && claude -p "$(cat "$PROMPT_FILE")" \
      --allowedTools "$ALLOWED_TOOLS" \
      --permission-mode "$PERMISSION_MODE" \
      --output-format stream-json \
      "${MODEL_ARG[@]}" \
  ) > "$LOG_DIR/${M}.jsonl" 2>"$LOG_DIR/${M}.err"

  # Did the agent escalate rather than finish?
  if compgen -G "$WT/decisions/*.open.md" >/dev/null 2>&1; then
    cp "$WT"/decisions/*.open.md "$DECISIONS_DIR/" 2>/dev/null || true
    ESCALATED+=("$M")
    continue
  fi

  # Independent gate run — never trust the agent's own report.
  if ( cd "$WT" && eval "$GATE_CMD" ) >"$LOG_DIR/${M}.gate" 2>&1; then
    ( cd "$WT" \
        && git add -A \
        && git commit -q -m "feat($M): agent session" \
        && git push -q -u origin "$BRANCH" \
        && command -v gh >/dev/null && gh pr create --fill --head "$BRANCH" >/dev/null 2>&1 )
    PASSED+=("$M")
  else
    FAILED+=("$M")
  fi
done

SUMMARY="[${PROJECT_NAME}] passed:${#PASSED[@]} failed:${#FAILED[@]} escalated:${#ESCALATED[@]}"
[[ ${#ESCALATED[@]} -gt 0 ]] && SUMMARY+=" | decide: ${ESCALATED[*]}"
[[ ${#FAILED[@]}    -gt 0 ]] && SUMMARY+=" | red: ${FAILED[*]}"

echo "$SUMMARY"
# Only ping a human when there is something only a human can do.
if [[ ${#ESCALATED[@]} -gt 0 || ${#FAILED[@]} -gt 0 ]]; then
  notify "$SUMMARY"
fi
