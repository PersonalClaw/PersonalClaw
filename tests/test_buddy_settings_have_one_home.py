"""Every buddy setting has one home, and every window reads the same answer (#3901).

The owner's knobs are the gateway config's ``buddy`` section: ``show``, the Buddy status opt-in,
off on a fresh home; which surfaces show the buddy (``window``, ``tray``, ``dock`` and
``notifications``); and ``away_after_secs``, how long the owner is gone before the buddy hides or
dozes. Nothing for play, drop, quick ask, sound, sprite export or the bridge exists yet: those
come back with their features.

``GET /api/buddy/policy`` answers every buddy question from one place, the knobs and the
notification posture the buddy's alerts follow (mute, quiet hours), so every window and every
machine follows one answer, and it is the knobs' reader in production. It is the owner's alone.
When a knob or the notification settings change, the owner's sockets hear one
``buddy_policy_changed`` frame, carrying nothing, and every window reads the policy again.
"""

from __future__ import annotations

import json
from dataclasses import fields
from datetime import datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.apps.permissions import app_request_denial, owner_only_api_reason
from personalclaw.dashboard.ws_state import OWNER_FRAMES, DashboardWebSocketState, frame_subject

pytestmark = pytest.mark.security

#: The v0.3.0 knobs, and nothing else (#3901's approved update).
KNOBS = {"show", "window", "tray", "dock", "notifications", "away_after_secs"}
#: What comes back only with its own feature.
LATER = ("play", "drop", "quick_ask", "sound", "volume", "sprite", "bridge")


class _State:
    def __init__(self) -> None:
        self.frames: list[tuple[str, Any]] = []

    def broadcast_ws(self, kind: str, data: Any = None) -> None:
        self.frames.append((kind, data))

    def push_refresh(self, *kinds: str) -> None:
        self.frames.append(("refresh", ",".join(kinds)))


@pytest.fixture
def home(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    with patch("personalclaw.config.loader.config_path", return_value=path):
        yield tmp_path


@pytest.fixture(autouse=True)
def _sel():
    with patch("personalclaw.dashboard.handlers.sel", return_value=MagicMock()):
        yield


def _app(state: _State) -> web.Application:
    from personalclaw.dashboard.handlers import api_personalclaw_config_patch
    from personalclaw.dashboard.handlers.buddy import register_buddy_routes
    from personalclaw.providers.entity_routes import register_entity_routes

    @web.middleware
    async def owner(request: web.Request, handler: Any) -> web.StreamResponse:
        request["user"] = "owner"
        return await handler(request)

    app = web.Application(middlewares=[owner])
    app["state"] = state
    app.router.add_patch("/api/config/personalclaw", api_personalclaw_config_patch)
    register_entity_routes(app)
    register_buddy_routes(app)
    return app


async def _policy(http: TestClient) -> dict[str, Any]:
    resp = await http.get("/api/buddy/policy")
    assert resp.status == 200, await resp.text()
    return await resp.json()


async def _set(http: TestClient, path: str, value: Any) -> None:
    resp = await http.patch("/api/config/personalclaw", json={"path": path, "value": value})
    assert resp.status == 200, await resp.text()


class TestTheKnobs:
    def test_a_fresh_home_has_the_buddy_off(self, home):
        from personalclaw.config.loader import AppConfig

        buddy = AppConfig.load().buddy
        assert buddy.show is False
        assert (buddy.window, buddy.tray, buddy.dock, buddy.notifications) == (True,) * 4
        assert buddy.away_after_secs == 180

    def test_there_are_the_v030_knobs_and_no_later_ones(self):
        from personalclaw.config.editable import _EDITABLE_CONFIG
        from personalclaw.config.loader import BuddyConfig

        assert {f.name for f in fields(BuddyConfig)} == KNOBS
        editable = {k.split(".", 1)[1] for k in _EDITABLE_CONFIG if k.startswith("buddy.")}
        assert editable == KNOBS
        for later in LATER:
            assert not any(later in k for k in editable), later

    def test_each_knob_survives_a_save_and_a_load(self, home):
        from personalclaw.config.loader import AppConfig

        cfg = AppConfig.load()
        cfg.buddy.show = True
        cfg.buddy.dock = False
        cfg.buddy.away_after_secs = 600
        cfg.save()
        again = AppConfig.load().buddy
        assert (again.show, again.dock, again.away_after_secs) == (True, False, 600)
        assert json.loads((home / "config.json").read_text())["buddy"]["show"] is True


@pytest.mark.asyncio
async def test_the_policy_answers_from_the_knobs_and_turns_every_surface_off_with_status(home):
    state = _State()
    async with TestClient(TestServer(_app(state))) as http:
        first = await _policy(http)
        assert first["show"] is False
        assert first["surfaces"] == dict.fromkeys(
            ("window", "tray", "dock", "notifications"), False
        )
        assert first["away_after_secs"] == 180
        await _set(http, "buddy.show", True)
        on = await _policy(http)
        assert on["surfaces"] == dict.fromkeys(("window", "tray", "dock", "notifications"), True)
        for surface in ("window", "tray", "dock", "notifications"):
            await _set(http, f"buddy.{surface}", False)
            assert (await _policy(http))["surfaces"][surface] is False, surface
        await _set(http, "buddy.away_after_secs", 600)
        assert (await _policy(http))["away_after_secs"] == 600


@pytest.mark.asyncio
async def test_the_policy_says_the_notification_posture_the_alerts_follow(home):
    state = _State()
    async with TestClient(TestServer(_app(state))) as http:
        calm = (await _policy(http))["notifications"]
        assert calm == {
            "muted": False,
            "quiet_now": False,
            "may_interrupt": True,
            "changes_at": None,
        }
        await http.put("/api/notifications/settings", json={"mute_all": True})
        muted = (await _policy(http))["notifications"]
        assert (muted["muted"], muted["may_interrupt"]) == (True, False)
        await http.put("/api/notifications/settings", json={"mute_all": False})
        # Quiet hours from a minute ago to an hour from now: quiet now, and it ends then.
        now = datetime.now()
        start = (now.hour * 60 + now.minute - 1) % 1440
        end = (now.hour * 60 + now.minute + 60) % 1440
        hhmm = lambda m: f"{m // 60:02d}:{m % 60:02d}"  # noqa: E731
        await http.put(
            "/api/notifications/settings",
            json={
                "quiet_hours_enabled": True,
                "quiet_hours_start": hhmm(start),
                "quiet_hours_end": hhmm(end),
            },
        )
        quiet = (await _policy(http))["notifications"]
    assert (quiet["quiet_now"], quiet["may_interrupt"]) == (True, False)
    edge = datetime.fromisoformat(quiet["changes_at"])
    assert (edge.hour * 60 + edge.minute) == end, "it says when quiet hours end"


@pytest.mark.asyncio
async def test_a_change_rings_the_owners_sockets_once_and_nothing_else_does(home):
    state = _State()
    async with TestClient(TestServer(_app(state))) as http:
        await _set(http, "buddy.show", True)
        await http.put("/api/notifications/settings", json={"mute_all": True})
        await _set(http, "companion.instance_name", "studio")
    rings = [f for f in state.frames if f[0] == "buddy_policy_changed"]
    assert rings == [("buddy_policy_changed", {}), ("buddy_policy_changed", {})]
    assert "buddy_policy_changed" in OWNER_FRAMES
    assert frame_subject("buddy_policy_changed", {}) == ("owner", "buddy_policy_changed")
    hearing = DashboardWebSocketState.__new__(DashboardWebSocketState)
    assert not hearing._app_may_hear_about(
        "probe", "buddy_policy_changed", ("owner", "buddy_policy_changed")
    )


def test_the_policy_is_the_owners_alone():
    assert owner_only_api_reason("/api/buddy/policy")
    assert app_request_denial("probe", "/api/buddy/policy", method="GET")
