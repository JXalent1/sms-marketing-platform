"""Revert one session-5i guarantee at a time and check that the suite notices.

Run by `agent/accept-5i.sh` as check 10. "The new test modules are red against
the pre-5i tree" is true and nearly meaningless — they fail there at *import*,
because `ALL_BIDDERS_LABEL`, `list_cards` and `parse_created_at` did not exist.
This reverts each guarantee **behaviourally**, inside the current API, one at a
time, in a scratch copy of the tree, and requires at least one test to go red for
each.

Every mutation is a plausible edit rather than a wrecking ball:

  - `A1a` is the pre-5i clock, both halves. See its own note: restoring the
    server default *alone* changes nothing, and that is the point.
  - `A1b` is the ordering somebody writes when they see a string column and
    reach for `sorted(..., reverse=True)`. It has to fail on the live data's own
    two spellings, not on a fixture nobody would write.
  - `A1c` is the migration classifying by the absence of a "T" rather than by
    the shape of a server-default value, which converts rows that are already
    local.
  - `A2a`/`A2b`/`A2c` are the three ways "hidden, not removed" comes apart: the
    picker offering categories again, the resolver losing them, and the pinned
    wording acquiring a second spelling.
  - `A4a`/`A4b` are the category rule in both directions — refusing a campaign
    the composer can now create, and accepting one it never could. `A4c` is what
    else arrives on the path 5i widened: a malformed `list:` selector used to be
    stopped by the category rule and now reaches the resolver, where an unmapped
    ValueError is a 500 saying nothing.
  - `A6a` is `0` for a list nobody has texted, which is the defect the em-dash
    rule exists for and which kept a whole niche from ever being picked.
  - `A7a` takes the prospects router out of the sweep's subtraction. It must
    make the sweep fail, which is what proves the sweep looks at that surface
    and that the exception is doing real work rather than decorating a list.

## This harness verifies its own preconditions first

P1's first mutation run reported 38 caught, 0 survived and was worthless: a run
killed on a timeout had left the scratch tree already mutated, so `pristine` was
captured from a dirty tree and every verdict sat on top of a leftover edit. So
before a single patch is applied, every file this script touches is compared byte
for byte against the repo's copy, and the run stops if any differs. It prints
`SCRATCH VERIFIED PRISTINE` when they match. Copied from `agent/mutate-P1b.py`,
which is the one earlier harness that implements this properly.

Read the results the same way: check that the tests failing for a mutation are
the tests that **name** it. A harness is code and it fails the same ways.

Nothing here sends. It edits a scratch copy of the tree, never the repo, and
restores every file between mutations.

    python3 agent/mutate-5i.py <scratch-tree> <python>
"""
import pathlib, re, subprocess, sys

SCRATCH = pathlib.Path(sys.argv[1])
PY = sys.argv[2]
REPO = pathlib.Path(__file__).resolve().parent.parent
TARGETS = [
    "tests/test_list_picker.py",
    "tests/test_audience_surfaces.py",
    "tests/test_dashboard.py",
    "tests/test_campaign_guardrails.py",
    "tests/test_campaign_first_flow.py",
    "tests/test_categories.py",
]

MUTATIONS = {
 # ── A1: one writer, one clock, one spelling ────────────────────────────────
 #
 # Both halves in one mutation, deliberately. Putting `server_default` back on
 # its own changes NOTHING while `get_or_create_list()` passes a value and the
 # model carries a Python-side default — a server default only fires on an
 # insert that omits the column. A one-line patch here would be a mutation on a
 # path nothing executes, reported CAUGHT or NOT CAUGHT for reasons unrelated to
 # the defect. This is the pre-5i tree, restored.
 "A1a the pre-5i clock is back: the server default writes created_at in UTC "
 "with a space, and the service stops writing it at all": [
   ("app/models/contact_list.py",
    "    created_at = Column(String(50), nullable=True, default=now_iso)",
    "    created_at = Column(String(50), server_default=func.now())"),
   ("app/services/contact_service.py",
    "    row = ContactList(name=name, description=description, source=source,\n"
    "                      created_at=datetime.now().isoformat())",
    "    row = ContactList(name=name, description=description, source=source)")],

 "A1b the picker orders lists by the raw string, which puts a same-day "
 "server-default row below every isoformat one": [
   ("app/services/contact_service.py",
    "        rows, key=lambda r: (parse_created_at(r[2]), r[0]), reverse=True)]",
    '        rows, key=lambda r: (str(r[2] or ""), r[0]), reverse=True)]')],

 "A1c the migration classifies by the absence of a T rather than by the "
 "server default's shape, so it shifts rows that are already local": [
   ("alembic/versions/f4a1c7d90e52_normalise_contact_list_created_at.py",
    '    return bool(SERVER_DEFAULT_SHAPE.fullmatch((value or "").strip()))',
    '    return "T" not in (value or "").strip() and bool((value or "").strip())')],

 "A1d an unparseable created_at raises instead of sorting last, so one bad "
 "row is a 500 on a screen that was only trying to sort": [
   ("app/models/contact_list.py",
    "    try:\n"
    "        return datetime.fromisoformat(text)\n"
    "    except (TypeError, ValueError):\n"
    "        return _SORTS_LAST",
    "    return datetime.fromisoformat(text)")],

 # ── A2: the picker, and what it must not stop resolving ────────────────────
 "A2a list_summaries() offers category entries again": [
   ("app/services/contact_service.py",
    '    audiences += [{\n'
    '        "selector": f"list:{list_id}",',
    '    audiences += [{\n'
    '        "selector": f"category:{name}",\n'
    '        "label": name,\n'
    '        "count": count,\n'
    '        "kind": "category",\n'
    '        "last_sent_at": None,\n'
    '        "days_since_sent": None,\n'
    '    } for list_id, name, _, count in rows]\n'
    '    audiences += [{\n'
    '        "selector": f"list:{list_id}",')],

 "A2b the category branch is gone from _term_ids_query(), so every campaign "
 "in history resolves to nobody": [
   ("app/services/contact_service.py",
    '    if term.startswith("category:"):\n'
    '        slugs = [s.strip() for s in term.split(":", 1)[1].split(",") if s.strip()]\n'
    '        if not slugs:\n'
    '            raise ValueError(f"Audience selector {term!r} names no category")',
    '    if False:\n'
    '        slugs = [s.strip() for s in term.split(":", 1)[1].split(",") if s.strip()]\n'
    '        if not slugs:\n'
    '            raise ValueError(f"Audience selector {term!r} names no category")')],

 "A2c the pinned wording is hard-coded in a second place, so a rename moves "
 "the dropdown and leaves the summary panel behind": [
   ("app/services/contact_service.py",
    'ALL_BIDDERS_LABEL = "⭐ ALL BIDDERS — MAIN LIST"',
    'ALL_BIDDERS_LABEL = "⭐ EVERY BIDDER"'),
   ("app/services/contact_service.py",
    "        return ALL_BIDDERS_LABEL\n"
    '    if term.startswith("list:"):',
    '        return "⭐ ALL BIDDERS — MAIN LIST"\n'
    '    if term.startswith("list:"):')],

 "A2d the freshness query reads the billable set, so a commercial change "
 "would silently rewrite the dates he schedules against": [
   ("app/services/contact_service.py",
    "from app.models.sms_message import SENT_STATUSES, SMSMessage",
    "from app.models.sms_message import BILLABLE_STATUSES, SENT_STATUSES, SMSMessage"),
   ("app/services/contact_service.py",
    "    rows = (db.query(ContactListMember.list_id, func.max(SMSMessage.sent_at))\n"
    "            .join(SMSMessage, SMSMessage.contact_id == ContactListMember.contact_id)\n"
    "            .filter(SMSMessage.status.in_(SENT_STATUSES),",
    "    rows = (db.query(ContactListMember.list_id, func.max(SMSMessage.sent_at))\n"
    "            .join(SMSMessage, SMSMessage.contact_id == ContactListMember.contact_id)\n"
    "            .filter(SMSMessage.status.in_(BILLABLE_STATUSES),")],

 "A2e the picker counts memberships rather than active contacts, so it "
 "promises people no campaign could reach": [
   ("app/services/contact_service.py",
    "            .outerjoin(Contact, (Contact.id == ContactListMember.contact_id)\n"
    "                                & (Contact.is_active == 1))",
    "            .outerjoin(Contact, Contact.id == ContactListMember.contact_id)")],

 # ── A4: the category rule, in both directions ──────────────────────────────
 "A4a resolve_category() refuses a list audience again — every campaign the "
 "composer can now build is a 400": [
   ("app/services/campaign_builder.py",
    "        if (cross_category_override or list_audience\n"
    "                or audience_names_its_own_target(audience)):",
    "        if cross_category_override or list_audience:")],

 "A4b resolve_category() accepts a hand-written category selector with no "
 "category and no override": [
   ("app/services/campaign_builder.py",
    '    return all(t == "all" or t.startswith("list:") for t in terms)',
    "    return True")],

 "A4c a malformed list selector reaches the resolver unmapped, so it is a 500 "
 "reading 'Could not create campaign' with the reason left in the log": [
   ("app/services/campaign_builder.py",
    "    try:\n"
    "        recipients = contact_service.resolve_audience(db, audience)\n"
    "    except ValueError as e:\n"
    "        raise CampaignError(str(e)) from e",
    "    recipients = contact_service.resolve_audience(db, audience)")],

 # ── A6: the dashboard ──────────────────────────────────────────────────────
 "A6a a list nobody has texted renders 0 instead of an em dash — 'texted "
 "today', which is the opposite of the truth": [
   ("app/services/dashboard_service.py",
    '        "days_label": "—" if days is None else str(days),',
    '        "days_label": str(days or 0),')],

 "A6b the pinned card is no longer first, so the main list is buried among "
 "the uploads": [
   ("app/services/dashboard_service.py",
    "    return [_card(e, threshold) for e in pinned + lists]",
    "    return [_card(e, threshold) for e in lists + pinned]")],

 "A6c the hero matches a campaign to a card by name rather than by its own "
 "audience selector, so it answers about the wrong list": [
   ("app/services/dashboard_service.py",
    '    return next((c for c in cards if c["selector"] == selector), None)',
    "    return cards[0] if cards else None")],

 # ── A7: the sweep's own exception ──────────────────────────────────────────
 "A7a the prospects router comes out of the sweep's subtraction — the sweep "
 "must then fail, which is what proves it looks at that surface": [
   ("tests/test_audience_surfaces.py",
    '    "app.routers.prospects",\n',
    "")],
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
    print("\nreverted guarantee(s) that no test noticed:")
    for name in survivors:
        print(f"   -> {name}")
sys.exit(1 if (survivors or unapplied) else 0)
