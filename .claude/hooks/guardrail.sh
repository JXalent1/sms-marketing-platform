#!/usr/bin/env bash
# PreToolUse hook. The one layer with no bypass upstream of it — an SDK
# canUseTool callback is silently skipped for any tool auto-approved by
# acceptEdits or a bare allow rule, so hard rules belong here.
#
# Matchers in settings.json are CASE-SENSITIVE: "Bash", not "bash".
# Exit 2 blocks the call and returns stderr to the agent. Exit 1 does nothing.

set -uo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
# shellcheck source=/dev/null
[[ -f "$REPO_ROOT/agent.config.sh" ]] && source "$REPO_ROOT/agent.config.sh"

INPUT="$(cat)"

DENY_REASON=$(python3 - <<PY
import json, os, re, sys

try:
    d = json.loads('''$INPUT''')
except Exception:
    sys.exit(0)

tool = d.get("tool_name", "")
inp  = d.get("tool_input", {}) or {}
repo = os.path.realpath("$REPO_ROOT")

BAD_CMDS = [
    r"\bgit\s+push\b.*--force(?!-with-lease)",
    r"\bgit\s+reset\s+--hard\s+origin",
    r"\brm\s+-rf\s+/(?!tmp)",
    r"\bDROP\s+(TABLE|DATABASE)\b",
    r"\bTRUNCATE\b",
    r"\bcurl\b[^|]*\|\s*(ba)?sh",
    r"\b(vercel|fly|railway|heroku)\s+deploy\b",
    r"\bnpm\s+publish\b",
    r"\bterraform\s+apply\b",
]

PROTECTED = [".env", ".env.local", ".env.production", "id_rsa"]
PROTECTED_RE = [r"\.pem$", r"\.key$", r"^infra/prod/", r"/secrets/"]

if tool == "Bash":
    cmd = inp.get("command", "")
    for pat in BAD_CMDS:
        if re.search(pat, cmd, re.IGNORECASE):
            print(f"Blocked: command matches a protected pattern ({pat}). "
                  f"If this is genuinely required, open an escalation in decisions/.")
            sys.exit(0)

if tool in ("Edit", "Write", "NotebookEdit"):
    path = inp.get("file_path") or inp.get("path") or ""
    if path:
        real = os.path.realpath(path)
        if not real.startswith(repo):
            print(f"Blocked: write outside the repo worktree ({real}).")
            sys.exit(0)
        rel = os.path.relpath(real, repo)
        base = os.path.basename(rel)
        if base in PROTECTED or any(re.search(p, rel) for p in PROTECTED_RE):
            print(f"Blocked: {rel} is a protected path. Secrets and prod config "
                  f"are human-only. Open an escalation if a change is needed.")
            sys.exit(0)
        # A4A: the gate is not the agent's to loosen.
        if rel in ("agent/gate.sh", "agent.config.sh"):
            print("Blocked: the verification gate and its config are human-only. "
                  "If the gate is wrong, open an escalation in decisions/ explaining why.")
            sys.exit(0)
PY
)

if [[ -n "$DENY_REASON" ]]; then
  echo "$DENY_REASON" >&2
  exit 2
fi
exit 0
