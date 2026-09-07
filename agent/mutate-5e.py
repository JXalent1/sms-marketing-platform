"""Revert one session-5e fix at a time and check that the suite notices.

Run by `agent/accept-5e.sh` as check 8b. Check 8 on its own is weak, for the
reason `mutate-5g.py` gives at length and CLAUDE.md now records: the new test
modules fail against the pre-fix tree at *import*, because the symbols they test
did not exist there. "The module is new" is not "these tests would catch the bug
coming back".

So this reverts each fix **behaviourally** — inside the current API, one at a
time — and requires at least one test to go red for each. A test that stays green
through the revert of the thing it names is decoration.

Every mutation below is a plausible edit, not a syntactic wrecking ball. That is
the discipline that makes the result mean something: `M2` is what a future
session writes when it decides the list-audience escape "should obviously also
apply to an existing list", `M7` is the tidy-up somebody does when they notice
two branches setting the same status, and `M11` is the one-line "simplification"
that turns the top-up guard from a number into a row id.

Nothing here sends. It edits a scratch copy of the tree, never the repo, and
restores every file between mutations.

    python3 agent/mutate-5e.py <scratch-tree> <python>
"""
import pathlib, re, subprocess, sys

SCRATCH = pathlib.Path(sys.argv[1])
PY = sys.argv[2]
REPO = pathlib.Path(__file__).resolve().parent.parent
TARGETS = [
    "tests/test_campaign_first_flow.py",
    "tests/test_topup_and_zero_send.py",
    "tests/test_suppression_window.py",
]

# M27 mutates a template, and the test that catches it reads the template's
# source rather than running a browser. That is deliberate: the guard it protects
# exists in one of two functions that need it, which is the shape of defect this
# project keeps finding, and asserting the property in the source is the cheapest
# honest check available without a headless browser in the suite.

MUTATIONS = {
 # ── A1: the upload is the campaign's audience ──────────────────────────────
 "M1 the list is not named for the campaign": [
   ("app/services/campaign_builder.py",
    "result = import_service.commit(db, content, category_id, list_name=name)",
    "result = import_service.commit(db, content, category_id)")],

 # Targeted at the *method's* default, not the builder function's. Every caller
 # of the function passes the flag explicitly, so mutating its default changes
 # nothing and would report a green tick for an untested rule — which is what
 # the first run of this harness reported, and why it is worth writing the
 # mutation you can actually reach.
 "M2 the list-audience escape leaks onto the ordinary create path": [
   ("app/services/campaign_service.py",
    "                        scheduled_at: Optional[str] = None,\n"
    "                        list_audience: bool = False) -> Campaign:",
    "                        scheduled_at: Optional[str] = None,\n"
    "                        list_audience: bool = True) -> Campaign:")],

 "M2b the category rule accepts a missing category from anywhere": [
   ("app/services/campaign_builder.py",
    "        if cross_category_override or list_audience:\n            return None",
    "        return None")],

 "M3 a failed campaign leaves its import behind": [
   ("app/services/campaign_builder.py",
    "    except CampaignError as e:\n        import_service.undo(db, list_id)",
    "    except CampaignError as e:\n        pass  # mutation: no rollback")],

 # ── A2: the category tag is optional, and untagged is still undoable ───────
 "M4 an untagged upload is recorded as a cross-category override": [
   ("app/services/campaign_builder.py",
    "        cross_category_override=1 if cross_category_override else 0,",
    "        cross_category_override=1 if (category is None) else 0,")],

 "M5 undo goes back to keying on category_id": [
   ("app/services/import_service.py",
    "    if batch.source != CSVContactSource.name:",
    "    if batch.category_id is None:")],

 # ── A3: a hand-added contact gets an import's guards ───────────────────────
 "M6 the manual-add form skips the blocklist": [
   ("app/routers/contacts.py",
    "    if blocklist_service.is_blocked(db, phone):\n"
    "        raise HTTPException(status_code=409, detail=BLOCKED_CONTACT_ERROR)",
    "    pass  # mutation: no blocklist check")],

 # The obvious mutation here — pass `payload.phone` instead of the normalised
 # form — is inert: `upsert_contact()` normalises internally, so the row is
 # identical either way. Writing it that way reported a green tick for a
 # redundancy. What is *not* redundant is the ordering: the blocklist is checked
 # before anything is written, so a refused add leaves no contact row behind. A
 # row the client can see on the Contacts screen that no campaign will ever text
 # reads as a bug in the send path rather than as an opt-out being honoured.
 "M7 the blocklist is checked after the contact is written": [
   ("app/routers/contacts.py",
    "    if blocklist_service.is_blocked(db, phone):\n"
    "        raise HTTPException(status_code=409, detail=BLOCKED_CONTACT_ERROR)",
    "    pass  # mutation: moved below the write"),
   ("app/routers/contacts.py",
    "    if not contact:\n"
    '        raise HTTPException(status_code=400, detail="Invalid phone number")\n\n'
    "    if payload.list_name:",
    "    if not contact:\n"
    '        raise HTTPException(status_code=400, detail="Invalid phone number")\n'
    "    if blocklist_service.is_blocked(db, phone):\n"
    "        raise HTTPException(status_code=409, detail=BLOCKED_CONTACT_ERROR)\n\n"
    "    if payload.list_name:")],

 # ── A4: the top-up ─────────────────────────────────────────────────────────
 # The already-reached filter is defence in depth behind the `added_at` window
 # since the review — a number can be put on a list twice, and the second
 # membership row is legitimately "added since". Removing it is still a real
 # regression and still has to be noticed.
 "M8 a top-up re-sends to everyone the campaign already reached": [
   ("app/services/campaign_topup.py",
    "    fresh = [c for c in _added_since(db, list_id, campaign.created_at)\n"
    "             if c.phone not in reached]",
    "    fresh = list(_added_since(db, list_id, campaign.created_at))")],

 "M9 the top-up guard is keyed on contact id, not the number": [
   ("app/services/campaign_topup.py",
    "    return {phone for (phone,) in\n"
    "            db.query(SMSMessage.phone).filter(SMSMessage.campaign_id == campaign_id).all()}",
    "    return {cid for (cid,) in\n"
    "            db.query(SMSMessage.contact_id).filter("
    "SMSMessage.campaign_id == campaign_id).all()}")],

 "M10 a top-up skips the pre-flight refusals": [
   ("app/services/campaign_topup.py",
    "    ok, detail = await service.preflight(campaign, segments=segments,\n"
    "                                         cost=wholesale_estimate(segments))",
    "    ok, detail = True, 'mutation: pre-flight skipped'")],

 "M11 an aborted campaign can be topped up": [
   ("app/services/campaign_topup.py",
    '    if campaign.status != "completed":',
    '    if campaign.status in ("draft", "running"):')],

 "M12 a top-up does not fold into the campaign's totals": [
   ("app/services/campaign_topup.py",
    "    campaign.total_recipients = (campaign.total_recipients or 0) + len(sendable)",
    "    campaign.total_recipients = (campaign.total_recipients or 0)")],

 "M13 the top-up stamp is dropped, so a report cannot say when": [
   ("app/services/campaign_topup.py",
    "            message=body, status=\"pending\", top_up_at=stamp,",
    "            message=body, status=\"pending\",")],

 # ── A5: the window is a stored setting ─────────────────────────────────────
 "M14 the window goes back to reading .env only": [
   ("app/services/suppression_service.py",
    "    raw = get_setting(db, SUPPRESSION_DAYS_KEY)",
    "    raw = None  # mutation: ignore the stored value")],

 "M15 the write path stops validating the range": [
   ("app/services/suppression_service.py",
    "    if not 0 <= value <= SUPPRESSION_DAYS_MAX:\n"
    "        raise ValueError(",
    "    if False:\n        raise ValueError(")],

 # ── A6: suppression is visible before the campaign is queued ───────────────
 "M16 the clearing time is never computed": [
   ("app/routers/campaigns.py",
    '        "suppression_clears_at": suppression_service.suppression_clears_at(\n'
    "            suppressed, days),",
    '        "suppression_clears_at": None,')],

 "M17 the clearing time is the earliest rather than the latest": [
   ("app/services/suppression_service.py",
    "        latest = datetime.fromisoformat(max(stamps))",
    "        latest = datetime.fromisoformat(min(stamps))")],

 "M18 the checklist row reads the window a second way": [
   ("app/services/preflight_service.py",
    "        check_recent_overlap(days, suppressed_count, sendable_count,\n"
    "                             suppression_clears_at),",
    "        check_recent_overlap(settings.RECENT_CONTACT_SUPPRESSION_DAYS,\n"
    "                             suppressed_count, sendable_count,\n"
    "                             suppression_clears_at),")],

 # ── A7: a run that reached nobody ──────────────────────────────────────────
 "M19 a campaign that reached nobody reports completed again": [
   ("app/services/campaign_service.py",
    '        elif run["sent"] == 0:',
    "        elif False:")],

 "M20 the abort reason is dropped, leaving a bare badge": [
   ("app/services/campaign_service.py",
    '            campaign.status = "aborted"\n            campaign.abort_reason = reason',
    '            campaign.status = "aborted"\n            campaign.abort_reason = None')],

 "M21 a top-up that reached nobody relabels the whole campaign": [
   ("app/services/campaign_service.py",
    "        if reason and not top_up:",
    "        if reason:")],

 "M22 every zero-send reason collapses to one generic sentence": [
   ("app/services/campaign_outcome.py",
    "    if queued == 0:\n        if suppressed:",
    "    if True:\n        if False:")],

 # ── What the fresh-context review found ───────────────────────────────────
 # Six defects, five fixed here. Each gets a mutation, because a fix a reviewer
 # had to find by hand is exactly the fix a future session undoes in good faith.

 "M23 the top-up candidate set goes back to subtracting message rows": [
   ("app/services/campaign_topup.py",
    "    fresh = [c for c in _added_since(db, list_id, campaign.created_at)\n"
    "             if c.phone not in reached]",
    "    from app.services import contact_service\n"
    "    fresh = [c for c in contact_service.resolve_audience(db, campaign.audience)\n"
    "             if c.phone not in reached]")],

 "M24 a top-up accepts an audience that is not its own list": [
   ("app/services/campaign_topup.py",
    "    if campaign_list_id(campaign) is None:\n"
    "        return {**empty, \"refusal\": NOT_A_LIST_AUDIENCE}",
    "    pass  # mutation: any audience will do"),
   ("app/services/campaign_topup.py",
    '    if "&" in selector or "," in selector or not selector.startswith("list:"):\n'
    "        return None",
    '    if not selector.startswith("list:"):\n        return 1')],

 "M25 add_to_list falls back to the server default's UTC clock": [
   ("app/services/contact_service.py",
    "    db.add(ContactListMember(list_id=list_id, contact_id=contact_id,\n"
    "                             added_at=datetime.now().isoformat()))",
    "    db.add(ContactListMember(list_id=list_id, contact_id=contact_id))")],

 "M26 the manual-add form accepts a category that does not exist": [
   ("app/routers/contacts.py",
    "    if payload.category_id is not None and db.get(Category, payload.category_id) is None:\n"
    "        raise HTTPException(\n"
    "            status_code=404,\n"
    '            detail=f"No category with id {payload.category_id}.")',
    "    pass  # mutation: the category is not resolved")],

 "M27 the composer runs pre-flight against the wrong audience in upload mode": [
   ("app/templates/_composer-script.html",
    "    if (composerMode === 'upload') {\n"
    "        list.innerHTML = '<li class=\"px-5 py-4 text-[13px] text-ink-3\">'",
    "    if (false) {\n"
    "        list.innerHTML = '<li class=\"px-5 py-4 text-[13px] text-ink-3\">'")],
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
