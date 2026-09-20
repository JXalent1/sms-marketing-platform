"""Revert one session-5n guard at a time and check that the suite notices.

Run by `agent/accept-5n.sh` as check 10. Each mutation is a plausible edit
inside the current API, applied to a scratch copy of the tree, and at least one
test must go red for each — the behavioural proof, not "the new modules fail to
import against the old tree".

5n fixed two things the client's operator hit, and the two halves are tested
very differently.

**A1 is JavaScript.** The composer's message row read `Characters 0 · Encoding —
· Segments/msg 0` under a full message for the whole upload flow, because
`refreshPreview()` returned outright in upload mode. The `U*` mutations edit the
templates and the preview endpoint, and are caught by
`tests/test_composer_panel.py`, which runs the real partials in node against
replies from the real endpoints — the only thing in this repo that can see them.

  - `U1` restores the blanket early return. The message row goes dark again.
  - `U2` paints an unknown as `0` — "0 recipients" above a list he is about to
    upload, the claim the em dash exists to avoid.
  - `U2b` is the same claim one layer down: the endpoint resolves "no audience"
    into zeros rather than saying it does not know.
  - `U3` skips the Unicode warning in upload mode — the emoji tripling the bill
    goes unmentioned on the primary flow.
  - `U4` measures the message in the browser: `Math.ceil(length / 160)`. On the
    fixture's emoji message that prints 1 where `count_sms_segments()` says 2.
    Criterion 4 is that no second segment calculation exists.
  - `U5` has the upload tab ask about the dropdown's audience — the previous
    tab's figures under a composer whose audience is a file.
  - `U6` stops the tab switch re-asking about the message, so a reply retired
    by the switch leaves the counter describing the previous keystroke.

**A2 is a race.** The `C*` mutations edit `campaign_dispatch.py` and the rail,
and are caught by `tests/test_cancel_scheduled.py`, whose two race tests cancel
between selection and dispatch — one surgically, one on the event loop while an
earlier campaign is mid-send.

  - `C1` drops the post-selection re-check. Selected, cancelled, sent anyway —
    this is the defect, measured on the pre-fix tree.
  - `C2` lets cancel apply to a `running` campaign: the schedule is cleared
    under a blast that is going out and the client is told it was cancelled.
  - `C3` leaves `scheduled_at` set after cancelling, so the next tick sends it.
  - `C4` reports every selected campaign as dispatched, cancelled or not.
  - `C5` draws Cancel on every draft, including the ones sent by hand.
  - `C6` stops refusing a draft that was never scheduled, so the generic
    "changed while you were cancelling it" sentence stands in for the real one.
  - `C8` has the re-check compare the wall clock again, so a campaign
    selected before the clocks fall back is skipped as "cancelled".
  - `C7` makes the flip to `running` unconditional again. The 5n review found
    the gap between `still_scheduled()` and that flip open in three of four
    provider/handler arrangements — a cancel lands during the campaign's own
    pre-flight, is told it succeeded, and the blast goes out. Caught by the
    tests that put a cancel in that exact gap, both from the loop and from a
    thread, and by the double-click test — where the second run does not
    re-send (nothing is pending) but relabels a completed campaign `aborted`.

## This harness verifies its own preconditions first

Before a single patch is applied, every file this script touches is compared
byte for byte against the repo's copy, and every anchor is resolved. It prints
`SCRATCH VERIFIED PRISTINE` and `ANCHORS VERIFIED` or exits 2. Read the results
the same way: check that the tests failing for a mutation are the tests that
**name** it.

Nothing here sends and nothing here reaches a paid API — the scratch tree
inherits `conftest.py`, which forces the console provider and blanks every
credential for the whole suite. It edits a scratch copy, never the repo, and
restores every file between mutations.

    python3 agent/mutate-5n.py <scratch-tree> <python>
"""
import pathlib, re, subprocess, sys

SCRATCH = pathlib.Path(sys.argv[1])
PY = sys.argv[2]
REPO = pathlib.Path(__file__).resolve().parent.parent
TARGETS = [
    "tests/test_composer_panel.py",
    "tests/test_cancel_scheduled.py",
    "tests/test_campaign_guardrails.py",
    "tests/test_timezone.py",
]

SCRIPT = "app/templates/_composer-script.html"
SUMMARY = "app/templates/_composer-summary.html"
UPLOAD = "app/templates/_composer-upload.html"
PREVIEW = "app/routers/campaign_preview.py"
DISPATCH = "app/services/campaign_dispatch.py"
CLAIM = "app/services/campaign_claim.py"

MUTATIONS = {
 # ── A1: the message's own figures under the upload tab ────────────────────
 "U1 the blanket early return comes back — the message row reads Characters 0 "
 "· Encoding — · Segments/msg 0 under a full message, for the whole upload flow": [
   (SCRIPT, "    const audience = composerMode === 'upload' ? null : el('audience').value;\n",
            "    if (composerMode === 'upload') { return; }\n"
            "    const audience = el('audience').value;\n")],

 "U2 an unknown is painted as 0 — \"0 recipients\" above a list he is about to "
 "upload": [
   (SUMMARY, "        : (value === null || value === undefined) ? '—'\n",
             "        : (value === null || value === undefined) ? '0'\n")],

 "U2b the endpoint resolves \"no audience\" into zeros instead of saying it does "
 "not know": [
   (PREVIEW, "    if not payload.audience:\n"
             "        return {**_template_half(db, payload, breakdown, sample=None),\n"
             "                **UNKNOWN_AUDIENCE,\n"
             "                \"price_per_segment\": settings.BILLING_PRICE_PER_SEGMENT}\n",
             "")],

 "U3 the Unicode warning is skipped in upload mode — the emoji that triples the "
 "bill goes unmentioned on the primary flow": [
   (SCRIPT, "    if (data.encoding !== 'UCS-2') { box.classList.add('hidden'); return; }\n\n"
            "    const chars = data.forced_unicode_by.join(' ');",
            "    if (data.encoding !== 'UCS-2' || composerMode === 'upload') { "
            "box.classList.add('hidden'); return; }\n\n"
            "    const chars = data.forced_unicode_by.join(' ');")],

 "U4 the browser measures the message itself — length over 160, which is 1 "
 "where count_sms_segments() says 2": [
   (SCRIPT, "    el('pvSegments').textContent = data.segments;",
            "    el('pvSegments').textContent = Math.ceil(template.length / 160) || 0;")],

 "U5 the upload tab asks about the dropdown's audience — the previous tab's "
 "figures under a composer whose audience is a file": [
   (SCRIPT, "    const audience = composerMode === 'upload' ? null : el('audience').value;\n",
            "    const audience = el('audience').value;\n")],

 "U6 the tab switch does not ask again, so a reply the switch retired leaves the "
 "counter describing the previous keystroke": [
   (UPLOAD, "    if (mode === 'upload') { resetSummary(); }\n    schedulePreview();\n",
            "    if (mode === 'upload') { resetSummary(); } else { schedulePreview(); }\n")],

 # ── A2: cancelling, and the race ───────────────────────────────────────────
 "C1 the post-selection re-check goes — selected, then cancelled, then sent "
 "anyway. This is the defect": [
   (DISPATCH, "            if not still_scheduled(db, campaign_id):\n",
              "            if False:\n")],

 "C2 cancel applies to a running campaign — the schedule is cleared under a "
 "blast that is going out, and he is told it was cancelled": [
   (DISPATCH, '    if campaign.status != "draft":\n        wording = CANCEL_STATE_ERRORS.get(',
              '    if campaign.status not in ("draft", "running"):\n        wording = CANCEL_STATE_ERRORS.get('),
   (DISPATCH, '                       Campaign.status == "draft",\n'
              "                       Campaign.scheduled_at.isnot(None))\n"
              '               .update({"scheduled_at": None}, synchronize_session=False))',
              '                       Campaign.status.in_(("draft", "running")),\n'
              "                       Campaign.scheduled_at.isnot(None))\n"
              '               .update({"scheduled_at": None}, synchronize_session=False))')],

 "C3 scheduled_at is left set after cancelling, so the next tick sends it": [
   (DISPATCH, '               .update({"scheduled_at": None}, synchronize_session=False))',
              '               .update({"abort_reason": None}, synchronize_session=False))')],

 "C4 every selected campaign is reported as dispatched, cancelled or not": [
   (DISPATCH, "        return dispatched\n",
              "        return campaign_ids\n")],

 "C5 the rail draws Cancel on every draft, including the ones he sends by hand": [
   (SCRIPT, "            ${c.status === 'draft' && c.scheduled_at ? `\n"
            "                <button type=\"button\" onclick=\"cancelCampaign(${c.id})\"",
            "            ${c.status === 'draft' ? `\n"
            "                <button type=\"button\" onclick=\"cancelCampaign(${c.id})\"")],

 "C6 a draft that was never scheduled is not refused by name — the generic "
 "sentence stands in for the real one": [
   (DISPATCH, "    if campaign.scheduled_at is None:\n        return NOT_SCHEDULED\n    return None\n",
              "    return None\n")],

 "C8 the re-check compares the wall clock again — a campaign selected before "
 "the clocks fall back is skipped and logged as cancelled": [
   (DISPATCH, '    return status == "draft" and scheduled_at is not None\n',
              '    return (status == "draft" and scheduled_at is not None\n'
              '            and scheduled_at <= clock.now_iso())\n')],

 "C7 the flip to running is unconditional again — a cancel that lands during "
 "the campaign's own pre-flight is told it succeeded, and the blast goes out": [
   (CLAIM, "    loaded_schedule = campaign.scheduled_at\n"
           "    predicate = (Campaign.scheduled_at.is_(None) if loaded_schedule is None\n"
           "                 else Campaign.scheduled_at == loaded_schedule)\n"
           "    taken = (db.query(Campaign)\n"
           "             .filter(Campaign.id == campaign.id,\n"
           "                     Campaign.status == \"draft\",\n"
           "                     predicate)\n"
           "             .update({\"status\": \"running\",\n"
           "                      \"started_at\": datetime.now().isoformat()},\n"
           "                     synchronize_session=False))\n"
           "    db.commit()\n"
           "    db.refresh(campaign)\n"
           "    if taken != 1:\n",
           "    campaign.status = \"running\"\n"
           "    campaign.started_at = datetime.now().isoformat()\n"
           "    db.commit()\n"
           "    taken = 1\n"
           "    if taken != 1:\n")],
}

# ── The precondition. P1's first run was worthless without it ──────────────
#
# Every source file the suite can read is compared, not only the files this
# harness patches (the 5n review): a leftover edit in a *test* file changes
# verdicts just as surely as one in the code, and the earlier harnesses could
# not see it.
files = sorted({path for muts in MUTATIONS.values() for path, _, _ in muts})
SOURCE = sorted(str(f.relative_to(REPO)) for top in ("app", "tests", "alembic")
                for f in (REPO / top).rglob("*")
                if f.suffix in (".py", ".html", ".mjs", ".json")
                and "__pycache__" not in f.parts and "node_modules" not in f.parts)
dirty = [p for p in SOURCE
         if not (SCRATCH / p).exists()
         or (SCRATCH / p).read_bytes() != (REPO / p).read_bytes()]
if dirty:
    print("SCRATCH TREE IS NOT PRISTINE — every verdict below would sit on top "
          "of a leftover edit:")
    for path in dirty[:20]:
        print(f"   -> {path} differs from the repo")
    sys.exit(2)
print(f"SCRATCH VERIFIED PRISTINE ({len(SOURCE)} source files under app/, tests/ and "
      f"alembic/ byte-identical to the repo)")

# The second precondition (B1): every anchor resolves before the first patch.
unresolved = [(name.split()[0], path, old.splitlines()[0][:60])
              for name, patches in MUTATIONS.items()
              for path, old, _ in patches
              if old not in (SCRATCH / path).read_text()]
if unresolved:
    print("MUTATION ANCHORS DO NOT RESOLVE — the code moved and this harness did "
          "not move with it. Fix the harness, not the code:")
    for entry in unresolved:
        print(f"   -> {entry[0]} in {entry[1]}: {entry[2]!r}")
    sys.exit(2)
patch_count = sum(len(patches) for patches in MUTATIONS.values())
print(f"ANCHORS VERIFIED ({patch_count} patches across {len(MUTATIONS)} mutations "
      f"all resolve)")

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
        env={"PATH": f"{pathlib.Path(PY).parent}:/usr/bin:/bin:/usr/local/bin",
             "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
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
    print("\nmutations that could not be applied (fix the harness, not the code):")
    for name in unapplied:
        print(f"   -> {name}")
if survivors:
    print("\nreverted guard(s) that no test noticed:")
    for name in survivors:
        print(f"   -> {name}")
sys.exit(1 if (survivors or unapplied) else 0)
