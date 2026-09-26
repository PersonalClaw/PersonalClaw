"""An installed app cannot post into your chats, rooms or runs, or steer them, and a conversation of
its own runs under its own grant. The conversation families #3618 left undeclared.

Every refusal here is driven through the REAL ``app_permission_middleware`` for an app installed in
a scratch home (#3614's harness), and reads the Security Event Log row that names the app. The
owner-side cases drive the same routes with no app identity. Imports of names this change adds sit
inside the tests, so on a tree without them each test fails on its own rather than the module
failing to collect.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_state
from test_apps_cannot_run_code_or_bypass_approvals import APP, _denials, _home, _install


@pytest.fixture
def sel_rows():
    """Every row the permission middleware writes (it imports ``sel`` at the refusal)."""
    fake = MagicMock()
    with patch("personalclaw.sel.sel", return_value=fake):
        yield fake.log_api_access


#: What the probe app declares: the chat, session, room, inbox and reveal families, so a refusal
#: is never "path not declared". `agent` too, so a refusal on a turn route is the OWNERSHIP rule
#: and not the missing grant (the agent grant has its own tests below).
DECLARED = {
    "api": ["/api/chat", "/api/sessions", "/api/rooms", "/api/inbox", "/api/reveal"],
    "agent": True,
}


def _gateway(state: Any, caller: str, routes: list[tuple[str, str, Any]]) -> web.Application:
    """The real permission middleware and the request boundary in front of *routes*, over a real
    ``DashboardState`` — the ownership half reads the conversation's creator off it."""
    from personalclaw.dashboard.request_boundary import request_boundary_middleware
    from personalclaw.dashboard.server import app_permission_middleware

    @web.middleware
    async def identity(request: web.Request, handler: Any) -> web.StreamResponse:
        request["user"] = "owner"
        if caller:
            request["app"] = caller
        return await handler(request)

    gw = web.Application(
        middlewares=[identity, app_permission_middleware, request_boundary_middleware()]
    )
    gw["state"] = state
    for method, template, handler in routes:
        gw.router.add_route(method, template, handler)
    return gw


def _stub() -> tuple[Any, list[str]]:
    reached: list[str] = []

    async def handler(request: web.Request) -> web.Response:
        reached.append(request.path)
        return web.json_response({"reached": True})

    return handler, reached


async def _send(state, caller: str, method: str, template: str, path: str, body: Any = None):
    """One request through a gateway whose route is a stub; whether it was REACHED is the
    verdict."""
    handler, reached = _stub()
    async with TestClient(TestServer(_gateway(state, caller, [(method, template, handler)]))) as c:
        resp = await c.request(method, path, json=body if body is not None else {})
        return resp.status, await resp.text(), reached


def _owner_chat(state, name: str = "mine", *, text: str = "my bank PIN is 4471"):
    session = state.get_or_create_session(name)
    session.append("user", text, "msg msg-u")
    session.drain()
    return session


# ── 1. Your chats ─────────────────────────────────────────────────────────────────────


#: Every write that addresses one conversation by `{session}`, with a body its handler would accept.
#: Railed against the route census below, so a `{session}` write added tomorrow has to be listed
#: here to keep this file green.
SESSION_WRITES: list[tuple[str, str, dict]] = [
    ("POST", "/api/chat/sessions/{session}/resume", {}),
    ("POST", "/api/chat/sessions/{session}/approve", {"action": "approved"}),
    ("POST", "/api/chat/sessions/{session}/regenerate", {}),
    ("POST", "/api/chat/sessions/{session}/edit-resend", {"index": 0, "content": "now do X"}),
    ("POST", "/api/chat/sessions/{session}/switch-variant", {"index": 0, "variant": 0}),
    ("POST", "/api/chat/sessions/{session}/side/open", {}),
    ("POST", "/api/chat/sessions/{session}/side/turn", {"message": "and then?"}),
    ("POST", "/api/chat/sessions/{session}/side/close", {}),
    ("POST", "/api/chat/sessions/{session}/plan/activate", {}),
    ("POST", "/api/chat/sessions/{session}/plan/edit", {"step_id": "s", "markdown": "x"}),
    ("POST", "/api/chat/sessions/{session}/plan/comment", {"step_id": "s", "text": "x"}),
    ("POST", "/api/chat/sessions/{session}/plan/approve", {"step_id": "s"}),
    ("POST", "/api/chat/sessions/{session}/plan/cancel", {}),
    ("POST", "/api/chat/sessions/{session}/generate-title", {}),
    ("POST", "/api/chat/sessions/{session}/context", {"content": "the user wants X"}),
    ("POST", "/api/chat/sessions/{session}/stop", {}),
    ("POST", "/api/chat/sessions/{session}/interrupt", {}),
    ("DELETE", "/api/chat/sessions/{session}/queue/{queue_id}", {}),
    ("DELETE", "/api/chat/sessions/{session}", {}),
    ("POST", "/api/chat/sessions/{session}/agent", {"agent": "helper"}),
    ("POST", "/api/chat/sessions/{session}/acp-agent", {"provider": "acp:claude-code"}),
    ("POST", "/api/chat/sessions/{session}/model", {"model": "other"}),
    ("POST", "/api/chat/sessions/{session}/reasoning-effort", {"reasoning_effort": "high"}),
    ("POST", "/api/chat/sessions/{session}/workspace-dir", {"workspace_dir": "/"}),
    ("PATCH", "/api/chat/sessions/{session}/color", {"color_index": 1}),
    ("PATCH", "/api/chat/sessions/{session}/natural-voice", {"natural_voice": "on"}),
    ("PATCH", "/api/chat/sessions/{session}/title", {"title": "pwned"}),
    ("PATCH", "/api/chat/sessions/{session}/pin", {"pinned": True}),
    ("PATCH", "/api/chat/sessions/{session}/folder", {"folder_id": ""}),
    ("PUT", "/api/chat/sessions/{session}/tags", {"tags": []}),
    ("POST", "/api/chat/sessions/{session}/drop", {"column_id": "c"}),
    ("PATCH", "/api/chat/sessions/{session}/lifecycle", {"lifecycle": "archived"}),
    ("POST", "/api/chat/sessions/{session}/organize/accept", {}),
    ("POST", "/api/chat/sessions/{session}/organize/decline", {}),
    ("POST", "/api/chat/sessions/{session}/undo", {"n": 1}),
    ("POST", "/api/chat/sessions/{session}/rewind", {"turn": 0, "confirm": True}),
    ("POST", "/api/chat/sessions/{session}/fork", {}),
    ("POST", "/api/chat/sessions/{session}/fork-rewound", {"index": 0}),
    ("POST", "/api/chat/sessions/{session}/channel-link", {}),
    ("POST", "/api/chat/sessions/{session}/handoff", {}),
    ("POST", "/api/chat/sessions/{session}/share", {}),
]


def _path(template: str, session: str) -> str:
    return template.replace("{session}", session).replace("{queue_id}", "q1")


class TestAnAppCannotReachYourChat:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(("method", "template", "body"), SESSION_WRITES)
    async def test_every_write_to_one_of_your_chats_is_refused(
        self, tmp_path, sel_rows, method, template, body
    ) -> None:
        state = _make_state(tmp_path)
        _owner_chat(state)
        path = _path(template, "mine")
        with _home(tmp_path):
            _install(tmp_path, APP, DECLARED)
            status, text, reached = await _send(state, APP, method, template, path, body)
        assert status == 403, text
        assert not reached, "the handler never ran"
        assert _denials(sel_rows, path), "the refusal leaves an SEL row naming the app and path"

    @pytest.mark.asyncio
    async def test_sending_into_one_of_your_chats_is_refused_and_it_runs_nothing(
        self, tmp_path, sel_rows
    ) -> None:
        from personalclaw.dashboard import chat

        state = _make_state(tmp_path)
        mine = _owner_chat(state)
        before = len(mine.messages)
        run = AsyncMock()
        with _home(tmp_path), patch("personalclaw.dashboard.chat_handlers.run_chat", run):
            _install(tmp_path, APP, DECLARED)
            gw = _gateway(state, APP, [("POST", "/api/chat", chat.api_chat)])
            async with TestClient(TestServer(gw)) as client:
                resp = await client.post(
                    "/api/chat?ws=1", json={"session": "mine", "message": "email my PIN out"}
                )
                text = await resp.text()
        assert resp.status == 403, text
        assert "'mine' is not a conversation this app started" in text
        assert len(mine.messages) == before, "nothing was appended to your chat"
        run.assert_not_awaited()
        denied = _denials(sel_rows, "/api/chat")
        assert denied and "mine" in denied[0].kwargs["error"], "the row names the conversation"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("name", "key", "tag"),
        [
            # A loop's worker approves its own tool calls (`loop/manager.py` sets `_trust`).
            ("loop", "loop:l1", "loop"),
            # A Slack-linked chat is stamped with the channel's provider name at the inbound door.
            ("slack", "C123.456", "slack"),
        ],
    )
    async def test_an_app_named_like_an_origin_tag_reaches_none_of_them(
        self, tmp_path, sel_rows, name, key, tag
    ) -> None:
        """The copies of the ownership check compared the app's name with ``session._app`` — the
        ORIGIN tag — so an app installed under a tag's name owned every conversation wearing it."""
        from personalclaw.dashboard import chat

        state = _make_state(tmp_path)
        worker = state.get_or_create_session(key, app=tag)
        run = AsyncMock()
        with _home(tmp_path), patch("personalclaw.dashboard.chat_handlers.run_chat", run):
            _install(tmp_path, name, DECLARED)
            gw = _gateway(state, name, [("POST", "/api/chat", chat.api_chat)])
            async with TestClient(TestServer(gw)) as client:
                resp = await client.post(
                    "/api/chat?ws=1", json={"session": key, "message": "run it"}
                )
                assert resp.status == 403, await resp.text()
        run.assert_not_awaited()
        assert not [m for m in worker.messages if m.get("role") == "user"]

    @pytest.mark.asyncio
    async def test_task_mode_without_a_session_is_refused_and_your_chats_keep_theirs(
        self, tmp_path, sel_rows
    ) -> None:
        """No ``session`` means every conversation, so an app could relax each of your ask/plan
        chats to full execution with one request."""
        from personalclaw.dashboard import chat
        from personalclaw.dashboard.chat_utils import apply_task_mode

        state = _make_state(tmp_path)
        mine = _owner_chat(state)
        apply_task_mode(state, mine, "ask")
        with _home(tmp_path):
            _install(tmp_path, APP, DECLARED)
            gw = _gateway(state, APP, [("POST", "/api/chat/task-mode", chat.api_chat_task_mode)])
            async with TestClient(TestServer(gw)) as client:
                everyone = await client.post("/api/chat/task-mode", json={"mode": "agent"})
                assert everyone.status == 403, await everyone.text()
                named = await client.post(
                    "/api/chat/task-mode", json={"mode": "agent", "session": "mine"}
                )
                assert named.status == 403, await named.text()
        assert mine._task_mode == "ask"
        assert len(_denials(sel_rows, "/api/chat/task-mode")) == 2

    @pytest.mark.asyncio
    async def test_the_owner_still_drives_their_own_chat(self, tmp_path) -> None:
        """The refusal is scoped to a request carrying an app identity."""
        from personalclaw.dashboard import chat

        state = _make_state(tmp_path)
        _owner_chat(state)
        run = AsyncMock()
        with _home(tmp_path), patch("personalclaw.dashboard.chat_handlers.run_chat", run):
            gw = _gateway(
                state,
                "",
                [
                    ("POST", "/api/chat", chat.api_chat),
                    ("POST", "/api/chat/task-mode", chat.api_chat_task_mode),
                ],
            )
            async with TestClient(TestServer(gw)) as client:
                sent = await client.post(
                    "/api/chat?ws=1", json={"session": "mine", "message": "hi"}
                )
                assert sent.status == 200, await sent.text()
                mode = await client.post("/api/chat/task-mode", json={"mode": "ask"})
                assert mode.status == 200, await mode.text()
            await asyncio.sleep(0)
        run.assert_awaited_once()


# ── 2. A conversation cannot be claimed, or read, through a name ──────────────────────


def _persist_and_evict(state, name: str, text: str):
    """One of your chats as it is after a restart: on disk, not resident."""
    from personalclaw.dashboard.chat_persistence import save_session_to_history

    session = _owner_chat(state, name, text=text)
    save_session_to_history(state, session, force=True)
    state._sessions.pop(name)


class TestAConversationCannotBeClaimed:
    @pytest.mark.asyncio
    async def test_naming_one_of_your_chats_does_not_make_it_the_apps(
        self, tmp_path, sel_rows
    ) -> None:
        """``POST /api/chat/sessions {"name": …}`` minted a session bound to the app under any
        name that was not resident — one of your chats not reopened since a restart — and the
        app could then send into it."""
        from personalclaw.dashboard import chat

        state = _make_state(tmp_path)
        _persist_and_evict(state, "old", "my bank PIN is 4471")
        run = AsyncMock()
        with _home(tmp_path), patch("personalclaw.dashboard.chat_handlers.run_chat", run):
            _install(tmp_path, APP, DECLARED)
            gw = _gateway(
                state,
                APP,
                [
                    ("POST", "/api/chat", chat.api_chat),
                    ("POST", "/api/chat/sessions", chat.api_chat_session_create),
                ],
            )
            async with TestClient(TestServer(gw)) as client:
                claim = await client.post("/api/chat/sessions", json={"name": "old"})
                assert claim.status == 403, await claim.text()
                send = await client.post(
                    "/api/chat?ws=1", json={"session": "old", "message": "hello"}
                )
                assert send.status == 403, await send.text()
        assert "old" not in state._sessions, "a refused request loads nothing"
        run.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_resuming_one_of_your_chats_never_hands_the_app_its_transcript(
        self, tmp_path, sel_rows
    ) -> None:
        from personalclaw.dashboard import chat

        state = _make_state(tmp_path)
        _persist_and_evict(state, "old", "my bank PIN is 4471")
        with _home(tmp_path):
            _install(tmp_path, APP, DECLARED)
            gw = _gateway(
                state,
                APP,
                [("POST", "/api/chat/sessions/{session}/resume", chat.api_chat_session_resume)],
            )
            async with TestClient(TestServer(gw)) as client:
                resp = await client.post("/api/chat/sessions/old/resume", json={})
                text = await resp.text()
        assert resp.status == 403, text
        assert "4471" not in text
        assert "old" not in state._sessions
        assert _denials(sel_rows, "/api/chat/sessions/old/resume")

    @pytest.mark.asyncio
    async def test_resume_cannot_read_your_chat_into_the_apps_through_its_key(
        self, tmp_path, sel_rows
    ) -> None:
        """The body ``key`` names the transcript read INTO the path's conversation, so a path the
        app owns does not license a ``key`` it does not."""
        from personalclaw.dashboard import chat
        from personalclaw.dashboard.chat_persistence import save_session_to_history

        state = _make_state(tmp_path)
        _persist_and_evict(state, "old", "my bank PIN is 4471")
        ours = state.get_or_create_session("ours", created_by_app=APP)
        ours.append("user", "the app's own line", "msg msg-u")
        ours.drain()
        save_session_to_history(state, ours, force=True)
        state._sessions.pop("ours")
        with _home(tmp_path):
            _install(tmp_path, APP, DECLARED)
            gw = _gateway(
                state,
                APP,
                [("POST", "/api/chat/sessions/{session}/resume", chat.api_chat_session_resume)],
            )
            async with TestClient(TestServer(gw)) as client:
                resp = await client.post("/api/chat/sessions/ours/resume", json={"key": "old"})
                text = await resp.text()
                assert resp.status == 403, text
                assert "4471" not in text
                # Its own, named the ordinary way, still resumes.
                own = await client.post("/api/chat/sessions/ours/resume", json={})
                assert own.status == 200, await own.text()
                assert "the app's own line" in await own.text()


# ── 3. A conversation of the app's own ────────────────────────────────────────────────


class TestAnAppsOwnConversation:
    @pytest.mark.asyncio
    async def test_the_app_starts_one_and_speaks_in_it(self, tmp_path) -> None:
        from personalclaw.dashboard import chat

        state = _make_state(tmp_path)
        run = AsyncMock()
        with _home(tmp_path), patch("personalclaw.dashboard.chat_handlers.run_chat", run):
            _install(tmp_path, APP, DECLARED)
            gw = _gateway(
                state,
                APP,
                [
                    ("POST", "/api/chat", chat.api_chat),
                    ("POST", "/api/chat/sessions", chat.api_chat_session_create),
                ],
            )
            async with TestClient(TestServer(gw)) as client:
                made = await client.post("/api/chat/sessions", json={})
                assert made.status == 200, await made.text()
                key = (await made.json())["key"]
                sent = await client.post("/api/chat?ws=1", json={"session": key, "message": "hi"})
                assert sent.status == 200, await sent.text()
                fresh = await client.post("/api/chat?ws=1", json={"message": "a new one"})
                assert fresh.status == 200, await fresh.text()
                fresh_key = (await fresh.json())["session"]
            await asyncio.sleep(0)
        assert state._sessions[key].created_by_app == APP
        assert state._sessions[fresh_key].created_by_app == APP
        assert state._sessions[key]._app == APP, "it stays out of your chat list"
        assert run.await_count == 2

    @pytest.mark.asyncio
    async def test_without_the_agent_grant_its_turn_is_refused(self, tmp_path, sel_rows) -> None:
        """A turn is your model working with your tools, and the grant that says an app runs agents
        is ``agent``. An app holding only the ``/api/chat`` path ran your agent before this."""
        from personalclaw.dashboard import chat

        state = _make_state(tmp_path)
        own = state.get_or_create_session("ours", created_by_app=APP)
        run = AsyncMock()
        with _home(tmp_path), patch("personalclaw.dashboard.chat_handlers.run_chat", run):
            _install(tmp_path, APP, {"api": ["/api/chat"]})
            gw = _gateway(state, APP, [("POST", "/api/chat", chat.api_chat)])
            async with TestClient(TestServer(gw)) as client:
                for body in ({"message": "hi"}, {"session": "ours", "message": "hi"}):
                    resp = await client.post("/api/chat?ws=1", json=body)
                    text = await resp.text()
                    assert resp.status == 403, text
                    assert "`agent` permission" in text, text
        run.assert_not_awaited()
        assert not own.messages
        assert len(_denials(sel_rows, "/api/chat")) == 2

    @pytest.mark.asyncio
    async def test_it_is_still_the_apps_after_a_restart(self, tmp_path) -> None:
        """The creator is on the meta line and read back where every restore mints its session —
        it used to come back as one of yours, which the app could no longer reach and which then
        ran under your switches."""
        from personalclaw.dashboard import chat
        from personalclaw.dashboard.chat_persistence import (
            restore_recent_sessions,
            save_session_to_history,
        )

        before = _make_state(tmp_path)
        own = before.get_or_create_session("ours", created_by_app=APP)
        own.append("user", "the app's first line", "msg msg-u")
        own.drain()
        save_session_to_history(before, own, force=True)

        after = _make_state(tmp_path)  # a new gateway over the same home
        restore_recent_sessions(after, window_minutes=0)
        assert after._sessions["ours"].created_by_app == APP
        assert after._sessions["ours"]._app == APP, "still out of your chat list"
        after._sessions.pop("ours")  # and the targeted rehydrate reads it too
        run = AsyncMock()
        with _home(tmp_path), patch("personalclaw.dashboard.chat_handlers.run_chat", run):
            _install(tmp_path, APP, DECLARED)
            gw = _gateway(after, APP, [("POST", "/api/chat", chat.api_chat)])
            async with TestClient(TestServer(gw)) as client:
                resp = await client.post(
                    "/api/chat?ws=1", json={"session": "ours", "message": "again"}
                )
                assert resp.status == 200, await resp.text()
            await asyncio.sleep(0)
        assert after._sessions["ours"].created_by_app == APP
        run.assert_awaited_once()

    def test_a_persisted_record_decides_the_creator_whatever_a_caller_passes(
        self, tmp_path
    ) -> None:
        """The chokepoint every restore path goes through keeps the recorded creator in both
        directions: an app's conversation stays the app's, and one of yours never becomes one."""
        from personalclaw.dashboard.chat_persistence import save_session_to_history

        state = _make_state(tmp_path)
        _persist_and_evict(state, "yours", "x")
        app_chat = state.get_or_create_session("theirs", created_by_app=APP)
        app_chat.append("user", "the app's line", "msg msg-u")
        app_chat.drain()
        save_session_to_history(state, app_chat, force=True)
        state._sessions.pop("theirs")

        assert state.get_or_create_session("yours", created_by_app=APP).created_by_app == ""
        assert state.get_or_create_session("theirs").created_by_app == APP
        assert state.session_creating_app("yours") == ""
        assert state.session_creating_app("nothing-by-this-name") == ""


# ── 4. How an app's conversation approves: the app's grant, never your switches ────────


def _runner(tmp_path):
    """``run_chat`` over a synthetic event stream — the harness the host-permission-authority
    tests use — so the posture is measured at the approval gate itself."""
    from test_acp_permission_authority import _context_builder
    from test_acp_permission_authority import _make_state as _runner_state

    return _runner_state(tmp_path, context_builder=_context_builder())


def _ask(client, *, safe: bool = False) -> None:
    from test_acp_permission_authority import _set_stream

    from personalclaw.llm.base import EVENT_COMPLETE, EVENT_PERMISSION_REQUEST, LLMEvent

    request = (
        LLMEvent(
            kind=EVENT_PERMISSION_REQUEST,
            title="read_file",
            tool_kind="read",
            request_id="req-1",
            risk_level="safe",
            tool_input='{"path": "/tmp/app-probe.txt"}',
        )
        if safe
        else LLMEvent(
            kind=EVENT_PERMISSION_REQUEST,
            title="fs_write",
            tool_kind="edit",
            request_id="req-1",
            tool_input='{"path": "/tmp/app-probe.txt", "content": "x"}',
        )
    )
    _set_stream(client, [request, LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")])


def _chat(key: str, *, creator: str = "", agent: str = ""):
    from personalclaw.dashboard.state import _ChatSession

    session = _ChatSession(key, agent=agent)
    if creator:
        session.created_by_app = creator
    return session


#: Each of YOUR approval switches, and the config/state that turns it on.
YOUR_SWITCHES = ["yolo", "agent_floor", "trust_reads_default"]


def _turn_on(switch: str, state, tmp_path: Path) -> tuple[str, bool]:
    """Turn *switch* on; returns the agent the chat should bind and whether the probe tool must be
    a SAFE read (Trust reads approves only those)."""
    if switch == "yolo":
        state.is_yolo_active = lambda: True
        return "", False
    if switch == "agent_floor":
        cfg = {"agents": {"helper": {"approval_mode": "auto"}}}
        (tmp_path / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
        return "helper", False
    cfg = {"agent": {"approval_mode": "trust_reads"}}
    (tmp_path / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    return "", True


class TestAnAppsConversationApprovesByItsOwnGrant:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("switch", YOUR_SWITCHES)
    async def test_none_of_your_switches_reaches_it(self, tmp_path, switch) -> None:
        """An app WITHOUT the ``agent`` grant: its conversation asks, whatever you switched on."""
        from test_acp_permission_authority import _drive

        state, client = _runner(tmp_path)
        agent, safe = _turn_on(switch, state, tmp_path)
        with _home(tmp_path):
            _install(tmp_path, APP, {"api": ["/api/chat"]})
            session = _chat("chat-app-1", creator=APP, agent=agent)
            _ask(client, safe=safe)
            await _drive(state, session, answer="denied")
        client.approve_tool.assert_not_awaited()
        client.reject_tool.assert_awaited_once()
        assert any(m.get("role") == "permission" for m in session.messages), "you were asked"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("switch", YOUR_SWITCHES)
    async def test_each_switch_still_works_in_your_own_chat(self, tmp_path, switch) -> None:
        """The inverse floor: the refusal above is about WHOSE conversation, not a dead switch."""
        from test_acp_permission_authority import _drive

        state, client = _runner(tmp_path)
        agent, safe = _turn_on(switch, state, tmp_path)
        with _home(tmp_path):
            session = _chat("chat-yours-1", agent=agent)
            _ask(client, safe=safe)
            await _drive(state, session, answer="denied")
        client.approve_tool.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_the_agent_grant_approves_the_apps_own_conversation(self, tmp_path) -> None:
        """The grant install consent words as agents that use any tool without asking — the same
        posture the app's background agent runs take — and the audit row says whose grant it was."""
        from test_acp_permission_authority import _drive

        state, client = _runner(tmp_path)
        rows = MagicMock()
        with _home(tmp_path):
            _install(tmp_path, APP, DECLARED)
            session = _chat("chat-app-2", creator=APP)
            _ask(client)
            await _drive(state, session, answer="denied", sel_mock=rows)
        client.approve_tool.assert_awaited_once()
        client.reject_tool.assert_not_awaited()
        reasons = [
            c.kwargs.get("metadata", {}).get("reason")
            for c in rows.return_value.log_tool_invocation.call_args_list
            if c.kwargs.get("outcome") == "auto_approved"
        ]
        assert reasons == ["app_grant"], reasons

    def test_the_operator_ceiling_still_bounds_the_grant(self, tmp_path) -> None:
        from personalclaw.apps.permissions import app_conversation_auto_approves
        from personalclaw.dashboard.chat_runner import app_conversation_posture

        with _home(tmp_path):
            _install(tmp_path, APP, DECLARED)
            assert app_conversation_auto_approves(APP) is True
            with patch(
                "personalclaw.guardrails.policy.ceiling_permits_approval", return_value=False
            ):
                assert app_conversation_auto_approves(APP) is False
            assert app_conversation_posture(_chat("c", creator=APP)) is True
            assert app_conversation_posture(_chat("c")) is None, "yours: your switches decide"

    def test_a_disabled_or_uninstalled_app_approves_nothing(self, tmp_path) -> None:
        from personalclaw.apps.permissions import app_conversation_auto_approves

        with _home(tmp_path):
            _install(tmp_path, APP, DECLARED)
            installed = tmp_path / "apps" / APP / "installed.json"
            installed.write_text(json.dumps({"name": APP, "enabled": False}), encoding="utf-8")
            assert app_conversation_auto_approves(APP) is False
            assert app_conversation_auto_approves("never-installed") is False


# ── 5. Approvals: whether a tool call runs is yours to say ─────────────────────────────


#: The chat card's whole vocabulary: a one-off answer and the four standing grants alike.
APPROVAL_VERBS = ["approved", "rejected", "trust", "trust_agent", "trust_reads", "yolo"]


def _pending(state, name: str, *, creator: str = ""):
    """A conversation holding one pending approval, ``req-1``, and that approval's future."""
    session = state.get_or_create_session(name, **({"created_by_app": creator} if creator else {}))
    future = asyncio.get_running_loop().create_future()
    session._approval_futures["req-1"] = future
    return session, future


class TestAnAppAnswersNoApprovalInAChat:
    """The chat's approve route is yours in every conversation. The relay the menu-bar companion
    runs is ``/api/approvals``, which it declares: it carries your answer, once, and never answers
    what the app's own conversation asked."""

    def test_the_verbs_are_the_cards_whole_vocabulary(self) -> None:
        from personalclaw.dashboard.approval_state import SESSION_APPROVAL_ACTIONS

        assert set(APPROVAL_VERBS) == SESSION_APPROVAL_ACTIONS

    @pytest.mark.asyncio
    @pytest.mark.parametrize("verb", APPROVAL_VERBS)
    @pytest.mark.parametrize("whose", ["yours", "the app's"])
    async def test_no_verb_answers_a_chats_approval(self, tmp_path, sel_rows, verb, whose) -> None:
        from personalclaw.dashboard import chat

        state = _make_state(tmp_path)
        name = "mine" if whose == "yours" else "ours"
        with _home(tmp_path):
            _install(tmp_path, APP, DECLARED)
            _session, pending = _pending(state, name, creator="" if whose == "yours" else APP)
            route = ("POST", "/api/chat/sessions/{session}/approve", chat.api_chat_session_approve)
            async with TestClient(TestServer(_gateway(state, APP, [route]))) as client:
                resp = await client.post(
                    f"/api/chat/sessions/{name}/approve",
                    json={"action": verb, "request_id": "req-1"},
                )
                text = await resp.text()
        assert resp.status == 403, text
        assert "owner-only" in text, text
        assert not pending.done(), "nothing was decided"
        assert _denials(sel_rows, f"/api/chat/sessions/{name}/approve")

    @pytest.mark.asyncio
    async def test_you_still_answer_in_the_chat(self, tmp_path) -> None:
        from personalclaw.dashboard import chat

        state = _make_state(tmp_path)
        _session, pending = _pending(state, "mine")
        route = ("POST", "/api/chat/sessions/{session}/approve", chat.api_chat_session_approve)
        async with TestClient(TestServer(_gateway(state, "", [route]))) as client:
            resp = await client.post(
                "/api/chat/sessions/mine/approve",
                json={"action": "approved", "request_id": "req-1"},
            )
            assert resp.status == 200, await resp.text()
        assert pending.done() and pending.result() == "approved"

    @pytest.mark.asyncio
    async def test_the_companion_relays_your_answer_through_the_approvals_list(
        self, tmp_path
    ) -> None:
        """What the menu-bar companion does (``POST /api/approvals/{id}/approve``), unchanged."""
        from personalclaw.dashboard.approval_state import chat_approval_id
        from personalclaw.dashboard.handlers.sessions import api_approval_resolve

        state = _make_state(tmp_path)
        mine, pending = _pending(state, "mine")
        approval_id = chat_approval_id(mine.key, "req-1")
        state._pending_approvals[approval_id] = {
            "id": approval_id,
            "session": mine.key,
            "request_id": "req-1",
        }
        with _home(tmp_path):
            _install(tmp_path, APP, {"api": ["/api/approvals"]})
            route = ("POST", "/api/approvals/{id}/{action}", api_approval_resolve)
            async with TestClient(TestServer(_gateway(state, APP, [route]))) as client:
                resp = await client.post(f"/api/approvals/{approval_id}/approve")
                assert resp.status == 200, await resp.text()
        assert pending.done() and pending.result() == "approved"

    @pytest.mark.asyncio
    async def test_the_relay_never_answers_the_apps_own_conversation(self, tmp_path) -> None:
        from personalclaw.dashboard.approval_state import chat_approval_id
        from personalclaw.dashboard.handlers.sessions import api_approval_resolve

        state = _make_state(tmp_path)
        rows = MagicMock()
        with _home(tmp_path), patch("personalclaw.dashboard.handlers.sel", return_value=rows):
            _install(tmp_path, APP, {"api": ["/api/approvals"]})
            own, pending = _pending(state, "ours", creator=APP)
            approval_id = chat_approval_id(own.key, "req-1")
            state._pending_approvals[approval_id] = {
                "id": approval_id,
                "session": own.key,
                "request_id": "req-1",
            }
            route = ("POST", "/api/approvals/{id}/{action}", api_approval_resolve)
            async with TestClient(TestServer(_gateway(state, APP, [route]))) as client:
                resp = await client.post(f"/api/approvals/{approval_id}/approve")
                body = await resp.json()
        assert resp.status == 403, body
        assert body["error"]["code"] == "approval_owner_only"
        assert not pending.done()
        assert rows.log_api_access.call_args.kwargs["caller"] == f"app:{APP}"


# ── 6. Rooms, the inbox, and the doors that speak as your agent ────────────────────────


#: Writes an app never reaches: a room line is written as YOURS and starts every member's turn; an
#: inbox answer is yours; the delivery door speaks as your agent; your history is yours to delete.
OWNER_ONLY_CONVERSATION_WRITES = [
    ("POST", "/api/rooms", "/api/rooms"),
    ("PATCH", "/api/rooms/{room_id}", "/api/rooms/r1"),
    ("POST", "/api/rooms/{room_id}/archive", "/api/rooms/r1/archive"),
    ("POST", "/api/rooms/{room_id}/members", "/api/rooms/r1/members"),
    ("DELETE", "/api/rooms/{room_id}/members/{name}", "/api/rooms/r1/members/critic"),
    ("POST", "/api/rooms/{room_id}/messages", "/api/rooms/r1/messages"),
    ("POST", "/api/rooms/{room_id}/continue", "/api/rooms/r1/continue"),
    ("POST", "/api/inbox/send", "/api/inbox/send"),
    ("POST", "/api/inbox/{id}/apply", "/api/inbox/i1/apply"),
    ("POST", "/api/inbox/notes", "/api/inbox/notes"),
    ("POST", "/api/inbox/{id}/draft", "/api/inbox/i1/draft"),
    ("PUT", "/api/inbox/{id}", "/api/inbox/i1"),
    ("POST", "/api/inbox/dismiss-all", "/api/inbox/dismiss-all"),
    ("POST", "/api/inbox/seen", "/api/inbox/seen"),
    ("PUT", "/api/inbox/settings", "/api/inbox/settings"),
    ("POST", "/api/send-message", "/api/send-message"),
    ("POST", "/api/channel/upload-file", "/api/channel/upload-file"),
    ("DELETE", "/api/sessions/{key}", "/api/sessions/dashboard:mine"),
    ("POST", "/api/sessions/restart", "/api/sessions/restart"),
    ("POST", "/api/chat/sessions/templates", "/api/chat/sessions/templates"),
    ("POST", "/api/chat/folders", "/api/chat/folders"),
    ("POST", "/api/chat/screen-frame", "/api/chat/screen-frame"),
]


class TestRoomsTheInboxAndYourAgentsVoice:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(("method", "template", "path"), OWNER_ONLY_CONVERSATION_WRITES)
    async def test_refused_with_a_row(self, tmp_path, sel_rows, method, template, path) -> None:
        state = _make_state(tmp_path)
        with _home(tmp_path):
            _install(tmp_path, APP, DECLARED)
            status, text, reached = await _send(state, APP, method, template, path)
        assert status == 403, text
        assert "owner-only" in text, text
        assert not reached
        assert _denials(sel_rows, path)

    @pytest.mark.asyncio
    async def test_the_owner_still_speaks_in_a_room(self, tmp_path) -> None:
        state = _make_state(tmp_path)
        with _home(tmp_path):
            status, _, reached = await _send(
                state, "", "POST", "/api/rooms/{room_id}/messages", "/api/rooms/r1/messages"
            )
        assert status == 200 and reached

    def test_a_manifest_naming_the_delivery_door_does_not_install(self) -> None:
        from personalclaw.apps.manifest import AppManifest

        errors = AppManifest.from_dict(
            {
                "name": "speaks-as-you",
                "version": "1.0.0",
                "displayName": "x",
                "description": "x",
                "permissions": {"api": ["/api/send-message"]},
            }
        ).validate()
        assert any("owner-only" in e for e in errors), errors

    @pytest.mark.asyncio
    async def test_an_app_still_reaches_you_with_a_proposal_that_names_it(
        self, tmp_path, monkeypatch
    ) -> None:
        """The app's own way to you stays open through the real middleware, labelled as the app."""
        from test_inbox_app_proposals import _State

        from personalclaw import proposals_contract as pc
        from personalclaw.apps import app_manager
        from personalclaw.dashboard import handlers_inbox

        monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))  # as the proposal tests' harness
        src = tmp_path / "src" / APP
        src.mkdir(parents=True)
        manifest = {
            "name": APP,
            "version": "1.0.0",
            "displayName": APP,
            "description": "x",
            "permissions": {
                "api": ["/api/inbox/proposals"],
                "proposals": [{"kind_suffix": "draft", "label": "Draft"}],
            },
        }
        (src / "app.json").write_text(json.dumps(manifest), encoding="utf-8")
        with _home(tmp_path), patch("personalclaw.inbox.config_dir", return_value=tmp_path):
            assert app_manager.install(src, confirm=True).ok
            route = ("POST", "/api/inbox/proposals", handlers_inbox.api_inbox_proposal_create)
            async with TestClient(TestServer(_gateway(_State(), APP, [route]))) as client:
                resp = await client.post(
                    "/api/inbox/proposals",
                    json={
                        "kind_suffix": "draft",
                        "title": "Send the reply?",
                        "apply": {"app_callback": {"route": "send"}},
                    },
                )
                assert resp.status == 201, await resp.text()
                item_id = (await resp.json())["id"]
            from personalclaw.inbox import InboxStore

            store = InboxStore()
            store.load()
            item = store.items[item_id]
        assert pc.Proposal.from_dict(item.refs[pc.REFS_KEY]).provenance == f"app:{APP}"
        assert item.source == f"app:{APP}"


# ── 7. What /api/reveal reaches for an app: the app's own data folder ──────────────────


class TestRevealReachesOnlyTheAppsOwnFiles:
    """``reveal`` puts a file on your screen and ``open`` hands it to your default app for its
    type, so an app names only a file in its own data folder."""

    def _gateway(self, tmp_path, caller: str):
        from personalclaw.dashboard.handlers.files import api_reveal_path

        return _gateway(_make_state(tmp_path), caller, [("POST", "/api/reveal", api_reveal_path)])

    @pytest.fixture
    def files(self, tmp_path, monkeypatch):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "notes.txt").write_text("yours", encoding="utf-8")
        monkeypatch.setattr("personalclaw.config.loader.workspace_root", lambda: workspace)
        data = tmp_path / "apps" / APP / "data"
        return workspace / "notes.txt", data

    @pytest.mark.asyncio
    async def test_a_file_of_yours_is_refused_with_a_row(self, tmp_path, files) -> None:
        yours, _ = files
        rows = MagicMock()
        popen = MagicMock()
        with (
            _home(tmp_path),
            patch("personalclaw.dashboard.handlers.sel", return_value=rows),
            patch("subprocess.Popen", popen),
        ):
            _install(tmp_path, APP, {"api": ["/api/reveal"], "storage": True})
            async with TestClient(TestServer(self._gateway(tmp_path, APP))) as client:
                resp = await client.post("/api/reveal", json={"path": str(yours), "action": "open"})
                body = await resp.json()
        assert resp.status == 403, body
        assert "its data folder" in body["error"]["message"], body
        popen.assert_not_called()
        assert rows.log_api_access.call_args.kwargs["caller"] == f"app:{APP}"

    @pytest.mark.asyncio
    async def test_a_file_in_its_own_data_folder_is_revealed(self, tmp_path, files) -> None:
        _, data = files
        popen = MagicMock()
        with _home(tmp_path), patch("subprocess.Popen", popen):
            _install(tmp_path, APP, {"api": ["/api/reveal"], "storage": True})
            data.mkdir(parents=True)
            (data / "report.html").write_text("<p>hi</p>", encoding="utf-8")
            async with TestClient(TestServer(self._gateway(tmp_path, APP))) as client:
                resp = await client.post(
                    "/api/reveal", json={"path": str(data / "report.html"), "action": "reveal"}
                )
                assert resp.status == 200, await resp.text()
        popen.assert_called_once()

    @pytest.mark.asyncio
    async def test_without_the_storage_grant_it_has_no_folder(self, tmp_path, files) -> None:
        _, data = files
        popen = MagicMock()
        with _home(tmp_path), patch("subprocess.Popen", popen):
            _install(tmp_path, APP, {"api": ["/api/reveal"]})
            data.mkdir(parents=True)
            (data / "report.html").write_text("<p>hi</p>", encoding="utf-8")
            async with TestClient(TestServer(self._gateway(tmp_path, APP))) as client:
                resp = await client.post("/api/reveal", json={"path": str(data / "report.html")})
                assert resp.status == 403, await resp.text()
        popen.assert_not_called()

    @pytest.mark.asyncio
    async def test_the_owner_reveals_their_own_file(self, tmp_path, files) -> None:
        yours, _ = files
        popen = MagicMock()
        with _home(tmp_path), patch("subprocess.Popen", popen):
            async with TestClient(TestServer(self._gateway(tmp_path, ""))) as client:
                resp = await client.post("/api/reveal", json={"path": str(yours)})
                assert resp.status == 200, await resp.text()
        popen.assert_called_once()


# ── 8. The route table: complete for these families, and it says why ──────────────────


def _census() -> set[tuple[str, str]]:
    from personalclaw.apps.permissions import WRITE_METHODS
    from personalclaw.manifest_reference import _routes_from_ast

    return {(r["method"], r["path"]) for r in _routes_from_ast() if r["method"] in WRITE_METHODS}


class TestTheRouteTableDeclaresYourConversations:
    def test_the_conversation_families_are_declared(self) -> None:
        from personalclaw.apps.permissions import SECURITY_ROUTE_FAMILIES, owner_only_api_reason

        for family in ("/api/chat", "/api/sessions", "/api/rooms", "/api/inbox", "/api/reveal"):
            assert family in SECURITY_ROUTE_FAMILIES, family
        assert "agent" in owner_only_api_reason("/api/send-message")
        assert "agent" in owner_only_api_reason("/api/channel/upload-file")

    def test_every_session_write_is_the_apps_own_or_yours(self) -> None:
        """A ``{session}`` write that an app may reach is held to the app's own conversations."""
        from personalclaw.apps.permissions import ROUTE_AUTHZ, AppMay, OwnedTarget

        rows = {k: v for k, v in ROUTE_AUTHZ.items() if "/api/chat/sessions/{session}" in k}
        assert len(rows) >= 40, f"only {len(rows)} session rows — vacuous"
        for key, authz in rows.items():
            if isinstance(authz, AppMay):
                assert OwnedTarget("session") in authz.owns, key

    def test_every_session_write_is_driven_above(self) -> None:
        listed = {(m, t) for m, t, _body in SESSION_WRITES}
        live = {(m, p) for m, p in _census() if p.startswith("/api/chat/sessions/{session}")}
        missing = sorted(live - listed)
        assert not missing, f"session writes this file never drives as an app: {missing}"

    def test_every_route_that_runs_a_turn_needs_the_agent_grant(self) -> None:
        from personalclaw.apps.permissions import ROUTE_AUTHZ

        for key in (
            "POST /api/chat",
            "POST /api/chat/sessions/{session}/regenerate",
            "POST /api/chat/sessions/{session}/edit-resend",
            "POST /api/chat/sessions/{session}/side/turn",
            "POST /api/chat/sessions/{session}/plan/comment",
            "POST /api/chat/sessions/{session}/plan/approve",
            "POST /api/chat/sessions/{session}/generate-title",
            "POST /api/chat/nav/resolve-links",
        ):
            assert ROUTE_AUTHZ[key].agent_work, key

    def test_running_work_is_steered_only_by_you(self) -> None:
        """A loop's nudge and a workflow run's steer stay owner-only; a loop's WORKER, reached
        through the chat instead, is section 1's origin-tag case."""
        from personalclaw.apps.permissions import ROUTE_AUTHZ, OwnerOnly

        for key in (
            "POST /api/loops/{id}/nudge",
            "POST /api/workflows/runs/{run_id}/steer",
            "POST /api/workflows/runs/{run_id}/resume",
            "PATCH /api/loops/{id}",
        ):
            assert isinstance(ROUTE_AUTHZ[key], OwnerOnly), key
