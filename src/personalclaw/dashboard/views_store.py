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
#: How a tile stays fresh. ``view`` (a bound AUTOMATION-SUBSTRATE trigger) is NOT here until
#: its runtime is — see :class:`TileRefresh`.
_REFRESH_MODES = ("manual", "ttl")

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


#: The Mission Control preset id — the attention-triage surface (AS-8).
PRESET_MISSION_CONTROL_ID = "mission-control"

#: The four attention lanes, in triage order (most-blocking first): what needs YOU to
#: approve, what needs YOU to answer, what the system is doing unattended, what is
#: parked. Same DECLARED-composition contract as ``_OVERVIEW_CORE_REFS`` — the FE
#: renders a fixed lane layout for these ``core:`` refs and sources every lane from the
#: unified attention store (INBOX-NOTIFICATIONS-UNIFICATION), so the schema is
#: composition-ready without making the render dynamic. The ``lane-`` segment is load
#: bearing: it keeps a lane ref from colliding with a same-named Overview widget
#: (``core:lane-working`` vs ``core:active-work``).
_MISSION_CONTROL_CORE_REFS = (
    "core:lane-needs-approval",
    "core:lane-your-turn",
    "core:lane-working",
    "core:lane-idle",
)


class PresetLockedError(Exception):
    """Raised when a write targets a locked preset (edit/delete refused)."""


class ViewNotFoundError(Exception):
    """Raised when a view id resolves to no preset and no user view."""


@dataclass
class TileDataNode:
    """One data source feeding a live tile's skeleton (AMBIENT-SURFACES §2.1).

    The plan's "bound data workflow (degenerate case: one action node)" — so a data node
    IS an action-provider dispatch, and a tile's ``data`` list is the whole workflow. Its
    ``id`` is the binding name: a node with ``id: "runs"`` fills ``{{nodes.runs.output}}``
    in the skeleton.

    ``provider`` is checked against a READ-ONLY allowlist at refresh time
    (:data:`personalclaw.dashboard.tile_refresh.DATA_PROVIDERS`) — a TTL tile fires with no
    human present, so `bash` behind a dashboard panel would be an unattended-execution
    surface the user never consented to.
    """

    id: str
    provider: str
    config: dict = field(default_factory=dict)


@dataclass
class TileRefresh:
    """How a tile stays fresh (§1.1 ``refresh``, §2.1 the layout/data split).

    ``mode``:

    * ``manual`` — only the tile's refresh button (the default; a pinned static artifact).
    * ``ttl`` — re-render when ``ttl_secs`` have elapsed since the last refresh. The
      pre-substrate cadence; ``0`` means "use ``AmbientConfig.default_refresh_ttl_secs``".

    ``mode: "view"`` (a bound AUTOMATION-SUBSTRATE view trigger) is deliberately absent: it
    is a later IN-PLACE ttl→view upgrade (EXT:AUTOMATION-SUBSTRATE step 8), and a declared
    mode with no runtime is the failure shape this repo has been burned by. Adding it when
    the substrate lands changes this literal and the dispatch in ``tile_refresh``, nothing else.

    ``skeleton`` is the slug of the artifact holding the ``{{...}}`` body — a SEPARATE
    artifact from the tile's own ``ref``. That split is the point: the skeleton is authored
    once (by a chat turn or a workflow stage) and stays intact, while the tile's artifact
    holds the RENDERED projection. Storing both in one artifact would mean the first refresh
    overwrote the very skeleton the next refresh needs.
    """

    mode: str = "manual"
    ttl_secs: int = 0
    skeleton: str = ""
    data: list[TileDataNode] = field(default_factory=list)


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
    #: The data seam (§2.1). Still not spatial — a refresh binding, not a coordinate.
    refresh: TileRefresh = field(default_factory=TileRefresh)


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


def _refresh_from_dict(d: object) -> TileRefresh:
    """Parse a tile's refresh binding. FAIL-OPEN (the storage convention for a
    user-facing availability surface): anything unreadable degrades to ``manual``, which
    refreshes only when the user presses the button. The opposite default would have a
    corrupt registry firing data fetches on a cadence nobody asked for."""
    if not isinstance(d, dict):
        return TileRefresh()
    mode = str(d.get("mode", "manual"))
    if mode not in _REFRESH_MODES:
        mode = "manual"
    try:
        ttl = int(d.get("ttl_secs", 0) or 0)
    except (TypeError, ValueError):
        ttl = 0
    nodes: list[TileDataNode] = []
    for raw in d.get("data") or []:
        if not isinstance(raw, dict):
            continue
        node_id = str(raw.get("id", "")).strip()
        provider = str(raw.get("provider", "")).strip()
        if not node_id or not provider:
            continue
        cfg = raw.get("config")
        nodes.append(
            TileDataNode(id=node_id, provider=provider, config=cfg if isinstance(cfg, dict) else {})
        )
    return TileRefresh(
        mode=mode,
        ttl_secs=max(0, ttl),
        skeleton=str(d.get("skeleton", "") or "").strip(),
        data=nodes,
    )


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
        refresh=_refresh_from_dict(d.get("refresh")),
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


def _mission_control_preset(data: dict) -> DashboardView:
    """Build the locked Mission Control preset: four attention lanes + persisted overlay."""
    core = [
        DashboardTile(ref=ref, size="m", order=i, added_by="user")
        for i, ref in enumerate(_MISSION_CONTROL_CORE_REFS)
    ]
    overlay = _overlay_tiles(data, PRESET_MISSION_CONTROL_ID)
    # Overlay tiles order AFTER the core band (they append; the registry is additive).
    for j, t in enumerate(overlay):
        t.order = len(core) + j
    return DashboardView(
        id=PRESET_MISSION_CONTROL_ID,
        name="Mission Control",
        icon="Radar",
        nav_pinned=True,
        preset=True,
        tiles=core + overlay,
    )


def _presets(data: dict) -> list[DashboardView]:
    """All locked presets, in nav order. Overview stays FIRST — it is the default home,
    and a second preset must never displace it."""
    return [_overview_preset(data), _mission_control_preset(data)]


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


def set_tile_refresh(view_id: str, ref: str, patch: dict) -> DashboardTile:
    """Bind (or unbind) a tile's refresh (§2.1). Returns the tile as stored.

    Validation happens in :func:`_refresh_from_dict`, so an unrecognized mode lands as
    ``manual`` rather than being rejected — the same fail-open the reader uses, applied at
    the write so what the caller reads back is what a refresh will actually honor.
    """
    ref = ref.strip()
    data = _read_disk()
    tiles = data["overlay"].get(view_id)
    if not isinstance(tiles, list):
        raise ViewNotFoundError(view_id)
    for t in tiles:
        if isinstance(t, dict) and t.get("ref") == ref:
            t["refresh"] = asdict(_refresh_from_dict(patch))
            _write_disk(data)
            return _tile_from_dict(t)
    raise ViewNotFoundError(f"{view_id}:{ref}")


def find_tile(view_id: str, ref: str) -> DashboardTile | None:
    """The tile ``ref`` in ``view_id``, or None. The refresh path's lookup."""
    view = get_view(view_id)
    if view is None:
        return None
    for t in view.tiles:
        if t.ref == ref.strip():
            return t
    return None


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
