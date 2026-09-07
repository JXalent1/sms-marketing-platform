"""Revert one session-5g fix at a time and check that the suite notices.

Run by `agent/accept-5g.sh` as check 8b. Check 8 on its own is weak: the two new
test modules fail against the pre-fix tree at *import*, because the symbols they
test did not exist yet. "The module is new" is not "the bug was real", and it is
certainly not "these tests would catch the bug coming back".

So this reverts each fix **behaviourally** — inside the current API, one at a
time — and requires at least one test to go red for each. A test that stays green
through the revert of the thing it names is decoration.

Nothing here sends. It edits a scratch copy of the tree, never the repo, and
restores every file between mutations.

    python3 agent/mutate-5g.py <scratch-tree> <python>
"""
import pathlib, re, subprocess, sys

SCRATCH = pathlib.Path(sys.argv[1])
PY = sys.argv[2]
REPO = pathlib.Path(__file__).resolve().parent.parent
TARGETS = ["tests/test_blocklist_correctness.py", "tests/test_carrier_error_surfaces.py"]

MUTATIONS = {
 "R1 transient guard removed": [
   ("app/sms/compliance.py",
    'TRANSIENT_FAILURE_MARKERS = ("temporar", "retry", "congestion", "try again")',
    'TRANSIENT_FAILURE_MARKERS = ()')],
 "R2 codes matched in prose again": [
   ("app/sms/compliance.py",
    '"not routable", "invalid phone number", "deemed invalid",\n)',
    '"not routable", "invalid phone number", "deemed invalid",\n    "21610", "21612", "40300",\n)'),
   ("app/sms/compliance.py",
    'AUTO_BLOCK_ERROR_CODES = frozenset({"21612", "40300"})',
    'AUTO_BLOCK_ERROR_CODES = frozenset()'),
   ("app/sms/compliance.py",
    'CARRIER_OPT_OUT_CODES = frozenset({"21610"})',
    'CARRIER_OPT_OUT_CODES = frozenset()')],
 "R3 word boundaries removed": [
   ("app/sms/compliance.py",
    'return re.compile(r"\\b(?:%s)\\b" % "|".join(re.escape(f) for f in fragments),',
    'return re.compile(r"(?:%s)" % "|".join(re.escape(f) for f in fragments),')],
 "R4 region wording blocks again": [
   ("app/sms/compliance.py",
    'CONFIGURATION_ERROR_MARKERS = ("has not been enabled for the region",)',
    'CONFIGURATION_ERROR_MARKERS = ()'),
   ("app/sms/compliance.py",
    '"not routable", "invalid phone number", "deemed invalid",\n)',
    '"not routable", "invalid phone number", "deemed invalid",\n    "has not been enabled for the region",\n)')],
 "R5 opt-outs counted as unreachable": [
   ("app/services/blocklist_service.py",
    'OPT_OUT_REASONS = ("stop_keyword", "carrier_opt_out")',
    'OPT_OUT_REASONS = ("stop_keyword",)')],
 "R6 dashboard uses the literal again": [
   ("app/services/dashboard_service.py",
    'BlockedNumber.reason.in_(blocklist_service.OPT_OUT_REASONS),',
    'BlockedNumber.reason == "stop_keyword",')],
 "R7 strip_payload is a no-op": [
   ("app/sms/phone.py",
    '    text = strip_payload(text) or NO_READABLE_DETAIL',
    '    text = text')],
 "R8 provider stringifies again": [
   ("app/sms/providers/telnyx.py",
    'error=describe_send_error(e)', 'error=str(e)'),
   ("app/sms/providers/telnyx.py",
    '    body = getattr(exc, "body", None)',
    '    return str(exc)\n    body = getattr(exc, "body", None)')],
 "R9 webhook drops the carrier code": [
   ("app/routers/webhooks/telnyx.py",
    'error_code=code)', 'error_code=None)'),
   ("app/routers/webhooks/twilio.py",
    'error_code=form.get("ErrorCode"),', 'error_code=None,')],
 "R10 carrier_opt_out reason not used": [
   ("app/sms/compliance.py",
    'CARRIER_OPT_OUT = "carrier_opt_out"', 'CARRIER_OPT_OUT = "delivery_failure"')],
 # Decision 004. The residual it accepts — a line the carrier calls only
 # "unreachable" survives — is a cost argument, not an obvious one, so the
 # fragment is exactly the kind of thing a future session puts back in good
 # faith. This is what makes that loud.
 "R11 unreachable back in the block list": [
   ("app/sms/compliance.py",
    '"not routable", "invalid phone number", "deemed invalid",\n)',
    '"not routable", "invalid phone number", "deemed invalid", "unreachable",\n)')],
}

# ── The precondition. P1's first run was worthless without it ──────────────
# Back-ported by session P2 (A4). P1's first mutation run reported 38 caught and
# 0 survived on a scratch tree a killed run had left already mutated, so every
# verdict sat on top of a leftover edit. `accept-P1.sh` did `rm -rf` before its
# rsync, which is weaker and only holds when the harness is run through that
# script. CLAUDE.md states that every harness verifies its tree and prints
# `SCRATCH VERIFIED PRISTINE`; before P2 that was true of two of seven.
#
# Read the results the same way: check that the tests failing for a mutation are
# the tests that **name** it. A harness is code and it fails the same ways.
files = sorted({path for muts in MUTATIONS.values() for path, _, _ in muts})
dirty = [p for p in files
         if (SCRATCH / p).read_bytes() != (REPO / p).read_bytes()]
if dirty:
    print("SCRATCH TREE IS NOT PRISTINE — every verdict below would sit on top "
          "of a leftover edit:")
    for path in dirty:
        print(f"   -> {path} differs from the repo")
    sys.exit(2)
print(f"SCRATCH VERIFIED PRISTINE ({len(files)} files byte-identical to the repo)")

pristine = {p: (SCRATCH / p).read_text() for p in files}

survivors = []
for name, mutations in MUTATIONS.items():
    for path, old, new in mutations:
        f = SCRATCH / path
        text = f.read_text()
        if old not in text:
            print(f"{name}: PATCH DID NOT APPLY to {path} — {old[:50]!r}")
            break
        f.write_text(text.replace(old, new, 1))
    result = subprocess.run(
        [PY, "-m", "pytest", *TARGETS, "-q", "--tb=no", "-p", "no:cacheprovider"],
        cwd=SCRATCH, capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
             "HOME": str(pathlib.Path.home())})
    tail = [l for l in result.stdout.splitlines() if "passed" in l or "failed" in l or "error" in l]
    failed = re.findall(r"^FAILED (\S+)", result.stdout, re.M)
    errors = re.findall(r"^ERROR (\S+)", result.stdout, re.M)
    verdict = "CAUGHT" if (failed or errors) else "*** NOT CAUGHT ***"
    print(f"\n{name}\n  {verdict}  ({len(failed)} failed, {len(errors)} errors)")
    for t in failed[:6]:
        print(f"    {t.split('::')[-1]}")
    if len(failed) > 6:
        print(f"    ... and {len(failed)-6} more")
    if not failed and not errors:
        survivors.append(name)
        print("    " + (tail[-1] if tail else result.stdout[-200:]))
    for path, text in pristine.items():
        (SCRATCH / path).write_text(text)

if survivors:
    print(f"\n{len(survivors)} reverted fix(es) that no test noticed:")
    for name in survivors:
        print(f"   -> {name}")
sys.exit(1 if survivors else 0)
