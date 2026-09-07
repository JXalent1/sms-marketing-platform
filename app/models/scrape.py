"""Scrape jobs and the line-type cache.

Two tables that belong together: one records a run, the other records what that
run learned about a phone number and never has to learn again.

## `scrape_jobs`

A job records what it attempted, what it produced, and what it cost. All three,
because a discovery run that returns nothing is not distinguishable from one
that never ran unless the attempt is written down, and a run that costs money is
not reviewable unless the spend is.

`cleanup_ran` is a column rather than a log line on purpose. The reference
system leaked one browser driver process per daily scrape — 17 orphans and
1.6 GB RSS on a 3.9 GB box — and the reason nobody noticed for months is that
"the cleanup ran" was something you could only establish by reading a journal
nobody read. A timed-out job with `cleanup_ran = 0` is a bug you can query for.

`cost` is a string holding a Decimal, the same shape as `sms_messages`'s carrier
cost columns and for the same reason: a Float column inserts a binary expansion
between the number we were charged and the number we report.

**It is OUR spend, not the client's.** He is billed per segment and nothing
else; screening a number costs us about a quarter of a cent and appears on no
invoice of his. It must not reach a response body, a template or an export, on
the same rule as `WHOLESALE_COST_PER_SEGMENT`. `agent/accept-P1.sh` asserts it
structurally rather than trusting this comment.

## `phone_lookups`

The cache that makes scraping economic. A carrier line-type lookup costs about
$0.0025; screening 10,000 numbers is $25 once, or $25 every time somebody
re-runs a search if there is no cache. Keyed on E.164 and uniquely indexed —
that constraint *is* the "look it up once" guarantee, exactly as
`ix_contacts_phone` is the dedup guarantee.

`status` separates an answer from a failure, and the distinction is load
bearing. An answered lookup is final and is never repeated. A failed one is not
an answer, and caching it as though it were would turn one carrier outage into a
permanent hole in the list — every number screened during it filed as `unknown`
forever, and `unknown` is not promote-eligible. So a failure records itself,
counts its attempts, and stays retryable.

The values `line_type` may hold are defined once, in `app/sms/lookup.py`
(`LINE_TYPES`) — the layer that talks to the carrier owns the vocabulary the
carrier answers in. `app/services/lookup_service.py` is the only writer of this
table and the only place that validates against it.

`error` is stored already scrubbed. It is carrier free text, and the one time
this codebase let carrier free text into a column at volume it turned up on the
client's Opt-outs page. Nothing renders this column today; storing it clean
means nothing has to remember not to.

`scrape_jobs.error` is **not** scrubbed, and that is a deliberate difference:
it holds a source's own exception, which is a developer's diagnostic, and there
is no jobs screen. If one is ever built, that column and the two `cost` columns
are what must not be on it.
"""

from sqlalchemy import Column, Integer, String, Text, Index, JSON
from app.core.database import Base

# `running` is the row a job writes before it does anything, so a process killed
# mid-run leaves evidence rather than nothing. `timed_out` is separate from
# `failed` because they call for different responses: a failure is a bug in the
# source, a timeout is a source that is slower than its budget, and folding them
# together is how the second one gets debugged as the first.
JOB_STATUSES = ("running", "completed", "failed", "timed_out")

LOOKUP_STATUSES = ("ok", "error")


class ScrapeJob(Base):
    __tablename__ = "scrape_jobs"

    id = Column(Integer, primary_key=True, index=True)

    # ─── What it attempted ──────────────────────────────────────────────────
    source = Column(String(50), nullable=False)
    search_term = Column(String(255), nullable=True)
    parameters = Column(JSON, nullable=True)

    status = Column(String(20), nullable=False, default="running")
    started_at = Column(String(50), nullable=False)
    finished_at = Column(String(50), nullable=True)

    # ─── What it produced ───────────────────────────────────────────────────
    records_yielded = Column(Integer, nullable=False, default=0)
    prospects_created = Column(Integer, nullable=False, default=0)
    prospects_corroborated = Column(Integer, nullable=False, default=0)
    # Records the source produced that a permanent rejection already covered.
    # Counted rather than ignored: a search term whose output is mostly numbers
    # somebody has already said no to is a term that should be retired, and that
    # is invisible if suppression is silent.
    records_suppressed = Column(Integer, nullable=False, default=0)
    # Businesses the never-prospect list stopped by name — another auction
    # house, an estate liquidator, an appraiser. Counted apart from every other
    # kind of nothing because the remedy is different: a term whose output is
    # mostly competitors is a term to retire, and that is invisible if it is
    # folded into `records_invalid`.
    records_excluded = Column(Integer, nullable=True, default=0)
    # Numbers the client already has as contacts. Also its own counter, and for
    # the opposite reason: a high count here is a search working correctly on a
    # niche already covered, not a search finding rubbish.
    records_known = Column(Integer, nullable=True, default=0)
    # No usable phone, or no buyer rationale. Both are the source's bug.
    records_invalid = Column(Integer, nullable=False, default=0)

    # ─── What it cost ───────────────────────────────────────────────────────
    # **Two meters, and they are independent, because they are two bills.**
    # `lookups_*` and `cost` are the carrier line-type spend. `api_*` is the
    # discovery API's, which is charged per *request* rather than per number —
    # one request returns up to twenty businesses. Summing them into one column
    # would be the overloaded-value mistake this file's neighbours keep paying
    # for: the two have different units, different ceilings and different
    # remedies when one runs out.
    lookups_performed = Column(Integer, nullable=False, default=0)
    lookups_cached = Column(Integer, nullable=False, default=0)
    cost = Column(String(20), nullable=True)          # Decimal as text. Ours.

    api_requests = Column(Integer, nullable=True, default=0)
    # Requests the monthly cap refused. A column rather than only a log line,
    # for `cleanup_ran`'s reason: "the cap stopped this run early" has to be
    # something you can query for, not something you establish by reading a
    # journal nobody reads. A job with records and a non-zero count here is a
    # search that is incomplete rather than exhausted.
    api_requests_skipped = Column(Integer, nullable=True, default=0)
    api_cost = Column(String(20), nullable=True)      # Decimal as text. Ours.
    #
    # The four counters above this line are `nullable=True` with a Python-side
    # default while the four older ones are `nullable=False`, and the difference
    # is the table's history rather than a distinction in meaning. They were
    # added to an existing table by `e7c05b3a1d94`, and making a new SQLite
    # column NOT NULL means either a server default — the second writer 5i spent
    # a migration removing — or a table rebuild, which is escalation item 8.
    # Every reader therefore treats NULL as 0, because a raw INSERT naming none
    # of them can still produce one.

    cleanup_ran = Column(Integer, nullable=False, default=0)
    error = Column(Text, nullable=True)

    __table_args__ = (
        Index("idx_scrape_jobs_started", "started_at"),
        Index("idx_scrape_jobs_status", "status"),
    )


class PhoneLookup(Base):
    __tablename__ = "phone_lookups"

    id = Column(Integer, primary_key=True, index=True)
    phone = Column(String(20), unique=True, nullable=False, index=True)

    line_type = Column(String(20), nullable=False)    # app/sms/lookup.py LINE_TYPES
    status = Column(String(10), nullable=False)       # LOOKUP_STATUSES
    provider = Column(String(50), nullable=True)
    error = Column(Text, nullable=True)               # scrubbed at write

    attempts = Column(Integer, nullable=False, default=1)
    looked_up_at = Column(String(50), nullable=False)
    cost = Column(String(20), nullable=True)          # Decimal as text. Ours.

    __table_args__ = (
        # "Which of these numbers are mobile" — the queue's join and the
        # screening pass's batch read.
        Index("idx_phone_lookups_line_type", "line_type"),
    )
