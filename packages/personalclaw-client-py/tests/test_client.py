"""Tests for personalclaw_client — mirrors TS @personalclaw/client test coverage."""

from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from personalclaw_client import PersonalClawClient, PersonalClawError
from personalclaw_client.errors import ErrorCode, http_error, http_status_to_code


class TestErrors:
    def test_5xx_maps_to_server_error(self):
        for status in (500, 502, 503):
            assert http_status_to_code(status) == ErrorCode.SERVER_ERROR

    def test_429_maps_to_rate_limited(self):
        assert http_status_to_code(429) == ErrorCode.RATE_LIMITED

    def test_404_maps_to_not_found(self):
        assert http_status_to_code(404) == ErrorCode.NOT_FOUND

    def test_401_maps_to_auth_expired(self):
        assert http_status_to_code(401) == ErrorCode.AUTH_EXPIRED

    def test_4xx_non_retryable(self):
        for status in (400, 403, 404, 405, 422):
            code = http_status_to_code(status)
            assert code not in (ErrorCode.SERVER_ERROR, ErrorCode.RATE_LIMITED)

    def test_http_error_has_code_and_message(self):
        err = http_error(500, "internal error")
        assert err.code == ErrorCode.SERVER_ERROR
        assert "internal error" in str(err)
        assert err.status == 500

    def test_the_gateways_error_envelope_gives_its_sentence(self):
        """``{"error": {"code", "message"}}`` is read for its message, not shown as a dict."""
        sentence = "A lesson takes no category, since no lesson stores one."
        err = http_error(400, {"error": {"code": "bad_request", "message": sentence}})
        assert (err.code, str(err), err.status) == (ErrorCode.VALIDATION_ERROR, sentence, 400)
        assert str(http_error(403, {"error": "Not given your memory."})) == "Not given your memory."

    def test_to_dict(self):
        err = PersonalClawError(ErrorCode.NOT_FOUND, "not found", status=404)
        d = err.to_dict()
        assert d["code"] == "NOT_FOUND"
        assert d["message"] == "not found"
        assert d["status"] == 404


class TestAuthCheck:
    def test_localhost_no_token_ok(self):
        pc = PersonalClawClient(base_url="http://localhost:7777")
        # Should not raise on construction
        assert pc.base_url == "http://localhost:7777"

    def test_remote_no_token_raises(self):
        pc = PersonalClawClient(base_url="http://example.com:7777")
        with pytest.raises(PersonalClawError) as exc_info:
            pc._check_auth()
        assert exc_info.value.code == ErrorCode.AUTH_REQUIRED

    def test_remote_with_token_ok(self):
        pc = PersonalClawClient(base_url="http://example.com:7777", token="test")
        pc._check_auth()  # should not raise


class TestAuthCarrier:
    """The gateway takes a program's token from ``Authorization: Bearer``. It never reads a cookie
    named ``pc_token``, which is what the client sent, so it refused every call."""

    @pytest.mark.asyncio
    async def test_the_token_travels_as_a_bearer_header(self):
        seen: dict[str, str | None] = {}

        async def status(request: web.Request) -> web.Response:
            seen["authorization"] = request.headers.get("Authorization")
            seen["cookie"] = request.headers.get("Cookie")
            if request.headers.get("Authorization") != "Bearer t":
                return web.json_response({"error": "Token required"}, status=401)
            return web.json_response({"ok": True})

        app = web.Application()
        app.router.add_get("/api/status", status)
        async with TestServer(app, host="127.0.0.1") as server:
            async with PersonalClawClient(
                base_url=str(server.make_url("/")), token="t", max_retries=0
            ) as pc:
                assert await pc.get_status() == {"ok": True}
        assert seen == {"authorization": "Bearer t", "cookie": None}


class TestMessageValidation:
    @pytest.mark.asyncio
    async def test_rejects_long_message(self):
        pc = PersonalClawClient(message_length_limit=100)
        with pytest.raises(PersonalClawError) as exc_info:
            await pc.send_message("session-1", "x" * 101)
        assert exc_info.value.code == ErrorCode.VALIDATION_ERROR

    @pytest.mark.asyncio
    async def test_rejects_long_notification(self):
        pc = PersonalClawClient(message_length_limit=100)
        with pytest.raises(PersonalClawError) as exc_info:
            await pc.send_notification("x" * 101)
        assert exc_info.value.code == ErrorCode.VALIDATION_ERROR


class TestMcpValidation:
    @pytest.mark.asyncio
    async def test_rejects_empty_name(self):
        pc = PersonalClawClient()
        with pytest.raises(PersonalClawError) as exc_info:
            await pc.register_mcp_server("", "node server.js")
        assert exc_info.value.code == ErrorCode.VALIDATION_ERROR

    @pytest.mark.asyncio
    async def test_rejects_empty_command(self):
        pc = PersonalClawClient()
        with pytest.raises(PersonalClawError) as exc_info:
            await pc.register_mcp_server("my-server", "")
        assert exc_info.value.code == ErrorCode.VALIDATION_ERROR


class TestMcpRegisterNamesWhatItReplaces:
    """The Gateway replaces a configured server's definition only over the revision it was read at
    (``If-Match``), so registering over an existing name reads it first; a new name needs none."""

    @staticmethod
    def _gateway(pc, existing):
        calls = []

        async def fake_request(method, path, body=None, headers=None):
            calls.append((method, path, body, headers or {}))
            if method == "GET":
                if existing is None:
                    raise http_error(404, "not found")
                return existing
            return {"ok": True}

        pc._request = fake_request
        return calls

    @pytest.mark.asyncio
    async def test_a_new_name_is_added_with_no_base(self):
        pc = PersonalClawClient()
        calls = self._gateway(pc, existing=None)
        await pc.register_mcp_server("files", "npx", ["server-files"])
        assert calls[-1] == (
            "PUT",
            "/api/mcp/servers/files",
            {"command": "npx", "args": ["server-files"]},
            {},
        )

    @pytest.mark.asyncio
    async def test_an_existing_name_is_replaced_over_the_revision_just_read(self):
        pc = PersonalClawClient()
        calls = self._gateway(pc, existing={"name": "files", "editable": True, "revision": "r7"})
        await pc.register_mcp_server("files", "npx")
        assert [c[0] for c in calls] == ["GET", "PUT"]
        assert calls[-1][3] == {"If-Match": '"r7"'}

    @pytest.mark.asyncio
    async def test_a_read_that_fails_otherwise_writes_nothing(self):
        pc = PersonalClawClient()
        calls = []

        async def fake_request(method, path, body=None, headers=None):
            calls.append(method)
            raise http_error(500, "boom")

        pc._request = fake_request
        with pytest.raises(PersonalClawError):
            await pc.register_mcp_server("files", "npx")
        assert calls == ["GET"]


class TestContextBuffer:
    @pytest.mark.asyncio
    async def test_null_session_buffers_locally(self):
        pc = PersonalClawClient()
        await pc.inject_context(None, "test content")
        assert pc.pending_context_count == 1

    @pytest.mark.asyncio
    async def test_buffer_limit_fifo(self):
        pc = PersonalClawClient()
        for i in range(60):
            await pc.inject_context(None, f"entry-{i}")
        assert pc.pending_context_count == 50

    @pytest.mark.asyncio
    async def test_flush_preserves_failed_entries(self):
        pc = PersonalClawClient(max_retries=0)
        await pc.inject_context(None, "a")
        await pc.inject_context(None, "b")
        assert pc.pending_context_count == 2
        with pytest.raises(Exception):
            await pc.flush_pending_context("session-1")
        # Failed entries preserved for retry
        assert pc.pending_context_count == 2


class TestAppDataDir:
    def test_contains_app_name(self):
        pc = PersonalClawClient(app_name="my-tool")
        d = pc.get_app_data_dir()
        assert "my-tool" in str(d)
        assert ".personalclaw" in str(d)
        assert "apps" in str(d)


class TestLessons:
    """A lesson has no category: the gateway refuses one sent with it, and ``add_lesson`` sent
    one on every call. ``add_lesson``'s other arguments are named, so an old call that passes a
    category second is refused rather than sent as the scope; a ``workspace`` lesson names its
    workspace. ``GET /api/lessons`` lists them under ``lessons``, and ``GET /api/spawn`` the
    subagents under ``agents``, which ``list_lessons`` and ``list_subagents`` read as none."""

    @staticmethod
    def _gateway(saved: list[dict]) -> web.Application:
        async def create(request: web.Request) -> web.Response:
            body = await request.json()
            if "category" in body:
                return web.json_response(
                    {"error": {"code": "bad_request", "message": "A lesson takes no category."}},
                    status=400,
                )
            saved.append(body)
            return web.json_response({"ok": True})

        async def listed(request: web.Request) -> web.Response:
            return web.json_response({"lessons": [{"rule": b["rule"]} for b in saved]})

        async def agents(request: web.Request) -> web.Response:
            return web.json_response({"agents": [{"id": "agent-9", "done": False}]})

        app = web.Application()
        app.router.add_post("/api/lessons", create)
        app.router.add_get("/api/lessons", listed)
        app.router.add_get("/api/spawn", agents)
        return app

    @pytest.mark.asyncio
    async def test_a_lesson_is_saved_without_a_category_and_listed(self):
        saved: list[dict] = []
        async with TestServer(self._gateway(saved), host="127.0.0.1") as server:
            async with PersonalClawClient(
                base_url=str(server.make_url("/")), token="t", max_retries=0
            ) as pc:
                await pc.add_lesson("Answer in French.")
                assert await pc.list_lessons() == [{"rule": "Answer in French."}]
        assert saved == [{"rule": "Answer in French.", "scope": ""}]

    @pytest.mark.asyncio
    async def test_a_category_passed_second_as_before_is_refused_and_nothing_is_sent(self):
        saved: list[dict] = []
        async with TestServer(self._gateway(saved), host="127.0.0.1") as server:
            async with PersonalClawClient(
                base_url=str(server.make_url("/")), token="t", max_retries=0
            ) as pc:
                with pytest.raises(TypeError):
                    await pc.add_lesson("Answer in French.", "preference")  # type: ignore[misc]
        assert saved == []

    @pytest.mark.asyncio
    async def test_a_workspace_lesson_names_its_workspace(self):
        saved: list[dict] = []
        async with TestServer(self._gateway(saved), host="127.0.0.1") as server:
            async with PersonalClawClient(
                base_url=str(server.make_url("/")), token="t", max_retries=0
            ) as pc:
                await pc.add_lesson("Run make lint first.", scope="workspace", workspace="/w/app")
        assert saved == [
            {"rule": "Run make lint first.", "scope": "workspace", "workspace": "/w/app"}
        ]

    @pytest.mark.asyncio
    async def test_the_subagents_are_listed_as_the_gateway_lists_them(self):
        async with TestServer(self._gateway([]), host="127.0.0.1") as server:
            async with PersonalClawClient(
                base_url=str(server.make_url("/")), token="t", max_retries=0
            ) as pc:
                assert await pc.list_subagents() == [{"id": "agent-9", "done": False}]
