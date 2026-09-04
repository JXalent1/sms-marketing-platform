# Session 5j — the index migration, the cost guard, and list archive/rename

**Module:** 5j · **Depends on:** 5i (deployed 2026-09-04) · **Ahead of:** P2

Forced into existence by deploy day. Two things happened that the client cannot live
with and cannot fix himself, and both are about the list picker being the product now
rather than a corner of it.

---

## Prerequisites

- Read `CLAUDE.md`, `RULES.md`, `modules.md`, `status.md` — **including the incident entry
  at the end of `status.md`** — and every file in `decisions/`.
- 5i is merged and deployed. **577 tests, gate green twice.**
- Run inside the project venv, or `PATH="$PWD/.venv/bin:$PATH" bash agent/gate.sh`. A
  system or conda interpreter dies at collection and reports "test suite is red" about the
  wrong Python.
- `npm run build:css` before serving anything.
- **Production is in a state no migration describes.** See A1. Read it before writing a
  single line of the migration.

---

## Why this session exists

### 1. Production's schema leads the migration history by two indexes

5i's freshness join took **10 minutes 2 seconds** on production and under a millisecond in
the suite. `_last_sent_by_list()` joins `contact_list_members` to `sms_messages` on
`contact_id` and neither side was indexed on that column. Two indexes were created **by
hand on the live database** on 2026-09-04 to end the outage:

```sql
CREATE INDEX IF NOT EXISTS ix_sms_messages_contact_id ON sms_messages(contact_id);
CREATE INDEX IF NOT EXISTS ix_clm_contact_id          ON contact_list_members(contact_id);
ANALYZE;
```

10m02s → 0.95s. Those indexes exist on the box and in no migration and in no model.

**This is the trap that makes A1 the first requirement.** A bare `op.create_index()` in
any future migration raises on the live database, and `deployment/deploy.sh` aborts the
deploy on a failed migration without restarting the service. The fix for the outage would
become the thing that cannot ship.

### 2. Nothing in the product can rename or hide a list

Every test upload becomes a permanent dropdown entry. Ten pieces of debris — one and two
member lists from a single evening — were deleted by hand on the live box on 2026-09-04,
which is Jordan doing something the client has no way to do himself.

Nineteen of the twenty-one lists were never used by a campaign, so there is no campaign
name for most of them to inherit; the Williamson model's "titled by the campaign that
first used it" cannot be applied retroactively here. The client has to name them himself.
This is the tool that lets him.

---

## What is in scope

### A1 — carry the two indexes into a migration, on a database that already has them

Write one migration that converges the live box and a fresh database on the same schema.

**Use the project's own naming convention, and drop the hand-made names.** The existing
convention is `idx_<table-ish>_<column>` — `idx_member_list`, `idx_sms_campaign`,
`idx_sms_status`, `idx_sms_sent_at`. The two hand-made indexes were named
`ix_sms_messages_contact_id` and `ix_clm_contact_id` under pressure and do not match.
Converge on `idx_sms_contact` and `idx_member_contact`.

So the migration, in order, for each of the two:

1. Inspect the live index list. **Use SQLAlchemy's inspector, not `IF NOT EXISTS`** —
   that spelling is SQLite-specific and this project is Postgres-ready through
   `DATABASE_URL`. `sa.inspect(op.get_bind()).get_indexes(table)` is the portable check.
2. Create the convention-named index if it is absent.
3. Drop the hand-made one if it is present.

Order matters and the reason goes in the docstring: **create before drop**, so there is no
instant at which the join is unindexed. The window is milliseconds either way and the box
is live during business hours; do it in the order that has no window at all.

`downgrade()` drops the convention-named indexes if present and does not recreate the
hand-made ones. They were an incident response, not a schema.

**Declare both indexes on the models too**, in `__table_args__` beside their neighbours —
`Index("idx_sms_contact", "contact_id")` on `SMSMessage`, `Index("idx_member_contact",
"contact_id")` on `ContactListMember`. A fresh test database is built by
`create_all()`, so without the declaration the suite runs unindexed and A2's guard passes
against a schema production does not have.

### A2 — a guard that fails on cost, not on behaviour

This project has no check that a query is affordable. The gate does not time anything, and
the mutation harness proves behaviour and is silent about how long the behaviour takes.
That is why a ten-minute query shipped green.

Add an `EXPLAIN QUERY PLAN` assertion over the freshness join: **no full scan of
`sms_messages` or `contact_list_members`.** Requirements on how it is built:

- **Explain the statement the service actually issues.** Do not hand-write a copy of the
  query in the test. 5f's lesson — a property proved of a helper is not proved of its only
  caller — applies exactly: a test that explains its own SQL passes while the service's SQL
  degrades. Capture the compiled statement from `_last_sent_by_list()` itself, either by
  exposing the query object or by capturing it with a SQLAlchemy `before_cursor_execute`
  event listener.
- **Skip cleanly on a non-SQLite bind**, naming why in the skip reason. `EXPLAIN QUERY
  PLAN` output is SQLite's; a Postgres box needs `EXPLAIN` and a different assertion, and a
  test that silently passes on the wrong engine is worse than one that says it did not run.
- **Do not assert a wall-clock time.** A timing threshold is flaky on shared CI and turns
  into a skipped test within a month. The plan is the durable statement of the property.

Also fix the docstring while you are in there: `_last_sent_by_list()` says the join is
"messages → contacts → memberships" and the code joins two tables, not three. There is no
`Contact` in that query. A docstring describing a join that is not the join is how the next
person reasons about the wrong index.

### A3 — `archived`, with the same ruling categories got

Add `archived` to `contact_lists`: additive, nullable, default 0. Migration is trivially
reversible, so it is not escalation item 8.

**Hidden from the picker, still resolving for history.** This is decision-for-decision the
same shape as the category ruling, and for the same reason:

- `list_summaries()` excludes archived lists. `dashboard_service.list_cards()` inherits
  that, because it is built from `list_summaries()` and must stay that way.
- `resolve_audience()`, `_term_ids_query()`, `_term_label()` and `audience_count()` **all
  keep resolving an archived list**. A campaign that targeted list 20 must still render its
  name in history and in its report. Archiving a list the client already sent to must not
  turn a report label into the raw string `list:20`.

There is a test for that and it is criterion 4.

### A4 — rename

`PATCH /api/lists/{id}` taking a new name.

`contact_lists.name` is `unique=True, nullable=False`, so a collision must come back as a
**400 carrying a sentence that says which name is taken**, not a 500 from an
`IntegrityError`. Validate before writing.

**A rename retroactively changes what old reports say, and that is correct.** `_term_label()`
looks the name up live rather than snapshotting it, so renaming "General Merchandise —
2026-08-21 upload" to "Main bidder import" changes every screen that ever referenced it.
That is the whole point — the bad names are the problem being solved. Write that in the
endpoint's docstring so nobody "fixes" it later by freezing the label at send time.

### A5 — the controls, where he picks an audience

The composer is where the picker is, so that is where the tool goes. A small panel reached
from beside the audience dropdown, listing every list — archived ones included, marked as
such — with rename and archive/unarchive per row.

Keep it in `campaigns.html` plus one partial. This is not a new page, a new nav entry, or a
new screen in the design; it is the affordance the picker was missing.

Show the same numbers the picker shows, from `list_summaries()`, so the panel and the
dropdown cannot disagree about a count. An archived list needs its own read — decide where
that lives and say why in a comment.

### A6 — make `DELETE /api/lists/{id}` refuse a list a campaign used

It exists, it has no caller, and it hard-deletes. Under this session's model, archive is
what "remove it from my dropdown" means, and delete is for a list nothing has ever
referenced.

Refuse with a 409 when any campaign's audience selector names the list, and say in the
message that archiving is what he wants. Deleting a referenced list degrades that
campaign's report label to `list:20` — the same class of loss decision 007's clause 2 is
about, arriving through a different door.

---

## What is explicitly out of scope

- **Merging two lists.** It is the obvious next request and it is a different problem:
  membership provenance (`created_contact`, `created_tag`) makes a merge irreversible in a
  way an archive is not.
- **The `categories` column in `/api/contacts/export.csv`** — residual 1 in `modules.md`.
  It is a `contact_query_service.py` change and it deserves its own attention, not a
  drive-by inside a session about indexes.
- Renaming a list from anywhere other than A5's panel.
- Every P2 file. Prospect scoring, the taxonomy, the radii.
- The send path, billing, the pre-flight capacity check, the suppression window.
- `app/services/campaign_service.py` **is at exactly 500 lines.** If this session needs to
  touch it, the split comes first and it comes as its own commit.

---

## File list

```
app/models/contact_list.py
app/models/sms_message.py
app/services/contact_service.py
app/services/dashboard_service.py
app/routers/contacts.py
app/templates/campaigns.html
app/templates/_composer-lists.html          (new — A5's panel)
app/templates/_composer-script.html
alembic/versions/                            (two migrations: A1's indexes, A3's column)
tests/
agent/accept-5j.sh
agent/mutate-5j.py
```

Two migrations, not one. The index convergence and the `archived` column are unrelated
changes and a reviewer should be able to revert either without the other.

---

## Acceptance criteria

`agent/accept-5j.sh` is the stop condition. Each criterion runs its own tests **in
isolation** — `accept-5e.sh` established this after two tests were found that only passed
inside a full run, one of them on `0 == 0`.

1. **The index migration is idempotent against a database that already has the hand-made
   indexes.** Seed a scratch copy with `ix_sms_messages_contact_id` and `ix_clm_contact_id`,
   stamp it at `f4a1c7d90e52`, run `upgrade head`, and assert: both convention-named indexes
   exist, both hand-made names are gone, and the run did not raise. Then run `upgrade head`
   again on the result and assert it is still clean.
2. **The same migration works on a database that has neither**, which is every fresh clone.
3. **`alembic upgrade head` from a clean database succeeds**, and `downgrade` of both new
   migrations succeeds and is followed by a clean `upgrade head`.
4. **An archived list still resolves.** Archive a list a campaign targeted; its campaign
   still renders the list's name in history and in its report, and `resolve_audience()`
   still returns its contacts. This is the criterion that stops a helpful cleanup from
   breaking every report that names a list.
5. **An archived list is not in the picker** — absent from `/api/campaigns/audiences`,
   `/api/lists` and the dashboard cards.
6. **A rename lands everywhere at once.** Rename a list a campaign used; the campaign's
   report label reads the new name. A rename to a taken name returns 400 with the
   conflicting name in the message, not a 500.
7. **`DELETE` refuses a referenced list with 409** and still deletes an unreferenced one.
8. **The cost guard fails on an unindexed schema.** Drop `idx_member_contact` in a scratch
   database and assert A2's test goes red. A guard that passes against the schema it is
   meant to reject is the third green-light-wired-to-nothing in this project.
9. **`bash agent/gate.sh` green, twice.** Run it yourself before you stop.
10. **Behavioural mutation run.** `agent/mutate-5j.py`, on a scratch tree **verified
    byte-identical to the repo before the first patch**, printing `SCRATCH VERIFIED
    PRISTINE`. Copy `agent/mutate-P1b.py`, which implements that check properly.

### Mutations the harness must include

- Drop the inspector check and call `op.create_index()` bare — criterion 1 must go red.
- Reverse A1's order so the hand-made index is dropped before the new one is created.
- Omit the `Index(...)` declarations from the models — criterion 8's scratch case must
  notice that a fresh database is unindexed.
- Make `list_summaries()` include archived lists.
- Make `_term_ids_query()` exclude archived lists — criterion 4 must go red.
- Make `list_cards()` query lists directly instead of going through `list_summaries()`,
  so the panel and the dropdown can disagree.
- Have A2's guard explain a hand-written copy of the query instead of the service's own
  statement, then degrade the service's query. The guard must fail to notice, proving the
  guard is worth building the harder way.
- Let the rename write without checking for a collision.
- Let `DELETE` remove a referenced list.

---

## `/goal`

> Session 5j is complete when `bash agent/accept-5j.sh` exits 0 with every criterion
> printed and passing, `bash agent/gate.sh` is green on two consecutive runs, and
> `agent/mutate-5j.py` reports every mutation caught on a scratch tree it verified
> pristine. Turn cap 60. Do not declare completion from a summary — show the output.

---

## Review

**One synchronous fresh-context review, in session. No spawned reviewers.** Lenses:

1. **What does this cost at production scale?** The lens whose absence caused the incident.
   For every query this session adds or changes, name the row counts it runs against on the
   live box — roughly 9,700 contacts, 30,000 messages, 15,500 memberships, 11 lists — and
   say which index serves it. A query with no answer to that question is not finished.
2. **The set before the guard.** A3 filters archived lists out of the picker. What is in
   the set before the filter, and does anything else read that set expecting everything?
3. **What else arrives on this path.** `DELETE` now refuses referenced lists. What counts
   as "referenced" — only `list:<id>` as a whole term, or does a compound selector like
   `category:estates&list:12` also name it? Check `_split_terms()` rather than assuming.
4. **Two moments, one sentence-maker.** The archived state is described in the picker's
   absence and in A5's panel. One definition of "archived", read by both.
5. **The scan's own first version.** Run A2's guard against a schema you know is unindexed
   before quoting it as evidence.

---

## Part B — Jordan's

After deploy, confirm the box converged rather than accumulated:

```
ssh -i ~/.ssh/a4a_deploy appuser@67.205.180.62 'cd /home/appuser/app && ./venv/bin/python -c "
import sqlite3
c = sqlite3.connect(\"data/app.db\")
for t in (\"sms_messages\", \"contact_list_members\"):
    print(t, [r[1] for r in c.execute(\"PRAGMA index_list(%s)\" % t)])
"'
```

Expect `idx_sms_contact` and `idx_member_contact` present, and
`ix_sms_messages_contact_id` and `ix_clm_contact_id` gone. Four indexes where there should
be two means the migration created rather than converged, and the duplicates cost write
time on every send.

## Deploy

Part A is this session. The deploy is Jordan's. `deployment/deploy.sh` runs
`scripts/backup.sh` immediately before `alembic upgrade head` and aborts without
restarting if the migration fails — which is the behaviour A1 exists to stay on the right
side of.
