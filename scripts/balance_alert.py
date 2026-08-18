#!/usr/bin/env python3
"""Low-balance alert. Run hourly from cron.

Texts the operator when the carrier balance drops below the threshold, so the
account gets topped up BEFORE a campaign drains it to zero.

Why this exists: in the reference deployment a zero balance deactivated the
account mid-blast and every remaining message failed with "Account inactive".
It happened at least five separate times across two months and cost more than
13,000 undelivered messages. Auto-recharge did not prevent it — the top-up was
configured at $10, and a 6,000-recipient blast spends that in about ninety
seconds, far faster than a PayPal recharge can post.

Set the threshold well above zero. The alert is sent over the same account it is
warning about, so at a true zero balance it cannot send at all.

    crontab -e
    0 * * * * cd /path/to/app && venv/bin/python scripts/balance_alert.py >> logs/balance_alert.log 2>&1
"""

import os
import sys
import json
import asyncio
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(HERE)                      # so .env and the sqlite path resolve
sys.path.insert(0, HERE)

from app.core.config import settings          # noqa: E402
from app.sms.factory import get_provider      # noqa: E402

REALERT_HOURS = 12                  # don't re-text more often than this while low
STATE_FILE = os.path.join(HERE, "data", ".balance_alert_state.json")


def load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state: dict):
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except Exception as e:
        print(f"warn: could not write state file: {e}")


async def main():
    if not settings.ALERT_PHONE:
        print("ALERT_PHONE not configured; nothing to do")
        return

    provider = get_provider()
    balance = await provider.get_balance()
    if balance is None:
        print("provider does not report a balance; nothing to do")
        return

    state = load_state()
    now = datetime.now()
    stamp = now.strftime("%Y-%m-%d %H:%M")
    threshold = settings.BALANCE_ALERT_THRESHOLD

    if balance >= threshold:
        if state.pop("last_alert", None):
            save_state(state)       # recovered — re-arm for the next dip
        print(f"{stamp}  balance ${balance:.2f} OK (>= ${threshold:.0f})")
        return

    last = state.get("last_alert")
    if last:
        try:
            if now - datetime.fromisoformat(last) < timedelta(hours=REALERT_HOURS):
                print(f"{stamp}  balance ${balance:.2f} low, already alerted {last}; skipping")
                return
        except ValueError:
            pass

    message = (
        f"{settings.BRAND_APP_NAME}: carrier balance is ${balance:.2f}, below the "
        f"${threshold:.0f} threshold. Top up before the next campaign or messages will fail."
    )
    result = await provider.send(settings.ALERT_PHONE, message)
    print(f"{stamp}  balance ${balance:.2f} low; alert sent={result.success}")

    if result.success:
        state["last_alert"] = now.isoformat()
        save_state(state)


if __name__ == "__main__":
    asyncio.run(main())
