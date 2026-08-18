#!/usr/bin/env python3
"""Seed demo contacts so you can click through the UI immediately.

    python scripts/seed_demo_data.py

Safe: it only writes contacts and a list, and the default SMS_PROVIDER is
"console", so nothing can be sent to these numbers. The 555-01xx range is
reserved for fiction and is not assigned to real subscribers.
"""

import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(HERE)
sys.path.insert(0, HERE)

from app.core.database import SessionLocal, engine, Base   # noqa: E402
import app.models                                          # noqa: F401,E402
from app.services import contact_service                   # noqa: E402

DEMO = [
    ("+15555550100", "Jane Doe", {"customer_tier": "gold"}),
    ("+15555550101", "John Smith", {"customer_tier": "silver"}),
    ("+15555550102", "Maria Garcia", {"customer_tier": "gold"}),
    ("+15555550103", "Sam Patel", {}),
    ("+15555550104", "Alex Chen", {"customer_tier": "bronze"}),
]

if __name__ == "__main__":
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        demo_list = contact_service.get_or_create_list(
            db, "Demo list", description="Seeded sample contacts", source="manual"
        )
        created = 0
        for phone, name, attributes in DEMO:
            contact = contact_service.upsert_contact(
                db, phone=phone, full_name=name, source="manual", attributes=attributes
            )
            if contact:
                contact_service.add_to_list(db, demo_list.id, contact.id)
                created += 1
        print(f"Seeded {created} contacts into '{demo_list.name}'.")
        print("Start the app and open http://localhost:8000/contacts")
    finally:
        db.close()
