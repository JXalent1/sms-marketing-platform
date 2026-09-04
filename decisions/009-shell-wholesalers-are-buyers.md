# Are shell wholesalers and importers sellers, and should they be excluded?

**Blocks:** P2, which is specced and not started. Nothing built depends on this.
**Why this is not mine to decide:** escalation item 10, and more to the point it is a
claim about who bids at this client's auctions. The answer lives with the client, not in
the taxonomy.

## Context

`A4A_BUILD_PLAN.md`'s Seashells section, written 2026-09-01, says:

> The genuine sellers here are **shell wholesalers and importers**, who are more likely to
> consign surplus stock to him than bid on it. Excluded, and named in the exclusion list.

`sessions/session-P2.md` carries that forward twice: "shell wholesalers and importers" sits
in the hard exclusion list matched on business name, and acceptance criterion 6 requires a
test proving they are rejected.

So P2, as specced, would find **Atlantic Coral Enterprise** — a Florida company whose own
site describes it as "Importer, Distributor and Wholesaler of Seashells" — and throw it
away. Same for US Shell, Worldwide Wildlife Products, California Seashell, Blue Seas
Trading.

Jordan, 2026-09-04, describing what the client means by the niche: companies that sell
seashells in bulk, whose shells go into tables, wall designs and streetscaping. Asked which
of four groups the client actually sees bidding, he named three, **with wholesalers,
importers and distributors as the priority.**

## First, this is my error, and it is a category error

The exclusion was reasoned, not observed. Nobody asked the client; the plan inferred it
from the word "wholesaler".

**A wholesaler is a merchant.** They buy inventory cheaply and resell at margin. When A4A
auctions a container of shells, the shell wholesaler is the most likely paddle in the room
— that is what their whole business is. The plan collapsed *"sells seashells for a living"*
into *"sells **to** the auction house"*, and those are different claims. The governing rule
is about which side of the paddle someone is on **at his auction**, and a trader is on both
sides on different days. Being a possible consignor does not disqualify anyone from being a
buyer.

This is the same shape as the error the plan already caught once and wrote down — estate
liquidators listed as buyers, corrected to sellers — arriving from the opposite direction.
The lesson generalizes past seashells: **"do they sell this thing?" is the wrong question.
"Would they raise a paddle for a lot of it?" is the question**, and for a trader in the
goods being auctioned the answer is usually yes.

## The ruling

**Shell wholesalers, importers and distributors are buyers, and they are the priority
group.** Removed from the exclusion list, added to the taxonomy as its own term group.

The seashell niche is **three groups, not one**, and they are three different businesses:

| Group | What they are | Why they bid | Radius |
|---|---|---|---|
| **Shell wholesalers, importers, distributors** — *priority* | The trade itself: containers in, sold on by the pound and the case | A discounted lot is inventory at margin | **National** |
| **Businesses that use shells as material** | Decor and furniture makers, mosaic and surface fabricators, craft manufacturers, sign and wall installers, coastal interior designers | Raw input for what they build | **National** |
| **Shell aggregate and landscape supply** | Crushed and washed shell by the cubic yard — driveways, paths, hardscape | Bulk material for jobs | **Regional — see below** |

**Retail shell and beach shops are demoted, not removed.** They were the plan's entire model
of this niche and the client did not name them. Keep them as a low-priority term group; the
term-flagging rule in `PROSPECT_TERM_FLAG_SHARE` will retire them on their own evidence if
they produce the wrong side of the room.

## Riders

**1. The third group must not run national, and this is not a detail.** Crushed shell is
sold by the cubic yard. It is heavy, low value per ton, and freight dominates its price, so
nobody buys a yard of it from a thousand miles away — the "can they collect it" rule binds
here harder than it does for a walk-in cooler, not less. **150 miles at the most, and
tighter is defensible.**

**2. That breaks how radius is configured.** `PROSPECT_CATEGORY_RADIUS_MILES` is keyed by
**category**, and this niche now needs two national term groups and one regional one inside
a single category. **Radius has to become a property of the search-term group, not the
category**, with the category value as the fallback. That is a change to P2's A1 and A2 and
it is written into the spec.

**3. The exclusion list is not empty here.** Other auction houses, estate-sale companies,
estate liquidators, appraisers and consignment galleries still stand. What comes out is the
single line about shell wholesalers and importers, and only that line.

**4. Volume expectations are unchanged by this ruling and should be said plainly.** The
priority group is a small universe — on the order of 50–200 businesses nationally. It is the
highest-intent group and it is not where a 2,000–3,000 number list comes from. Memorabilia
carries that: 9,755 pawn shops and 3,256 sports card stores nationally, both already
national in the taxonomy.

**Decided by:** Jordan, 2026-09-04
**Status:** resolved
