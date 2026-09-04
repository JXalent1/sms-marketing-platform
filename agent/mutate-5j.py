"""Revert one session-5j guarantee at a time and check that the suite notices.

Run by `agent/accept-5j.sh` as check 10. "The new test modules are red against
the pre-5j tree" is true and nearly meaningless — they fail there at *import*,
because `list_admin` and `tests/_query_plan.py` did not exist. This reverts each
guarantee **behaviourally**, inside the current API, one at a time, in a scratch
copy of the tree, and requires at least one test to go red for each.

Every mutation is a plausible edit rather than a wrecking ball:

  - `A1a` is the bare `op.create_index()` anyone would write who had not read
    the incident entry. It is the edit that makes the deploy abort on the live
    box, so criterion 1 is what has to catch it.
  - `A1b` reverses create-and-drop into drop-and-create. Both orders converge on
    the same schema, so only the DDL the migration *issues* can tell them apart.
  - `A1c` drops the model declarations, which is invisible to every behavioural
    test because the suite's schema comes from alembic. `alembic check` is what
    sees it.
  - `A2a` degrades the freshness join so no index on `contact_id` can serve it —
    the outage, put back.
  - `A2b` is the one worth reading the output for. It hands the guard a
    hand-written copy of the query **and** degrades the service's query. The
    plan assertion stays green, exactly as the session spec predicted; the test
    that checks the guard reads the service's own statement is the only thing
    that fails, which is the argument for building the guard the harder way.
  - `A3a`-`A3c` are the three ways "hidden from the picker, still resolving for
    history" comes apart.
  - `A4a` lets a rename write without checking for a collision; `A4b` stops it
    refreshing the stored labels, which is the whole of criterion 6.
  - `A5a` puts the text escaper back in attribute position, which is a real
    hole now that the client types the list names the composer interpolates.
  - `A6a` lets DELETE remove a list a campaign used.

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

    python3 agent/mutate-5j.py <scratch-tree> <python>
"""
import pathlib, re, subprocess, sys

SCRATCH = pathlib.Path(sys.argv[1])
PY = sys.argv[2]
REPO = pathlib.Path(__file__).resolve().parent.parent
TARGETS = [
    "tests/test_index_convergence.py",
    "tests/test_query_cost.py",
    "tests/test_list_admin.py",
    "tests/test_migrations.py",
    "tests/test_list_picker.py",
    "tests/test_dashboard.py",
    "tests/test_composer_markup.py",
]

MIGRATION = "alembic/versions/a3f1e08c5d47_contact_id_indexes_for_the_freshness_join.py"

MUTATIONS = {
 # ── A1: the migration that has to survive production's schema ──────────────
 "A1a the inspector check is gone and op.create_index() is called bare — the "
 "edit that makes the deploy abort on the live box": [
   (MIGRATION,
    "        present = _index_names(table)\n"
    "        if wanted not in present:\n"
    "            op.create_index(wanted, table, [column])",
    "        op.create_index(wanted, table, [column])")],

 "A1b the hand-made index is dropped before the new one exists, so the join "
 "runs unindexed for the width of the window": [
   (MIGRATION,
    "        present = _index_names(table)\n"
    "        if wanted not in present:\n"
    "            op.create_index(wanted, table, [column])\n"
    "        # Read after the create, so the drop below is decided against the state\n"
    "        # this migration has already produced rather than a stale snapshot.\n"
    "        if handmade in _index_names(table):\n"
    "            op.drop_index(handmade, table_name=table)",
    "        present = _index_names(table)\n"
    "        if handmade in present:\n"
    "            op.drop_index(handmade, table_name=table)\n"
    "        if wanted not in _index_names(table):\n"
    "            op.create_index(wanted, table, [column])")],

 "A1c the models no longer declare the indexes, so the schema and the model "
 "layer disagree about what the table has": [
   ("app/models/sms_message.py",
    '        Index("idx_sms_contact", "contact_id"),\n',
    ""),
   ("app/models/contact_list.py",
    '        Index("idx_member_contact", "contact_id"),\n',
    "")],

 "A1d downgrade recreates the hand-made names, putting a rolled-back box back "
 "into the state nothing describes": [
   (MIGRATION,
    "    for table, wanted, _column, _handmade in CONVERGENCE:\n"
    "        if wanted in _index_names(table):\n"
    "            op.drop_index(wanted, table_name=table)",
    "    for table, wanted, _column, _handmade in CONVERGENCE:\n"
    "        if wanted in _index_names(table):\n"
    "            op.drop_index(wanted, table_name=table)\n"
    "        if _handmade not in _index_names(table):\n"
    "            op.create_index(_handmade, table, [_column])")],

 # ── A2: the cost guard ─────────────────────────────────────────────────────
 "A2a the freshness join is degraded so no index on contact_id can serve it — "
 "the 10m02s plan, put back": [
   ("app/services/contact_service.py",
    "            .join(SMSMessage, SMSMessage.contact_id == ContactListMember.contact_id)",
    "            .join(SMSMessage, func.abs(SMSMessage.contact_id)\n"
    "                  == ContactListMember.contact_id)")],

 "A2b the guard explains a hand-written copy AND the service's query is "
 "degraded — the plan assertion must stay green and something else must fail": [
   ("tests/_query_plan.py",
    "    return [(sql, params) for sql, params in captured\n"
    "            if sql.lstrip().upper().startswith(\"SELECT\")]",
    "    return [(\n"
    "        'SELECT contact_list_members.list_id, max(sms_messages.sent_at) '\n"
    "        'FROM contact_list_members JOIN sms_messages ON '\n"
    "        'sms_messages.contact_id = contact_list_members.contact_id '\n"
    "        'GROUP BY contact_list_members.list_id', ())]"),
   ("app/services/contact_service.py",
    "            .join(SMSMessage, SMSMessage.contact_id == ContactListMember.contact_id)",
    "            .join(SMSMessage, func.abs(SMSMessage.contact_id)\n"
    "                  == ContactListMember.contact_id)")],

 "A2c the schema half is gone, so only the side today's statistics happen to "
 "favour is ever checked": [
   ("tests/_query_plan.py",
    "    return [name for name, columns in covering\n"
    "            if columns and columns[0] == column]",
    "    return [name for name, columns in covering\n"
    "            if columns and column in columns]")],

 # ── A3: hidden from the picker, still resolving for history ────────────────
 "A3a list_summaries() offers archived lists again": [
   ("app/services/contact_service.py",
    "    audiences += [{k: v for k, v in row.items() if k not in (\"id\", \"archived\")}\n"
    "                  for row in _list_rows(db, today) if not row[\"archived\"]]",
    "    audiences += [{k: v for k, v in row.items() if k not in (\"id\", \"archived\")}\n"
    "                  for row in _list_rows(db, today)]")],

 "A3b _term_ids_query() learns about the flag, so archiving a list empties "
 "every campaign that ever targeted it": [
   ("app/services/contact_service.py",
    "                .filter(ContactListMember.list_id == _int_arg(term),\n"
    "                        Contact.is_active == 1))",
    "                .join(ContactList, ContactList.id == ContactListMember.list_id)\n"
    "                .filter(ContactListMember.list_id == _int_arg(term),\n"
    "                        ContactList.archived != 1,\n"
    "                        Contact.is_active == 1))")],

 "A3c list_cards() queries lists directly instead of going through "
 "list_summaries(), so the panel and the dropdown can disagree": [
   ("app/services/dashboard_service.py",
    "    entries = contact_service.list_summaries(db)",
    "    from app.models.contact_list import ContactList\n"
    "    entries = [{\"selector\": f\"list:{row.id}\", \"label\": row.name,\n"
    "                \"count\": 0, \"kind\": \"list\", \"last_sent_at\": None,\n"
    "                \"days_since_sent\": None}\n"
    "               for row in db.query(ContactList).all()]\n"
    "    entries = [e for e in contact_service.list_summaries(db)\n"
    "               if e[\"kind\"] == \"all\"] + entries")],

 "A3d is_archived() reads NULL as archived, so a list nobody archived vanishes "
 "from the picker": [
   ("app/services/contact_service.py",
    "    return bool(value)\n\n\ndef _list_rows",
    "    return value != 1\n\n\ndef _list_rows")],

 # ── A4: rename ─────────────────────────────────────────────────────────────
 "A4a the rename writes without checking for a collision, so the unique index "
 "answers the client with a 500": [
   ("app/services/list_admin.py",
    "    clash = (db.query(ContactList)\n"
    "             .filter(ContactList.name == name, ContactList.id != list_id)\n"
    "             .first())",
    "    clash = None")],

 "A4b the rename stops refreshing the stored campaign labels, so the report of "
 "a campaign already sent keeps quoting the old name": [
   ("app/services/list_admin.py",
    "    for campaign in campaigns_using(db, list_id):",
    "    for campaign in []:")],

 "A4c the stored label is patched by substitution rather than rebuilt from "
 "audience_label(), so a compound selector loses its other term": [
   ("app/services/list_admin.py",
    "        fresh = contact_service.audience_label(db, campaign.audience)",
    "        fresh = name")],

 "A4d the PATCH applies the archive before the rename, so a refused rename "
 "still hides the list from the picker": [
   ("app/routers/contacts.py",
    "        if payload.name is not None:\n"
    "            result = list_admin.rename(db, list_id, payload.name)\n"
    "        if payload.archived is not None:\n"
    "            result = list_admin.set_archived(db, list_id, payload.archived)",
    "        if payload.archived is not None:\n"
    "            result = list_admin.set_archived(db, list_id, payload.archived)\n"
    "        if payload.name is not None:\n"
    "            result = list_admin.rename(db, list_id, payload.name)")],

 "A4e the collision refusal offers one remedy whatever holds the name, so an "
 "archived list is answered with advice that cannot work": [
   ("app/services/list_admin.py",
    "        remedy = (\"Rename that one first, or pick a different name here.\"\n"
    "                  if contact_service.is_archived(clash.archived)\n"
    "                  else \"Archive that one first, or pick a different name here.\")\n"
    "        held_by = (\"a list you have archived\" if contact_service.is_archived(clash.archived)\n"
    "                   else \"another list\")",
    "        remedy = \"Archive that one first, or pick a different name here.\"\n"
    "        held_by = \"another list\"")],

 "A5a the rename field escapes its value with the text escaper, so a list name "
 "carrying a double quote breaks out of the attribute": [
   ("app/templates/_composer-lists.html",
    '      <input type="text" value="${attr(row.label)}" data-list-name="${row.id}"',
    '      <input type="text" value="${esc(row.label)}" data-list-name="${row.id}"')],

 # ── A6: what DELETE may remove ─────────────────────────────────────────────
 "A6a DELETE removes a list a campaign used, degrading that campaign's report "
 "label to the raw selector": [
   ("app/services/list_admin.py",
    "    used_by = campaigns_using(db, list_id)",
    "    used_by = []")],

 "A6b the reference check goes back to a substring test, so list:12 is found "
 "inside list:120": [
   ("app/services/list_admin.py",
    "    try:\n"
    "        terms = _split_terms((selector or \"\").strip())\n"
    "    except ValueError:\n"
    "        return False\n"
    "    for term in terms:\n"
    "        term = term.strip()\n"
    "        if not term.startswith(\"list:\"):\n"
    "            continue\n"
    "        try:\n"
    "            if _int_arg(term) == list_id:\n"
    "                return True\n"
    "        except ValueError:\n"
    "            continue\n"
    "    return False",
    "    return f\"list:{list_id}\" in (selector or \"\")")],
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
