"""UI-originated tile re-fire, fenced by the tile's FROZEN capability set.

AMBIENT-SURFACES §5.4, third routing case: *"tile widgets → actions run through the
tile's bound workflow (re-fire with bound args), subject to the trigger's frozen
capability set — a rendered button can never introduce actions the trigger didn't
declare (the frozen action-set invariant applies to UI-originated fires too)."*

🔴 WHAT THE FENCE IS HERE, precisely. A tile's body is MODEL-AUTHORED (a genui tree a
workflow or a chat turn wrote), and it renders in the host React tree. So the action name
arriving on this endpoint is untrusted text that a model chose. Without a fence, a
rendered button could name any action provider and the dashboard would dispatch it — the
"a widget introduces a capability nobody consented to" escalation. The fence is the SAVED
binding: the tile's `refresh.data` nodes are the only workflow it has, so the providers
those nodes declare are the only providers a UI-originated fire may reach. The frozen set
is DERIVED FROM PERSISTED STATE, never from the request.

Why the trigger fence's READ-ONLY DEFAULT is deliberately NOT applied. `firepath`'s
capability gate lets a read-only provider through with no `capabilities` block at all,
because no writer populated that block and enforcing without the default would refuse
every automation in existence (S116). A tile's set has the opposite property: it is
derived from the tile's own saved nodes, so it is never empty-by-omission — and applying
the default here would let a button reach a read-only provider the tile never declared,
which is exactly the sentence this module exists to make false. Membership in
`tile_refresh.DATA_PROVIDERS` is checked TOO (defense in depth): the fence bounds what
this tile may do, the allowlist bounds what any tile may do.

DEVIATION (recorded in the plan's execution log): the criterion says "the *trigger's*
frozen capability set", but a tile cannot bind a trigger yet — `TileRefresh.mode: "view"`
(the bound AUTOMATION-SUBSTRATE view trigger) is deliberately absent until substrate step
8. So the frozen set is read from the tile's binding today, through `frozen_capabilities`,
which is the ONE place a `mode: "view"` tile will later read `Trigger.capabilities`
instead. The invariant and its enforcement point do not move.
"""

from __future__ import annotations

import logging
from typing import Any

from personalclaw.dashboard import views_store as store

logger = logging.getLogger(__name__)

#: The bare re-fire: "run this tile's bound workflow again". Recognized by name because a
#: refresh button and a genui `Button(action: "refresh")` are the same request, and giving
#: the second one its own vocabulary would make them drift.
REFRESH_ACTION = "refresh"

#: Refusal codes. Named so the FE can tell "outside the frozen set" (the user needs to
#: re-bind the tile) from "this tile has no bound workflow" (nothing to re-fire).
CODE_REFUSED = "tile_capability_refused"
CODE_NOT_BOUND = "tile_not_bound"
CODE_NOT_FOUND = "tile_not_found"


def frozen_capabilities(tile: store.DashboardTile) -> dict[str, Any]:
    """The capability block this tile's SAVED binding implies.

    Mirrors `triggers.screen.capabilities_for_action`: the block is DERIVED from the action
    the tile already carries rather than restated by the author, so binding a tile IS the
    opt-in for the providers that binding names — and nothing else.

    A tile with no data nodes yields ``{"providers": []}``, which `capability_allows`
    refuses for every value (`EMPTY_MEANS = "deny"`). That is the right answer: a tile with
    no bound workflow has no action a button could re-fire.
    """
    providers = sorted(
        {(n.provider or "").strip() for n in tile.refresh.data if (n.provider or "").strip()}
    )
    return {"providers": providers}


def requested_providers(tile: store.DashboardTile, action: str) -> list[str]:
    """The providers a UI action wants, as the capability gate's `requested` values.

    Three shapes, in order:

    * ``refresh`` — the bare re-fire: every provider the binding declares (so an empty
      binding requests nothing and is refused as "not bound" rather than as a violation).
    * a declared NODE ID — that node's provider. This is the useful form: a button named
      after the binding's own node re-fires exactly that node.
    * anything else — the name itself, verbatim. A model-authored name nobody declared must
      arrive at the gate as a REQUEST FOR THAT NAME so the refusal says which action was
      outside the set. Mapping it to "nothing requested" would let it through silently,
      which is how a fence becomes decorative.
    """
    name = (action or "").strip()
    if not name or name == REFRESH_ACTION:
        return sorted(
            {(n.provider or "").strip() for n in tile.refresh.data if (n.provider or "").strip()}
        )
    for node in tile.refresh.data:
        if node.id == name:
            return [(node.provider or "").strip()]
    return [name]


def check(tile: store.DashboardTile, action: str) -> dict[str, Any]:
    """Decide whether `action` may re-fire `tile`. Pure — no dispatch, no I/O.

    Returns ``{"ok": True, "providers": [...]}`` or a refusal carrying the `violations`
    list `unfenced_actions` produced, so the row/response says WHICH action was outside the
    set rather than merely that one was.
    """
    from personalclaw.dashboard.tile_refresh import DATA_PROVIDERS
    from personalclaw.triggers.screen import unfenced_actions

    frozen = frozen_capabilities(tile)
    wanted = [p for p in requested_providers(tile, action) if p]
    if not wanted:
        return {
            "ok": False,
            "code": CODE_NOT_BOUND,
            "message": "this tile has no bound workflow to re-fire",
            "violations": [],
        }
    violations = unfenced_actions(frozen, requested={"providers": wanted})
    # Defense in depth: the frozen set bounds THIS tile, the allowlist bounds EVERY tile. A
    # provider that somehow got persisted into a binding but is not a data provider is
    # refused here rather than dispatched — the same posture `refresh_tile` takes.
    for provider in wanted:
        if provider not in DATA_PROVIDERS:
            violations.append(
                (
                    "providers",
                    provider,
                    f"{provider!r} is not a tile data provider; a dashboard tile may only "
                    f"dispatch {', '.join(DATA_PROVIDERS)}",
                )
            )
    if violations:
        named = ", ".join(f"{k}={v}" for k, v, _ in violations[:3])
        return {
            "ok": False,
            "code": CODE_REFUSED,
            "message": f"action outside the tile's frozen capability set: {named}",
            "violations": [list(v) for v in violations],
        }
    return {"ok": True, "providers": wanted, "violations": []}


async def refire(view_id: str, ref: str, *, action: str, payload: Any = None) -> dict[str, Any]:
    """Re-fire a tile's bound workflow from a genui control inside its widget.

    The fence runs BEFORE any dispatch, and the dispatch itself is `refresh_tile(force=True)`
    — the SAME path the tile's own refresh button uses, so a UI-originated fire cannot skip
    the unattended gates (`incident_active`, `enforce_action(session_key="tile:…")`) or
    write a different ledger shape than an ordinary refresh. A second dispatcher for
    "the same thing, but from a button" is how two paths drift into one bug.
    """
    from personalclaw.dashboard import tile_refresh

    tile = store.find_tile(view_id, ref)
    if tile is None:
        return {"ok": False, "code": CODE_NOT_FOUND, "message": "view or tile not found"}
    verdict = check(tile, action)
    if not verdict["ok"]:
        logger.info(
            "tile action refused: view=%s ref=%s action=%r code=%s",
            view_id,
            ref,
            action,
            verdict["code"],
        )
        return verdict
    result = await tile_refresh.refresh_tile(view_id, ref, force=True)
    out = result.to_dict()
    # `ok` here means "the fence passed AND the re-fire ran", which is what the caller
    # renders. The refresh's own ok/reason ride along untouched.
    return {
        "ok": bool(result.ok),
        "outcome": "tile-refired",
        "action": (action or "").strip() or REFRESH_ACTION,
        "providers": verdict["providers"],
        "refresh": out,
        "row": out.get("row"),
        "payload_keys": sorted(payload.keys()) if isinstance(payload, dict) else [],
    }
