
---

# Prospecting — plan of record, 2026-08-31

Supersedes §4's original sketch. Written after launch, against what production taught us.

## The one rule everything else serves

**We are looking for people who BUY at his auctions. Never people who sell into them.**

He has no shortage of consignors. Every prospect this system produces must be someone
who would raise a paddle, and a business that supplies him is worse than useless — it
costs money to text, it dilutes the list, and it puts a competitor on his own marketing
channel.

This is easy to get wrong in the taxonomy and easy to get wrong again in review, so it
is enforced in three places rather than trusted once:

1. **Every search term carries a written buyer rationale.** "Food trucks buy used prep
   equipment" is a claim someone can disagree with. A term with no rationale does not
   ship.
2. **The review queue shows that rationale on every prospect**, so the reviewer is
   answering "would this person bid?" rather than "does this look like a business?"
3. **`seller_or_consignor` and `competitor` are first-class reject reasons**, and both
   suppress permanently. A term whose rejections skew to those two gets flagged for
   removal — the system learns which searches produce the wrong side of the room.

### The exclusion list is part of the design, not an afterthought

Never prospect, and actively filter by business name: other auction houses, estate-sale
companies, estate liquidators, appraisers, consignment galleries, "we buy houses"
operators. These sell *to* him or compete with him.

Note the trap in that list: **consignment shops take goods on consignment and rarely buy
inventory. Resale and thrift shops do buy.** They read alike and behave oppositely.

## What production taught us that changes the plan

**Landlines are the dominant cost.** 2,526 of one campaign's failures were "not routable"
— 39% of a 6,857 send. Google Places returns business main lines. A directory scraper
without line-type screening in front of it manufactures dead weight that is paid for on
every future send.

Telnyx MCC/MNC lookup is **$0.0025 per number**. Screening 10,000 costs $25. It goes in
front of everything.

**Scrape by who answers their own phone.** A restaurant lists a landline. A food truck
lists the owner's cell, because the business *is* the person. Sole proprietors and mobile
businesses are the difference between a 30% mobile rate and a 70% one, and that is worth
more than any other targeting decision here.

## The taxonomy — buyers only

| Category | Buyer types | Why they bid |
|---|---|---|
| Food service | food trucks, caterers, ghost kitchens, mobile bartenders, small delis, new restaurants | fitting a kitchen on a budget; used prep and refrigeration |
| Equipment | contractors, landscapers, tree services, mobile welders, hauling, small machine shops | tools and machinery at below dealer price |
| Estates | interior designers, home stagers, flippers, **resale and thrift shops** | inventory and furnishings to resell or place |
| Memorabilia | card / comic / coin dealers, pawn shops, show vendors — national | inventory for their own shelves |
| General | flea-market vendors, resellers, discount stores | pallets and lots to break and resell |
| Marine | brokers, marina services, refit yards, boat detailers — national | equipment, tenders, fittings |

**Corrected from the first draft:** "estate liquidators" was listed under Estates. They
are sellers. Removed. "Consignment dealers" replaced with resale and thrift shops for
the reason above.

## Economics

- Google Places Enterprise tier returns a phone: **$35 / 1,000 Text Search requests**,
  up to 20 places each — about **$0.00175 per business**. First 1,000 requests a month
  are free (the pooled $200 credit was retired March 2025).
- Line-type screening: **$0.0025 per number**.
- **~10,000 businesses scraped and screened: under $60.** At a 25–35% mobile rate that is
  2,500–3,500 new textable buyers, against a current reachable list of ~4,200.

## Radius per category

The question is "can they collect it."

| Category | Radius |
|---|---|
| Food service, Equipment, General | 150 miles |
| Estates | 100 miles |
| Memorabilia, Marine | national |

Config values, not code. Move them once there is response data to move them with.

## Build order

**P1 — prospect pipeline.** The holding pen, the line-type gate and the review queue. No
source implementations. Nothing reaches the contact list without passing through it.

**P2 — Google Places source.** The taxonomy above, per-category radius, spend cap, dedup
against both prospects and existing contacts.

**P3 — registries and enrichment.** FL DBPR food-service and contractor licences, Sunbiz
officer names. The prize is *new* licences: a kitchen licensed last month is being fitted
this month, and that timing beats volume.

---

## Seashells — a named niche, added 2026-09-01, rewritten 2026-09-04

The client asked for this specifically: seashell businesses perform unusually well for him.

**Rewritten after `decisions/009`.** The first version of this section had the niche wrong
in a way that would have made P2 discard its best prospects. What it said, and why it was
wrong, is kept below the taxonomy — the reasoning is the useful part.

### The three groups

**Priority is the first.** These are three different industries that happen to share a
material, and treating them as one group is what produced the original error.

| Group | What they are | Why they bid | Radius |
|---|---|---|---|
| **Wholesalers, importers, distributors** — *priority* | The trade itself. Containers in, sold on by the pound and the case: Atlantic Coral Enterprise, US Shell, Worldwide Wildlife Products, California Seashell, Blue Seas Trading | A discounted lot is inventory at margin. This is their whole business | **National** |
| **Businesses that use shells as material** | Decor and furniture makers, mosaic and surface fabricators, craft manufacturers, sign and wall installers, coastal interior designers | Raw input for what they build — the tables and the wall designs | **National** |
| **Shell aggregate and landscape supply** | Crushed and washed shell by the cubic yard: driveways, paths, hardscape. Landscape supply yards, hardscape contractors, decorative concrete | Bulk material for jobs | **Regional, 150 miles at most** |
| Retail shell and beach shops | Tourist-facing shops stocking shells to sell on | Inventory for the shelf | National, low priority |

**The third group must not run national.** Crushed shell is sold by the cubic yard, it is
heavy, and freight dominates its price — nobody buys a yard of it from a thousand miles
away. The "can they collect it" rule binds harder here than it does for a walk-in cooler,
not less.

**Retail shops are demoted, not removed.** They were this section's entire model of the
niche and the client did not name them. `PROSPECT_TERM_FLAG_SHARE` will retire them on
their own evidence if they produce the wrong side of the room.

### The inversion this section originally got backwards

The first version read:

> "People who sell seashells" reads like a seller, and this plan's one rule is buyers only.
> It isn't. **A shell shop buys inventory to stock its shelves.** … The genuine sellers here
> are **shell wholesalers and importers**, who are more likely to consign surplus stock to
> him than bid on it. Excluded.

Half right and half backwards. It caught that a retail shop is a buyer and then invented a
seller to sit behind it, from the word "wholesaler" rather than from anything the client
said.

**A wholesaler is a merchant.** They buy cheap and resell at margin, so when a container of
shells goes under the hammer they are the most likely paddle in the room. The section
collapsed *"sells seashells for a living"* into *"sells **to** the auction house"*, and
those are different claims. Being a possible consignor does not disqualify anyone from
being a buyer — a trader is on both sides on different days.

**The general form, and it is worth carrying past seashells:** "do they sell this thing?"
is the wrong question. **"Would they raise a paddle for a lot of it?"** is the question.
For a trader in the goods being auctioned, the answer is usually yes. This is the estate
liquidator error — listed as a buyer, corrected to a seller — arriving from the opposite
direction, which is why the rationale-per-term rule exists: a claim somebody can disagree
with is a claim somebody can correct.

### What still stands

The exclusion list is unchanged apart from one line. Other auction houses, estate-sale
companies, estate liquidators, appraisers, consignment galleries and "we buy houses"
operators are still never prospected. Only "shell wholesalers and importers" comes out.

Geography is national with a dense Florida cluster for the first two groups — Sanibel,
Captiva and the Gulf coast are the centre of the trade, so a local sweep and a national
sweep both pay and they are different runs.

### Sources beyond Google Places

Unchanged and still the higher-intent end, still P3 rather than P2:

- **Shell club and conchological society directories**, and **shell show exhibitor lists.**
  A membership list is a list of people who buy specimen shells on purpose.
- **Marketplace sellers** on eBay and Etsy listing shells — they buy inventory to resell,
  and they are not in Places.

### Volume, stated plainly

The priority group is small: on the order of 50–200 businesses nationally. It is the
highest-intent group in the plan and it is **not** where a 2,000–3,000 number list comes
from. Memorabilia carries that — 9,755 pawn shops and 3,256 sports card stores nationally,
both already national in the taxonomy. Seashells is the niche the client says converts;
memorabilia is the niche that supplies the volume. Run both.
