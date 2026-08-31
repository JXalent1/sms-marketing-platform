"""What to say when a send run reached nobody.

Two campaigns reported `completed` with `sent_count = 0` and the campaign rail
drew them exactly like campaigns that worked. That is the same defect session 5d
removed one level down: the rail is the entire UI — there is no detail screen —
and it renders a status badge plus a reason *only when `abort_reason` is set*, so
a blast that reached nobody read as a success.

The rule is therefore about the run, not the lifetime: a run that put no message
on a carrier ends `aborted` with a reason naming the cause. A top-up run is the
one exception and it is handled at the call site, not here — a campaign that
already reached 1,200 people did complete, and a later top-up finding nothing new
must not retroactively relabel that. It gets the same sentence in
`abort_reason` and keeps its `completed` badge, which is why every sentence below
has to name its own cause rather than leaning on the badge next to it.

Everything here is a pure function over counts and, since decision 006, one
timestamp. No session, no campaign object, no writes — so the wording can be
asserted directly and the send loop keeps one job. The clearing time is computed
by the caller (`campaign_release.hold_clears_at()`, which has the session) and
passed in; only its *rendering* happens here, through the same
`clears_at_clock()` the composer's checklist row uses, so the sentence before
the send and the sentence after it describe the same moment the same way.

Counts come from the run's own rows, not from the campaign's lifetime counters,
because those already carry the original send's numbers.
"""

from datetime import datetime
from typing import List, Optional

from app.services.suppression_service import clears_at_clock

# The fact every reason carries. It is the sentence the client actually needs —
# the cause explains it, this is what happened. Past tense throughout: unlike the
# composer's pre-send refusals (see `send_path_assessment` in app/sms/factory.py),
# nothing reads this before the run.
#
# Trailing everywhere except the fully-held-back branch, where it leads. That
# sentence has a clearing time and a remedy the client acts on, and decision
# 006's own example states the fact once, at the front; wedging it back in
# between the two actionable clauses is how a sentence stops being read.
NOTHING_SENT = "Nothing was sent."


def zero_send_reason(*, queued: int, blocked: int = 0, region_skipped: int = 0,
                     failed: int = 0, suppressed: int = 0,
                     suppression_days: Optional[int] = None,
                     clears_at: Optional[str] = None) -> str:
    """Why this run reached nobody, in one sentence naming the cause.

    `queued` is how many messages the run actually had to work with — the rows
    that were `pending` when it started. `suppressed` is how many contacts were
    held back before that, which is the only cause that leaves `queued` at zero
    while the audience itself was not empty.

    The four causes are reported in the order that answers "what do I change?":
    an empty audience and a fully-suppressed one are different problems with
    different fixes, and a list that is entirely opted out is neither.

    `suppression_days` and `clears_at` are decision 006's requirement and only
    the suppressed branch reads them. They stay optional because the clearing
    time is genuinely unknowable on some inputs — a contact deleted between the
    build and the send, a timestamp that will not parse — and the sentence has
    to work without it. `run_send_loop()` always passes both;
    `test_the_abort_reason_names_the_window_and_when_it_clears` drives the real
    send path rather than this function, so a wiring that quietly stopped
    passing them fails there.
    """
    if queued == 0:
        if suppressed:
            # The remedy has to be one that works on *this* campaign, and neither
            # "lower the window" nor "wait" is — which is what an earlier wording
            # of this sentence recommended. Suppression is partitioned when the
            # draft is built and frozen into `held_back` rows (`skipped` before
            # 5h, which is the overloading decisions/005 removed), and the send
            # loop only ever reads `pending`; nothing moves a campaign back from
            # `aborted`. So a client who follows that advice changes a setting,
            # presses send again, and watches the same campaign fail the same
            # way. Naming the window is still worth doing — it is what caused
            # this — but the action is to build the campaign again.
            #
            # 5h's release does NOT change this branch, and decision 006 ruled
            # that it should not: a campaign the window held *entirely* sent
            # zero messages, so rebuilding it loses nothing — no delivery
            # record, no cost, no history — which is not true of 005's own
            # scenario, where one send had happened and rebuilding threw it
            # away. Option 3 would have made this the first thing in the
            # codebase to move a campaign out of `aborted`, against decision
            # 002's reasoning, to save a few clicks.
            #
            # **The defect here was the sentence, not the state**, and 006 makes
            # fixing it mandatory. "Stopped before it sent, create a new
            # campaign" is true and useless: it does not say why nothing sent or
            # when he could try again, so a working guard reads as a broken tool
            # — which is what happened to this client for two consecutive
            # campaigns before anybody ran SQL against it.
            return _all_held_back(suppressed, suppression_days, clears_at)
        return (
            f"This audience resolved to nobody, so the campaign had no recipients. "
            f"{NOTHING_SENT}"
        )

    if blocked == queued:
        return (
            f"Every one of the {queued:,} recipients is on your opt-out list. "
            f"{NOTHING_SENT}"
        )

    if region_skipped == queued:
        return (
            f"All {queued:,} numbers are outside the regions this account can send "
            f"to. {NOTHING_SENT}"
        )

    if failed == queued:
        return (
            f"All {queued:,} messages were rejected before delivery. {NOTHING_SENT}"
        )

    return (
        f"Nobody received this campaign. Of {queued:,} recipients: "
        f"{_breakdown(blocked=blocked, region_skipped=region_skipped, failed=failed, queued=queued)}. "
        f"{NOTHING_SENT}"
    )


def _all_held_back(suppressed: int, days: Optional[int],
                   clears_at: Optional[str]) -> str:
    """Decision 006's required sentence: the cause, the window, and when it lifts.

    Three clauses, and each earns its place:

      *the cause* — "all 2,140 contacts were texted in the last 3 days" says why
      nothing went out, in the client's own terms. The old wording said
      "recently", which is the word a support call starts with.

      *when it clears* — the actionable half. "2,140 held back" invites "held
      back until when?", and the answer decides whether he waits or re-cuts the
      audience. It is the same phrasing the composer's checklist row uses one
      screen earlier (`preflight_service.check_recent_overlap`) rendered through
      the same `clears_at_clock()`, because these are two sentences about one
      moment and they must not differ.

      *the remedy* — build the campaign again. Not "lower the window and press
      send", which an earlier wording recommended and which cannot work:
      suppression is frozen into `held_back` rows at build time, the send loop
      reads only `pending`, and nothing moves a campaign back from `aborted`.
      A client following that advice watches the same campaign fail the same way.

    **The hold may already have lifted by the time this is written, and then the
    remedy is different.** The held rows are frozen when the draft is built and
    the run can happen days later — a draft saved on Monday and sent on Friday,
    or a scheduled send — so the campaign still has nothing to send while the
    people it held are now perfectly reachable. Found by this session's review:
    the first version printed a clearing time three days in the *past*, in the
    present tense, and told the client to wait for it. A window changed to 0 on
    the Settings screen produces the same shape. Both now say the hold has gone
    and to rebuild now, which is true and actionable; neither invents a time.

    The window and the clearing time are dropped independently when they are not
    known — never invented, and never printed as "the last 0 days", which reads
    as a fact about the audience rather than about the rule (5e A6's own lesson).
    """
    window = (f" in the last {days} day{'s' if days != 1 else ''}"
              if days else " recently")
    # "All 1 contacts were texted" — the review found the count pluralised on
    # one side of the sentence and not the other, and the test that was meant to
    # catch it pinned the broken half. A 6,857-person list really can hold one
    # person back, so the singular is a sentence the client sees.
    people = (f"The one contact in this audience was texted{window}"
              if suppressed == 1
              else f"All {suppressed:,} contacts were texted{window}")

    if _still_ahead(clears_at):
        when = f" The hold clears at {clears_at_clock(clears_at)}."
        remedy = "Create the campaign again after that"
    elif clears_at or days == 0:
        # Either the moment has passed or the window has been switched off. The
        # hold is over either way, and the only wrong answer is to keep him
        # waiting for it.
        when = " That hold has since cleared."
        remedy = "Create the campaign again now"
    else:
        when = ""
        remedy = "Create the campaign again once the hold clears"

    return (
        f"{NOTHING_SENT} {people} and held back.{when} {remedy}; this one cannot "
        f"be restarted."
    )


def _still_ahead(stamp: Optional[str], now: Optional[datetime] = None) -> bool:
    """Is this clearing time in the future? Unparseable and absent are False.

    False rather than True on anything it cannot read, because the two branches
    fail differently: a past time presented as future tells the client to wait
    for a moment that has gone, and "the hold has since cleared" on a hold that
    has not merely sends him back to a composer that will hold them again — one
    wastes a day, the other wastes a click.
    """
    if not stamp:
        return False
    try:
        return datetime.fromisoformat(stamp) > (now or datetime.now())
    except (TypeError, ValueError):
        return False


def _breakdown(*, blocked: int, region_skipped: int, failed: int, queued: int) -> str:
    """"412 on the opt-out list, 118 rejected, 3 unaccounted for".

    The remainder is named rather than dropped. A breakdown that does not add up
    to the total invites the reader to assume the missing rows went out, which is
    the one thing this reason exists to deny.
    """
    parts: List[str] = []
    if blocked:
        parts.append(f"{blocked:,} on the opt-out list")
    if region_skipped:
        parts.append(f"{region_skipped:,} outside the sending region")
    if failed:
        parts.append(f"{failed:,} rejected before delivery")

    other = queued - blocked - region_skipped - failed
    if other > 0:
        parts.append(f"{other:,} unaccounted for")
    return ", ".join(parts) if parts else f"{queued:,} unaccounted for"


def top_up_prefix(added: int) -> str:
    """Front a reason with the fact that it describes a top-up, not the campaign.

    A top-up's reason lands on a campaign whose badge still reads `completed`,
    because the original blast really did complete and one later event cannot
    revoke that. The sentence has to carry the distinction on its own.
    """
    return f"Top-up of {added:,} recipient{'' if added == 1 else 's'}: "


def top_up_reason(added: int, reason: Optional[str]) -> Optional[str]:
    """The stored `abort_reason` for a top-up run that reached nobody."""
    if not reason:
        return None
    return top_up_prefix(added) + reason[0].lower() + reason[1:]
