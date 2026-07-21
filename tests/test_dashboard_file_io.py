"""Tests for /api/file-read and /api/file-write endpoints."""

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.dashboard.handlers import (
    _sanitize_blocks,
    api_file_create,
    api_file_delete,
    api_file_list,
    api_file_move,
    api_file_read,
    api_file_upload,
    api_file_write,
    api_send_message,
)


def _make_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/api/file-read", api_file_read)
    app.router.add_post("/api/file-write", api_file_write)
    app.router.add_get("/api/file-list", api_file_list)
    app.router.add_post("/api/file-create", api_file_create)
    app.router.add_post("/api/file-move", api_file_move)
    app.router.add_post("/api/file-delete", api_file_delete)
    app.router.add_post("/api/file-upload", api_file_upload)
    return app


@pytest.fixture
def mock_sel():
    with patch("personalclaw.sel.sel") as m:
        instance = MagicMock()
        m.return_value = instance
        yield instance


@pytest.fixture
def tmp_file(tmp_path):
    f = tmp_path / "test.md"
    f.write_text("hello world")
    return f


@pytest.fixture
def home_patch(tmp_path):
    """Treat tmp_path as the dashboard's allowed root.

    The dashboard file I/O is sandboxed to an allowlist of roots built by
    ``_dashboard_roots`` (workspace, home, outbox, uploads). For tests we
    patch that allowlist to a single ``tmp_path`` root so file ops inside
    tmp_path are permitted and anything outside is denied — the real
    security boundary.
    """
    real_realpath = os.path.realpath

    def fake_expanduser(p):
        return p.replace("~", str(tmp_path))

    with (
        patch("os.path.expanduser", side_effect=fake_expanduser),
        patch("os.path.realpath", side_effect=real_realpath),
        patch("pathlib.Path.home", return_value=tmp_path),
        patch(
            "personalclaw.dashboard.handlers.files._dashboard_roots",
            return_value=[("Test", str(tmp_path))],
        ),
    ):
        yield tmp_path


class TestFileRead:
    @pytest.mark.asyncio
    async def test_read_success(self, tmp_file, mock_sel, home_patch):
        async with TestClient(TestServer(_make_app())) as client:
            resp = await client.get(f"/api/file-read?path={tmp_file}")
            assert resp.status == 200
            text = await resp.text()
            assert "hello world" in text
            mock_sel.log_tool_invocation.assert_called_with(
                session_key="dashboard",
                tool_name="file_read",
                outcome="success",
                resources=str(tmp_file),
            )

    @pytest.mark.asyncio
    async def test_read_missing_path(self, mock_sel):
        async with TestClient(TestServer(_make_app())) as client:
            resp = await client.get("/api/file-read?path=")
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_read_outside_allowlist(self, mock_sel, home_patch):
        """Paths outside the dashboard allowlist are denied (sandbox boundary)."""
        async with TestClient(TestServer(_make_app())) as client:
            resp = await client.get("/api/file-read?path=/etc/passwd")
            assert resp.status == 400  # outside allowed roots → forbidden

    @pytest.mark.asyncio
    async def test_read_sensitive_path(self, mock_sel, home_patch):
        ssh_dir = home_patch / ".ssh"
        ssh_dir.mkdir()
        key_file = ssh_dir / "id_rsa"
        key_file.write_text("secret")
        async with TestClient(TestServer(_make_app())) as client:
            resp = await client.get(f"/api/file-read?path={key_file}")
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_read_not_found(self, mock_sel, home_patch):
        async with TestClient(TestServer(_make_app())) as client:
            resp = await client.get(f"/api/file-read?path={home_patch}/nonexistent.txt")
            assert resp.status == 404


class TestFileWrite:
    @pytest.mark.asyncio
    async def test_write_success(self, tmp_file, mock_sel, home_patch):
        async with TestClient(TestServer(_make_app())) as client:
            resp = await client.post(
                "/api/file-write", json={"path": str(tmp_file), "content": "updated"}
            )
            assert resp.status == 200
            assert tmp_file.read_text() == "updated"
            mock_sel.log_tool_invocation.assert_called_with(
                session_key="dashboard",
                tool_name="file_write",
                outcome="success",
                resources=str(tmp_file),
            )

    @pytest.mark.asyncio
    async def test_write_outside_allowlist(self, mock_sel, home_patch):
        """Writes outside the dashboard allowlist are denied (sandbox boundary)."""
        async with TestClient(TestServer(_make_app())) as client:
            resp = await client.post("/api/file-write", json={"path": "/etc/evil", "content": "x"})
            assert resp.status == 400  # outside allowed roots → forbidden

    @pytest.mark.asyncio
    async def test_write_invalid_json(self, mock_sel):
        async with TestClient(TestServer(_make_app())) as client:
            resp = await client.post(
                "/api/file-write", data=b"not json", headers={"Content-Type": "application/json"}
            )
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_write_sensitive_path(self, mock_sel, home_patch):
        async with TestClient(TestServer(_make_app())) as client:
            resp = await client.post(
                "/api/file-write", json={"path": str(home_patch / ".ssh/id_rsa"), "content": "x"}
            )
            assert resp.status == 400


class TestFileList:
    @pytest.mark.asyncio
    async def test_list_roots_when_no_path(self, mock_sel, home_patch):
        async with TestClient(TestServer(_make_app())) as client:
            resp = await client.get("/api/file-list")
            assert resp.status == 200
            data = await resp.json()
            assert any(r["label"] == "Test" and r["path"] == str(home_patch) for r in data["roots"])

    @pytest.mark.asyncio
    async def test_list_directory_dirs_first(self, mock_sel, home_patch):
        (home_patch / "b.txt").write_text("x")
        (home_patch / "a_dir").mkdir()
        (home_patch / "a.txt").write_text("y")
        async with TestClient(TestServer(_make_app())) as client:
            resp = await client.get(f"/api/file-list?path={home_patch}")
            assert resp.status == 200
            data = await resp.json()
            names = [e["name"] for e in data["entries"]]
            # Directory sorts before files, then alphabetical.
            assert names == ["a_dir", "a.txt", "b.txt"]
            assert data["entries"][0]["is_dir"] is True

    @pytest.mark.asyncio
    async def test_list_outside_allowlist_denied(self, mock_sel, home_patch):
        async with TestClient(TestServer(_make_app())) as client:
            resp = await client.get("/api/file-list?path=/etc")
            assert resp.status == 400


class TestFileCreate:
    @pytest.mark.asyncio
    async def test_create_file(self, mock_sel, home_patch):
        async with TestClient(TestServer(_make_app())) as client:
            resp = await client.post(
                "/api/file-create",
                json={"path": str(home_patch), "name": "new.md", "kind": "file", "content": "hi"},
            )
            assert resp.status == 200
            assert (home_patch / "new.md").read_text() == "hi"

    @pytest.mark.asyncio
    async def test_create_dir(self, mock_sel, home_patch):
        async with TestClient(TestServer(_make_app())) as client:
            resp = await client.post(
                "/api/file-create", json={"path": str(home_patch), "name": "sub", "kind": "dir"}
            )
            assert resp.status == 200
            assert (home_patch / "sub").is_dir()

    @pytest.mark.asyncio
    async def test_create_refuses_overwrite(self, mock_sel, home_patch):
        (home_patch / "exists.txt").write_text("old")
        async with TestClient(TestServer(_make_app())) as client:
            resp = await client.post(
                "/api/file-create",
                json={"path": str(home_patch), "name": "exists.txt", "kind": "file"},
            )
            assert resp.status == 409
            assert (home_patch / "exists.txt").read_text() == "old"

    @pytest.mark.asyncio
    async def test_create_rejects_traversal_name(self, mock_sel, home_patch):
        async with TestClient(TestServer(_make_app())) as client:
            resp = await client.post(
                "/api/file-create",
                json={"path": str(home_patch), "name": "../escape.txt", "kind": "file"},
            )
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_create_outside_allowlist_denied(self, mock_sel, home_patch):
        async with TestClient(TestServer(_make_app())) as client:
            resp = await client.post(
                "/api/file-create", json={"path": "/etc", "name": "evil.txt", "kind": "file"}
            )
            assert resp.status == 400


def _make_send_app(state) -> web.Application:
    app = web.Application()
    app.router.add_post("/api/send-message", api_send_message)
    app["state"] = state
    return app


def _mock_state(channel_delivery=None, owner_id=""):
    state = MagicMock()
    state.channel_delivery = channel_delivery
    state.owner_id = owner_id
    return state


class TestSendMessage:
    @pytest.mark.asyncio
    async def test_send_message_missing_text(self):
        app = _make_send_app(_mock_state())
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/send-message", json={})
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_send_message_dashboard_only(self):
        state = _mock_state()
        app = _make_send_app(state)
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/send-message", json={"text": "hello"})
            assert resp.status == 200
            data = await resp.json()
            assert data == {"ok": True, "channel": False, "session": False}
            state.notify.assert_called_once_with("agent", "Agent Message", "hello")

    @pytest.mark.asyncio
    async def test_send_message_with_slack(self):
        slack = MagicMock()
        slack.open_dm = AsyncMock(return_value="C123")
        slack.deliver_text = AsyncMock(return_value="1712793600.000001")
        state = _mock_state(channel_delivery=slack, owner_id="U123")
        app = _make_send_app(state)
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/send-message", json={"text": "hello", "title": "Test"})
            assert resp.status == 200
            data = await resp.json()
            assert data == {
                "ok": True,
                "channel": True,
                "session": False,
                "ts": "1712793600.000001",
            }
            state.notify.assert_called_once_with("agent", "Test", "hello")
            slack.open_dm.assert_called_once_with("U123")
            slack.deliver_text.assert_called_once_with(
                "C123",
                "hello",
                thread_ts=None,
                unfurl_links=None,
                unfurl_media=None,
                reply_broadcast=None,
            )

    @pytest.mark.asyncio
    async def test_send_message_slack_error(self):
        slack = MagicMock()
        slack.open_dm = AsyncMock(side_effect=Exception("fail"))
        state = _mock_state(channel_delivery=slack, owner_id="U123")
        app = _make_send_app(state)
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/send-message", json={"text": "hello"})
            assert resp.status == 502
            data = await resp.json()
            assert data["ok"] is False
            assert "fail" in data["error"]

    @pytest.mark.asyncio
    async def test_send_message_slack_post_error(self):
        """502 when open_dm succeeds but post_message raises."""
        slack = MagicMock()
        slack.open_dm = AsyncMock(return_value="C123")
        slack.deliver_text = AsyncMock(side_effect=Exception("slack_api_error"))
        state = _mock_state(channel_delivery=slack, owner_id="U123")
        app = _make_send_app(state)
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/send-message", json={"text": "hello"})
            assert resp.status == 502
            data = await resp.json()
            assert data["ok"] is False
            assert "slack_api_error" in data["error"]

    @pytest.mark.asyncio
    async def test_send_message_with_blocks(self):
        """Blocks are sent via post_blocks with text as fallback."""
        slack = MagicMock()
        slack.open_dm = AsyncMock(return_value="C123")
        slack.deliver_rich = AsyncMock(return_value="1712793600.000001")
        state = _mock_state(channel_delivery=slack, owner_id="U123")
        app = _make_send_app(state)
        blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "hello"}}]
        async with TestClient(TestServer(app)) as client:
            resp = await client.post(
                "/api/send-message", json={"text": "fallback", "blocks": blocks}
            )
            assert resp.status == 200
            data = await resp.json()
            assert data == {
                "ok": True,
                "channel": True,
                "session": False,
                "ts": "1712793600.000001",
            }
            slack.deliver_rich.assert_called_once_with(
                "C123",
                blocks,
                "fallback",
                thread_ts=None,
                unfurl_links=None,
                unfurl_media=None,
                reply_broadcast=None,
            )
            slack.deliver_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_message_without_blocks_uses_post_message(self):
        """Without blocks, falls back to post_message (backward compat)."""
        slack = MagicMock()
        slack.open_dm = AsyncMock(return_value="C123")
        slack.deliver_text = AsyncMock(return_value="1712793600.000001")
        state = _mock_state(channel_delivery=slack, owner_id="U123")
        app = _make_send_app(state)
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/send-message", json={"text": "hello"})
            assert resp.status == 200
            slack.deliver_text.assert_called_once_with(
                "C123",
                "hello",
                thread_ts=None,
                unfurl_links=None,
                unfurl_media=None,
                reply_broadcast=None,
            )
            slack.deliver_rich.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_message_blocks_passed_to_post_blocks(self):
        """Blocks are forwarded to post_blocks with content intact."""
        slack = MagicMock()
        slack.open_dm = AsyncMock(return_value="C123")
        slack.deliver_rich = AsyncMock(return_value="1712793600.000001")
        state = _mock_state(channel_delivery=slack, owner_id="U123")
        app = _make_send_app(state)
        blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "safe text"}}]
        async with TestClient(TestServer(app)) as client:
            resp = await client.post(
                "/api/send-message", json={"text": "fallback", "blocks": blocks}
            )
            assert resp.status == 200
            # Verify blocks were passed (sanitized) — content should survive intact
            call_args = slack.deliver_rich.call_args
            sent_blocks = call_args[0][1]
            assert sent_blocks[0]["text"]["text"] == "safe text"

    @pytest.mark.asyncio
    async def test_send_message_session_origin(self):
        """session='origin' injects into the cron's originating session and triggers a turn."""
        state = _mock_state()
        # Mock a session that the cron originated from
        mock_session = MagicMock()
        mock_session.running = False
        mock_session.task = None
        mock_session.key = "chat-1-1712793600"
        state.get_session = MagicMock(return_value=mock_session)
        state._background_tasks = set()
        state.push_sessions_update = MagicMock()
        # Mock cron job with session_key pointing to the origin session
        mock_job = MagicMock()
        mock_job.id = "abc12345"
        mock_job.name = "check pipeline"
        mock_job.session_key = "dashboard:chat-1-1712793600"
        state.crons.list_jobs = MagicMock(return_value=[mock_job])
        app = _make_send_app(state)
        with (
            patch(
                "personalclaw.dashboard.chat_runner._run_chat", new_callable=AsyncMock
            ) as mock_run,
            patch(
                "personalclaw.dashboard.handlers.messaging._rehydrate_session_from_history"
            ) as mock_rehydrate,
        ):
            async with TestClient(TestServer(app)) as client:
                resp = await client.post(
                    "/api/send-message",
                    json={
                        "text": "build failed",
                        "session": "origin",
                        "caller_session": "cron:abc12345",
                    },
                )
                assert resp.status == 200
                data = await resp.json()
                assert data == {"ok": True, "channel": False, "session": True}
                # Hot-path: in-memory session found, no rehydrate needed.
                state.get_session.assert_called_once_with("chat-1-1712793600")
                mock_rehydrate.assert_not_called()
                # Injected as user message to trigger agent turn
                call_args = mock_session.append.call_args
                assert call_args[0][0] == "inject"
                assert '[Cron notification from "check pipeline"]' in call_args[0][1]
                assert "build failed" in call_args[0][1]
                assert json.loads(call_args[0][2]) == {"cronLabel": "check pipeline"}
                mock_run.assert_called_once()
                # Should NOT fall back to notify/Slack
                state.notify.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_message_session_origin_queued(self):
        """Queues the message when the target session is already running."""
        state = _mock_state()
        mock_session = MagicMock()
        mock_session.running = True
        mock_session._queue = []
        mock_session.queue_append = lambda content: (
            mock_session._queue.append({"id": "test", "content": content}) or "test"
        )
        state.get_session = MagicMock(return_value=mock_session)
        mock_job = MagicMock()
        mock_job.id = "abc12345"
        mock_job.name = "monitor build"
        mock_job.session_key = "dashboard:chat-1-1712793600"
        state.crons.list_jobs = MagicMock(return_value=[mock_job])
        app = _make_send_app(state)
        with patch(
            "personalclaw.dashboard.handlers.messaging._rehydrate_session_from_history"
        ) as mock_rehydrate:
            async with TestClient(TestServer(app)) as client:
                resp = await client.post(
                    "/api/send-message",
                    json={
                        "text": "build failed",
                        "session": "origin",
                        "caller_session": "cron:abc12345",
                    },
                )
                assert resp.status == 200
                data = await resp.json()
                assert data["session"] is True
                # Message queued, not triggering a new turn
                assert len(mock_session._queue) == 1
                assert "build failed" in mock_session._queue[0]["content"]
                call_args = mock_session.append.call_args
                assert call_args[0][0] == "queued"
                # Hot-path: no rehydrate when session is in memory.
                mock_rehydrate.assert_not_called()
                state.notify.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_message_session_origin_revives_missing_session(self):
        """When session isn't in memory (e.g. after gateway restart), rehydrate via
        _rehydrate_session_from_history and still trigger an agent turn on the revived
        session. Regression test for silent-fail bug where cron→origin injection fell
        back to owner DM after gateway restart.

        Mirrors the coverage of test_send_message_session_origin (happy path) —
        patches _run_chat, asserts it was invoked on the revived session, and verifies
        the injected message content matches the cron-notification contract.

        This is a focused routing test: it mocks _rehydrate_session_from_history so
        we can assert the handler calls it exactly when get_session returns None. The
        end-to-end rehydrate path (real ConversationLog, real DashboardState,
        real _ChatSession creation) is covered by
        TestRehydrateSessionFromHistory in test_session_restore.py."""
        state = _mock_state()
        # Simulate cold-start: session not loaded in memory yet.
        state.get_session = MagicMock(return_value=None)
        # Rehydrate helper returns a session reconstructed from persisted history.
        mock_session = MagicMock()
        mock_session.running = False
        mock_session.task = None
        mock_session.key = "chat-1-1712793600"
        state._background_tasks = set()
        state.push_sessions_update = MagicMock()
        mock_job = MagicMock()
        mock_job.id = "abc12345"
        mock_job.name = "test-cron"
        mock_job.session_key = "dashboard:chat-1-1712793600"
        state.crons.list_jobs = MagicMock(return_value=[mock_job])
        app = _make_send_app(state)
        with (
            patch(
                "personalclaw.dashboard.chat_runner._run_chat", new_callable=AsyncMock
            ) as mock_run,
            patch(
                "personalclaw.dashboard.handlers.messaging._rehydrate_session_from_history",
                return_value=mock_session,
            ) as mock_rehydrate,
        ):
            async with TestClient(TestServer(app)) as client:
                resp = await client.post(
                    "/api/send-message",
                    json={"text": "update", "session": "origin", "caller_session": "cron:abc12345"},
                )
                assert resp.status == 200
                data = await resp.json()
                # Session delivery succeeded — no Slack DM fallback.
                assert data == {"ok": True, "channel": False, "session": True}
                # Hot-path miss: get_session called first, then rehydrate helper.
                state.get_session.assert_called_once_with("chat-1-1712793600")
                mock_rehydrate.assert_called_once_with(state, "chat-1-1712793600")
                # Agent turn was triggered on the revived session (the whole point of the fix).
                mock_run.assert_called_once()
                # Injected as user message with cron-notification contract.
                call_args = mock_session.append.call_args
                assert call_args[0][0] == "inject"
                assert '[Cron notification from "test-cron"]' in call_args[0][1]
                assert "update" in call_args[0][1]
                # Message was injected, not sent as a notification.
                state.notify.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_message_session_origin_rehydrate_returns_none_falls_back(self):
        """When get_session returns None AND rehydrate returns None (no persisted
        session on disk), fall back to normal delivery (notification + optional
        Slack DM). Prevents phantom-session creation when the origin session was
        never persisted or was explicitly closed."""
        state = _mock_state()
        state.get_session = MagicMock(return_value=None)
        mock_job = MagicMock()
        mock_job.id = "abc12345"
        mock_job.name = "test-cron"
        mock_job.session_key = "dashboard:chat-1-1712793600"
        state.crons.list_jobs = MagicMock(return_value=[mock_job])
        app = _make_send_app(state)
        with patch(
            "personalclaw.dashboard.handlers.messaging._rehydrate_session_from_history",
            return_value=None,
        ) as mock_rehydrate:
            async with TestClient(TestServer(app)) as client:
                resp = await client.post(
                    "/api/send-message",
                    json={"text": "update", "session": "origin", "caller_session": "cron:abc12345"},
                )
                assert resp.status == 200
                data = await resp.json()
                # No session delivery — fell through to notification.
                assert data["session"] is False
                mock_rehydrate.assert_called_once_with(state, "chat-1-1712793600")
                state.notify.assert_called_once()
                call_args = state.notify.call_args[0]
                assert call_args[1] == "⏰ test-cron"
                assert "session closed" in call_args[2]

    @pytest.mark.asyncio
    async def test_send_message_session_origin_no_cron(self):
        """Falls back when caller is not a cron session."""
        state = _mock_state()
        app = _make_send_app(state)
        async with TestClient(TestServer(app)) as client:
            resp = await client.post(
                "/api/send-message",
                json={"text": "update", "session": "origin", "caller_session": "dashboard:chat-1"},
            )
            assert resp.status == 200
            data = await resp.json()
            assert data["session"] is False
            state.notify.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_message_session_rejects_arbitrary_key(self):
        """Arbitrary session keys are rejected — only 'origin' and 'channel' are valid."""
        state = _mock_state()
        state.get_session = MagicMock()
        app = _make_send_app(state)
        with patch(
            "personalclaw.dashboard.handlers.messaging._rehydrate_session_from_history"
        ) as mock_rehydrate:
            async with TestClient(TestServer(app)) as client:
                resp = await client.post(
                    "/api/send-message",
                    json={
                        "text": "update",
                        "session": "chat-1-1712793600",
                        "caller_session": "cron:abc",
                    },
                )
                assert resp.status == 200
                data = await resp.json()
                assert data["session"] is False
                # Should NOT attempt any session lookup or rehydrate for a non-"origin" key.
                state.get_session.assert_not_called()
                mock_rehydrate.assert_not_called()
                state.notify.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_message_session_channel_bypasses_origin(self):
        """session='channel' is the explicit opt-out: skip origin routing
        entirely and fall through to the channel-delivery path (+ dashboard
        notification). Even if the cron has a valid originating session that
        would normally receive the injection, session='channel' routes to the
        owner's messaging channel instead."""
        state = _mock_state()
        mock_session = MagicMock()
        mock_session.running = False
        state.get_session = MagicMock(return_value=mock_session)
        # Cron has an origin that WOULD be resolvable — proves session='channel'
        # suppresses resolution regardless.
        mock_job = MagicMock()
        mock_job.id = "abc12345"
        mock_job.name = "notify-channel-cron"
        mock_job.session_key = "dashboard:chat-1-1712793600"
        state.crons.list_jobs = MagicMock(return_value=[mock_job])
        app = _make_send_app(state)
        with patch(
            "personalclaw.dashboard.handlers.messaging._rehydrate_session_from_history"
        ) as mock_rehydrate:
            async with TestClient(TestServer(app)) as client:
                resp = await client.post(
                    "/api/send-message",
                    json={
                        "text": "heads up",
                        "session": "channel",
                        "caller_session": "cron:abc12345",
                    },
                )
                assert resp.status == 200
                data = await resp.json()
                # No origin injection despite a valid origin being available.
                assert data["session"] is False
                # Rehydrate and origin get_session path never engage for session='channel'.
                state.get_session.assert_not_called()
                mock_rehydrate.assert_not_called()
                # Dashboard notification always fires (contract invariant).
                state.notify.assert_called_once()


class TestSanitizeBlocks:
    def test_redacts_strings_in_nested_blocks(self):
        """All string values in blocks are passed through redactors."""

        def mock_redactor(s):
            return s.replace("SECRET", "[REDACTED]"), [s] if "SECRET" in s else []

        blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "has SECRET here"}}]
        result = _sanitize_blocks(blocks, mock_redactor)
        assert result[0]["text"]["text"] == "has [REDACTED] here"
        # Original not mutated
        assert blocks[0]["text"]["text"] == "has SECRET here"

    def test_truncates_to_50_blocks(self):
        blocks = [{"type": "divider"} for _ in range(100)]
        result = _sanitize_blocks(blocks, lambda s: (s, []))
        assert len(result) == 50

    def test_depth_limit(self):
        """Beyond _MAX_WALK_DEPTH: strings are still sanitized, containers are dropped."""
        # Build a 20-level deep nested dict (exceeds _MAX_WALK_DEPTH=10)
        obj: dict = {"text": "deep_leaf"}
        for _ in range(20):
            obj = {"nested": obj}
        blocks = [obj]
        # Use a targeted redactor that only modifies values containing "deep"
        # so structural keys pass through unchanged
        result = _sanitize_blocks(blocks, lambda s: (s.replace("deep", "DEEP"), []))
        # Should not raise
        assert isinstance(result, list)
        # Walk to depth boundary — containers beyond limit are dropped to {}
        node = result[0]
        for i in range(20):
            if "nested" not in node:
                break
            node = node["nested"]
        assert (
            "text" not in node
        ), f"deep leaf should have been truncated but was reached at depth {i}"
        # A shallow value SHOULD be sanitized
        shallow = [{"text": "deep_value"}]
        result2 = _sanitize_blocks(shallow, lambda s: (s.replace("deep", "DEEP"), []))
        assert result2[0]["text"] == "DEEP_value"


class TestFileMove:
    @pytest.mark.asyncio
    async def test_rename_success(self, mock_sel, home_patch):
        src = home_patch / "a.txt"
        src.write_text("x")
        async with TestClient(TestServer(_make_app())) as client:
            r = await client.post(
                "/api/file-move", json={"src": str(src), "dest": str(home_patch / "b.txt")}
            )
            assert r.status == 200
        assert not src.exists()
        assert (home_patch / "b.txt").read_text() == "x"

    @pytest.mark.asyncio
    async def test_move_refuses_existing_dest(self, mock_sel, home_patch):
        src = home_patch / "a.txt"
        src.write_text("x")
        dest = home_patch / "b.txt"
        dest.write_text("y")
        async with TestClient(TestServer(_make_app())) as client:
            r = await client.post("/api/file-move", json={"src": str(src), "dest": str(dest)})
            assert r.status == 409

    @pytest.mark.asyncio
    async def test_move_outside_allowlist_denied(self, mock_sel, home_patch):
        src = home_patch / "a.txt"
        src.write_text("x")
        async with TestClient(TestServer(_make_app())) as client:
            r = await client.post("/api/file-move", json={"src": str(src), "dest": "/etc/evil.txt"})
            assert r.status == 400
        assert src.exists()

    @pytest.mark.asyncio
    async def test_move_missing_source_404(self, mock_sel, home_patch):
        async with TestClient(TestServer(_make_app())) as client:
            r = await client.post(
                "/api/file-move",
                json={"src": str(home_patch / "nope.txt"), "dest": str(home_patch / "x.txt")},
            )
            assert r.status == 404


class TestFileDelete:
    @pytest.mark.asyncio
    async def test_delete_file(self, mock_sel, home_patch):
        f = home_patch / "a.txt"
        f.write_text("x")
        async with TestClient(TestServer(_make_app())) as client:
            r = await client.post("/api/file-delete", json={"path": str(f)})
            assert r.status == 200
        assert not f.exists()

    @pytest.mark.asyncio
    async def test_delete_dir_recursive(self, mock_sel, home_patch):
        d = home_patch / "sub"
        d.mkdir()
        (d / "x.txt").write_text("x")
        async with TestClient(TestServer(_make_app())) as client:
            r = await client.post("/api/file-delete", json={"path": str(d)})
            assert r.status == 200
        assert not d.exists()

    @pytest.mark.asyncio
    async def test_delete_outside_allowlist_denied(self, mock_sel, home_patch):
        async with TestClient(TestServer(_make_app())) as client:
            r = await client.post("/api/file-delete", json={"path": "/etc/passwd"})
            assert r.status == 400

    @pytest.mark.asyncio
    async def test_delete_not_found_404(self, mock_sel, home_patch):
        async with TestClient(TestServer(_make_app())) as client:
            r = await client.post("/api/file-delete", json={"path": str(home_patch / "nope.txt")})
            assert r.status == 404


class TestFileUpload:
    @pytest.mark.asyncio
    async def test_upload_success(self, mock_sel, home_patch):
        from aiohttp import FormData

        async with TestClient(TestServer(_make_app())) as client:
            fd = FormData()
            fd.add_field("file", b"payload", filename="up.txt", content_type="text/plain")
            r = await client.post(f"/api/file-upload?path={home_patch}", data=fd)
            assert r.status == 200
            data = await r.json()
            assert data["ok"] is True
        assert (home_patch / "up.txt").read_bytes() == b"payload"

    @pytest.mark.asyncio
    async def test_upload_forbidden_dir(self, mock_sel, home_patch):
        from aiohttp import FormData

        async with TestClient(TestServer(_make_app())) as client:
            fd = FormData()
            fd.add_field("file", b"x", filename="up.txt", content_type="text/plain")
            r = await client.post("/api/file-upload?path=/etc", data=fd)
            assert r.status == 400

    @pytest.mark.asyncio
    async def test_upload_refuses_existing(self, mock_sel, home_patch):
        from aiohttp import FormData

        (home_patch / "up.txt").write_text("old")
        async with TestClient(TestServer(_make_app())) as client:
            fd = FormData()
            fd.add_field("file", b"new", filename="up.txt", content_type="text/plain")
            r = await client.post(f"/api/file-upload?path={home_patch}", data=fd)
            assert r.status == 409
        assert (home_patch / "up.txt").read_text() == "old"


class TestDashboardRootsProjectWorkspace:
    """A bound Project.workspace_dir must be in the browse allowlist.

    projects-native-entity makes a Project a first-class entity that can bind an
    arbitrary codebase dir; its detail view peeks that workspace + offers Open-in-
    Files. Regression guard: ``_dashboard_roots`` must surface each bound project
    workspace as a root (mirroring the loop-workspace block), else browsing a
    project workspace not coincidentally shared by a Loop returns 400.
    """

    @pytest.fixture
    def _isolated_home(self, tmp_path, monkeypatch):
        import personalclaw.config.loader as cfg
        import personalclaw.tasks.hierarchy as hier

        monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
        monkeypatch.setattr(hier, "config_dir", lambda: tmp_path, raising=False)
        return tmp_path

    def test_bound_project_workspace_is_allowlisted(self, _isolated_home, tmp_path):
        from personalclaw.dashboard.handlers.files import (
            _dashboard_roots,
            _validate_dashboard_path,
        )
        from personalclaw.projects import _store

        ws = tmp_path / "codebase"
        ws.mkdir()
        (ws / "main.py").write_text("print('hi')\n")
        proj = _store().create_project(name="ZZ-Roots-Probe", workspace_dir=str(ws))

        roots = _dashboard_roots()
        assert any(
            label.startswith("Project: ZZ-Roots-Probe") for label, _ in roots
        ), "bound project workspace must be surfaced as a dashboard root"
        # The bound workspace dir AND files under it are admitted by the allowlist.
        assert _validate_dashboard_path(str(ws)) is not None
        assert _validate_dashboard_path(str(ws / "main.py")) is not None
        assert proj.workspace_dir == str(ws)

    def test_unbound_project_adds_no_root(self, _isolated_home, tmp_path):
        """A project with no bound workspace ("" ) contributes no extra root."""
        from personalclaw.dashboard.handlers.files import _dashboard_roots
        from personalclaw.projects import _store

        _store().create_project(name="ZZ-NoWs-Probe")  # no workspace_dir
        labels = [label for label, _ in _dashboard_roots()]
        assert not any(label.startswith("Project: ZZ-NoWs-Probe") for label in labels)

    def test_system_root_workspace_is_refused(self, _isolated_home, tmp_path):
        """A project (or loop) bound to a protected system root must NOT become a
        browsable root — else /etc, /usr, / etc. would leak via a workspace binding.
        Surfacing bound workspaces (the fix above) must not widen that surface."""
        from personalclaw.dashboard.handlers.files import (
            _dashboard_roots,
            _validate_dashboard_path,
        )
        from personalclaw.projects import _store

        _store().create_project(name="ZZ-Sys-Probe", workspace_dir="/etc")
        labels = [label for label, _ in _dashboard_roots()]
        assert not any(
            label.startswith("Project: ZZ-Sys-Probe") for label in labels
        ), "a system-root workspace must not be surfaced as a browsable root"
        # /etc (and files under it) stay blocked — not admitted via the binding.
        assert _validate_dashboard_path("/etc") is None
        assert _validate_dashboard_path("/etc/passwd") is None
