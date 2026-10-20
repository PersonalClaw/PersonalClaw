"""Tests for _resolve_session_key ancestor PID walk."""

import os
from unittest.mock import patch

from personalclaw import mcp_core
from personalclaw.mcp_core import _get_ppid, _resolve_session_key


class TestResolveSessionKey:
    def test_env_var_takes_priority(self):
        """Env var is returned immediately without file I/O."""
        with patch.dict("os.environ", {"PERSONALCLAW_SESSION_KEY": "dashboard:chat-1-123"}):
            assert _resolve_session_key() == "dashboard:chat-1-123"

    def test_contextvar_used_when_no_env(self, tmp_path):
        """In-process native runtime binds the session key via contextvar; it is
        consulted after the env var and before the PID-file walk. This is what
        lets a subagent spawned by a native worker resolve THIS session as parent
        (and inherit its trust) instead of a stale gateway PID file."""
        env = {k: v for k, v in os.environ.items() if k != "PERSONALCLAW_SESSION_KEY"}
        token = mcp_core.set_current_session_key("dashboard:loop-abc123")
        try:
            with (
                patch.dict("os.environ", env, clear=True),
                patch("personalclaw.mcp_core.config_dir", return_value=tmp_path),
            ):
                assert _resolve_session_key() == "dashboard:loop-abc123"
        finally:
            mcp_core.reset_current_session_key(token)

    def test_env_var_beats_contextvar(self):
        """Env var (subprocess MCP) still wins over the in-process contextvar."""
        token = mcp_core.set_current_session_key("dashboard:loop-ctx")
        try:
            with patch.dict("os.environ", {"PERSONALCLAW_SESSION_KEY": "dashboard:env-wins"}):
                assert _resolve_session_key() == "dashboard:env-wins"
        finally:
            mcp_core.reset_current_session_key(token)

    def test_contextvar_reset_clears_binding(self, tmp_path):
        """After reset the binding is gone (no leak across turns/sessions)."""
        env = {k: v for k, v in os.environ.items() if k != "PERSONALCLAW_SESSION_KEY"}
        token = mcp_core.set_current_session_key("dashboard:loop-leak")
        mcp_core.reset_current_session_key(token)
        with (
            patch.dict("os.environ", env, clear=True),
            patch("personalclaw.mcp_core.config_dir", return_value=tmp_path),
            patch("os.getppid", return_value=1),
        ):
            assert _resolve_session_key() == ""

    def test_pid_file_immediate_parent(self, tmp_path):
        """Finds PID file on immediate parent (cold-start case)."""
        ppid = os.getppid()
        (tmp_path / f"session_pid_{ppid}.txt").write_text("dashboard:chat-2-456")

        env = {k: v for k, v in os.environ.items() if k != "PERSONALCLAW_SESSION_KEY"}
        with (
            patch.dict("os.environ", env, clear=True),
            patch("personalclaw.mcp_core.config_dir", return_value=tmp_path),
        ):
            assert _resolve_session_key() == "dashboard:chat-2-456"

    def test_ancestor_walk_finds_grandparent(self, tmp_path):
        """Walks up ancestors when immediate parent has no PID file."""
        (tmp_path / "session_pid_25.txt").write_text("dashboard:chat-3-789")

        # Mock: PID 100 -> parent 50 -> parent 25 (has file)
        def fake_get_ppid(pid):
            return {100: 50, 50: 25}.get(pid, 0)

        env = {k: v for k, v in os.environ.items() if k != "PERSONALCLAW_SESSION_KEY"}
        with (
            patch.dict("os.environ", env, clear=True),
            patch("personalclaw.mcp_core.config_dir", return_value=tmp_path),
            patch("os.getppid", return_value=100),
            patch("personalclaw.mcp_core._get_ppid", side_effect=fake_get_ppid),
        ):
            assert _resolve_session_key() == "dashboard:chat-3-789"

    def test_returns_empty_when_no_file_found(self, tmp_path):
        """Returns empty string when ancestor chain reaches init."""

        def fake_get_ppid(pid):
            return {100: 1}.get(pid, 0)

        env = {k: v for k, v in os.environ.items() if k != "PERSONALCLAW_SESSION_KEY"}
        with (
            patch.dict("os.environ", env, clear=True),
            patch("personalclaw.mcp_core.config_dir", return_value=tmp_path),
            patch("os.getppid", return_value=100),
            patch("personalclaw.mcp_core._get_ppid", side_effect=fake_get_ppid),
        ):
            assert _resolve_session_key() == ""

    def test_stops_on_ppid_failure(self, tmp_path):
        """Stops walking when _get_ppid returns 0 (failure)."""
        env = {k: v for k, v in os.environ.items() if k != "PERSONALCLAW_SESSION_KEY"}
        with (
            patch.dict("os.environ", env, clear=True),
            patch("personalclaw.mcp_core.config_dir", return_value=tmp_path),
            patch("os.getppid", return_value=99999),
            patch("personalclaw.mcp_core._get_ppid", return_value=0),
        ):
            assert _resolve_session_key() == ""

    def test_handles_cycle_detection(self, tmp_path):
        """Stops if PID chain forms a cycle (prevents infinite loop)."""

        def fake_get_ppid(pid):
            return {100: 50, 50: 100}.get(pid, 0)

        env = {k: v for k, v in os.environ.items() if k != "PERSONALCLAW_SESSION_KEY"}
        with (
            patch.dict("os.environ", env, clear=True),
            patch("personalclaw.mcp_core.config_dir", return_value=tmp_path),
            patch("os.getppid", return_value=100),
            patch("personalclaw.mcp_core._get_ppid", side_effect=fake_get_ppid),
        ):
            assert _resolve_session_key() == ""


class TestGetPpid:
    def test_linux_reads_proc(self):
        """On Linux, parses PPid from /proc status."""
        fake_content = "Name:\tpersonalclaw-cli\nPPid:\t42\nTgid:\t100\n"

        with (
            patch("platform.system", return_value="Linux"),
            patch("pathlib.Path.read_text", return_value=fake_content),
        ):
            assert _get_ppid(100) == 42

    def test_linux_returns_0_on_missing_proc(self):
        """On Linux, returns 0 when /proc file doesn't exist."""
        with (
            patch("platform.system", return_value="Linux"),
            patch("pathlib.Path.read_text", side_effect=FileNotFoundError),
        ):
            assert _get_ppid(99999) == 0

    def test_linux_returns_0_on_malformed(self):
        """On Linux, returns 0 when PPid line is missing."""
        with (
            patch("platform.system", return_value="Linux"),
            patch("pathlib.Path.read_text", return_value="Name:\ttest\nTgid:\t1\n"),
        ):
            assert _get_ppid(100) == 0

    def test_macos_uses_ps(self):
        """On non-Linux, calls ps to get ppid."""
        with (
            patch("platform.system", return_value="Darwin"),
            patch("subprocess.check_output", return_value="  42\n"),
        ):
            assert _get_ppid(100) == 42

    def test_macos_returns_0_on_ps_failure(self):
        """On non-Linux, returns 0 when ps fails."""
        with (
            patch("platform.system", return_value="Darwin"),
            patch("subprocess.check_output", side_effect=Exception("no such process")),
        ):
            assert _get_ppid(99999) == 0
