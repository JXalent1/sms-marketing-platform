"""Revert one session-P1 guard at a time and check that the suite notices.

Run by `agent/accept-P1.sh` as check 10. "The new test modules are red against
the pre-P1 tree" is true and nearly meaningless — they fail there at *import*,
because `Prospect` and `lookup_service` did not exist — so this reverts each
guard **behaviourally**, inside the current API, one at a time, in a scratch copy
of the tree, and requires at least one test to go red for each.

Every mutation is a plausible edit rather than a wrecking ball, which is what
makes a green run mean something:

  - `R1` is the tidy-up somebody writes the first time the queue looks empty on
    a box with no screening credential: "unknown just means we haven't checked,
    let him promote it." It is how the 2,526-landline campaign comes back.
  - `R4` is "an error is an answer too, and re-querying costs money" — true of a
    landline and false of a timeout, and it turns one bad afternoon into a
    permanent hole in the list.
  - `R7` is the obvious simplification of the runner: the source is a generator,
    so why not just iterate it? Because a source blocked inside a third-party
    call never comes back, and the deadline stops meaning anything.
  - `R12` is the reasonable-looking scoping of the screening pass to the
    sightings a job wrote — which silently stops screening any prospect a repeat
    of the same search finds.

Nothing here sends, and nothing here calls a paid API. It edits a scratch copy
of the tree, never the repo, and restores every file between mutations.

    python3 agent/mutate-P1.py <scratch-tree> <python>
"""
import pathlib, re, subprocess, sys

SCRATCH = pathlib.Path(sys.argv[1])
PY = sys.argv[2]
TARGETS = [
    "tests/test_prospect_pipeline.py",
    "tests/test_prospect_review.py",
]

MUTATIONS = {
 # ── A4: the line-type gate ─────────────────────────────────────────────────
 "R1 an unscreened number is promote-eligible — the gate waves through "
 "everything on a box with no screening": [
   ("app/services/lookup_service.py",
    'PROMOTABLE_LINE_TYPES = ("mobile", "voip")',
    'PROMOTABLE_LINE_TYPES = ("mobile", "voip", "unknown")')],

 "R2 landlines are promote-eligible again": [
   ("app/services/lookup_service.py",
    'PROMOTABLE_LINE_TYPES = ("mobile", "voip")',
    'PROMOTABLE_LINE_TYPES = ("mobile", "voip", "landline")')],

 "R3 a line type nobody wrote a refusal for falls through as promotable": [
   ("app/services/lookup_service.py",
    "    if line_type in PROMOTABLE_LINE_TYPES:\n"
    "        return None\n"
    "    return REFUSALS.get(line_type, UNSCREENED_REFUSAL)",
    "    return REFUSALS.get(line_type)")],

 # ── A4: the cache ──────────────────────────────────────────────────────────
 "R4 a failed lookup is cached as an answer — one outage, a permanent hole": [
   ("app/services/lookup_service.py",
    '        .filter(PhoneLookup.phone.in_(wanted), PhoneLookup.status == "ok")',
    '        .filter(PhoneLookup.phone.in_(wanted))')],

 "R5 the cache is never read, so every number is paid for again": [
   ("app/services/lookup_service.py",
    '    if row is not None and row.status == "ok":\n'
    '        return {"line_type": row.line_type, "cached": True, "ok": True}',
    '    if False:\n'
    '        return {"line_type": row.line_type, "cached": True, "ok": True}')],

 "R5b the batch screen re-looks-up the numbers it already found cached": [
   ("app/services/lookup_service.py",
    "        for phone in unique:\n"
    "            if phone in known:\n"
    "                continue",
    "        for phone in unique:\n"
    "            if False:\n"
    "                continue")],

 # ── A3: the runner ─────────────────────────────────────────────────────────
 "R6 cleanup runs only on the happy path, as the reference system's did": [
   ("app/services/scrape_runner.py",
    "    finally:\n"
    "        # Every way out of this function passes here, including the one where\n"
    "        # `_collect` raised before producing anything. The reference system's\n"
    "        # leak was one missing branch, not a missing concept.\n"
    "        try:\n"
    "            source.cleanup()\n"
    "            job.cleanup_ran = 1",
    "    finally:\n"
    "        try:\n"
    "            if not timed_out and failure is None:\n"
    "                source.cleanup()\n"
    "            job.cleanup_ran = 1")],

 "R7 the deadline is dropped and the generator is drained inline": [
   ("app/services/scrape_runner.py",
    "    deadline = time.monotonic() + timeout\n"
    "    produced = []\n"
    "    while True:\n"
    "        remaining = deadline - time.monotonic()\n"
    "        if remaining <= 0:",
    "    deadline = time.monotonic() + timeout\n"
    "    produced = []\n"
    "    while True:\n"
    "        remaining = 3600.0\n"
    "        if remaining <= 0:")],

 "R8 the job row does not record that cleanup ran": [
   ("app/services/scrape_runner.py",
    "            source.cleanup()\n"
    "            job.cleanup_ran = 1",
    "            source.cleanup()\n"
    "            job.cleanup_ran = 0")],

 "R9 a timed-out job is reported completed": [
   ("app/services/scrape_runner.py",
    '        if timed_out:\n'
    '            job.status = "timed_out"',
    '        if False:\n'
    '            job.status = "timed_out"')],

 "R10 a timeout throws away everything the source had already produced": [
   ("app/services/scrape_runner.py",
    "        result = source.ingest(db, records=produced, job=job)",
    "        result = source.ingest(db, records=[] if timed_out else produced, job=job)")],

 "R11 the runner keeps its own persistence loop instead of going through "
 "the source's ingest": [
   ("app/services/scrape_runner.py",
    "        result = source.ingest(db, records=produced, job=job)\n"
    "        job.records_yielded += result.total",
    "        for one in produced:\n"
    "            prospect_service.record_prospect(db, one, source_name=source.name,\n"
    "                                             job=job)\n"
    "        job.records_yielded += len(produced)")],

 # The plausible tidy-up: the handler only re-raises, so why does it assign?
 # Because the `finally` below reads `failure` to decide the job's status, and
 # without the assignment a job that blew up in ingestion is filed `completed`.
 "R11b a job that raised out of its persistence step is reported completed": [
   ("app/services/scrape_runner.py",
    "        failure = failure or e\n        raise",
    "        raise")],

 "R12 screening is scoped to the sightings a job wrote, so a repeat of the "
 "same search never screens what it finds": [
   ("app/services/scrape_runner.py",
    "    phones = [row[0] for row in\n"
    "              db.query(Prospect.phone)\n"
    "              .filter(Prospect.phone.in_(wanted), Prospect.status == \"pending\")\n"
    "              .all()]",
    "    from app.models.prospect import ProspectSighting as _S\n"
    "    phones = [row[0] for row in\n"
    "              db.query(Prospect.phone)\n"
    "              .join(_S, _S.prospect_id == Prospect.id)\n"
    "              .filter(_S.job_id == job.id).distinct().all()]")],

 "R12b the screening pass pays to look up numbers a human already rejected": [
   ("app/services/scrape_runner.py",
    "              .filter(Prospect.phone.in_(wanted), Prospect.status == \"pending\")\n",
    "              .filter(Prospect.phone.in_(wanted))\n")],

 "R13 a lookup landing does not rescore, so the queue ranks on line types "
 "from before anybody looked them up": [
   ("app/services/scrape_runner.py",
    "        prospect_service.rescore(\n"
    "            db, prospect,\n"
    '            line_type=outcome["results"].get(prospect.phone, "unknown"),\n'
    "            commit=False)",
    "        pass")],

 # ── A6: rejection is permanent, and permanent on the number ────────────────
 "R14 ingestion stops checking the rejection list — a rejected business "
 "resurfaces from the next source": [
   ("app/services/prospect_service.py",
    "    if is_suppressed(db, phone):\n"
    '        return _count(job, "records_suppressed", "suppressed")',
    "    if False:\n"
    '        return _count(job, "records_suppressed", "suppressed")')],

 "R14b the suppression record is not written, only the prospect's status": [
   ("app/services/prospect_service.py",
    "        if not db.query(ProspectRejection).filter(\n"
    "                ProspectRejection.phone == prospect.phone).first():\n"
    "            db.add(ProspectRejection(",
    "        if False:\n"
    "            db.add(ProspectRejection(")],

 "R15 a rejected prospect can be promoted": [
   ("app/services/prospect_service.py",
    '    if prospect.status == "rejected":\n'
    "        return REJECTED_REFUSAL",
    '    if False:\n'
    "        return REJECTED_REFUSAL")],

 # ── A7: promote runs the same guards as every other way in ─────────────────
 "R16 the blocklist is not checked at promote time": [
   ("app/services/prospect_service.py",
    "    reason = blocked.get(prospect.phone)\n"
    "    if reason is not None:",
    "    reason = None\n"
    "    if reason is not None:")],

 "R17 an opt-out and an undeliverable number get the same sentence": [
   ("app/services/prospect_service.py",
    "        if reason in blocklist_service.OPT_OUT_REASONS:\n"
    "            return OPTED_OUT_REFUSAL",
    "        if False:\n"
    "            return OPTED_OUT_REFUSAL")],

 "R18 the line-type gate is not consulted at promote time": [
   ("app/services/prospect_service.py",
    "    return lookup_service.refusal_for(line_types.get(prospect.phone))",
    "    return None")],

 "R19 promote does not link the contact back to the prospect": [
   ("app/services/prospect_service.py",
    "        prospect.promoted_contact_id = contact.id",
    "        prospect.promoted_contact_id = None")],

 "R20 promote tags the contact as inferred, storing a confidence for a "
 "decision a human made": [
   ("app/services/prospect_service.py",
    '        category_service.tag_contact(db, contact.id, category_id,\n'
    '                                     source="manual", commit=False)',
    '        category_service.tag_contact(db, contact.id, category_id,\n'
    '                                     source="inferred", confidence=0.9,\n'
    '                                     commit=False)')],

 "R21 promote does not tag the contact at all": [
   ("app/services/prospect_service.py",
    '        category_service.tag_contact(db, contact.id, category_id,\n'
    '                                     source="manual", commit=False)',
    "        pass")],

 # ── A1/A2: the rationale requirement and provenance ────────────────────────
 "R22 a record with no buyer rationale is persisted anyway": [
   ("app/services/prospect_service.py",
    "    if not rationale or not term or not source_url:",
    "    if False:")],

 "R23 the sighting constraint is bypassed, so a nightly re-run inflates "
 "corroboration and re-orders the queue": [
   ("app/services/prospect_service.py",
    "    if exists is not None:\n"
    "        return False",
    "    if False:\n"
    "        return False")],

 "R23b source_count is read after the new sighting is flushed, so a second "
 "source never counts": [
   ("app/services/prospect_service.py",
    "    already = {row[0] for row in\n"
    "               db.query(ProspectSighting.source)\n"
    "               .filter(ProspectSighting.prospect_id == prospect.id)\n"
    "               .distinct().all()} if count_source else set()\n"
    "\n"
    "    db.add(ProspectSighting(",
    "    db.add(ProspectSighting(")],

 # ── A5: scoring ────────────────────────────────────────────────────────────
 "R24 line type stops being the dominant term — a perfect landline outranks "
 "a bare mobile": [
   ("app/services/prospect_scoring.py",
    'LINE_TYPE_POINTS = {\n'
    '    "mobile": 60,\n'
    '    "voip": 30,\n'
    '    "unknown": 10,\n'
    '    "toll_free": 5,\n'
    '    "landline": 0,\n'
    '}',
    'LINE_TYPE_POINTS = {\n'
    '    "mobile": 12,\n'
    '    "voip": 8,\n'
    '    "unknown": 4,\n'
    '    "toll_free": 2,\n'
    '    "landline": 0,\n'
    '}')],

 "R25 an unconfigured category is treated as national, so a food-service "
 "search starts ranking Seattle": [
   ("app/services/prospect_scoring.py",
    "    if category_slug in configured:\n"
    "        return configured[category_slug]\n"
    "    return settings.PROSPECT_DEFAULT_RADIUS_MILES",
    "    return configured.get(category_slug)")],

 "R26 corroboration is uncapped, so the queue measures how often a search ran": [
   ("app/services/prospect_scoring.py",
    "    points += min(MAX_CORROBORATION_POINTS,\n"
    "                  extra_sources * CORROBORATION_POINTS_PER_EXTRA_SOURCE)",
    "    points += extra_sources * CORROBORATION_POINTS_PER_EXTRA_SOURCE")],

 # ── A6: what the reviewer is actually shown ────────────────────────────────
 "R27 the queue stops carrying the buyer rationale, so the reviewer answers "
 "'is this a real business?' instead": [
   ("app/services/prospect_queue.py",
    '        "buyer_rationale": prospect.buyer_rationale,',
    '        "buyer_rationale": None,')],

 "R28 the term breakdown folds sellers in with every other reason": [
   ("app/services/prospect_queue.py",
    "        if reason in WRONG_SIDE_REASONS:\n"
    '            entry["wrong_side"] += count',
    "        if False:\n"
    '            entry["wrong_side"] += count')],

 "R29 a term is flagged off one seller rejection": [
   ("app/services/prospect_queue.py",
    '        entry["flagged"] = (rejected >= minimum\n'
    '                            and entry["wrong_side_share"] >= threshold)',
    '        entry["flagged"] = entry["wrong_side"] > 0')],

 "R30 the eligible filter uses its own idea of a good number instead of the "
 "gate's": [
   ("app/services/prospect_queue.py",
    "    if promote_eligible is True:\n"
    "        filters.append(line_type_column.in_(PROMOTABLE_LINE_TYPES))",
    "    if promote_eligible is True:\n"
    '        filters.append(line_type_column.in_(("mobile", "voip", "unknown")))')],

 "R31 the queue total is the length of the page it returned": [
   ("app/services/prospect_queue.py",
    "    total = total_query.scalar() or 0",
    "    total = 0")],

 # ── What must not cross the API boundary ───────────────────────────────────
 "R32 the raw third-party payload is serialized onto every queue row": [
   ("app/services/prospect_queue.py",
    '        "promoted_contact_id": prospect.promoted_contact_id,',
    '        "promoted_contact_id": prospect.promoted_contact_id,\n'
    '        "raw_payload": prospect.raw_payload,')],

 "R33 the export carries the payload column too": [
   ("app/routers/prospects.py",
    'EXPORT_COLUMNS = ["phone", "business_name", "category", "line_type", "score",\n'
    '                  "distance_miles", "status", "search_term", "buyer_rationale",\n'
    '                  "source", "source_url", "scraped_at"]',
    'EXPORT_COLUMNS = ["phone", "business_name", "category", "line_type", "score",\n'
    '                  "distance_miles", "status", "search_term", "buyer_rationale",\n'
    '                  "source", "source_url", "scraped_at", "raw_payload"]')],

 "R34 the reject dropdown keeps its own list, which drifts from the one "
 "reject() accepts": [
   ("app/routers/prospects.py",
    "    return [{\"value\": reason, \"label\": REJECT_REASON_LABELS.get(reason, reason)}\n"
    "            for reason in REJECT_REASONS]",
    "    return [{\"value\": reason, \"label\": REJECT_REASON_LABELS[reason]}\n"
    "            for reason in (\"not_a_buyer\", \"other\")]")],

 "R35 the queue endpoints are open to an unauthenticated caller": [
   ("app/routers/prospects.py",
    "                         db: Session = Depends(get_db),\n"
    "                         user: str = Depends(require_auth)):\n"
    '    """One page of the queue. Server-side paging and sorting, always."""',
    "                         db: Session = Depends(get_db)):\n"
    '    """One page of the queue. Server-side paging and sorting, always."""')],
}

pristine = {p: (SCRATCH / p).read_text()
            for muts in MUTATIONS.values() for p, _, _ in muts}

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
