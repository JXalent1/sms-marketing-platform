#!/usr/bin/env bash
# PART A ACCEPTANCE — session 5c.
#
# The stop condition for this session. `agent/gate.sh` answers "is the repo
# still sound?"; this answers "did session 5c do what it was sent to do?", which
# is a different question and the one RULES.md requires an agent to demonstrate
# rather than declare. Each check below is one numbered criterion from
# `sessions/session-5c.md` → "Part A acceptance", in order.
#
#   bash agent/accept-5c.sh                 checks 1-5 (everything local)
#   bash agent/accept-5c.sh --with-remote   adds check 6, against the live box
#
# Check 6 needs the deployed site and a login, so it is opt-in and never runs by
# accident:  A4A_URL=https://... A4A_PASSWORD=... bash agent/accept-5c.sh --with-remote
#
# TURN CAP: 60 assistant turns for Part A. On exhausting it, stop and write
# `decisions/NNN-*.open.md` rather than continuing — the same discipline
# MAX_GATE_ATTEMPTS=4 applies to the gate. A session that has spent sixty turns
# and still cannot make this script exit 0 has found something a human needs to
# look at.
#
# Nothing here sends a message. The carrier provider is constructed but never
# used, exactly as in tests/_provider_setup.py.
#
# What it DOES do to your machine, so nobody is surprised: builds two throwaway
# venvs under $ACCEPT_SCRATCH (a few hundred MB, cached between runs, rebuilt
# with --rebuild-venvs), and adds and removes a detached git worktree at
# $BASE_REF for check 5. It writes nothing inside the repo except that worktree,
# which it removes on the way out.

set -uo pipefail
cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)" || exit 1

WITH_REMOTE=0
REBUILD=0
for arg in "$@"; do
  case "$arg" in
    --with-remote) WITH_REMOTE=1 ;;
    --rebuild-venvs) REBUILD=1 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

# The commit session 5c started from. Check 5 runs the new tests against this
# tree to prove they fail on the code that shipped the bug.
BASE_REF="${BASE_REF:-0e89818}"
SCRATCH="${ACCEPT_SCRATCH:-${TMPDIR:-/tmp}/a4a-accept-5c}"
PY="${PY:-.venv/bin/python}"
[[ -x "$PY" ]] || PY="python3"

mkdir -p "$SCRATCH"

FAIL=0
step() { printf '\n── %s\n' "$1"; }
bad()  { printf 'ACCEPT FAIL: %s\n' "$1"; FAIL=1; }
ok()   { printf '   ok — %s\n' "$1"; }

build_venv() {   # build_venv <dir> <requirements file>
  local dir="$1" reqs="$2"
  if [[ $REBUILD -eq 1 || ! -x "$dir/bin/python" ]]; then
    rm -rf "$dir"
    python3 -m venv "$dir" || return 1
    "$dir/bin/pip" install -q --disable-pip-version-check -r "$reqs" || return 1
    "$dir/bin/pip" install -q --disable-pip-version-check pytest==9.1.1 || return 1
  fi
}

# ── 1. The gate ─────────────────────────────────────────────────────────────
# The gate calls bare `python` and `alembic`, so it runs against whatever is
# first on PATH. On a machine whose default python is a conda base env it dies
# at collection with ModuleNotFoundError and reports "test suite is red", which
# is a true statement about the wrong interpreter. Put the project venv in front
# of it. This changes nothing the gate checks.
step "1. agent/gate.sh"
GATE_PATH="$PATH"
[[ -x .venv/bin/python ]] && GATE_PATH="$PWD/.venv/bin:$PATH"
if ! PATH="$GATE_PATH" bash agent/gate.sh; then
  bad "the gate did not pass"
else
  ok "all six gate checks"
fi

# ── 2. A clean venv from requirements.txt can drive the provider ────────────
# The bug was a pin, so the check has to start from the pin. Installing into
# this repo's existing venv would prove nothing: it is already patched.
step "2. clean venv from requirements.txt"
if ! build_venv "$SCRATCH/cleanvenv" requirements.txt; then
  bad "could not build a clean venv from requirements.txt"
else
  CLEAN_PY="$SCRATCH/cleanvenv/bin/python"
  # Credentials are deliberately fake and the provider is only constructed —
  # `telnyx.Telnyx(api_key=...)` opens no connection. See the note in
  # tests/_provider_setup.py.
  if ! SMS_PROVIDER=telnyx \
       TELNYX_API_KEY="KEY_NOT_A_REAL_CREDENTIAL_test_only" \
       TELNYX_PHONE_NUMBER="+15555550140" \
       "$CLEAN_PY" - <<'PY'
import importlib.metadata as md
import sys

version = md.version("telnyx")
if version != "4.175.0":
    sys.exit(f"requirements.txt installed telnyx {version}, expected 4.175.0")

from app.sms.providers.telnyx import TelnyxProvider

provider = TelnyxProvider()          # this is what raised under the old pin
print(f"   telnyx {version}; {type(provider).__name__} constructed "
      f"(client: {type(provider.client).__name__})")

# The four API facts app/sms/providers/telnyx.py depends on. A future SDK bump
# that keeps the class but moves any of these breaks sending just as silently.
import inspect
send = set(inspect.signature(provider.client.messages.send).parameters)
for name in ("to", "from_", "text", "messaging_profile_id"):
    if name not in send:
        sys.exit(f"messages.send() has no '{name}' parameter")
from telnyx.types import MessageSendResponse
from telnyx.types.messaging_outbound_message_payload import MessagingOutboundMessagePayload
if "data" not in MessageSendResponse.model_fields:
    sys.exit("MessageSendResponse has no .data")
for name in ("id", "parts"):
    if name not in MessagingOutboundMessagePayload.model_fields:
        sys.exit(f"the send response payload has no .{name}")
print("   messages.send(to, from_, text, messaging_profile_id) -> .data.id / .data.parts")
PY
  then
    bad "a clean install of requirements.txt cannot construct the carrier provider"
  else
    ok "clean venv installs 4.175.0 and the provider constructs against it"
  fi
fi

# ── 3-4. Provider selection and the degraded state, rendered ────────────────
step "3-4. provider selection, and a failed provider that says so"
ACCEPT_DB="$SCRATCH/accept.db" "$PY" - <<'PY'
import os
import re
import sys

# Set before importing the app: Settings is built at import of app.core.config.
os.environ["DATABASE_URL"] = f"sqlite:///{os.environ['ACCEPT_DB']}"
os.environ["SMS_PROVIDER"] = "console"      # the suite's own rule: never live
os.environ.setdefault("SECRET_KEY", "accept-5c-scratch")
os.environ.setdefault("ADMIN_PASSWORD", "devpassword123")
os.environ["COOKIE_SECURE"] = "false"

for suffix in ("", "-wal", "-shm"):
    try:
        os.unlink(os.environ["ACCEPT_DB"] + suffix)
    except FileNotFoundError:
        pass

from alembic import command
from alembic.config import Config

cfg = Config()
cfg.set_main_option("script_location", os.path.join(os.getcwd(), "alembic"))
command.upgrade(cfg, "head")

from fastapi.testclient import TestClient

from app.main import app
from app.sms import factory
from tests._provider_setup import carrier_provider, degraded_provider

CARRIER = re.compile(r"telnyx|twilio", re.IGNORECASE)
PAGES = ("/dashboard", "/campaigns", "/contacts", "/blocklist", "/usage", "/settings")
PILL = re.compile(r'id="sendModePill"[^>]*data-mode="([a-z_]+)"')

failures = []


def check(condition, message):
    if not condition:
        failures.append(message)


client = TestClient(app)
login = client.post("/login", data={"username": "admin", "password": os.environ["ADMIN_PASSWORD"]})
if login.status_code not in (200, 302):
    sys.exit(f"login failed with {login.status_code}; every check below would be vacuous")

# 3. SMS_PROVIDER names a carrier and a key is set -> that provider is used.
with carrier_provider():
    provider = factory.get_provider()
    check(provider.name != "console",
          "SMS_PROVIDER=telnyx with a key set still resolves to the console provider")
    check(type(provider).__name__ == "TelnyxProvider",
          f"expected TelnyxProvider, got {type(provider).__name__}")
    check(factory.send_mode().key == "live",
          f"send mode is {factory.send_mode().key!r} with a working carrier")
print("   3. configured carrier is the provider that gets used")

# 4. A broken credential is visibly different from a chosen dry run.
calm_json = client.get("/api/settings/system").json()
calm_html = client.get("/settings").text
check(calm_json["send_mode"] == "dry_run", "a deliberate dry run no longer reports itself as one")
check(PILL.search(calm_html).group(1) == "dry_run", "the pill lost its dry-run state")

with degraded_provider():
    broken_json = client.get("/api/settings/system").json()
    broken_html = client.get("/settings").text
    fallback = factory.provider_fallback()

    check(fallback is not None, "the fallback was not recorded anywhere")
    check(CARRIER.search(fallback.error) is not None,
          "the simulated failure does not name the carrier, so the leak check proves nothing")
    check(broken_json["send_mode"] == "unavailable",
          f"settings payload reports {broken_json['send_mode']!r}, not 'unavailable'")
    check(broken_json["dry_run"] is False, "a failed carrier is still reported as a dry run")
    check(broken_json["send_mode_label"] != calm_json["send_mode_label"],
          "the failed state and the chosen dry run share a label")
    check("sendingUnavailableBanner" in broken_html, "the Settings page shows no degraded banner")

    for path in PAGES:
        html = client.get(path).text
        found = PILL.search(html)
        check(found is not None and found.group(1) == "unavailable",
              f"{path}: pill does not show the degraded state")
        check(not CARRIER.search(html), f"{path}: leaked the carrier name while degraded")
    check(not CARRIER.search(client.get("/api/settings/system").text),
          "/api/settings/system leaked the carrier name while degraded")

print("   4. degraded state is distinct from dry run on the pill, the page and the API")
print("      and names no carrier")

for suffix in ("", "-wal", "-shm"):
    try:
        os.unlink(os.environ["ACCEPT_DB"] + suffix)
    except FileNotFoundError:
        pass

if failures:
    for message in failures:
        print(f"   -> {message}")
    sys.exit(1)
PY
if [[ $? -ne 0 ]]; then
  bad "provider selection or the degraded state did not behave as specified"
else
  ok "criteria 3 and 4"
fi

# ── 5. The new tests fail against the code that shipped the bug ─────────────
# A regression test that passes on the broken code is decoration. This runs the
# new module against BASE_REF, with a venv built from BASE_REF's own
# requirements.txt — the old pin included, since the old pin is half the bug.
step "5. new tests pass here and fail at $BASE_REF"
if ! PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "$PY" -m pytest tests/test_provider_status.py -q >/dev/null 2>&1; then
  bad "tests/test_provider_status.py does not pass against the current tree"
else
  ok "new tests pass here"
fi

PREFIX_TREE="$SCRATCH/prefix-tree"
git show "$BASE_REF:requirements.txt" > "$SCRATCH/requirements-prefix.txt" 2>/dev/null \
  && printf -- "-r requirements-prefix.txt\n" > "$SCRATCH/requirements-prefix-dev.txt"

if ! build_venv "$SCRATCH/prefixvenv" "$SCRATCH/requirements-prefix-dev.txt"; then
  bad "could not build a venv from ${BASE_REF}'s requirements.txt"
else
  rm -rf "$PREFIX_TREE"
  git worktree remove --force "$PREFIX_TREE" >/dev/null 2>&1
  if ! git worktree add --detach "$PREFIX_TREE" "$BASE_REF" >/dev/null 2>&1; then
    bad "could not check out $BASE_REF to compare against"
  else
    cp tests/_provider_setup.py tests/test_provider_status.py "$PREFIX_TREE/tests/"
    PREFIX_OUT=$(cd "$PREFIX_TREE" && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
      "$SCRATCH/prefixvenv/bin/python" -m pytest tests/test_provider_status.py -q --tb=no 2>&1)
    PREFIX_RC=$?
    if [[ $PREFIX_RC -eq 0 ]]; then
      bad "the new tests pass against $BASE_REF — they do not test the fix"
    else
      printf '%s\n' "$PREFIX_OUT" | tail -3 | sed 's/^/   /'
      ok "the same tests fail against the pre-fix tree"
    fi
    git worktree remove --force "$PREFIX_TREE" >/dev/null 2>&1
  fi
fi

# ── 6. The deployed site (opt-in) ───────────────────────────────────────────
step "6. deployed site over HTTPS"
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
      if printf '%s' "$BODY" | grep -qiE "telnyx|twilio"; then
        bad "${path} names the carrier in a rendered page"
      fi
    done
    for asset in /static/app.css \
                 /static/fonts/inter-latin-400-normal.woff2 \
                 /static/fonts/inter-latin-500-normal.woff2 \
                 /static/fonts/inter-latin-600-normal.woff2 \
                 /static/fonts/inter-latin-700-normal.woff2; do
      CODE=$(curl -s -o /dev/null -w "%{http_code}" -b "$JAR" "${A4A_URL}${asset}")
      [[ "$CODE" == "200" ]] || bad "${asset} returned ${CODE} — fonts or styles are not loading"
    done
    if curl -s -b "$JAR" "${A4A_URL}/api/settings/system" | grep -qiE "telnyx|twilio"; then
      bad "/api/settings/system names the carrier"
    fi
    rm -f "$JAR"
    [[ $FAIL -eq 0 ]] && ok "seven screens, styles and all four font weights, no carrier name"
  fi
fi

printf '\n'
if [[ $FAIL -eq 0 ]]; then
  echo "PART A ACCEPTANCE PASS"
else
  echo "PART A ACCEPTANCE FAILED — see above"
fi
exit $FAIL
