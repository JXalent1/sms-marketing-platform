#!/usr/bin/env bash
# Answer an open escalation. This is the half of the loop you do from your phone.
#
#   ./agent/decide.sh 003-settlement-retry "Option B. Idempotency key on the
#   operator contract, not the tx hash. Do not change the ABI."
#
# The file flips from .open.md to .answered.md and the next run picks it up.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=/dev/null
source "$REPO_ROOT/agent.config.sh"

if [[ $# -lt 2 ]]; then
  echo "Open decisions:"
  ls -1 "$DECISIONS_DIR"/*.open.md 2>/dev/null | xargs -n1 basename || echo "  (none)"
  echo
  echo "Usage: ./agent/decide.sh <slug> '<answer>'"
  exit 1
fi

SLUG="${1%%.open.md}"
ANSWER="$2"
FILE="$DECISIONS_DIR/${SLUG}.open.md"

[[ -f "$FILE" ]] || { echo "No open decision: $SLUG"; exit 1; }

{
  echo
  echo "## Decision — $(date -u '+%Y-%m-%d %H:%M UTC')"
  echo
  echo "$ANSWER"
} >> "$FILE"

mv "$FILE" "$DECISIONS_DIR/${SLUG}.answered.md"
echo "Answered: ${SLUG}"
echo "Run: ./agent/run.sh  (blocked modules will resume)"
