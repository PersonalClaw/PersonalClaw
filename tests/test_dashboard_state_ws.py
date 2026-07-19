"""Tests for DashboardState WebSocket subscriber methods (activity viewer)."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from personalclaw.dashboard.state import DashboardState


@pytest.fixture(autouse=True)
def sync_event_loop():
    """Provide an event loop for sync tests calling asyncio.ensure_future.

    Production broadcast methods use ensure_future (fire-and-forget) which
    requires a running event loop.  Under xdist each worker is a separate
    process with no default loop, so we create one here.  autouse=True
    ensures every test gets a loop without opt-in, preventing flakes when
    new broadcast tests are added.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield loop
    loop.close()
    asyncio.set_event_loop(None)


@pytest.fixture
def state(monkeypatch, tmp_path):
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    return DashboardState(
        sessions=MagicMock(count=0),
        crons=MagicMock(),
        lessons=MagicMock(),
        start_time=0.0,
    )


class TestSubagentSubscribers:
    def test_subscribe_and_unsubscribe(self, state: DashboardState) -> None:
        ws = MagicMock()
        state.subscribe_subagents(ws)
        assert ws in state._ws_subagent_subscribers
        state.unsubscribe_subagents(ws)
        assert ws not in state._ws_subagent_subscribers

    def test_unsubscribe_idempotent(self, state: DashboardState) -> None:
        ws = MagicMock()
        state.unsubscribe_subagents(ws)  # should not raise

    def test_broadcast_sends_to_subscribed_only(self, state: DashboardState) -> None:
        ws_sub = MagicMock(closed=False)
        ws_sub.send_str = AsyncMock()
        ws_nosub = MagicMock(closed=False)
        ws_nosub.send_str = AsyncMock()
        state.subscribe_subagents(ws_sub)
        state.register_ws(ws_nosub)
        state.broadcast_ws_subagent_subscribers("subagent_chunk", {"id": "a1", "text": "hi"})
        ws_sub.send_str.assert_called_once()
        payload = json.loads(ws_sub.send_str.call_args[0][0])
        assert payload["type"] == "subagent_chunk"
        assert payload["data"]["id"] == "a1"
        ws_nosub.send_str.assert_not_called()

    def test_broadcast_noop_when_empty(self, state: DashboardState) -> None:
        state.broadcast_ws_subagent_subscribers("subagent_chunk", {"id": "a1"})

    def test_broadcast_ws_sends_to_all(self, state: DashboardState) -> None:
        ws1 = MagicMock(closed=False)
        ws1.send_str = AsyncMock()
        ws2 = MagicMock(closed=False)
        ws2.send_str = AsyncMock()
        state.register_ws(ws1)
        state.register_ws(ws2)
        state.broadcast_ws("subagent_spawn", {"id": "a1", "session": "chat-1"})
        ws1.send_str.assert_called_once()
        ws2.send_str.assert_called_once()

    def test_broken_subscriber_removed(self, state: DashboardState) -> None:
        ws = MagicMock(closed=False)
        ws.send_str = MagicMock(side_effect=ConnectionResetError)
        state.subscribe_subagents(ws)
        state.broadcast_ws_subagent_subscribers("subagent_chunk", {"id": "a1"})
        assert ws not in state._ws_subagent_subscribers

    def test_closed_ws_removed_on_broadcast(self, state: DashboardState) -> None:
        ws_alive = MagicMock(closed=False)
        ws_alive.send_str = AsyncMock()
        ws_dead = MagicMock(closed=True)
        ws_dead.send_str = AsyncMock()
        state.register_ws(ws_alive)
        state.register_ws(ws_dead)
        state.broadcast_ws("test", {"x": 1})
        ws_alive.send_str.assert_called_once()
        ws_dead.send_str.assert_not_called()
        assert ws_dead not in state._ws_clients
        assert ws_alive in state._ws_clients


class TestAppScopedWs:
    """Untrusted-app sandbox P1: a WS registered with an app identity receives
    ONLY the events the app's manifest declares (permissions.events); an owner
    connection (no app) still receives everything."""

    def _app_state_with(self, state, monkeypatch, allowed_events):
        from personalclaw.apps.manifest import Permissions
        from personalclaw.apps.permissions import PermissionChecker
        monkeypatch.setattr(
            "personalclaw.apps.permissions.checker_for",
            lambda name: PermissionChecker(app_name=name, permissions=Permissions(events=allowed_events)),
        )
        return state

    def test_app_ws_only_gets_declared_events(self, state, monkeypatch) -> None:
        self._app_state_with(state, monkeypatch, ["chat_message"])
        owner = MagicMock(closed=False); owner.send_str = AsyncMock()
        app = MagicMock(closed=False); app.send_str = AsyncMock()
        state.register_ws(owner)                 # owner: full stream
        state.register_ws(app, app="notes")      # app: filtered

        # An event the app DID declare → both get it.
        state.broadcast_ws("chat_message", {"x": 1})
        owner.send_str.assert_called_once()
        app.send_str.assert_called_once()

        # An event the app did NOT declare → only the owner gets it.
        owner.send_str.reset_mock(); app.send_str.reset_mock()
        state.broadcast_ws("approval", {"y": 2})
        owner.send_str.assert_called_once()
        app.send_str.assert_not_called()

    def test_app_identity_cleared_on_unregister(self, state, monkeypatch) -> None:
        self._app_state_with(state, monkeypatch, ["chat_message"])
        app = MagicMock(closed=False); app.send_str = AsyncMock()
        state.register_ws(app, app="notes")
        assert state._ws_app.get(app) == "notes"
        state.unregister_ws(app)
        assert app not in state._ws_app

    def test_no_app_connections_uses_fast_path(self, state) -> None:
        # With zero app-scoped connections, broadcast delivers to everyone (owner).
        ws = MagicMock(closed=False); ws.send_str = AsyncMock()
        state.register_ws(ws)
        state.broadcast_ws("anything_at_all", {"z": 3})
        ws.send_str.assert_called_once()


class TestSessionModel:
    def test_model_in_to_dict(self, state: DashboardState) -> None:
        session = state.get_or_create_session("test-1", model="claude-opus-4.5")
        assert session.to_dict()["model"] == "claude-opus-4.5"

    def test_model_defaults_empty(self, state: DashboardState) -> None:
        session = state.get_or_create_session("test-2")
        assert session.model == ""


class TestChatSessionStopState:
    """Tests for _ChatSession._stop_state and _stopping property."""

    def test_stop_state_default_idle(self) -> None:
        from personalclaw.dashboard.state import _ChatSession

        session = _ChatSession("s1")
        assert session._stop_state == "idle"
        assert session._stopping is False

    def test_stopping_property_reflects_stop_state(self) -> None:
        from personalclaw.dashboard.state import _ChatSession

        session = _ChatSession("s1")
        session._stop_state = "soft_pending"
        assert session._stopping is True
        session._stop_state = "killing"
        assert session._stopping is True
        session._stop_state = "idle"
        assert session._stopping is False

    def test_stopping_setter_maps_to_stop_state(self) -> None:
        from personalclaw.dashboard.state import _ChatSession

        session = _ChatSession("s1")
        session._stopping = True
        assert session._stop_state == "soft_pending"
        session._stopping = False
        assert session._stop_state == "idle"

    def test_to_dict_includes_stop_state(self) -> None:
        from personalclaw.dashboard.state import _ChatSession

        session = _ChatSession("s1")
        d = session.to_dict()
        assert d["stop_state"] == "idle"
        session._stop_state = "soft_pending"
        d = session.to_dict()
        assert d["stop_state"] == "soft_pending"
        assert d["stopping"] is True


class TestCompactCallbackWiring:
    """Tests for DashboardState.wire_session_compact_callback.

    Covers the async closure that fires after SessionManager recycles a
    dashboard session: posts a visible notice and broadcasts context_usage
    reset.  Non-dashboard session keys and missing sessions short-circuit.
    """

    def _captured_callback(self, state: DashboardState):
        """Install the callback and return the closure passed to sessions."""
        state.wire_session_compact_callback()
        state.sessions.set_compact_callback.assert_called_once()
        return state.sessions.set_compact_callback.call_args[0][0]

    def test_wire_installs_callback_on_sessions(self, state: DashboardState) -> None:
        state.wire_session_compact_callback()
        state.sessions.set_compact_callback.assert_called_once()
        cb = state.sessions.set_compact_callback.call_args[0][0]
        assert callable(cb)

    @pytest.mark.asyncio
    async def test_callback_ignores_non_dashboard_keys(self, state: DashboardState) -> None:
        session = state.get_or_create_session("chat-1")
        baseline = len(session.messages)
        cb = self._captured_callback(state)

        await cb("heartbeat", 90.0)
        await cb("cron:daily-digest", 95.0)

        assert len(session.messages) == baseline

    @pytest.mark.asyncio
    async def test_callback_noop_when_session_missing(self, state: DashboardState) -> None:
        cb = self._captured_callback(state)

        # No session named chat-ghost exists.  Must not raise.
        await cb("dashboard:chat-ghost", 90.0)

    @pytest.mark.asyncio
    async def test_callback_appends_assistant_notice(self, state: DashboardState) -> None:
        session = state.get_or_create_session("chat-1")
        before = len(session.messages)
        cb = self._captured_callback(state)

        await cb("dashboard:chat-1", 92.0)

        assert len(session.messages) == before + 1
        added = session.messages[-1]
        assert added["role"] == "assistant"
        assert added["cls"] == "msg msg-a"
        assert "92" in added["content"]
        assert "Auto-compacted" in added["content"]

    @pytest.mark.asyncio
    async def test_callback_rounds_pct_in_notice(self, state: DashboardState) -> None:
        """`{pct:.0f}` format keeps the notice terse — 91.7 renders as 92."""
        state.get_or_create_session("chat-1")
        cb = self._captured_callback(state)

        await cb("dashboard:chat-1", 91.7)

        added = state.get_session("chat-1").messages[-1]
        assert "92%" in added["content"]

    @pytest.mark.asyncio
    async def test_callback_broadcasts_context_usage_reset(
        self, state: DashboardState
    ) -> None:
        ws = MagicMock(closed=False)
        ws.send_str = AsyncMock()
        state.register_ws(ws)
        state.get_or_create_session("chat-1")
        cb = self._captured_callback(state)

        await cb("dashboard:chat-1", 92.0)

        payloads = [json.loads(c.args[0]) for c in ws.send_str.call_args_list]
        context = [p for p in payloads if p.get("type") == "context_usage"]
        assert len(context) == 1
        assert context[0]["data"] == {"session": "chat-1", "pct": 0.0}

    @pytest.mark.asyncio
    async def test_callback_broadcast_runs_even_if_append_fails(
        self, state: DashboardState, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from personalclaw.dashboard.state import _ChatSession

        ws = MagicMock(closed=False)
        ws.send_str = AsyncMock()
        state.register_ws(ws)
        state.get_or_create_session("chat-1")
        # _ChatSession uses __slots__, so monkeypatch at the class level.
        monkeypatch.setattr(
            _ChatSession, "append", MagicMock(side_effect=RuntimeError("append boom"))
        )
        cb = self._captured_callback(state)

        await cb("dashboard:chat-1", 92.0)

        payloads = [json.loads(c.args[0]) for c in ws.send_str.call_args_list]
        context = [p for p in payloads if p.get("type") == "context_usage"]
        assert len(context) == 1

    @pytest.mark.asyncio
    async def test_callback_broadcast_failure_does_not_propagate(
        self, state: DashboardState
    ) -> None:
        session = state.get_or_create_session("chat-1")
        cb = self._captured_callback(state)
        # Force broadcast to raise — append should still land, callback should return cleanly
        with pytest.MonkeyPatch.context() as mp:
            def boom(*a, **kw):
                raise RuntimeError("ws boom")
            mp.setattr(state, "broadcast_ws", boom)

            await cb("dashboard:chat-1", 92.0)

        assert session.messages[-1]["role"] == "assistant"
