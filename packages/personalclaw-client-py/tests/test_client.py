"""Tests for personalclaw_client — mirrors TS @personalclaw/client test coverage."""
from __future__ import annotations

import pytest

from personalclaw_client import PersonalClawClient, PersonalClawError
from personalclaw_client.errors import ErrorCode, http_status_to_code, http_error


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
