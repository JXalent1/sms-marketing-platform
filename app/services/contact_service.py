"""Contacts, lists and audience resolution.

Audience selectors are strings so they can round-trip through a form field and
be stored on the campaign:

    "all"                            every active contact
    "list:<id>"                      members of one list
    "source:csv"                     everything a given ContactSource produced
    "category:food_service"          members of one category
    "category:food_service,equipment"  UNION of two or more
    "category:equipment&list:12"     INTERSECTION — in the category AND on the list

Grammar, stated so nothing has to be guessed:

  - `&` binds looser than `,`. "category:a,b&list:12" is (a ∪ b) ∩ list12.
  - Exactly one `&` is supported. Two is a ValueError, not a best guess.
  - An unknown category slug is a ValueError naming the slug. It is never a
    silent empty audience: a typo that quietly resolves to zero recipients is
    how a campaign gets "sent" to nobody and nobody finds out for a week.

Deactivating a category does not stop it resolving. Retiring a category from the
pickers must not silently empty a campaign already pointed at it — `list_summaries()`
is what hides it, and that is the honest place for the distinction.

Session 5i took that to its conclusion: **the picker no longer offers a category
at all, and the resolver still consumes every one it ever produced.** Categories
are hidden, not removed. `campaigns.audience` holds `category:<slug>` on every
campaign this client has run so far, and `audience_label()` renders those in the
campaign rail, in history and in every per-campaign report. Deleting the
resolution path here would turn every report older than 5i into a broken string,
and it would take the prospecting taxonomy — which is keyed on the same tables —
with it. Do not "tidy up" the `category:` branches below.
"""

from sqlalchemy.orm import Session
from sqlalchemy import func
from app.models.contact import Contact
from app.models.category import Category, ContactCategory
from app.models.contact_list import ContactList, ContactListMember, parse_created_at
from app.models.sms_message import SENT_STATUSES, SMSMessage
from app.sms.phone import normalize, is_valid
from datetime import date, datetime
from typing import List, Optional
import logging

logger = logging.getLogger("contacts")

# The pinned entry at the top of every audience picker, in one place.
#
# It renders in the composer's dropdown, on the dashboard's first list card, and
# in `audience_label()` — which is what the composer's summary panel and every
# campaign report read. `send_mode()` in `app/sms/factory.py` is the pattern and
# the reason: one module owns the client-safe wording so a second surface cannot
# invent a second spelling of the same thing. A dropdown reading
# "⭐ ALL BIDDERS — MAIN LIST" beside a summary row reading "All contacts" is two
# names for one audience, on one screen.
ALL_BIDDERS_LABEL = "⭐ ALL BIDDERS — MAIN LIST"


def days_since(iso: Optional[str], today: date = None) -> Optional[int]:
    """Whole days between an ISO timestamp and today. None when never.

    None and 0 are opposite facts — "never texted" and "texted this morning" —
    so this never collapses one into the other. That distinction is the whole
    point of the em dash on the dashboard's cards.

    Defined here rather than in `dashboard_service` because `list_summaries()`
    below has to report freshness and `dashboard_service` already imports this
    module, so the dependency only runs one way. `dashboard_service._days_since`
    delegates to it: two copies of this is how two screens come to disagree
    about how old the same send is.
    """
    if not iso:
        return None
    try:
        when = date.fromisoformat(str(iso)[:10])
    except ValueError:
        return None
    return max(0, ((today or date.today()) - when).days)


# ─── Upsert ─────────────────────────────────────────────────────────────────

def upsert_contact(db: Session, phone: str, full_name: str = None, email: str = None,
                   source: str = None, external_ref: str = None,
                   attributes: dict = None, commit: bool = True) -> Optional[Contact]:
    """Create or update a contact keyed on the normalized phone number.

    Returns None for unusable numbers rather than storing junk — a list full of
    malformed numbers turns into a list full of paid-for failures later.
    """
    normalized = normalize(phone)
    if not normalized or not is_valid(normalized):
        return None

    contact = db.query(Contact).filter(Contact.phone == normalized).first()
    if contact:
        # Only fill gaps; never let a sparse import blank out good data.
        if full_name and not contact.full_name:
            contact.full_name = full_name
        if email and not contact.email:
            contact.email = email
        if attributes:
            merged = dict(contact.attributes or {})
            merged.update(attributes)
            contact.attributes = merged
        contact.updated_at = datetime.now().isoformat()
    else:
        contact = Contact(
            phone=normalized,
            full_name=full_name,
            email=email,
            source=source,
            external_ref=external_ref,
            attributes=attributes or {},
            created_at=datetime.now().isoformat(),
        )
        db.add(contact)

    if commit:
        db.commit()
        db.refresh(contact)
    return contact


# ─── Lists ──────────────────────────────────────────────────────────────────

def get_or_create_list(db: Session, name: str, description: str = None,
                       source: str = None) -> ContactList:
    row = db.query(ContactList).filter(ContactList.name == name).first()
    if row:
        return row
    # `created_at` is written explicitly rather than left to a server default,
    # and since 5i there is no default to fall back to. This function was the
    # second writer of that column: it omitted the value, SQLite's
    # CURRENT_TIMESTAMP filled it in **UTC** with a space separator, and
    # `import_service.commit()` wrote local isoformat into the same column. The
    # picker, the dashboard cards and the composer are all sorted on recency
    # now, so two clocks in one column is an ordering that is silently wrong.
    # See `app/models/contact_list.py` and migration `f4a1c7d90e52`.
    row = ContactList(name=name, description=description, source=source,
                      created_at=datetime.now().isoformat())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def add_to_list(db: Session, list_id: int, contact_id: int, commit: bool = True) -> bool:
    """Put a contact on a list. Returns False if it was already there.

    `added_at` is written explicitly rather than left to the column's server
    default, and that is load-bearing since 5e. The default is SQLite's
    `CURRENT_TIMESTAMP`, which is **UTC**; every other timestamp this application
    writes — `campaigns.created_at`, `import_service`'s own membership rows —
    is `datetime.now()`, which is local. A top-up asks "who was added to this
    list after the campaign was created", and comparing a UTC row against a local
    cutoff makes every hand-added contact look up to five hours newer than it is,
    so somebody added shortly *before* the campaign reads as added since.

    Two clocks in one column is the same defect as two formats in one column, and
    the fix is the same: one writer, one clock.
    """
    exists = db.query(ContactListMember).filter(
        ContactListMember.list_id == list_id,
        ContactListMember.contact_id == contact_id,
    ).first()
    if exists:
        return False
    db.add(ContactListMember(list_id=list_id, contact_id=contact_id,
                             added_at=datetime.now().isoformat()))
    if commit:
        db.commit()
    return True


def _last_sent_by_list(db: Session) -> dict:
    """list_id -> the most recent moment a member of it was actually texted.

    One grouped join: messages → contacts → memberships. Freshness comes from
    `sms_messages` and never from a campaign's audience string, which is
    `dashboard_service`'s own rule and has not changed — a campaign row records
    who it *meant* to text, and the message table records who was texted. That
    stays correct when a send was aborted half-way, when a campaign targeted a
    union, and when somebody edits a saved selector afterwards.

    `SENT_STATUSES` comes from `app/models/sms_message.py`, beside the billable
    set it must not become. See the comment there.

    `added_at` is deliberately not read. It carries its own UTC/local defect
    (see `add_to_list`), and nothing about "when was this list last texted"
    needs to know when a member joined it.
    """
    rows = (db.query(ContactListMember.list_id, func.max(SMSMessage.sent_at))
            .join(SMSMessage, SMSMessage.contact_id == ContactListMember.contact_id)
            .filter(SMSMessage.status.in_(SENT_STATUSES),
                    SMSMessage.sent_at.isnot(None))
            .group_by(ContactListMember.list_id)
            .all())
    return {list_id: last for list_id, last in rows if last}


def _last_sent_overall(db: Session) -> Optional[str]:
    """The most recent moment anybody was texted — the pinned entry's freshness.

    `contact_id` is required for the same reason the per-list query joins
    memberships: the pinned entry means "every bidder", and a message row with
    no contact behind it was not a message to one of them. Nothing writes such a
    row today — all four construction sites pass a contact — and this query is
    the one that would silently start counting it.
    """
    return (db.query(func.max(SMSMessage.sent_at))
            .filter(SMSMessage.status.in_(SENT_STATUSES),
                    SMSMessage.contact_id.isnot(None),
                    SMSMessage.sent_at.isnot(None))
            .scalar())


def list_summaries(db: Session) -> List[dict]:
    """Every audience a picker can offer: the pinned entry, then lists.

    Session 5i replaced the category-first picker with the flat, recency-sorted
    list of named lists the client asked for: **the pinned
    `ALL_BIDDERS_LABEL` entry, then every list, newest first.** No category
    entries — the picker stops offering them, and nothing else stops resolving
    them. `resolve_audience()`, `_term_ids_query()`, `_term_label()` and
    `audience_count()` all keep their `category:` branches, because every
    campaign in history stores `category:<slug>` and a report that cannot
    resolve its own audience is a broken string on every screen it appears on.

    Ordered by `parse_created_at()`, never by the raw string. The column had two
    writers with two spellings and a lexicographic comparison across them is
    wrong in the direction nobody checks — see `app/models/contact_list.py`.

    Each entry carries `selector`, `label`, `count`, `kind`, `last_sent_at` and
    `days_since_sent`. The count is **active** contacts, matching what a send
    would actually reach and matching `audience_count()` on the same selector;
    a list whose members have all been deactivated reports 0 rather than a
    number no campaign could ever hit.
    """
    rows = (db.query(ContactList.id, ContactList.name, ContactList.created_at,
                     func.count(Contact.id))
            .outerjoin(ContactListMember,
                       ContactListMember.list_id == ContactList.id)
            # Outer-joined and filtered in the ON clause, not in WHERE: a list
            # with no members at all still belongs in the picker, reading 0.
            # "Yacht buyers: 0" is information; a missing row is confusing.
            .outerjoin(Contact, (Contact.id == ContactListMember.contact_id)
                                & (Contact.is_active == 1))
            .group_by(ContactList.id, ContactList.name, ContactList.created_at)
            .all())

    total_active = db.query(Contact).filter(Contact.is_active == 1).count()
    last_sent = _last_sent_by_list(db)
    overall = _last_sent_overall(db)
    today = date.today()

    audiences = [{
        "selector": "all",
        "label": ALL_BIDDERS_LABEL,
        "count": total_active,
        "kind": "all",
        "last_sent_at": overall,
        "days_since_sent": days_since(overall, today),
    }]
    audiences += [{
        "selector": f"list:{list_id}",
        "label": name,
        "count": count,
        "kind": "list",
        "last_sent_at": last_sent.get(list_id),
        "days_since_sent": days_since(last_sent.get(list_id), today),
    } for list_id, name, _, count in sorted(
        # Id descending as the tiebreaker, so "newest first" is a total order.
        # Two lists created in the same second — a script, a fast double
        # upload — would otherwise fall back on whatever order the ungrouped
        # query happened to return, which is not something to depend on.
        rows, key=lambda r: (parse_created_at(r[2]), r[0]), reverse=True)]
    return audiences


# ─── Audience resolution ────────────────────────────────────────────────────

def _category_ids_for_slugs(db: Session, slugs: List[str]) -> List[int]:
    """Slugs to ids, raising on the first one that does not exist."""
    found = {slug: cid for cid, slug in
             db.query(Category.id, Category.slug).filter(Category.slug.in_(slugs)).all()}
    missing = [s for s in slugs if s not in found]
    if missing:
        raise ValueError(
            f"Unknown category slug: {', '.join(missing)}. "
            "A typo must not resolve to an empty audience."
        )
    return [found[s] for s in slugs]


def _term_ids_query(db: Session, term: str):
    """A query over Contact.id for one selector term, active contacts only.

    Returned as a query rather than a Python set so an intersection stays in
    SQL. Materializing 50k ids to feed them back through an IN clause is both
    slower and, on SQLite, past the bound-parameter limit.
    """
    term = term.strip()

    if term == "all":
        return db.query(Contact.id).filter(Contact.is_active == 1)

    if term.startswith("list:"):
        return (db.query(Contact.id)
                .join(ContactListMember, ContactListMember.contact_id == Contact.id)
                .filter(ContactListMember.list_id == _int_arg(term),
                        Contact.is_active == 1))

    if term.startswith("source:"):
        return db.query(Contact.id).filter(
            Contact.source == term.split(":", 1)[1], Contact.is_active == 1
        )

    if term.startswith("category:"):
        slugs = [s.strip() for s in term.split(":", 1)[1].split(",") if s.strip()]
        if not slugs:
            raise ValueError(f"Audience selector {term!r} names no category")
        ids = _category_ids_for_slugs(db, slugs)
        # IN across several categories is the union; the outer query never
        # joins, so a contact in two of them still comes back once.
        return (db.query(Contact.id)
                .join(ContactCategory, ContactCategory.contact_id == Contact.id)
                .filter(ContactCategory.category_id.in_(ids), Contact.is_active == 1))

    raise ValueError(f"Unknown audience selector: {term!r}")


def _int_arg(term: str) -> int:
    raw = term.split(":", 1)[1]
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"Audience selector {term!r} needs a numeric id, got {raw!r}")


def _split_terms(selector: str) -> List[str]:
    terms = [t for t in (selector or "").split("&")]
    if len(terms) > 2:
        raise ValueError(
            f"Audience selector {selector!r} has more than one '&'. Only one "
            "intersection is supported — write the union on the left, e.g. "
            "'category:food_service,equipment&list:12'."
        )
    return terms


def resolve_audience(db: Session, selector: str) -> List[Contact]:
    """Turn an audience selector into contacts.

    Deduplication is guaranteed by Contact.phone being unique, and reinforced
    here: each term contributes an IN sub-select rather than a join, so a
    contact in two categories and on three lists is still a single row.
    """
    terms = _split_terms((selector or "").strip())

    query = db.query(Contact).filter(Contact.is_active == 1)
    for term in terms:
        query = query.filter(Contact.id.in_(_term_ids_query(db, term).scalar_subquery()))
    return query.order_by(Contact.id).all()


def _term_label(db: Session, term: str) -> str:
    term = term.strip()
    if term == "all":
        # The picker's own wording for this selector, not a second name for it.
        # The composer's summary panel reads `audience_label()` while the
        # dropdown beside it reads `list_summaries()`; "All contacts" here and
        # the pinned label there would be one audience wearing two names on one
        # screen.
        return ALL_BIDDERS_LABEL
    if term.startswith("list:"):
        try:
            row = db.get(ContactList, _int_arg(term))
        except ValueError:
            return term
        return row.name if row else term
    if term.startswith("source:"):
        return f"Source: {term.split(':', 1)[1]}"
    if term.startswith("category:"):
        slugs = [s.strip() for s in term.split(":", 1)[1].split(",") if s.strip()]
        rows = {r.slug: r.label for r in
                db.query(Category).filter(Category.slug.in_(slugs)).all()}
        return " + ".join(rows.get(s, s) for s in slugs)
    return term


def audience_count(db: Session, selector: str) -> int:
    """How many contacts a selector resolves to, without materializing them.

    resolve_audience() returns objects because the send loop needs them. A
    screen that only wants the number must not pay for 50,000 hydrated rows to
    call len() on them — that is the difference between a dashboard that opens
    instantly and one that appears to hang.
    """
    terms = _split_terms((selector or "").strip())
    query = db.query(func.count(Contact.id)).filter(Contact.is_active == 1)
    for term in terms:
        query = query.filter(Contact.id.in_(_term_ids_query(db, term).scalar_subquery()))
    return query.scalar() or 0


def audience_label(db: Session, selector: str) -> str:
    """Human wording for a selector: "Food Service + Equipment ∩ Aug 22 preview".

    Never raises. This renders in page headers and campaign rows, and a label
    helper that throws turns a typo in a saved selector into a 500 on a screen
    that was only trying to describe it.
    """
    selector = (selector or "").strip()
    try:
        terms = _split_terms(selector)
    except ValueError:
        return selector
    return " ∩ ".join(_term_label(db, t) for t in terms)

