#!/usr/bin/env python3
"""What the carrier actually charged, against what we reserved. OURS, not his.

    python scripts/cost_report.py                # the last 20 campaigns
    python scripts/cost_report.py 47             # one campaign
    python scripts/cost_report.py --limit 100

`campaigns.estimated_cost` has said since the skeleton that it exists so the
damage can be "reconciled against the invoice afterwards". Session 5f captured
the other half — the per-message cost and its rate/carrier-fee split — and this
is the reader.

**Deliberately a script and not a screen.** The admin login *is* the client, so
"operator-only" cannot mean "behind auth"; it has to mean "not served". Every
figure below is our wholesale cost. Showing it to him discloses our margin and
under-states his bill by roughly 40% — the leak session 1b removed and CLAUDE.md
still names. His report is at /history/<id> and is denominated in
BILLING_PRICE_PER_SEGMENT.

Read `coverage` before anything else. Most carriers price at delivery rather
than at submission, so a campaign that finished a minute ago legitimately has
few priced rows, and a total assembled from twelve of four thousand messages is
not a campaign's cost.

Read-only: it opens a session, runs two queries per campaign and writes nothing.
"""

import argparse
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import SessionLocal            # noqa: E402
from app.models.campaign import Campaign              # noqa: E402
from app.services import cost_reconciliation          # noqa: E402


def _money(value, places="0.0001") -> str:
    return "—" if value is None else f"${Decimal(value).quantize(Decimal(places))}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("campaign_id", nargs="?", type=int)
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()

    db = SessionLocal()
    try:
        if args.campaign_id:
            ids = [args.campaign_id]
        else:
            ids = [c.id for c in db.query(Campaign).order_by(Campaign.id.desc())
                   .limit(args.limit).all()][::-1]

        if not ids:
            print("No campaigns.")
            return 0

        print(f"{'id':>5}  {'campaign':<28} {'segs':>6} {'priced':>10} "
              f"{'estimate':>10} {'actual':>10} {'rate':>10} {'fee':>10} {'per seg':>9}")
        print("-" * 108)

        totals = {"estimate": Decimal("0"), "actual": Decimal("0"), "priced": 0}
        for campaign_id in ids:
            row = cost_reconciliation.reconcile(db, campaign_id)
            if row is None:
                print(f"{campaign_id:>5}  (no such campaign)")
                continue

            name = (row["campaign_name"] or "")[:28]
            coverage = (f"{row['priced_messages']}/{row['messages']}"
                        if row["messages"] else "0/0")
            print(f"{row['campaign_id']:>5}  {name:<28} {row['segments']:>6} "
                  f"{coverage:>10} {_money(row['estimated']):>10} "
                  f"{_money(row['actual']):>10} {_money(row['rate_component']):>10} "
                  f"{_money(row['carrier_fee_component']):>10} "
                  f"{_money(row['effective_rate_per_segment'], '0.00001'):>9}")

            totals["estimate"] += row["estimated"]
            if row["actual"] is not None:
                totals["actual"] += row["actual"]
                totals["priced"] += row["priced_messages"]
            if len(row["currencies"]) > 1:
                # Never summed away. Two currencies added together is arithmetic
                # that is wrong quietly, and the total above would be meaningless.
                print(f"       ! campaign {row['campaign_id']} mixes currencies "
                      f"{row['currencies']} — the totals above do not apply to it")

        print("-" * 108)
        print(f"{'':>5}  {'total':<28} {'':>6} {totals['priced']:>10} "
              f"{_money(totals['estimate']):>10} {_money(totals['actual']):>10}")
        if not totals["priced"]:
            print("\nNo message carries a carrier cost yet. Most carriers price at "
                  "delivery, not at submission — and the console provider never "
                  "prices, because a dry run spends nothing.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
