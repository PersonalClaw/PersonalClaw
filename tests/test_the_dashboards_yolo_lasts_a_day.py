"""YOLO turned on from the dashboard lasts a day, and nothing else about it changes.

Measured before this change: the dashboard's YOLO turned itself off six hours after it was turned
on (``trust_mode.YOLO_DASHBOARD_TTL_SECS``), part-way through a night it was left on for, and every
call asked again from then on, a directory listing included. It now lasts 24 hours.

What stays: it still ends at a restart, since it is held in memory and never written to the
config (only ``agent.yolo`` there is permanent); its end still hands each chat the posture it
answers without YOLO; and a channel's ``!yolo on`` still lasts 30 minutes.

The 🔴 test is red before this change; the others are its controls.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _api_app, _make_state

from personalclaw import trust_mode
from personalclaw.dashboard.chat import api_chat_mode
from personalclaw.dashboard.handlers import api_status

DAY = 24 * 3600


class _Clock:
    """The clock YOLO's time limit reads (``trust_mode``'s own ``time.monotonic``), and only it:
    the event loop's clock is left alone."""

    def __init__(self) -> None:
        self.now = 1000.0

    def at(self, secs: float) -> None:
        self.now = 1000.0 + secs

    def monotonic(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch) -> _Clock:
    c = _Clock()
    monkeypatch.setattr(trust_mode, "time", SimpleNamespace(monotonic=c.monotonic))
    return c


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    trust_mode.disable_yolo()
    yield _make_state(tmp_path)
    trust_mode.disable_yolo()


@pytest.mark.asyncio
async def test_the_dashboards_yolo_lasts_a_day_and_the_status_says_so(state, clock):
    """🔴 It ended at six hours, and the status said so."""
    app = _api_app(state)
    app.router.add_post("/api/chat/mode", api_chat_mode)
    app.router.add_get("/api/status", api_status)

    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/api/chat/mode", json={"mode": "yolo"})
        assert resp.status == 200, await resp.text()
        status = await (await client.get("/api/status")).json()

    assert status["yolo"] is True
    assert status["yolo_expires_in"] == DAY
    clock.at(DAY - 1)
    assert state.is_yolo_active()
    clock.at(DAY + 1)
    assert not state.is_yolo_active()
    assert state.yolo_remaining_secs() is None


def test_its_end_hands_each_chat_the_posture_it_answers_without_yolo(state, clock):
    plain = state.get_or_create_session("chat-plain")
    trusted = state.get_or_create_session("chat-trusted")
    trusted._trust = True
    pushed: list[str] = []
    push = lambda session: pushed.append(session.key)  # noqa: E731
    state.push_chat_policy = push  # type: ignore[method-assign]
    state.enable_yolo()
    assert [state.approval_of(s) for s in (plain, trusted)] == ["yolo", "yolo"]

    clock.at(DAY + 1)
    assert not state.is_yolo_active()

    assert {"chat-plain", "chat-trusted"} <= set(pushed)
    assert [state.approval_of(s) for s in (plain, trusted)] == ["normal", "trust"]


def test_a_restart_ends_it(state, tmp_path):
    """The dashboard's YOLO is in memory: it writes nothing to the config, so a gateway started
    again reads no YOLO there and starts with it off."""
    from personalclaw.config.loader import AppConfig
    from personalclaw.dashboard.server import _apply_startup_yolo

    state.enable_yolo()
    config = tmp_path / "config.json"
    assert not config.exists() or "yolo" not in json.loads(config.read_text()).get("agent", {})

    trust_mode.disable_yolo()  # the process ends, and its memory with it
    again = _make_state(tmp_path)
    _apply_startup_yolo(again, AppConfig())
    assert not again.is_yolo_active()


def test_a_channels_yolo_on_still_lasts_30_minutes(clock):
    assert trust_mode.YOLO_CHANNEL_TTL_SECS == 30 * 60
    trust_mode.enable_yolo(ttl_secs=trust_mode.YOLO_CHANNEL_TTL_SECS)
    try:
        clock.at(30 * 60 - 1)
        assert trust_mode.is_yolo_active()
        clock.at(30 * 60 + 1)
        assert not trust_mode.is_yolo_active()
    finally:
        trust_mode.disable_yolo()
