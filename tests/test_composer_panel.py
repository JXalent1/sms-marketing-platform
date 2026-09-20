"""The composer's summary panel, run the way the browser runs it.

Session 5m: the panel described three audiences at once — the Audience row named
the pinned all-bidders entry, Recipients and Opted out were the whole database's,
and Segments and Estimated cost were a 443-contact list's. `paintAudienceSummary()`
*was* wired to the select's `change` event, so nothing a Python test could read
off the template would have found it. Three mechanisms, none visible in the
source's shape:

  * assigning `select.value` fires no `change` event, so the row was never
    repainted after the upload flow moved the dropdown;
  * nothing sequenced the `/preview` replies, and the reply about the audience
    he had left behind was twelve times slower and landed last;
  * `runPreflight()` wrote two of the six rows.

So these tests run the real partials in node (`tests/js/`), against response
bodies produced **by the real endpoints in this process**. A hand-written fixture
would be a second copy of the API drifting away from it, and the property under
test is exactly that the panel repeats what the API said about the audience the
send will use.

`test_the_harness_reproduces_the_defect_it_was_written_for` is here for
CLAUDE.md's reason: a green check whose harness is broken is worse than no check.
It reverts the guard inside the scenario's own copy of the partials and requires
the stale reply to win.
"""

import json
import os
import pathlib
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.database import SessionLocal
from app.models.blocked_number import BlockedNumber
from app.models.contact import Contact
from app.models.contact_list import ContactList, ContactListMember
from tests import _guardrail_setup as guardrail_setup

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCENARIOS = ROOT / "tests" / "js" / "composer_scenarios.mjs"

MESSAGE = "Private record collection, tonight 6pm. Doors 5."
LIST_LABEL = "09/09, 6:00 PM Private Record Collection"
# One emoji, and a length chosen so the two ways of counting disagree: 79
# characters is one segment by "length over 160" and **two** by the UCS-2 rule
# `count_sms_segments()` applies (70 per single segment, 67 per part after
# that). The figure on screen can only be the server's.
EMOJI_MESSAGE = ("\U0001F525 Private record collection tonight 6pm, doors 5. "
                 "Rare pressings, sealed boxes.")
SCHEDULED_NAME = "Panel rail scheduled draft"
UNSCHEDULED_NAME = "Panel rail draft sent by hand"

# The measured ordering, exaggerated so a race is a fact rather than a coin toss.
# At the production shape /preview for the whole database took 237.9 ms against
# 20.3 ms for the 443-contact list; the client's box is slower still and the gap
# with it. What the scenarios need is only that the reply about the audience he
# left behind is the last one to land.
LATENCY = {"audiences": 20, "preflight": 60, "preview": {"all": 600}}


def _client():
    client = TestClient(app)
    client.post("/login", data={"username": "admin", "password": "devpassword123"},
                follow_redirects=False)
    return client


def _seed():
    """A small database of the production shape: a list inside a bigger whole."""
    db = SessionLocal()
    try:
        existing = db.query(ContactList).filter(ContactList.name == LIST_LABEL).first()
        if existing:
            return existing.id
        contacts = []
        for i in range(40):
            contacts.append(Contact(phone=f"+1954777{i:04d}", full_name=f"Panel Bidder {i}",
                                    is_active=1, source="csv"))
        db.add_all(contacts)
        # Opted out, and deliberately mostly *outside* the list: the whole
        # database's opted-out figure and the list's have to differ, or the
        # assertion that the panel is not quoting the wrong audience's number
        # passes for a reason that has nothing to do with the guard.
        for i in [0] + list(range(20, 26)):
            db.add(BlockedNumber(phone=f"+1954777{i:04d}", reason="stop_keyword",
                                 source="webhook", blocked_at="2026-08-01T10:00:00"))
        listed = ContactList(name=LIST_LABEL, created_at="2026-09-08T09:00:00", archived=0)
        db.add(listed)
        db.commit()
        # Members 10-21, deliberately not starting at contact 0: the sample
        # contact the phone preview renders against has to differ between this
        # list and the whole database, or the test that the preview is not
        # repainted from a superseded reply passes without the gate.
        for contact in contacts[10:22]:
            db.add(ContactListMember(list_id=listed.id, contact_id=contact.id,
                                     added_at="2026-09-08T09:00:00"))
        db.commit()
        return listed.id
    finally:
        db.close()


def _add_member(list_id: int) -> None:
    """One more contact on the list, so /preflight and /preview disagree."""
    db = SessionLocal()
    try:
        taken = {phone for (phone,) in db.query(Contact.phone)}
        i = 0
        while f"+1954778{i:04d}" in taken:
            i += 1
        contact = Contact(phone=f"+1954778{i:04d}", full_name="Late Arrival",
                          is_active=1, source="csv")
        db.add(contact)
        db.commit()
        db.add(ContactListMember(list_id=list_id, contact_id=contact.id,
                                 added_at="2026-09-08T10:00:00"))
        db.commit()
    finally:
        db.close()


# Every row this module writes carries one of these prefixes, and every one of
# them is removed again when the module finishes. The suite is deliberately one
# shared end-to-end story — `tests/conftest.py` says so — so a module that seeds
# forty contacts and seven blocklist rows and leaves them there makes its
# neighbours prove something other than what they claim: `test_smoke`'s
# blocklist arithmetic and `test_dashboard`'s never-texted list both went red
# the first time this ran. The mirror of 5i's lesson, one file along.
PHONE_PREFIXES = ("+1954777", "+1954778")
CAMPAIGN_NAMES = ("Panel and draft agree", SCHEDULED_NAME, UNSCHEDULED_NAME)


def _tear_down():
    from app.models.campaign import Campaign
    from app.models.sms_message import SMSMessage

    db = SessionLocal()
    try:
        phones = [p for (p,) in db.query(Contact.phone)
                  if p.startswith(PHONE_PREFIXES)]
        ids = [i for (i,) in db.query(Contact.id).filter(Contact.phone.in_(phones))]
        campaigns = [c for (c,) in db.query(Campaign.id)
                     .filter(Campaign.name.in_(CAMPAIGN_NAMES))]
        if campaigns:
            db.query(SMSMessage).filter(SMSMessage.campaign_id.in_(campaigns)).delete(
                synchronize_session=False)
            db.query(Campaign).filter(Campaign.id.in_(campaigns)).delete(
                synchronize_session=False)
        if ids:
            db.query(SMSMessage).filter(SMSMessage.contact_id.in_(ids)).delete(
                synchronize_session=False)
            db.query(ContactListMember).filter(
                ContactListMember.contact_id.in_(ids)).delete(synchronize_session=False)
        db.query(ContactList).filter(ContactList.name == LIST_LABEL).delete(
            synchronize_session=False)
        if phones:
            db.query(BlockedNumber).filter(BlockedNumber.phone.in_(phones)).delete(
                synchronize_session=False)
            db.query(Contact).filter(Contact.phone.in_(phones)).delete(
                synchronize_session=False)
        db.commit()
    finally:
        db.close()


@pytest.fixture(scope="module")
def fixtures(tmp_path_factory):
    """Real endpoint responses, written where the node scenarios can read them."""
    if shutil.which("node") is None:                        # pragma: no cover
        pytest.fail("node is required: the composer's panel is JavaScript, and "
                    "the only honest proof runs it. `npm` is already a build "
                    "dependency of this project (npm run build:css).")

    list_id = _seed()
    selector = f"list:{list_id}"
    client = _client()

    audiences = client.get("/api/campaigns/audiences").json()
    preview_all = client.post("/api/campaigns/preview",
                              json={"message_template": MESSAGE, "audience": "all"}).json()
    preview_list = client.post("/api/campaigns/preview",
                               json={"message_template": MESSAGE, "audience": selector}).json()

    # The list grows between the preview and the checks — an import in another
    # tab, or a top-up. The two responses now genuinely disagree about how many
    # people this send reaches, which is what makes "one writer" testable.
    _add_member(list_id)
    preflight_list = client.post("/api/campaigns/preflight",
                                 json={"message_template": MESSAGE, "audience": selector,
                                       "batch_size": None, "link_target_url": None}).json()

    # The upload tab's question (5n): the message alone, no audience. One reply
    # per message the scenarios can type, and the plan refuses any other text.
    template_previews = {
        text: client.post("/api/campaigns/preview",
                          json={"message_template": text, "audience": None}).json()
        for text in ("", MESSAGE, EMOJI_MESSAGE)
    }

    # The rail: a scheduled draft the JavaScript has to offer Cancel on, beside
    # the drafts it must not. Both the list and the cancel reply are the real
    # endpoints' — the rail renders what `/api/campaigns` says and the toast
    # repeats what `/cancel` says, so neither may be hand-written here.
    scheduled = client.post("/api/campaigns", json={
        "name": SCHEDULED_NAME, "message_template": MESSAGE, "audience": selector,
        "cross_category_override": True, "scheduled_at": "2030-01-01T18:00:00",
    })
    assert scheduled.status_code == 200, scheduled.text
    scheduled_id = scheduled.json()["campaign"]["id"]
    # And a draft with no time on it, in the same rail: the control must be
    # absent there, and a fixture without one could not say so.
    by_hand = client.post("/api/campaigns", json={
        "name": UNSCHEDULED_NAME, "message_template": MESSAGE, "audience": selector,
        "cross_category_override": True,
    })
    assert by_hand.status_code == 200, by_hand.text
    rail = client.get("/api/campaigns?limit=8").json()
    cancelled = client.post(f"/api/campaigns/{scheduled_id}/cancel").json()

    payload = {
        "list_selector": selector,
        "list_label": LIST_LABEL,
        "message": MESSAGE,
        "emoji_message": EMOJI_MESSAGE,
        "latency": {**LATENCY, "preview": {**LATENCY["preview"], selector: 40}},
        "audiences": audiences,
        "preview": {"all": preview_all, selector: preview_list},
        "template_previews": template_previews,
        "preflight": {selector: preflight_list},
        "rail": rail,
        "scheduled_campaign_id": scheduled_id,
        "cancel_replies": {str(scheduled_id): cancelled},
        "from_upload": {"success": True,
                        "campaign": {"id": 901, "name": LIST_LABEL, "scheduled_at": None,
                                     "total_recipients": preview_list["recipients"],
                                     "estimated_segments": preview_list["total_segments"]},
                        "import": {"list_id": list_id, "list_name": LIST_LABEL}},
        "created": {"success": True,
                    "campaign": {"id": 902, "name": LIST_LABEL, "scheduled_at": None,
                                 "total_recipients": preview_list["recipients"],
                                 "estimated_segments": preview_list["total_segments"]}},
    }
    path = tmp_path_factory.mktemp("composer") / "fixtures.json"
    path.write_text(json.dumps(payload))
    try:
        yield payload, path
    finally:
        _tear_down()


def run_scenario(name: str, path, templates=None) -> dict:
    env = dict(os.environ)
    if templates is not None:
        env["COMPOSER_TEMPLATES"] = str(templates)
    result = subprocess.run(["node", str(SCENARIOS), name, "--fixtures", str(path)],
                            capture_output=True, text=True, cwd=ROOT, env=env, timeout=120)
    assert result.returncode == 0, f"{name} failed:\n{result.stderr}"
    return json.loads(result.stdout)


# ─── The panel ──────────────────────────────────────────────────────────────

def test_a_stale_reply_cannot_paint_over_a_newer_one(fixtures):
    """The reply about the audience he left behind lands last and is discarded."""
    payload, path = fixtures
    listed = payload["preview"][payload["list_selector"]]
    panel = run_scenario("stale_reply_loses", path)

    assert panel["selector"] == payload["list_selector"]
    assert panel["audience"] == listed["audience_label"]
    assert panel["recipients"] == f'{listed["recipients"]:,}'
    assert panel["opted_out"] == f'{listed["opted_out"]:,}'
    assert panel["segments"] == f'{listed["total_segments"]:,}'
    # And specifically not the whole database's figures, which arrived later.
    everyone = payload["preview"]["all"]
    assert panel["recipients"] != f'{everyone["recipients"]:,}'
    assert panel["opted_out"] != f'{everyone["opted_out"]:,}'


def test_a_stale_reply_does_not_repaint_the_phone_preview(fixtures):
    """The gate is on the handler, so it covers the rows the panel does not own.

    `refreshPreview()` paints the character count, the phone preview and "rendered
    for <name>" as well as the summary. Those come from the same reply and are
    wrong in the same way — a preview rendered against a contact who is not in
    the audience he is about to send to.
    """
    payload, path = fixtures
    listed = payload["preview"][payload["list_selector"]]
    everyone = payload["preview"]["all"]
    assert listed["sample_name"] != everyone["sample_name"], (
        "the two audiences must have different sample contacts, or this test "
        "passes without the gate")

    panel = run_scenario("stale_reply_does_not_repaint_the_phone_preview", path)
    assert panel["sample"] == f'Rendered for {listed["sample_name"]}.'


def test_every_row_of_the_panel_comes_from_one_response(fixtures):
    """The campaign-first flow: one audience, named and counted by one reply.

    The flow ends by running the checks, so the newest answer is the pre-flight
    report — and *every* row has to be that report's, including the two the
    preview would otherwise still own. The fixture's two responses disagree
    about the recipient count on purpose, so "the panel matches the newest
    response" is a claim that can fail.
    """
    payload, path = fixtures
    selector = payload["list_selector"]
    listed = payload["preview"][selector]
    report = payload["preflight"][selector]
    assert report["counts"]["recipients"] != listed["recipients"]

    panel = run_scenario("upload_names_its_list", path)
    assert panel["audience"] == report["audience_label"] == LIST_LABEL
    assert panel["recipients"] == f'{report["counts"]["recipients"]:,}'
    assert panel["segments"] == f'{report["counts"]["total_segments"]:,}'
    assert panel["opted_out"] == f'{report["counts"]["opted_out"]:,}'
    assert panel["selector"] == selector
    # Not a mixture: the preview's recipient count is the one that used to
    # survive underneath the pre-flight's segment total.
    assert panel["recipients"] != f'{listed["recipients"]:,}'


def test_preflight_moves_every_row_or_none(fixtures):
    """Run checks is a whole answer about one audience, not two rows."""
    payload, path = fixtures
    listed = payload["preview"][payload["list_selector"]]
    report = payload["preflight"][payload["list_selector"]]
    # The fixture is built so the two disagree; without that this proves nothing.
    assert report["counts"]["recipients"] != listed["recipients"]

    out = run_scenario("preflight_repaints_the_whole_panel", path)
    assert out["before"]["recipients"] == f'{listed["recipients"]:,}'
    assert out["after"]["recipients"] == f'{report["counts"]["recipients"]:,}'
    assert out["after"]["segments"] == f'{report["counts"]["total_segments"]:,}'
    assert out["after"]["audience"] == report["audience_label"]


def test_the_panel_names_the_selector_the_send_will_use(fixtures):
    """The panel is a description of the send, never a second instruction."""
    payload, path = fixtures
    out = run_scenario("create_posts_the_selector", path)
    assert out["posted"]["audience"] == payload["list_selector"]
    assert out["panel"]["selector"] == payload["list_selector"]
    assert out["panel"]["audience"] == \
        payload["preview"][payload["list_selector"]]["audience_label"]


def test_the_panel_says_nothing_while_it_waits(fixtures):
    """The window between picking an audience and hearing about it.

    The ordering guard decides which reply wins; it cannot make one arrive. For
    300 ms of debounce plus a request — 237.9 ms for the whole database at this
    client's shape, more on his — the panel would otherwise show the audience he
    moved *off*, every row agreeing with every other, beside a dropdown naming a
    different one and a Create button that sends to that one. That is the
    dangerous direction of the same defect, and it is the direction a consistent
    panel hides better than an inconsistent one did.
    """
    payload, path = fixtures
    everyone = payload["preview"]["all"]
    out = run_scenario("panel_while_it_waits", path)

    assert out["waiting"]["audience"] == "—"
    assert out["waiting"]["recipients"] == "…", (
        "zeros would be a different claim; nothing may be asserted here")
    assert out["waiting"]["segments"] == "…"
    assert out["waiting"]["cost"] == "…"
    # Specifically not the audience it was showing a moment ago.
    assert out["waiting"]["recipients"] != f'{everyone["recipients"]:,}'
    # And it fills in from the response, as before.
    listed = payload["preview"][payload["list_selector"]]
    assert out["settled"]["recipients"] == f'{listed["recipients"]:,}'
    assert out["settled"]["audience"] == listed["audience_label"]


def test_a_superseded_preflight_leaves_a_checklist_you_can_re_run(fixtures):
    """Not a spinner that never resolves.

    `runPreflight()` writes "Running checks…" before the request. Discarding a
    superseded report is right; leaving that sentence up forever is decision
    006's defect — a refusal that explains nothing reads as a broken tool, on
    the one screen whose whole job is to stop a bad send.
    """
    _, path = fixtures
    out = run_scenario("preflight_superseded_midway", path)
    assert "Running checks" not in out["checklist"]
    assert "Run checks" in out["checklist"] and "changed" in out["checklist"]


def test_switching_to_upload_retires_the_replies_still_in_flight(fixtures):
    """A cleared panel stays cleared; the slow reply must not refill it.

    Cleared to "—", not to 0, since 5n: under the upload tab there is no
    audience to count, and "0 recipients" is a claim about one.
    """
    payload, path = fixtures
    everyone = payload["preview"]["all"]
    out = run_scenario("upload_mode_clears_and_stays_clear", path)
    # Inside the window: the abandoned reply has landed and the re-ask has not
    # answered. Only `resetSummary()`'s ticket keeps it off the panel here.
    for when in ("inside", "settled"):
        panel = out[when]
        assert panel["audience"] == "—", when
        assert panel["recipients"] == "—", when
        assert panel["segments"] == "—", when
        assert panel["cost"] == "—", when
        assert panel["recipients"] != f'{everyone["recipients"]:,}', when


# ─── 5n A1: the message's own figures, under the upload tab ────────────────

def test_upload_mode_measures_the_template_and_admits_what_it_does_not_know(fixtures):
    """Three rows are true of the message alone; three need an audience.

    Characters, Encoding and Segments/msg are live with no audience anywhere.
    Recipients, Total segments and Estimated cost read "—": not 0, which is a
    claim about an audience that does not exist yet. And the request that
    filled the row asked about **no** audience — not the dropdown's, which
    still holds whatever the other tab was pointing at.
    """
    payload, path = fixtures
    measured = payload["template_previews"][EMOJI_MESSAGE]
    out = run_scenario("upload_mode_measures_the_template", path)
    panel = out["panel"]

    assert out["mode"] == "upload"
    assert panel["characters"] == str(measured["characters"])
    assert panel["encoding"] == measured["encoding"] == "UCS-2"
    assert panel["segments_per_message"] == str(measured["segments"])
    assert panel["strip_recipients"] == "—", "an unknown is not a zero"
    assert panel["strip_total"] == "—"
    assert panel["strip_cost"] == "—"
    # "This send" says the same thing about the same unknowns.
    assert panel["audience"] == "—" and panel["recipients"] == "—"
    assert panel["segments"] == "—" and panel["cost"] == "—"
    # Every preview the upload tab asked for asked about nobody.
    assert out["audiences_asked"], "no preview was requested in upload mode"
    assert set(out["audiences_asked"]) == {None}


def test_the_segment_figure_on_screen_is_count_sms_segments_via_the_server(fixtures):
    """Criterion 4: no second segment calculation exists.

    The figure the upload tab shows is asserted against three things at once:
    the fixture reply it was painted from, `count_sms_segments()` called
    directly on the same text the endpoint counts, and the number a browser-side
    `Math.ceil(length / 160)` would have produced instead. On the emoji message
    those last two differ — 2 against 1 — so a JavaScript that measured the
    message itself cannot pass this.
    """
    from app.services import link_service
    from app.sms.segments import count_segments

    payload, path = fixtures
    measured = payload["template_previews"][EMOJI_MESSAGE]
    authority = count_segments(link_service.for_counting(EMOJI_MESSAGE))
    naive = -(-len(EMOJI_MESSAGE) // 160)
    assert authority != naive, "the fixture message must separate the two counters"
    assert measured["segments"] == authority

    panel = run_scenario("upload_mode_measures_the_template", path)["panel"]
    assert panel["segments_per_message"] == str(authority)
    assert panel["segments_per_message"] != str(naive)


def test_the_unicode_warning_fires_under_the_upload_tab(fixtures):
    """Criterion 3. One emoji triples the cost, and the upload flow is the primary one.

    With no audience the warning names the per-recipient fact — segments per
    message, before and after — and makes no claim about a recipient count or a
    dollar figure, because it has neither.
    """
    payload, path = fixtures
    measured = payload["template_previews"][EMOJI_MESSAGE]
    warning = run_scenario("upload_mode_measures_the_template", path)["panel"]["unicode_warning"]

    assert warning["shown"] is True
    assert "switches this message to Unicode" in warning["html"]
    assert (f'{measured["segments"]} segments instead of '
            f'{measured["gsm7_segments_if_stripped"]}') in warning["html"]
    assert "recipients" not in warning["html"], "no audience, so no recipient count"
    assert "$" not in warning["html"], "no audience, so no dollar figure"


def test_carrying_a_message_to_the_upload_tab_asks_about_it_again(fixtures):
    """The switch retires the reply in flight, so it has to ask again.

    Typed on the existing tab, switched before the reply landed: the reset
    discards that reply on purpose. Without a fresh request the counter would
    keep describing the previous keystroke — the plain message — under a box
    that now carries an emoji, with the warning silent.
    """
    payload, path = fixtures
    measured = payload["template_previews"][EMOJI_MESSAGE]
    panel = run_scenario("upload_switch_reasks_about_the_message", path)
    assert panel["encoding"] == "UCS-2"
    assert panel["segments_per_message"] == str(measured["segments"])
    assert panel["unicode_warning"]["shown"] is True
    assert panel["strip_recipients"] == "—"


# ─── 5n A2: the rail's Cancel control ───────────────────────────────────────

def test_the_rail_offers_cancel_on_a_scheduled_draft_and_posts_to_its_route(fixtures):
    """Criterion 5, the client-side half: the control exists where he sees
    "Scheduled …", on that campaign and on no other, and pressing it posts to
    that campaign's own cancel route. The toast repeats the server's sentence.
    """
    payload, path = fixtures
    scheduled_id = payload["scheduled_campaign_id"]
    rail_rows = payload["rail"]["campaigns"]
    assert any(c["id"] == scheduled_id and c["scheduled_at"] for c in rail_rows)
    others = [c["id"] for c in rail_rows if c["id"] != scheduled_id]
    assert any(c["status"] == "draft" and not c["scheduled_at"] for c in rail_rows), (
        "the rail must also hold a draft with no time, or 'Cancel on every "
        "draft' passes this test")

    out = run_scenario("rail_offers_cancel_on_a_scheduled_draft", path)
    assert f"cancelCampaign({scheduled_id})" in out["rail"]
    for other in others:
        assert f"cancelCampaign({other})" not in out["rail"], other
    assert out["posted"] == [f"/api/campaigns/{scheduled_id}/cancel"]
    assert out["toasts"][-1]["type"] == "success"
    assert out["toasts"][-1]["message"] == payload["cancel_replies"][str(scheduled_id)]["message"]


# ─── The harness, checked against a case whose answer is known ──────────────

def test_the_harness_reproduces_the_defect_it_was_written_for(fixtures, tmp_path):
    """Revert the guard in a scratch copy and the stale reply must win.

    Without this the four tests above are a green light wired to nothing: a
    stub `<select>` that never actually raced, or a scenario whose replies all
    happened to land in order, would pass every one of them. Sixth measurement
    script in this project to be wrong before the code was — so the harness is
    made to fail on purpose, first.
    """
    payload, path = fixtures
    scratch = tmp_path / "templates"
    shutil.copytree(ROOT / "app" / "templates", scratch)

    script = scratch / "_composer-script.html"
    source = script.read_text()
    # One gate per response handler. The preview's is a one-liner; the
    # pre-flight's also restores the checklist, so it is stripped by its own
    # opening line. Both counts are asserted: if the shape changes, this check
    # is silently stripping something else and proves nothing.
    preview_gate = "if (panelStale(token)) { return; }"
    preflight_gate = "if (panelStale(token)) {"
    assert source.count(preview_gate) == 1
    assert source.count(preflight_gate) == 2      # the one-liner plus the block
    source = source.replace(preview_gate, "")
    source = source.replace(preflight_gate, "if (false) {")
    script.write_text(source)

    panel = run_scenario("stale_reply_loses", path, templates=scratch)
    everyone = payload["preview"]["all"]
    assert panel["selector"] == payload["list_selector"], "the dropdown still holds the list"
    assert panel["recipients"] == f'{everyone["recipients"]:,}', (
        "the harness cannot see a stale reply paint, so it proves nothing about "
        "the guard that stops one")


# ─── The endpoints the panel is painted from ────────────────────────────────
#
# The two properties above the JavaScript: the panel can only be right if the
# response it repeats is right, and it can only agree with the send if the
# endpoint and the create resolve the same selector the same way.

@pytest.fixture
def rate_limit_budget():
    yield from guardrail_setup.rate_limit_fixture_body()


def test_the_panel_and_the_draft_name_the_same_audience(fixtures, rate_limit_budget):
    """Asserted through the endpoints, not through `audience_label()`.

    The composer's Audience row is `/preview`'s `audience_label`; the campaign
    rail's is `campaigns.audience_label`, written by `create_campaign()`. Both
    read `contact_service.audience_label()` today, and the assertion that keeps
    them together has to go through the two HTTP surfaces — a test on the helper
    would pass with either one of them wired to something else.
    """
    payload, _ = fixtures
    selector = payload["list_selector"]
    client = _client()

    preview = client.post("/api/campaigns/preview",
                          json={"message_template": MESSAGE, "audience": selector}).json()
    report = client.post("/api/campaigns/preflight",
                         json={"message_template": MESSAGE, "audience": selector,
                               "batch_size": None, "link_target_url": None}).json()
    created = client.post("/api/campaigns", json={
        "name": "Panel and draft agree", "message_template": MESSAGE,
        "audience": selector, "cross_category_override": True,
    })
    assert created.status_code == 200, created.text
    draft = created.json()["campaign"]

    assert preview["audience"] == report["audience"] == draft["audience"] == selector
    assert preview["audience_label"] == report["audience_label"] == draft["audience_label"]
    assert draft["audience_label"] == LIST_LABEL
    # And the count the panel showed is the count the draft was built with.
    assert preview["recipients"] == draft["total_recipients"]


def test_total_segments_is_recipients_times_segments_per_message(fixtures):
    """The class of defect caught outright: two rows that cannot both be true.

    A one-segment message costs exactly one segment per recipient. If Recipients
    and Segments ever come from different audiences, this identity breaks — which
    is why it is asserted on both of the responses the panel is painted from.
    """
    payload, _ = fixtures
    selector = payload["list_selector"]
    client = _client()

    preview = client.post("/api/campaigns/preview",
                          json={"message_template": MESSAGE, "audience": selector}).json()
    assert preview["segments"] == 1, "the fixture message must be one segment"
    assert preview["total_segments"] == preview["recipients"] * preview["segments"]

    report = client.post("/api/campaigns/preflight",
                         json={"message_template": MESSAGE, "audience": selector,
                               "batch_size": None, "link_target_url": None}).json()
    counts = report["counts"]
    assert counts["total_segments"] == counts["recipients"] * counts["segments_per_message"]
    assert counts["recipients"] == preview["recipients"]
    assert counts["total_segments"] == preview["total_segments"]


def test_a_capped_send_is_quoted_at_the_cap_on_both_surfaces(fixtures):
    """"Send to the first 5" means five people, and both boxes have to say five.

    `create_campaign()` applies the cap, and the checklist has always passed it.
    The preview did not — the field was wired to re-run it and the value was
    never in the body — so the summary panel quoted the whole list while the
    checklist beside it quoted the cap.
    """
    payload, _ = fixtures
    selector = payload["list_selector"]
    client = _client()

    uncapped = client.post("/api/campaigns/preview",
                           json={"message_template": MESSAGE, "audience": selector}).json()
    capped = client.post("/api/campaigns/preview",
                         json={"message_template": MESSAGE, "audience": selector,
                               "batch_size": 5}).json()
    report = client.post("/api/campaigns/preflight",
                         json={"message_template": MESSAGE, "audience": selector,
                               "batch_size": 5, "link_target_url": None}).json()

    assert uncapped["recipients"] > 5, "the fixture list must be bigger than the cap"
    assert capped["recipients"] == 5
    assert capped["total_segments"] == 5
    assert report["counts"]["recipients"] == capped["recipients"]
    assert report["counts"]["total_segments"] == capped["total_segments"]


def test_an_empty_message_is_zero_segments_and_the_audience_is_still_named(fixtures):
    """The panel is fetched before a word is typed, so this answer is on screen.

    `describe("")` counts one segment, because it answers about text. Quoting
    that would price an empty composer at one segment per recipient — 10,146
    segments and $2.19 at this client's shape — on the screen whose whole job is
    to be trusted about money.
    """
    payload, _ = fixtures
    selector = payload["list_selector"]
    client = _client()

    blank = client.post("/api/campaigns/preview",
                        json={"message_template": "", "audience": selector}).json()
    assert blank["segments"] == 0
    assert blank["total_segments"] == 0
    assert blank["estimated_cost"] == 0
    # The audience half of the answer is unaffected: it is what fills the panel.
    assert blank["audience"] == selector
    assert blank["audience_label"] == LIST_LABEL
    assert blank["recipients"] > 0


def test_preview_without_an_audience_measures_the_message_and_admits_the_rest(fixtures):
    """5n A1 at the API: the message half answered, the audience half `None`.

    Not 0. `/preview` used to be asked only with an audience; the upload tab
    now asks with none, and an endpoint that resolved "no audience" into
    `recipients: 0` would hand the panel a claim to paint. Every figure that
    needs an audience is null, and every figure the message alone decides is
    the same one an audience-bearing preview reports.
    """
    from app.services import link_service
    from app.sms.segments import count_segments

    payload, _ = fixtures
    client = _client()
    alone = client.post("/api/campaigns/preview",
                        json={"message_template": EMOJI_MESSAGE, "audience": None}).json()
    with_audience = client.post("/api/campaigns/preview",
                                json={"message_template": EMOJI_MESSAGE,
                                      "audience": payload["list_selector"]}).json()

    for key in ("recipients", "suppressed", "opted_out", "total_segments",
                "estimated_cost", "estimated_cost_if_gsm7", "audience", "audience_label"):
        assert alone[key] is None, f"{key} should be unknown, not {alone[key]!r}"
    for key in ("characters", "encoding", "segments", "forced_unicode_by",
                "gsm7_segments_if_stripped", "risky_links", "link_in_message"):
        assert alone[key] == with_audience[key], key
    assert alone["segments"] == count_segments(link_service.for_counting(EMOJI_MESSAGE))
    assert alone["encoding"] == "UCS-2"
    # And the empty template is still zero segments, on this path too.
    blank = client.post("/api/campaigns/preview", json={"message_template": ""}).json()
    assert blank["segments"] == 0 and blank["recipients"] is None
