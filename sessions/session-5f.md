# Session 5f — Short links, click stats, and reporting

## Objective

The client can send. He cannot yet answer "did it work?" — not for himself, and not for
the auction house paying for it.

Three things, in dependency order: a short-link service he can put in a message, the
click data that comes back from it, and the reports that turn a campaign into a number
someone will act on.

This is the last planned session before the client gets a login.

## Prerequisites

- Read `CLAUDE.md`, `RULES.md`, `status.md`, `modules.md` before starting.
- 5e, 5g and 5h are merged and deployed. 371 tests, gate green.
- Production holds the client's real contacts and message history.
- Server: `ssh -i ~/.ssh/a4a_deploy appuser@67.205.180.62`, sudo limited to
  `systemctl restart a4a-sms`.
- Deploy: `SERVER=appuser@67.205.180.62 SERVICE=a4a-sms ./deployment/deploy.sh`

## Decided already — do not relitigate

Recorded in `modules.md` § "Requested 2026-08-24":

- **A short dedicated domain, not a subdomain of the main site.** `a4a.bz/a7k` is 10
  characters against `go.auctions4america.com/a7k` at 27 — 11% of a segment, enough to
  push a tight message to two. Precedent: `es.pn`, `swoo.sh`.
- **The domain is a config value.** It may not be registered when you build this.
  `SHORT_LINK_DOMAIN` in settings, no hardcoding anywhere, and the feature degrades
  cleanly with a clear operator-facing message when it is unset rather than minting
  broken links.

---

# Part A — agent work

## A1. Short links

A `short_links` table, a redirect route, and nothing else in the request path.

**Per recipient, not per campaign.** One row per contact per campaign, each with its own
slug. This costs ~4,200 rows on a full send and buys the thing that makes the whole
feature worth building: *which buyers clicked*. For an auction house that list is the
highest-intent audience it will ever have — people who opened the message about tonight's
sale — and it is worthless in aggregate. "340 clicks" is a statistic; "these 340 people"
is a phone list.

Requirements:

- **One redirect, never a chain.** T-Mobile's code of conduct flags anything redirecting
  more than once, and a chain is what gets a domain filtered.
- **Closed minting.** Only an authenticated campaign send creates links. An open
  redirector is found and abused within weeks, and then the branded domain is the one on
  the blocklist — which would cost far more than the feature is worth.
- **Slugs are short, URL-safe and non-sequential.** Sequential ids leak send volume to
  anyone who reads one. Avoid visually ambiguous characters.
- **Recording a click must never delay or break the redirect.** If the write fails the
  person still reaches the auction.
- Store the resolved target so a report can show where a link pointed after the auction
  page is gone.

## A2. Bot and scanner filtering

SMS links get fetched by carrier scanners, handset link previews and security tooling
before any human sees them. Unfiltered, a campaign shows clicks it did not earn — and
this number is going to a client who will make decisions with it.

Record enough to separate them: user agent, timestamp, and whether a click arrived
implausibly soon after the send. Mark suspected non-human clicks rather than discarding
them, and have the reports count only the human ones by default.

Do not invent precision. If the classification is a heuristic, name it as one in the UI —
"340 clicks (12 filtered as automated)" is honest; a bare number that quietly excludes
things is not.

## A3. Composer merge tag

A merge tag that renders each recipient's own link.

The segment counter must count the **rendered** link, not the tag. `CLAUDE.md` carries
the A7 lesson from 5b: a template that looks like one segment can render to two, and the
count that matters is per recipient. The pre-flight already renders per recipient —
follow that path, do not add a second estimator.

If `SHORT_LINK_DOMAIN` is unset, the tag must refuse at compose time with a message
naming what is missing. Never at send time.

## A4. Capture what the carrier actually charged

`campaigns.estimated_cost` exists and its own comment says it is there to *"reconcile
against the invoice afterwards"* — but nothing stores the actual, so there has never been
anything to reconcile against.

The provider returns `cost` and `cost_breakdown` (rate and carrier fee separately) on
every message and the provider class discards both. Capture them per message.

This matters commercially, not just tidily: the configured `WHOLESALE_COST_PER_SEGMENT`
of 0.009 is a deliberate over-reserve for the capacity guard, the real blended rate is
believed to be around half that, and nobody can currently prove it without subtracting
account balances by hand. Per-carrier pass-through differs, so the true figure is
per-campaign, not a constant.

Keep it white-label: the client's reports show his price, never our cost. `CLAUDE.md`
already records that leak and it has been made once.

## A5. Per-campaign report

One screen per campaign: recipients, delivered, failed, held back, opt-outs, clicks,
click-through rate, and cost at **his** price.

- Top-up contributions are visible, not merged away — `sms_messages.top_up_at` exists so
  a report reads "3 + 1 added 27 Aug".
- A campaign that reached nobody shows its abort reason, in the wording 5h already
  produces. Do not write a second sentence about the same fact.
- Exportable. He will want to send it to the auction house.

## A6. Campaign history and message history

Deferred at launch as module 8; now needed.

Campaign history is a list with the numbers that matter and a route into A5. Message
history is per-contact: what this person was sent, when, whether it arrived, whether they
clicked. That per-contact view is what makes a phone call possible — "you looked at the
flooring sale on Tuesday" — and it is the payoff for keeping the contacts table under the
per-campaign uploads.

Both paginate. There are 3,000+ message rows per campaign and the box is a 1vCPU droplet.

---

## Part A acceptance

Demonstrate each in the transcript.

1. `agent/gate.sh` passes all six checks, twice.
2. A campaign with the merge tag mints one link per recipient; each resolves to the target
   in exactly one hop; the segment count reflects the rendered link.
3. `SHORT_LINK_DOMAIN` unset: composing with the tag refuses with a message naming the
   cause. No broken links are minted and no send is attempted.
4. An unauthenticated attempt to mint a link fails.
5. A click is recorded and attributed to the right contact. A click whose write fails
   still redirects — show it.
6. A scanner-shaped request is marked non-human and excluded from the default count,
   with the filtered count shown alongside.
7. Actual cost and its rate/carrier-fee split are captured per message, and the campaign
   report reconciles estimate against actual.
8. No wholesale cost, carrier name, or raw provider payload appears in any client-facing
   page, API response, or export. Prove it over the report and both history screens, not
   just the pages that existed before.
9. Both history screens paginate; show the query count does not scale with rows returned.
10. Behavioural mutation run per `CLAUDE.md` — mutations inside the current API, not an
    import failure. Follow `agent/mutate-5h.py`; wire in as check 8b. At minimum: revert
    the one-hop guarantee, the mint authentication, the bot filter, and the
    render-time segment count.
11. After deploy: all screens 200 over HTTPS, fonts load, no leaks.

Wire into a `/goal` stop condition with a turn cap, then **one synchronous**
fresh-context review. No background reviewers.

## Constraints

- Do not touch `.env`, `.env.production`, `agent/gate.sh`, `agent.config.sh`.
- **Do not send SMS. Do not modify, delete or re-import contact data.**
- Do not weaken any pre-flight check.
- No source file over 500 lines. Two files have crossed it twice; `campaign_dispatch.py`,
  `campaign_release.py` and `factory.py` show the grain to follow.
- `app/sms/` stays DB-free.
- No hardcoded commercials, and no hardcoded domain.

## Explicitly out of scope

- Registering the domain. Jordan's, and the build must not wait on it.
- Line-type screening at import.
- Decision 004 rider 3, the structured transient-code set.
- The prospecting engine and data streams.
