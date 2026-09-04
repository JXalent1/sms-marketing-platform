# Can A7's category sweep subtract its exceptions by module?

**Blocks:** nothing — 5i Part A is complete, `agent/accept-5i.sh` exits 0, the gate is
green twice and the mutation run is 16 caught / 0 survived. This records a ruling on a
departure already shipped, and supersedes a clause in `sessions/session-5i.md`.
**Why this is recorded rather than absorbed:** `RULES.md` — a session spec is not edited
after the fact except when a resolved decision supersedes something in it. A7's mechanism
clause is wrong and leaving it wrong is worse than editing it.

## Context

`sessions/session-5i.md` A7 requires a runtime proof that no client-facing surface names a
category, and specifies the mechanism:

> Subtract the routes owned by **`app.routers.prospects`** — by module, not by path
> string. The prospect review queue keeps its category, and A8 says why.

That instruction cannot be carried out, and the reason is structural rather than a matter
of effort.

**`/prospects` is not served by `app.routers.prospects`.** It is served by `pages.page`,
the shared handler behind six screens, so the module that owns the route is
`app.routers.pages` and subtracting it would take the Contacts and Today screens — the
two surfaces the sweep exists to cover — out with it.

Five further surfaces keep the word on purpose, each under a clause of the same spec:

| Surface | Retained by |
|---|---|
| `app.routers.categories` | A8 — fills the prospect promote dropdown; A5 removed every other caller |
| `app.routers.reports`, `/history`, `/history/{id}` | the file list: `report_service` / `history_service` are in scope "for one reason only" |
| `/api/contacts`, `/api/contacts/export.csv` | A5 — built by `contact_query_service`, which is outside the file list |

So the choice was never module-subtraction versus path-subtraction. It was between a sweep
with a named exemption list and no sweep at all.

## First, this is my error

I wrote "by module, not by path string" from the general principle in `CLAUDE.md` — that a
rule recognising a shape must subtract the same reserved set from one shared definition,
never from copies that drift. That principle is right and it is about `RESERVED_SLUGS`,
where every collision genuinely is one shape decided in several layers. It does not
transfer to a route sweep, because a FastAPI route's owning module is a fact about which
file the handler lives in, not about which screen it serves. `pages.page` is the
counter-example and it was already in the repo when I wrote the clause. I did not check it.

The same paragraph also carried the reason the instruction existed — "do not pin a list of
paths", because a denylist rots. That half was right and the implementation kept it.

## The ruling

**Option 2 — a named exemption list, each entry carrying the clause that retains it, with
a staleness check that fails when an entry stops being needed.** This is what shipped, in
`tests/test_audience_surfaces.py`, and it is a better answer than the one I specified.

Three properties earn it:

1. **`test_no_exemption_has_gone_stale`** fails the moment an exempted route stops carrying
   the word — which is the moment the entry should be deleted, not the moment somebody
   notices. That is the rot a denylist is feared for, closed.
2. **`test_the_sweep_covers_the_audience_surface`** names the eight routes that must remain
   covered rather than asserting a count. A floor of "at least N routes" drifts; a named
   set fails loudly when an exemption eats one.
3. **`test_the_sweep_actually_fires`** points the matcher at `/api/categories`, a body
   entirely about categories, before the sweep is quoted as evidence. `CLAUDE.md`: a green
   check whose script is broken is worse than no check.

Discovery is still the app's own route table, so a route added tomorrow is scanned the
moment it exists. The exemptions are subtractions from a live set, not a hand-maintained
list of what to look at.

## Riders

**1. The spec's A7 mechanism clause is struck through in place**, quoting the old text and
naming this decision, per the precedent 5g criterion 3 set with decision 004.

**2. The contacts CSV export is the one residual a client actually sees.**
`/api/contacts/export.csv` still carries a `categories` column. It is correctly exempt —
`contact_query_service.py` is outside 5i's file list and the spec routes out-of-list
findings to `status.md` — but a spreadsheet the client opens is a category surface in a way
a JSON key with no renderer is not. It is the first item of whichever session next touches
that file, and it should not wait for module 8.

**3. `app.routers.reports` is exempt for a file-list reason, not a product reason.** The
report screens render `category_label` with `audience_label` behind it, so a post-5i
campaign reads correctly today and the exemption costs nothing. When the reports files are
next in scope, drop `category_label` from the payload and delete the exemption — the
staleness check will then be the thing that tells you it worked.

**Decided by:** Jordan (via Cowork), 2026-09-04
**Status:** resolved
