#!/usr/bin/env bash
# PART A ACCEPTANCE — session 5f, short links, click stats and reporting.
#
# The stop condition for this session. `agent/gate.sh` answers "is this repo
# still sound?"; this answers "did session 5f do what it was sent to do?", which
# is a different question and the one RULES.md requires an agent to demonstrate
# rather than declare. Each check below is one numbered criterion from
# `sessions/session-5f.md` -> "Part A acceptance", in order.
#
#   bash agent/accept-5f.sh                 checks 1-10 (all local)
#   bash agent/accept-5f.sh --with-remote   adds check 11, against the deployed box
#
# Check 11 needs the deployed site and a login, so it is opt-in and never runs by
# accident:  A4A_URL=https://... A4A_PASSWORD=... bash agent/accept-5f.sh --with-remote
#
# TURN CAP: 60 assistant turns for Part A. On exhausting it, stop and write
# `decisions/NNN-*.open.md` rather than continuing — the same discipline
# MAX_GATE_ATTEMPTS=4 applies to the gate. A session that has spent sixty turns
# and still cannot make this script exit 0 has found something a human needs to
# look at.
#
# Nothing here sends a message. SMS_PROVIDER is console in every subprocess, no
# live credential is read, and no contact data is imported, modified or deleted
# outside the suite's own scratch database. SHORT_LINK_DOMAIN is never written
# to .env — the tests set it on the settings object and restore it.
#
# What it DOES do to your machine: writes scratch files under $ACCEPT_SCRATCH,
# and for check 10 rsyncs a throwaway copy of the tree there. It writes nothing
# inside the repo, and it never touches the live box unless you pass
# --with-remote.

set -uo pipefail
cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)" || exit 1

WITH_REMOTE=0
for arg in "$@"; do
  case "$arg" in
    --with-remote) WITH_REMOTE=1 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

SCRATCH="${ACCEPT_SCRATCH:-${TMPDIR:-/tmp}/a4a-accept-5f}"
PY="${PY:-.venv/bin/python}"
[[ -x "$PY" ]] || PY="python3"
ABS_PY="$PY"; [[ "$ABS_PY" == /* ]] || ABS_PY="$PWD/$PY"

mkdir -p "$SCRATCH"

FAIL=0
step() { printf '\n── %s\n' "$1"; }
bad()  { printf 'ACCEPT FAIL: %s\n' "$1"; FAIL=1; }
ok()   { printf '   ok — %s\n' "$1"; }

# ── 1. The gate, twice in a row ─────────────────────────────────────────────
step "1. agent/gate.sh, twice"
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

# ── 2-9. The behaviour, exercised, one criterion at a time ──────────────────
# Each criterion's tests run in their **own** pytest process. 5e's review found
# two tests that only passed inside a full run — one leaning on contacts another
# module had seeded, one reading a list an earlier test in its own file filled,
# the latter passing in isolation with two assertions comparing 0 == 0. A test
# that needs its neighbours proves nothing about the criterion it is named for.
step "2-9. the behaviour each criterion names"
declare -a CRITERIA=(
  "2|one link per recipient; each resolves to its target in exactly one hop; the segment count is measured on the rendered link|tests/test_short_links.py::test_a_campaign_with_the_tag_mints_one_link_per_recipient tests/test_short_links.py::test_a_link_resolves_to_its_target_in_exactly_one_hop tests/test_short_links.py::test_the_segment_count_is_measured_on_the_rendered_link tests/test_short_links.py::test_the_preflight_endpoint_quotes_the_rendered_link_not_the_tag tests/test_short_links.py::test_the_placeholder_is_the_same_length_as_a_minted_link tests/test_short_links.py::test_a_target_that_would_chain_or_break_is_refused tests/test_short_links.py::test_a_top_up_mints_a_link_for_each_row_it_adds_and_reuses_none tests/test_short_links.py::test_no_slug_can_be_minted_that_a_page_already_owns tests/test_short_links.py::test_the_generator_throws_away_a_reserved_slug"
  "3|SHORT_LINK_DOMAIN unset: composing with the tag refuses, names the cause, mints nothing and sends nothing|tests/test_short_links.py::test_an_unconfigured_domain_refuses_at_compose_time tests/test_short_links.py::test_the_preflight_row_fails_when_the_link_cannot_be_minted tests/test_short_links.py::test_a_top_up_refuses_when_the_link_can_no_longer_be_minted"
  "4|an unauthenticated attempt to mint a link fails|tests/test_short_links.py::test_minting_is_closed_to_unauthenticated_callers tests/test_short_links.py::test_an_unknown_slug_answers_404_and_creates_nothing"
  "5|a click is recorded and attributed to the right contact; a click whose write fails still redirects|tests/test_short_links.py::test_a_click_is_recorded_and_attributed_to_the_right_contact tests/test_short_links.py::test_a_click_whose_write_fails_still_redirects"
  "6|a scanner-shaped request is marked non-human and kept out of the default count, with the filtered count beside it|tests/test_short_links.py::test_a_scanner_is_marked_and_kept_out_of_the_headline tests/test_short_links.py::test_a_click_that_arrives_before_a_person_could_read_it_is_filtered tests/test_click_filtering.py"
  "7|actual cost and its rate/carrier-fee split are captured per message, and the reconciliation puts estimate against actual|tests/test_campaign_reports.py::test_the_carrier_cost_and_its_split_are_captured_per_message tests/test_campaign_reports.py::test_the_reconciliation_puts_estimate_against_actual tests/test_campaign_reports.py::test_an_unpriced_message_is_not_counted_as_free"
  "8|no wholesale cost, carrier name or raw provider payload on the report, the export or either history screen|tests/test_campaign_reports.py::test_no_new_surface_leaks_the_carrier_or_our_cost tests/test_campaign_reports.py::test_the_export_carries_the_report_and_the_recipients tests/test_whitelabel.py"
  "9|both history screens paginate, and the query count does not scale with the rows returned|tests/test_campaign_reports.py::test_both_history_screens_paginate_in_a_bounded_number_of_queries tests/test_campaign_reports.py::test_contact_history_names_the_campaign_and_says_whether_they_clicked"
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

# ── 2b. The one hop, printed rather than asserted ───────────────────────────
# Criterion 2 says "resolves to the target in exactly one hop". A test asserting
# `status_code == 302` is true and easy to misread; this walks the redirect and
# prints what actually comes back, which is the shape accept-5g check 3
# established for "prove it, do not claim it".
step "2b. what the redirect actually answers"
# Against a scratch database, never the one DATABASE_URL points at. This block
# seeds a contact and a campaign, and the box this script might one day be run
# on is the box holding the client's real list.
ACCEPT_DB="$SCRATCH/accept-5f.db"
rm -f "$ACCEPT_DB"
export DATABASE_URL="sqlite:///${ACCEPT_DB}"
if ! DATABASE_URL="$DATABASE_URL" alembic upgrade head >"$SCRATCH/accept-db.log" 2>&1; then
  tail -10 "$SCRATCH/accept-db.log"
  bad "could not build the scratch database for check 2b"
fi
"$PY" - <<'PY'
import sys
sys.path.insert(0, ".")
from fastapi.testclient import TestClient
from app.core.database import SessionLocal
from app.main import app
from app.services import link_service
from tests import _link_setup as setup

db = SessionLocal()
try:
    setup.purge(db)
    with setup.short_link_domain():
        contacts = setup.seed_contacts(db, ["+15555550999"], source="accept-5f")
        from app.services.campaign_service import CampaignService
        service = CampaignService(db)
        campaign = service.create_campaign(
            name=f"{setup.NAME_PREFIX}accept",
            message_template=f"Auctions4America: {link_service.LINK_TAG} Reply STOP to opt out.",
            audience="source:accept-5f", cross_category_override=True,
            link_target_url=setup.TARGET_URL)
        from app.models.short_link import ShortLink
        link = db.query(ShortLink).filter(ShortLink.campaign_id == campaign.id).one()
        url = link_service.url_for(link.slug)
        print(f"     link in the message: {url}  ({len(url)} characters)")

        client = TestClient(app)
        hops = []
        response = client.get(f"/{link.slug}", follow_redirects=False)
        while response.status_code in (301, 302, 303, 307, 308):
            location = response.headers["location"]
            hops.append((response.status_code, location))
            if not location.startswith("/"):
                break
            response = client.get(location, follow_redirects=False)

        for code, location in hops:
            print(f"     {code} -> {location}")
        print(f"     cache-control: {dict(client.get(f'/{link.slug}', follow_redirects=False).headers).get('cache-control')}")
        ok = (len(hops) == 1 and hops[0][0] == 302
              and hops[0][1] == setup.TARGET_URL)
finally:
    setup.purge(db)
    db.close()
sys.exit(0 if ok else 1)
PY
if [[ $? -ne 0 ]]; then
  bad "the redirect is not exactly one 302 to the stored target"
else
  ok "one hop, 302, straight to the target"
fi
rm -f "$ACCEPT_DB"
unset DATABASE_URL

# ── 8b. Our cost is structurally absent from every serialized surface ───────
# Criterion 8 again, from the other side. The runtime scan in check 8 proves the
# routes as they are today; this proves nothing in a router or a template
# *reads* a wholesale figure, so a route added later inherits the property
# instead of needing a new test.
#
# By AST rather than by grep, and the first draft of this check is why: a plain
# grep failed on `app/routers/reports.py:9`, which is a docstring saying these
# fields must never be returned. That is accept-5h check 7's lesson repeated —
# a check that counts prose about a rule as a violation of it is a check nobody
# will keep passing. Templates are grepped, because Jinja has no docstrings.
step "8b. no client-facing module reads our cost columns"
"$PY" - <<'PY'
import ast, pathlib, sys

FORBIDDEN = {"WHOLESALE_COST_PER_SEGMENT", "carrier_cost", "carrier_cost_rate",
             "carrier_cost_fee", "carrier_cost_currency", "cost_breakdown"}

# The one reader in app/routers/, declared rather than silently excluded.
# Session 1b put it there deliberately: it is the divisor that turns our carrier
# balance into HIS segment capacity, and neither the rate nor the balance is in
# the response — tests/test_whitelabel.py proves that by running the route.
# `usage.py` belongs to module 8, so moving it behind a service helper is that
# module's job; status.md has carried the note since session 4.
ALLOWED = {("app/routers/usage.py", "capacity", "WHOLESALE_COST_PER_SEGMENT")}

found = set()
for path in sorted(pathlib.Path("app/routers").rglob("*.py")):
    stack = []
    class V(ast.NodeVisitor):
        def visit_FunctionDef(self, node):
            stack.append(node.name); self.generic_visit(node); stack.pop()
        visit_AsyncFunctionDef = visit_FunctionDef
        def visit_Attribute(self, node):
            if node.attr in FORBIDDEN:
                found.add((str(path), stack[-1] if stack else "<module>", node.attr))
            self.generic_visit(node)
        def visit_Name(self, node):
            if node.id in FORBIDDEN:
                found.add((str(path), stack[-1] if stack else "<module>", node.id))
            self.generic_visit(node)
        def visit_Constant(self, node):
            # A string literal naming a column is how it reaches a payload key.
            if isinstance(node.value, str) and node.value in FORBIDDEN:
                found.add((str(path), stack[-1] if stack else "<module>", node.value))
    V().visit(ast.parse(path.read_text()))

for entry in sorted(found & ALLOWED):
    print(f"     allowed: {entry[0]} {entry[1]}() reads {entry[2]}")
new = sorted(found - ALLOWED)
for path, func, name in new:
    print(f"     *** {path} {func}() reads {name}")
sys.exit(1 if new else 0)
PY
if [[ $? -ne 0 ]]; then
  bad "a router reads our wholesale cost — it must not cross the API boundary"
else
  ok "no router reads a wholesale figure except the one declared above"
fi
HITS=$(grep -rnI --exclude-dir=__pycache__ \
  "carrier_cost\|WHOLESALE_COST_PER_SEGMENT\|cost_breakdown" \
  app/templates/ 2>/dev/null || true)
if [[ -n "$HITS" ]]; then
  printf '%s\n' "$HITS"
  bad "a template names our wholesale cost"
else
  ok "no template names a wholesale figure"
fi

# ── 10. Each fix reverted on its own, and the suite has to notice ───────────
step "10. every 5f fix reverted one at a time; each must break a test"
MUTATION_TREE="$SCRATCH/mutation-tree"
rm -rf "$MUTATION_TREE"
mkdir -p "$MUTATION_TREE"
if ! rsync -a --exclude .venv --exclude node_modules --exclude .git --exclude data \
      --exclude '__pycache__' --exclude '*.db' --exclude .pytest_cache \
      ./ "$MUTATION_TREE/" 2>/dev/null; then
  bad "could not stage a scratch tree for the mutation check"
elif ! "$ABS_PY" agent/mutate-5f.py "$MUTATION_TREE" "$ABS_PY" >"$SCRATCH/mutations.log" 2>&1; then
  sed 's/^/   /' "$SCRATCH/mutations.log"
  bad "a fix was reverted and no test noticed"
else
  grep -E '^(R[0-9]|  CAUGHT|[0-9]+ mutations)' "$SCRATCH/mutations.log" | sed 's/^/   /'
  ok "every reverted fix breaks at least one test that names it"
fi
rm -rf "$MUTATION_TREE"

# ── 11. The deployed box (opt-in) ───────────────────────────────────────────
step "11. deployed site over HTTPS: every screen 200, fonts load, no leaks"
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
  if [[ "$LOGIN_CODE" != "200" && "$LOGIN_CODE" != "302" ]]; then
    bad "could not log in to ${A4A_URL} (HTTP $LOGIN_CODE)"
  else
    for path in / /campaigns /contacts /history /blocklist /usage /settings /health; do
      CODE=$(curl -s -o "$SCRATCH/page.html" -w "%{http_code}" -b "$JAR" "${A4A_URL}${path}")
      if [[ "$CODE" != "200" ]]; then
        bad "${path} returned $CODE"
        continue
      fi
      if grep -qiE 'telnyx|twilio' "$SCRATCH/page.html"; then
        bad "${path} names the carrier"
      elif grep -qE "\{'errors'|\[\{'code'" "$SCRATCH/page.html"; then
        bad "${path} renders a raw provider payload"
      else
        ok "${path} 200, no carrier name, no raw payload"
      fi
    done
    # The stylesheet and one font, because a deploy that skipped `npm run
    # build:css` serves a 404 here and the whole app renders unstyled.
    for asset in /static/app.css /static/fonts/inter-latin-400-normal.woff2; do
      CODE=$(curl -s -o /dev/null -w "%{http_code}" -b "$JAR" "${A4A_URL}${asset}")
      [[ "$CODE" == "200" ]] && ok "${asset} 200" || bad "${asset} returned $CODE"
    done
    for path in /api/reports/campaigns /api/campaigns; do
      BODY=$(curl -s -b "$JAR" "${A4A_URL}${path}")
      if grep -qiE 'telnyx|twilio|carrier_cost|wholesale' <<<"$BODY"; then
        bad "${path} names the carrier or our cost"
      else
        ok "${path} names no carrier and no wholesale figure"
      fi
    done
  fi
fi

printf '\n'
if [[ $FAIL -eq 0 ]]; then
  echo "ACCEPT PASS — session 5f Part A"
  [[ $WITH_REMOTE -eq 0 ]] && echo "  (criterion 11 not run; it needs the deploy)"
else
  echo "ACCEPT FAILED — see above"
fi
exit $FAIL
