"""Revert one session-5m guard at a time and check that the suite notices.

Run by `agent/accept-5m.sh` as check 10. Each mutation is a plausible edit
inside the current API, applied to a scratch copy of the tree, and at least one
test must go red for each — the behavioural proof, not "the new modules fail to
import against the old tree".

5m fixed two live defects, and the two halves are tested very differently.

**A1 is JavaScript.** The composer's summary panel named three audiences at
once and nothing a Python test could read off the template would have found it:
`paintAudienceSummary()` *was* wired to the select's `change` event, which is
why the spec said so. So `tests/js/` runs the real partials in node against
response bodies produced by the real endpoints, and the `P*` mutations below
edit the templates. They are caught by `tests/test_composer_panel.py`, which is
the only thing in this repo that can see them.

**A2 is a timezone.** The `T*` mutations are caught by `tests/test_timezone.py`,
whose every case names an aware UTC instant rather than using the local clock —
the machine this runs on is already in the client's zone, so a test that asked
it what time it was would pass with the whole session deleted.

  - `P1` paints from a stale reply: the token gate goes, and the reply about the
    audience he left behind lands last and wins. This is the defect.
  - `P2` gives `sumSegments` its second writer back — pre-flight writing two of
    the six rows over a panel describing a different audience.
  - `P3` drops `audience_label` from `/preview`, so the panel has to name its
    audience from the control beside it again.
  - `P4` stops sending the cap to `/preview`: the panel quotes the whole list
    for a send capped at fifty, next to a checklist that quotes fifty.
  - `P5` prices an empty message at one segment per recipient.
  - `P6` zeroes the panel's rows directly instead of taking a ticket, so a reply
    already in flight fills them back in under an upload composer.
  - `T1` compares `scheduled_at` against the box's naive clock — the defect.
  - `T2` uses a fixed −4 offset instead of `zoneinfo`: right in September, an
    hour early every day from November to March.
  - `T3` drops the `status == "draft"` filter, which is what makes the repeated
    hour on the first Sunday in November send once instead of twice.
  - `T4` renders a client-facing time through the viewer's zone again.
  - `T5` leaves the process in the box's timezone, so every `sent_at` on a UTC
    droplet is four hours out and every screen shows it.
  - `T6` puts the dashboard's hero back on the box's clock — the same rule, the
    other call site, and the screen the client opens first.
  - `T7` has the migration convert a value that is already the client's wall
    clock: the 6:00 PM campaign shifted a second time, to 10:00 PM.
  - `T9` and `T10` are the fresh-context review's sharpest finding. Every
    criterion-5 test handed `due_campaign_ids` an aware instant, which takes
    `wall_clock()`'s *other* branch — so reinstating the production defect in
    `clock.now()` left both of them green, and pointing the aware branch at the
    box's own zone survived the entire suite. Both are caught now, by tests
    parametrized over three box zones and by one that calls the scheduler the
    way production does, with no `now` at all.
  - `T11` stores an offset-bearing send time verbatim: four hours **late**,
    the mirror of the defect, on the class the migration only adjudicated.
  - `T12` leaves the checklist spinning after a superseded report; `T13` keeps
    the previous audience's figures on screen while the new one is in flight —
    the window in which Create sends to an audience the panel is not describing.

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

    python3 agent/mutate-5m.py <scratch-tree> <python>
"""
import pathlib, re, subprocess, sys

SCRATCH = pathlib.Path(sys.argv[1])
PY = sys.argv[2]
REPO = pathlib.Path(__file__).resolve().parent.parent
TARGETS = [
    "tests/test_composer_panel.py",
    "tests/test_timezone.py",
    "tests/test_campaign_guardrails.py",
]

SCRIPT = "app/templates/_composer-script.html"
SUMMARY = "app/templates/_composer-summary.html"
UPLOAD = "app/templates/_composer-upload.html"
BASE = "app/templates/base.html"
ROUTER = "app/routers/campaigns.py"
DISPATCH = "app/services/campaign_dispatch.py"
CLOCK = "app/core/clock.py"
CONFIG = "app/core/config.py"
DASHBOARD = "app/services/dashboard_service.py"
MIGRATION = "alembic/versions/b7d43f0c9a15_adjudicate_campaign_scheduled_at.py"

MUTATIONS = {
 # ── A1: the composer's summary panel ───────────────────────────────────────
 "P1 a stale reply paints — the token gate goes from both response handlers, "
 "and the answer about the audience he left behind lands last and wins": [
   (SCRIPT, "    if (panelStale(token)) { return; }\n    lastPreview = data;",
            "    lastPreview = data;"),
   (SCRIPT, "    if (panelStale(token)) {\n        list.innerHTML",
            "    if (false) {\n        list.innerHTML")],

 "P2 sumSegments gets its second writer back — pre-flight writes two of the six "
 "rows over a panel describing a different audience": [
   (SCRIPT, "    paintSummary(preflightSummary(report));",
            "    if (report.counts?.segments_measured) {\n"
            "        el('sumSegments').textContent = "
            "report.counts.total_segments.toLocaleString();\n"
            "        el('sumCost').textContent = money(report.estimated_cost);\n"
            "    }")],

 "P3 /preview stops naming the audience it answered about, so the panel has to "
 "read its Audience row off the control beside it again": [
   (ROUTER, '        "audience": split["audience"],\n'
            '        "audience_label": split["audience_label"],\n'
            '        "recipients": recipients,',
            '        "audience": split["audience"],\n'
            '        "audience_label": None,\n'
            '        "recipients": recipients,')],

 "P4 the cap is not sent to /preview — the panel quotes the whole list for a "
 "send capped at fifty, beside a checklist that quotes fifty": [
   (SCRIPT, "        body: JSON.stringify({ message_template: template, audience,\n"
            "                               batch_size: batchSize() }),",
            "        body: JSON.stringify({ message_template: template, audience }),"),
   (ROUTER, "    split = audience_split.resolve(db, payload.audience, payload.batch_size)\n"
            "    recipients = split[\"recipients\"]",
            "    split = audience_split.resolve(db, payload.audience)\n"
            "    recipients = split[\"recipients\"]")],

 "P5 an empty message is priced as one segment per recipient — 10,146 segments "
 "and $2.19 quoted for an empty composer": [
   (ROUTER, "    written = bool(payload.message_template)\n"
            "    priced_for = recipients if written else 0",
            "    written = True\n"
            "    priced_for = recipients")],

 "P6 the upload tab zeroes the rows without taking a ticket, so a reply already "
 "in flight fills them back in under an upload composer": [
   (UPLOAD, "    panelRequest();          // retires every reply still on its way\n"
            "    paintSummary(EMPTY_SUMMARY);",
            "    paintSummary(EMPTY_SUMMARY);")],

 # ── A2: the clock ──────────────────────────────────────────────────────────
 "T1 the scheduler compares against the box's naive clock — the defect: a "
 "campaign set for 6:00 PM Eastern goes out at 2:00 PM": [
   (DISPATCH, "    cutoff = clock.wall_clock(now).isoformat()",
              "    cutoff = (now or datetime.now()).isoformat()")],

 "T2 a fixed −4 offset instead of zoneinfo — right in September, an hour early "
 "every day from November to March": [
   (CLOCK, "from datetime import date, datetime\nfrom typing import Optional",
           "from datetime import date, datetime, timedelta, timezone\n"
           "from typing import Optional\n"
           "_FIXED = timezone(timedelta(hours=-4))"),
   (CLOCK, "    return datetime.now(ZONE).replace(tzinfo=None)",
           "    return datetime.now(_FIXED).replace(tzinfo=None)"),
   (CLOCK, "    return moment.astimezone(ZONE).replace(tzinfo=None)",
           "    return moment.astimezone(_FIXED).replace(tzinfo=None)")],

 "T3 the draft filter goes — the repeated hour on the first Sunday in November "
 "sends the same campaign twice": [
   (DISPATCH, '            .filter(Campaign.status == "draft",\n'
              "                    Campaign.scheduled_at.isnot(None),",
              "            .filter(Campaign.scheduled_at.isnot(None),")],

 "T4 a client-facing time is rendered through the viewer's zone again": [
   (BASE, "function fmtDate(iso) {\n"
          "    if (!iso) return '—';\n"
          "    const t = appTime(iso);\n"
          "    if (!t) return String(iso);\n"
          "    const day = `${MONTHS[t.month - 1]} ${t.day}, ${t.year}`;\n"
          "    return t.hour === null ? day : `${day}, ${clockOf(t)}`;\n"
          "}",
          "function fmtDate(iso) {\n"
          "    if (!iso) return '—';\n"
          "    const d = new Date(iso);\n"
          "    return isNaN(d) ? iso : d.toLocaleString('en-US',\n"
          "        { month: 'short', day: 'numeric', year: 'numeric',\n"
          "          hour: 'numeric', minute: '2-digit' });\n"
          "}")],

 "T5 the process keeps the box's timezone — every sent_at on a UTC droplet is "
 "four hours out, and every screen shows it": [
   (CONFIG, '    os.environ["TZ"] = APP_ZONE_NAME\n'
            "    if hasattr(time, \"tzset\"):",
            '    os.environ["TZ"] = os.environ.get("TZ", APP_ZONE_NAME)\n'
            "    if False:")],

 "T6 the dashboard's hero goes back on the box's clock — the same rule, the "
 "other call site, on the screen the client opens first": [
   (DASHBOARD, "                            scheduled_at >= clock.now_iso(),",
               "                            scheduled_at >= datetime.now().isoformat(),")],

 "T7 the migration converts a value that is already the client's wall clock — "
 "the 6:00 PM campaign shifted a second time, to 10:00 PM": [
   (MIGRATION, '    if classify(value) != "offset":\n        return None',
               '    if classify(value) == "unreadable":\n        return None\n'
               "    if classify(value) == \"naive\":\n"
               "        from datetime import timezone as _tz\n"
               "        return (datetime.fromisoformat(str(value).strip())\n"
               "                .replace(tzinfo=_tz.utc).astimezone(APP_ZONE)\n"
               "                .replace(tzinfo=None).isoformat())")],

 "T9 clock.now() answers in UTC — the production defect reinstated on the path "
 "production actually takes, which every criterion-5 test used to miss": [
   (CLOCK, "    return datetime.now(ZONE).replace(tzinfo=None)",
           "    from datetime import timezone as _utc\n"
           "    return datetime.now(_utc.utc).replace(tzinfo=None)")],

 "T10 wall_clock's aware branch converts into the BOX's zone rather than the "
 "client's — the exact line due_campaign_ids calls, and a survivor until the "
 "5m review found it": [
   (CLOCK, "    return moment.astimezone(ZONE).replace(tzinfo=None)",
           "    return moment.astimezone().replace(tzinfo=None)")],

 "T11 an offset-bearing send time is stored verbatim — a campaign booked for "
 "18:00:00+00:00 goes out at 6:00 PM Eastern, four hours late": [
   (CLOCK, "    if parsed.tzinfo is None:\n"
           "        # Re-spelled rather than passed through.",
           "    if True:\n"
           "        # Re-spelled rather than passed through.")],

 "T12 a superseded pre-flight leaves \"Running checks…\" on screen forever, on "
 "the one page whose job is to stop a bad send": [
   (SCRIPT, "    if (panelStale(token)) {\n"
            "        list.innerHTML = '<li class=\"px-5 py-4 text-[13px] text-ink-3\">'",
            "    if (panelStale(token)) {\n"
            "        return;\n"
            "    }\n"
            "    if (false) {\n"
            "        list.innerHTML = '<li class=\"px-5 py-4 text-[13px] text-ink-3\">'")],

 "T13 the panel keeps the previous audience's figures while the new one is on "
 "the wire — every row agreeing with every other, and wrong": [
   (SCRIPT, "el('audience').addEventListener('change', () => {\n"
            "    paintSummary(pendingSummary());\n"
            "    schedulePreview();\n"
            "});",
            "el('audience').addEventListener('change', schedulePreview);")],

 # There is no `T8`. It was "APScheduler inherits a zone instead of being told
 # one", and it **survived** on the first run — correctly. `apply_process_timezone()`
 # has already set the process zone by the time `main.py` is imported, so a bare
 # `AsyncIOScheduler()` infers the same zone and nothing can tell them apart.
 # The argument stays because it makes the scheduler's zone visible where the
 # scheduler is and because it is the only thing saying it on a platform with no
 # `time.tzset()`; the comment on that line now says it is not a guard, rather
 # than reading as a rule the next session would defend. P2's lesson, one
 # session along: when you add a guard, ask what makes it reachable *here*.
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
