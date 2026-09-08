#!/usr/bin/env bash
# PART A ACCEPTANCE — session B1, Stripe: settle August, then auto-bill usage.
#
# The stop condition for this session. `agent/gate.sh` answers "is this repo
# still sound?"; this answers "did session B1 do what it was sent to do?", which
# is a different question and the one RULES.md requires an agent to demonstrate
# rather than declare. Checks 1-11 are the numbered criteria from
# `sessions/session-B1.md` -> "Acceptance", in order.
#
#   bash agent/accept-B1.sh                  checks 1-11 (all local, no network)
#   bash agent/accept-B1.sh --with-stripe    adds check 12, against a REAL
#                                            test-mode Stripe account
#
# ── NOTHING HERE REACHES STRIPE, and that is asserted rather than promised ───
#
# Criterion 1 runs the billing modules with `socket.connect` and
# `socket.create_connection` raising, so a test that reached the network fails
# the run. `tests/conftest.py` also blanks every Stripe credential for the whole
# suite, and every Stripe call in the codebase goes through one replaceable
# object. Three independent guards, because the expensive mistake here is a
# suite that quietly charges a real customer.
#
# Check 12 is the one deliberate exception and it is opt-in. A3 requires the
# live Checkout shape to be verified against a real session rather than chosen
# from memory; that verification happens **once, by a human, with a test-mode
# key**, and never from a test:
#
#   STRIPE_SECRET_KEY=sk_test_… STRIPE_PRICE_METERED=price_… \
#   STRIPE_PRICE_BALANCE=price_… bash agent/accept-B1.sh --with-stripe
#
# It refuses a live key outright. The offline half of A3 —
# `tests/test_stripe_contract.py` — runs on every invocation and is what
# actually settled the mechanism: `subscription_data.add_invoice_items` is not a
# Checkout parameter in the pinned SDK, so the outstanding balance rides as a
# second line item.
#
# Nothing here sends an SMS: SMS_PROVIDER is console in every subprocess.
# What it DOES do to your machine: writes scratch files under $ACCEPT_SCRATCH,
# and for check 11 rsyncs a throwaway copy of the tree there. It writes nothing
# inside the repo.
#
# TURN CAP: 60 assistant turns for Part A. On exhausting it, stop and write
# `decisions/NNN-*.open.md` rather than continuing.

set -uo pipefail
cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)" || exit 1

WITH_STRIPE=0
for arg in "$@"; do
  case "$arg" in
    --with-stripe) WITH_STRIPE=1 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

SCRATCH="${ACCEPT_SCRATCH:-${TMPDIR:-/tmp}/a4a-accept-B1}"
PY="${PY:-.venv/bin/python}"
[[ -x "$PY" ]] || PY="python3"
ABS_PY="$PY"; [[ "$ABS_PY" == /* ]] || ABS_PY="$PWD/$PY"

mkdir -p "$SCRATCH"

FAIL=0
step() { printf '\n── %s\n' "$1"; }
bad()  { printf 'ACCEPT FAIL: %s\n' "$1"; FAIL=1; }
ok()   { printf '   ok — %s\n' "$1"; }

# ── 1. No real Stripe call in the suite ─────────────────────────────────────
step "1. the billing suite with the network switched off"
"$PY" - <<'PY'
import socket, sys

sys.path.insert(0, ".")


class NoNetwork(RuntimeError):
    pass


def _refuse(*args, **kwargs):
    raise NoNetwork(
        "a test tried to open a network connection — every Stripe client must "
        "be replaced, and every payload must come from a recorded fixture")


socket.socket.connect = _refuse
socket.socket.connect_ex = _refuse
socket.create_connection = _refuse

import pytest
raise SystemExit(pytest.main([
    "tests/test_stripe_billing.py", "tests/test_stripe_contract.py",
    "tests/test_whitelabel.py", "tests/test_billing.py",
    "-q", "-p", "no:cacheprovider",
]))
PY
if [[ $? -ne 0 ]]; then
  bad "the billing suite does not pass with the network switched off"
else
  ok "every Stripe client is replaced; socket.connect raised for nobody"
fi

# The guard has to be able to fire, or check 1 is a green light wired to
# nothing. This is the fifth measurement script in this repo to get its own
# self-check, and the fourth time one was needed.
#
# The first version of this check was worse than useless: it reassigned
# `socket.create_connection`, then called `socket.create_connection`, and
# reported "the guard blocks a real outbound connection". It could only ever
# pass, and it exercised the one patch of the three that does NOT matter --
# the HTTP stack an SDK uses builds its own `socket.socket` and calls
# `.connect()` on it, so `socket.socket.connect` is the patch that actually
# intercepts. This drives a real HTTP client at a closed local port and
# requires the guard's own exception rather than a connection refusal.
step "1b. the no-network guard itself, against a case whose answer is known"
"$PY" - <<'GUARD'
import socket, sys, urllib.request


class NoNetwork(RuntimeError):
    pass


def _refuse(*args, **kwargs):
    raise NoNetwork("blocked")


# Port 9 (discard) on loopback, closed here. Unguarded this raises
# ConnectionRefusedError; guarded it raises NoNetwork. The two are
# distinguishable, which is what makes this a known-answer test and not a
# tautology -- the first version called the function it had just replaced.
try:
    urllib.request.urlopen("http://127.0.0.1:9/", timeout=1)
except NoNetwork:
    sys.exit("the guard fired before it was installed -- this check is broken")
except Exception as exc:
    print(f"     unguarded: {type(exc).__name__} (a socket was really attempted)")

socket.socket.connect = _refuse
socket.socket.connect_ex = _refuse
socket.create_connection = _refuse
try:
    urllib.request.urlopen("http://127.0.0.1:9/", timeout=1)
except Exception as exc:
    cause = exc
    while cause is not None and not isinstance(cause, NoNetwork):
        cause = cause.__cause__ or cause.__context__
    if cause is None:
        sys.exit(f"the guard did NOT intercept the HTTP client: {exc!r}")
    print("     guarded:   NoNetwork raised from inside a real HTTP request")
    sys.exit(0)
sys.exit("the guarded request succeeded, which cannot happen")
GUARD
if [[ $? -ne 0 ]]; then
  bad "the no-network guard cannot fire on the path an SDK takes"
else
  ok "a real HTTP request is intercepted by socket.socket.connect"
fi

# ── 2-9. The behaviour, one criterion at a time, each in its own process ────
# 5e's review found two tests that only passed inside a full run. A test that
# needs its neighbours proves nothing about the criterion it is named for.
step "2-9. the behaviour each criterion names, in isolation"
declare -a CRITERIA=(
  "2|the meter receives RAW billable-status segments, not billable_segments()|tests/test_stripe_billing.py::test_a_fifteen_thousand_segment_month_reports_fifteen_thousand tests/test_stripe_billing.py::test_the_meter_gets_the_raw_count_for_one_campaign tests/test_stripe_billing.py::test_only_billable_statuses_reach_the_meter tests/test_stripe_billing.py::test_the_meter_follows_the_models_definition_of_billable tests/test_stripe_billing.py::test_a_row_with_no_segment_count_is_billed_as_one tests/test_stripe_billing.py::test_the_button_press_path_reports_what_it_sent"
  "3|the tier-drift check fails on a mismatched tier, and /health still answers 200|tests/test_stripe_billing.py::test_the_tier_check_reports_agreement_on_a_matching_price tests/test_stripe_billing.py::test_the_tier_check_fails_on_a_first_tier_of_five_thousand tests/test_stripe_billing.py::test_the_tier_check_fails_on_a_mispriced_second_tier tests/test_stripe_billing.py::test_a_sub_cent_rate_is_compared_as_a_decimal_number_of_cents tests/test_stripe_billing.py::test_a_stripe_outage_is_neither_agreement_nor_disagreement tests/test_stripe_billing.py::test_a_configured_box_that_has_never_checked_is_not_ok tests/test_stripe_billing.py::test_a_box_with_no_stripe_reports_nothing_to_disagree_about tests/test_stripe_billing.py::test_health_stays_200_while_the_tier_disagrees tests/test_stripe_billing.py::test_health_makes_no_stripe_call tests/test_stripe_contract.py::test_the_price_is_always_fetched_with_its_tiers_expanded"
  "4|/usage's cycle and the subscription's cycle are the same two dates|tests/test_stripe_billing.py::test_usages_cycle_and_the_subscriptions_cycle_are_the_same_two_dates tests/test_stripe_billing.py::test_the_cycle_falls_back_to_config_when_nothing_is_stored tests/test_stripe_billing.py::test_the_stored_anchor_wins_over_the_configured_day tests/test_stripe_billing.py::test_an_anchor_on_the_31st_is_not_flattened_by_a_short_month tests/test_stripe_billing.py::test_the_two_windows_agree_across_a_month_too_short_for_the_anchor tests/test_stripe_contract.py::test_the_billing_period_is_on_the_subscription_item_not_the_subscription"
  "5|session_is_ours() rejects another product, on the webhook and on the success page|tests/test_stripe_billing.py::test_a_session_for_another_product_is_not_ours tests/test_stripe_billing.py::test_the_guard_fails_closed_when_stripe_cannot_be_read tests/test_stripe_billing.py::test_the_webhook_stores_nothing_for_another_clients_checkout tests/test_stripe_billing.py::test_the_webhook_stores_the_customer_for_our_own_checkout tests/test_stripe_billing.py::test_the_success_page_applies_the_same_guard_as_the_webhook"
  "6|an unsigned webhook stores nothing, and an unconfigured secret ignores the payload|tests/test_stripe_billing.py::test_an_unsigned_webhook_is_ignored tests/test_stripe_billing.py::test_an_unconfigured_signing_secret_ignores_the_payload_rather_than_trusting_it tests/test_stripe_billing.py::test_a_correctly_signed_webhook_is_accepted tests/test_stripe_billing.py::test_the_webhook_route_rejects_an_unsigned_payload"
  "7|a repeated meter event with the same identifier bills once|tests/test_stripe_billing.py::test_a_repeated_meter_event_bills_once tests/test_stripe_billing.py::test_a_backfill_replays_only_what_was_never_reported tests/test_stripe_billing.py::test_a_backfill_will_not_re_meter_the_period_the_balance_already_settled tests/test_stripe_contract.py::test_a_meter_event_takes_a_deterministic_identifier tests/test_stripe_contract.py::test_the_meter_event_payload_uses_the_meters_own_field_names"
  "8|the back-bill arithmetic is billing_service's own|tests/test_stripe_billing.py::test_the_back_bill_tool_prices_august_from_billing_services_own_functions tests/test_stripe_billing.py::test_the_invoice_amount_is_cents_rounded_once"
  "9|with no Stripe keys, /subscribe says so and the checkout endpoint answers 503|tests/test_stripe_billing.py::test_subscribe_renders_a_notice_and_the_endpoint_answers_503 tests/test_whitelabel.py::test_the_billing_routes_are_discovered_by_the_scan_above tests/test_whitelabel.py::test_no_state_of_the_subscribe_page_carries_one_of_our_own_costs tests/test_whitelabel.py::test_the_billing_status_payload_is_walked_field_by_field tests/test_whitelabel.py::test_a_checkout_failure_tells_the_client_nothing_the_sdk_said"
)
for entry in "${CRITERIA[@]}"; do
  number="${entry%%|*}"; rest="${entry#*|}"
  label="${rest%%|*}"; targets="${rest#*|}"
  # shellcheck disable=SC2086
  if ! PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "$PY" -m pytest $targets -q \
       >"$SCRATCH/criterion-$number.log" 2>&1; then
    tail -25 "$SCRATCH/criterion-$number.log"
    bad "criterion $number: $label"
  else
    ok "$number. $label"
  fi
done

# ── 3b. The tier check run against a price known to be wrong ────────────────
# Review lens 5: run criterion 3 against a price you know is wrong *before*
# quoting the check as evidence. A passing test named "the check fails" would
# also pass on a check that failed for the wrong reason, or on one that failed
# always. This prints the verdict, the sentence the operator gets, and /health.
step "3b. the drift check, shown rather than claimed"
"$PY" - <<'PY'
import os, sys, tempfile

sys.path.insert(0, ".")
_fd, _db = tempfile.mkstemp(prefix="accept-B1-", suffix=".db"); os.close(_fd); os.unlink(_db)
os.environ["DATABASE_URL"] = f"sqlite:///{_db}"
os.environ["SMS_PROVIDER"] = "console"
os.environ.setdefault("SECRET_KEY", "accept-secret")
os.environ.setdefault("ADMIN_PASSWORD", "devpassword123")
for key in ("STRIPE_SECRET_KEY", "STRIPE_PRICE_METERED", "STRIPE_WEBHOOK_SECRET"):
    os.environ[key] = ""

from alembic import command
from alembic.config import Config
cfg = Config(); cfg.set_main_option("script_location", "alembic")
command.upgrade(cfg, "head")

from fastapi.testclient import TestClient
from app.core.config import settings
from app.core.database import SessionLocal
from app.main import app
from app.services import stripe_meter, stripe_tiers
from tests import _stripe_fixtures as fx

db = SessionLocal()
client = TestClient(app)

print(f"     the plan in .env: {settings.BILLING_SEGMENTS_INCLUDED:,} included, "
      f"${settings.BILLING_PRICE_PER_SEGMENT}/segment")

cases = {
    "a price that agrees": fx.tiered_price(),
    "a first tier of 5,000": fx.tiered_price(up_to=5000),
    "a second tier at 2 cents": fx.tiered_price(rate_cents="2"),
}
seen = {}
for label, price in cases.items():
    with fx.stripe_configured(), fx.replaced_api(fx.FakeStripe(price=price)):
        verdict = stripe_tiers.check_tier_drift(db)
        health = client.get("/health")
    seen[label] = verdict["state"]
    print(f"\n     {label}")
    print(f"       state        {verdict['state']}")
    for issue in verdict["issues"]:
        print(f"       issue        {issue}")
    print(f"       /health      HTTP {health.status_code}  "
          f"pricing_ok={health.json()['pricing_ok']}  "
          f"state={health.json()['pricing_state']}  "
          f"sending_ok={health.json()['sending_ok']}")
    assert health.status_code == 200, "a 503 here rolls back every deploy"

assert seen["a price that agrees"] == "agree", seen
assert seen["a first tier of 5,000"] == "disagree", seen
assert seen["a second tier at 2 cents"] == "disagree", seen
db.close(); os.unlink(_db)
PY
if [[ $? -ne 0 ]]; then
  bad "the tier-drift check did not separate a correct price from two wrong ones"
else
  ok "agrees on the configured plan, disagrees on both wrong prices, /health 200 throughout"
fi

# ── 8b. The back-bill tool's dry run over August, from the CLI ──────────────
# The test above proves the arithmetic. This runs the actual command line the
# spec names, against a database seeded with August's 28,002 segments, and
# requires $270.03 on stdout. It is the figure a human will read.
step "8b. tools/bill_period.py --start 2026-08-01 --end 2026-08-31"
AUGUST_DB="$SCRATCH/august.db"
rm -f "$AUGUST_DB"
DATABASE_URL="sqlite:///${AUGUST_DB}" "$PY" - <<'PY'
import os, sys
sys.path.insert(0, ".")
from alembic import command
from alembic.config import Config
cfg = Config(); cfg.set_main_option("script_location", "alembic")
command.upgrade(cfg, "head")

from app.core.database import SessionLocal
from app.models.sms_message import SMSMessage

db = SessionLocal()
# 28,002 segments sent in August 2026, plus rows that must NOT be counted: a
# failed send, a held-back row, one outside the window. A seed that contained
# only billable rows would pass on a tool that ignored the status filter.
for segments in (20000, 8000, 2):
    db.add(SMSMessage(phone="+15555550400", message="x", status="sent",
                      segments=segments, sent_at="2026-08-14T10:00:00"))
db.add(SMSMessage(phone="+15555550401", message="x", status="failed",
                  segments=5000, sent_at="2026-08-14T10:00:00"))
db.add(SMSMessage(phone="+15555550402", message="x", status="held_back",
                  segments=5000, sent_at="2026-08-14T10:00:00"))
db.add(SMSMessage(phone="+15555550403", message="x", status="sent",
                  segments=5000, sent_at="2026-09-02T10:00:00"))
db.commit(); db.close()
print("     seeded: 28,002 billable segments in August, plus 15,000 that must not count")
PY
if [[ $? -ne 0 ]]; then
  bad "could not seed the August database"
else
  BILL_OUT=$(DATABASE_URL="sqlite:///${AUGUST_DB}" SMS_PROVIDER=console \
             "$PY" tools/bill_period.py --start 2026-08-01 --end 2026-08-31 2>&1)
  printf '%s\n' "$BILL_OUT" | sed 's/^/   /'
  if ! printf '%s' "$BILL_OUT" | grep -q '\$270\.03'; then
    bad "the August dry run did not print \$270.03 — do NOT adjust the tool; one of the two is wrong"
  elif ! printf '%s' "$BILL_OUT" | grep -q '28,002'; then
    bad "the August dry run did not count 28,002 segments"
  elif ! printf '%s' "$BILL_OUT" | grep -q 'dry run'; then
    bad "the run did not announce itself as a dry run"
  else
    ok "28,002 segments, 18,002 billable, \$270.03 — and nothing was sent to Stripe"
  fi
  # And it must refuse to finalise without drafting.
  if DATABASE_URL="sqlite:///${AUGUST_DB}" "$PY" tools/bill_period.py \
       --start 2026-08-01 --end 2026-08-31 --charge >/dev/null 2>&1; then
    bad "--charge was accepted without --create"
  else
    ok "--charge without --create is refused"
  fi
fi
rm -f "$AUGUST_DB"

# ── 10. The gate, twice in a row ────────────────────────────────────────────
step "10. agent/gate.sh, twice"
GATE_PATH="$PATH"
[[ -x .venv/bin/python ]] && GATE_PATH="$PWD/.venv/bin:$PATH"
for run in 1 2; do
  if ! PATH="$GATE_PATH" bash agent/gate.sh >"$SCRATCH/gate-$run.log" 2>&1; then
    tail -20 "$SCRATCH/gate-$run.log"
    bad "the gate did not pass on run $run"
  else
    ok "run $run: $(grep -E '^[0-9]+ passed' "$SCRATCH/gate-$run.log" | tail -1)"
  fi
done

# ── 11. Every guard reverted on its own, twice, on a verified-pristine tree ─
step "11. agent/mutate-B1.py, two consecutive invocations on one pristine tree"
MUTATION_TREE="$SCRATCH/mutation-tree"
for run in 1 2; do
  rm -rf "$MUTATION_TREE"
  mkdir -p "$MUTATION_TREE"
  if ! rsync -a --exclude .venv --exclude node_modules --exclude .git --exclude data \
        --exclude '__pycache__' --exclude '*.db' --exclude .pytest_cache \
        ./ "$MUTATION_TREE/" 2>/dev/null; then
    bad "could not stage a scratch tree for the mutation check (run $run)"
    continue
  fi
  if ! "$ABS_PY" agent/mutate-B1.py "$MUTATION_TREE" "$ABS_PY" \
       >"$SCRATCH/mutations-$run.log" 2>&1; then
    sed 's/^/   /' "$SCRATCH/mutations-$run.log"
    bad "run $run: a guard was reverted and no test noticed"
  else
    grep -E '^(SCRATCH VERIFIED|ANCHORS VERIFIED|[0-9]+ mutations)' \
      "$SCRATCH/mutations-$run.log" | sed "s/^/   run $run: /"
  fi
done
rm -rf "$MUTATION_TREE"
if [[ -f "$SCRATCH/mutations-1.log" && -f "$SCRATCH/mutations-2.log" ]]; then
  # Reproducibility, not just success. P2 shipped a harness whose verdict moved
  # between runs; a mutation report that is not stable is not evidence.
  V1=$(grep -E '^[0-9]+ mutations' "$SCRATCH/mutations-1.log")
  V2=$(grep -E '^[0-9]+ mutations' "$SCRATCH/mutations-2.log")
  C1=$(grep -cE '^  (CAUGHT|\*\*\* NOT CAUGHT)' "$SCRATCH/mutations-1.log")
  C2=$(grep -cE '^  (CAUGHT|\*\*\* NOT CAUGHT)' "$SCRATCH/mutations-2.log")
  if [[ "$V1" != "$V2" || "$C1" != "$C2" ]]; then
    bad "the mutation run is not reproducible: '$V1' then '$V2'"
  else
    ok "both invocations: $V1 ($C1 verdicts each)"
  fi
fi

# ── 12. The live Checkout shape (opt-in, test-mode key, once) ───────────────
step "12. A3's live verification against a real Stripe test account"
if [[ $WITH_STRIPE -eq 0 ]]; then
  echo "   (not run — needs Part B. See the header for the invocation.)"
  echo "   The offline half ran above: tests/test_stripe_contract.py asserts"
  echo "   against the installed SDK that subscription_data does NOT accept"
  echo "   add_invoice_items, which is what chose the second line item."
elif [[ -z "${STRIPE_SECRET_KEY:-}" || -z "${STRIPE_PRICE_METERED:-}" ]]; then
  bad "--with-stripe needs STRIPE_SECRET_KEY and STRIPE_PRICE_METERED"
elif [[ "${STRIPE_SECRET_KEY}" != sk_test_* ]]; then
  bad "--with-stripe refuses anything but a test-mode key (sk_test_…)"
else
  "$PY" - <<'PY'
import os, sys
sys.path.insert(0, ".")
os.environ["SMS_PROVIDER"] = "console"
os.environ.setdefault("SECRET_KEY", "accept-secret")
os.environ.setdefault("ADMIN_PASSWORD", "devpassword123")

from app.services import stripe_billing, stripe_tiers
from app.core.database import SessionLocal

# The real API, once, deliberately, on a test-mode key. This is the only place
# in the repo that opens a network connection to Stripe.
session = stripe_billing.create_checkout_session(
    "https://example.invalid/ok", "https://example.invalid/no")
print(f"     Checkout Session created: {session.id}")
print(f"     mode={session.mode}  "
      f"payment_method_collection={session.payment_method_collection}")
items = stripe_billing.api().list_session_line_items(session.id)
for item in items.data:
    print(f"     line item: {item.price.id}  quantity={item.quantity}")
assert stripe_billing.session_is_ours(session.id), (
    "the ownership guard does not recognise a session it just created")

db = SessionLocal()
verdict = stripe_tiers.check_tier_drift(db)
db.close()
print(f"     tier check against the live price: {verdict['state']}")
for issue in verdict["issues"]:
    print(f"       {issue}")
assert verdict["state"] == "agree", verdict
PY
  if [[ $? -ne 0 ]]; then
    bad "the live Checkout Session did not match the shape this app builds"
  else
    ok "the real API accepted the session, the guard recognised it, the tiers agree"
  fi
fi

printf '\n'
if [[ $FAIL -eq 0 ]]; then
  echo "ACCEPT PASS — session B1 Part A"
  [[ $WITH_STRIPE -eq 0 ]] && echo "  (criterion 12 not run; it needs Part B and a test-mode key)"
else
  echo "ACCEPT FAILED — see above"
fi
exit $FAIL
