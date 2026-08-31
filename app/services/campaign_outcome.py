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

Everything here is a pure function over counts. No session, no campaign object,
no writes — so the wording can be asserted directly and the send loop keeps one
job. Counts come from the run's own rows, not from the campaign's lifetime
counters, because those already carry the original send's numbers.
"""

from typing import List, Optional

# The trailing fact, appended to every reason. It is the sentence the client
# actually needs — the cause explains it, this is what happened. Past tense
# throughout: unlike the composer's pre-send refusals (see `send_path_assessment`
# in app/sms/factory.py), nothing reads this before the run.
NOTHING_SENT = "Nothing was sent."


def zero_send_reason(*, queued: int, blocked: int = 0, region_skipped: int = 0,
                     failed: int = 0, suppressed: int = 0) -> str:
    """Why this run reached nobody, in one sentence naming the cause.

    `queued` is how many messages the run actually had to work with — the rows
    that were `pending` when it started. `suppressed` is how many contacts were
    held back before that, which is the only cause that leaves `queued` at zero
    while the audience itself was not empty.

    The four causes are reported in the order that answers "what do I change?":
    an empty audience and a fully-suppressed one are different problems with
    different fixes, and a list that is entirely opted out is neither.
    """
    if queued == 0:
        if suppressed:
            # The remedy has to be one that works on *this* campaign, and neither
            # "lower the window" nor "wait" is — which is what an earlier wording
            # of this sentence recommended. Suppression is partitioned when the
            # draft is built and frozen into `skipped` rows, and the send loop
            # only ever reads `pending`; nothing moves a campaign back from
            # `aborted`. So a client who follows that advice changes a setting,
            # presses send again, and watches the same campaign fail the same
            # way. Naming the window is still worth doing — it is what caused
            # this — but the action is to build the campaign again.
            return (
                f"Every contact in this audience — all {suppressed:,} — was texted "
                f"recently and held back, so there was nobody left to send to. "
                f"{NOTHING_SENT} Lower the hold-back window in Settings if this is "
                f"not what you wanted, then create the campaign again — this one "
                f"cannot be restarted."
            )
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
