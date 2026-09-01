"""Revert one session-P1b guard at a time and check that the suite notices.

Run by `agent/accept-P1b.sh` as check 9. "The new test modules are red against
the pre-P1b tree" is true and nearly meaningless — they fail there at *import*,
because `TelnyxLineTypeProvider` and `_wholesale_scan` did not exist. This
reverts each guard **behaviourally**, inside the current API, one at a time, in
a scratch copy of the tree, and requires at least one test to go red for each.

Every mutation is a plausible edit rather than a wrecking ball:

  - `C1` is the cap check deleted by somebody who read it as belt and braces
    because `screen()` already works the budget out. It is the whole session:
    lookups and sends draw on the same balance.
  - `C3` is "zero means no limit", which is what `cap <= 0` would mean if the
    guard were written the obvious way round. It turns the off switch into the
    on switch.
  - `U1`/`U2` are the two halves of "never spend on a number nobody will use",
    and P1 already shipped this bug once in the other direction.
  - `S1` is the assertion this session exists to fix, put back: a bare decimal
    tested against a whole body. It must fail on the timestamp.
  - `M1` is the retry that overwrites the spend it should be adding to, which
    is a cap that quietly permits more than it says.

## This harness verifies its own preconditions first

P1's first mutation run reported 38 caught, 0 survived and was worthless: a run
killed on a timeout had left the scratch tree already mutated, so `pristine` was
captured from a dirty tree and every verdict sat on top of a leftover edit. So
before a single patch is applied, every file this script touches is compared
byte for byte against the repo's copy, and the run stops if any differs. It
prints `SCRATCH VERIFIED PRISTINE` when they match.

Read the results the same way: check that the tests failing for a mutation are
the tests that **name** it. A harness is code and it fails the same ways.

Nothing here sends, and nothing here calls a paid API. It edits a scratch copy
of the tree, never the repo, and restores every file between mutations.

    python3 agent/mutate-P1b.py <scratch-tree> <python>
"""
import pathlib, re, subprocess, sys

SCRATCH = pathlib.Path(sys.argv[1])
PY = sys.argv[2]
REPO = pathlib.Path(__file__).resolve().parent.parent
TARGETS = [
    "tests/test_lookup_provider.py",
    "tests/test_wholesale_scan.py",
    "tests/test_prospect_pipeline.py",
]

MUTATIONS = {
 # ── A1: the spend cap ──────────────────────────────────────────────────────
 "C1 the cap is not checked before the call — screening spends the balance "
 "the next campaign sends on": [
   ("app/services/lookup_service.py",
    "    if not budget.allows(cost_per_lookup()):",
    "    if False:")],

 "C2 the budget starts from zero every run, so a monthly cap is a per-run one": [
   ("app/services/lookup_service.py",
    "        return cls(monthly_cap(), spend_this_month(db, month))",
    '        return cls(monthly_cap(), Decimal("0"))')],

 "C3 a cap of zero means unlimited rather than off": [
   ("app/services/lookup_service.py",
    "        if self.cap <= 0:\n"
    "            return False\n"
    "        return cost <= self.remaining",
    "        return cost <= self.remaining")],

 "C4 the month is ignored, so the cap never resets and eventually stops "
 "screening for good": [
   ("app/services/lookup_service.py",
    "            .filter(PhoneLookup.cost.isnot(None),\n"
    '                    PhoneLookup.looked_up_at.like(f"{month}%"))',
    "            .filter(PhoneLookup.cost.isnot(None))")],

 "C5 a number refused for cost is written down as an answer, so it can never "
 "be screened later": [
   ("app/services/lookup_service.py",
    '    return {"line_type": "unknown", "cached": False, "ok": False,\n'
    '            "skipped": reason}',
    '    return {"line_type": "unknown", "cached": True, "ok": True,\n'
    '            "skipped": None}')],

 "C6 skipped numbers are counted as lookups performed, so the job cost is "
 "reported for calls nobody made": [
   ("app/services/lookup_service.py",
    '            if outcome["skipped"]:\n'
    '                skipped[outcome["skipped"]] += 1\n'
    "            else:\n"
    "                performed += 1",
    "            performed += 1")],

 "M1 a retry overwrites the spend instead of adding to it — a cap that "
 "under-counts permits more than it says": [
   ("app/services/lookup_service.py",
    "        row.cost = str(total) if total else None",
    "        row.cost = cost")],

 "M2 an unreadable cost from a provider is stored raw, so one row breaks "
 "every later cap check": [
   ("app/services/lookup_service.py",
    "        try:\n"
    "            return Decimal(str(reported))\n"
    "        except InvalidOperation:",
    "        try:\n"
    "            return reported\n"
    "        except InvalidOperation:")],

 "M3 a pass reports rate x attempts instead of what it was charged, so a "
 "day of timeouts reads as money spent": [
   ("app/services/lookup_service.py",
    "        charged = budget.spent - opening",
    "        charged = cost_per_lookup() * performed")],

 # ── A1: never spend on a number nobody will use ────────────────────────────
 "U1 the unusable check is gone from the one place a call is made": [
   ("app/services/lookup_service.py",
    "    if normalized in unusable:\n"
    '        return _refused("not_usable")',
    "    if False:\n"
    '        return _refused("not_usable")')],

 "U2 rejected prospects are looked up again on every re-run of the search "
 "that finds them": [
   ("app/services/lookup_service.py",
    "    rejected = {row[0] for row in\n"
    "                db.query(ProspectRejection.phone)\n"
    "                .filter(ProspectRejection.phone.in_(wanted)).all()}",
    "    rejected = set()")],

 "U3 blocklisted numbers are paid for": [
   ("app/services/lookup_service.py",
    "    blocked = {row[0] for row in\n"
    "               db.query(BlockedNumber.phone)\n"
    "               .filter(BlockedNumber.phone.in_(wanted)).all()}",
    "    blocked = set()")],

 # ── A1: the provider itself ────────────────────────────────────────────────
 "P1 a landline reads as a mobile — the 2,526-number campaign, one prospect "
 "at a time": [
   ("app/sms/providers/telnyx_lookup.py",
    '    "fixed line": "landline",',
    '    "fixed line": "mobile",')],

 "P2 an ambiguous line type is treated as a mobile": [
   ("app/sms/providers/telnyx_lookup.py",
    '    "fixed line or mobile": "unknown",',
    '    "fixed line or mobile": "mobile",')],

 "P3 a carrier's word we do not map falls through as mobile instead of "
 "unknown": [
   ("app/sms/providers/telnyx_lookup.py",
    '    return CARRIER_LINE_TYPES.get(key, "unknown")',
    '    return CARRIER_LINE_TYPES.get(key, "mobile")')],

 "P4 a response with no line type is cached as an answer — a parsing bug "
 "becomes a permanent hole in the list": [
   ("app/sms/providers/telnyx_lookup.py",
    "            return LineTypeResult(line_type=\"unknown\", ok=False,\n"
    "                                  error=NO_CLASSIFICATION, cost=charged)",
    "            return LineTypeResult(line_type=\"unknown\", ok=True,\n"
    "                                  error=NO_CLASSIFICATION, cost=charged)")],

 "P5 a failed call is billed for, so the cap counts money nobody spent": [
   ("app/sms/providers/telnyx_lookup.py",
    "            return LineTypeResult(line_type=\"unknown\", ok=False,\n"
    "                                  error=describe_api_error(exc))",
    "            return LineTypeResult(line_type=\"unknown\", ok=False,\n"
    "                                  error=describe_api_error(exc), cost=charged)")],

 "P6 the provider raises instead of answering, taking the whole screening "
 "pass with it": [
   ("app/sms/providers/telnyx_lookup.py",
    "        try:\n"
    "            response = self.client.number_lookup.retrieve(phone, type=\"carrier\")\n"
    "        except Exception as exc:",
    "        try:\n"
    "            response = self.client.number_lookup.retrieve(phone, type=\"carrier\")\n"
    "        except ZeroDivisionError as exc:")],

 "P7 the raw SDK error string is stored, payload and all": [
   ("app/sms/providers/telnyx_lookup.py",
    "                                  error=describe_api_error(exc))",
    "                                  error=str(exc))")],

 "P8 a misconfigured screening provider takes the whole app down instead of "
 "degrading": [
   ("app/sms/lookup.py",
    "    except Exception as exc:\n"
    "        logger.error(",
    "    except LookupError as exc:\n"
    "        logger.error(")],

 # ── A2: the assertion that kept failing a sound build ──────────────────────
 "S1 the wholesale scan goes back to a substring sweep, which matches a "
 "microsecond timestamp": [
   ("tests/_wholesale_scan.py",
    "    present = set(numbers_in(text))\n"
    "    for label, figure in wholesale_figures().items():\n"
    "        assert figure not in present, (",
    "    present = text or ''\n"
    "    for label, figure in wholesale_figures().items():\n"
    "        assert str(figure) not in present, (")],

 "S2 the number tokeniser loses its boundaries, so 40.009312 is read in "
 "pieces": [
   ("tests/_wholesale_scan.py",
    'DECIMAL = re.compile(r"(?<![\\d.])(?:0|[1-9]\\d*)\\.\\d+")',
    'DECIMAL = re.compile(r"\\d*\\.\\d{1,4}")')],

 "S2b clock times are no longer removed first, so 14:23:00.009000 reads as "
 "our rate — the check-7b failure, put back": [
   ("tests/_wholesale_scan.py",
    'for token in DECIMAL.findall(CLOCK_TIME.sub(" ", text or "")):',
    'for token in DECIMAL.findall(text or ""):')],

 "S2c the tokeniser accepts a redundant leading zero, so a clock component "
 "in a format CLOCK_TIME has not seen reads as a price": [
   ("tests/_wholesale_scan.py",
    'DECIMAL = re.compile(r"(?<![\\d.])(?:0|[1-9]\\d*)\\.\\d+")',
    'DECIMAL = re.compile(r"(?<![\\d.])\\d*\\.\\d+")')],

 "S3 string fields are not scanned, so a rate rendered into a sentence goes "
 "through": [
   ("tests/_wholesale_scan.py",
    "        elif isinstance(value, str):\n"
    "            candidates = numbers_in(value)",
    "        elif isinstance(value, str):\n"
    "            candidates = []")],

 "S4 the scan only knows about the per-segment rate, so the screening cost "
 "is invisible to it": [
   ("tests/_wholesale_scan.py",
    '        "PROSPECT_LOOKUP_COST_PER_NUMBER (our per-lookup cost)":\n'
    "            Decimal(str(settings.PROSPECT_LOOKUP_COST_PER_NUMBER)),",
    "")],

 "S5 a field named for our rate is no longer a leak on its own": [
   ("tests/_wholesale_scan.py",
    '        assert "wholesale" not in path.lower(), (',
    '        assert "wholesale" not in "", (')],
}

# ── The precondition. P1's first run was worthless without it ──────────────
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

survivors, unapplied = [], []
for name, mutations in MUTATIONS.items():
    applied = True
    for path, old, new in mutations:
        f = SCRATCH / path
        text = f.read_text()
        if old not in text:
            print(f"\n{name}\n  *** PATCH DID NOT APPLY *** to {path} — {old[:60]!r}")
            unapplied.append(name)
            applied = False
            break
        f.write_text(text.replace(old, new, 1))
    if not applied:
        for path, text in pristine.items():
            (SCRATCH / path).write_text(text)
        continue

    result = subprocess.run(
        [PY, "-m", "pytest", *TARGETS, "-q", "--tb=no", "-p", "no:cacheprovider"],
        cwd=SCRATCH, capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
             "HOME": str(pathlib.Path.home())})
    tail = [l for l in result.stdout.splitlines()
            if "passed" in l or "failed" in l or "error" in l]
    failed = re.findall(r"^FAILED (\S+)", result.stdout, re.M)
    errors = re.findall(r"^ERROR (\S+)", result.stdout, re.M)
    verdict = "CAUGHT" if (failed or errors) else "*** NOT CAUGHT ***"
    print(f"\n{name}\n  {verdict}  ({len(failed)} failed, {len(errors)} errors)")
    for t in failed[:5]:
        print(f"    {t.split('::')[-1]}")
    if len(failed) > 5:
        print(f"    ... and {len(failed) - 5} more")
    if not failed and not errors:
        survivors.append(name)
        print("    " + (tail[-1] if tail else result.stdout[-200:]))
    for path, text in pristine.items():
        (SCRATCH / path).write_text(text)

print(f"\n{len(MUTATIONS)} mutations, {len(survivors)} survived, "
      f"{len(unapplied)} failed to apply")
if unapplied:
    # A patch that does not apply proves nothing and must not read as a pass.
    # It usually means the code moved and this file was not updated with it.
    print("\nmutations that could not be applied (fix the harness, not the code):")
    for name in unapplied:
        print(f"   -> {name}")
if survivors:
    print("\nreverted guard(s) that no test noticed:")
    for name in survivors:
        print(f"   -> {name}")
sys.exit(1 if (survivors or unapplied) else 0)
