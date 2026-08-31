"""Session 5f A2: which clicks are people and which are the delivery path.

Unit tests on the matcher itself, because that is where the ambiguous-fragment
lesson bites. `AUTO_BLOCK_ERROR_FRAGMENTS` was word-anchored and narrow because
an over-match deletes a buyer; this list triggers a presentation choice with both
numbers on screen and nothing discarded, so it is allowed to be wider. What it is
*not* allowed to be is careless about a fragment that means two things — and
`bot` is one, because `CUBOT_X20` is an Android handset that appears in real
user agents.

That case is the reason the generic words are `\\b`-anchored and the named
crawlers are not: `Googlebot/2.1` has no boundary before its `bot` and never
will.
"""

import pytest

from app.services.click_classifier import NO_AGENT, classify

from tests import _link_setup as setup

# Real user agents, or close enough to matter. The handsets are the half that
# would be silently mis-filed if the matcher were sloppy, and every one of them
# is a buyer whose click would vanish from the client's headline.
HANDSETS = [
    ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
     "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"),
    # The one that matters: an unanchored "bot" files this handset as a crawler.
    ("Mozilla/5.0 (Linux; Android 10; CUBOT_X20) AppleWebKit/537.36 (KHTML, like "
     "Gecko) Chrome/91.0.4472.120 Mobile Safari/537.36"),
    ("Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 (KHTML, like "
     "Gecko) Chrome/124.0.0.0 Mobile Safari/537.36"),
    # "Robot" as part of an ordinary word is not a crawler either.
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) RobotoBrowser/3.1",
]

MACHINES = [
    ("Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
     "ua:googlebot"),
    ("facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
     "ua:facebookexternalhit"),
    ("Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)",
     "ua:bingbot"),
    ("curl/8.4.0", "ua:curl"),
    ("python-requests/2.31.0", "ua:python-requests"),
    ("Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 HeadlessChrome/120.0.0.0",
     "ua:headlesschrome"),
    # A security appliance that opens every link in a message before the
    # recipient's handset has finished ringing.
    ("Mozilla/5.0 (compatible; ProofPoint URL Defense)", "ua:proofpoint"),
    # A generic word, word-anchored.
    ("SomeCompany Link Crawler 1.0", "ua:crawler"),
    ("Mozilla/5.0 (compatible; SiteScanner)", None),
]


@pytest.mark.parametrize("agent", HANDSETS)
def test_a_real_handset_is_never_filtered(agent):
    is_bot, reason = classify(agent, seconds_after_send=3600)
    assert is_bot is False, f"{agent!r} was filed as automated because of {reason}"
    assert reason is None


@pytest.mark.parametrize("agent,expected", MACHINES)
def test_a_machine_is_filtered_and_says_which_rule_fired(agent, expected):
    is_bot, reason = classify(agent, seconds_after_send=3600)
    # `SiteScanner` is deliberately in the list with no expected reason: it is
    # the one entry that has no word boundary before "Scanner", so it documents
    # what the anchoring gives up rather than pretending it is caught.
    if expected is None:
        assert is_bot is False
        return
    assert is_bot is True, agent
    assert reason == expected


def test_a_click_with_no_user_agent_at_all_is_a_script():
    """Every browser sends one. Something that does not is not a person."""
    for value in (None, "", "   "):
        assert classify(value) == (True, NO_AGENT)


def test_the_timing_rule_only_runs_when_the_send_time_is_known():
    """None is not evidence.

    A link on a draft, or a message with no `sent_at`, has no send to be
    measured against. Treating that as zero seconds would file every click on an
    unsent message as a robot.
    """
    handset = HANDSETS[0]
    with setup.click_window(60):
        assert classify(handset, seconds_after_send=None) == (False, None)
        assert classify(handset, seconds_after_send=2) == (True, "timing:too_soon")
        assert classify(handset, seconds_after_send=61) == (False, None)


def test_the_timing_rule_can_be_switched_off_entirely():
    """A threshold is a judgement about how fast this client's audience is, so
    zero has to mean "do not run this rule" rather than "filter nothing ever"."""
    with setup.click_window(0):
        assert classify(HANDSETS[0], seconds_after_send=0) == (False, None)
        # The user-agent half still runs. Turning the timing rule off is not
        # turning the filter off.
        assert classify("curl/8.4.0", seconds_after_send=0)[0] is True


def test_a_clock_skew_in_the_wrong_direction_is_not_a_robot():
    """A click that appears to predate its own send is a clock, not a scanner.

    `sent_at` is local time and a click is stamped from the same clock, so this
    should not happen — but a negative interval reaching a `< threshold` test
    would silently file a real buyer as automated, and the row is deleted from
    the headline rather than from the table.
    """
    with setup.click_window(60):
        assert classify(HANDSETS[0], seconds_after_send=-30) == (False, None)
