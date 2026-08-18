"""Worked example: pulling contacts from a client's API.

Not registered by default — copy it, rename it, point it at the real system.

This file is where the old LiveAuctioneers Playwright scraper would now live. It
was ~930 lines of browser automation with hardcoded selectors for one specific
site, and it broke every time that site shipped a redesign. Whatever you build
here, keep it behind fetch() so a change in the client's system never reaches the
campaign engine.

Scheduling: register a job in app/main.py's scheduler block. Two lessons from
running a daily scrape in production for eight months:

  - Retry once, ~30 minutes later, then give up and alert. Endless retries just
    turn a transient outage into a rate-limit ban.
  - Playwright leaks a browser process per run if you don't close it in a
    `finally`. The reference box accumulated 17 orphaned driver processes and
    1.6 GB of RSS between restarts.
"""

from typing import Iterable
from app.sources.base import ContactSource, ContactRecord
import logging

logger = logging.getLogger("sources.example_api")


class ExampleAPIContactSource(ContactSource):
    name = "example_api"
    description = "Pull contacts from the client's CRM/booking system"

    def __init__(self, api_key: str, base_url: str):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    def fetch(self, since: str = None, **kwargs) -> Iterable[ContactRecord]:
        """Yield ContactRecords from the upstream system.

        Yield, don't return a list — a full customer export can be 50k rows and
        ingest() commits once at the end either way.
        """
        import httpx

        page = 1
        while True:
            params = {"page": page, "per_page": 200}
            if since:
                params["updated_since"] = since

            with httpx.Client(timeout=30.0) as client:
                response = client.get(
                    f"{self.base_url}/customers",
                    params=params,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
                response.raise_for_status()
                payload = response.json()

            records = payload.get("data", [])
            if not records:
                break

            for item in records:
                yield ContactRecord(
                    phone=item.get("phone_number", ""),
                    full_name=item.get("name"),
                    email=item.get("email"),
                    external_ref=str(item.get("id")),
                    # Anything the client may later want as a merge tag.
                    attributes={
                        "customer_tier": item.get("tier"),
                        "last_order_date": item.get("last_order_at"),
                    },
                )

            if not payload.get("has_more"):
                break
            page += 1
            logger.info(f"[{self.name}] fetched page {page}")
