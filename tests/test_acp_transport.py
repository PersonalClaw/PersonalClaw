"""Tests for AcpProcess (acp/transport.py) — the shared ACP subprocess + stdio
transport. Driven against a fake process (no real spawn) except where a helper is
pure. Complements test_acp_client.py, which exercises the transport through the
client's delegating methods."""

from __future__ import annotations

from collections import deque
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw.acp.errors import AcpError, AcpProcessDied
from personalclaw.acp.transport import AcpProcess


def _mk(**kw) -> AcpProcess:
    return AcpProcess(
        command=kw.pop("command", ["/bin/echo"]), work_dir=kw.pop("work_dir", "/tmp"), **kw
    )


async def _spawned_env(transport: AcpProcess) -> dict:
    """Spawn *transport* against a mocked subprocess and return the env dict it passed
    to ``create_subprocess_exec`` (the env-building the transport owns)."""
    with (
        patch("personalclaw.acp.transport.wrap_argv", return_value=(["/bin/echo"], None)),
        patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec,
        patch("personalclaw.session._track_pid"),
        patch("personalclaw.session._track_session_pid"),
    ):
        proc = MagicMock()
        proc.pid = 12345
        proc.returncode = None
        # Give stderr an awaitable readline that immediately signals EOF so the
        # background _drain_stderr task exits cleanly (no 'MagicMock not awaitable').
        proc.stderr = MagicMock()
        proc.stderr.readline = AsyncMock(return_value=b"")
        mock_exec.return_value = proc
        await transport.spawn()
        call = mock_exec.call_args
        return call.kwargs.get("env") or call[1].get("env")


@pytest.mark.asyncio
async def test_spawn_env_has_session_key_and_channel(tmp_path):
    # env-building moved from AcpClient to the transport (P9#7). The subprocess env
    # must carry PERSONALCLAW_SESSION_KEY + PERSONALCLAW_CHANNEL_ID when set.
    t = _mk(work_dir=tmp_path, session_key="test-key", channel_id="C0ABC123")
    env = await _spawned_env(t)
    assert env is not None
    assert env["PERSONALCLAW_SESSION_KEY"] == "test-key"
    assert env["PERSONALCLAW_CHANNEL_ID"] == "C0ABC123"


@pytest.mark.asyncio
async def test_spawn_env_omits_channel_when_absent(tmp_path):
    t = _mk(work_dir=tmp_path, session_key="k", channel_id=None)
    env = await _spawned_env(t)
    assert env is not None
    assert env["PERSONALCLAW_SESSION_KEY"] == "k"
    assert "PERSONALCLAW_CHANNEL_ID" not in env


@pytest.mark.asyncio
async def test_spawn_env_omits_session_key_when_absent(tmp_path):
    import os

    clean = {k: v for k, v in os.environ.items() if k != "PERSONALCLAW_SESSION_KEY"}
    with patch.dict(os.environ, clean, clear=True):
        t = _mk(work_dir=tmp_path, session_key=None, channel_id="C0ABC123")
        env = await _spawned_env(t)
    assert env is not None
    assert env["PERSONALCLAW_CHANNEL_ID"] == "C0ABC123"
    assert "PERSONALCLAW_SESSION_KEY" not in env


def test_liveness_before_spawn():
    t = _mk()
    assert t.is_alive() is False
    assert t.exit_code is None
    assert t.is_responsive() is False  # not alive → not responsive


def test_touch_refreshes_activity():
    t = _mk()
    before = t.last_activity
    t.touch()
    assert t.last_activity >= before


@pytest.mark.asyncio
async def test_write_requires_running_process():
    t = _mk()
    with pytest.raises(AcpError, match="not running"):
        await t.write("{}\n")


@pytest.mark.asyncio
async def test_write_broken_pipe_raises_process_died():
    t = _mk()
    proc = MagicMock()
    proc.returncode = None
    proc.stdin = MagicMock()
    proc.stdin.write = MagicMock(side_effect=BrokenPipeError("gone"))
    proc.stdin.drain = AsyncMock()
    t._process = proc
    with pytest.raises(AcpProcessDied):
        await t.write("{}\n")


@pytest.mark.asyncio
async def test_write_stamps_activity_and_frames():
    t = _mk()
    proc = MagicMock()
    proc.returncode = None
    written: list[bytes] = []
    proc.stdin = MagicMock()
    proc.stdin.write = lambda b: written.append(b)
    proc.stdin.drain = AsyncMock()
    t._process = proc
    before = t.last_activity
    await t.write('{"x":1}\n')
    assert written == [b'{"x":1}\n']
    assert t.last_activity >= before


@pytest.mark.asyncio
async def test_readline_requires_running_process():
    t = _mk()
    with pytest.raises(AcpError, match="not running"):
        await t.readline()


@pytest.mark.asyncio
async def test_readline_returns_line():
    t = _mk()
    proc = MagicMock()
    proc.returncode = None
    proc.stdout = MagicMock()
    proc.stdout.readline = AsyncMock(return_value=b'{"ok":true}\n')
    t._process = proc
    assert await t.readline() == b'{"ok":true}\n'


def test_stderr_tail_redacts():
    t = _mk()
    t._stderr_lines = deque(["boom key=AKIAIOSFODNN7EXAMPLE"])
    tail = t.stderr_tail()
    assert "AKIAIOSFODNN7EXAMPLE" not in tail
    assert "boom" in tail


def test_stderr_tail_empty():
    assert _mk().stderr_tail() == ""


@pytest.mark.asyncio
async def test_kill_noop_when_no_process():
    await _mk().kill()  # must not raise


@pytest.mark.asyncio
async def test_kill_sigterm_then_sweeps_escaped():
    t = _mk()
    proc = MagicMock()
    proc.returncode = None
    proc.stdin = proc.stdout = proc.stderr = MagicMock()
    proc.wait = AsyncMock(return_value=0)
    t._process = proc
    t._pid = 4321
    t._child_pids = {}
    with (
        patch("os.killpg") as killpg,
        patch("os.getpgid", return_value=4321),
        patch("personalclaw.acp.transport._kill_escaped_children") as sweep,
    ):
        await t.kill()
    killpg.assert_called_once()
    sweep.assert_called_once()


def test_teardown_clears_and_unlinks(tmp_path):
    t = _mk()
    proc = MagicMock()
    proc.stdin = proc.stdout = proc.stderr = MagicMock()
    t._process = proc
    sb = tmp_path / "sandbox.sb"
    sb.write_text("profile")
    t._sandbox_cleanup = str(sb)
    t._child_pids = {123: None}
    with (
        patch("personalclaw.session._untrack_child_pids"),
        patch("personalclaw.session._untrack_pid"),
        patch("personalclaw.session._untrack_session_pid"),
    ):
        t.teardown()
    assert t._process is None
    assert t.pid is None
    assert t._child_pids == {}
    assert not sb.exists()  # sandbox temp unlinked


def test_get_child_pids_none_and_missing():
    from personalclaw.acp.transport import _get_child_pids

    assert _get_child_pids(None) == []
    assert _get_child_pids(999999) == []


def test_recursive_children(monkeypatch):
    import personalclaw.acp.transport as tmod
    from personalclaw.acp.transport import _get_child_pids

    tree = {1000: [2000, 3000], 2000: [4000], 3000: [5000]}
    monkeypatch.setattr(tmod, "_direct_children", lambda pid: tree.get(pid, []))
    assert _get_child_pids(1000) == [2000, 4000, 3000, 5000]
