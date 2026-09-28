#!/usr/bin/env python3
"""Run named prospect searches from the box. Dry run first, always.

    python -m scripts.prospect_run --groups lighting_retail,lighting_trade --dry-run
    python -m scripts.prospect_run --groups lighting_retail,lighting_trade --live

The only interface to `scrape_runner.run_plan()` (session LT1). A script rather
than a screen for the reason `cost_report.py` gives: every figure it prints is
**our** spend — Google requests and line-type lookups — and the admin login is
the client, so "operator-only" has to mean "not served".

**`--groups` is required and has no default.** A bare invocation must not be
able to run the whole taxonomy: hundreds of searches, and explicit-only groups
such as `lighting_decor` are reachable only by naming them.

**`--dry-run` makes no network call.** It reads the plan, the search ledger and
both meters, and prints what the run would cost at worst. Every figure comes
from config or from those tables; nothing here restates a price.

**`--live` refuses before it spends** if there is no Places key or no working
line-type screening. The second is the one that costs money quietly: a run that
cannot screen still pays for every request, then leaves every number `unknown`
— which promotes nobody — and the review queue fills with rows nobody can act
on. A configured provider that fell back to the disabled one is refused as
well, because a check that reads the setting instead of the result asks the
fallback whether it is healthy (CLAUDE.md, the pre-flight lesson).

**Output never names the carrier.** It is for Jordan, and it is still on a
screen: every log line on the way out goes through `scrub_provider_text()`,
and an exception is reported as one scrubbed sentence, never `str(e)` raw.
"""

import argparse
import logging
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings                              # noqa: E402
from app.core.database import SessionLocal                        # noqa: E402
from app.models.prospect import Prospect, ProspectSighting        # noqa: E402
from app.models.scrape import ScrapeJob                           # noqa: E402
from app.services import api_budget, lookup_service, scrape_runner  # noqa: E402
from app.sms.lookup import (DisabledLineTypeProvider, cost_per_lookup,     # noqa: E402
                            get_lookup_provider)
from app.sms.phone import scrub_provider_text                     # noqa: E402
from app.sources import taxonomy                                  # noqa: E402
from app.sources.google_places import GooglePlacesSource          # noqa: E402

logger = logging.getLogger("prospects")

LINE_TYPES_SHOWN = ("mobile", "voip", "landline", "unknown")


def _money(value) -> str:
    return f"${Decimal(value):,.2f}"


def _group_slugs(value: str) -> list:
    slugs = [s.strip() for s in value.split(",") if s.strip()]
    if not slugs:
        raise argparse.ArgumentTypeError("name at least one group")
    for slug in slugs:
        try:
            taxonomy.group(slug)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(str(exc))
    return slugs


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="python -m scripts.prospect_run",
        description="Run named prospect searches. --dry-run first.")
    parser.add_argument("--groups", required=True, type=_group_slugs,
                        help="comma-separated term-group slugs (no default)")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true",
                      help="print the plan and its worst-case cost; no request")
    mode.add_argument("--live", action="store_true",
                      help="run the searches and screen what they find")
    return parser.parse_args(argv)


# ─── The dry run ────────────────────────────────────────────────────────────

def plan_figures(db, group_slugs) -> dict:
    """Everything the dry run prints, read from config and the two meters.

    The Places cost goes through `RequestBudget.charged` rather than a second
    copy of its arithmetic, so the free allowance already used this month is
    applied the way the run itself will apply it — one definition of a cost,
    not two that agree today.
    """
    source_name = GooglePlacesSource.name
    plan = list(taxonomy.searches(group_slugs=group_slugs))
    already = scrape_runner.searched_recently(db, source_name)
    to_run = sum(1 for s in plan if s.ledger_key not in already)
    pages = max(1, settings.GOOGLE_PLACES_MAX_PAGES)   # the source's own floor
    worst = to_run * pages

    budget = api_budget.RequestBudget.for_month(db, source_name)
    room = max(0, budget.cap - budget.spent)
    possible = min(worst, room)
    budget.charge(possible)
    capped_cost = budget.charged

    uncapped = api_budget.RequestBudget.for_month(db, source_name)
    uncapped.charge(worst)

    lookup_cap = lookup_service.monthly_cap()
    lookup_spent = lookup_service.spend_this_month(db)
    lookup_left = max(Decimal("0"), lookup_cap - lookup_spent)
    per_number = cost_per_lookup()
    numbers = possible * settings.GOOGLE_PLACES_PAGE_SIZE
    lookup_worst = min(lookup_left, per_number * numbers)

    return {
        "planned": len(plan), "already": len(plan) - to_run, "to_run": to_run,
        "pages": pages, "worst_requests": worst,
        "used": budget.opening, "cap": budget.cap, "room": room,
        "possible": possible, "free": budget.free_per_month,
        "cost": capped_cost, "cost_uncapped": uncapped.charged,
        "lookup_cap": lookup_cap, "lookup_spent": lookup_spent,
        "lookup_left": lookup_left, "per_number": per_number,
        "numbers": numbers, "lookup_worst": lookup_worst,
    }


def print_dry_run(f: dict, group_slugs, out) -> None:
    w = lambda line="": print(line, file=out)            # noqa: E731
    w("Prospect run: DRY RUN. No request is made.")
    w(f"Groups: {', '.join(group_slugs)}")
    w(f"Searches planned: {f['planned']:,}")
    w(f"  already searched in the last {settings.GOOGLE_PLACES_QUERY_REPEAT_DAYS}"
      f" days (skipped, free): {f['already']:,}")
    w(f"  to run: {f['to_run']:,}")
    w(f"Worst-case Places requests: {f['worst_requests']:,} "
      f"({f['to_run']:,} searches x {f['pages']} pages, GOOGLE_PLACES_MAX_PAGES)")
    w(f"Places requests already used this month: {f['used']:,}")
    w(f"GOOGLE_PLACES_MONTHLY_REQUEST_CAP: {f['cap']:,} "
      f"({f['room']:,} left this month)")
    if f["worst_requests"] > f["room"]:
        w(f"  The cap is below the worst case: the run stops after "
          f"{f['possible']:,} requests and the rest wait for next month.")
    w(f"Free Places requests per month: {f['free']:,}")
    w(f"Worst-case Places cost beyond the free allowance: {_money(f['cost'])} "
      f"(at most {f['possible']:,} requests; the whole plan uncapped would be "
      f"{_money(f['cost_uncapped'])})")
    w(f"PROSPECT_LOOKUP_MONTHLY_CAP: {_money(f['lookup_cap'])}; spent this "
      f"month: {_money(f['lookup_spent'])}; left: {_money(f['lookup_left'])}")
    w(f"Worst-case lookup spend: {_money(f['lookup_worst'])} (up to "
      f"{f['numbers']:,} numbers at ${f['per_number']} each, never past the cap)")
    if f["per_number"] * f["numbers"] > f["lookup_left"]:
        w("  The lookup cap is below the worst case: numbers past it stay "
          "unscreened, and unpromotable, until PROSPECT_LOOKUP_MONTHLY_CAP is raised.")
    w("Most searches return fewer than the maximum pages; these are ceilings.")


# ─── The live run ───────────────────────────────────────────────────────────

def live_refusal(provider=None, db=None):
    """One line naming the missing setting, or None when the run may spend.

    The lookup cap is checked here as well as per call, because a run with no
    screening room still pays Places for every request, leaves every number
    `unknown`, and the completed searches then sit in the repeat ledger — so
    the only way to screen them later would be to pay for them again.
    """
    if not (settings.GOOGLE_PLACES_API_KEY or "").strip():
        return "Refusing --live: GOOGLE_PLACES_API_KEY is not set."
    if (settings.PROSPECT_LOOKUP_PROVIDER or "none").strip().lower() == "none":
        return ("Refusing --live: PROSPECT_LOOKUP_PROVIDER is none, so nothing "
                "found could be screened or promoted.")
    provider = provider or get_lookup_provider()
    if isinstance(provider, DisabledLineTypeProvider):
        return ("Refusing --live: PROSPECT_LOOKUP_PROVIDER is set but screening "
                "did not start. Check its credential; the log line above says why.")
    if db is not None and not lookup_service.LookupBudget.for_month(db).allows(
            cost_per_lookup()):
        return ("Refusing --live: PROSPECT_LOOKUP_MONTHLY_CAP has no room left "
                "this month, so nothing found could be screened or promoted.")
    return None


def line_type_breakdown(db, job_ids) -> dict:
    """Line types of the pending prospects this run's jobs sighted.

    Pending only: a store the client already promoted or rejected is not
    something he can act on today, and "promotable" has to mean promotable now.
    """
    counts = {name: 0 for name in LINE_TYPES_SHOWN}
    if not job_ids:
        return counts
    phones = [row[0] for row in
              db.query(Prospect.phone)
              .join(ProspectSighting, ProspectSighting.prospect_id == Prospect.id)
              .filter(ProspectSighting.job_id.in_(job_ids),
                      Prospect.status == "pending")
              .distinct().all()]
    known = lookup_service.cached_line_types(db, phones)
    for phone in phones:
        kind = known.get(phone, "unknown")
        counts[kind] = counts.get(kind, 0) + 1
    return counts


def print_live(db, summary: dict, out) -> None:
    w = lambda line="": print(line, file=out)            # noqa: E731
    jobs = (db.query(ScrapeJob).filter(ScrapeJob.id.in_(summary["jobs"])).all()
            if summary["jobs"] else [])
    places_cost = sum((Decimal(j.api_cost) for j in jobs if j.api_cost), Decimal("0"))
    lookup_cost = sum((Decimal(j.cost) for j in jobs if j.cost), Decimal("0"))
    performed = sum(j.lookups_performed or 0 for j in jobs)
    cached = sum(j.lookups_cached or 0 for j in jobs)

    w(f"Searches planned: {summary['planned']:,}; run: {summary['ran']:,}; "
      f"already searched: {summary['skipped_recent']:,}; "
      f"stopped by the request cap: {summary['skipped_cap']:,}")
    w(f"Prospects queued: {summary['created']:,} new; "
      f"{summary['corroborated']:,} found again; "
      f"{summary['excluded']:,} excluded; {summary['known']:,} already contacts")
    w(f"Places requests: {summary['requests']:,}; cost beyond the free "
      f"allowance: {_money(places_cost)}")
    w(f"Lookups: {performed:,} paid, {cached:,} from cache; cost: {_money(lookup_cost)}")

    counts = line_type_breakdown(db, summary["jobs"])
    w("Line types found: " + "; ".join(f"{k} {v:,}" for k, v in counts.items()))
    promotable = sum(counts.get(t, 0) for t in lookup_service.PROMOTABLE_LINE_TYPES)
    w(f"Promotable now: {promotable:,} "
      f"({' + '.join(lookup_service.PROMOTABLE_LINE_TYPES)})")
    if summary["skipped_cap"]:
        w("The monthly request cap stopped the plan. The rest run next month, or "
          "sooner if GOOGLE_PLACES_MONTHLY_REQUEST_CAP is raised.")


def main(argv=None, *, source_factory=None, provider=None, out=None) -> int:
    out = out or sys.stdout
    args = parse_args(argv)
    db = SessionLocal()
    try:
        if args.dry_run:
            print_dry_run(plan_figures(db, args.groups), args.groups, out)
            return 0

        refusal = live_refusal(provider, db)
        if refusal:
            print(refusal, file=out)
            return 2
        try:
            summary = scrape_runner.run_plan(
                db, source_factory=source_factory, group_slugs=args.groups,
                provider=provider)
        except KeyboardInterrupt:
            # Caught so Python's own traceback — file paths and all, through a
            # carrier SDK if it lands mid-lookup — never reaches the screen.
            print("Interrupted. Searches already run are saved and a re-run "
                  "skips them.", file=out)
            return 130
        except Exception as exc:                      # noqa: BLE001
            # One scrubbed sentence on screen; the traceback goes to the log
            # through the same scrubbing formatter. Jobs that finished are
            # committed, and a re-run skips them.
            logger.error("prospect run stopped", exc_info=True)
            print("The run stopped early: " + scrub_provider_text(
                f"{type(exc).__name__}: {exc}") + ". Searches already run are "
                "saved and a re-run skips them.", file=out)
            return 1
        print_live(db, summary, out)
        return 0
    finally:
        db.close()


class ScrubbingFormatter(logging.Formatter):
    """Scrubs the whole formatted record, traceback included, not just the message."""

    def format(self, record) -> str:
        return scrub_provider_text(super().format(record))


def configure_logging() -> None:
    """Progress on stderr, scrubbed. Only when run as a command, never in tests."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(ScrubbingFormatter("%(asctime)s %(levelname)s %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.WARNING)
    logger.setLevel(logging.INFO)


if __name__ == "__main__":
    configure_logging()
    sys.exit(main())
