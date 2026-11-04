"""The desktop is live while the agent uses it, and the dashboard is told when it starts.

Home's Overview showed its Desktop live view band on every install, all the time: an Armed/Off
pill, two toggles and an empty feed, whether or not anything was on the desktop. The band now
shows only while the agent is driving the desktop, and the gateway is what says so:
`render.is_live()` is true while an approved action landed or a window was walked within
`render.LIVE_SECS` (30 s), the live-view read carries it as `live`, and each action or walk rings a
`computer_use` refresh hint (at most every two seconds), so an open dashboard brings the band in
without polling a desktop nobody is driving.
"""

from __future__ import annotations

import asyncio
import json

import computer_use_live_view_shared
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.computer_use import enable_state, overlay, render, service

_isolated = computer_use_live_view_shared._isolated

APP = "TextEdit"
BUTTON = {
    "index": 0,
    "role": "AXButton",
    "title": "Save",
    "enabled": True,
    "frame": {"x": 100.0, "y": 200.0, "width": 50.0, "height": 20.0},
    "actions": ["AXPress"],
}


def _arm(tmp_path) -> None:
    (tmp_path / "enable.json").write_text(
        json.dumps({"version": 1, "enabled": True, "apps": [APP]}), encoding="utf-8"
    )
    enable_state.reset_enable_state()


def _driver(monkeypatch) -> None:
    async def run(op, payload, *, tool):
        if op == "snapshot":
            return {"fingerprint": "fp", "elements": [BUTTON]}
        return {"ok": True, "op": op}

    monkeypatch.setattr(service, "_run_driver", run)


class _Hints:
    """The dashboard state the doorbell reaches: the refresh hints it was sent."""

    def __init__(self) -> None:
        self.kinds: list[str] = []

    def push_refresh(self, *kinds: str) -> None:
        self.kinds.extend(kinds)


def _hints(monkeypatch) -> _Hints:
    hints = _Hints()
    monkeypatch.setattr(
        "personalclaw.inbox_providers.native_source.get_dashboard_state", lambda: hints
    )
    monkeypatch.setattr(overlay, "_announced_at", 0.0)
    return hints


def _click(tmp_path, monkeypatch) -> None:
    _arm(tmp_path)
    _driver(monkeypatch)
    snap = service._remember(APP, "fp", [BUTTON])
    asyncio.run(
        service.computer_dispatch(
            "computer_click", {"snapshot_id": snap.snapshot_id, "element_index": 0}
        )
    )


def test_a_desktop_nobody_drives_is_not_live():
    assert render.is_live() is False
    assert render.live_view()["live"] is False


def test_an_approved_action_makes_it_live_and_rings_the_dashboard(tmp_path, monkeypatch):
    hints = _hints(monkeypatch)

    _click(tmp_path, monkeypatch)

    assert render.is_live() is True
    assert "computer_use" in hints.kinds


def test_it_stops_being_live_once_nothing_happened_for_the_window(tmp_path, monkeypatch):
    _click(tmp_path, monkeypatch)
    later = service._now() + render.LIVE_SECS + 1
    monkeypatch.setattr(service, "_now", lambda: later)
    stamp = overlay.motion_trail(1)[-1]["ts"]
    monkeypatch.setattr(render, "_wall", lambda: stamp + render.LIVE_SECS + 1)

    assert render.is_live() is False


def test_a_window_walked_is_the_desktop_in_use(monkeypatch):
    hints = _hints(monkeypatch)

    service._remember(APP, "fp", [BUTTON])

    assert render.is_live() is True
    assert hints.kinds == ["computer_use"]


def test_a_burst_of_actions_rings_once_in_its_window(monkeypatch):
    hints = _hints(monkeypatch)

    for _ in range(5):
        overlay.observe_action(tool="computer_click", app=APP, element=BUTTON)

    assert hints.kinds == ["computer_use"]


def test_the_route_serves_live(tmp_path, monkeypatch):
    from personalclaw.dashboard.handlers.computer_use import api_computer_use_live_view

    _click(tmp_path, monkeypatch)
    app = web.Application()
    app.router.add_get("/api/computer-use/live-view", api_computer_use_live_view)

    async def served() -> dict:
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            resp = await client.get("/api/computer-use/live-view")
            return await resp.json()
        finally:
            await client.close()

    assert asyncio.run(served())["live"] is True
