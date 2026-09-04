"""Renaming, archiving and deleting a contact list.

## Why this is its own module

`contact_service.py` was at 482 lines before this session and the 500-line rule
is a hard one, so the additions had to split somewhere. This is the natural
seam: `contact_service` answers "what is this audience and who is in it", and
this file answers "what may the client do to the list itself". A reviewer
reading A4, A5 and A6 reads one file.

## Why it exists at all

Nothing in the product could rename or hide a list. Every test upload became a
permanent entry in the audience dropdown, and ten pieces of debris from a single
evening — one- and two-member lists — were deleted by hand on the live box on
2026-09-04, which is Jordan doing something the client has no way to do himself.

Nineteen of the twenty-one lists on that box were never used by a campaign, so
there is no campaign name for most of them to inherit and the "titled by the
campaign that first used it" model cannot be applied retroactively. He has to
name them himself. This is the tool that lets him.

## The three verbs, and why they are three

**Rename** is the fix for a bad name. **Archive** is the fix for a list he no
longer wants offered — hidden from the picker, still resolving for history.
**Delete** is for a list nothing has ever referenced, and it refuses anything
else, because hard-deleting a list a campaign targeted degrades that campaign's
report label to the raw string `list:20` — the same loss decision 007's clause 2
is about, arriving through a different door.
"""

import logging
from typing import List

from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.contact_list import ContactList, ContactListMember
from app.services import contact_service
# The selector grammar is imported, never re-implemented. Two definitions of
# what `category:estates&list:12` names is how a rename and a delete come to
# disagree about the same campaign — and one of them would be wrong about
# whether the client is about to break a report. Same argument as
# `SENT_STATUSES` living beside `BILLABLE_STATUSES` rather than in each reader.
from app.services.contact_service import _int_arg, _split_terms

logger = logging.getLogger("lists")


class ListAdminError(Exception):
    """A refusal the client is meant to read. The routers map it to a status."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def selector_names_list(selector: str, list_id: int) -> bool:
    """Does this audience selector name list `list_id`?

    Through `_split_terms()`, not a substring test for `"list:<id>"`. A compound
    selector names the list too — `category:estates&list:12` is one campaign's
    audience and deleting list 12 costs it its label exactly as `list:12` would.
    A substring test would also find `list:12` inside `list:120`, which is the
    unanchored-substring mistake this project has now made three times.

    Never raises. A typo in one stored selector is not a reason to refuse an
    operation on a different list.
    """
    try:
        terms = _split_terms((selector or "").strip())
    except ValueError:
        return False
    for term in terms:
        term = term.strip()
        if not term.startswith("list:"):
            continue
        try:
            if _int_arg(term) == list_id:
                return True
        except ValueError:
            continue
    return False


def campaigns_using(db: Session, list_id: int) -> List[Campaign]:
    """Every campaign whose audience selector names this list.

    Filtered in Python rather than with `audience LIKE '%list:12%'`, for
    `selector_names_list()`'s reason: `LIKE` cannot tell `list:12` from
    `list:120`.

    The cost, at the row counts that matter: this client runs roughly one
    auction a day, so `campaigns` is in the hundreds. Measured at 400 rows it
    is 3ms, and it runs on a rename or a delete — a click, not a poll. If that
    table ever reaches five figures, the fix is a `LIKE` prefilter with this
    predicate still deciding, not this predicate replaced.
    """
    return [c for c in db.query(Campaign).all()
            if selector_names_list(c.audience, list_id)]


def _get(db: Session, list_id: int) -> ContactList:
    row = db.get(ContactList, list_id)
    if not row:
        raise ListAdminError("List not found", status_code=404)
    return row


# ─── A5's read ──────────────────────────────────────────────────────────────

def admin_summaries(db: Session) -> List[dict]:
    """Every list the panel shows — archived ones included, marked as such.

    Built from `contact_service._list_rows()`, the same read `list_summaries()`
    serves the picker from, so the panel and the dropdown cannot disagree about
    a count or about how long ago a list was texted. The archived rows are the
    only difference between the two, and `contact_service.is_archived()` is the
    one predicate that decides which is which — the picker says "archived" by
    absence and this panel says it with a badge, and two definitions of that
    word is how the two surfaces come to disagree about the same list.

    An archived list needs its own read because `list_summaries()` is
    *defined* as what the client may pick; widening it with a flag would leave
    every picker free to start rendering rows it must not offer.
    """
    return contact_service._list_rows(db)


# ─── A4: rename ─────────────────────────────────────────────────────────────

def rename(db: Session, list_id: int, name: str) -> dict:
    """Give a list a new name, everywhere at once.

    **A rename retroactively changes what old reports say, and that is correct.**
    The bad names are the problem being solved: renaming "General Merchandise —
    2026-08-21 upload" to "Main bidder import" is meant to change every screen
    that ever referenced that list, including a report on a campaign sent weeks
    ago. Do not "fix" this later by freezing the label at send time.

    `contact_service._term_label()` already looks the name up live, so every
    surface that calls `audience_label()` follows a rename on its own. What does
    **not** follow on its own is `campaigns.audience_label`, which
    `campaign_builder` writes once at creation and the campaign rail, the
    history screen and the per-campaign report all read back. That column is a
    cached render of `audience_label()`, and renaming the list is exactly what
    invalidates it — so it is recomputed here, from `audience_label()` rather
    than by substituting the new name into the old string, because a campaign's
    label may also name a category or a second term.

    A collision comes back as a refusal naming the taken name, not as a 500 from
    the unique index: `contact_lists.name` is `unique=True, nullable=False`, and
    an `IntegrityError` reaching the client is a guard that refuses without
    explaining.
    """
    row = _get(db, list_id)
    name = (name or "").strip()
    if not name:
        raise ListAdminError("A list needs a name.")

    clash = (db.query(ContactList)
             .filter(ContactList.name == name, ContactList.id != list_id)
             .first())
    if clash:
        # The remedy differs by which list holds the name, and offering the
        # wrong one is how a correct refusal reads as a broken tool. "Archive
        # the other one" is useless advice when the other one is already
        # archived — it is out of his picker and still holding the name.
        remedy = ("Rename that one first, or pick a different name here."
                  if contact_service.is_archived(clash.archived)
                  else "Archive that one first, or pick a different name here.")
        held_by = ("a list you have archived" if contact_service.is_archived(clash.archived)
                   else "another list")
        raise ListAdminError(
            f"“{name}” is already the name of {held_by}, and two lists cannot "
            f"share a name. {remedy}")

    was = row.name
    row.name = name
    # Flushed before the labels are rebuilt: `audience_label()` reads the name
    # back out of the database, and SQLAlchemy would otherwise autoflush in the
    # middle of that query and leave the ordering to chance. Same shape as the
    # 5i sighting bug — a "what does this look like now" question asked around
    # a pending write answers about whichever world got there first.
    db.flush()

    relabelled = 0
    for campaign in campaigns_using(db, list_id):
        fresh = contact_service.audience_label(db, campaign.audience)
        if campaign.audience_label != fresh:
            campaign.audience_label = fresh
            relabelled += 1
    db.commit()

    logger.info("list %s renamed from %r to %r; %d campaign label(s) refreshed",
                list_id, was, name, relabelled)
    return {"success": True, "id": list_id, "name": name,
            "campaigns_relabelled": relabelled}


# ─── A3: archive and unarchive ──────────────────────────────────────────────

def set_archived(db: Session, list_id: int, archived: bool) -> dict:
    """Hide a list from the picker, or put it back.

    Archiving resolves nothing away. `resolve_audience()`, `_term_ids_query()`,
    `_term_label()` and `audience_count()` never look at this column, on
    purpose: a campaign that targeted list 20 must still return its contacts and
    still render its name in history and in its report. The flag is read in
    exactly one place — `contact_service._list_rows()`, through
    `is_archived()` — and `list_summaries()` is what drops the row.
    """
    row = _get(db, list_id)
    row.archived = 1 if archived else 0
    db.commit()
    logger.info("list %s %s", list_id, "archived" if archived else "unarchived")
    return {"success": True, "id": list_id, "archived": bool(archived)}


# ─── A6: delete, and the refusal ────────────────────────────────────────────

def delete(db: Session, list_id: int) -> dict:
    """Delete a list nothing has ever referenced. Contacts are never deleted.

    This endpoint existed with no caller and hard-deleted whatever it was
    pointed at. Under this session's model that is the wrong tool for "take it
    out of my dropdown" — archive is — and it is the *destructive* tool for a
    list a campaign used: `_term_label()` resolves `list:20` by looking the row
    up, so deleting the row turns that campaign's audience label into the raw
    string `list:20` on the rail, in history and in its report, permanently.

    So it refuses, with the remedy that actually works on this object. A guard
    that refuses without explaining reads as a broken tool — decision 006.
    """
    row = _get(db, list_id)
    used_by = campaigns_using(db, list_id)
    if used_by:
        names = ", ".join(f"“{c.name}”" for c in used_by[:3])
        more = f" and {len(used_by) - 3} more" if len(used_by) > 3 else ""
        raise ListAdminError(
            f"“{row.name}” was the audience for {names}{more}, so deleting it "
            "would leave those campaigns' reports naming a list that no longer "
            "exists. Archive it instead — it disappears from the audience "
            "picker and every report keeps its name.",
            status_code=409)

    removed = db.query(ContactListMember).filter(
        ContactListMember.list_id == list_id
    ).delete()
    db.delete(row)
    db.commit()
    logger.info("list %s deleted with %d membership(s)", list_id, removed)
    return {"success": True, "memberships_removed": removed}
