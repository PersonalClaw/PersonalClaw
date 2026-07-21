"""AcpProcess — the ACP backend subprocess + its stdio, as a standalone transport.

The process lifecycle (spawn with sandbox wrap + env/PATH/SSH resolution + process-group
isolation, kill via ``killpg`` tree-sweep + escaped-child cleanup, PID/child-PID tracking,
stderr draining, liveness) and the raw stdio primitives (stdin ``write``, stdout
``readline``) used to live fused inside :class:`~personalclaw.acp.client.AcpClient` next
to its inline single-reader turn loop. Pulled out here so BOTH the one-session client and
the concurrent :class:`~personalclaw.acp.session.AcpConnection` drive the SAME process
machinery — no duplicate spawn/kill/track code — and so a
:class:`~personalclaw.acp.reader.FrameRouter`
can take ``readline`` as its line source directly.

Vendor-neutral: the caller supplies the launch argv; this layer knows nothing about any
specific ACP backend. Raises :class:`~personalclaw.acp.errors.AcpError` /
:class:`~personalclaw.acp.errors.AcpProcessDied` (the leaf error module) so it never has
to import the client.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess as subprocess_mod
import sys
import time
from collections import deque
from pathlib import Path

from personalclaw.acp.errors import AcpError, AcpProcessDied
from personalclaw.env import augmented_path
from personalclaw.sandbox import wrap_argv

logger = logging.getLogger(__name__)

_ACP_TRACE = os.environ.get("PERSONALCLAW_ACP_TRACE") == "1"


def _acp_trace(direction: str, text: str) -> None:
    if _ACP_TRACE:
        logger.info("ACP-TRACE %s %s", direction, text[:600])


# Subprocess stdout buffer — agents can send large JSON-RPC lines (tool outputs)
_STDOUT_BUFFER_LIMIT = 10 * 1024 * 1024  # 10MB


def _resolve_ssh_auth_sock(env: dict[str, str]) -> None:
    """Ensure SSH_AUTH_SOCK points to a live agent socket.

    The gateway's inherited value may be stale after ssh-agent restarts.
    Mirrors the env-resolution that long-running editors do via
    ``getUnixShellEnvironment()`` but without spawning a login shell.

    - macOS: launchd listener path changes on reboot
    - Linux: ssh-agent sockets live under /tmp/ssh-*/agent.*
    """
    import glob
    import stat
    import sys

    current = env.get("SSH_AUTH_SOCK", "")
    if current and os.path.exists(current):
        return  # already valid

    if sys.platform == "darwin":
        patterns = ["/tmp/com.apple.launchd.*/Listeners"]
    else:
        uid = os.getuid()
        patterns = [
            "/tmp/ssh-*/agent.*",
            f"/run/user/{uid}/ssh-agent.socket",
            f"/run/user/{uid}/keyring/ssh",
        ]

    for pattern in patterns:
        candidates = [p for p in glob.glob(pattern) if stat.S_ISSOCK(os.stat(p).st_mode)]
        if candidates:
            best = max(candidates, key=lambda p: os.path.getmtime(p))
            env["SSH_AUTH_SOCK"] = best
            logger.debug("Resolved SSH_AUTH_SOCK → %s", best)
            return


def _get_child_pids(parent_pid: int | None, _visited: set[int] | None = None) -> list[int]:
    """Return PIDs of all descendants recursively (best-effort).

    Uses a visited set to prevent infinite loops from PID cycles.
    On Linux, reads /proc/<pid>/task/*/children (kernel-provided, fast).
    Falls back to pgrep -P on other platforms.
    """
    if not parent_pid:
        return []
    if _visited is None:
        _visited = set()
    if parent_pid in _visited:
        return []
    _visited.add(parent_pid)

    direct = _direct_children(parent_pid)
    all_pids = []
    for cpid in direct:
        if cpid not in _visited:
            all_pids.append(cpid)
            all_pids.extend(_get_child_pids(cpid, _visited))
    return all_pids


def _direct_children(pid: int) -> list[int]:
    """Return direct child PIDs. Uses /proc on Linux, pgrep elsewhere."""
    if sys.platform == "linux":
        try:
            children: list[int] = []
            tasks_dir = Path(f"/proc/{pid}/task")
            if tasks_dir.is_dir():
                for tid in tasks_dir.iterdir():
                    cf = tid / "children"
                    if cf.exists():
                        children.extend(int(p) for p in cf.read_text().split() if p.strip())
            if children:
                return children
        except Exception:
            pass  # fall through to pgrep
    try:
        out = subprocess_mod.check_output(["pgrep", "-P", str(pid)], stderr=subprocess_mod.DEVNULL)
        return [int(p) for p in out.decode().split() if p.strip()]
    except Exception:
        return []


def _get_start_time(pid: int) -> int | None:
    """Read process start time to detect PID recycling."""
    try:
        if sys.platform == "linux":
            stat = Path(f"/proc/{pid}/stat").read_text()
            fields = stat.rsplit(")", 1)[1].split()
            return int(fields[19])  # field 22 = starttime
        # macOS: use ps -o lstart= (absolute start timestamp, constant for process lifetime)
        out = subprocess_mod.check_output(
            ["ps", "-o", "lstart=", "-p", str(pid)], stderr=subprocess_mod.DEVNULL, timeout=2
        )
        return hash(out.strip())  # stable per-process, changes on recycle
    except Exception:
        return None


def _is_our_child(pid: int, expected_start: int | None = None) -> bool:
    """Verify a PID still belongs to an agent / MCP-related process (deny-by-default).

    Uses an allowlist on executable basename — only kills processes whose
    binary matches known ACP agent / MCP runtime names. Returns False
    for anything else, including recycled PIDs. The allowlist covers
    common ACP agent CLIs (``claude``) and MCP server runtimes
    (``node``, ``npx``, ``python``, ``ruby``, ``uv``); deployments that
    ship their own ACP backend should extend the list as needed.
    """
    allowed_prefixes = (
        b"claude",
        b"node",
        b"npx",
        b"python",
        b"ruby",
    )
    allowed_exact = (b"uv",)
    try:
        if sys.platform == "linux":
            cmdline_path = Path(f"/proc/{pid}/cmdline")
            if not cmdline_path.exists():
                return False
            cmdline = cmdline_path.read_bytes()
            exe = cmdline.split(b"\x00", 1)[0].rsplit(b"/", 1)[-1]
        else:
            out = subprocess_mod.check_output(
                ["ps", "-o", "comm=", "-p", str(pid)], stderr=subprocess_mod.DEVNULL, timeout=2
            )
            exe = out.strip().rsplit(b"/", 1)[-1]
        # Match runtime prefixes, exact names, or any binary with "mcp" in the name
        if not (
            any(exe.startswith(tok) for tok in allowed_prefixes)
            or exe in allowed_exact
            or b"mcp" in exe
        ):
            return False
        # Start-time check: definitive PID recycling detection (always required)
        actual_start = _get_start_time(pid)
        if expected_start is None or actual_start is None:
            logger.debug("PID %d start time unavailable — denying (fail-closed)", pid)
            return False
        if actual_start != expected_start:
            logger.debug("PID %d start time mismatch (recycled)", pid)
            return False
        return True
    except Exception:
        return False


def _kill_escaped_children(child_pids: dict[int, int | None]) -> None:
    """SIGKILL descendants that survived killpg (different PGID). Kills leaf-first."""
    for cpid in reversed(list(child_pids.keys())):
        try:
            os.kill(cpid, 0)  # still alive?
            if not _is_our_child(cpid, expected_start=child_pids.get(cpid)):
                logger.debug("Skipping PID %d — not an ACP agent / MCP process (recycled?)", cpid)
                continue
            os.kill(cpid, signal.SIGKILL)
            logger.debug("Killed escaped child PID %d", cpid)
        except (ProcessLookupError, OSError):
            pass


class AcpProcess:
    """One ACP backend subprocess + its stdio pipes.

    Owns spawn/kill/PID-tree tracking/stderr drain/liveness and the raw stdin
    ``write`` + stdout ``readline`` primitives. The turn loop (client or session)
    layers on top; a FrameRouter can take :meth:`readline` as its line source."""

    def __init__(
        self,
        *,
        command: list[str],
        work_dir: Path,
        sandbox_mode: str = "auto",
        extra_env: dict[str, str] | None = None,
        session_key: str | None = None,
        channel_id: str | None = None,
    ) -> None:
        self._command = list(command) if command else []
        self._work_dir = Path(work_dir)
        self._sandbox_mode = sandbox_mode
        self._extra_env = dict(extra_env) if extra_env else None
        self._session_key = session_key
        self._channel_id = channel_id

        self._process: asyncio.subprocess.Process | None = None
        self._pid: int | None = None
        self._start_time: int | None = None  # start time for PID-recycle detection
        self._child_pids: dict[int, int | None] = {}  # pid → start_time snapshot
        self._sandbox_cleanup: str | None = None
        self._stderr_lines: deque[str] = deque(maxlen=20)
        self._stderr_task: asyncio.Task | None = None  # type: ignore[type-arg]
        self._last_activity: float = time.monotonic()

    # ── identity ────────────────────────────────────────────────────────────────
    @property
    def process(self) -> asyncio.subprocess.Process | None:
        return self._process

    @property
    def pid(self) -> int | None:
        return self._pid

    # ── liveness ────────────────────────────────────────────────────────────────
    def is_alive(self) -> bool:
        return self._process is not None and self._process.returncode is None

    @property
    def exit_code(self) -> int | None:
        return self._process.returncode if self._process else None

    def is_responsive(self, stale_threshold: float = 600.0) -> bool:
        if not self.is_alive():
            return False
        return (time.monotonic() - self._last_activity) < stale_threshold

    def touch(self) -> None:
        """Refresh the activity timestamp without I/O (long MCP tools call this)."""
        self._last_activity = time.monotonic()

    @property
    def last_activity(self) -> float:
        return self._last_activity

    # ── stdio primitives ──────────────────────────────────────────────────────
    async def write(self, data: str) -> None:
        """Write a framed line to stdin (+drain), stamping activity. Raises
        AcpProcessDied on a broken pipe, AcpError if the process isn't running."""
        if not self._process or not self._process.stdin:
            raise AcpError("ACP process not running")
        _acp_trace(">>", data.strip())
        try:
            self._process.stdin.write(data.encode())
            await self._process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as exc:
            raise AcpProcessDied(f"ACP process pipe broken: {exc}") from exc
        self._last_activity = time.monotonic()

    async def readline(self) -> bytes:
        """Read one raw line from stdout (the FrameRouter/turn-loop line source).
        Returns ``b""`` on EOF. Raises AcpError if the process isn't running."""
        if not self._process or not self._process.stdout:
            raise AcpError("ACP process not running")
        return await self._process.stdout.readline()

    def stderr_tail(self) -> str:
        """Redacted tail of recent stderr lines (for death diagnostics)."""
        if not self._stderr_lines:
            return ""
        tail = "; ".join(self._stderr_lines)
        from personalclaw.security import redact_credentials, redact_exfiltration_urls

        tail, _ = redact_exfiltration_urls(tail)
        tail, _ = redact_credentials(tail)
        return tail

    # ── spawn / kill / teardown ─────────────────────────────────────────────────
    async def spawn(self) -> None:
        """Start the ACP agent subprocess with stdio pipes + process-group isolation."""
        self._work_dir.mkdir(parents=True, exist_ok=True)

        if not self._command:
            raise AcpError("AcpProcess requires a non-empty command argv to spawn an ACP agent")
        argv = list(self._command)

        # OS-level sandbox: wrap the command to hide sensitive paths
        argv, self._sandbox_cleanup = wrap_argv(argv, mode=self._sandbox_mode)

        # Process group isolation: start_new_session=True (calls setsid, enables killpg)
        env = {**os.environ}
        if self._extra_env:
            env.update(self._extra_env)
        env["PATH"] = augmented_path(env.get("PATH", ""))
        if self._session_key:
            env["PERSONALCLAW_SESSION_KEY"] = self._session_key
        else:
            env.pop("PERSONALCLAW_SESSION_KEY", None)
        if self._channel_id:
            env["PERSONALCLAW_CHANNEL_ID"] = self._channel_id
        else:
            env.pop("PERSONALCLAW_CHANNEL_ID", None)

        # Resolve SSH_AUTH_SOCK dynamically — the gateway's env may be stale after
        # credential refreshes (same issue editors solve via getUnixShellEnvironment).
        _resolve_ssh_auth_sock(env)

        kwargs: dict = {
            "stdin": asyncio.subprocess.PIPE,
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
            "cwd": str(self._work_dir),
            "limit": _STDOUT_BUFFER_LIMIT,
            "start_new_session": True,
            "env": env,
        }

        self._process = await asyncio.create_subprocess_exec(*argv, **kwargs)
        self._pid = self._process.pid
        self._start_time = _get_start_time(self._pid)
        # Log the binary basename without leaking the full argv (may carry creds).
        binary_name = Path(argv[0]).name if argv else "acp-agent"
        logger.info("Spawned %s (PID %d)", binary_name, self._pid)
        # Track root PID + early descendant scan (agents fork MCP servers fast;
        # recording them here means kill() can clean up even if init fails).
        from personalclaw.session import _track_child_pids, _track_pid, _track_session_pid

        _track_pid(self._pid)
        _track_session_pid(self._pid)  # separate file for startup cleanup
        await asyncio.sleep(0.3)
        early_descendants = _get_child_pids(self._pid)
        if early_descendants:
            self._child_pids = {p: _get_start_time(p) for p in early_descendants}
            _track_child_pids(self._child_pids, parent_pid=self._pid or 0)
            logger.info("Early tracking %d descendants of PID %d", len(self._child_pids), self._pid)

        if self._process.stderr:
            self._stderr_task = asyncio.ensure_future(self._drain_stderr(self._process.stderr))

    async def _drain_stderr(self, stderr: asyncio.StreamReader) -> None:
        binary_name = Path(self._command[0]).name if self._command else "acp-agent"
        while True:
            line = await stderr.readline()
            if not line:
                break
            text = line.decode(errors="replace").strip()
            if text:
                self._stderr_lines.append(text)
                self._last_activity = time.monotonic()
                from personalclaw.security import redact_credentials, redact_exfiltration_urls

                redacted, _ = redact_exfiltration_urls(text)
                redacted, _ = redact_credentials(redacted)
                logger.warning("%s stderr: %s", binary_name, redacted)

    async def snapshot_process_tree(self) -> None:
        """Discover + track the full process tree after MCP servers load.

        Merges with the early snapshot from spawn(); MCP servers (node, etc.) may
        not exist until after the session handshake."""
        descendants = _get_child_pids(self._pid)
        if not descendants:
            await asyncio.sleep(0.5)  # children may not have forked yet — retry once
            descendants = _get_child_pids(self._pid)

        for p in descendants:
            if p not in self._child_pids:
                self._child_pids[p] = _get_start_time(p)

        if self._child_pids:
            from personalclaw.session import _track_child_pids

            _track_child_pids(self._child_pids, parent_pid=self._pid or 0)
            logger.info("Tracked %d descendant PIDs for PID %d", len(self._child_pids), self._pid)

    async def kill(self, *, force: bool = False) -> None:
        """Kill the subprocess + its tree and wait for exit (killpg, then sweep
        escaped children). ``force`` skips the graceful SIGTERM."""
        if not self._process or self._process.returncode is not None:
            return
        pid = self._pid
        # Close pipes first to unblock any pending reads/writes.
        for pipe in (self._process.stdin, self._process.stdout, self._process.stderr):
            if pipe:
                try:
                    pipe.close()  # type: ignore[union-attr]
                except Exception:
                    pass

        # Snapshot child PIDs before killing — children in a different process
        # group survive killpg. Merge stored (from init, catches reparented PIDs)
        # with a fresh scan (catches children spawned after init).
        fresh = _get_child_pids(pid)
        merged: dict[int, int | None] = dict(self._child_pids)
        for p in fresh:
            if p not in merged:
                merged[p] = _get_start_time(p)

        if not force:
            try:
                os.killpg(os.getpgid(pid), signal.SIGTERM)  # type: ignore[arg-type]
            except (ProcessLookupError, OSError):
                pass
            try:
                await asyncio.wait_for(self._process.wait(), timeout=3.0)
                _kill_escaped_children(merged)
                return
            except asyncio.TimeoutError:
                pass
        # Force kill
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)  # type: ignore[arg-type]
        except (ProcessLookupError, OSError):
            try:
                self._process.kill()
            except (ProcessLookupError, OSError):
                pass
        _kill_escaped_children(merged)
        try:
            await asyncio.wait_for(self._process.wait(), timeout=1.0)
        except asyncio.TimeoutError:
            logger.warning("PID %s did not exit after force kill", pid)

    def teardown(self) -> None:
        """Release process resources after it's dead: close pipes, unlink the
        sandbox temp profile, cancel the stderr task, untrack PIDs, null state.
        (The turn/reader-state reset stays with the owning turn loop.)"""
        if self._process:
            for pipe in (self._process.stdin, self._process.stdout, self._process.stderr):
                if pipe:
                    try:
                        pipe.close()  # type: ignore[union-attr]
                    except Exception:
                        pass
        # Clean up sandbox temp files (macOS seatbelt profile)
        if self._sandbox_cleanup:
            try:
                os.remove(self._sandbox_cleanup)
            except OSError:
                pass
            self._sandbox_cleanup = None
        saved_pid = self._pid
        saved_child_pids = self._child_pids
        self._process = None
        self._pid = None
        self._stderr_lines.clear()
        if self._stderr_task and not self._stderr_task.done():
            self._stderr_task.cancel()
        self._stderr_task = None
        self._child_pids = {}
        # Untrack PIDs from the orphan-tracking files.
        if saved_child_pids:
            try:
                from personalclaw.session import _untrack_child_pids

                _untrack_child_pids(saved_child_pids)
            except Exception:
                pass
        if saved_pid is not None:
            try:
                from personalclaw.session import _untrack_pid

                _untrack_pid(saved_pid)
            except Exception:
                pass
            try:
                from personalclaw.session import _untrack_session_pid

                _untrack_session_pid(saved_pid)
            except Exception:
                pass
