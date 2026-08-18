"""Blocklist operations.

Thin on purpose — the value is that every write goes through normalize() so the
send-path lookup can never miss because of formatting.
"""

from sqlalchemy.orm import Session
from app.models.blocked_number import BlockedNumber
from app.sms.phone import normalize
from datetime import datetime
import logging

logger = logging.getLogger("blocklist")


def is_blocked(db: Session, phone: str) -> bool:
    normalized = normalize(phone)
    if not normalized:
        return False
    return db.query(BlockedNumber).filter(BlockedNumber.phone == normalized).first() is not None


def load_blocked_set(db: Session) -> set:
    """All blocked numbers as a set.

    The send loop uses this instead of one query per recipient — at 6,000
    recipients that is 6,000 round-trips saved, which on SQLite is the difference
    between a campaign that starts immediately and one that appears to hang.
    """
    return {row.phone for row in db.query(BlockedNumber.phone).all()}


def block_number(db: Session, phone: str, reason: str, source: str = "manual",
                 notes: str = None) -> bool:
    """Add to the blocklist. Returns True if newly blocked."""
    normalized = normalize(phone)
    if not normalized:
        logger.warning(f"Cannot block invalid phone: {phone}")
        return False

    if db.query(BlockedNumber).filter(BlockedNumber.phone == normalized).first():
        return False

    db.add(BlockedNumber(
        phone=normalized,
        reason=reason,
        source=source,
        blocked_at=datetime.now().isoformat(),
        notes=notes,
    ))
    db.commit()
    logger.info(f"BLOCKED {normalized} | reason={reason} | source={source}")
    return True


def unblock_number(db: Session, phone: str) -> bool:
    normalized = normalize(phone)
    if not normalized:
        return False
    row = db.query(BlockedNumber).filter(BlockedNumber.phone == normalized).first()
    if not row:
        return False
    db.delete(row)
    db.commit()
    logger.info(f"UNBLOCKED {normalized}")
    return True


def get_all_blocked(db: Session, limit: int = 5000) -> list:
    return (db.query(BlockedNumber)
            .order_by(BlockedNumber.blocked_at.desc())
            .limit(limit).all())


def get_blocked_count(db: Session) -> int:
    return db.query(BlockedNumber).count()
