#!/usr/bin/env bash
# Send a notification. Swap the body for whatever you use — this is Telnyx.
set -euo pipefail
MSG="${1:-}"

if [[ -z "${TELNYX_API_KEY:-}" || -z "${NOTIFY_TO:-}" ]]; then
  echo "[notify] $MSG" >&2
  exit 0
fi

PAYLOAD=$(python3 - "$TELNYX_FROM" "$NOTIFY_TO" "$MSG" <<'PY'
import json, sys
frm, to, text = sys.argv[1], sys.argv[2], sys.argv[3]
print(json.dumps({"from": frm, "to": to, "text": text[:1500]}))
PY
)

curl -sS -X POST https://api.telnyx.com/v2/messages \
  -H "Authorization: Bearer ${TELNYX_API_KEY}" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD" >/dev/null
