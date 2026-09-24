"""`DCU-7` — the human-facing live view + cursor-motion overlay GRANT NOTHING.

DESKTOP-COMPUTER-USE §3 floor 7 in test form. The atom's done_when is *"the views render;
neither adds any agent capability — asserted by confirming the tool surface is unchanged
with the views on"*, and that clause is a census, not a vibe, so it is asserted three ways
that fail independently:

* **The tool surface is byte-identical with the views on**
  (:func:`test_the_tool_surface_is_unchanged_with_the_views_on`): the seven-tool census is
  pinned by name, and ``tools._list_tools()`` is serialized before and after the views have
  actively rendered — an observed dispatch, a rendered view model, and a served GET — so a
  view that registered, renamed, widened or re-described a tool reds on bytes.
* **No route that can act appeared** (:func:`test_the_computer_use_route_surface_is_pinned`):
  the registered ``/api/computer-use/*`` surface is pinned to exactly one POST (the dispatch,
  `DCU-4`) and one GET (the view). A second acting verb under this prefix is a second chain.
* **The view modules structurally cannot reach the desktop**
  (:func:`test_the_view_modules_import_no_driver_and_reach_no_dispatch`): asserted by AST,
  the same way ``tools.py``'s thinness is — ``overlay``/``render`` import no driver module
  and call neither ``computer_dispatch`` nor ``_run_driver``. The complementary screen-caller
  census (``test_computer_use_call_sites.py``) already sweeps these files for policy calls,
  so a view that started *deciding* reds there without this file changing.

The behavioural half keeps the mirror honest: the trail point for an approved click is the
fresh element's frame centre and it exists BEFORE the driver acts (a wedged driver still
shows the human what was attempted); a refused attempt paints no fake cursor over a click
that will never land; and the view model renders on a disarmed machine, calls no driver, and
scrubs credential-shaped text with the same definition the dispatch's step 7 applies for the
model.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import json
import pathlib

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.computer_use import enable_state, overlay, render, service
from personalclaw.computer_use import tools as ct

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "personalclaw"

ARMED_APP = "TextEdit"
FINGERPRINT = "fp-live-view"

#: A pressable element WITH geometry, because the overlay's whole claim is "where a click
#: will land": frame (100, 200, 50, 20) → centre (125.0, 210.0).
BUTTON = {
    "index": 0,
    "role": "AXButton",
    "title": "Save",
    "enabled": True,
    "frame": {"x": 100.0, "y": 200.0, "width": 50.0, "height": 20.0},
    "actions": ["AXPress"],
}


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Keystone → tmp, home → tmp, and every view/snapshot/trail store cleared, every test."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setenv(enable_state.ENABLE_PATH_ENV, str(tmp_path / "enable.json"))
    enable_state.reset_enable_state()
    service.reset_snapshots()
    overlay.reset_trail()
    yield
    enable_state.reset_enable_state()
    service.reset_snapshots()
    overlay.reset_trail()


def _arm(tmp_path, *apps: str) -> None:
    (tmp_path / "enable.json").write_text(
        json.dumps({"version": 1, "enabled": True, "apps": list(apps)}), encoding="utf-8"
    )
    enable_state.reset_enable_state()


def _fake_driver(monkeypatch, *, elements=None, fingerprint=FINGERPRINT, act_fails=False):
    """Step 6 as an in-process double (the `test_computer_use_dispatch` idiom), optionally
    failing every ACTING op while the read (snapshot) still answers — the wedged-driver case."""
    calls: list[str] = []

    async def run(op, payload, *, tool):
        calls.append(op)
        if op == "snapshot":
            return {"fingerprint": fingerprint, "elements": list(elements or [BUTTON])}
        if op == "list_apps":
            return {"apps": [ARMED_APP]}
        if act_fails:
            # What the real _run_driver does with a child's error envelope: a typed refusal.
            service._refuse(service.ERR_DRIVER_FAILED, what="wedged", why="", fix="retry")
        return {"ok": True, "op": op}

    monkeypatch.setattr(service, "_run_driver", run)
    return calls


def _run(coro):
    return asyncio.run(coro)


def _click(snapshot_id: str):
    return service.computer_dispatch(
        "computer_click", {"snapshot_id": snapshot_id, "element_index": 0}
    )


def _view_client(app_name: str = "") -> TestClient:
    """The live-view route mounted the way `test_security_audit_api` mounts its surface."""
    from personalclaw.dashboard.handlers.computer_use import api_computer_use_live_view

    app = web.Application()
    if app_name:
        # Mirrors what the auth middleware stamps for an app-scoped token.
        @web.middleware
        async def stamp_app(request, handler):
            request["app"] = app_name
            return await handler(request)

        app.middlewares.append(stamp_app)
    app.router.add_get("/api/computer-use/live-view", api_computer_use_live_view)
    return TestClient(TestServer(app))


# ── 1. the capability census: the tool surface is UNCHANGED with the views on ─


def test_the_tool_surface_is_unchanged_with_the_views_on(tmp_path, monkeypatch):
    """THE done_when clause. "Views on" is exercised, not declared: before the after-shot,
    an approved dispatch feeds the overlay, ``render.live_view()`` builds the view model, and
    the dashboard route serves it — every DCU-7 surface has actually run. The before/after
    byte-compare catches mutation; the pinned name census catches a surface that was already
    wrong before this test imported anything."""
    before = json.dumps(ct._list_tools(), sort_keys=True)
    assert ct.TOOL_NAMES == frozenset(
        {
            "computer_list_apps",
            "computer_snapshot",
            "computer_click",
            "computer_type",
            "computer_set_value",
            "computer_scroll",
            "computer_perform_action",
        }
    ), "the seven-tool surface (§2) changed — DCU-7 must not touch it"

    # Views ON: overlay observing a real approved dispatch…
    _arm(tmp_path, ARMED_APP)
    _fake_driver(monkeypatch)
    snap = service._remember(ARMED_APP, FINGERPRINT, [BUTTON])
    _run(_click(snap.snapshot_id))
    assert overlay.motion_trail(), "the observed dispatch left no trail — 'views on' is vacuous"
    # …the live view rendering…
    view = render.live_view()
    assert view["snapshots"] and view["trail"], "the view rendered empty — 'views on' is vacuous"

    # …and the dashboard route serving it.
    async def served():
        client = _view_client()
        await client.start_server()
        try:
            resp = await client.get("/api/computer-use/live-view")
            assert resp.status == 200
            return await resp.json()
        finally:
            await client.close()

    body = _run(served())
    assert body["trail"], "the route served an empty view — 'views on' is vacuous"

    after = json.dumps(ct._list_tools(), sort_keys=True)
    assert before == after, (
        "the computer-use tool surface CHANGED while the views were on. DCU-7's views are "
        "observation-only; a view that adds, renames or re-describes a tool is a capability."
    )


def _registered_computer_use_routes() -> set[tuple[str, str]]:
    """(verb, path) for every /api/computer-use route server.py registers, by AST."""
    tree = ast.parse((SRC / "dashboard" / "server.py").read_text(encoding="utf-8"))
    found: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        verb = node.func.attr
        if not verb.startswith("add_"):
            continue
        args = [a.value for a in node.args if isinstance(a, ast.Constant)]
        paths = [a for a in args if isinstance(a, str) and a.startswith("/api/computer-use")]
        for path in paths:
            found.add((verb.removeprefix("add_"), path))
    return found


def test_the_computer_use_route_surface_is_pinned():
    """One POST that can act, one GET that can only look. Exact equality against a non-empty
    expected set is its own vacuity floor: a scanner gone blind returns ``set()`` and reds."""
    assert _registered_computer_use_routes() == {
        ("post", "/api/computer-use/dispatch"),
        ("get", "/api/computer-use/live-view"),
    }, (
        "the /api/computer-use route surface changed. The views must add no route that can "
        "act — a new verb here needs the whole DCU-4 chain and its own atom, not a view."
    )


#: Modules whose import inside a view would be desktop reach. `ctypes` is included for the
#: same reason `test_the_macos_driver_holds_no_ctypes` pins it: the FFI lives in ONE file.
_DRIVER_MODULES = (
    "macos_driver",
    "macos_ffi",
    "windows_driver",
    "linux_driver",
    "driver_host",
    "unsupported_platform",
    "ctypes",
)
_REACH_CALLS = frozenset({"computer_dispatch", "_run_driver"})


def _reach(source: str) -> list[str]:
    """Every way this source could touch the desktop: driver imports and dispatch calls."""
    tree = ast.parse(source)
    offences: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            offences += [a.name for a in node.names if any(m in a.name for m in _DRIVER_MODULES)]
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if any(m in module for m in _DRIVER_MODULES):
                offences.append(module)
            offences += [a.name for a in node.names if a.name in _DRIVER_MODULES]
        elif isinstance(node, ast.Call):
            name = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else getattr(node.func, "id", "")
            )
            if name in _REACH_CALLS:
                offences.append(f"{name}()")
    return offences


def test_the_view_modules_import_no_driver_and_reach_no_dispatch():
    for module in ("overlay.py", "render.py"):
        source = (SRC / "computer_use" / module).read_text(encoding="utf-8")
        assert _reach(source) == [], (
            f"{module} reaches the desktop: {_reach(source)}. The views are observation-only "
            "(§3 floor 7) — they mirror stored state and may never import a driver or call "
            "the dispatch."
        )


def test_the_reach_scanner_detects_an_offender():
    """The scanner's own efficacy proof, so an empty offence list is a finding, not blindness."""
    assert _reach("from personalclaw.computer_use import macos_ffi\n") != []
    assert _reach("import ctypes\n") != []
    assert _reach("async def peek():\n    return await computer_dispatch('computer_click')\n")
    assert _reach("x = 1\n") == []


# ── 2. the overlay observes; it never gates and never leads ───────────────────


def test_an_approved_click_lands_a_trail_point_at_the_element_centre(tmp_path, monkeypatch):
    _arm(tmp_path, ARMED_APP)
    _fake_driver(monkeypatch)
    snap = service._remember(ARMED_APP, FINGERPRINT, [BUTTON])
    _run(_click(snap.snapshot_id))

    (point,) = overlay.motion_trail()
    assert (point["x"], point["y"]) == (125.0, 210.0)
    assert point["tool"] == "computer_click"
    assert point["app"] == ARMED_APP
    assert point["method"] == "ax_press"
    assert point["label"] == "Save · AXButton"


def test_a_refused_attempt_paints_no_fake_cursor(tmp_path, monkeypatch):
    """A refusal never lands, so the overlay must not show a landing. The feed carries the
    refusal (the SEL row exists); the trail stays empty."""
    _arm(tmp_path, ARMED_APP)
    _fake_driver(monkeypatch)
    snap = service._remember("Terminal", FINGERPRINT, [BUTTON])  # NOT allowlisted
    import personalclaw.computer_use.policy as policy

    with pytest.raises(policy.ComputerUsePolicyRefusal):
        _run(_click(snap.snapshot_id))
    assert overlay.motion_trail() == []


def test_the_point_exists_even_when_the_acting_driver_call_fails(tmp_path, monkeypatch):
    """ "Where a click WILL land" is published before the driver runs — the same reasoning
    that writes the approved SEL row first: a wedged driver must still leave the human the
    evidence of what was attempted."""
    _arm(tmp_path, ARMED_APP)
    _fake_driver(monkeypatch, act_fails=True)
    snap = service._remember(ARMED_APP, FINGERPRINT, [BUTTON])
    with pytest.raises(service.ComputerUseRefusal):
        _run(_click(snap.snapshot_id))
    (point,) = overlay.motion_trail()
    assert (point["x"], point["y"]) == (125.0, 210.0)


def test_reads_leave_no_trail(tmp_path, monkeypatch):
    """list_apps and snapshot act on nothing, so there is nowhere for a cursor to land."""
    _arm(tmp_path, ARMED_APP)
    _fake_driver(monkeypatch)
    _run(service.computer_dispatch("computer_list_apps", {}))
    _run(service.computer_dispatch("computer_snapshot", {"app": ARMED_APP}))
    assert overlay.motion_trail() == []


def test_the_trail_is_bounded():
    for i in range(overlay.MAX_TRAIL + 25):
        overlay.observe_action(tool="computer_click", app=ARMED_APP, element=BUTTON, params={})
    trail = overlay.motion_trail()
    assert len(trail) == overlay.MAX_TRAIL
    seqs = [p["seq"] for p in trail]
    assert seqs == sorted(seqs), "the trail must stay oldest-first — it IS the motion"


def test_observation_fails_open_on_any_input():
    """The overlay runs inside the dispatch chain, so it inherits the gate's law: never
    decides, never raises — on ANY input, including shapes no driver should produce."""
    overlay.observe_action(  # type: ignore[arg-type]
        tool=None, app=object(), element="not-a-dict", params="nope"
    )
    overlay.observe_action(
        tool="computer_click",
        app=ARMED_APP,
        element={"frame": {"x": "NaNsense", "width": []}},
        params={"click_method": 7},
    )
    # Whatever was recorded, nothing raised — and the malformed frame produced no phantom
    # (0, 0) landing in the display corner.
    assert all(p["x"] is None for p in overlay.motion_trail() if p["label"] == "")


def test_a_named_pointer_method_records_its_own_spelling(tmp_path, monkeypatch):
    """The plan wants the real-cursor warp one filter away everywhere it appears — the SEL
    gets `computer_click:global` (`DCU-4`), and the overlay keeps the same distinction so a
    watching human can tell a pointerless press from their own mouse being taken."""
    _arm(tmp_path, ARMED_APP)
    _fake_driver(monkeypatch)
    _run(
        service.computer_dispatch(
            "computer_click",
            {"click_method": "global", "x": 10.0, "y": 20.0, "app": ARMED_APP},
        )
    )
    (point,) = overlay.motion_trail()
    assert point["method"] == "global"
    assert (point["x"], point["y"]) == (10.0, 20.0)


# ── 3. the live view renders, mirrors, and reaches nothing ───────────────────


def test_the_view_renders_on_a_disarmed_machine():
    """The most useful sentence a view can say on a disarmed machine is that it is disarmed —
    so the keystone is displayed here, never consulted as a gate."""
    view = render.live_view()
    assert view["enabled"] is False
    assert view["allowed_apps"] == []
    assert view["snapshots"] == [] and view["trail"] == [] and view["feed"] == []


def test_the_view_calls_no_driver(monkeypatch):
    """The mirror shows what the model already read. A live view that walked a window would
    BE a read capability — so the driver seam explodes under it and the view must not care."""

    def boom(*a, **k):  # pragma: no cover - failing is the test
        raise AssertionError("the live view reached the driver")

    monkeypatch.setattr(service, "_run_driver", boom)
    service._remember(ARMED_APP, FINGERPRINT, [BUTTON])
    view = render.live_view()
    assert view["snapshots"][0]["app"] == ARMED_APP


def test_the_mirror_carries_geometry_and_identity_but_no_field_contents():
    secret = "sk-ant-api03-PLANTEDoTTERsecretVALUE0123456789abcdefXYZ"
    field = {**BUTTON, "role": "AXTextField", "title": f"key {secret}", "value": secret}
    service._remember(ARMED_APP, FINGERPRINT, [field])
    (snap_row,) = render.live_view()["snapshots"]
    (element,) = snap_row["elements"]
    assert element["frame"] == BUTTON["frame"]
    assert "value" not in element, "the wireframe must not mirror field contents at all"
    assert secret not in json.dumps(element), "credential-shaped text must not reach a browser"
    assert element["role"] == "AXTextField"


def test_only_the_newest_snapshot_carries_its_elements():
    service._remember("Older", "fp-old", [BUTTON])
    newest = service._remember(ARMED_APP, FINGERPRINT, [BUTTON])
    rows = render.live_view()["snapshots"]
    assert [r["snapshot_id"] for r in rows][0] == newest.snapshot_id
    assert "elements" in rows[0] and "elements" not in rows[1]
    assert rows[1]["element_count"] == 1


def test_the_feed_shows_both_verdicts_from_the_real_sel(tmp_path, monkeypatch):
    """One approved and one denied attempt through the REAL chain and the REAL log — the feed
    is the SEL reflected, so both verdicts must surface, newest first, with the app parsed
    back out of the row's `resources`."""
    _arm(tmp_path, ARMED_APP)
    _fake_driver(monkeypatch)
    snap = service._remember(ARMED_APP, FINGERPRINT, [BUTTON])
    _run(_click(snap.snapshot_id))
    denied_snap = service._remember("Terminal", FINGERPRINT, [BUTTON])
    import personalclaw.computer_use.policy as policy

    with pytest.raises(policy.ComputerUsePolicyRefusal):
        _run(_click(denied_snap.snapshot_id))

    feed = render.live_view()["feed"]
    outcomes = [(row["operation"], row["outcome"], row["app"]) for row in feed]
    assert ("computer_click", "denied", "Terminal") in outcomes
    assert ("computer_click", "approved", ARMED_APP) in outcomes
    assert outcomes.index(("computer_click", "denied", "Terminal")) < outcomes.index(
        ("computer_click", "approved", ARMED_APP)
    ), "the feed must read newest first"


def test_the_feed_carries_only_computer_use_rows(tmp_path, monkeypatch):
    from personalclaw.sel import sel

    sel().log_api_access(
        caller="dashboard:abc", operation="GET /api/tools", outcome="completed", source="dashboard"
    )
    import personalclaw.computer_use.gate as gate

    gate.require_computer_use(tool="computer_click", app=ARMED_APP, outcome="approved")
    feed = render.live_view()["feed"]
    assert [row["operation"] for row in feed] == ["computer_click"]


# ── 4. the dashboard route ────────────────────────────────────────────────────


def test_the_route_serves_the_view_model():
    async def check():
        client = _view_client()
        await client.start_server()
        try:
            resp = await client.get("/api/computer-use/live-view")
            assert resp.status == 200
            body = await resp.json()
            assert set(body) == {
                "enabled",
                "allowed_apps",
                "ttl_secs",
                "snapshots",
                "trail",
                "feed",
            }
        finally:
            await client.close()

    _run(check())


def test_an_app_scoped_token_is_refused_categorically():
    """Same shape and same reasoning as the audit surface: 403 with its own stable code,
    never a 200 carrying an empty view (which would read as "the agent is doing nothing")."""

    async def check():
        client = _view_client(app_name="some-installed-app")
        await client.start_server()
        try:
            resp = await client.get("/api/computer-use/live-view")
            assert resp.status == 403
            body = await resp.json()
            assert body["error"]["code"] == "computer_use_view_owner_only"
        finally:
            await client.close()

    _run(check())


# ── 5. the observation seam stays where the chain put it ─────────────────────


def test_the_observation_runs_after_the_audit_and_before_the_driver():
    """Source-order companion (the `test_the_dispatch_calls_the_screens_in_source_order`
    idea): the observe call sits after the LAST `_audit` (the approved row — a refused
    attempt must never be observed as a landing) and before the LAST `_run_driver` (the
    acting call — "will land" must stay the true tense)."""
    tree = ast.parse(inspect.getsource(service.computer_dispatch))
    calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else getattr(node.func, "id", "")
            )
            if name in {"_audit", "observe_action", "_run_driver"}:
                calls.append((node.lineno, name))
    order = [name for _line, name in sorted(calls)]
    assert "observe_action" in order, "the dispatch no longer feeds the overlay"
    observe = order.index("observe_action")
    assert (
        max(i for i, n in enumerate(order) if n == "_audit")
        < observe
        < max(i for i, n in enumerate(order) if n == "_run_driver")
    ), order
