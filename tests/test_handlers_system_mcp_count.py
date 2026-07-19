"""Tests for MCP process counting in handlers_system.py (Linux + macOS)."""

import os
from pathlib import Path
from unittest.mock import patch


def _run_collect() -> dict:
    """Run _collect_system_metrics and return the result dict.

    Mocks _get_static_system_info to avoid subprocess calls for static info.
    The remaining system metrics (memory, CPU, network) are all wrapped in
    try/except so they degrade gracefully — only the MCP block matters here.
    """
    from personalclaw.dashboard import handlers_system

    with patch.object(handlers_system, "_get_static_system_info", return_value={}):
        # Reset cache so each test gets a fresh call
        handlers_system._metrics_cache.clear()
        handlers_system._metrics_cache_ts = 0.0
        return handlers_system._collect_system_metrics()


class TestMcpProcessCountLinux:
    """Linux path: scans /proc/*/cmdline."""

    def test_counts_all_signatures(self) -> None:
        # agent_cli is detected via the external ACP agent CLIs ("claude" /
        # "acp-agent") — the native agent runs in-process, and external agents
        # are wrapped as acp:<cli> bundles, so those binaries are the signature.
        fake_procs = {
            "10": b"python3\x00/tmp/personalclaw_sandbox_abc.py",
            "20": b"claude\x00acp\x00--agent\x00personalclaw",
            "30": b"node\x00my-mcp-server\x00serve",
            "40": b"postgres\x00-D\x00/var/lib/pg",
        }
        orig_listdir = os.listdir

        def fake_listdir(path: str) -> list[str]:
            if path == "/proc":
                return list(fake_procs.keys()) + ["self", "99999"]
            return orig_listdir(path)

        orig_read_bytes = Path.read_bytes

        def fake_read_bytes(self_path: Path) -> bytes:
            parts = str(self_path).split("/")
            if len(parts) >= 4 and parts[1] == "proc" and parts[3] == "cmdline":
                pid = parts[2]
                if pid in fake_procs:
                    return fake_procs[pid]
            return orig_read_bytes(self_path)

        with (
            patch("personalclaw.dashboard.handlers_system.sys") as mock_sys,
            patch("personalclaw.dashboard.handlers_system.os.getpid", return_value=99999),
            patch("personalclaw.dashboard.handlers_system.os.listdir", side_effect=fake_listdir),
            patch.object(Path, "read_bytes", fake_read_bytes),
        ):
            mock_sys.platform = "linux"
            data = _run_collect()

        assert data["mcp_processes"]["sandbox"] == 1
        assert data["mcp_processes"]["agent_cli"] == 1
        assert data["mcp_processes"]["mcp_server"] == 1
        assert data["mcp_total"] == 3

    def test_excludes_self(self) -> None:
        orig_listdir = os.listdir

        def fake_listdir(path: str) -> list[str]:
            if path == "/proc":
                return ["10"]
            return orig_listdir(path)

        def fake_read_bytes(self_path: Path) -> bytes:
            return b"personalclaw-cli\x00acp"

        with (
            patch("personalclaw.dashboard.handlers_system.sys") as mock_sys,
            patch("personalclaw.dashboard.handlers_system.os.getpid", return_value=10),
            patch("personalclaw.dashboard.handlers_system.os.listdir", side_effect=fake_listdir),
            patch.object(Path, "read_bytes", fake_read_bytes),
        ):
            mock_sys.platform = "linux"
            data = _run_collect()

        assert data["mcp_total"] == 0


class TestMcpProcessCountMacOS:
    """macOS path: uses ps -eo pid,command."""

    def test_counts_all_signatures(self) -> None:
        # macOS scans `ps -eo pid,command` for two signatures only:
        # "personalclaw_sandbox" and "mcp-server".  agent_cli is NOT tracked on
        # macOS — sandbox-exec replaces the process image so the launcher's
        # cmdline is lost (see handlers_system comment), and the macOS _sigs map
        # intentionally omits an agent_cli signature.
        ps_output = (
            "  PID COMMAND\n"
            "   10 python3 /tmp/personalclaw_sandbox_abc.py\n"
            "   20 claude acp --agent personalclaw\n"
            "   30 node my-mcp-server serve\n"
            "   40 postgres -D /var/lib/pg\n"
        )

        with (
            patch("personalclaw.dashboard.handlers_system.sys") as mock_sys,
            patch("personalclaw.dashboard.handlers_system.os.getpid", return_value=99999),
            patch(
                "personalclaw.dashboard.handlers_system.subprocess.check_output",
                return_value=ps_output,
            ),
        ):
            mock_sys.platform = "darwin"
            data = _run_collect()

        assert data["mcp_processes"]["sandbox"] == 1
        assert data["mcp_processes"]["agent_cli"] == 0
        assert data["mcp_processes"]["mcp_server"] == 1
        assert data["mcp_total"] == 2

    def test_excludes_self(self) -> None:
        ps_output = "  PID COMMAND\n   42 personalclaw-cli acp\n"

        with (
            patch("personalclaw.dashboard.handlers_system.sys") as mock_sys,
            patch("personalclaw.dashboard.handlers_system.os.getpid", return_value=42),
            patch(
                "personalclaw.dashboard.handlers_system.subprocess.check_output",
                return_value=ps_output,
            ),
        ):
            mock_sys.platform = "darwin"
            data = _run_collect()

        assert data["mcp_total"] == 0

    def test_ps_failure_returns_zeros(self) -> None:
        with (
            patch("personalclaw.dashboard.handlers_system.sys") as mock_sys,
            patch("personalclaw.dashboard.handlers_system.os.getpid", return_value=1),
            patch(
                "personalclaw.dashboard.handlers_system.subprocess.check_output",
                side_effect=OSError("ps not found"),
            ),
        ):
            mock_sys.platform = "darwin"
            data = _run_collect()

        assert data["mcp_processes"] == {"sandbox": 0, "agent_cli": 0, "mcp_server": 0}
        assert data["mcp_total"] == 0
