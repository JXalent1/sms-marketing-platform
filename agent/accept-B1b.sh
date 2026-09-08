#!/usr/bin/env bash
# PART A ACCEPTANCE — session B1b, meter once, late, and correctly.
#
# The stop condition for this session. `agent/gate.sh` answers "is this repo
# still sound?"; this answers "did session B1b do what it was sent to do?",
# which RULES.md requires an agent to demonstrate rather than declare. Checks
# 1-10 are the numbered criteria from `sessions/session-B1b.md` -> "Acceptance",
# in order; the lettered checks (1b, 5b, 7b, 8b) *show* the thing the numbered
# check asserts, against a scratch database, so a reader can see the figure
# rather than a tick.
#
#   bash agent/accept-B1b.sh
#
# ── NOTHING HERE REACHES STRIPE, and that is asserted rather than promised ───
#
# Check 0 runs the billing modules with `socket.socket.connect` raising, so a
# test that reached the network fails the run; check 0b proves that guard can
# fire, against a real HTTP request whose answer is known. `tests/conftest.py`
# blanks every Stripe credential for the whole suite, and every Stripe call in
# the codebase goes through one replaceable object. Nothing here sends an SMS:
# SMS_PROVIDER is console in every subprocess.
#
# What it DOES do to your machine: writes scratch files under $ACCEPT_SCRATCH,
# and for check 10 rsyncs a throwaway copy of the tree there. It writes nothing
# inside the repo.
#
# TURN CAP: 40 assistant turns. On exhausting it, stop and write
# `decisions/NNN-*.open.md` rather than continuing.

set -uo pipefail
cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)" || exit 1

SCRATCH="${ACCEPT_SCRATCH:-${TMPDIR:-/tmp}/a4a-accept-B1b}"
PY="${PY:-.venv/bin/python}"
[[ -x "$PY" ]] || PY="python3"
ABS_PY="$PY"; [[ "$ABS_PY" == /* ]] || ABS_PY="$PWD/$PY"

mkdir -p "$SCRATCH"

FAIL=0
step() { printf '\n── %s\n' "$1"; }
bad()  { printf 'ACCEPT FAIL: %s\n' "$1"; FAIL=1; }
ok()   { printf '   ok — %s\n' "$1"; }

# A scratch database at head, for the "shown" checks. Each gets its own file.
fresh_db() {
  local path="$1"
  rm -f "$path"
  DATABASE_URL="sqlite:///${path}" "$PY" - <<'PY' >/dev/null
import sys
sys.path.insert(0, ".")
from alembic import command
from alembic.config import Config
cfg = Config(); cfg.set_main_option("script_location", "alembic")
command.upgrade(cfg, "head")
PY
}

# ── 0. No real Stripe call in the suite ─────────────────────────────────────
step "0. the billing suite with the network switched off"
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
    "tests/test_metering_pass.py", "tests/test_stripe_billing.py",
    "tests/test_stripe_contract.py", "tests/test_billing.py",
    "-q", "-p", "no:cacheprovider",
]))
PY
if [[ $? -ne 0 ]]; then
  bad "the billing suite does not pass with the network switched off"
else
  ok "every Stripe client is replaced; socket.connect raised for nobody"
fi

step "0b. the no-network guard itself, against a case whose answer is known"
"$PY" - <<'GUARD'
import socket, sys, urllib.request


class NoNetwork(RuntimeError):
    pass


def _refuse(*args, **kwargs):
    raise NoNetwork("blocked")


# Port 9 (discard) on loopback, closed here. Unguarded this raises
# ConnectionRefusedError; guarded it raises NoNetwork. Distinguishable, which
# is what makes this a known-answer test — B1's first version called the
# function it had just replaced.
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

# ── 1-8. The behaviour, one criterion at a time, each in its own process ────
# 5e's review found two tests that only passed inside a full run. A test that
# needs its neighbours proves nothing about the criterion it is named for.
step "1-8. the behaviour each criterion names, in isolation"
M="tests/test_metering_pass.py"
C="tests/test_stripe_contract.py"
declare -a CRITERIA=(
  "1|the over-bill is gone: 12,000 sent, 4,000 undelivered by webhook, the meter receives 8,000 once|$M::test_the_meter_receives_what_the_webhook_left_billable_once"
  "2|the top-up is billed as a later batch and the campaign total equals compute_usage()|$M::test_a_top_up_is_a_later_batch_and_the_campaign_total_matches_usage"
  "3|a second pass over the same rows makes no call — asserted on the call count|$M::test_a_second_pass_over_the_same_rows_makes_no_call_at_all $M::test_a_marked_row_is_never_reported_whatever_its_status"
  "4|a backfill of a 30-day-old period double-bills nothing, proven against marked rows|$M::test_a_backfill_of_a_thirty_day_old_period_double_bills_nothing $M::test_the_backfill_meters_what_was_missed_and_only_that"
  "5|usage older than 35 days is refused, visibly, metered_at left NULL, the SDK still says 35|$M::test_usage_older_than_thirty_five_days_is_refused_visibly $C::test_the_sdk_still_documents_the_two_limits_this_session_builds_on"
  "6|the event carries the send timestamp and lands in the earlier cycle|$M::test_the_event_carries_the_send_time_and_lands_in_the_earlier_cycle $M::test_a_campaign_that_straddles_midnight_is_split_the_way_usage_splits_it $C::test_a_meter_event_takes_an_identifier_and_a_timestamp"
  "7|nothing meters from the send path — the Send button, the scheduler, the top-up, and the count of call sites|$M::test_a_real_send_meters_nothing_and_the_pass_meters_it_later $M::test_neither_the_scheduler_nor_the_top_up_path_meters $M::test_a_segment_can_be_metered_from_exactly_one_place"
  "8|/usage and the metered total agree for a window with failures and a top-up|$M::test_usage_and_the_metered_total_agree_with_failures_and_a_top_up $M::test_the_breakdown_adds_up_to_usage_and_names_every_reason"
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

# ── 1b. The over-bill, shown ────────────────────────────────────────────────
# Review lens 5 asks for the scan's own first version: this is the measurement
# from decisions/011 (12,000 metered, 8,000 used) run against the new
# mechanism, printing the figures rather than a tick.
step "1b. decisions/011's measurement, re-run against the pass"
DB1="$SCRATCH/overbill.db"; fresh_db "$DB1"
DATABASE_URL="sqlite:///${DB1}" SMS_PROVIDER=console "$PY" - <<'PY'
import sys
from datetime import datetime, timedelta, date
sys.path.insert(0, ".")
from app.core.config import settings
from app.core.database import SessionLocal
from app.models.app_setting import set_setting
from app.models.campaign import Campaign
from app.models.sms_message import SMSMessage
from app.routers.webhooks.common import record_delivery_status
from app.services import billing_service, stripe_billing, stripe_meter
from tests import _stripe_fixtures as fx

db = SessionLocal()
set_setting(db, stripe_billing.CUSTOMER_ID_KEY, fx.CUSTOMER)
set_setting(db, stripe_billing.CYCLE_ANCHOR_AT_KEY, "2026-08-01")
campaign = Campaign(name="accept-B1b", message_template="x", audience="all",
                    status="completed", created_at="2026-08-14T09:00:00", sent_count=12)
db.add(campaign); db.commit(); db.refresh(campaign)
sent = "2026-08-14T10:00:00"
for n in range(12):
    db.add(SMSMessage(campaign_id=campaign.id, phone="+15555550400", message="x",
                      status="sent", segments=1000, sent_at=sent, external_id=f"acc-{n}"))
db.commit()
print("     sent            : 12,000 segments, every row `sent`")
for n in range(4):
    record_delivery_status(db, f"acc-{n}", "undelivered", "Carrier did not deliver")
db.expire_all()
_, usage = billing_service.compute_usage(db, date(2026, 8, 14), date(2026, 8, 14))
print(f"     webhook         : 4,000 moved to `undelivered`; /usage now reports {usage:,}")

fake = fx.FakeStripe(dedupe=False)
now = datetime.fromisoformat(sent) + timedelta(hours=settings.BILLING_SETTLE_HOURS + 1)
with fx.stripe_configured(), fx.replaced_api(fake):
    early = stripe_meter.meter_settled_rows(db, now=datetime.fromisoformat(sent) + timedelta(hours=1))
    print(f"     pass at +1h     : {len(fake.named('create_meter_event'))} event(s) — not settled yet")
    first = stripe_meter.meter_settled_rows(db, now=now)
    calls_after_first = len(fake.named("create_meter_event"))
    print(f"     pass at +{settings.BILLING_SETTLE_HOURS + 1}h    : {calls_after_first} event(s), "
          f"meter holds {sum(fake.metered_values):,}")
    second = stripe_meter.meter_settled_rows(db, now=now + timedelta(hours=1))
    print(f"     pass again      : {len(fake.named('create_meter_event')) - calls_after_first} "
          f"new event(s), meter holds {sum(fake.metered_values):,}")
(_, params), = fake.named("create_meter_event")
print(f"     event timestamp : {datetime.fromtimestamp(params['timestamp'])} (the send, not the pass)")
marked = db.query(SMSMessage).filter(SMSMessage.metered_at.isnot(None)).count()
print(f"     rows marked     : {marked} of 12 (the 4 undelivered stay NULL)")
db.close()
assert early["reported"] == [], early
assert sum(fake.metered_values) == 8000 == usage, (fake.metered_values, usage)
assert calls_after_first == 1 and len(fake.named("create_meter_event")) == 1
assert marked == 8
PY
if [[ $? -ne 0 ]]; then
  bad "the pass did not meter 8,000 once for decisions/011's scenario"
else
  ok "8,000 once, stamped with the send time, and nothing on a second pass"
fi
rm -f "$DB1"

# ── 5b. The refusal, shown, and the tool's --unmetered view ─────────────────
step "5b. usage older than 35 days: the ERROR line and tools/bill_period.py --unmetered"
DB5="$SCRATCH/toold.db"; fresh_db "$DB5"
DATABASE_URL="sqlite:///${DB5}" SMS_PROVIDER=console "$PY" - <<'PY'
import logging, sys
from datetime import datetime, timedelta
sys.path.insert(0, ".")
from app.core.database import SessionLocal
from app.models.app_setting import set_setting
from app.models.sms_message import SMSMessage
from app.services import stripe_billing, stripe_meter
from tests import _stripe_fixtures as fx

db = SessionLocal()
set_setting(db, stripe_billing.CUSTOMER_ID_KEY, fx.CUSTOMER)
set_setting(db, stripe_billing.CYCLE_ANCHOR_AT_KEY, "2026-07-01")
for n in range(3):
    db.add(SMSMessage(campaign_id=None, phone="+15555550401", message="x",
                      status="delivered", segments=700, sent_at="2026-07-20T10:00:00"))
db.commit()

lines = []
class _Capture(logging.Handler):
    def emit(self, record):
        lines.append(record.getMessage())
logging.getLogger("billing.stripe").addHandler(_Capture())
fake = fx.FakeStripe()
with fx.stripe_configured(), fx.replaced_api(fake):
    verdict = stripe_meter.meter_settled_rows(db, now=datetime(2026, 9, 8, 12, 0))
print("     ERROR log line  :", [l for l in lines if "USAGE NOT METERED" in l][0][:140] + " …")
print(f"     Stripe calls    : {len(fake.calls)}")
print(f"     refused         : {verdict['refused']}")
still_null = db.query(SMSMessage).filter(SMSMessage.metered_at.is_(None)).count()
print(f"     metered_at NULL : {still_null} of 3")
db.close()
assert fake.calls == [] and still_null == 3 and verdict["refused"][0]["segments"] == 2100
PY
if [[ $? -ne 0 ]]; then
  bad "usage older than 35 days was not refused visibly"
else
  ok "refused, logged at ERROR, metered_at left NULL"
fi
TOOL_OUT=$(DATABASE_URL="sqlite:///${DB5}" SMS_PROVIDER=console \
           "$PY" tools/bill_period.py --start 2026-07-01 --end 2026-07-31 --unmetered 2>&1)
printf '%s\n' "$TOOL_OUT" | sed 's/^/   /'
if ! printf '%s' "$TOOL_OUT" | grep -q 'older than 35 days'; then
  bad "the tool's --unmetered view does not say why the rows are unmetered"
elif ! printf '%s' "$TOOL_OUT" | grep -q 'unmetered       2,100'; then
  bad "the tool's --unmetered view does not carry the unmetered total"
elif ! printf '%s' "$TOOL_OUT" | grep -q 'dry run'; then
  bad "the run did not announce itself as a dry run"
else
  ok "the tool names the 2,100 unmetered segments and the reason"
fi
rm -f "$DB5"

# ── 7b. One place a segment can be metered, shown ───────────────────────────
step "7b. the places a segment can be metered (review lens 1)"
SITES=$(grep -rn --include='*.py' --exclude-dir=__pycache__ '\.create_meter_event(' app/ \
        | grep -v 'app/services/stripe_billing.py' || true)
printf '%s\n' "$SITES" | sed 's/^/   /'
if [[ "$(printf '%s\n' "$SITES" | grep -c .)" -ne 1 ]]; then
  bad "expected exactly one call site outside the adapter"
else
  ok "one call site, in app/services/stripe_meter.py"
fi

# ── 8b. Cost at production scale ────────────────────────────────────────────
# 5j: time every new query against production-scale row counts, because the
# suite's twelve rows cannot show cost. 360,000 messages, a year's worth at
# this account's rate, eleven months already marked.
step "8b. the pass's two selections against 360,000 rows"
DB8="$SCRATCH/scale.db"; fresh_db "$DB8"
DATABASE_URL="sqlite:///${DB8}" SMS_PROVIDER=console "$PY" - <<'PY'
import random, sqlite3, sys, time
from datetime import date, datetime
sys.path.insert(0, ".")
from app.core.config import settings
path = settings.DATABASE_URL.replace("sqlite:///", "")
con = sqlite3.connect(path)
random.seed(1)
statuses = ["sent"] * 35 + ["delivered"] * 35 + ["undelivered"] * 20 + ["failed"] * 5 + ["held_back"] * 5
rows = []
for month in range(12):
    y, m = (2025 + (9 + month) // 12), ((9 + month) % 12) + 1
    for _ in range(30000):
        day, hour = random.randint(1, 28), random.randint(0, 23)
        status = random.choice(statuses)
        sent_at = f"{y:04d}-{m:02d}-{day:02d}T{hour:02d}:{random.randint(0, 59):02d}:00"
        marked = (f"{y:04d}-{m:02d}-{day:02d}T23:00:00"
                  if month < 11 and status in ("sent", "delivered") else None)
        rows.append((random.randint(1, 400), "+15555550000", "x", status, 1, sent_at, marked))
con.executemany("INSERT INTO sms_messages (campaign_id, phone, message, status, "
                "segments, sent_at, metered_at) VALUES (?,?,?,?,?,?,?)", rows)
con.commit(); con.close()

from app.core.database import SessionLocal
from app.services import stripe_meter
db = SessionLocal()
now, since = datetime(2026, 9, 8, 12, 0), date(2025, 9, 9)
worst = 0
for name, fn in (("settled_unmetered", stripe_meter.settled_unmetered),
                 ("too_old_unmetered", stripe_meter.too_old_unmetered)):
    t0 = time.perf_counter(); got = fn(db, now, since); ms = (time.perf_counter() - t0) * 1000
    worst = max(worst, ms)
    print(f"     {name:<18} {len(got):>6,} rows in {ms:6.0f} ms")
db.close()
assert worst < 2000, f"{worst:.0f} ms — a selection this slow belongs behind an index (escalation item 8)"
PY
if [[ $? -ne 0 ]]; then
  bad "the pass's selection is too slow at production scale"
else
  ok "both selections under two seconds on 360,000 rows, off the event loop"
fi
rm -f "$DB8"

# ── 9. The gate, twice in a row ─────────────────────────────────────────────
step "9. agent/gate.sh, twice"
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

# ── 10. Every guard reverted on its own, twice, on a verified-pristine tree ─
step "10. agent/mutate-B1b.py, two consecutive invocations on one pristine tree"
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
  if ! "$ABS_PY" agent/mutate-B1b.py "$MUTATION_TREE" "$ABS_PY" \
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
  # Reproducibility, not just success: a mutation report that is not stable
  # is not evidence.
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

printf '\n'
if [[ $FAIL -eq 0 ]]; then
  echo "ACCEPT PASS — session B1b Part A"
else
  echo "ACCEPT FAILED — see above"
fi
exit $FAIL
