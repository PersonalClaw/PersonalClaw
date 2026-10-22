"""Whether the microphone is live reaches the gateway, the owner's pages, and no app (#3833).

The desktop shell says when any capture source holds the microphone (the composer's dictation,
hands-free, the Knowledge recorder) to every window and to the gateway. The gateway keeps it, serves
it, and sends it as a ``listening`` frame when it changed. Both routes are the owner's, and the
frame reaches no app's socket whatever it declares: what her microphone is doing is not an app's.
"""

from __future__ import annotations

from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.apps.permissions import app_request_denial, owner_only_api_reason
from personalclaw.dashboard.handlers.listening import register_listening_routes
from personalclaw.dashboard.ws_state import OWNER_FRAMES, DashboardWebSocketState, frame_subject


class _State:
    def __init__(self) -> None:
        self.frames: list[tuple[str, Any]] = []

    def broadcast_ws(self, kind: str, data: Any = None) -> None:
        self.frames.append((kind, data))


def _app(state: _State) -> web.Application:
    app = web.Application()
    app["state"] = state
    register_listening_routes(app)
    return app


@pytest.mark.asyncio
async def test_the_shell_s_report_is_kept_served_and_sent_once_per_change():
    state = _State()
    async with TestClient(TestServer(_app(state))) as http:
        assert await (await http.get("/api/listening")).json() == {
            "on": False,
            "sources": [],
            "since": None,
        }
        r = await http.post(
            "/api/listening", json={"on": True, "sources": ["recorder", "dictation"]}
        )
        assert r.status == 200
        said = await (await http.get("/api/listening")).json()
        assert said["on"] is True and said["sources"] == ["dictation", "recorder"]
        assert said["since"], "when it started"
        # The same report again says nothing new.
        await http.post("/api/listening", json={"on": True, "sources": ["dictation", "recorder"]})
        await http.post("/api/listening", json={"on": False, "sources": []})
    assert [(k, d["on"], d["sources"]) for k, d in state.frames] == [
        ("listening", True, ["dictation", "recorder"]),
        ("listening", False, []),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"on": "yes", "sources": []},
        {"on": True, "sources": ["the camera"]},
        {"on": True, "sources": "dictation"},
        {"sources": ["dictation"]},
        ["on"],
    ],
)
async def test_a_report_that_is_not_one_is_refused_and_changes_nothing(body):
    state = _State()
    async with TestClient(TestServer(_app(state))) as http:
        r = await http.post("/api/listening", json=body)
        assert r.status == 400
        assert (await (await http.get("/api/listening")).json())["on"] is False
    assert state.frames == []


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_both_routes_are_the_owner_s(method):
    """No app declaration reaches either: the path is owner-only, every method of it."""
    assert "microphone" in owner_only_api_reason("/api/listening", method=method)
    why = app_request_denial("probe-app", "/api/listening", method=method, route="/api/listening")
    assert why, f"an app may {method} /api/listening"


def test_the_frame_reaches_no_app_whatever_it_declares():
    subject = frame_subject("listening", {"on": True, "sources": ["dictation"], "since": "now"})
    assert subject == ("owner", "listening")
    assert "listening" in OWNER_FRAMES
    ws = DashboardWebSocketState.__new__(DashboardWebSocketState)
    ws.session_creating_app = lambda key: "probe-app"  # it would own any conversation
    assert ws._app_may_hear_about("probe-app", "listening", subject) is False
    # The control: a frame about a conversation the app started does reach it.
    assert ws._app_may_hear_about("probe-app", "chat_chunk", ("conversation", "c1")) is True
