"""Revert one session-P2 guard at a time and check that the suite notices.

Run by `agent/accept-P2.sh` as check 9. "The new test modules are red against
the pre-P2 tree" is true and nearly meaningless — they fail there at *import*,
because `GooglePlacesSource`, `taxonomy` and `api_budget` did not exist. This
reverts each guard **behaviourally**, inside the current API, one at a time, in
a scratch copy of the tree, and requires at least one test to go red for each.

The three the spec names, and why each is the session:

  - **The exclusion list** (`X*`). An auction house or an estate liquidator on
    this list is a competitor holding a seat on the client's own marketing
    channel. `X3` is the one that matters most: it re-adds the shell wholesaler
    exclusion `decisions/009` removed, which is what a later session reading the
    plan's old wording would do.
  - **The pre-spend dedup** (`D*`). Both meters. A request is charged for
    asking, so nothing downstream can save a repeat; a lookup is charged per
    number, so a business he already has costs $0.0025 to rediscover.
  - **The request cap** (`C*`). $35 per thousand, checked before the call.
    `C3` is the inclusive-ceiling off-by-one, and it is here because the
    mutation written first — reverting an `if self.cap <= 0: return False` —
    **survived**, which is how the guard above it was found to be dead code
    that no arrangement could reach. See `api_budget.allows()`.

And two the spec does not name but which are the other half of `decisions/009`:
`R1` collapses radius back onto the category, and `R2` deletes the rule
entirely.

## This harness verifies its own preconditions first

P1's first mutation run reported 38 caught, 0 survived and was worthless: a run
killed on a timeout had left the scratch tree already mutated, so `pristine` was
captured from a dirty tree and every verdict sat on top of a leftover edit. So
before a single patch is applied, every file this script touches is compared
byte for byte against the repo's copy, and the run stops if any differs. It
prints `SCRATCH VERIFIED PRISTINE` when they match.

Read the results the same way: check that the tests failing for a mutation are
the tests that **name** it. A harness is code and it fails the same ways.

Nothing here sends, and nothing here calls a paid API — neither Google nor the
carrier. It edits a scratch copy of the tree, never the repo, and restores every
file between mutations.

    python3 agent/mutate-P2.py <scratch-tree> <python>
"""
import pathlib, re, subprocess, sys

SCRATCH = pathlib.Path(sys.argv[1])
PY = sys.argv[2]
REPO = pathlib.Path(__file__).resolve().parent.parent
TARGETS = [
    "tests/test_google_places.py",
    "tests/test_taxonomy.py",
    "tests/test_prospect_pipeline.py",
    "tests/test_prospect_review.py",
]

MUTATIONS = {
 # ── A1: the exclusion list ─────────────────────────────────────────────────
 "X1 the exclusion list is never consulted — every auction house and estate "
 "liquidator becomes a prospect": [
   ("app/sources/exclusions.py",
    "    padded = normalize_name(business_name)\n"
    "    if not padded.strip():\n"
    "        return None",
    "    padded = normalize_name(business_name)\n"
    "    if True:\n"
    "        return None")],

 "X2 the check is dropped from record_prospect, so the list exists and nothing "
 "calls it — a guard with one call site, moved to none": [
   ("app/services/prospect_ingest.py",
    "    excluded = excluded_reason(business_name)\n"
    "    if excluded is not None:",
    "    excluded = excluded_reason(business_name)\n"
    "    if False:")],

 "X3 shell wholesalers and importers are excluded again — decisions/009 "
 "reversed by a session reading the plan's old wording": [
   ("app/sources/exclusions.py",
    '    Exclusion("we_buy_houses", "\\"We buy houses\\" operator",',
    '    Exclusion("shell_wholesaler", "Shell wholesaler or importer",\n'
    '              ("wholesaler", "importer", "distributor"),\n'
    '              "They consign surplus stock rather than bidding on it."),\n'
    '    Exclusion("we_buy_houses", "\\"We buy houses\\" operator",')],

 "X4 the matcher loses its word anchor, so `auction` fires on `precaution`": [
   ("app/sources/exclusions.py",
    '            if f" {phrase}" in padded:',
    "            if phrase in padded:")],

 "X5 `estate liquidat` widens to `liquidat`, taking the closeout stores that "
 "are one of our own search terms": [
   ("app/sources/exclusions.py",
    '    Exclusion("estate_liquidator", "Estate liquidator", ("estate liquidat",),',
    '    Exclusion("estate_liquidator", "Estate liquidator", ("liquidat",),')],

 "X6 an excluded business is counted `invalid`, so a term finding nothing but "
 "competitors reads as a broken source": [
   ("app/services/prospect_ingest.py",
    '        return _count(job, "records_excluded", "excluded")',
    '        return _count(job, "records_invalid", "invalid")')],

 # ── A2 / decisions/009: radius belongs to the group ────────────────────────
 "R1 radius collapses back onto the category, so one value has to serve two "
 "national seashell groups and one regional one": [
   ("app/sources/taxonomy.py",
    "    if item.radius_miles is INHERIT:\n"
    "        return prospect_scoring.radius_for(item.category_slug)\n"
    "    return item.radius_miles",
    "    return prospect_scoring.radius_for(item.category_slug)")],

 "R2 the radius rule is not applied to a result, so a regional group pays to "
 "screen a yard 962 miles away": [
   ("app/sources/google_places.py",
    "        if search.radius_miles is not None and miles is not None:\n"
    "            if miles > search.radius_miles:",
    "        if False:\n"
    "            if miles > search.radius_miles:")],

 "R3 distance is measured from the sweep's centre rather than from the auction "
 "house, so a Gulf coast sweep reports everything as local": [
   ("app/sources/google_places.py",
    "        origin = (settings.PROSPECT_ORIGIN_LAT, settings.PROSPECT_ORIGIN_LON)\n"
    "        bias = None",
    "        origin = search.sweep.center or (settings.PROSPECT_ORIGIN_LAT,\n"
    "                                        settings.PROSPECT_ORIGIN_LON)\n"
    "        bias = None")],

 "R4 marine and seashells lose their radius entries and fall back to the "
 "conservative 150-mile default": [
   ("app/core/config.py",
    '        "memorabilia": None,\n'
    '        "marine": None,\n'
    '        "seashells": None,\n',
    '        "memorabilia": None,\n')],

 "R5 the two national seashell groups inherit their radius instead of stating "
 "it, so an .env pinning the old map makes them regional": [
   ("app/sources/taxonomy.py",
    '        slug="shell_wholesale",\n'
    '        label="Shell wholesalers, importers and distributors",\n'
    '        category_slug="seashells",\n'
    '        priority=1,\n'
    "        radius_miles=None,",
    '        slug="shell_wholesale",\n'
    '        label="Shell wholesalers, importers and distributors",\n'
    '        category_slug="seashells",\n'
    '        priority=1,\n'
    "        radius_miles=INHERIT,")],

 "R6 the aggregate group runs national, so crushed shell is offered to a buyer "
 "who would pay more in freight than for the lot": [
   ("app/sources/taxonomy.py",
    '        radius_miles=150,\n'
    "        sweeps=(),                                    # home sweep only",
    "        radius_miles=None,\n"
    "        sweeps=(),                                    # home sweep only")],

 "R7 the seashell sweeps collapse to one, so the Gulf coast search that "
 "decisions/009 asks for never runs": [
   ("app/sources/taxonomy.py",
    "        sweeps=(NATIONAL, GULF_COAST),\n"
    '        terms=("seashell wholesaler", "seashell importer",',
    "        sweeps=(NATIONAL,),\n"
    '        terms=("seashell wholesaler", "seashell importer",')],

 "R8 the ledger key drops the sweep, so running the national sweep retires the "
 "Gulf coast one unrun": [
   ("app/sources/taxonomy.py",
    '        return f"{self.group.slug}:{self.sweep.name}:{self.term}"',
    '        return f"{self.group.slug}:{self.term}"')],

 # ── A3: dedup before spending ──────────────────────────────────────────────
 "D1 a business the client already has becomes a prospect and is screened at "
 "$0.0025 for an answer his contact row already implies": [
   ("app/services/prospect_ingest.py",
    "    if is_known_contact(db, phone):\n"
    '        return _count(job, "records_known", "known")',
    "    if False:\n"
    '        return _count(job, "records_known", "known")')],

 "D2 the search ledger is not consulted, so every nightly re-run pays $0.035 a "
 "query to be told the same sixty businesses": [
   ("app/services/scrape_runner.py",
    "    already = searched_recently(db, name, repeat_days)",
    "    already = set()")],

 "D3 the NULL branch is dropped from the freshness query, so every job row "
 "written before the meter existed falls out of the ledger and is paid for "
 "again — `NULL = 0` is NULL in SQL, not true": [
   ("app/services/scrape_runner.py",
    "                    or_(ScrapeJob.api_requests_skipped == 0,\n"
    "                        ScrapeJob.api_requests_skipped.is_(None)))",
    "                    ScrapeJob.api_requests_skipped == 0)")],

 "D4 the ledger counts a failed search as searched, so a niche stays "
 "permanently unsearched after one bad night": [
   ("app/services/scrape_runner.py",
    '                    ScrapeJob.status == "completed",\n',
    "")],

 "D5 a repeat window of zero is read as `never repeat` rather than as `no "
 "window`": [
   ("app/services/scrape_runner.py",
    "    if days <= 0:\n"
    "        return set()",
    "    if days <= 0:\n"
    "        days = 36500")],

 # ── A3: the request cap ────────────────────────────────────────────────────
 "C1 the cap is not checked before the request — a ceiling that is a ledger": [
   ("app/sources/google_places.py",
    "            if request_budget is not None and not request_budget.allows():",
    "            if False:")],

 "C2 the cap is checked but the refusal is not recorded, so an incomplete "
 "search is indistinguishable from an exhausted one": [
   ("app/services/scrape_runner.py",
    "            job.api_requests_skipped = request_budget.skipped",
    "            job.api_requests_skipped = 0")],

 # `<=` rather than `<` is the classic off-by-one, and it is the whole rule
 # here: it turns a cap of zero into a cap of one — "off" into "one more" — and
 # lets every cap permit one request past its ceiling. The first version of this
 # mutation reverted an `if self.cap <= 0: return False` sitting above
 # `remaining > 0`, and it survived, because with an integer counter that `if`
 # decides nothing the comparison below it does not already decide. The guard
 # was dead code; this is the line that actually holds the ceiling.
 "C3 the ceiling is inclusive, so a cap of zero becomes a cap of one and every "
 "cap permits one request past itself": [
   ("app/services/api_budget.py",
    "        return self.spent < self.cap",
    "        return self.spent <= self.cap")],

 "C4 the budget starts from zero every run, so a monthly cap is a per-run one": [
   ("app/services/api_budget.py",
    "        return cls(source_name, spent=spend_this_month(db, source_name, month),\n"
    "                   **terms())",
    "        return cls(source_name, spent=0, **terms())")],

 "C5 the month is ignored, so the cap never resets": [
   ("app/services/api_budget.py",
    '                    ScrapeJob.started_at.like(f"{month}%"))\n',
    "                    )\n")],

 "D6 the freshness query subtracts every term ever interrupted rather than "
 "asking whether THIS job was, so a search interrupted once is paid for every "
 "night forever": [
   ("app/services/scrape_runner.py",
    "                    ScrapeJob.started_at >= cutoff.isoformat(),\n"
    "                    or_(ScrapeJob.api_requests_skipped == 0,\n"
    "                        ScrapeJob.api_requests_skipped.is_(None)))",
    "                    ScrapeJob.started_at >= cutoff.isoformat())")],

 "C8 the cap's skipped count subtracts the already-searched twice, so every "
 "run after the first under-reports what it did not do": [
   ("app/services/scrape_runner.py",
    "            summary[\"skipped_cap\"] = sum(\n"
    "                1 for later in plan[index:] if later.ledger_key not in already)",
    "            summary[\"skipped_cap\"] = max(\n"
    "                0, len(plan) - index - summary[\"skipped_recent\"])")],

 "C6 the plan opens a job for a search it cannot pay for, and that empty "
 "completed job then retires the search in the ledger": [
   ("app/services/scrape_runner.py",
    "        budget = api_budget.RequestBudget.for_month(db, name)\n"
    "        if budget is not None and not budget.allows():",
    "        budget = api_budget.RequestBudget.for_month(db, name)\n"
    "        if False:")],

 "C7 the requests a run made are not written to the job, so the monthly meter "
 "reads zero forever and the cap never binds": [
   ("app/services/scrape_runner.py",
    "            job.api_requests = request_budget.requests_made",
    "            job.api_requests = 0")],

 # ── A1 / A5: the rationale and the provenance the queue renders ────────────
 "P1 the client is shown the machine ledger key instead of the search term he "
 "can judge": [
   ("app/sources/google_places.py",
    "            search_term=search.term,",
    "            search_term=search.ledger_key,")],

 "P2 the record carries no buyer rationale, so nothing reaches a reviewer who "
 "could disagree with it": [
   ("app/sources/google_places.py",
    "            buyer_rationale=search.group.buyer_rationale,",
    '            buyer_rationale="",')],

 "P3 source_url becomes the API endpoint, which is the field the CSV export "
 "hands the client": [
   ("app/sources/google_places.py",
    '            source_url=place.get("googleMapsUri")\n'
    '            or f"https://www.google.com/maps/place/?q=place_id:{place.get(\'id\', \'\')}",',
    "            source_url=SEARCH_URL,")],

 # ── The failure paths ──────────────────────────────────────────────────────
 "E1 a failed request is described by stringifying the body, so the key and "
 "the raw payload land in a column stored unscrubbed": [
   ("app/sources/google_places.py",
    '    text = f"The discovery search failed (HTTP {status_code})."\n'
    "    if message:\n"
    '        text = f"{text} {message}"\n'
    "    key = settings.GOOGLE_PLACES_API_KEY\n"
    "    return text.replace(key, \"[redacted]\") if key else text",
    '    return f"The discovery search failed (HTTP {status_code}): {body}"')],

 "E2 cleanup does not close the HTTP client — one leaked handle per search": [
   ("app/sources/google_places.py",
    "        if client is not None:\n"
    "            try:\n"
    "                client.close()",
    "        if False:\n"
    "            try:\n"
    "                client.close()")],

 "E3 a key without Enterprise tier reports finding nothing instead of saying "
 "the phone field is missing": [
   ("app/sources/google_places.py",
    "            if places and not any(_phone_of(p) for p in places):",
    "            if False:")],

 "E4 a source with no key builds a client anyway and searches with an empty "
 "credential": [
   ("app/sources/google_places.py",
    "        if not self.api_key:\n"
    "            raise ValueError(NO_API_KEY)",
    "        if False:\n"
    "            raise ValueError(NO_API_KEY)")],

 "E5 a source reopens a client after cleanup, which is how one leak becomes "
 "seventeen": [
   ("app/sources/google_places.py",
    "        if self._closed:\n"
    "            raise RuntimeError(",
    "        if False:\n"
    "            raise RuntimeError(")],
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
