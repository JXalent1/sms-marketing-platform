# Session 5i — retire the category model for named lists

**Module:** 5i · **Depends on:** 5h, P1b · **Parallel-safe with:** P2 (file sets are disjoint)

Requested by Jordan 2026-09-04. Decisions taken 2026-09-04 and recorded in `modules.md`.

---

## Prerequisites

- Read `CLAUDE.md`, `RULES.md`, `status.md`, `modules.md`, and every file in `decisions/`.
- 5h and P1b are merged. **544 tests, gate green twice** — a lower count means a stale
  branch, not that tests vanished.
- **Run inside the project venv.** `agent/gate.sh` calls bare `python` and `alembic`. A
  system or conda interpreter dies at collection with `ModuleNotFoundError: slowapi` and
  reports "test suite is red" — a true statement about the wrong interpreter. Either
  activate `.venv` or run `PATH="$PWD/.venv/bin:$PATH" bash agent/gate.sh`.
- `npm run build:css` before serving anything. `app/static/app.css` is a gitignored build
  artifact and 404s in a fresh clone or worktree.
- Production holds the client's real contacts, lists and message history. The migration in
  A1 rewrites data in a live table.
- **Part B below is Jordan's and is not a blocker.** Build against the local database,
  which already carries one row of each `created_at` spelling — the exact fixture A1 needs.

## Why this session exists

Jordan's dad asked where to put the list for a yacht auction. None of the five
categories — Food Service, Equipment & Machinery, Estates, Memorabilia, General
Merchandise — fit, and a sixth was not available: the palette is validated at four hues
plus neutral and is full (escalation item 9).

The Williamson build (wagmarketingbot.com) never had this problem, because it never had
categories. It has **one flat dropdown of past lists, each titled by the campaign that
first used it, newest first, with `⭐ ALL BIDDERS — MAIN LIST` pinned at the top.** You
either upload a fresh list or pick one you used before. That is the model this session
moves to.

Session 5e already did most of the groundwork: the category is optional, upload-per-
campaign is the primary flow, and `contact_lists` / `audience = "list:<id>"` have existed
since the skeleton. What remains is mostly presentation, plus one data defect that only
becomes dangerous when recency is what the picker is sorted by.

## The decision that governs everything below

**Categories are hidden, not removed.** Jordan ruled on this on 2026-09-04.

Every category surface comes off the client's screens. Nothing comes out of the schema.
`categories`, `contact_categories`, `contact_lists.category_id`, `campaigns.category_id`,
`campaigns.cross_category_override` and the `--s1`..`--s4` palette variables all stay
exactly as they are.

Three reasons this is the ruling and not a compromise:

1. **The prospecting engine keys off categories.** `prospect_scoring.py`,
   `prospect_service.py`, `prospect_base.py` and the five per-category radius settings in
   `core/config.py` are P2's taxonomy. Removing the model is not a UI change; it is a
   redesign of a module that is specced and about to run.
2. **Historical campaigns store `category:<slug>` selectors.** `audience_label()` renders
   them in campaign history and in every per-campaign report. Deleting the resolution
   path turns every report older than this session into a broken string.
3. It keeps a reporting axis open — "how do estate buyers perform against memorabilia" —
   at the cost of a concept in the schema nobody sees. That cost is a comment; the other
   direction's cost is a migration nobody can reverse.

**So: do not write a migration that drops a table, a column or an index in this session.**
If you believe one is needed, that is escalation item 8 and the answer is already no.

---

## What is in scope

### A1 — one writer owns `contact_lists.created_at`

**This is the requirement to do first, and it is the only one with a data defect under it.**

`contact_lists.created_at` has two writers keeping two clocks, which is the exact defect
`CLAUDE.md` records for `contact_list_members.added_at`:

- `contact_service.py:90` — `ContactList(name=..., description=..., source=...)` omits the
  column, so SQLite's `CURRENT_TIMESTAMP` server default writes it. That is **UTC**, and
  it is spelled `YYYY-MM-DD HH:MM:SS` with a space.
- `import_service.py:247` — writes `datetime.now().isoformat()`. That is **local**, and it
  is spelled with a `T` and microseconds.

Both spellings are in the live database today:

```
(1, 'Demo list',          'manual', '2026-08-20 00:12:30',          None)
(2, 'Italian restaurants','csv',    '2026-08-27T10:46:45.685160',   None)
```

Until now nothing compared these rows. This session makes recency the sort order of the
picker, the dashboard and the composer — so the error becomes visible, and it is
one-directional: every server-defaulted row reads up to five hours **newer** than it is,
and a lexicographic comparison across `T` and a space is wrong independently of that.

Required:

1. Remove the `server_default` from `ContactList.created_at`. Every insert sets it
   explicitly, from the application clock, in the same spelling. One writer.
2. A migration normalises the rows already stored. The two writers are 1:1 with the two
   spellings — `CURRENT_TIMESTAMP` cannot emit a `T` and `isoformat()` cannot emit a
   space — so a space-separated value is a server-default row, is UTC, and converts. Say
   that in the migration's docstring, and say what makes it safe: the classification is
   from the format, not from a guess about the row.
3. **Order by a parsed value, never by the string.** A helper parses `created_at` and
   returns a `datetime`; an unparseable value sorts last and does not raise, on the same
   grounds as `audience_label()` — this renders in page headers, and a helper that throws
   turns one bad row into a 500 on a screen that was only trying to sort.
4. `alembic upgrade head` from a clean database, and against a copy of the live database.

### A2 — `contact_service.list_summaries()` returns the new picker

Today it returns `all`, then one entry per active category, then every list ordered by
`ContactList.name`. It becomes:

1. **The pinned entry first**, selector `all`. Its wording lives in **one module-level
   constant** and every surface renders it verbatim — the composer dropdown, the dashboard
   card and anything added later. `send_mode()` in `app/sms/factory.py` is the pattern and
   the reason: one function owns the client-safe wording so a second surface cannot invent
   a second spelling of the same thing. The wording is `⭐ ALL BIDDERS — MAIN LIST`.
2. **Then every list, newest first**, by the parsed `created_at` from A1.
3. **No category entries.** The picker stops offering them.

Each entry keeps `selector`, `label`, `count`, `kind`, and gains `last_sent_at` /
`days_since_sent` for A6.

**`resolve_audience()`, `_term_ids_query()`, `_term_label()` and `audience_count()` keep
their `category:` branches untouched.** The picker stops producing category selectors;
the resolver must still consume them, or every campaign in history loses its label. There
is a test for this and it is criterion 4.

Freshness comes from `sms_messages`, never from a campaign's audience string — the reason
is in `dashboard_service.py`'s own docstring and it has not changed. The query is one
grouped join: messages → contacts → `contact_list_members`, `max(sent_at)` by `list_id`,
statuses in the sent set.

**That sent set already exists twice, and this session would make it three times.**
`SENT_STATUSES = ("sent", "delivered")` is defined independently at
`dashboard_service.py:44` and at `report_service.py:55`, and `history_service.py:41`
imports the second one. Two definitions of one rule is how the two screens come to
disagree about what "texted" means — the same defect
`test_the_opt_out_definition_matches_the_dashboard_tile` was written for.

`dashboard_service` imports `contact_service`, so `contact_service` cannot import it back.
Move the constant to `app/models/sms_message.py`, where `BILLABLE_STATUSES` already lives
(line 53), and have all three modules import it from there.

**Carry `dashboard_service`'s comment with it.** Its point is that the sent set and the
billable set are identical today and must not be bound together — a commercial change to
the billable set would silently rewrite the freshness figures the client schedules his
auctions against. A constant that arrives in a new file without that reasoning is one
"simplification" away from becoming `BILLABLE_STATUSES`.

And do not assert the tuple's contents in a test. Assert the property the constant exists
for — that the screens reading it agree. `CLAUDE.md`: a test that pins a literal fails on
the intended change and passes on the dangerous one.

### A3 — the composer

`app/templates/campaigns.html`, `_composer-script.html`, `_composer-upload.html`.

Remove, from the "Use an existing audience" tab:

- the `Category *` fieldset, the `categoryChoices` radiogroup and `categoryError`
- the `crossCategory` checkbox and its handler
- `loadCategories()`'s chip rendering and `selectCategory()`, including the line that
  auto-selects `category:<slug>` in the audience dropdown when a chip is clicked

Remove, from the "Upload a list" tab:

- the optional `uploadCategory` select and its help text. Hidden means hidden; the upload
  path passes `category_id=None` every time.

The audience `<select>` renders `list_summaries()` in order, pinned entry first. It is the
only control in that tab.

The summary panel's `Category` row (`campaigns.html:342`, `id="sumCategory"`) becomes an
**Audience** row showing the resolved audience label. `audience_label()` already produces
it and already never raises. Do not leave a row that reads `—` on every campaign.

**The tab labels stay.** "Upload a list" and "Use an existing audience" are still the two
things he does, and upload is still first and still the default.

### A4 — the category rule relaxes for list and `all` audiences

`campaign_builder.resolve_category()` today requires a real category or an explicitly
typed `cross_category_override` unless `list_audience=True`. Under this model a campaign
pointing at a list or at `all` has no category to give, and the UI no longer offers a way
to type an override.

Required: a `list:` or `all` audience resolves with `category_id=None` and
`cross_category_override=False`, and does not raise.

Two things must not happen:

- **A `category:` selector still requires a category or an override.** No UI path can
  produce one, and an API caller that hand-writes one is doing something deliberate.
- **`cross_category_override` is never written `1` by any path this session leaves
  standing.** The column stays; the escape hatch it recorded no longer exists, because
  there is no longer a rule to escape.

Note what is being given up and write it next to the change: `resolve_category()` existed
so "no category" could not become a value the form submits by accident. Under the new
model "no category" is the normal outcome for every campaign, so that guard now protects
nothing on the list path. Deleting a guard silently is how it comes back as a defect;
deleting it with the reason written down is a decision.

### A5 — the Contacts page

`app/templates/contacts.html`, `app/routers/contacts.py`.

Remove from the page: the "Filter by category" tablist, the bulk **Add to category** /
**Remove from category** controls and the `bulkCategory` select, the `addCategory` field on
the add-contact form, and the per-row category chips column.

The CSV import block stops requiring a category. It reads "Import a CSV into a new list",
takes a list name, and commits with `category_id=None`. The client-side guard at
`contacts.html:448` — `Choose the category this file belongs to` — comes out with it.

**The endpoints stay.** `/api/contacts/categories` and the bulk category routes are
retained and simply have no caller in the UI. Deleting them is schema-adjacent and this
session does not do it.

### A6 — the dashboard

`app/services/dashboard_service.py`, `app/routers/dashboard.py`, `app/templates/today.html`.

`category_cards()` becomes `list_cards()`. Same card shape, same grid, same rules:

- The pinned `⭐ ALL BIDDERS — MAIN LIST` card first, from A2's constant, then the **five
  most recent lists**. Five, because the grid was built for five and a dashboard that
  grows without bound stops being a dashboard.
- Per card: the list name, its active contact count, and days since that list was last
  actually texted.
- **`days_label` keeps the em-dash rule verbatim.** A list never texted shows `—`, not
  `0` — "0" reads as "texted today", the opposite of the truth, and that is why a whole
  niche never got picked under the old cards.
- The stale threshold and the red border behave exactly as they do now.
- The swatch comes off. There is no palette token on a list, and colour was never the
  identity channel anyway — the label was.

The section heading "Days since last send" is still correct and stays.

The hero: `next_up.category` chip → nothing; the "Category last texted" tile → **"List
last texted"**, computed the same way for the campaign's resolved audience. Its
`days_caption` fallback `no category` becomes wording that fits a list.

### A7 — no audience surface names a category, proved at runtime

A grep cannot prove this, and three separate times in this project a measurement script
has counted prose describing a rule as a violation of it. So this is a **runtime** test,
in the shape `tests/test_whitelabel.py` already established: render the routes and read
the rendered bodies.

- Discover client-facing GET routes from the app's own route table. `test_whitelabel.py`
  already has `_get_routes()` (line 119) and a `>= 15` sanity floor on it — reuse that,
  do not write a second discoverer, and do not pin a list of paths.
- Subtract the routes owned by **`app.routers.prospects`** — by module, not by path
  string. The prospect review queue keeps its category, and A8 says why.
- Assert that no remaining rendered body contains `category` or `categories`,
  case-insensitively.

The word must not survive in a rendered body, an option label, an error string or a JSON
field a template reads. Comments in source are not rendered and are not violations —
which is the whole reason this is a render test and not a grep.

### A8 — the prospect queue is deliberately untouched

`app/templates/prospects.html` and `app/routers/prospects.py` keep their category chip and
keep requiring a category to promote a prospect (`prospects.html:346`).

That is not an oversight and it is not an inconsistency to tidy up. The category model is
retained as the **prospecting taxonomy** — it is what P2's per-category radii and
`prospect_scoring.py` are keyed on. A prospect promoted into Food Service becomes a
contact tagged `food_service`, and it is reachable through `all` and through any list it
is later put on, exactly as before. The client's complaint was about where a list goes,
not about the review queue.

**Do not remove the category from the prospect screens in this session.** If you think it
should go, that is a product decision and a separate session.

---

## What is explicitly out of scope

- **Any migration that drops or renames a table, a column or an index.** Escalation item 8;
  the answer is in "The decision that governs everything below".
- Prospect scoring, the per-category radii, the taxonomy, and every P2 file.
- A lists management page — renaming, merging, archiving or deleting a list. Nothing in
  this session creates one and nothing in it needs one.
- A per-contact "which lists is this person on" column on the Contacts page. It is the
  right product answer and it is a new query on a paged screen; note it under "Found while
  working" and leave it.
- The `--s1`..`--s4` palette variables. Unused by the dashboard after this session, still
  used by the prospect screens, and adjusting or removing them is escalation item 9.
- Anything on `app/sms/`, the send loop, billing, the pre-flight capacity check or the
  suppression window.

---

## File list

Stay inside it.

```
app/models/contact_list.py
app/models/sms_message.py                 (SENT_STATUSES moves here, with its comment)
app/services/contact_service.py
app/services/dashboard_service.py
app/services/campaign_builder.py
app/services/report_service.py            (SENT_STATUSES import only)
app/services/history_service.py           (SENT_STATUSES import only)
app/routers/campaigns.py
app/routers/contacts.py
app/routers/dashboard.py
app/templates/campaigns.html
app/templates/_composer-script.html
app/templates/_composer-upload.html
app/templates/contacts.html
app/templates/today.html
alembic/versions/                          (one migration: A1's normalisation only)
tests/
agent/accept-5i.sh
agent/mutate-5i.py
```

`app/services/report_service.py` and `history_service.py` are in the list **for one
reason only**: they hold and import the second copy of `SENT_STATUSES`, and A2
consolidates it. Nothing else in either file is in scope.

Both already fall back from `category_label` to `audience_label`
(`campaign-report.html:116`), so a campaign with no category renders correctly today. If
that turns out to be wrong, it is a finding for `status.md`, not an edit.

---

## Acceptance criteria

`agent/accept-5i.sh` is the stop condition. Each criterion runs its own tests **in
isolation** — `accept-5e.sh` established this and it caught two tests that only passed
inside a full run, one of them passing on `0 == 0`.

1. **The clock has one writer.** `ContactList.created_at` carries no server default; both
   insert paths write the same spelling from the same clock; the migration converts a
   space-separated row from UTC to local and leaves a `T`-separated row alone. A test
   seeds one row of each spelling, runs the ordering, and asserts the order is the true
   chronological one — which the pre-migration string comparison gets wrong.
2. **`alembic upgrade head` succeeds from a clean database**, and the suite is green
   twice in a row.
3. **The picker is the new shape.** `list_summaries()` returns the pinned entry first,
   then lists newest first, and **no entry whose `kind` is `category`**.
4. **Historical selectors still resolve.** A campaign stored with
   `audience = "category:food_service"` still resolves to the right contacts and still
   renders its label. This is the criterion that stops a helpful cleanup from breaking
   every old report.
5. **A list campaign needs no category.** `POST /api/campaigns` with `audience=list:<id>`
   and no `category_id` and no override creates a draft. With `audience=all`, likewise.
   With `audience=category:<slug>` and neither, it still refuses.
6. **A7's render scan passes** across every client-facing GET route outside
   `app.routers.prospects`.
7. **The prospect queue is unchanged.** `/prospects` still renders its category chip and
   the promote flow still requires a category. Assert it, so the next session cannot
   remove it by accident.
8. **The em-dash rule survives.** A list never texted renders `—` and not `0`.
9. **`bash agent/gate.sh` green, twice.** Run it yourself before you stop.
10. **Behavioural mutation run.** `agent/mutate-5i.py`, on a scratch tree **verified
    byte-identical to the repo before the first patch**, printing `SCRATCH VERIFIED
    PRISTINE`. `agent/mutate-P1b.py` implements this check properly — copy that one, not
    the four earlier harnesses.

### Mutations the harness must include

Each one must be on a path something executes, and the tests that fail for it must be the
tests that **name** it. A mutation caught only by an unrelated test is not caught.

- Restore the `server_default` on `created_at`.
- Order lists by the raw string instead of the parsed value. **This must fail on the
  live data's own two spellings**, not on synthetic rows — a mutation that only breaks
  under a fixture nobody would write proves nothing about the defect being fixed.
- Have the migration convert a `T`-separated row as though it were UTC.
- Make `list_summaries()` emit category entries again.
- Delete the `category:` branch from `_term_ids_query()`. Criterion 4 must go red.
- Make `resolve_category()` raise on a `list:` audience again.
- Make `resolve_category()` accept a `category:` audience with no category and no
  override.
- Render `0` instead of `—` for a list never texted.
- Hard-code the pinned wording in a second place instead of reading the constant.
- Point the freshness query at `BILLABLE_STATUSES`.
- Take the prospects router out of A7's subtraction — the scan must then fail, which
  proves the scan is looking at that surface and the exception is doing real work.

---

## `/goal`

> Session 5i is complete when `bash agent/accept-5i.sh` exits 0 with every criterion
> printed and passing, `bash agent/gate.sh` is green on two consecutive runs, and
> `agent/mutate-5i.py` reports every mutation caught on a scratch tree it verified
> pristine. Turn cap 60. Do not declare completion from a summary — show the output.

---

## Review

**One synchronous fresh-context review, in session. No spawned reviewers** — three of four
produced nothing across a dozen idle cycles in 5g, and the one synchronous reviewer found
four real defects.

Work these lenses directly:

1. **The set before the guard, not just the guard.** 5e's top-up bug was a correct rule on
   a wrong candidate set. Here: what is in `list_summaries()`'s list set before it is
   sorted and sliced to five — every list ever created, including one-off uploads from
   months ago? Is "the five most recent" the five he would name?
2. **Two moments, one sentence-maker.** The pinned wording renders in the composer and on
   the dashboard. Prove they read it from the same constant rather than agreeing by
   coincidence.
3. **A property proved of a helper is not proved of its caller.** If the ordering test
   goes through the sort helper rather than through `/api/campaigns/audiences`, the
   endpoint can be reverted with the suite green. Test the thing that tells the client.
4. **What else arrives on this path.** The freshness join now runs over
   `contact_list_members`, which has its own UTC/local defect on `added_at`. This session
   does not read `added_at` — confirm that, and if something does, say so rather than
   fixing it across a module boundary.
5. **What the fallback answers.** A list whose contacts were all deleted, a list with zero
   members, a database with no lists at all: what does the picker show, and does the
   composer's preview say something true about it?
6. **The scan's own first version.** Re-run A7's render scan against a case you know the
   answer to — a route you deliberately break — before quoting it as evidence. A green
   check whose script is broken is worse than no check.

---

## Part B — Jordan's

**One check, before the migration is trusted on production.** A1's migration classifies a
row by its format: space-separated means the server default wrote it, means UTC, means
convert. That holds because `CURRENT_TIMESTAMP` cannot emit a `T` and `isoformat()` cannot
emit a space — but it has only been confirmed against the local database's two rows.

Dump what production actually holds:

```
ssh -i ~/.ssh/a4a_deploy appuser@67.205.180.62 \
  "cd /home/appuser/app && python3 -c \"
import sqlite3
c = sqlite3.connect('data/app.db')
for r in c.execute('select id, name, created_at from contact_lists order by id'):
    print(r)
\""
```

Every value should be either `YYYY-MM-DD HH:MM:SS` or `YYYY-MM-DDTHH:MM:SS.ffffff`. **A
third spelling means the classification is not 1:1 and the migration is wrong** — stop and
escalate rather than converting.

## Deploy

Part A is this session. The deploy is Jordan's, as every session since 5b. Note in
`status.md` that the migration in A1 touches a live table's data and that a
`scripts/backup.sh` run comes before it.
