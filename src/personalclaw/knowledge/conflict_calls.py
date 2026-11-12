"""The owner's call on a conflict, kept by the pair of items that disagree.

A conflict entry sits on the item whose reading found it (``file_metadata.conflicts``), with that
item's claim on the ``left`` and the stored one it disagrees with on the ``right``, and every
reading replaces the list. A call kept on the entry would be gone at the next reading, and one
kept by the claims would be lost when a new reading words them differently. So the call is kept
by the pair's id (:func:`conflict_id`, a hash of the two items' ids, sorted) in the knowledge
store's ``conflict_decisions``, and joined onto each entry as it is served (:func:`served`). It
names an item, never a side: reading the other item puts the entry there with the sides swapped.
Both claims are always kept.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

#: The call that goes by neither item: both claims stand.
BOTH = "both"


def conflict_id(a: str, b: str) -> str:
    """The id of the conflict between items *a* and *b*, the same whichever comes first."""
    lo, hi = sorted((a, b))
    return hashlib.sha256(f"conflict:{lo}:{hi}".encode()).hexdigest()[:16]


def pair_of(holder: str, entry: dict) -> tuple[str, str]:
    """The two items an entry held by *holder* is between: the holder, and the item on the other
    side (its ``right_item``, the stored claim's item; ``""`` when the entry names none)."""
    right = str(entry.get("right_item") or "")
    left = str(entry.get("left_item") or "")
    return holder, right if right and right != holder else (left if left != holder else "")


def entries(store: Any, *, limit: int | None = None) -> list[dict]:
    """Every conflict entry now in the library, ``{item_id, item_title, **entry}``, the most
    recently changed holder first, at most *limit* holders when given."""
    # A LIKE prefilter before parsing: `file_metadata` is a JSON blob on every row, and
    # json-loading the whole store to find the few items with conflicts would make this read
    # scale with library size rather than with the number of conflicts.
    sql = (
        "SELECT id, title, file_metadata FROM items "
        "WHERE is_archived = 0 AND file_metadata LIKE '%\"conflicts\"%' ORDER BY updated_at DESC"
    )
    args: tuple = ()
    if limit is not None:
        sql += " LIMIT ?"
        args = (limit,)
    rows: list[dict] = []
    for row in store.db.execute(sql, args):
        try:
            meta = json.loads(row["file_metadata"] or "{}")
        except (TypeError, ValueError):
            continue
        found = meta.get("conflicts") if isinstance(meta, dict) else None
        if not isinstance(found, list):
            continue
        for entry in found:
            if isinstance(entry, dict):
                rows.append({"item_id": row["id"], "item_title": row["title"], **entry})
    return rows


def served(row: dict, choices: dict[str, str]) -> dict:
    """*row* (from :func:`entries`) with its pair's ``id``, the owner's ``choice`` ("" for none)
    and ``prefer`` worked out from it: the side the chosen item is on, "" for :data:`BOTH`, and
    the precedence ladder's own when there is no call."""
    holder, other = pair_of(str(row.get("item_id") or ""), row)
    cid = conflict_id(holder, other) if holder and other else ""
    choice = choices.get(cid, "") if cid else ""
    prefer = str(row.get("prefer") or "")
    if choice == BOTH:
        prefer = ""
    elif choice:
        other_side = "right" if str(row.get("right_item") or "") == other else "left"
        if choice == other:
            prefer = other_side
        elif choice == holder:
            prefer = "left" if other_side == "right" else "right"
    return {**row, "id": cid, "choice": choice, "prefer": prefer}


def pair_items(store: Any, cid: str) -> tuple[str, str] | None:
    """The two items of conflict *cid* while an entry between them stands, else None."""
    for row in entries(store):
        holder, other = pair_of(str(row.get("item_id") or ""), row)
        if holder and other and conflict_id(holder, other) == cid:
            return holder, other
    return None


def undecided(store: Any) -> dict[str, set[str]]:
    """Map of item id → the ids of its conflicts with no call yet, on both items of each pair."""
    choices = store.conflict_choices()
    out: dict[str, set[str]] = {}
    for row in entries(store):
        holder, other = pair_of(str(row.get("item_id") or ""), row)
        if not (holder and other):
            continue
        cid = conflict_id(holder, other)
        if cid in choices:
            continue
        out.setdefault(holder, set()).add(cid)
        out.setdefault(other, set()).add(cid)
    return out
