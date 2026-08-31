
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
