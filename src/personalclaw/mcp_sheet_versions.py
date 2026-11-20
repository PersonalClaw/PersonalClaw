"""A workbook's next version, as ``sheet_create`` writes it: carried from the version it was read
from, refused when that version holds what no version written from rows can carry, and said in its
reply. The carry itself is ``documents.sheet_carry``; this is the tool's side of it, its refusal and
its words, kept beside ``mcp_artifacts`` rather than in it.
"""

from __future__ import annotations

import logging
from typing import Any

from personalclaw.safety_flags import yes_or_no
from personalclaw.tool_providers.base import tool_failure

logger = logging.getLogger(__name__)

#: How many items of one kind a workbook's refusal or reply names before it counts the rest.
NAMED_ITEMS = 8


def carry_workbook(
    prov: Any, named: Any, expected: Any, model: Any, args: dict[str, Any]
) -> tuple[Any, Any]:
    """``(carried, refusal)``: the next version of the workbook *named*, carried from the version
    the call was read from (``documents.sheet_carry``), or why it is not written. A base that
    holds what no version written here can carry (a chart, conditional formatting, a table, a
    border) is written over only when the call says she agreed (``drop_unsupported``, read as
    the word it spells: what spells neither is a no), and a base whose file cannot be read is not
    written over at all."""
    from personalclaw.documents.sheet_carry import carry_from_base
    from personalclaw.documents.xlsx_parser import parse_xlsx

    version = expected if isinstance(expected, int) else None
    unread = tool_failure(
        f"could not read version {version or named.version} of '{named.slug}' to keep its "
        "formulas and formatting. Nothing was written."
    )
    raw = prov.raw_bytes(named.slug, version=version)
    if raw is None:
        return None, unread
    try:
        base, losses = parse_xlsx(raw[0])
    except Exception:  # noqa: BLE001 — a base that cannot be read is not written over
        logger.warning("sheet_create: could not read %s's base to carry it", named.slug)
        return None, unread
    carried = carry_from_base(model, base, losses)
    if carried.unsupported and yes_or_no(args.get("drop_unsupported")) is not True:
        return None, tool_failure(
            f"'{named.slug}' holds what a version written with sheet_create cannot carry: "
            f"{named_items(carried.unsupported)}. Writing this version would drop them. Ask the "
            "user first, naming them; if they agree, call sheet_create again with "
            "drop_unsupported: true. Nothing was written.",
            code="ERR_WORKBOOK_FEATURES_DROPPED",
        )
    return carried, None


def named_items(items: list[Any]) -> str:
    """A workbook's loss items as a refusal or a reply names them, each with where it is."""
    said = "; ".join(f"{item.detail} ({item.location})" for item in items[:NAMED_ITEMS])
    more = len(items) - NAMED_ITEMS
    return said + (f"; and {more} more" if more > 0 else "")


def carry_note(carried: Any) -> str:
    """What a workbook's next version kept of the version it was read from, and what it
    replaced or dropped, for its reply: one line, ``""`` for a call that carried nothing."""
    if carried is None:
        return ""
    said: list[str] = []
    if kept := len(carried.formulas_kept):
        said.append(f"Kept {kept} formula{'' if kept == 1 else 's'}.")
    for found, words in (
        (carried.formulas_replaced, "{0}'s formula {1} was replaced by a value."),
        (carried.formulas_removed, "{0}'s formula {1} is not in this version."),
    ):
        said += [words.format(where, formula) for where, formula in found[:NAMED_ITEMS]]
        if len(found) > NAMED_ITEMS:
            said.append(f"And {len(found) - NAMED_ITEMS} more like it.")
    said += [
        f"{ref} is no longer merged: a cell inside it now holds a value."
        for ref in carried.merges_dropped
    ]
    said += [
        f"Sheet '{name}' of the version you read is not in this one."
        for name in carried.sheets_left_out
    ]
    if carried.unsupported:
        said.append(f"This version does not carry: {named_items(carried.unsupported)}.")
    return "\n" + " ".join(said) if said else ""


__all__ = ["NAMED_ITEMS", "carry_note", "carry_workbook", "named_items"]
