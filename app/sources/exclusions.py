"""Businesses this system must never prospect, and why.

Part of the taxonomy rather than a filter bolted on afterwards — it is the other
half of "buyers, never sellers", and a term list without it finds the wrong side
of the room by design. It lives in its own module for two reasons: `taxonomy.py`
was approaching the 500-line rule, and this list is enforced from
`app/services/prospect_ingest.py`, which must not import a module that reaches
back into `app.services`.

**One shared definition.** `CLAUDE.md` records what happens when a reserved set
gets copied into three layers: `/settings` had to be excluded from slug minting
and then leaked again on the serving side, three fixes for one collision. This
list is matched in exactly one place — `record_prospect()`, the single path from
any source into the prospect tables — so a source written next year inherits it
without knowing it exists.

## Which way this matcher should fail

`CLAUDE.md` asks for the answer to be written next to the list, because the two
matchers in `app/sms/compliance.py` differ on precisely this and it is not a
style question.

This one causes a **refusal to prospect**. Wrong in one direction it costs a
single missed business out of thousands. Wrong in the other it puts a competitor
on the client's own marketing channel and a number that will never bid on a list
he pays a segment for on every send. So it is deliberately a shade wider than
word-exact: a phrase matches at the **start of a word and is open at the end**,
which catches `auctioneers`, `appraisals` and `estate liquidation` from one
entry each.

It is start-anchored rather than a bare substring for the reason `21610` taught
this codebase — an unanchored fragment matches things that are not words of that
kind. `auction` must not fire on `precaution`.

## `estate liquidat`, not `liquidat`

A **liquidation store** buys closeout stock. It is a general-merchandise buyer
and `closeout store` is one of our own search terms. An **estate liquidator**
consigns to the auction house. One word, two industries, opposite sides of the
paddle — so the phrase is the narrow one that separates them, exactly as
`"unreachable"` was removed for meaning three things at once.

## What is NOT here, and must not come back

**Shell wholesalers, importers and distributors.** `decisions/009`, 2026-09-04:
they are buyers and they are the priority group. The old plan inferred "seller"
from the word "wholesaler" without asking the client, and a wholesaler is a
merchant — he buys cheaply and resells at margin, so a discounted lot is his
whole business. `tests/test_taxonomy.py` fails if any phrase below names a
wholesaler, an importer or a distributor, because a later session reading the
plan's old wording is exactly how that line comes back.
"""

from dataclasses import dataclass
from typing import Optional, Tuple
import re


@dataclass(frozen=True)
class Exclusion:
    slug: str
    label: str
    phrases: Tuple[str, ...]
    why: str


EXCLUSIONS: Tuple[Exclusion, ...] = (
    Exclusion("auction_house", "Another auction house", ("auction",),
              "A competitor. Prospecting one puts our client's marketing "
              "channel in front of the business taking his bidders."),
    Exclusion("estate_sale_company", "Estate-sale company",
              ("estate sale", "tag sale"),
              "They run their own sales and consign what does not move — a "
              "seller, and a competitor for the same consignments."),
    Exclusion("estate_liquidator", "Estate liquidator", ("estate liquidat",),
              "Sellers. They consign to him. The plan of record listed them as "
              "buyers in its first draft and corrected it."),
    Exclusion("appraiser", "Appraiser", ("appraiser", "appraisal"),
              "They value goods for the people who consign them. No paddle."),
    Exclusion("consignment_gallery", "Consignment gallery", ("consignment",),
              "A consignment gallery takes goods on consignment and rarely "
              "buys inventory. Resale and thrift shops do — they read alike "
              "and behave oppositely, which is why this phrase is exact."),
    Exclusion("we_buy_houses", "\"We buy houses\" operator",
              ("we buy houses", "we buy ugly houses", "cash for homes",
               "sell your house fast"),
              "They buy property to flip and move the contents on. Whatever "
              "they do with an estate, they are not raising a paddle for it."),
)

_NON_WORD = re.compile(r"[^a-z0-9]+")


def normalize_name(business_name: Optional[str]) -> str:
    """Lowercase, punctuation to spaces, padded so a phrase can be anchored."""
    if not business_name:
        return ""
    return " " + _NON_WORD.sub(" ", business_name.lower()).strip() + " "


def excluded_reason(business_name: Optional[str]) -> Optional[str]:
    """Why this business must never be prospected, or None.

    Answers with the human sentence rather than the slug: the only consumers are
    a log line and a job counter, and both are read by somebody deciding whether
    a search term is worth keeping.
    """
    padded = normalize_name(business_name)
    if not padded.strip():
        return None
    for rule in EXCLUSIONS:
        for phrase in rule.phrases:
            if f" {phrase}" in padded:
                return f"{rule.label}: {rule.why}"
    return None
