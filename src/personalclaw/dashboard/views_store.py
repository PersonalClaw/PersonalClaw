"""The dashboard-as-views registry (AMBIENT-SURFACES §1 / A2-1).

One JSON store under the home — ``dashboard_views.json`` — holds the composable
home: named VIEWS over one widget registry. A view is an ordered list of tile
REFS + size hints, never coordinates. The retirement's law (DashboardPage.tsx:24 —
"the customizable grid + per-user layout persistence were retired") stays law: a
tile has a ``ref``, a ``size``, and an ``order`` index, and **nothing spatial** —
no x/y/w/h grid, no drag canvas.

Two facts make byte-identical safety hold:

1. **Presets are read-only.** The "Overview" preset's CORE composition (the eight
   first-party widgets in today's order) is code-defined and immutable — CRUD
   refuses to edit or delete a preset. First-party widgets stay hard imports; the
   registry covers artifact-backed tiles ONLY (§1.2).
2. **The registry is additive.** Artifact tiles (``artifact:<slug>``) pinned to a
   view are user data, overlaid onto the active view. An EMPTY registry ⇒ no
   artifact tiles ⇒ the dashboard renders exactly today's fixed layout. That is the
   critical safety property: a fresh install sees the current ``DashboardPage``.

So the on-disk store persists only what is user-owned: user-created views (full) and
the artifact tiles overlaid on each view (presets included). A preset's core refs are
never written — they are reconstructed from code on every load, so a preset can never
drift from the shipped layout.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

from personalclaw.atomic_write import atomic_write
from personalclaw.config.loader import config_dir

logger = logging.getLogger(__name__)

_STORE_FILENAME = "dashboard_views.json"

#: A tile size is a HINT to the band's flow layout, not coordinates.
_SIZES = ("s", "m", "l", "full")
#: Who added a tile. An ``agent`` row is a PROPOSAL (renders with an accept/dismiss
#: chip); the agent never silently rearranges the user's home (§1.3 propose-don't-pin).
_ADDED_BY = ("user", "agent")

#: The Overview preset id — the default home.
PRESET_OVERVIEW_ID = "overview"

#: The eight core widgets in today's ``DashboardPage`` order (HeroPulse, ActionCenter,
#: ActiveWork, Tasks, Suggestions, Discover, Schedule/"Recent activity", SystemHealth).
#: This is the Overview preset's DECLARED composition — the FE renders the fixed
#: layout for ``core:`` refs (first-party widgets stay hard imports), so the schema is
#: composition-ready for the later compose-editor without making the render dynamic.
_OVERVIEW_CORE_REFS = (
    "core:hero-pulse",
    "core:action-center",
    "core:active-work",
    "core:tasks",
    "core:suggestions",
    "core:discover",
    "core:schedule",
    "core:system-health",
)


class PresetLockedError(Exception):
    """Raised when a write targets a locked preset (edit/delete refused)."""


class ViewNotFoundError(Exception):
    """Raised when a view id resolves to no preset and no user view."""


@dataclass
class DashboardTile:
    """One tile in a view: a content ref + a size hint + an order index.

    Deliberately carries NO x/y/w/h — the coordinate grid is the failure mode that
    got the bento dashboard retired. ``size`` + ``order`` feed a simple flow layout.
    """

    ref: str  # "core:<widget>" | "artifact:<slug>" — the ONLY content pointer
    size: str = "m"  # "s" | "m" | "l" | "full" — a flow-layout hint, never coordinates
    order: int = 0  # explicit ordering within the view
    added_by: str = "user"  # "user" | "agent" — agent rows are proposals


@dataclass
class DashboardView:
    """A named view: ordered tile refs + size hints. ``preset`` locks it read-only."""

    id: str
    name: str
    icon: str | None = None
    nav_pinned: bool = False
    preset: bool = False
    tiles: list[DashboardTile] = field(default_factory=list)


def views_path() -> Path:
    """Home-scoped location of ``dashboard_views.json``."""
    return config_dir() / _STORE_FILENAME


def _empty_disk() -> dict:
    # ``views`` = user-created views (full); ``overlay`` = artifact tiles pinned to a
    # view id (presets included) — the only mutable surface on a locked preset.
    return {"views": [], "overlay": {}}


def _read_disk() -> dict:
    p = views_path()
    if not p.exists():
        return _empty_disk()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("dashboard_views.json unreadable; treating as empty", exc_info=True)
        return _empty_disk()
    if not isinstance(data, dict):
        return _empty_disk()
    data.setdefault("views", [])
    data.setdefault("overlay", {})
    if not isinstance(data["views"], list):
        data["views"] = []
    if not isinstance(data["overlay"], dict):
        data["overlay"] = {}
    return data


def _write_disk(data: dict) -> None:
    atomic_write(views_path(), json.dumps(data, indent=2) + "\n")


def _tile_from_dict(d: dict) -> DashboardTile:
    ref = str(d.get("ref", "")).strip()
    size = str(d.get("size", "m"))
    added_by = str(d.get("added_by", "user"))
    try:
        order = int(d.get("order", 0))
    except (TypeError, ValueError):
        order = 0
    return DashboardTile(
        ref=ref,
        size=size if size in _SIZES else "m",
        order=order,
        added_by=added_by if added_by in _ADDED_BY else "user",
    )


def _overlay_tiles(data: dict, view_id: str) -> list[DashboardTile]:
    """Artifact tiles overlaid on ``view_id`` (sorted by order), from disk."""
    raw = data["overlay"].get(view_id, [])
    if not isinstance(raw, list):
        return []
    tiles = [_tile_from_dict(t) for t in raw if isinstance(t, dict) and t.get("ref")]
    return sorted(tiles, key=lambda t: t.order)


def _overview_preset(data: dict) -> DashboardView:
    """Build the locked Overview preset: code-defined core refs + persisted overlay."""
    core = [
        DashboardTile(ref=ref, size="m", order=i, added_by="user")
        for i, ref in enumerate(_OVERVIEW_CORE_REFS)
    ]
    overlay = _overlay_tiles(data, PRESET_OVERVIEW_ID)
    # Overlay tiles order AFTER the core band (they append; the registry is additive).
    for j, t in enumerate(overlay):
        t.order = len(core) + j
    return DashboardView(
        id=PRESET_OVERVIEW_ID,
        name="Overview",
        icon="LayoutDashboard",
        nav_pinned=True,
        preset=True,
        tiles=core + overlay,
    )


def _presets(data: dict) -> list[DashboardView]:
    """All locked presets, in nav order. Only Overview ships in AS-1 (Mission Control
    is AS-8, gated on INBOX-NOTIFICATIONS-UNIFICATION)."""
    return [_overview_preset(data)]


def _user_view_from_dict(d: dict) -> DashboardView:
    tiles = [_tile_from_dict(t) for t in d.get("tiles", []) if isinstance(t, dict) and t.get("ref")]
    return DashboardView(
        id=str(d.get("id", "")),
        name=str(d.get("name", "")),
        icon=(str(d["icon"]) if d.get("icon") else None),
        nav_pinned=bool(d.get("nav_pinned", False)),
        preset=False,
        tiles=sorted(tiles, key=lambda t: t.order),
    )


def load_views() -> list[DashboardView]:
    """Every view: locked presets first, then user-created views."""
    data = _read_disk()
    user = [_user_view_from_dict(v) for v in data["views"] if isinstance(v, dict) and v.get("id")]
    return [*_presets(data), *user]


def list_views() -> list[dict]:
    """Views as JSON-safe dicts — the ``/api/dashboard/views`` list payload."""
    return [asdict(v) for v in load_views()]


def get_view(view_id: str) -> DashboardView | None:
    for v in load_views():
        if v.id == view_id:
            return v
    return None


def _is_preset(view_id: str) -> bool:
    return any(p.id == view_id for p in _presets(_empty_disk()))


def create_view(name: str, icon: str | None = None) -> DashboardView:
    """Create an empty user view. The compose-editor (later) fills its tiles; S1 ships
    presets, so a fresh user view starts empty and composable against this schema."""
    import uuid

    name = name.strip()
    if not name:
        raise ValueError("view name is required")
    data = _read_disk()
    view_id = uuid.uuid4().hex[:8]
    data["views"].append(
        {"id": view_id, "name": name, "icon": icon or None, "nav_pinned": False, "tiles": []}
    )
    _write_disk(data)
    return DashboardView(id=view_id, name=name, icon=icon or None, preset=False, tiles=[])


def update_view(view_id: str, patch: dict) -> DashboardView:
    """Update a user view's metadata. Presets refuse edit (they are code-locked)."""
    if _is_preset(view_id):
        raise PresetLockedError(f"'{view_id}' is a preset and cannot be edited")
    data = _read_disk()
    for v in data["views"]:
        if v.get("id") == view_id:
            if "name" in patch and str(patch["name"]).strip():
                v["name"] = str(patch["name"]).strip()
            if "icon" in patch:
                v["icon"] = str(patch["icon"]) if patch["icon"] else None
            if "nav_pinned" in patch:
                v["nav_pinned"] = bool(patch["nav_pinned"])
            _write_disk(data)
            return _user_view_from_dict(v)
    raise ViewNotFoundError(view_id)


def delete_view(view_id: str) -> None:
    """Delete a user view. Presets refuse deletion."""
    if _is_preset(view_id):
        raise PresetLockedError(f"'{view_id}' is a preset and cannot be deleted")
    data = _read_disk()
    before = len(data["views"])
    data["views"] = [v for v in data["views"] if v.get("id") != view_id]
    if len(data["views"]) == before:
        raise ViewNotFoundError(view_id)
    data["overlay"].pop(view_id, None)
    _write_disk(data)


def _max_tiles() -> int:
    """The AmbientConfig cap on tiles per view (default 12)."""
    try:
        from personalclaw.config.loader import AppConfig

        return int(AppConfig.load().ambient.max_tiles)
    except Exception:
        return 12


def add_tile(view_id: str, ref: str, size: str = "m", added_by: str = "user") -> DashboardView:
    """Pin an artifact tile to a view (the pin-to-dashboard / propose path).

    Only ``artifact:<slug>`` refs are addable — first-party ``core:`` widgets stay
    hard imports and are never registry entries (§1.2). Adding to a preset writes to
    the view's overlay, never the locked core composition. Bounded by ``max_tiles``.
    """
    ref = ref.strip()
    if not ref.startswith("artifact:"):
        raise ValueError("only artifact:<slug> tiles can be added (core widgets are hard imports)")
    if size not in _SIZES:
        size = "m"
    if added_by not in _ADDED_BY:
        added_by = "user"
    if not _is_preset(view_id) and get_view(view_id) is None:
        raise ViewNotFoundError(view_id)

    data = _read_disk()
    tiles = data["overlay"].setdefault(view_id, [])
    if not isinstance(tiles, list):
        tiles = []
        data["overlay"][view_id] = tiles
    # Idempotent: pinning an already-pinned slug is a no-op (never a duplicate tile).
    for t in tiles:
        if isinstance(t, dict) and t.get("ref") == ref:
            return get_view(view_id)  # type: ignore[return-value]
    cap = _max_tiles()
    if len([t for t in tiles if isinstance(t, dict)]) >= cap:
        raise ValueError(f"view is at its tile cap ({cap}); unpin one first")
    tiles.append({"ref": ref, "size": size, "order": len(tiles), "added_by": added_by})
    _write_disk(data)
    return get_view(view_id)  # type: ignore[return-value]


def resolve_tile(view_id: str, ref: str, keep: bool) -> DashboardView:
    """Accept or remove an overlay tile.

    ``keep=True`` accepts an agent-proposed tile (flips ``added_by`` → ``user``, so it
    stops rendering as a proposal); ``keep=False`` removes it (dismiss a proposal, or
    unpin a user tile). Both are the human's decision — the agent only proposes.
    """
    ref = ref.strip()
    data = _read_disk()
    tiles = data["overlay"].get(view_id)
    if not isinstance(tiles, list):
        raise ViewNotFoundError(view_id)
    if keep:
        found = False
        for t in tiles:
            if isinstance(t, dict) and t.get("ref") == ref:
                t["added_by"] = "user"
                found = True
        if not found:
            raise ViewNotFoundError(f"{view_id}:{ref}")
    else:
        kept = [t for t in tiles if not (isinstance(t, dict) and t.get("ref") == ref)]
        if len(kept) == len(tiles):
            raise ViewNotFoundError(f"{view_id}:{ref}")
        data["overlay"][view_id] = kept
        # Re-pack order so the flow layout has no gaps after a removal.
        for i, t in enumerate(data["overlay"][view_id]):
            if isinstance(t, dict):
                t["order"] = i
    _write_disk(data)
    return get_view(view_id)  # type: ignore[return-value]
