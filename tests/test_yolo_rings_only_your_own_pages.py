"""YOLO turning on or off rings your own pages, and no app's.

The shell's corner says while YOLO is on (`YoloChip`), on every page. It read the status once, on
mount, and no frame said YOLO moved, so a chat turning YOLO on left every other page saying nothing
until a reload, and a timed YOLO that ran out left the mark up. The gateway now rings a `refresh`
of kind `yolo` whenever YOLO turns on or off, however it went (a toggle, its time running out, the
config). An app's socket that declares `refresh` hears the frame's other kinds and never this one,
as the session list stopped carrying your YOLO state.

Each case below is red on the code before the change.
"""

from __future__ import annotations

import json

import pytest
from chat_test_helpers import _make_state
from test_ws_broadcast_gate import _FakeWS


@pytest.fixture
def rings(tmp_path, monkeypatch):
    """A state, and the refresh kinds it rings, in order."""
    state = _make_state(tmp_path)
    kinds: list[str] = []
    real = state.push_refresh

    def record(*k: str) -> None:
        kinds.extend(k)
        real(*k)

    monkeypatch.setattr(state, "push_refresh", record)
    return state, kinds


def test_yolo_rings_when_it_turns_on_and_when_it_turns_off(rings):
    state, kinds = rings
    state.enable_yolo()
    assert kinds.count("yolo") == 1, kinds
    state.disable_yolo()
    assert kinds.count("yolo") == 2, kinds


def test_yolo_rings_when_its_time_runs_out(rings, monkeypatch):
    import time

    state, kinds = rings
    base = 1000.0
    monkeypatch.setattr(time, "monotonic", lambda: base)
    state.enable_yolo()
    monkeypatch.setattr(time, "monotonic", lambda: base + state._YOLO_TTL + 1)
    assert state.is_yolo_active() is False
    assert kinds.count("yolo") == 2, "a timed YOLO ran out and rang nothing"


@pytest.fixture
def sockets(monkeypatch, tmp_path):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    from personalclaw.dashboard.state import DashboardState

    st = DashboardState.__new__(DashboardState)
    st._ws_clients = []
    st._ws_app = {}
    st._ws_scope = {}
    st._ws_release = {}
    st._ws_here = {}
    st._notification_log = []
    st._sessions = {}
    st.conversation_log = None
    monkeypatch.setattr(
        DashboardState,
        "_schedule_ws_send",
        lambda self, coro: (coro.close(), True)[1],
        raising=False,
    )
    monkeypatch.setattr(DashboardState, "_remove_ws", lambda self, ws: None, raising=False)
    owner = _FakeWS()
    st._ws_clients.append(owner)
    # Two apps, each read by its own manifest: one declares `refresh`, and the differential's
    # control, which declared none, hears none of it.
    declared = {"an-app": ["refresh"], "a-quiet-app": []}
    socks = {}
    for name in declared:
        socks[name] = _FakeWS()
        st._ws_clients.append(socks[name])
        st._ws_app[socks[name]] = name

    class _Checker:
        def __init__(self, events: list[str]) -> None:
            self.events = events

        def can_use_event(self, event_type: str) -> bool:
            return event_type in self.events

    monkeypatch.setattr(
        "personalclaw.apps.permissions.checker_for",
        lambda name: _Checker(declared.get(name, [])),
        raising=False,
    )
    return st, owner, socks["an-app"], socks["a-quiet-app"]


def _kinds(ws: _FakeWS) -> list[list[str]]:
    return [json.loads(m)["data"]["kinds"] for m in ws.sent if json.loads(m)["type"] == "refresh"]


def test_an_app_that_declares_refresh_never_hears_yolo(sockets):
    state, owner, app, quiet = sockets
    state.push_refresh("yolo")
    state.push_refresh("loops", "yolo")
    state.push_refresh("loops")

    assert _kinds(owner) == [["yolo"], ["loops", "yolo"], ["loops"]]
    assert _kinds(app) == [["loops"], ["loops"]], "an app heard that your YOLO moved"
    assert _kinds(quiet) == [], "an app that declared no refresh heard one"
