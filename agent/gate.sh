#!/usr/bin/env bash
# THE GATE — Auctions4America SMS platform
#
# Exits non-zero on anything that should have been caught in manual review.
# Referenced by GATE_CMD in agent.config.sh; the Stop hook runs it when an agent
# thinks it's finished. The agent is not the judge of its own work.
#
# Design notes:
#  - Checks 3-5 are project-specific and are here because a plain `pytest` gate
#    would go green on the exact mistakes this project is most likely to make:
#    leaking the carrier's name into client-facing UI, reintroducing the runtime
#    Tailwind CDN, and letting a file sprawl past the 500-line rule.
#  - Steps skip gracefully when their target doesn't exist yet (alembic arrives
#    in module 1), so the gate is usable from session 1 and tightens on its own.
#  - Keep this fast. A gate slower than a couple of minutes gets bypassed.

set -uo pipefail
cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)" || exit 1

FAIL=0
step() { printf '\n── %s\n' "$1"; }
bad()  { printf 'GATE FAIL: %s\n' "$1"; FAIL=1; }

# ---- 1. Tests -------------------------------------------------------------
step "pytest"
if ! python -m pytest tests/ -q --maxfail=1; then
  bad "test suite is red"
fi

# ---- 2. Migrations apply to a clean database ------------------------------
# Agents write plausible migrations that don't match the model layer. Applying
# to a scratch DB is the cheapest way to catch it.
step "alembic upgrade head (scratch db)"
if [[ -f alembic.ini ]]; then
  SCRATCH=".gate-scratch.db"
  rm -f "$SCRATCH"
  if ! DATABASE_URL="sqlite:///./${SCRATCH}" alembic upgrade head; then
    bad "migrations do not apply cleanly to an empty database"
  fi
  rm -f "$SCRATCH"
else
  echo "   (skipped — no alembic.ini yet)"
fi

# ---- 3. White-label: the carrier is never visible to the client -----------
# Scope is deliberately narrow — this is about what a USER can see, not about
# renaming internals. Excluded on purpose:
#   app/routers/webhooks/  the carrier posts to those URLs; the module name and
#                          the provider string in their healthchecks are ours.
#   agent/notify.sh        our own alerting channel, not the client's product.
# Everything else — templates, and every other router — must be clean.
step "white-label"
HITS=$(grep -rni "telnyx\|twilio" app/templates/ app/routers/ 2>/dev/null \
  | grep -v "^app/routers/webhooks/" || true)
if [[ -n "$HITS" ]]; then
  printf '%s\n' "$HITS" | head -20
  bad "carrier name appears in a client-facing surface"
fi

# ---- 4. No runtime CDN dependency -----------------------------------------
# The app rendered as raw unstyled HTML when the Tailwind Play CDN was
# unreachable. Once module 1 removes it, it stays removed.
step "no runtime CDN"
if grep -rnq "cdn.tailwindcss.com\|fonts.googleapis.com" app/templates/ 2>/dev/null; then
  grep -rn "cdn.tailwindcss.com\|fonts.googleapis.com" app/templates/ | head
  bad "a template still fetches CSS or fonts at runtime"
fi

# ---- 5. File size ---------------------------------------------------------
step "500-line rule"
OVERSIZE=$(find app -name '*.py' -o -name '*.html' | xargs wc -l 2>/dev/null \
  | awk '$1 > 500 && $2 != "total" {print $2" ("$1" lines)"}')
if [[ -n "$OVERSIZE" ]]; then
  echo "$OVERSIZE"
  bad "file(s) over 500 lines"
fi

# ---- 6. Layering: app/sms must not import the DB --------------------------
# This boundary is why the SMS engine was reusable across clients. Losing it is
# silent and expensive.
step "layering (app/sms is DB-free)"
if grep -rnq "^from app\.\(models\|services\)\|^import app\.\(models\|services\)" app/sms/ 2>/dev/null; then
  grep -rn "app\.\(models\|services\)" app/sms/ | head
  bad "app/sms/ imports from app.models or app.services"
fi

printf '\n'
if [[ $FAIL -eq 0 ]]; then
  echo "GATE PASS"
else
  echo "GATE FAILED — see above"
fi
exit $FAIL
