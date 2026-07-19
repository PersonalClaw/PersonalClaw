"""Tests for mcp_core session key routing."""

from unittest.mock import patch

from personalclaw.mcp_core import _call_tool


class TestSpawnRunSessionKeyRouting:
    # subagent_run lives in mcp_subagents now; its parent-session resolution still
    # uses _resolve_session_key (owned by mcp_core, imported there). The dispatch +
    # the _post it calls are mcp_subagents'.
    def test_uses_env_var_when_set(self):
        """PERSONALCLAW_SESSION_KEY env var is used as parent_session."""
        from personalclaw.mcp_subagents import _call_tool as _sa_call_tool

        with patch("personalclaw.mcp_subagents._post") as mock_post, patch.dict(
            "os.environ", {"PERSONALCLAW_SESSION_KEY": "sess-from-env"}
        ):
            mock_post.return_value = {"id": "agent1"}

            _sa_call_tool("subagent_run", {"task": "test"})

            call_body = mock_post.call_args[0][1]
            assert call_body["parent_session"] == "sess-from-env"

    def test_falls_back_to_pid_file(self, tmp_path):
        import os

        from personalclaw.mcp_subagents import _call_tool as _sa_call_tool

        with patch("personalclaw.mcp_subagents._post") as mock_post, patch(
            "pathlib.Path.home", return_value=tmp_path / "fake_home"
        ):
            env = os.environ.copy()
            env.pop("PERSONALCLAW_SESSION_KEY", None)
            with patch.dict("os.environ", env, clear=True):
                personalclaw_dir = tmp_path / "fake_home" / ".personalclaw"
                personalclaw_dir.mkdir(parents=True)
                (personalclaw_dir / f"session_pid_{os.getppid()}.txt").write_text("sess-from-pid")

                mock_post.return_value = {"id": "agent1"}
                _sa_call_tool("subagent_run", {"task": "test"})

                assert mock_post.call_args[0][1]["parent_session"] == "sess-from-pid"


class TestSendMessageUnfurlForwarding:
    def test_unfurl_params_forwarded_in_payload(self):
        """unfurl_links and unfurl_media are forwarded to /api/send-message."""
        with patch("personalclaw.mcp_core._post") as mock_post:
            mock_post.return_value = {"ok": True}

            _call_tool("notify", {
                "text": "test",
                "unfurl_links": False,
                "unfurl_media": False,
            })

            payload = mock_post.call_args[0][1]
            assert payload["unfurl_links"] is False
            assert payload["unfurl_media"] is False

    def test_unfurl_params_omitted_when_absent(self):
        """unfurl params are not in payload when not provided."""
        with patch("personalclaw.mcp_core._post") as mock_post:
            mock_post.return_value = {"ok": True}

            _call_tool("notify", {"text": "test"})

            payload = mock_post.call_args[0][1]
            assert "unfurl_links" not in payload
            assert "unfurl_media" not in payload


class TestSendMessageCronAutoOrigin:
    """Auto-default logic: when the caller is a cron job and the LLM didn't
    explicitly set session/channel/user, `send_message` auto-applies
    session="origin" so cron updates inject into the session that spawned
    them. Explicit channel/user/session values always win.
    """

    def test_auto_applies_origin_for_cron_caller(self):
        """Cron caller with bare send_message(text=...) → session=origin injected."""
        with patch("personalclaw.mcp_core._post") as mock_post, patch.dict(
            "os.environ", {"PERSONALCLAW_SESSION_KEY": "cron:abc123"}
        ):
            mock_post.return_value = {"ok": True}
            _call_tool("notify", {"text": "build passed"})

            payload = mock_post.call_args[0][1]
            assert payload.get("session") == "origin"

    def test_explicit_channel_suppresses_auto_default(self):
        """Cron caller with explicit channel=... → no session auto-default."""
        with patch("personalclaw.mcp_core._post") as mock_post, patch.dict(
            "os.environ", {"PERSONALCLAW_SESSION_KEY": "cron:abc123"}
        ):
            mock_post.return_value = {"ok": True}
            _call_tool("notify", {"text": "hi", "channel": "C12345"})

            payload = mock_post.call_args[0][1]
            assert "session" not in payload
            assert payload.get("channel") == "C12345"

    def test_explicit_user_suppresses_auto_default(self):
        """Cron caller with explicit user=... (intentional Slack DM) → no auto-default."""
        with patch("personalclaw.mcp_core._post") as mock_post, patch.dict(
            "os.environ", {"PERSONALCLAW_SESSION_KEY": "cron:abc123"}
        ):
            mock_post.return_value = {"ok": True}
            _call_tool("notify", {"text": "hi", "user": "U05J78ZGYNQ"})

            payload = mock_post.call_args[0][1]
            assert "session" not in payload
            assert payload.get("user") == "U05J78ZGYNQ"

    def test_non_cron_session_skips_auto_default(self):
        """Dashboard (non-cron) caller → no auto-default, sends to owner DM as before."""
        with patch("personalclaw.mcp_core._post") as mock_post, patch.dict(
            "os.environ", {"PERSONALCLAW_SESSION_KEY": "dashboard:chat-1"}
        ):
            mock_post.return_value = {"ok": True}
            _call_tool("notify", {"text": "hi"})

            payload = mock_post.call_args[0][1]
            assert "session" not in payload

    def test_missing_env_var_skips_auto_default(self):
        """Absent PERSONALCLAW_SESSION_KEY → no auto-default."""
        import os

        with patch("personalclaw.mcp_core._post") as mock_post:
            env = os.environ.copy()
            env.pop("PERSONALCLAW_SESSION_KEY", None)
            with patch.dict("os.environ", env, clear=True):
                mock_post.return_value = {"ok": True}
                _call_tool("notify", {"text": "hi"})

                payload = mock_post.call_args[0][1]
                assert "session" not in payload

    def test_explicit_session_origin_is_idempotent(self):
        """LLM explicitly passes session=origin from cron → still origin, no double-application."""
        with patch("personalclaw.mcp_core._post") as mock_post, patch.dict(
            "os.environ", {"PERSONALCLAW_SESSION_KEY": "cron:abc123"}
        ):
            mock_post.return_value = {"ok": True}
            _call_tool("notify", {"text": "hi", "session": "origin"})

            payload = mock_post.call_args[0][1]
            assert payload.get("session") == "origin"

    def test_explicit_session_channel_is_accepted(self):
        """session='channel' is a valid explicit opt-out value."""
        with patch("personalclaw.mcp_core._post") as mock_post, patch.dict(
            "os.environ", {"PERSONALCLAW_SESSION_KEY": "cron:abc123"}
        ):
            mock_post.return_value = {"ok": True}
            _call_tool("notify", {"text": "hi", "session": "channel"})

            payload = mock_post.call_args[0][1]
            assert payload.get("session") == "channel"

    def test_invalid_session_value_rejected(self):
        """session must match the ^(origin|channel)$ pattern; other values rejected."""
        with patch("personalclaw.mcp_core._post") as mock_post, patch.dict(
            "os.environ", {"PERSONALCLAW_SESSION_KEY": "cron:abc123"}
        ):
            result = _call_tool("notify", {"text": "hi", "session": "bogus"})
            # Validator-level rejection (pattern mismatch on FieldSpec), not
            # the handler's post-validation check. Either way, no network call.
            assert "session" in result.lower() or "error" in result.lower()
            mock_post.assert_not_called()
