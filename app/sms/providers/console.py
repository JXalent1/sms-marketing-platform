"""Console (dry-run) provider — the default.

Logs what it would have sent and returns success. Use it for all local
development and for the first end-to-end run against a new client's data, so a
misconfigured recipient list costs nothing and reaches nobody.

SMS_PROVIDER defaults to "console" precisely so that a fresh checkout, or a
deploy where the real credentials failed to load, cannot text 6,000 strangers.
"""

import logging
from typing import Optional
from app.sms.base import SMSProvider, SendResult
from app.sms.segments import count_segments, is_gsm7

logger = logging.getLogger("sms.console")


class ConsoleProvider(SMSProvider):
    name = "console"

    def __init__(self):
        self._counter = 0

    async def send(self, to: str, text: str) -> SendResult:
        self._counter += 1
        segments = count_segments(text)
        encoding = "GSM-7" if is_gsm7(text) else "UCS-2"
        preview = text.replace("\n", " ⏎ ")
        if len(preview) > 120:
            preview = preview[:117] + "..."

        logger.info(f"[DRY RUN] -> {to} | {segments} seg ({encoding}) | {preview}")

        return SendResult(
            success=True,
            message_id=f"console-{self._counter:08d}",
            parts=segments,
            raw={"dry_run": True, "to": to},
        )

    async def get_balance(self) -> Optional[float]:
        return 999_999.0        # never trips the pre-flight check

    async def get_message_status(self, message_id: str) -> Optional[str]:
        return "delivered"
