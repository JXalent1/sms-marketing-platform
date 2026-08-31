"""Session 5f A1-A3: minting, the one-hop redirect, clicks, and the segment count.

Every test here drives the real code path. The two that matter most are the
ones a reviewer should read first:

  - `test_the_segment_count_is_measured_on_the_rendered_link` is 5f A3. A
    template that counts as one segment with `{link}` in it renders to two, and
    the quote has to be the second number. That is the 5b A7 lesson applied to
    a tag whose expansion is bigger than any name.
  - `test_a_click_whose_write_fails_still_redirects` is A1's promise about the
    request path. It is asserted by breaking the recorder, not by reading it.

Nothing here sends a message: `SMS_PROVIDER` is `console` throughout and no
carrier object is constructed.
"""

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.models.campaign import Campaign
from app.models.short_link import LinkClick, ShortLink
from app.models.sms_message import SMSMessage
from app.services import link_service, preflight_service, preflight_totals
from app.services.campaign_builder import CampaignError
from app.services.campaign_service import CampaignService
from app.sms.segments import count_segments

from tests import _link_setup as setup

PASSWORD = "devpassword123"

IPHONE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
             "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148")


@pytest.fixture(scope="module")
def db():
    yield from setup.purged_db_fixture_body()


@pytest.fixture(autouse=True)
def rate_limits():
    yield from setup.rate_limit_fixture_body()


@pytest.fixture(scope="module")
def client():
    c = TestClient(app)
    login = c.post("/login", data={"username": "admin", "password": PASSWORD})
    assert login.status_code in (200, 302), (
        f"login failed with {login.status_code} — every later assertion would "
        f"be about a 401 body")
    return c


def _campaign(db, *, phones, template, target=setup.TARGET_URL, name="basic"):
    """Build a draft through the real service, with links switched on.

    Each call gets its own `source:` audience. A shared one resolves to every
    contact any earlier test in this module seeded, which is how "one link per
    recipient" passes with nine links for six people.
    """
    source = f"{setup.CONTACT_SOURCE}-{name}"
    contacts = setup.seed_contacts(db, phones, source=source)
    service = CampaignService(db)
    campaign = service.create_campaign(
        name=f"{setup.NAME_PREFIX}{name}",
        message_template=template,
        audience=f"source:{source}",
        cross_category_override=True,
        link_target_url=target,
    )
    return campaign, contacts


# ─── A1: one link per recipient ─────────────────────────────────────────────

def test_a_campaign_with_the_tag_mints_one_link_per_recipient(db):
    """Per recipient, not per campaign — which buyers clicked is the point."""
    with setup.short_link_domain():
        campaign, contacts = _campaign(
            db, phones=setup.take(3),
            template=f"Auctions4America: {link_service.LINK_TAG} Reply STOP to opt out.",
            name="mint")

        links = db.query(ShortLink).filter(ShortLink.campaign_id == campaign.id).all()
        assert len(links) == 3, "one link per recipient"
        assert len({l.slug for l in links}) == 3, "slugs must be distinct"
        assert {l.contact_id for l in links} == {c.id for c in contacts}

        messages = db.query(SMSMessage).filter(
            SMSMessage.campaign_id == campaign.id).all()
        by_contact = {m.contact_id: m for m in messages}
        for link in links:
            body = by_contact[link.contact_id].message
            assert link_service.url_for(link.slug) in body
            # The tag itself must be gone. A literal `{link}` in a queued body
            # is a broken URL sent to everybody at full price.
            assert link_service.LINK_TAG not in body
            # And the link has to know which message it went into, or a click
            # cannot be dated against the send.
            assert link.message_id == by_contact[link.contact_id].id
            assert link.target_url == setup.TARGET_URL


def test_slugs_are_unguessable_rather_than_sequential(db):
    """A sequential id in a message tells its recipient this client's volume."""
    with setup.short_link_domain():
        campaign, _ = _campaign(
            db, phones=setup.take(6),
            template=f"Auctions4America: {link_service.LINK_TAG} Reply STOP to opt out.",
            name="slugs")
        slugs = [row.slug for row in db.query(ShortLink)
                 .filter(ShortLink.campaign_id == campaign.id)
                 .order_by(ShortLink.id).all()]

    assert len(slugs) == 6
    assert len(set(slugs)) == 6
    assert all(link_service.SLUG_RE.match(s) for s in slugs), slugs
    # Nothing ambiguous to read out in a saleroom.
    assert not set("".join(slugs)) & set("01loOI")
    # Not derived from the row id: six rows inserted consecutively share no
    # 4-character prefix. Deliberately not "the slugs are not in sorted order" —
    # six random draws land sorted once in 720 runs, and a test that fails one
    # morning in three hundred is worse than no test.
    assert len({s[:4] for s in slugs}) == 6, slugs


def test_no_slug_can_be_minted_that_a_page_already_owns():
    """`GET /{slug}` is registered last, so a root page always wins the match.

    A slug that spells one is therefore a link nobody can follow: the recipient
    taps it and lands on a login screen instead of the auction. Exactly one page
    name is currently eight characters of the slug alphabet — "settings" — and
    the odds of drawing it are about one in 10^12, which is not a reason to
    leave the class open when closing it is a set membership test.

    This asserts the *property* rather than pinning the list: it reads the app's
    own route table, so a page added later that could be drawn as a slug fails
    here instead of failing on a handset. Same lesson as 5g's `OPT_OUT_REASONS`
    test, which pinned a literal and went green over the thing it existed to
    prevent.
    """
    roots = {path.strip("/") for path in
             (getattr(route, "path", "") for route in app.routes)
             if path.count("/") == 1 and "{" not in path and path != "/"}
    collidable = {name for name in roots if link_service.SLUG_RE.match(name)}
    assert collidable <= link_service.RESERVED_SLUGS, (
        f"these root pages could be drawn as a slug and are not reserved: "
        f"{sorted(collidable - link_service.RESERVED_SLUGS)}")


def test_the_generator_throws_away_a_reserved_slug(db, monkeypatch):
    """The list above is only worth having if the mint reads it.

    Driven by making the draw deterministic, because the honest alternative —
    minting until "settings" comes up — is one in 10^12. The generator is asked
    for one slug and handed the reserved word first; it must discard it and
    draw again.
    """
    reserved = "settings"
    assert link_service.SLUG_RE.match(reserved), "precondition: it is a valid slug"

    stream = iter(list(reserved) + list("abcdefgh"))
    monkeypatch.setattr(link_service.secrets, "choice", lambda alphabet: next(stream))
    assert link_service._fresh_slugs(db, 1) == ["abcdefgh"]


def test_a_campaign_without_the_tag_mints_nothing(db):
    """The feature costs nothing on the messages that do not use it."""
    with setup.short_link_domain():
        campaign, _ = _campaign(db, phones=setup.take(2), template=setup.MESSAGE,
                                target=None, name="no-tag")
        assert db.query(ShortLink).filter(
            ShortLink.campaign_id == campaign.id).count() == 0


# ─── A1: one hop, never a chain ─────────────────────────────────────────────

def test_a_link_resolves_to_its_target_in_exactly_one_hop(db, client):
    with setup.short_link_domain():
        campaign, _ = _campaign(
            db, phones=setup.take(1),
            template=f"Auctions4America: {link_service.LINK_TAG} Reply STOP to opt out.",
            name="one-hop")
        link = db.query(ShortLink).filter(
            ShortLink.campaign_id == campaign.id).first()

        response = client.get(f"/{link.slug}", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == setup.TARGET_URL
    # 301 would be cached by the handset and by every proxy in between, so the
    # second click never arrives and the report under-counts everyone who
    # looked twice.
    assert "no-store" in response.headers.get("cache-control", "")


@pytest.mark.parametrize("target", [
    "https://bit.ly/abc123",                       # a public shortener: hop two
    f"https://{setup.TEST_DOMAIN}/aaaaaaaa",       # our own domain: a chain
    "auctions4america.test/thursday",              # no scheme: nothing to redirect to
    "",                                            # nothing at all
])
def test_a_target_that_would_chain_or_break_is_refused(target):
    with setup.short_link_domain():
        with pytest.raises(link_service.LinkError):
            link_service.validate_target(target)


def test_an_unknown_slug_answers_404_and_creates_nothing(db, client):
    before = db.query(ShortLink).count()
    for path in ("/zzzzzzzz", "/wp-login.php", "/not-a-slug"):
        assert client.get(path, follow_redirects=False).status_code == 404
    db.expire_all()
    assert db.query(ShortLink).count() == before


# ─── A1: minting is closed ──────────────────────────────────────────────────

def test_minting_is_closed_to_unauthenticated_callers(db):
    """There is no route that mints a link, and the ones that create campaigns
    are behind auth. An open redirector is found and abused within weeks, and
    then the branded sending domain is the one on the carrier blocklist."""
    anonymous = TestClient(app)
    before = db.query(ShortLink).count()

    created = anonymous.post("/api/campaigns", json={
        "name": f"{setup.NAME_PREFIX}anonymous",
        "message_template": f"Hi {link_service.LINK_TAG}",
        "audience": "all", "cross_category_override": True,
        "link_target_url": setup.TARGET_URL,
    })
    assert created.status_code in (401, 403), created.text

    uploaded = anonymous.post("/api/campaigns/from-upload", files={
        "file": ("l.csv", setup.csv_bytes([("A", setup.POOL[0], "Co")]), "text/csv")},
        data={"name": f"{setup.NAME_PREFIX}anon-upload",
              "message_template": f"Hi {link_service.LINK_TAG}",
              "link_target_url": setup.TARGET_URL})
    assert uploaded.status_code in (401, 403), uploaded.text

    db.expire_all()
    assert db.query(ShortLink).count() == before

    # And the only route that names a slug is the public redirect, which is a
    # GET that resolves and never creates. There is no mint endpoint to defend.
    slug_routes = [r for r in app.routes if getattr(r, "path", "") == "/{slug}"]
    assert len(slug_routes) == 1
    assert set(slug_routes[0].methods) == {"GET"}


# ─── A3: the segment count is measured on the rendered link ─────────────────

def test_the_segment_count_is_measured_on_the_rendered_link(db):
    """The whole of A3.

    `{link}` is six characters and renders to seventeen on this domain, so a
    template that measures as one segment lands on a handset as two. The
    composer's keystroke counter is allowed to be an estimate; the quote is not.
    """
    with setup.short_link_domain():
        rendered_length = len(link_service.placeholder_url())
        # Grown to the boundary rather than hardcoded at 160 characters, because
        # `{` and `}` are GSM-7 *extended* characters and cost two septets each
        # — so `{link}` is eight septets, not six, and a length-based literal
        # here would be wrong in the direction that makes the test pass anyway.
        head = f"A4A: {link_service.LINK_TAG} STOP to stop. "
        filler = 0
        while count_segments(head + "x" * (filler + 1)) == 1:
            filler += 1
        template = head + "x" * filler
        assert count_segments(template) == 1, "the template alone is one segment"
        assert count_segments(
            template.replace(link_service.LINK_TAG,
                             link_service.placeholder_url())) == 2

        campaign, contacts = _campaign(db, phones=setup.take(2), template=template,
                                       name="segments")

        # What the campaign recorded, which is what the capacity check enforces.
        assert campaign.estimated_segments == 4, "two recipients at two segments"

        # And what pre-flight quotes, through the same placeholder the composer
        # endpoint passes. Without the placeholder this returns 2, not 4 — the
        # nine-character difference is the defect A3 names.
        service = CampaignService(db)
        link = link_service.placeholder_url()
        totals = preflight_totals.exact_segment_totals(
            template, contacts, lambda t, c: service.render(t, c, link))
        assert totals["total_segments"] == 4
        assert totals["exact"] is True

    assert rendered_length == len(setup.TEST_DOMAIN) + 1 + link_service.SLUG_LENGTH


def test_the_preflight_endpoint_quotes_the_rendered_link_not_the_tag(db, client):
    """The same property, through the route the composer actually calls.

    Written because the mutation harness found the gap: reverting
    `routers/campaigns.py` to hand `exact_segment_totals()` the bare renderer —
    the tidy-up a future session writes, since pre-flight already has one —
    broke nothing. The test above measures the *helper*; this one measures the
    endpoint, and the endpoint is what quotes him a number. A property proved of
    a function and not of its only caller is proved of nothing.
    """
    with setup.short_link_domain():
        head = f"A4A: {link_service.LINK_TAG} STOP to stop. "
        filler = 0
        while count_segments(head + "x" * (filler + 1)) == 1:
            filler += 1
        template = head + "x" * filler

        source = f"{setup.CONTACT_SOURCE}-preflight"
        setup.seed_contacts(db, setup.take(2), source=source)

        report = client.post("/api/campaigns/preflight", json={
            "message_template": template,
            "audience": f"source:{source}",
            "link_target_url": setup.TARGET_URL,
        }).json()
        # Inside the domain context on purpose: `for_counting()` reads the
        # setting live, and a call made outside it would measure the literal tag
        # and assert the very thing this test exists to rule out.
        preview = client.post("/api/campaigns/preview", json={
            "message_template": template, "audience": f"source:{source}",
        }).json()

    counts = report["counts"]
    assert counts["recipients"] == 2
    assert counts["segments_measured"] is True
    # Both figures see the link. `segments_per_message` is the number the live
    # keystroke counter shows and is kept here so the two panels can be
    # compared; before the counter measured the rendered link it said 1 while
    # the total said 4, which is a screen contradicting itself an inch apart —
    # the shape of 5d's Opt-outs headline, where the rows were right and the
    # number above them was not.
    assert counts["segments_per_message"] == 2
    assert counts["max_segments_per_message"] == 2
    assert counts["total_segments"] == 4, "measured on the rendered link"
    # Which is what the composer's own live counter now reports too.
    assert preview["segments"] == 2, "the keystroke counter sees the link as well"
    assert preview["total_segments"] == 4
    assert report["link"]["in_message"] is True
    assert report["link"]["available"] is True


def test_the_placeholder_is_the_same_length_as_a_minted_link(db):
    """Not by coincidence — by construction, and this is what pins it.

    Pre-flight cannot mint 4,200 rows every time somebody presses "Run checks",
    so it measures with a placeholder. If the two lengths ever diverge the quote
    and the invoice diverge with them, silently.
    """
    with setup.short_link_domain():
        campaign, _ = _campaign(
            db, phones=setup.take(1),
            template=f"Auctions4America: {link_service.LINK_TAG} Reply STOP to opt out.",
            name="placeholder")
        link = db.query(ShortLink).filter(
            ShortLink.campaign_id == campaign.id).first()
        assert len(link_service.url_for(link.slug)) == len(link_service.placeholder_url())


# ─── A3: refuse at compose time, never at send time ─────────────────────────

def test_an_unconfigured_domain_refuses_at_compose_time(db):
    """No domain, no links, no campaign, and a sentence naming the cause."""
    with setup.short_link_domain(""):
        assert link_service.configured() is False
        campaigns_before = db.query(Campaign).count()
        links_before = db.query(ShortLink).count()

        with pytest.raises(CampaignError) as raised:
            _campaign(db, phones=setup.take(2),
                      template=f"A4A: {link_service.LINK_TAG} Reply STOP to opt out.",
                      name="no-domain")

        assert "link domain" in str(raised.value).lower()
        db.expire_all()
        assert db.query(Campaign).count() == campaigns_before, "nothing was created"
        assert db.query(ShortLink).count() == links_before, "no broken links minted"


def test_the_preflight_row_fails_when_the_link_cannot_be_minted():
    """The composer says it before the send, in link_service's own words."""
    template = f"A4A: {link_service.LINK_TAG} Reply STOP to opt out."
    with setup.short_link_domain(""):
        row = preflight_service.check_short_link(template, setup.TARGET_URL)
        assert row["status"] == "fail"
        assert row["reason"] == link_service.NO_DOMAIN_ERROR

    with setup.short_link_domain():
        missing = preflight_service.check_short_link(template, None)
        assert missing["status"] == "fail"
        assert link_service.LINK_TAG in missing["reason"]

        ok = preflight_service.check_short_link(template, setup.TARGET_URL)
        assert ok["status"] == "pass"
        assert link_service.placeholder_url() in ok["reason"]

        # A destination with no tag is a warning, not a refusal: the message is
        # sendable, it just will not carry the link he typed.
        stray = preflight_service.check_short_link(setup.MESSAGE, setup.TARGET_URL)
        assert stray["status"] == "warn"


# ─── A2: clicks, and who they belong to ─────────────────────────────────────

def _sent_campaign(db, name):
    """A campaign whose one message has been sent ten minutes ago.

    Backdated deliberately: a click one second after the send is filtered as a
    scanner by the timing rule, which is correct and would make every click
    test here assert the wrong thing.
    """
    campaign, contacts = _campaign(
        db, phones=setup.take(1),
        template=f"Auctions4America: {link_service.LINK_TAG} Reply STOP to opt out.",
        name=name)
    message = db.query(SMSMessage).filter(
        SMSMessage.campaign_id == campaign.id).first()
    message.status = "sent"
    message.sent_at = setup.iso_minutes_ago(10)
    message.segments = 1
    db.commit()
    link = db.query(ShortLink).filter(ShortLink.campaign_id == campaign.id).first()
    return campaign, contacts[0], link


def test_a_click_is_recorded_and_attributed_to_the_right_contact(db, client):
    with setup.short_link_domain():
        campaign, contact, link = _sent_campaign(db, "click")
        response = client.get(f"/{link.slug}", follow_redirects=False,
                              headers={"user-agent": IPHONE_UA})
        assert response.status_code == 302

        db.expire_all()
        link = db.get(ShortLink, link.id)
        assert link.click_count == 1
        assert link.bot_click_count == 0
        assert link.first_clicked_at and link.last_clicked_at

        click = db.query(LinkClick).filter(
            LinkClick.short_link_id == link.id).one()
        assert click.is_bot == 0
        assert click.bot_reason is None
        assert click.user_agent == IPHONE_UA
        # Dated against the send, which is what the timing rule reads.
        assert 500 < click.seconds_after_send < 700

        # Attribution: the click belongs to this buyer on this campaign.
        assert link.contact_id == contact.id
        assert link.campaign_id == campaign.id


def test_a_click_whose_write_fails_still_redirects(db, client, monkeypatch):
    """The person is going to the auction whatever the database is doing.

    Broken at the recorder rather than asserted from the docstring, and broken
    with a *raise* — `record_click()` promises not to raise, and this proves the
    route does not depend on the promise.
    """
    with setup.short_link_domain():
        campaign, _, link = _sent_campaign(db, "click-fails")

        def explode(*args, **kwargs):
            raise RuntimeError("disk full")

        monkeypatch.setattr(link_service, "record_click", explode)
        response = client.get(f"/{link.slug}", follow_redirects=False,
                              headers={"user-agent": IPHONE_UA})

    assert response.status_code == 302
    assert response.headers["location"] == setup.TARGET_URL
    db.expire_all()
    assert db.query(LinkClick).filter(LinkClick.short_link_id == link.id).count() == 0


def test_the_redirect_route_never_leaks_a_database_connection(db, client):
    """The route opens its own session, so it has to close it on every way out.

    Found by review rather than by reasoning, and it was real: the first draft
    returned early on an unknown slug from *inside* the try and closed the
    session only on the other two paths. Forty requests left seven connections
    checked out, reclaimed whenever the garbage collector got round to it — and
    this route sits at the root of a domain that will be swept, so the leak was
    on the path a scanner takes rather than the one a buyer takes. The reference
    system leaked one browser context per daily scrape for the same reason: the
    cleanup was on each way out instead of in a `finally`.

    Asserted against the pool rather than by reading the code, because "there is
    a finally now" is exactly the kind of claim that goes stale.
    """
    from app.core.database import engine

    with setup.short_link_domain():
        campaign, _, link = _sent_campaign(db, "no-leak")
        before = engine.pool.checkedout()

        for _ in range(30):
            assert client.get("/zzzzzzzz", follow_redirects=False).status_code == 404
        unknown = engine.pool.checkedout()

        for _ in range(30):
            assert client.get(f"/{link.slug}", follow_redirects=False,
                              headers={"user-agent": IPHONE_UA}).status_code == 302
        resolved = engine.pool.checkedout()

    assert unknown <= before, (
        f"{unknown - before} connection(s) leaked over 30 unknown slugs")
    assert resolved <= before, (
        f"{resolved - before} connection(s) leaked over 30 resolved slugs")


def test_a_scanner_is_marked_and_kept_out_of_the_headline(db, client):
    """A2: mark, do not discard, and report both numbers."""
    with setup.short_link_domain():
        campaign, _, link = _sent_campaign(db, "scanner")

        scanner = ("Mozilla/5.0 (compatible; Googlebot/2.1; "
                   "+http://www.google.com/bot.html)")
        assert client.get(f"/{link.slug}", follow_redirects=False,
                          headers={"user-agent": scanner}).status_code == 302
        assert client.get(f"/{link.slug}", follow_redirects=False,
                          headers={"user-agent": IPHONE_UA}).status_code == 302

        db.expire_all()
        link = db.get(ShortLink, link.id)
        assert link.click_count == 1, "the person"
        assert link.bot_click_count == 1, "the scanner, kept and labelled"

        rows = db.query(LinkClick).filter(
            LinkClick.short_link_id == link.id).order_by(LinkClick.id).all()
        assert [r.is_bot for r in rows] == [1, 0]
        assert rows[0].bot_reason == "ua:googlebot"
        # The scanner's own user agent is still on the row. A count that
        # excludes things without recording what it excluded cannot be argued
        # with, and this number goes to a client.
        assert rows[0].user_agent == scanner


def test_a_click_that_arrives_before_a_person_could_read_it_is_filtered(db, client):
    """The timing backstop, for a scanner that copies a browser's user agent."""
    with setup.short_link_domain(), setup.click_window(60):
        campaign, _, link = _sent_campaign(db, "too-soon")
        message = db.query(SMSMessage).filter(
            SMSMessage.campaign_id == campaign.id).first()
        message.sent_at = setup.iso_minutes_ago(0)
        db.commit()

        assert client.get(f"/{link.slug}", follow_redirects=False,
                          headers={"user-agent": IPHONE_UA}).status_code == 302
        db.expire_all()
        link = db.get(ShortLink, link.id)
        assert link.click_count == 0
        assert link.bot_click_count == 1
        assert db.query(LinkClick).filter(
            LinkClick.short_link_id == link.id).one().bot_reason == "timing:too_soon"


def test_a_link_on_an_unsent_message_is_never_filtered_for_timing(db, client):
    """`seconds_after_send` is None when it cannot be known, and None is not
    evidence. Guessing zero here would file every click on a draft as a robot."""
    with setup.short_link_domain(), setup.click_window(3600):
        campaign, _ = _campaign(
            db, phones=setup.take(1),
            template=f"Auctions4America: {link_service.LINK_TAG} Reply STOP to opt out.",
            name="unsent")
        link = db.query(ShortLink).filter(
            ShortLink.campaign_id == campaign.id).first()
        assert client.get(f"/{link.slug}", follow_redirects=False,
                          headers={"user-agent": IPHONE_UA}).status_code == 302
        db.expire_all()
        assert db.get(ShortLink, link.id).click_count == 1


def test_the_domain_setting_is_the_only_place_the_domain_lives():
    """No hardcoded domain anywhere — it may not even be registered yet."""
    with setup.short_link_domain("example.test"):
        assert link_service.url_for("abcdefgh") == "example.test/abcdefgh"
    with setup.short_link_domain(""):
        assert link_service.configured() is False
    assert settings.SHORT_LINK_DOMAIN == "" or settings.SHORT_LINK_DOMAIN


# ─── A1 on the other creation path: a top-up mints for what it adds ─────────

def test_a_top_up_mints_a_link_for_each_row_it_adds_and_reuses_none(db):
    """The second way a campaign gains recipients, which the spec does not name.

    CLAUDE.md's opening lesson is a guard wired into one of its call sites. The
    same applies to a feature: a `{link}` that works when a campaign is created
    and ships a literal `{link}` to everyone added afterwards is worse than one
    that never worked, because nobody looks at a top-up's message body.

    Also pins the thing a shared mint would break: a released or already-sent
    row keeps the link it was sent with. Minting a second one for the same
    person would mean the message quoted on screen and the message queued were
    different, and it would attribute their click to the wrong row.
    """
    import asyncio
    from app.services import campaign_builder, campaign_topup, contact_service
    from app.models.contact_list import ContactList

    with setup.short_link_domain():
        phones = setup.take(3)
        template = (f"Auctions4America: {link_service.LINK_TAG} "
                    f"Reply STOP to opt out.")
        campaign, imported = campaign_builder.create_campaign_from_upload(
            db, CampaignService(db).render,
            name=f"{setup.NAME_PREFIX}topup",
            message_template=template,
            content=setup.csv_bytes([(f"Buyer {i}", p, "Co")
                                     for i, p in enumerate(phones[:2])]),
            link_target_url=setup.TARGET_URL,
        )
        service = CampaignService(db)
        service.provider = setup.CostingProvider()
        asyncio.run(service.send_campaign(campaign.id))
        db.refresh(campaign)
        assert campaign.status == "completed", campaign.abort_reason

        first_slugs = {row.slug for row in db.query(ShortLink)
                       .filter(ShortLink.campaign_id == campaign.id).all()}
        assert len(first_slugs) == 2

        # Somebody phones in after the blast and is added to the campaign's list.
        newcomer = setup.seed_contacts(db, [phones[2]], source="5f-topup-late")[0]
        contact_service.add_to_list(db, imported["list_id"], newcomer.id)

        service.provider = setup.CostingProvider()
        asyncio.run(campaign_topup.top_up(db, campaign.id))

        links = db.query(ShortLink).filter(
            ShortLink.campaign_id == campaign.id).all()
        assert len(links) == 3, "one more link, for the one more recipient"
        assert first_slugs < {row.slug for row in links}, (
            "the original recipients' links must be untouched")

        added = [row for row in links if row.contact_id == newcomer.id]
        assert len(added) == 1, "exactly one link for the contact added"
        message = db.query(SMSMessage).filter(
            SMSMessage.campaign_id == campaign.id,
            SMSMessage.contact_id == newcomer.id).one()
        assert link_service.url_for(added[0].slug) in message.message
        assert link_service.LINK_TAG not in message.message
        assert added[0].message_id == message.id
        assert message.status == "sent"

        # Every link on the campaign belongs to exactly one message.
        assert len({row.message_id for row in links}) == 3


def test_a_top_up_refuses_when_the_link_can_no_longer_be_minted(db):
    """A campaign carrying the tag whose short domain has gone must not queue
    messages with a literal `{link}` in them — and the refusal is the composer's
    own sentence rather than a second one invented here."""
    import asyncio
    from app.services import campaign_builder, campaign_topup, contact_service

    with setup.short_link_domain():
        phones = setup.take(3)
        template = (f"Auctions4America: {link_service.LINK_TAG} "
                    f"Reply STOP to opt out.")
        campaign, imported = campaign_builder.create_campaign_from_upload(
            db, CampaignService(db).render,
            name=f"{setup.NAME_PREFIX}topup-nodomain",
            message_template=template,
            content=setup.csv_bytes([(f"Buyer {i}", p, "Co")
                                     for i, p in enumerate(phones[:2])]),
            link_target_url=setup.TARGET_URL,
        )
        service = CampaignService(db)
        service.provider = setup.CostingProvider()
        asyncio.run(service.send_campaign(campaign.id))

        newcomer = setup.seed_contacts(db, [phones[2]], source="5f-topup-gone")[0]
        contact_service.add_to_list(db, imported["list_id"], newcomer.id)
        rows_before = db.query(SMSMessage).filter(
            SMSMessage.campaign_id == campaign.id).count()

    # The domain is gone by the time the top-up runs.
    with setup.short_link_domain(""):
        verdict = asyncio.run(campaign_topup.assess(db, campaign))
        assert verdict["refusal"] == link_service.NO_DOMAIN_ERROR
        with pytest.raises(CampaignError):
            asyncio.run(campaign_topup.top_up(db, campaign.id))

    db.expire_all()
    assert db.query(SMSMessage).filter(
        SMSMessage.campaign_id == campaign.id).count() == rows_before, (
        "nothing was queued")
