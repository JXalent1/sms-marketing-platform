"""Revert one session-5f fix at a time and check that the suite notices.

Run by `agent/accept-5f.sh` as check 10. "The new modules are red against the
pre-5f tree" is true and nearly meaningless — they fail there at *import*,
because `ShortLink` and `link_service` did not exist — so this reverts each fix
**behaviourally**, inside the current API, one at a time, in a scratch copy of
the tree, and requires at least one test to go red for each.

Every mutation is a plausible edit rather than a wrecking ball, which is what
makes a green run mean something:

  - `R3` is the one a future session writes while tidying: pre-flight already
    has a renderer, so why pass it a link? Because `{link}` is six characters
    and renders to seventeen, and the difference is the whole of A3.
  - `R5` is "a permanent redirect is more correct", which is true of a permalink
    and wrong of a click counter: the handset caches it and the second click
    never arrives.
  - `R9` is the obvious simplification of the bot matcher — drop the word
    boundaries and just look for "bot" — which files a CUBOT handset as a
    crawler and deletes a real buyer from the client's headline.
  - `R13` is the tidy-up that turns "the carrier did not price this" into
    "$0.00", which makes an unpriced campaign look free.

Nothing here sends. It edits a scratch copy of the tree, never the repo, and
restores every file between mutations.

    python3 agent/mutate-5f.py <scratch-tree> <python>
"""
import pathlib, re, subprocess, sys

SCRATCH = pathlib.Path(sys.argv[1])
PY = sys.argv[2]
TARGETS = [
    "tests/test_short_links.py",
    "tests/test_click_filtering.py",
    "tests/test_campaign_reports.py",
    "tests/test_campaign_preflight.py",
    "tests/test_short_link_host.py",
]

MUTATIONS = {
 # ── A1: one link per recipient, minted at creation ─────────────────────────
 "R1 the builder mints no links — the tag stays literal in every message": [
   ("app/services/campaign_builder.py",
    "    if not target or not link_service.has_link_tag(campaign.message_template):\n"
    "        return {}",
    "    if True:\n        return {}")],

 "R2 one link for the whole campaign instead of one per recipient": [
   ("app/services/campaign_builder.py",
    "    minted = link_service.mint(db, campaign_id=campaign.id, target_url=target,\n"
    "                               contacts=contacts)",
    "    minted = link_service.mint(db, campaign_id=campaign.id, target_url=target,\n"
    "                               contacts=contacts[:1])")],

 "R2b the link never learns which message it went into": [
   ("app/services/campaign_builder.py",
    "        for link, message in queued:\n"
    "            if link is not None:\n"
    "                link.message_id = message.id",
    "        for link, message in queued:\n"
    "            pass")],

 # ── A3: the segment count is measured on the rendered link ─────────────────
 "R3 pre-flight measures the tag rather than the rendered link": [
   ("app/routers/campaigns.py",
    "    totals = preflight_service.exact_segment_totals(\n"
    "        payload.message_template, split[\"sendable\"],\n"
    "        lambda template, contact: service.render(template, contact, link),\n"
    "    )",
    "    totals = preflight_service.exact_segment_totals(\n"
    "        payload.message_template, split[\"sendable\"], service.render,\n"
    "    )")],

 "R4 the placeholder is not the length of a real link": [
   ("app/services/link_service.py",
    '    return url_for(SLUG_ALPHABET[0] * SLUG_LENGTH)',
    '    return url_for(SLUG_ALPHABET[0])')],

 "R3b the keystroke counter measures the tag, contradicting the phone preview": [
   ("app/services/link_service.py",
    "    if not has_link_tag(template) or not configured():\n"
    "        return template or \"\"\n"
    "    return template.replace(LINK_TAG, placeholder_url())",
    "    return template or \"\"")],

 "R4b the renderer ignores the link it was given": [
   ("app/services/message_render.py",
    '    if link_url is not None:',
    '    if False:')],

 # ── A3: refuse at compose time, never at send time ─────────────────────────
 "R5 an unconfigured domain is accepted and the tag ships literally": [
   ("app/services/campaign_builder.py",
    "        if not link_service.configured():\n"
    "            raise CampaignError(link_service.NO_DOMAIN_ERROR)",
    "        if False:\n"
    "            raise CampaignError(link_service.NO_DOMAIN_ERROR)")],

 "R5b the pre-flight row never fails on a missing destination": [
   ("app/services/preflight_service.py",
    "    try:\n"
    "        link_service.validate_target(link_target_url)\n"
    "    except link_service.LinkError as e:\n"
    "        return _check(\"short_link\", \"Link\", FAIL, str(e))",
    "    try:\n"
    "        link_service.validate_target(link_target_url)\n"
    "    except link_service.LinkError as e:\n"
    "        return _check(\"short_link\", \"Link\", WARN, str(e))")],

 # ── A1: one hop, never a chain ─────────────────────────────────────────────
 "R6 a target on our own short domain is allowed — the message chains": [
   ("app/services/link_service.py",
    "    if configured() and (host == domain() or host.endswith(\".\" + domain())):",
    "    if False:")],

 "R7 a public shortener is allowed as a target — hop two, and a filtered domain": [
   ("app/services/link_service.py",
    "    if any(host == d or host.endswith(\".\" + d) for d in SHARED_SHORTENER_DOMAINS):",
    "    if False:")],

 "R8 the redirect is a permanent one, so the second click never arrives": [
   ("app/routers/links.py",
    "        url=target, status_code=302,",
    "        url=target, status_code=301,")],

 # ── A1: recording a click must never break the redirect ────────────────────
 "R9 a failed click write takes the redirect down with it": [
   ("app/routers/links.py",
    "            try:\n"
    "                link_service.record_click(db, link,\n"
    "                                          request.headers.get(\"user-agent\"))\n"
    "            except Exception as e:               # noqa: BLE001\n"
    "                logger.error(\"click not recorded for %r: %s\", slug, e)",
    "            link_service.record_click(db, link,\n"
    "                                      request.headers.get(\"user-agent\"))")],

 # The leak this route shipped with, reproduced faithfully: close the session on
 # the way out of each branch instead of in a `finally`, and let the unresolved
 # path return before reaching any of them.
 #
 # The first version of this mutation only reinstated the early `return` and was
 # NOT caught — correctly, because with one `finally` covering the handler an
 # early return still closes the session. A mutation with no reachable defect
 # behind it is worth saying out loud rather than dressing up as a green tick
 # (5h dropped its R15 for the same reason); this one was rewritten to bite
 # instead, because the leak it describes is real and there is a test for it.
 "R9b the session is closed per branch instead of in a finally, and the "
 "unresolved path returns before any of them": [
   ("app/routers/links.py",
    "        if link is not None:\n"
    "            target = link.target_url",
    "        if link is None:\n"
    "            return PlainTextResponse(UNKNOWN_LINK, status_code=404)\n"
    "        if link is not None:\n"
    "            target = link.target_url"),
   ("app/routers/links.py",
    "    finally:\n"
    "        db.close()\n"
    "\n"
    "    if target is None:",
    "    db.close()\n"
    "\n"
    "    if target is None:")],

 # ── A2: bots are marked, not counted, and not discarded ────────────────────
 "R10 the bot matcher drops its word boundaries — a CUBOT handset is a crawler": [
   ("app/services/click_classifier.py",
    "GENERIC_AGENT_WORDS = re.compile(\n"
    "    r\"(?<![\\w-])(?:bot|bots|crawler|crawl|spider|scraper|scanner|preview|monitor|\"\n"
    "    r\"validator|fetcher|archiver)(?![\\w-])\",\n"
    "    re.IGNORECASE,\n"
    ")",
    "GENERIC_AGENT_WORDS = re.compile(\n"
    "    r\"(?:bot|bots|crawler|crawl|spider|scraper|scanner|preview|monitor|\"\n"
    "    r\"validator|fetcher|archiver)\",\n"
    "    re.IGNORECASE,\n"
    ")")],

 "R11 the timing rule never runs, so a scanner in a browser's clothes counts": [
   ("app/services/click_classifier.py",
    "    if (threshold > 0 and seconds_after_send is not None\n"
    "            and 0 <= seconds_after_send < threshold):",
    "    if False:")],

 "R11b a click that predates its own send is filed as a robot": [
   ("app/services/click_classifier.py",
    "            and 0 <= seconds_after_send < threshold):",
    "            and seconds_after_send < threshold):")],

 "R12 a scanner's click is counted in the headline like anyone else's": [
   ("app/services/link_service.py",
    "        if is_bot:\n"
    "            link.bot_click_count = (link.bot_click_count or 0) + 1\n"
    "        else:",
    "        if False:\n"
    "            link.bot_click_count = (link.bot_click_count or 0) + 1\n"
    "        else:")],

 "R12b the report leads with total clicks rather than human ones": [
   ("app/services/report_service.py",
    "    return {\"clicks\": int(clicks or 0), \"filtered_clicks\": int(bots or 0),",
    "    return {\"clicks\": int(clicks or 0) + int(bots or 0),\n"
    "            \"filtered_clicks\": int(bots or 0),")],

 # ── A4: what the carrier actually charged ──────────────────────────────────
 "R13 the send loop drops the carrier's cost again": [
   ("app/services/campaign_service.py",
    "                    cost_reconciliation.record(msg, result)",
    "                    pass")],

 "R13b the rate/carrier-fee split is not kept, only the total": [
   ("app/services/cost_reconciliation.py",
    "    message.carrier_cost_rate = result.cost_rate\n"
    "    message.carrier_cost_fee = result.cost_carrier_fee",
    "    message.carrier_cost_rate = None\n"
    "    message.carrier_cost_fee = None")],

 "R14 an unpriced message is counted as costing nothing": [
   ("app/services/cost_reconciliation.py",
    "    if raw is None or str(raw).strip() == \"\":\n"
    "        return None",
    "    if raw is None or str(raw).strip() == \"\":\n"
    "        return Decimal(\"0\")")],

 # ── A5/A6: counted in the database, not in the page ────────────────────────
 "R15 the message page fetches click data one row at a time": [
   ("app/services/history_service.py",
    "    clicks = link_service.clicks_for_messages(db, [m.id for m in messages])",
    "    clicks = {}\n"
    "    for m in messages:\n"
    "        clicks.update(link_service.clicks_for_messages(db, [m.id]))")],

 "R15b campaign history counts each campaign's outcome with its own query": [
   ("app/services/history_service.py",
    "    outcomes = _outcomes_for(db, ids)",
    "    outcomes = {}\n"
    "    for one in ids:\n"
    "        outcomes.update(_outcomes_for(db, [one]))")],

 "R16 the campaign report prices a send at the flat rate, ignoring the allowance": [
   ("app/services/report_service.py",
    "    added = (billing_service.cost_for_segments(cycle_segments)\n"
    "             - billing_service.cost_for_segments(before))",
    "    from decimal import Decimal as _D\n"
    "    added = _D(str(own_segments)) * _D(str(settings.BILLING_PRICE_PER_SEGMENT))")],

 "R17 the report paraphrases the abort reason instead of repeating it": [
   ("app/services/report_service.py",
    '            "abort_reason": campaign.abort_reason,',
    '            "abort_reason": ("This campaign was stopped before it sent."\n'
    '                             if campaign.abort_reason else None),')],

 # ── A1 on the other creation path: the top-up ──────────────────────────────
 "R20 a top-up mints nothing, so everyone added afterwards gets a literal tag": [
   ("app/services/campaign_topup.py",
    "    if link_target and (sendable or suppressed):",
    "    if False:")],

 "R20b a top-up whose link cannot be minted queues the message anyway": [
   ("app/services/campaign_topup.py",
    "    except CampaignError as e:\n"
    "        return {**empty, \"refusal\": str(e), \"code\": 400}",
    "    except CampaignError:\n"
    "        link_target = None")],

 "R21 the mint stops avoiding a slug a page already owns": [
   ("app/services/link_service.py",
    "            if candidate not in RESERVED_SLUGS:\n"
    "                chosen.add(candidate)",
    "            chosen.add(candidate)")],

 # ── The host guard: the short domain serves short links and nothing else ───
 "R22 the host guard is gone — every admin route answers on the short domain": [
   ("app/main.py",
    "    if (link_service.is_short_link_host(request.headers.get(\"host\"))\n"
    "            and not link_service.is_slug_path(request.url.path)):\n"
    "        return PlainTextResponse(links.UNKNOWN_LINK, status_code=404)",
    "    if False:\n"
    "        return PlainTextResponse(links.UNKNOWN_LINK, status_code=404)")],

 "R22b the guard forgets the reserved words, so /settings serves its page": [
   ("app/services/link_service.py",
    "    return bool(SLUG_RE.match(candidate)) and candidate not in RESERVED_SLUGS",
    "    return bool(SLUG_RE.match(candidate))")],

 "R22c the guard blocks the slug route on the primary host too, breaking "
 "every link already in somebody's phone": [
   ("app/main.py",
    "    if (link_service.is_short_link_host(request.headers.get(\"host\"))\n"
    "            and not link_service.is_slug_path(request.url.path)):",
    "    if not link_service.is_slug_path(request.url.path):")],

 "R22d the host comparison is case- and port-sensitive": [
   ("app/services/link_service.py",
    "    return normalize_host(raw_host) == normalize_host(configured_domain)",
    "    return (raw_host or \"\") == configured_domain")],

 "R22f the guard stays on when the short domain IS the admin host, 404ing "
 "every page of the product": [
   ("app/services/link_service.py",
    "    if not configured_domain or short_domain_conflicts():",
    "    if not configured_domain:")],

 "R22e the guard reads a client-supplied forwarding header instead of Host": [
   ("app/main.py",
    "    if (link_service.is_short_link_host(request.headers.get(\"host\"))",
    "    if (link_service.is_short_link_host(\n"
    "            request.headers.get(\"x-forwarded-host\")\n"
    "            or request.headers.get(\"host\"))")],

 # ── Criterion 8: nothing on a client surface is ours ───────────────────────
 "R18 the message history stops scrubbing the carrier's own error text": [
   ("app/services/history_service.py",
    '        "error_message": scrub_provider_text(m.error_message),',
    '        "error_message": m.error_message,')],

 "R19 the report hands the client our carrier cost": [
   ("app/services/report_service.py",
    '        "cost": campaign_cost(db, campaign, billed_segments),',
    '        "cost": {**campaign_cost(db, campaign, billed_segments),\n'
    '                 "wholesale_cost": str(settings.WHOLESALE_COST_PER_SEGMENT)},')],
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
