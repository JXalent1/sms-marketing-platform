"""Revert one session-B1 guard at a time and check that the suite notices.

Run by `agent/accept-B1.sh` as check 11. "The new test modules are red against
the pre-B1 tree" is true and nearly meaningless — they fail there at *import*,
because `stripe_billing` and `stripe_meter` did not exist. This reverts each
guard **behaviourally**, inside the current API, one at a time, in a scratch copy
of the tree, and requires at least one test to go red for each.

Every mutation is a plausible edit rather than a wrecking ball. The ones worth
naming:

  - `A1a` is the entire session in one line. `billable_segments()` and the
    Stripe tier both subtract 10,000, so reporting the first to the second
    subtracts it twice: a 15,000-segment month invoices $0 instead of $75 and
    `/usage` goes on saying $75 due. It is the quietest way this codebase could
    lose money.
  - `A1c` restates `BILLABLE_STATUSES` locally. Behaviourally identical *today*,
    which is exactly why no assertion about its contents can catch it — 5i's
    lesson, and the reason `campaign_segments()` reads the constant through the
    model module rather than binding it at import.
  - `A3b` drops `payment_method_collection`. Nothing fails until the first month
    under the allowance, when a $0 invoice turns out to have no card behind it.
  - `A5a`/`A5b` remove the ownership guard from each of its two call sites in
    turn. A guard on one path is a guard on one path, and this codebase has
    shipped that mistake twice.
  - `A6b` makes the meter identifier non-deterministic, which is a double bill
    on every retry and every redeploy.
  - `A6c` lets the reporting error escape into the send loop — **two patches in
    one mutation**, deliberately: either wrapper alone still catches, so
    reverting only one proves nothing about the rule.
  - `A2c` reads a tier's `unit_amount` instead of `unit_amount_decimal`.
    `unit_amount` is an integer number of cents and this client's rate is one
    and a half of them, so the check would call a correct price wrong. This is
    the mechanism `sessions/session-B1.md` A2 actually names; the SDK is what
    said otherwise.
  - `A6f` unbounds the backfill, which re-meters the period the one-time balance
    already settled.
  - `R1`-`R8` are the defects a fresh-context review found after the first 38
    were all green. None of them was caught by anything above, which is the
    argument for running both: the harness proves a rule cannot be reverted,
    and only a reader notices a rule that was written slightly wrong in the
    first place. `R1` is the sharpest — two similar arithmetics with a docstring
    between them asserting they agreed.

## This harness verifies its own preconditions first

P1's first mutation run reported 38 caught, 0 survived and was worthless: a run
killed on a timeout had left the scratch tree already mutated, so every verdict
sat on top of a leftover edit. So before a single patch is applied, every file
this script touches is compared byte for byte against the repo's copy, and the
run stops if any differs. It prints `SCRATCH VERIFIED PRISTINE` when they match.

Read the results the same way: check that the tests failing for a mutation are
the tests that **name** it. A harness is code and it fails the same ways.

Nothing here sends, nothing here calls a paid API, and nothing here reaches
Stripe — the scratch tree inherits `conftest.py`, which blanks the Stripe
credentials for the whole suite. It edits a scratch copy of the tree, never the
repo, and restores every file between mutations.

    python3 agent/mutate-B1.py <scratch-tree> <python>
"""
import pathlib, re, subprocess, sys

SCRATCH = pathlib.Path(sys.argv[1])
PY = sys.argv[2]
REPO = pathlib.Path(__file__).resolve().parent.parent
TARGETS = [
    "tests/test_stripe_billing.py",
    "tests/test_stripe_contract.py",
    "tests/test_billing.py",
    "tests/test_whitelabel.py",
]

MUTATIONS = {
 # ── A1: the allowance is applied once, and it is applied in Stripe ─────────
 "A1a the meter is sent billable_segments() instead of the raw count — the "
 "allowance is subtracted twice and a 15,000-segment month bills $0": [
   ("app/services/stripe_meter.py",
    "    return report_segments(campaign_segments(db, campaign_id), campaign_id, db)",
    "    return report_segments(\n"
    "        billing_service.billable_segments(campaign_segments(db, campaign_id)),\n"
    "        campaign_id, db)")],

 "A1b the backfill applies the allowance on the way out, so a replayed "
 "campaign bills less than the one that was reported live": [
   ("app/services/stripe_meter.py",
    "        segments = campaign_segments(db, campaign_id)\n"
    "        if segments <= 0:",
    "        segments = billing_service.billable_segments(\n"
    "            campaign_segments(db, campaign_id))\n"
    "        if segments <= 0:")],

 "A1c BILLABLE_STATUSES is restated locally instead of read from the model — "
 "identical today, and deaf to any commercial change to the set": [
   ("app/services/stripe_meter.py",
    "from app.models import sms_message as message_model\n"
    "from app.models.sms_message import SMSMessage",
    "from app.models.sms_message import SMSMessage\n\n\n"
    "class message_model:\n"
    '    BILLABLE_STATUSES = ("sent", "delivered")')],

 "A1d a row with no segment count is dropped entirely, so the meter and "
 "/usage disagree about legacy rows": [
   ("app/services/stripe_meter.py",
    "    return sum(int(segments) if segments\n"
    "               else billing_service.legacy_segment_count(message)\n"
    "               for segments, message in rows)",
    "    return sum(int(segments or 0) for segments, _ in rows)")],

 # ── A2: the tier and the plan must prove they agree ────────────────────────
 "A2a the tier-drift check always reports agreement": [
   ("app/services/stripe_tiers.py",
    '    return {"state": "disagree" if issues else "agree", "issues": issues}',
    '    return {"state": "agree", "issues": []}')],

 "A2b the first tier's size is not compared, so an allowance changed in .env "
 "and never changed in Stripe passes": [
   ("app/services/stripe_tiers.py",
    "    if included != settings.BILLING_SEGMENTS_INCLUDED:",
    "    if False:")],

 "A2c the rate is read from unit_amount (integer cents), which cannot express "
 "$0.015 — the mechanism the spec named": [
   ("app/services/stripe_tiers.py",
    '    rate_cents = _cents(getattr(second, "unit_amount_decimal", None))\n'
    "    if rate_cents is None:\n"
    '        rate_cents = _cents(getattr(second, "unit_amount", None))',
    '    rate_cents = _cents(getattr(second, "unit_amount", None))')],

 "A2d a Stripe outage is reported as agreement rather than as its own state": [
   ("app/services/stripe_tiers.py",
    '        return _store_verdict(db, "unavailable", [',
    '        return _store_verdict(db, "agree", [')],

 "A2e a configured box that has never run the check reports healthy pricing": [
   ("app/services/stripe_tiers.py",
    '            return {"state": "not_configured" if not stripe_billing.configured()\n'
    '                    else "never_checked",',
    '            return {"state": "not_configured",')],

 "A2f /health stops reporting the pricing verdict at all": [
   ("app/routers/pages.py",
    '        "pricing_ok": stripe_tiers.pricing_ok(pricing),\n'
    '        "pricing_state": pricing.get("state"),\n'
    '        "pricing_issues": stripe_tiers.public_pricing_issues(pricing),',
    '        "pricing_ok": True,\n'
    '        "pricing_state": "agree",\n'
    '        "pricing_issues": [],')],

 "A2g the price is fetched without expanding its tiers, so every price looks "
 "like a price with no tiers": [
   ("app/services/stripe_billing.py",
    '        return self._stripe().Price.retrieve(price_id, expand=["tiers"],\n'
    "                                             api_key=self._key)",
    "        return self._stripe().Price.retrieve(price_id, api_key=self._key)")],

 # ── A3: one checkout, both jobs ────────────────────────────────────────────
 "A3a a quantity is put on the metered line item, which Stripe rejects "
 "outright — the client cannot check out at all": [
   ("app/services/stripe_billing.py",
    '    items = [{"price": settings.STRIPE_PRICE_METERED}]',
    '    items = [{"price": settings.STRIPE_PRICE_METERED, "quantity": 1}]')],

 "A3b payment_method_collection is dropped — a $0 recurring total completes "
 "with no card on file, and every month under the allowance is $0": [
   ("app/services/stripe_billing.py",
    '        payment_method_collection="always",\n',
    "")],

 "A3c the outstanding balance is left off the session, so checkout takes a "
 "card and settles nothing": [
   ("app/services/stripe_billing.py",
    "    if settings.STRIPE_PRICE_BALANCE:\n"
    '        items.append({"price": settings.STRIPE_PRICE_BALANCE, "quantity": 1})',
    "    if False:\n"
    '        items.append({"price": settings.STRIPE_PRICE_BALANCE, "quantity": 1})')],

 "A3d the checkout is a payment rather than a subscription, so nothing is "
 "metered afterwards": [
   ("app/services/stripe_billing.py",
    '        mode="subscription",',
    '        mode="payment",')],

 # ── A4: one window, described twice ────────────────────────────────────────
 "A4a BILLING_CYCLE_DAY is preferred over the stored anchor — /usage shows a "
 "1st-to-1st cycle while the invoice runs 9th to 9th": [
   ("app/services/billing_service.py",
    "        stored = get_setting(db, CYCLE_ANCHOR_DAY_KEY)\n"
    "        if stored is None:\n"
    "            return settings.BILLING_CYCLE_DAY",
    "        stored = None\n"
    "        if stored is None:\n"
    "            return settings.BILLING_CYCLE_DAY")],

 "A4b the anchor is never stored, so there is nothing for /usage to prefer": [
   ("app/services/stripe_billing.py",
    "        set_setting(db, CYCLE_ANCHOR_DAY_KEY, str(day),",
    "        set_setting(db, CYCLE_ANCHOR_DAY_KEY + '_unused', str(day),")],

 "A4c the anchor day comes from the current period's start, so an anchor on "
 "the 31st is flattened to the 28th by one February": [
   ("app/services/stripe_billing.py",
    '    ts = getattr(subscription, "billing_cycle_anchor", None)\n'
    "    if not ts:\n"
    "        item = _first_item(subscription)\n"
    '        ts = getattr(item, "current_period_start", None) if item else None',
    "    item = _first_item(subscription)\n"
    '    ts = getattr(item, "current_period_start", None) if item else None')],

 "A4d the subscription's period end is used as-is, so the reported window "
 "overlaps the next one by a day": [
   ("app/services/stripe_billing.py",
    "    return (_local_date(start_ts),\n"
    "            date.fromordinal(_local_date(end_ts).toordinal() - 1))",
    "    return (_local_date(start_ts), _local_date(end_ts))")],

 "A4e pricing_table() reports the configured day rather than the day in "
 "force, so the plan panel contradicts the invoice": [
   ("app/services/billing_service.py",
    '        "cycle_day": cycle_day(db),',
    '        "cycle_day": settings.BILLING_CYCLE_DAY,')],

 # ── A5: the shared-account guard ───────────────────────────────────────────
 "A5a the webhook stores the customer from ANY completed checkout — another "
 "client's payment re-points this client's meter at their card": [
   ("app/services/stripe_billing.py",
    "    if not session_is_ours(session_id):",
    "    if False:")],

 # The route-level `session_is_ours()` call is a round-trip saver, not the
 # guard — see the docstring on `checkout_success`. Reverting it alone changes
 # nothing, because the decision is made one layer down, so a mutation that
 # removed only it would survive and read as an uncovered guard. The plausible
 # defect is the shortcut: "we already hold the session, just store it."
 "A5b the success page stores the customer directly instead of going through "
 "the handler that applies the guard — anyone can hit that URL with any id": [
   ("app/routers/billing.py",
    "    stored = {\"stored\": False, \"reason\": \"no session id\"}\n"
    "    if session_id and stripe_billing.session_is_ours(session_id):\n"
    "        try:\n"
    "            session = stripe_billing.api().retrieve_checkout_session(session_id)\n"
    "            stored = stripe_billing.handle_checkout_completed(\n"
    "                db, {\"data\": {\"object\": session}})",
    "    stored = {\"stored\": False, \"reason\": \"no session id\"}\n"
    "    if session_id:\n"
    "        try:\n"
    "            session = stripe_billing.api().retrieve_checkout_session(session_id)\n"
    "            stripe_billing.store_subscription(db, session.customer,\n"
    "                                              session.subscription)\n"
    "            stored = {\"stored\": True}")],

 "A5b2 the success page skips the round-trip saver AND the handler's guard is "
 "the only thing left — which must still be enough": [
   ("app/routers/billing.py",
    "    if session_id and stripe_billing.session_is_ours(session_id):",
    "    if session_id:"),
   ("app/services/stripe_billing.py",
    "    if not session_is_ours(session_id):",
    "    if False:")],

 "A5c the guard answers yes when Stripe cannot be read — a timeout becomes a "
 "customer id from a session nobody verified": [
   ("app/services/stripe_billing.py",
    "        logger.error(\"Could not read line items for checkout session %s: %s\",\n"
    "                     session_id, exc)\n"
    "        return False",
    "        logger.error(\"Could not read line items for checkout session %s: %s\",\n"
    "                     session_id, exc)\n"
    "        return True")],

 "A5d an unsigned webhook is trusted": [
   ("app/services/stripe_billing.py",
    "    if not signature:\n"
    '        logger.error("Stripe webhook received with no signature header — ignored")\n'
    "        return None",
    "    if not signature:\n"
    '        logger.error("Stripe webhook received with no signature header — ignored")\n'
    "        return {}")],

 "A5e no signing secret configured means the payload is believed rather than "
 "ignored — the guard that is switched off waves things through": [
   ("app/services/stripe_billing.py",
    "    if not settings.STRIPE_WEBHOOK_SECRET:\n"
    '        logger.error("Stripe webhook received with no STRIPE_WEBHOOK_SECRET "\n'
    '                     "configured — payload ignored, nothing stored")\n'
    "        return None",
    "    if not settings.STRIPE_WEBHOOK_SECRET:\n"
    '        logger.error("Stripe webhook received with no STRIPE_WEBHOOK_SECRET "\n'
    '                     "configured — payload ignored, nothing stored")\n'
    "        return _event_field(None) or __import__('json').loads(payload or b'{}')")],

 "A5f the route accepts an unverified webhook instead of refusing it": [
   ("app/routers/billing.py",
    "    if event is None:\n"
    '        return JSONResponse({"error": "signature not verified"}, status_code=400)',
    "    if event is None:\n"
    '        event = {"type": "checkout.session.completed"}')],

 # ── A6: metering that cannot stop a send and cannot double-bill ────────────
 "A6a the Send button's path never reports what it sent": [
   ("app/services/campaign_dispatch.py",
    "        await CampaignService(db).send_campaign(campaign_id)\n"
    "        report_usage(db, campaign_id)",
    "        await CampaignService(db).send_campaign(campaign_id)")],

 "A6a2 the scheduler's path never reports what it sent — the same hook, the "
 "other entry point": [
   ("app/services/campaign_dispatch.py",
    "                await service.send_campaign(campaign_id)\n"
    "                report_usage(db, campaign_id)",
    "                await service.send_campaign(campaign_id)")],

 "A6b the meter identifier is non-deterministic — every retry, redeploy and "
 "backfill bills the same campaign again": [
   ("app/services/stripe_meter.py",
    '    identifier = f"campaign_{campaign_id}"',
    '    identifier = f"campaign_{campaign_id}_{date.today().isoformat()}_"\\\n'
    "                 f\"{len(str(segments))}{segments}\"")],

 "A6c the reporting error escapes into the send loop — BOTH wrappers, because "
 "either alone still catches and reverting one proves nothing": [
   ("app/services/stripe_meter.py",
    "    except Exception as exc:\n"
    "        # Logged and swallowed. The caller is on the send path and a carrier\n"
    "        # that worked must not be undone by a payment processor that did not;\n"
    "        # `backfill_unreported()` is the recovery, and it is safe because of the\n"
    "        # identifier above.\n"
    '        logger.error("Meter event %s (%s segments) failed: %s",\n'
    "                     identifier, segments, exc)\n"
    '        return {"reported": False, "reason": "stripe call failed",\n'
    '                "segments": segments, "error": str(exc)}',
    "    except ZeroDivisionError as exc:\n"
    '        logger.error("Meter event %s (%s segments) failed: %s",\n'
    "                     identifier, segments, exc)\n"
    '        return {"reported": False, "reason": "stripe call failed",\n'
    '                "segments": segments, "error": str(exc)}'),
   ("app/services/campaign_dispatch.py",
    "    except Exception as e:\n"
    '        logger.error(f"Campaign {campaign_id} usage reporting failed: {e}")',
    "    except ZeroDivisionError as e:\n"
    '        logger.error(f"Campaign {campaign_id} usage reporting failed: {e}")')],

 "A6d a campaign the client never subscribed for is metered against nothing, "
 "so the payload carries an empty customer": [
   ("app/services/stripe_meter.py",
    "    if not customer:\n"
    '        return {"reported": False, "reason": "no subscription yet",\n'
    '                "segments": segments}',
    "    if not customer:\n"
    "        customer = \"\"")],

 "A6e the backfill re-offers campaigns the ledger already records, so a "
 "dry run reads as work outstanding forever": [
   ("app/services/stripe_meter.py",
    "        if already_reported(db, campaign_id):\n"
    "            skipped += 1\n"
    "            continue",
    "        if False:\n"
    "            skipped += 1\n"
    "            continue")],

 "A6f the backfill is unbounded, so it re-meters the period the one-time "
 "balance already settled — on top of a charge he has paid": [
   ("app/services/stripe_meter.py",
    "    bound = since or subscription_start(db)\n"
    "    if bound is None:",
    "    bound = since or date(1970, 1, 1)\n"
    "    if False:")],

 # ── A7: the back-bill tool, and the arithmetic it must not re-invent ───────
 "A7a the back-bill tool does its own arithmetic instead of billing_service's, "
 "so the invoice and the dashboard can disagree": [
   ("app/services/stripe_meter.py",
    "    messages, segments = billing_service.compute_usage(db, start, end)\n"
    "    exact = billing_service.cost_for_segments(segments)",
    "    messages, segments = billing_service.compute_usage(db, start, end)\n"
    "    exact = Decimal(str(settings.BILLING_PRICE_PER_SEGMENT)) * max(\n"
    "        0, segments - settings.BILLING_SEGMENTS_INCLUDED + 1)")],

 "A7b the invoice amount is computed from the rounded float, so $344.10 "
 "becomes 34,409 cents": [
   ("tools/bill_period.py",
    '    return int((exact * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))',
    "    return int(float(exact) * 100)")],

 # ── Review round 2: the defects one fresh-context pass found ───────────────
 #
 # Every one of these was live in the first version of this session and none was
 # caught by the 38 mutations above — which is the argument for the review as
 # well as for the harness. They are here so the next edit cannot put them back.

 "R1 the meter prices a legacy row as 1 segment while /usage prices it by "
 "length — the same row, two answers, invoice against dashboard": [
   ("app/services/stripe_meter.py",
    "    return sum(int(segments) if segments\n"
    "               else billing_service.legacy_segment_count(message)\n"
    "               for segments, message in rows)",
    "    return sum(int(segments or 1) for segments, _ in rows)")],

 "R2 the legacy rule is restated in billing_service instead of shared, so the "
 "two can drift again": [
   ("app/services/billing_service.py",
    "        segments += (stored_segments if stored_segments\n"
    "                     else legacy_segment_count(message))",
    "        segments += stored_segments if stored_segments else 1")],

 "R3 an agreement nobody has re-checked is reported as current forever — a "
 "tier edited six months after checkout is never detected": [
   ("app/services/stripe_tiers.py",
    "        return _aged(stored)",
    "        return stored")],

 "R4 the daily tier check is not registered at all, so the only run is at "
 "checkout and a tier edited later is never detected": [
   ("app/main.py",
    "    scheduler.add_job(\n"
    "        billing_tiers.daily_tier_check,\n"
    "        CronTrigger(hour=6, minute=0),",
    "    scheduler.add_job(\n"
    "        monitoring_service.failure_digest_job,\n"
    "        CronTrigger(hour=6, minute=0),")],

 "R4b the tier check is registered under the failure digest's id, so "
 "replace_existing=True silently unregisters the digest": [
   ("app/main.py",
    '        id="daily_tier_check", replace_existing=True, max_instances=1,',
    '        id="daily_failure_digest", replace_existing=True, max_instances=1,')],

 "R5 /health publishes this account's allowance and rate to anyone who asks — "
 "no login, no rate limit, every scanner on the internet": [
   ("app/routers/pages.py",
    '        "pricing_issues": stripe_tiers.public_pricing_issues(pricing),',
    '        "pricing_issues": pricing.get("issues") or [],')],

 "R6 the backfill is bounded on when the campaign row was created rather than "
 "on when its segments were sent — a September send from an August draft is "
 "outside the window forever": [
   ("app/services/stripe_meter.py",
    "    query = (db.query(SMSMessage.campaign_id)\n"
    "             .filter(SMSMessage.campaign_id.isnot(None),\n"
    "                     SMSMessage.status.in_(message_model.BILLABLE_STATUSES),\n"
    "                     SMSMessage.sent_at >= bound.isoformat())\n"
    "             .distinct()\n"
    "             .order_by(SMSMessage.campaign_id))",
    "    from app.models.campaign import Campaign\n"
    "    query = (db.query(Campaign.id)\n"
    "             .filter(Campaign.created_at >= bound.isoformat())\n"
    "             .order_by(Campaign.id))")],

 "R7 the back-bill tool stops saying when its window is not a billing cycle, "
 "so half a month gets its own free allowance and the invoice looks right": [
   ("tools/bill_period.py",
    "    if (start, end) == (cycle_start, cycle_end):\n"
    '        return ""',
    "    if True:\n"
    '        return ""')],

 "R8 an unknown tier state reaches the field /health publishes, because the "
 "check is an assert and assertions are stripped under -O": [
   ("app/services/stripe_tiers.py",
    "    if state not in TIER_STATES:\n"
    '        raise ValueError(f"unknown tier-check state {state!r}")',
    "    if False:\n"
    '        raise ValueError(f"unknown tier-check state {state!r}")')],

 # ── A9: safe to deploy before Stripe exists ────────────────────────────────
 "A9a an unconfigured box tries to open a Checkout Session anyway, so the "
 "page fails with an SDK error instead of a sentence": [
   ("app/routers/billing.py",
    "    if not stripe_billing.configured():\n"
    "        return JSONResponse({\"error\": stripe_billing.NOT_CONNECTED}, status_code=503)",
    "    if False:\n"
    "        return JSONResponse({\"error\": stripe_billing.NOT_CONNECTED}, status_code=503)")],

 "A9b a checkout failure hands the SDK's own text to the client, request ids "
 "and another client's price id included": [
   ("app/routers/billing.py",
    '        return JSONResponse({"error": stripe_billing.CHECKOUT_FAILED}, status_code=502)',
    '        return JSONResponse({"error": str(exc)}, status_code=502)')],
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

# The second precondition, and it was added the hard way. Splitting a module on
# the 500-line rule moved seven mutations to a new file and a path-rewrite bled
# across one mutation's boundary — which this script reports honestly, but only
# after spending a full pytest run on every mutation ahead of it, and only one
# stale anchor per run. Resolving all of them up front costs milliseconds and
# turns "the code moved and this file was not updated with it" from a slow drip
# into one line.
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
    # It usually means the code moved and this file was not updated with it.
    print("\nmutations that could not be applied (fix the harness, not the code):")
    for name in unapplied:
        print(f"   -> {name}")
if survivors:
    print("\nreverted guard(s) that no test noticed:")
    for name in survivors:
        print(f"   -> {name}")
sys.exit(1 if (survivors or unapplied) else 0)
