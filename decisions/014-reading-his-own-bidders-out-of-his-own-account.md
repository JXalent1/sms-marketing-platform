# Reading his own registered bidders out of his own LiveAuctioneers account

**Blocks:** nothing. This records a reversal ordered by `sessions/session-L1.md`
before L1 builds on it, so that the reversal has a file of its own rather than
being a silent override of an earlier ruling.
**Status:** resolved — by the spec, 2026-09-22. Recorded, not escalated.

## What is being reversed

The plan of record, revised 18 Aug 2026 ("buyers only"), settled two things
that L1 touches:

- **Contacts arrive as CSVs he sends.** `A4A_BUILD_PLAN.md` §"Ship here":
  "He runs segmented campaigns off CSVs while Part 2 is built."
- **No marketplace scraping.** `modules.md` → *Deliberately not scoped*, item 2:
  "Competitor marketplace scraping. Flagged in the plan as likely violating
  those platforms' terms. Left out on purpose; if it happens it should be a
  manual occasional job, never the daily cron." `A4A_BUILD_PLAN.md`'s sources
  table says the same of "Competitor auction sites & marketplaces".

L1 adds a **daily scheduled job** that logs into a marketplace with a browser
and reads bidders out of it. On its face that is the thing item 2 forbade, on
the daily cron it forbade it on.

## Why it is a different act, not the same act with permission

The 18 Aug ruling was about **whose data** and **whose account**:

| | Marketplace scraping (still out) | L1 (in) |
|---|---|---|
| Account | none, or somebody else's | **his own partner account**, his credentials |
| Page | public listings, other houses' sales | `partners.liveauctioneers.com/house/<his id>/bidders` |
| People | strangers who bid elsewhere | people who **registered for his own sales** |
| Relationship | none — they are prospects | **his customers** |
| Where they land | would be the prospect review queue | the contact list, the way a CSV does |

The ToS concern in item 2 was reading a platform's public pages to harvest
bidders who have no relationship with him. L1 reads a portal page the platform
built *for him* to see exactly these people. The CSV he would otherwise export
from the same portal and upload by hand contains the same rows; L1 removes the
hand.

That is also why none of the prospecting rules apply: no buyer rationale, no
review queue, no `seller_or_consignor`. A registered bidder has already told
him, by registering, which side of the paddle they are on.

## What does not change

- **Item 2 stands.** Scraping other houses' sales, public listings or any page
  outside his own partner account is still out, and still not the daily cron.
  The platform registry (`app/sources/platforms.py`) makes room for Proxibid and
  AuctionZip on the same terms — **his own seller/partner account on each** —
  not for reading those sites' public bidder lists.
- **The CSV upload flow stays exactly as it is.** L1 is a second way in, not a
  replacement.
- **The first live run is Jordan's**, with his credentials, watching memory
  (`sessions/session-L1.md` Part B). Nothing in L1 ran against the live site.
- Every rule a contact is subject to — the blocklist, opt-out, the suppression
  window, quiet hours, line-type screening when it is on — applies to a
  scraped bidder without exception.

## What would reopen this

A term in LiveAuctioneers' partner agreement that forbids automated access to
the partner portal. Nobody has read it for this purpose. That is a Part B
question for Jordan, and if the answer is "forbidden", the job is switched off
by leaving `LA_USERNAME` blank — it does not register without credentials.
