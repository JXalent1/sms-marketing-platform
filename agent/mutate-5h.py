"""Revert one session-5h fix at a time and check that the suite notices.

Run by `agent/accept-5h.sh` as check 8b. Check 8 on its own — "the new modules
are red against the pre-fix tree" — is true and nearly meaningless, for the
reason CLAUDE.md records: they fail there at *import*, because `HELD_BACK_STATUS`
and `wholesale_cost` did not exist yet. A new module always fails against a tree
that predates it.

So this reverts each fix **behaviourally**, inside the current API, one at a
time, in a scratch copy of the tree, and requires at least one test to go red for
each. Every mutation is a plausible edit rather than a wrecking ball — that is
what makes a green run mean something:

  - `R1` is the revert of the status split itself, which is what a session that
    decides "`skipped` was fine, this is churn" would write.
  - `R2` is the tidy-up somebody does when they see a row being mutated and
    reach for the `db.add()` that every other branch in that loop uses.
  - `R5` is the *cheap* release — "we already know who was held back, just send
    to them" — which is the version that texts somebody four hours after the
    message they were held back from.
  - `R10` is the backfill, written by a future reader who finds the old
    `skipped` rows and helpfully sorts them out. Decision 005 rider 2 forbids
    it, the migration says so, and this is what makes ignoring both loud.
  - `R12` is the honest-looking simplification of A2: compare the exact figure
    and drop the caller's stated one. It is not a tightening, and escalation
    item 3 says the guard may only ever be tightened.

Nothing here sends. It edits a scratch copy of the tree, never the repo, and
restores every file between mutations.

    python3 agent/mutate-5h.py <scratch-tree> <python>
"""
import pathlib, re, subprocess, sys

SCRATCH = pathlib.Path(sys.argv[1])
PY = sys.argv[2]
TARGETS = [
    "tests/test_held_back_release.py",
    "tests/test_hold_wording.py",
    "tests/test_capacity_rounding.py",
    "tests/test_campaign_guardrails.py",
    "tests/test_topup_and_zero_send.py",
]

MUTATIONS = {
 # ── A1: held_back is its own status ────────────────────────────────────────
 "R1 the builder writes `skipped` again — the status split reverted": [
   ("app/services/campaign_builder.py",
    "            status=HELD_BACK_STATUS,\n            error_message=held_back,",
    '            status="skipped",\n            error_message=held_back,')],

 "R2 the release writes a second row instead of flipping the first": [
   ("app/services/campaign_topup.py",
    "    for row in released:\n"
    "        row.status = \"pending\"\n"
    "        row.error_message = None\n"
    "        row.top_up_at = stamp",
    "    for row in released:\n"
    "        db.add(SMSMessage(\n"
    "            campaign_id=campaign.id, contact_id=row.contact_id,\n"
    "            phone=row.phone, message=row.message, status=\"pending\",\n"
    "            top_up_at=stamp))")],

 "R3 a released row is not stamped, so the rail cannot say where it came from": [
   ("app/services/campaign_topup.py",
    "        row.error_message = None\n        row.top_up_at = stamp",
    "        row.error_message = None")],

 "R4 a released row keeps the sentence explaining why it was held back": [
   ("app/services/campaign_topup.py",
    '        row.status = "pending"\n        row.error_message = None',
    '        row.status = "pending"')],

 "R5 the hold is not re-adjudicated — every held-back row is released": [
   ("app/services/campaign_release.py",
    "    sendable, _ = suppression_service.partition_recent(\n"
    "        db, [contacts[p] for p in phones if p in contacts])\n"
    "    clear = {c.phone for c in sendable}",
    "    clear = set(phones)")],

 "R6 a phone the campaign already reached is released anyway": [
   ("app/services/campaign_release.py",
    "        if row.phone in reached_otherwise or row.phone in seen:",
    "        if row.phone in seen:")],

 "R7 one phone with two held-back rows is queued twice": [
   ("app/services/campaign_release.py",
    "        if row.phone in reached_otherwise or row.phone in seen:\n"
    "            continue\n"
    "        seen.add(row.phone)",
    "        if row.phone in reached_otherwise:\n"
    "            continue")],

 "R8 the top-up ignores held-back rows entirely — the pre-5h behaviour": [
   ("app/services/campaign_topup.py",
    "    released, still_held, departed = releasable(db, campaign)",
    "    released, still_held, departed = [], [], []")],

 "R9 a released contact stays counted as held back on the campaign": [
   ("app/services/campaign_topup.py",
    "    campaign.suppressed_count = max(0, (campaign.suppressed_count or 0)\n"
    "                                    - len(released)) + len(suppressed)",
    "    campaign.suppressed_count = (campaign.suppressed_count or 0) + len(suppressed)")],

 "R10 pre-5h `skipped` rows are re-adjudicated after all — the backfill": [
   ("app/services/campaign_release.py",
    "                .filter(SMSMessage.campaign_id == campaign_id,\n"
    "                        SMSMessage.status == HELD_BACK_STATUS)",
    "                .filter(SMSMessage.campaign_id == campaign_id,\n"
    "                        SMSMessage.status.in_((HELD_BACK_STATUS, \"skipped\")))"),
   ("app/services/campaign_release.py",
    "        SMSMessage.status != HELD_BACK_STATUS).all()}",
    "        SMSMessage.status.notin_((HELD_BACK_STATUS, \"skipped\"))).all()}")],

 "R11 a released contact is described to the client as a new one": [
   ("app/services/campaign_topup.py",
    "    if released:\n"
    "        return (f\"Top-up sending to {people(released)} this campaign held back \"\n"
    "                f\"earlier, now that the hold has cleared\")",
    "    if released:\n"
    "        return f\"Top-up sending to {people(released)} added since this campaign "
    "went out\"")],

 # ── A2: the capacity guard compares exact money ────────────────────────────
 "R12 the requirement is derived from segments alone — not a tightening": [
   ("app/services/campaign_service.py",
    "        exact_needed = max(wholesale_cost(estimated_segments),\n"
    "                           Decimal(str(estimated_cost or 0)))",
    "        exact_needed = wholesale_cost(estimated_segments)")],

 "R13 the comparison rounds again": [
   ("app/services/campaign_service.py",
    "        exact_needed = max(wholesale_cost(estimated_segments),\n"
    "                           Decimal(str(estimated_cost or 0)))",
    "        exact_needed = Decimal(str(wholesale_estimate(estimated_segments)))")],

 "R14 the exact cost function rounds, so nothing downstream can be exact": [
   ("app/services/campaign_builder.py",
    "    rate = Decimal(str(settings.WHOLESALE_COST_PER_SEGMENT))\n"
    "    return max(0, segments or 0) * rate",
    "    rate = Decimal(str(settings.WHOLESALE_COST_PER_SEGMENT))\n"
    "    return (max(0, segments or 0) * rate).quantize(Decimal('0.01'))")],

 # There is no R15, and the gap is the finding. It was "the balance is compared
 # as a float again" — and no test caught it, correctly. `float(exact_required)`
 # and `Decimal(str(balance))` order identically at every magnitude this
 # application will ever see: the values are cents, floats carry fifteen
 # significant digits, and the disagreement needs a number neither a carrier
 # balance nor a campaign estimate can produce. What carried the defect was the
 # *rounding*, not the type of the comparison, and R13/R14 catch that. Keeping
 # the Decimal comparison is hygiene — it stops a future edit reintroducing a
 # float that has been through `n * rate` — and hygiene with no reachable
 # mutation is worth saying out loud rather than dressing up as a tenth green
 # tick. CLAUDE.md: write mutations you can reach.

 # ── What the fresh-context review found ────────────────────────────────────
 "R17 a capped campaign releases everything the window held back": [
   ("app/services/campaign_release.py",
    "    if campaign.batch_size and campaign.batch_size > 0:",
    "    if False:")],

 "R18 the cap is applied and then thrown away again": [
   ("app/services/campaign_builder.py",
    "        batch_size=batch_size if (batch_size and batch_size > 0) else None,",
    "        batch_size=None,")],

 "R19 a contact who left the list is reported as held back by the window": [
   ("app/services/campaign_release.py",
    "    still_held = [row for row in rows\n"
    "                  if row.phone not in clear and row.phone in contacts]\n"
    "    departed = [row for row in rows if row.phone not in contacts]",
    "    still_held = [row for row in rows if row.phone not in clear]\n"
    "    departed = []")],

 # ── Decision 006: the two sentences a hold produces ────────────────────────
 # 006 ruled the *state* right and the *wording* wrong, so these four mutations
 # are all on sentences. That is not a softer class of defect here: the campaign
 # rail is the entire UI, there is no detail screen, and a refusal the client
 # cannot act on is the failure mode 006 was written about — two campaigns that
 # sent nothing and read as a broken tool until somebody ran SQL.
 "R20 the abort reason stops saying when the hold clears": [
   ("app/services/campaign_release.py",
    '        "clears_at": hold_clears_at(db, campaign_id),',
    '        "clears_at": None,')],

 "R21 the abort reason stops naming the window": [
   ("app/services/campaign_release.py",
    '        "suppression_days": suppression_service.suppression_days(db),',
    '        "suppression_days": None,')],

 "R21b the send loop stops asking for the hold's facts at all": [
   ("app/services/campaign_service.py",
    "            window = (campaign_release.hold_facts(self.db, campaign_id)\n"
    "                      if total == 0 and suppressed else {})",
    "            window = {}")],

 # A6's rule, and the reason it is the latest: the earliest names a time at
 # which most of the audience is still held, which reads as a promise.
 "R22 the clearing time is the first hold to lift, not the last": [
   ("app/services/suppression_service.py",
    "        latest = datetime.fromisoformat(max(stamps))",
    "        latest = datetime.fromisoformat(min(stamps))")],

 "R23 the capped refusal stops naming the cap and the count": [
   ("app/services/campaign_topup.py",
    "            refusal = capped_campaign_hold(campaign.batch_size, len(held),\n"
    "                                           hold_clears_at(db, campaign.id))",
    '            refusal = "This campaign was capped and cannot be topped up."')],

 # ── The two the review found, both about a hold that is no longer holding ──
 # Neither had a mutation until it had a defect: the sentence was correct at the
 # instant the draft was built and wrong at the instant it was read, which is a
 # gap no amount of asserting the happy path closes.
 "R24 a clearing time in the past is still printed as the future": [
   ("app/services/campaign_outcome.py",
    "    if _still_ahead(clears_at):",
    "    if clears_at:")],

 "R25 a window switched off still tells him to wait for the hold": [
   ("app/services/campaign_outcome.py",
    "    elif clears_at or days == 0:",
    "    elif clears_at:")],

 # The guard that keeps a failed lookup from stranding the campaign in
 # `running`. Mutating it to catch nothing is what a future reader writes when
 # they see a broad `except Exception` and reach for the linter's advice.
 "R27 a failed hold lookup strands the campaign instead of losing a clause": [
   ("app/services/campaign_release.py",
    "    try:\n"
    "        return {\n"
    '            "suppression_days": suppression_service.suppression_days(db),\n'
    '            "clears_at": hold_clears_at(db, campaign_id),\n'
    "        }\n"
    "    except Exception:",
    "    if True:\n"
    "        return {\n"
    '            "suppression_days": suppression_service.suppression_days(db),\n'
    '            "clears_at": hold_clears_at(db, campaign_id),\n'
    "        }\n"
    "    if False:")],

 "R26 the capped refusal drops the clearing time and the remedy stops working": [
   ("app/services/campaign_topup.py",
    "                                           hold_clears_at(db, campaign.id))",
    "                                           None)")],

 # ── The reporting consequence of the status split ──────────────────────────
 "R16 held-back rows are counted as top-up recipients again": [
   ("app/services/campaign_topup.py",
    "                    SMSMessage.top_up_at.isnot(None),\n"
    "                    SMSMessage.status != HELD_BACK_STATUS)",
    "                    SMSMessage.top_up_at.isnot(None))")],
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
    print("\nreverted fix(es) that no test noticed:")
    for name in survivors:
        print(f"   -> {name}")
sys.exit(1 if (survivors or unapplied) else 0)
