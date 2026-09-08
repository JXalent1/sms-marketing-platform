"""Revert one session-B1b guard at a time and check that the suite notices.

Run by `agent/accept-B1b.sh` as check 10. Each mutation is a plausible edit
inside the current API, applied to a scratch copy of the tree, and at least one
test must go red for each — the behavioural proof, not "the new modules fail to
import against the old tree".

The nine the spec names, and where each lands:

  - `S1` meters at send time again — the entire session in reverse. The send
    path posts a meter event for the rows it just wrote, unsettled, unmarked.
    Caught by the end-to-end send (zero Stripe calls) and by the sweep that
    counts the places a segment can be metered.
  - `S2` meters unsettled rows: a `sent` row is reported the hour it is sent,
    before the delivery webhook can move it out of the billable set.
  - `S3` skips the `metered_at` mark after a successful report; `S4` marks
    before the report succeeds — the two halves of "same transaction as the
    successful report", each with a test that goes red without the other.
  - `S5` reports a marked row: the ledger filter is dropped, and the second
    pass — asserted on the *call count*, with a fake that bills every call —
    re-reports everything.
  - `S6` drops the 35-day refusal and meters silently; `S6b` keeps the refusal
    but demotes it to DEBUG, which is the same thing on a box nobody tails.
  - `S7` omits the timestamp so the event lands in the reporting cycle; `S7b`
    stamps it with the pass's clock instead of the send's, which is the same
    defect written more carefully.
  - `S8` restates `BILLABLE_STATUSES` locally — identical today, deaf to any
    commercial change; `S8b`/`S8c` restate one set each, so a copy of either
    is caught on its own.
  - `S9` lets the pass raise into the scheduler; `S9b` lets a Stripe failure
    raise out of the pass, which is the same thing one layer down.

And the ones the mechanism added: the bound (`B1`, `B1b`), the raw count
(`B2`), the legacy rule (`B3`), the identifier (`B4`), one batch per day
(`B5`), the mark chunking (`B6`), stop-on-failure (`B7`), the scheduler
registration (`B8`, `B8b`), the empty-customer guard (`B9`), and the
reconciliation view (`B10`, `B11`).

`R1`-`R8` are what one fresh-context review found in a tree at 26 caught / 0
survived. `R1` moves money: the back-bill tool priced a window the meter had
already billed half of, and would have invoiced that half twice with the
allowance applied twice. `R2`-`R5` are the staged batch — the review's second
finding was that a database lock during the mark, under a delivery-webhook
storm, is a likelier way into the lost-commit window than a process death,
and that one status flip in the batch between passes changed a recomputed
identifier and billed the survivors twice. `R8` is the same rule from the
other side.

## This harness verifies its own preconditions first

Before a single patch is applied, every file this script touches is compared
byte for byte against the repo's copy, and every anchor is resolved. It prints
`SCRATCH VERIFIED PRISTINE` and `ANCHORS VERIFIED` or exits 2. Read the
results the same way: check that the tests failing for a mutation are the
tests that **name** it.

Nothing here sends, nothing here calls a paid API, and nothing here reaches
Stripe — the scratch tree inherits `conftest.py`, which blanks the Stripe
credentials for the whole suite. It edits a scratch copy of the tree, never the
repo, and restores every file between mutations.

    python3 agent/mutate-B1b.py <scratch-tree> <python>
"""
import pathlib, re, subprocess, sys

SCRATCH = pathlib.Path(sys.argv[1])
PY = sys.argv[2]
REPO = pathlib.Path(__file__).resolve().parent.parent
TARGETS = [
    "tests/test_metering_pass.py",
    "tests/test_stripe_billing.py",
    "tests/test_stripe_contract.py",
    "tests/test_billing.py",
]

METER = "app/services/stripe_meter.py"

MUTATIONS = {
 # ── The nine the spec names ────────────────────────────────────────────────
 "S1 the send path meters again — a meter event posted the instant the send "
 "loop returns, unsettled and unmarked, from a second place": [
   ("app/services/campaign_dispatch.py",
    "        await CampaignService(db).send_campaign(campaign_id)\n"
    "    except Exception as e:\n"
    '        logger.error(f"Campaign {campaign_id} background send failed: {e}")',
    "        await CampaignService(db).send_campaign(campaign_id)\n"
    "        from app.models.sms_message import SMSMessage\n"
    "        from app.services import stripe_billing, stripe_meter\n"
    "        rows = db.query(SMSMessage).filter(\n"
    "            SMSMessage.campaign_id == campaign_id,\n"
    "            SMSMessage.status == 'sent').all()\n"
    "        if rows and stripe_billing.configured() and stripe_billing.customer_id(db):\n"
    "            for batch in stripe_meter.batches(rows):\n"
    "                stripe_billing.api().create_meter_event(\n"
    "                    event_name='sms_segments', identifier=batch.identifier,\n"
    "                    payload={'stripe_customer_id': stripe_billing.customer_id(db),\n"
    "                             'value': str(batch.segments)})\n"
    "    except Exception as e:\n"
    '        logger.error(f"Campaign {campaign_id} background send failed: {e}")')],

 "S2 unsettled rows are metered — a sent row is reported the hour it is sent, "
 "before the webhook can move it out of the billable set": [
   (METER,
    "                        SMSMessage.sent_at <= settle_cutoff(now).isoformat()))",
    "                        SMSMessage.sent_at <= now.isoformat()))")],

 "S3 the metered_at mark is skipped after a successful report": [
   (METER,
    '         .update({"metered_at": stamp}, synchronize_session=False))',
    '         .update({"metered_at": None}, synchronize_session=False))')],

 "S4 rows are marked BEFORE the report succeeds — a Stripe failure leaves them "
 "marked and never billed": [
   (METER,
    "    try:\n"
    "        _report(customer, staged)\n"
    "    except Exception as exc:",
    "    _finish(db, staged, now)\n"
    "    try:\n"
    "        _report(customer, staged)\n"
    "    except Exception as exc:")],

 "S5 a marked row is reported again — the ledger filter is dropped": [
   (METER,
    "                    SMSMessage.metered_at.is_(None),\n",
    "")],

 "S6 the 35-day refusal is dropped and old usage is metered silently": [
   (METER,
    "    too_old = too_old_unmetered(db, now, bound)",
    "    too_old = []"),
   (METER,
    "            .filter(SMSMessage.sent_at >= too_old_cutoff(now).isoformat(),",
    "            .filter(SMSMessage.sent_at >= since.isoformat(),")],

 "S6b the refusal is kept but logged at DEBUG — silent on any box nobody tails": [
   (METER,
    "        logger.error(\n"
    '            "USAGE NOT METERED:',
    "        logger.debug(\n"
    '            "USAGE NOT METERED:')],

 "S7 the timestamp is omitted, so the event lands in the cycle the pass ran in": [
   (METER,
    '        timestamp=staged["timestamp"],\n',
    "")],

 "S7b the event is stamped with the pass's clock rather than the send's": [
   (METER,
    '        timestamp=staged["timestamp"],',
    "        timestamp=int(datetime.now().timestamp()),")],

 "S8 both status sets are restated locally — identical today, deaf to any "
 "commercial change": [
   (METER,
    "from app.models import sms_message as message_model\n"
    "from app.models.sms_message import SMSMessage",
    "from app.models.sms_message import SMSMessage\n\n\n"
    "class message_model:\n"
    '    BILLABLE_STATUSES = ("sent", "delivered")\n'
    '    SETTLED_STATUSES = ("delivered",)')],

 "S8b only SETTLED_STATUSES is restated, at the one place it is read": [
   (METER,
    "                    or_(SMSMessage.status.in_(message_model.SETTLED_STATUSES),",
    '                    or_(SMSMessage.status.in_(("delivered",)),')],

 "S8c only BILLABLE_STATUSES is restated, at the one place the pass reads it": [
   (METER,
    "            .filter(SMSMessage.status.in_(message_model.BILLABLE_STATUSES),\n"
    "                    SMSMessage.metered_at.is_(None),",
    '            .filter(SMSMessage.status.in_(("sent", "delivered")),\n'
    "                    SMSMessage.metered_at.is_(None),")],

 "S9 the scheduled job lets an exception escape into APScheduler": [
   (METER,
    "    except Exception as exc:\n"
    '        logger.error("Metering pass failed: %s", exc)',
    "    except ZeroDivisionError as exc:\n"
    '        logger.error("Metering pass failed: %s", exc)')],

 "S9b a Stripe failure raises out of the pass instead of stopping it": [
   (METER,
    "    except Exception as exc:\n"
    "        # Nothing marked. The batch stays staged",
    "    except ZeroDivisionError as exc:\n"
    "        # Nothing marked. The batch stays staged")],

 # ── The mechanism's own guards ─────────────────────────────────────────────
 "B1 the lower bound is dropped — every pre-subscription row, settled by the "
 "one-time balance, is metered a second time": [
   (METER,
    "    bound = since or subscription_start(db)\n"
    "    if bound is None:",
    "    bound = since or subscription_start(db) or date(1970, 1, 1)\n"
    "    if False:")],

 "B1b the stored subscription start is ignored — the pass ranges from the "
 "beginning of time whether or not a start is stored": [
   (METER,
    "    bound = since or subscription_start(db)\n"
    "    if bound is None:",
    "    bound = since or date(1970, 1, 1)\n"
    "    if bound is None:")],

 "B2 the meter is sent billable_segments() — the allowance is subtracted twice "
 "and a 15,000-segment month bills $0": [
   (METER,
    '                 "value": str(staged["segments"])},',
    '                 "value": str(billing_service.billable_segments(\n'
    '                     staged["segments"]))},')],

 "B3 a legacy row is priced as 1 while /usage prices it by length": [
   (METER,
    "    return int(segments) if segments else billing_service.legacy_segment_count(message)",
    "    return int(segments or 1)")],

 "B4 the identifier is non-deterministic — two builds of one batch are two "
 "events": [
   (METER,
    "                f\"_{len(self.ids)}_{self.segments}\")",
    "                f\"_{len(self.ids)}_{self.segments}_{__import__('uuid').uuid4().hex}\")")],

 "B5 one batch per campaign rather than per day — a campaign straddling "
 "midnight on the last day of a cycle lands whole in one of them": [
   (METER,
    "        grouped.setdefault((row.campaign_id, row.sent_at[:10]), []).append(row)",
    "        grouped.setdefault((row.campaign_id, \"\"), []).append(row)")],

 "B6 only the first chunk of a large batch is marked": [
   (METER,
    "    for start in range(0, len(ids), MARK_CHUNK):",
    "    for start in range(0, min(len(ids), MARK_CHUNK), MARK_CHUNK):")],

 "B7 the pass keeps going after a failure — hammering a Stripe that is down": [
   (METER,
    "        if not _offer(db, customer, staged, now, verdict):\n"
    "            break",
    "        if not _offer(db, customer, staged, now, verdict):\n"
    "            continue")],

 "B8 the pass is not registered with the scheduler at all": [
   ("app/main.py",
    "        billing_meter.metering_pass_job,\n"
    "        IntervalTrigger(hours=1),",
    "        billing_tiers.daily_tier_check,\n"
    "        IntervalTrigger(hours=1),")],

 "B8b the pass is registered under the tier check's id, so replace_existing "
 "silently unregisters the tier check": [
   ("app/main.py",
    '        id="usage_metering", replace_existing=True, max_instances=1,',
    '        id="daily_tier_check", replace_existing=True, max_instances=1,')],

 "B9 a campaign with no stored customer is metered against an empty one": [
   (METER,
    "    if not customer:\n"
    '        verdict["reason"] = "no subscription yet"\n'
    "        return verdict",
    "    if not customer:\n"
    '        customer = ""')],

 "B10 the reconciliation view labels usage the meter can never take as merely "
 "awaiting the next pass": [
   ("app/services/stripe_reconcile.py",
    "    if row.sent_at < too_old_cutoff(now).isoformat():\n"
    "        return TOO_OLD",
    "    if False:\n"
    "        return TOO_OLD")],

 "B11 the back-bill tool drops the --unmetered view": [
   ("tools/bill_period.py",
    "        if args.unmetered or refusal:\n"
    "            print(describe_unmetered(breakdown))",
    "        if False:\n"
    "            print(describe_unmetered(breakdown))")],

 # ── Review round: what one fresh-context pass found in a tree at 26/0 ──────
 "R1 the back-bill tool drafts an invoice over a window the meter has already "
 "billed part of — the metered half twice, the allowance twice": [
   ("tools/bill_period.py",
    "        if refusal:\n"
    "            return 2",
    "        if False:\n"
    "            return 2")],

 "R2 the batch is written down AFTER the Stripe call, so a failure leaves "
 "nothing staged and the next pass recomputes the identifier": [
   (METER,
    "        staged = _stage(db, batch, now)\n"
    "        if not _offer(db, customer, staged, now, verdict):",
    '        staged = {**batch.staged(), "staged_at": now.isoformat(timespec="seconds")}\n'
    "        if not _offer(db, customer, staged, now, verdict):")],

 "R3 a staged batch is replayed under a fresh identifier — the same rows, "
 "a second event": [
   (METER,
    "        if not _offer(db, customer, staged, now, verdict):\n"
    "            return verdict\n"
    '        verdict["replayed"] = _summary(staged)',
    '        if not _offer(db, customer, {**staged, "identifier": staged["identifier"] + "_r"},\n'
    "                      now, verdict):\n"
    "            return verdict\n"
    '        verdict["replayed"] = _summary(staged)')],

 "R4 a mark that fails after Stripe accepted is reported as success": [
   (METER,
    '        verdict["failed"] = {**_summary(staged), "error": str(exc)}\n'
    "        return False\n"
    "    return True",
    '        verdict["failed"] = None\n'
    "        return True\n"
    "    return True")],

 "R5 a batch staged longer than Stripe's identifier window is retried anyway": [
   (METER,
    "        if _is_stale(staged, now):",
    "        if False:")],

 "R6 a key removed after checkout is a silent hourly skip": [
   (METER,
    '            logger.error("A Stripe customer is stored but STRIPE_SECRET_KEY or "',
    '            logger.debug("A Stripe customer is stored but STRIPE_SECRET_KEY or "')],

 "R7 a dry run posts": [
   (METER,
    "        if dry_run:\n"
    '            verdict["reported"].append(_summary(batch.staged()))\n'
    "            continue",
    "        if False:\n"
    '            verdict["reported"].append(_summary(batch.staged()))\n'
    "            continue")],

 "R8 a staged batch is ignored by the next pass — Stripe may have the event, "
 "the rows are unmarked, and they are batched afresh": [
   (METER,
    "    staged = pending_batch(db)\n"
    "    if staged and not dry_run:",
    "    staged = pending_batch(db)\n"
    "    if False:")],
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
    print("\nmutations that could not be applied (fix the harness, not the code):")
    for name in unapplied:
        print(f"   -> {name}")
if survivors:
    print("\nreverted guard(s) that no test noticed:")
    for name in survivors:
        print(f"   -> {name}")
sys.exit(1 if (survivors or unapplied) else 0)
