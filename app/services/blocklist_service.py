"""Blocklist operations.

Thin on purpose — the value is that every write goes through normalize() so the
send-path lookup can never miss because of formatting.
"""

from sqlalchemy import func
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


# Two kinds of number sit on this list and they mean opposite things to the
# client. An opt-out is a person who asked not to be texted — a compliance
# event, and the number he watches. An unreachable number is a data-quality
# fact: the carrier says a landline or a dead line, and no human decided
# anything. Reporting one figure labelled "Blocked" made 2,626 auto-blocked
# landlines read as 2,626 people opting out, on a screen headed "Opt-outs"
# under copy reading "Opt-outs are permanent".
#
# OPT_OUT_REASONS is the one definition of "opt-out" in this codebase.
# dashboard_service's opt-out-rate tile imports it rather than repeating its
# filter; a second definition would drift, and the two screens would disagree
# about the single number a client judges his list by.
#
# `carrier_opt_out` joined it in session 5g. Both are a person asking not to be
# texted, which is the question this figure answers, so both belong in it — the
# reasons stay separate rows because they are separate evidence (see
# blocked_number.py). Leaving carrier opt-outs filed under `delivery_failure`
# put them in the *unreachable* bucket, which is the same defect this split was
# built to fix, arriving from the other direction.
OPT_OUT_REASONS = ("stop_keyword", "carrier_opt_out")
UNREACHABLE_REASONS = ("delivery_failure", "carrier_block")


def blocked_counts(db: Session) -> dict:
    """Blocked numbers split by what the block actually means.

    `other` is everything in neither bucket — manual blocks today. It is
    returned rather than folded into either, because a manual block is neither
    a request from the person nor a verdict from a carrier, and quietly adding
    it to one of them would put the headline back to counting the wrong thing.
    """
    rows = db.query(BlockedNumber.reason, func.count(BlockedNumber.id)).group_by(
        BlockedNumber.reason
    ).all()

    counts = {"opt_outs": 0, "unreachable": 0, "other": 0, "total": 0}
    for reason, count in rows:
        if reason in OPT_OUT_REASONS:
            counts["opt_outs"] += count
        elif reason in UNREACHABLE_REASONS:
            counts["unreachable"] += count
        else:
            counts["other"] += count
        counts["total"] += count
    return counts
