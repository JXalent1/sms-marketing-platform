"""Contact sources — the pluggable ingestion layer.

This is the seam where each client differs most. One client uploads CSVs, the
next syncs a CRM, the next needs a login-and-scrape job on a schedule. Everything
downstream (lists, campaigns, blocklist, billing) is identical regardless.

The reference system had no seam: a 930-line Playwright scraper for one specific
auction site was wired directly into the models, the dashboard and the scheduler,
so "reuse this for another client" meant deleting a third of the app.

To add a source:
    1. Subclass ContactSource, implement fetch().
    2. Register it in app/sources/__init__.py.
    3. Call it from a route or a scheduled job.

fetch() only produces records. Normalization, dedup and persistence are handled
by ingest() below, so a source never touches the database directly.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional, Iterable
from sqlalchemy.orm import Session
import logging

logger = logging.getLogger("sources")


@dataclass
class ContactRecord:
    """One inbound contact, before normalization."""
    phone: str
    full_name: Optional[str] = None
    email: Optional[str] = None
    external_ref: Optional[str] = None
    attributes: dict = field(default_factory=dict)


@dataclass
class IngestResult:
    total: int = 0
    created: int = 0
    updated: int = 0
    invalid: int = 0
    duplicates: int = 0

    def as_dict(self) -> dict:
        return {
            "total": self.total,
            "created": self.created,
            "updated": self.updated,
            "invalid": self.invalid,
            "duplicates": self.duplicates,
        }


class ContactSource(ABC):
    """Base class for anything that produces contacts."""

    name: str = "base"
    description: str = ""

    @abstractmethod
    def fetch(self, **kwargs) -> Iterable[ContactRecord]:
        """Yield ContactRecords. May be a generator for large imports."""

    def ingest(self, db: Session, list_name: Optional[str] = None, **kwargs) -> IngestResult:
        """Fetch, normalize, dedup and persist. Optionally add to a named list."""
        from app.services import contact_service

        result = IngestResult()
        target_list = (contact_service.get_or_create_list(db, list_name, source=self.name)
                       if list_name else None)

        seen = set()
        for record in self.fetch(**kwargs):
            result.total += 1

            contact = contact_service.upsert_contact(
                db,
                phone=record.phone,
                full_name=record.full_name,
                email=record.email,
                source=self.name,
                external_ref=record.external_ref,
                attributes=record.attributes,
                commit=False,
            )

            if contact is None:
                result.invalid += 1
                continue

            if contact.phone in seen:
                result.duplicates += 1
                continue
            seen.add(contact.phone)

            # id is None until flush — needed for the list membership row.
            db.flush()
            if contact.created_at and contact.updated_at:
                result.updated += 1
            else:
                result.created += 1

            if target_list:
                contact_service.add_to_list(db, target_list.id, contact.id, commit=False)

        db.commit()
        logger.info(f"[{self.name}] ingest complete: {result.as_dict()}")
        return result
