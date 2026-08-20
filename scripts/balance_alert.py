#!/usr/bin/env python3
"""Low-balance alert — the cron entry point.

The app registers the same check on its own scheduler (`app/main.py`), so on a
normal deployment this script is redundant. It stays because the two fail
differently: the scheduled job dies with the app, and "the app is down" and "the
account is empty" are the two situations you most want a warning about. Run it
from cron on a box where the app is not the only thing running.

Both entry points call `monitoring_service.check_low_balance()`. There is one
implementation of the threshold, one of the re-alert window, and one state file
— two copies would drift, and the copy that drifts is the one that stops firing.

Why this used to be wrong, kept as a warning: the alert was sent with
`provider.send()`, over the carrier account it was warning about. At a true zero
balance that send fails too, so the one moment the alert mattered was the one
moment it was silently dropped. It now goes through `agent/notify.sh`, on a
credential independent of the one being watched.

    crontab -e
    0 * * * * cd /path/to/app && venv/bin/python scripts/balance_alert.py >> logs/balance_alert.log 2>&1
"""

import os
import sys
import asyncio
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(HERE)                      # so .env and the sqlite path resolve
sys.path.insert(0, HERE)

from app.services import monitoring_service          # noqa: E402


async def main() -> int:
    result = await monitoring_service.check_low_balance()
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    if not result.get("checked"):
        print(f"{stamp}  {result.get('reason', 'not checked')}; nothing to do")
        return 0

    if not result.get("low"):
        print(f"{stamp}  sending capacity OK")
        return 0

    if result.get("alerted"):
        print(f"{stamp}  sending capacity LOW "
              f"(~{result.get('remaining_segments', 0):,} segments); alert sent")
        return 0

    print(f"{stamp}  sending capacity LOW; {result.get('reason', 'alert not sent')}")
    # Non-zero so cron mail and the log both show that a low balance went
    # un-paged, rather than burying it in a success line.
    return 0 if result.get("reason") == "already alerted" else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
