
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

## Seashells — a named niche, added 2026-09-01

The client asked for this specifically: seashell businesses perform unusually well for him.

### The inversion to be careful about

"People who sell seashells" reads like a seller, and this plan's one rule is buyers only.
It isn't. **A shell shop buys inventory to stock its shelves** — it bids at his auction.
Same shape as resale shops under Estates, and the same shape that made me wrongly list
estate liquidators as buyers in the first draft.

The genuine sellers here are **shell wholesalers and importers**, who are more likely to
consign surplus stock to him than bid on it. Excluded, and named in the exclusion list.

### Buyer types

| Buyer | Why they bid |
|---|---|
| Shell and beach shops | inventory to resell — the core of this niche |
| Coastal souvenir and gift shops | shells as stock, especially Gulf-coast towns |
| Beach / nautical decor retailers | decor lots |
| Interior designers and stagers doing coastal work | already in Estates; shells overlap |
| Aquarium and reef shops | shells, coral, specimens |
| Jewellery makers and craft suppliers | raw material by weight |
| Collectors — conchologists | specimen shells, the high-value end |

### Geography: national, with a dense Florida cluster

Shells ship cheaply, so the collector market is national like memorabilia. But Florida is
the centre of it — Sanibel, Captiva and the Gulf coast — so a local sweep and a national
sweep will both pay, and they are different search runs with different radii.

### Sources beyond Google Places

Places will find shell shops, souvenir and decor retailers. Two source types will find the
higher-intent end and neither is a Places query:

- **Shell club and conchological society directories**, and **shell show exhibitor
  lists.** Public, targeted, and a membership list is a list of people who buy specimen
  shells on purpose. The highest-intent seashell source available.
- **Marketplace sellers** on eBay and Etsy listing shells. These are literally "people who
  sell seashells" — they buy inventory to resell, and they are not in Places. A separate
  source type; note it for P3+ rather than bending Places to reach it.

### Where it lives in the product

**Not a new category.** The palette is validated at four hues plus neutral and is already
full, and since 5e the campaign-first flow means a niche does not need a category to be
textable — upload the list, name the campaign "Seashell Auction 9/14", send.

Seashells is a **search-term group in the P2 taxonomy**, with its own buyer rationale per
term, promoted into General or left untagged as the operator prefers.
