"""AMBIENT-SURFACES AS-8 — the locked Mission Control preset.

AS-1 shipped the dashboard-as-views registry with ONE preset and a comment reserving
the second for AS-8 (gated on INBOX-NOTIFICATIONS-UNIFICATION, now done). This covers
the server third of AS-8's done-when clause: a **locked** "Mission Control" view whose
DECLARED composition is the four attention lanes, in triage order.

The lane refs asserted here are a CONTRACT with the frontend — ``attentionLanes.ts`` /
``MissionControl.tsx`` render by these exact strings, so a rename is a breaking change
on both sides, not a local edit.

Isolation: every test binds an isolated home via ``PERSONALCLAW_HOME`` AND
monkeypatches the store's imported ``config_dir`` symbol, so nothing touches the real
``~/.personalclaw`` (same two-lever pattern as ``test_dashboard_views.py``).
"""

from __future__ import annotations

import json

import pytest

from personalclaw.dashboard import views_store as store

#: The four lane refs, verbatim, in triage order. Duplicated from the module ON PURPOSE:
#: asserting against ``store._MISSION_CONTROL_CORE_REFS`` alone would pass through any
#: rename, and the FE codes against these literals.
LANE_REFS = [
    "core:lane-needs-approval",
    "core:lane-your-turn",
    "core:lane-working",
    "core:lane-idle",
]

#: The eight Overview refs, verbatim — a second preset must not perturb the first.
OVERVIEW_REFS = [
    "core:hero-pulse",
    "core:action-center",
    "core:active-work",
    "core:tasks",
    "core:suggestions",
    "core:discover",
    "core:schedule",
    "core:system-health",
]


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setattr("personalclaw.dashboard.views_store.config_dir", lambda: home)
    return home


# ── The preset resolves, and is locked ──────────────────────────────────────


def test_mission_control_resolves_as_a_locked_preset_with_four_lanes():
    view = store.get_view(store.PRESET_MISSION_CONTROL_ID)
    assert view is not None
    assert view.id == "mission-control"
    assert view.name == "Mission Control"
    assert view.preset is True
    # Exactly the four lanes, in declared triage order — nothing more on a fresh home.
    assert [t.ref for t in view.tiles] == LANE_REFS
    assert store._MISSION_CONTROL_CORE_REFS == tuple(LANE_REFS)


def test_mission_control_is_nav_pinned_with_a_resolvable_icon():
    """A preset nobody can find is a preset nobody uses; the icon name must be a real
    lucide-react export (``resolveAppIcon`` falls back to a generic block otherwise)."""
    view = store.get_view(store.PRESET_MISSION_CONTROL_ID)
    assert view.nav_pinned is True
    assert view.icon == "Radar"


def test_mission_control_refuses_edit():
    with pytest.raises(store.PresetLockedError):
        store.update_view(store.PRESET_MISSION_CONTROL_ID, {"name": "Renamed"})


def test_mission_control_refuses_delete():
    # Separate call site from update_view's guard — both must be covered.
    with pytest.raises(store.PresetLockedError):
        store.delete_view(store.PRESET_MISSION_CONTROL_ID)


# ── The first preset is untouched ────────────────────────────────────────────


def test_both_presets_listed_and_overview_is_unchanged():
    views = store.list_views()
    ids = [v["id"] for v in views]
    assert ids[:2] == ["overview", "mission-control"], "Overview stays the default home"
    overview = next(v for v in views if v["id"] == "overview")
    assert [t["ref"] for t in overview["tiles"]] == OVERVIEW_REFS
    assert overview["preset"] is True and overview["nav_pinned"] is True
    assert overview["icon"] == "LayoutDashboard"
    # And the two presets share no refs — the ``lane-`` segment is doing its job.
    assert not set(LANE_REFS) & set(OVERVIEW_REFS)


# ── Overlay ordering (the Overview invariant, held for the new preset) ───────


def test_overlay_tile_orders_after_the_four_core_lanes():
    store.add_tile(store.PRESET_MISSION_CONTROL_ID, "artifact:board")
    view = store.get_view(store.PRESET_MISSION_CONTROL_ID)
    refs = [t.ref for t in view.tiles]
    assert refs == [*LANE_REFS, "artifact:board"]
    overlaid = next(t for t in view.tiles if t.ref == "artifact:board")
    assert overlaid.order == len(LANE_REFS)
    assert all(t.order < overlaid.order for t in view.tiles if t.ref in LANE_REFS)


# ── Vacuity floor: resolution DISCRIMINATES ─────────────────────────────────


def test_presets_are_exactly_two_and_a_bogus_id_does_not_resolve():
    presets = store._presets(store._empty_disk())
    assert [p.id for p in presets] == ["overview", "mission-control"]
    assert all(p.preset for p in presets)
    # ``get_view`` returns None for an unknown id; the WRITE paths raise.
    assert store.get_view("not-a-view") is None
    assert store._is_preset("not-a-view") is False
    with pytest.raises(store.ViewNotFoundError):
        store.update_view("not-a-view", {"name": "x"})
    with pytest.raises(store.ViewNotFoundError):
        store.delete_view("not-a-view")


# ── Presets are code-defined, never persisted as user views ─────────────────


def test_preset_never_round_trips_to_disk_as_a_user_view():
    """If Mission Control landed in ``dashboard_views.json`` under ``views`` it would
    become editable and the lock would be a fiction."""
    # Force a write, and add an overlay tile so the file definitely exists.
    store.add_tile(store.PRESET_MISSION_CONTROL_ID, "artifact:board")
    disk = json.loads(store.views_path().read_text())
    assert [v.get("id") for v in disk["views"]] == []
    # The overlay is persisted; the preset's own composition is NOT.
    assert "mission-control" in disk["overlay"]
    assert [t["ref"] for t in disk["overlay"]["mission-control"]] == ["artifact:board"]
    assert not any(r in json.dumps(disk["views"]) for r in LANE_REFS)
    # Re-reading rebuilds the lanes from code, and the lock still holds.
    reread = store.get_view(store.PRESET_MISSION_CONTROL_ID)
    assert [t.ref for t in reread.tiles] == [*LANE_REFS, "artifact:board"]
    with pytest.raises(store.PresetLockedError):
        store.delete_view(store.PRESET_MISSION_CONTROL_ID)


def test_a_user_view_named_like_the_preset_is_still_not_a_preset():
    """Same-name user view must not inherit the lock or displace the preset."""
    created = store.create_view("Mission Control")
    assert created.id != "mission-control"
    assert created.preset is False
    ids = [v.id for v in store.load_views()]
    assert ids[:2] == ["overview", "mission-control"]
    assert created.id in ids[2:]
    # And it IS editable — proving the lock is keyed on the preset id, not the name.
    store.update_view(created.id, {"name": "Renamed"})
    store.delete_view(created.id)


def test_the_frontend_names_the_same_four_refs_as_the_registry():
    """The cross-language drift rail — the ONLY check that can fail on this mismatch.

    The registry and the view that renders it were built on separate branches against a
    contract written in prose, and they disagreed: `core:lane-*` here, `core:attention-*`
    in `MissionControl.tsx`. Nothing caught it. A tile ref is a plain string on both sides,
    so `make lint`, mypy and both suites were green while the view's ref map named four
    tiles the preset does not register — the frontend would have looked correct and matched
    nothing.

    So this reads the frontend's map out of the file and asserts set equality. It is a
    source-text rail, which is weaker than an import, but it is the only rail that spans
    the language boundary at all.
    """
    import pathlib
    import re

    tsx = pathlib.Path(__file__).resolve().parents[1] / "web/src/pages/dashboard/MissionControl.tsx"
    assert tsx.exists(), f"{tsx} moved — re-point this rail"
    body = tsx.read_text()
    block = re.search(r"export const LANE_REFS[^{]*\{(.*?)\n\}", body, re.S)
    assert (
        block
    ), "LANE_REFS is no longer a literal object in MissionControl.tsx — re-derive this rail"
    fe_refs = set(re.findall(r"'(core:[^']+)'", block.group(1)))
    # Vacuity floor: a regex that matched an empty block would make the comparison below
    # trivially true against an empty set, which is exactly how a rail reads clean forever.
    assert len(fe_refs) == 4, f"parsed {len(fe_refs)} refs out of LANE_REFS, expected 4: {fe_refs}"
    assert fe_refs == set(store._MISSION_CONTROL_CORE_REFS), (
        "the Mission Control view's LANE_REFS and the registry's _MISSION_CONTROL_CORE_REFS name "
        f"different tiles.\n  frontend: {sorted(fe_refs)}\n  registry: "
        f"{sorted(store._MISSION_CONTROL_CORE_REFS)}"
    )
