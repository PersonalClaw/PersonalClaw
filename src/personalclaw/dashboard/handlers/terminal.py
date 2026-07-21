"""WebSocket PTY handler for the built-in CLI panel."""

import asyncio
import fcntl
import json
import logging
import os
import pty as _pty
import shutil
import signal
import struct
import termios
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from aiohttp import web

from personalclaw.config.loader import config_path

if TYPE_CHECKING:
    from personalclaw.dashboard.state import DashboardState

logger = logging.getLogger(__name__)

_MAX_SESSIONS = 3
_ORPHAN_TIMEOUT_S = 300  # 5 min with no WS → reap PTY

# Requested cwd from POST /sessions, consumed by the WS spawn (create returns a
# session_id but the PTY spawns on WS connect). Keyed by session_id.
_pending_cwd: dict[str, str] = {}


def _sel():
    import personalclaw.dashboard.handlers as _pkg  # circular import: __init__ imports terminal

    return _pkg.sel()


@dataclass
class _TerminalSession:
    """Server-side state for one PTY session."""

    session_id: str
    master_fd: int
    proc: asyncio.subprocess.Process
    cols: int = 80
    rows: int = 24
    created_at: float = field(default_factory=time.monotonic)
    last_ws_disconnect: float | None = None  # set when WS drops, cleared on reconnect
    ws: web.WebSocketResponse | None = None
    reader_task: asyncio.Task | None = None
    cwd: str = ""  # the dir the PTY started in (shown in the UI)
    shell: str = ""  # the shell binary (shown in the UI)
    # P25: tmux-backed persistence. When True the PTY wraps a tmux CLIENT attached to a
    # detached tmux session (the daemon owns the real shell), so the shell + scrollback
    # survive a gateway restart — only the attach-client dies on restart/WS-drop, not the
    # session. The orphan-reaper kills the client, never `tmux kill-session`.
    persistent: bool = False


def _get_registry(request: web.Request) -> dict[str, _TerminalSession | None]:
    state: "DashboardState" = request.app["state"]
    return state._terminal_sessions


def _get_config(request: web.Request) -> dict:
    try:
        data = json.loads(config_path().read_text(encoding="utf-8"))
        return data.get("dashboard", {}).get("terminal", {})
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


# P25 — tmux-backed persistence. Opt-in (config `dashboard.terminal.persist`) AND requires
# the `tmux` binary; both gates default to the in-process PTY (today's behavior). A dedicated
# socket (`-L personalclaw`) isolates our sessions from the user's own tmux.
_TMUX_SOCKET = "personalclaw"


def _tmux_available() -> bool:
    """Whether the tmux binary is on PATH (macOS/Linux only; Windows has none)."""
    return shutil.which("tmux") is not None


def _persist_enabled(request: web.Request) -> bool:
    """Persistent (tmux-backed) terminals are ON only when BOTH the opt-in config flag
    and a usable tmux binary are present — else fall back to the in-process PTY unchanged."""
    return bool(_get_config(request).get("persist", False)) and _tmux_available()


def _tmux_session_name(session_id: str) -> str:
    """tmux session name for a PClaw terminal id — tmux forbids '.' in names, so map
    it to '_' (the dashboard session_id is otherwise a safe slug)."""
    return "pclaw-" + str(session_id).replace(".", "_")


def _is_enabled(request: web.Request) -> bool:
    """Terminal PTY is enabled by default — it is the user's own interactive
    shell, powering both the freeform CLI panel and the per-provider Sign-in
    terminal (the agent-runtime auth flow depends on it). Like every route it
    is authenticated at the WS handshake via ``token_auth_middleware``.

    Opt OUT explicitly via config.json:
    {"dashboard": {"terminal": {"enabled": false}}}
    Cached for 30s to avoid disk I/O per request.
    """
    now = time.monotonic()
    if now - _enabled_cache[1] < 30:
        return _enabled_cache[0]
    result = bool(_get_config(request).get("enabled", True))
    _enabled_cache[0] = result
    _enabled_cache[1] = now
    return result


_enabled_cache: list = [True, 0.0]  # [value, timestamp]


async def _safe_send(sess: "_TerminalSession", data: bytes) -> None:
    """Send PTY bytes to the WS, swallowing errors (the socket may close mid-send)."""
    try:
        if sess.ws and not sess.ws.closed:
            await sess.ws.send_bytes(data)
    except Exception:
        pass


async def _kill_session(sess: _TerminalSession) -> None:
    """Kill PTY process and close FDs for a session."""
    # Stop watching the fd on the event loop, then close it. With add_reader (no
    # blocking executor thread) this is clean + instant — nothing to leak.
    if sess.master_fd >= 0:
        try:
            asyncio.get_running_loop().remove_reader(sess.master_fd)
        except Exception:
            pass
        try:
            os.close(sess.master_fd)
        except OSError:
            pass
        sess.master_fd = -1
    if sess.reader_task is not None:
        sess.reader_task.cancel()
        try:
            await sess.reader_task
        except (asyncio.CancelledError, Exception):
            pass
    if sess.proc is not None and sess.proc.returncode is None:
        try:
            os.killpg(sess.proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(sess.proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            try:
                os.killpg(sess.proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await sess.proc.wait()


async def api_terminal_ws(request: web.Request) -> web.WebSocketResponse | web.Response:
    """WebSocket PTY for the built-in CLI panel.

    Protocol:
      - Binary frames: raw terminal I/O (both directions)
      - Text frames (JSON): control messages
        - Client→Server: {"type":"resize","cols":N,"rows":N}
        - Client→Server: {"type":"ping"}
        - Server→Client: {"type":"pong"}
    """
    caller = request.get("user")
    if not caller:
        _sel().log_api_access(
            caller="unknown",
            operation="terminal.ws.open",
            outcome="denied",
            source="dashboard",
            resources=str(request.remote),
        )
        return web.Response(status=401, text="Unauthorized")
    if not _is_enabled(request):
        _sel().log_api_access(
            caller=caller,
            operation="terminal.ws.open",
            outcome="denied",
            source="dashboard",
            resources="feature_disabled",
        )
        return web.Response(status=403, text="Terminal panel disabled")

    session_id = request.match_info.get("session_id", "")
    if not session_id or len(session_id) > 64:
        _sel().log_api_access(
            caller=caller,
            operation="terminal.ws.open",
            outcome="denied",
            source="dashboard",
            resources=f"invalid_session_id={session_id!r}",
        )
        return web.Response(status=400, text="Invalid session_id")

    registry = _get_registry(request)
    cfg = _get_config(request)
    max_sessions = cfg.get("max_sessions", _MAX_SESSIONS)

    # Check if reconnecting to existing session
    existing = registry.get(session_id)
    if existing and existing.proc.returncode is not None:
        # Process died — clean up stale entry
        await _kill_session(existing)
        del registry[session_id]
        existing = None

    # Reserve session synchronously before any await to prevent race condition
    if not existing and len(registry) >= max_sessions:
        _sel().log_api_access(
            caller=caller,
            operation="terminal.ws.open",
            outcome="denied",
            source="dashboard",
            resources=f"max_sessions={max_sessions}",
        )
        return web.Response(status=429, text=f"Max {max_sessions} terminal sessions")

    # Reserve a placeholder so concurrent requests see the session as taken
    placeholder = not existing
    if placeholder:
        registry[session_id] = None

    ws = web.WebSocketResponse(heartbeat=30, timeout=300)
    try:
        await ws.prepare(request)
    except Exception:
        if placeholder:
            registry.pop(session_id, None)  # type: ignore[arg-type]
        raise

    if existing:
        # Reconnect to existing PTY
        existing.ws = ws
        existing.last_ws_disconnect = None
        sess = existing
        _sel().log_api_access(
            caller=caller,
            operation="terminal.ws.reconnect",
            outcome="ok",
            source="dashboard",
            resources=f"session={session_id},pid={sess.proc.pid}",
        )
    else:
        # Spawn new PTY
        master_fd, worker_fd = _pty.openpty()
        # Non-blocking master so we can read it via the event loop's add_reader
        # (NOT a blocking os.read on an executor thread — closing the fd from
        # another thread does NOT reliably unblock a thread parked in os.read on
        # macOS, leaking executor threads on every session close until the pool
        # exhausts and the whole gateway hangs. add_reader avoids threads entirely).
        os.set_blocking(master_fd, False)
        try:
            fcntl.ioctl(
                worker_fd,
                termios.TIOCSWINSZ,
                struct.pack("HHHH", 24, 80, 0, 0),
            )
            shell = str(cfg.get("shell") or os.environ.get("SHELL", "/bin/bash"))
            # prefer the cwd requested at create time (workspace-scoped), then
            # cfg, then HOME. Only honor a requested cwd that actually exists.
            _req_cwd = _pending_cwd.pop(session_id, "")
            cwd = cfg.get("cwd") or os.environ.get("HOME", "/")
            if _req_cwd and os.path.isdir(_req_cwd):
                cwd = _req_cwd
            env = {
                **os.environ,
                "TERM": "xterm-256color",
                "PERSONALCLAW_TERMINAL": "1",
                # This PTY is an automation target: the cockpit/chat inject commands
                # the moment the socket opens, while the login shell is still running
                # rc files. oh-my-zsh's periodic update prompt does `read -r -k 1`
                # during init and STEALS the first byte of that pending input
                # ("python …" → "ython …" → command not found); its has_typed_input
                # guard is broken on macOS (GNU-only `stty --save`). Disable the
                # updater in embedded shells (omz-documented env var; the user's own
                # terminals still prompt). Harmless for bash/fish.
                "DISABLE_AUTO_UPDATE": "true",
            }
            # Security: intentionally unsandboxed — this is the user's own
            # interactive terminal (like SSH), not agent-executed code.
            # Auth is enforced at WS handshake via token_auth_middleware.
            # See CLI_PANEL_DESIGN.md §8 "Security Considerations".
            persistent = _persist_enabled(request)
            if persistent:
                # P25: the PTY runs a tmux CLIENT attached to a detached session (created
                # if absent, re-attached if it survived a restart). `new-session -A -s`
                # is attach-or-create; the daemon (not this client) owns the shell, so a
                # gateway restart kills only the client — the shell + scrollback live on.
                tname = _tmux_session_name(session_id)
                argv = ["tmux", "-L", _TMUX_SOCKET, "new-session", "-A", "-s", tname, shell, "-l"]
            else:
                argv = [shell, "-l"]
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=worker_fd,
                stdout=worker_fd,
                stderr=worker_fd,
                start_new_session=True,
                cwd=cwd,
                env=env,
            )
        except Exception as exc:
            # Clean up master_fd on failure
            try:
                os.close(master_fd)
            except OSError:
                pass
            registry.pop(session_id, None)  # type: ignore[arg-type]
            # WS already prepared — send error over WS then close
            if not ws.closed:
                await ws.send_str(json.dumps({"type": "error", "message": str(exc)}))
                await ws.close()
            return ws
        finally:
            os.close(worker_fd)

        sess = _TerminalSession(
            session_id=session_id,
            master_fd=master_fd,
            proc=proc,
            ws=ws,
            cwd=str(cwd),
            shell=shell,
            persistent=persistent,
        )
        registry[session_id] = sess
        _sel().log_api_access(
            caller=caller,
            operation="terminal.ws.open",
            outcome="ok",
            source="dashboard",
            resources=f"session={session_id},pid={proc.pid},shell={shell}",
        )

    # --- Read loop: PTY → WebSocket, via the event loop (NO executor thread) ---
    # The master_fd is non-blocking; add_reader fires this callback whenever the
    # PTY has output. EOF (empty read) = the shell exited → notify the client and
    # reap. This design has NO blocking thread to leak on close (the old
    # run_in_executor(os.read) leaked one parked thread per closed session →
    # pool exhaustion → gateway hang, the tab-✕ bug).
    async def handle_exit():
        # GENUINE shell exit while NOT being explicitly torn down (delete pops
        # from the registry first → guard below). Notify + reap once.
        if registry.get(session_id) is not sess:
            return
        try:
            await asyncio.wait_for(sess.proc.wait(), timeout=5)
        except Exception:
            pass
        code = sess.proc.returncode if sess.proc else None
        loop_ = asyncio.get_running_loop()
        if sess.master_fd >= 0:
            try:
                loop_.remove_reader(sess.master_fd)
            except Exception:
                pass
            try:
                os.close(sess.master_fd)
            except OSError:
                pass
            sess.master_fd = -1
        registry.pop(session_id, None)
        if sess.ws and not sess.ws.closed:
            try:
                await sess.ws.send_str(json.dumps({"type": "exited", "code": code}))
                await sess.ws.close()
            except Exception:
                pass

    def on_pty_readable():
        if sess.master_fd < 0:
            return
        try:
            data = os.read(sess.master_fd, 65536)
        except BlockingIOError:
            return  # spurious wakeup, nothing to read yet
        except OSError:
            data = b""  # fd error → treat as EOF
        if not data:
            # EOF → shell exited. Stop watching, hand off to the async reaper.
            try:
                asyncio.get_running_loop().remove_reader(sess.master_fd)
            except Exception:
                pass
            asyncio.ensure_future(handle_exit())
            return
        if sess.ws and not sess.ws.closed:
            asyncio.ensure_future(_safe_send(sess, data))

    # register the fd reader on the running loop (idempotent on reconnect)
    _loop = asyncio.get_running_loop()
    try:
        _loop.remove_reader(sess.master_fd)
    except Exception:
        pass
    _loop.add_reader(sess.master_fd, on_pty_readable)

    # --- Write loop: WebSocket → PTY ---
    try:
        async for msg in ws:
            if msg.type == web.WSMsgType.BINARY:
                # master_fd is non-blocking; write directly (no executor thread).
                # Handle partial writes; EAGAIN is vanishingly rare for keystroke-
                # sized input but loop just in case.
                try:
                    buf = msg.data
                    while buf and sess.master_fd >= 0:
                        try:
                            n = os.write(sess.master_fd, buf)
                            buf = buf[n:]
                        except BlockingIOError:
                            await asyncio.sleep(0)
                except OSError:
                    break
            elif msg.type == web.WSMsgType.TEXT:
                try:
                    ctrl = json.loads(msg.data)
                except (json.JSONDecodeError, ValueError):
                    continue
                if ctrl.get("type") == "resize":
                    try:
                        cols = min(max(int(ctrl.get("cols", 80)), 1), 500)
                        rows = min(max(int(ctrl.get("rows", 24)), 1), 200)
                    except (ValueError, TypeError):
                        continue
                    sess.cols = cols
                    sess.rows = rows
                    try:
                        fcntl.ioctl(
                            sess.master_fd,
                            termios.TIOCSWINSZ,
                            struct.pack("HHHH", rows, cols, 0, 0),
                        )
                    except OSError:
                        pass
                elif ctrl.get("type") == "ping":
                    if not ws.closed:
                        await ws.send_str(json.dumps({"type": "pong"}))
            elif msg.type in (web.WSMsgType.CLOSE, web.WSMsgType.ERROR):
                break
    finally:
        # WS disconnected — mark for orphan reaper, but keep PTY alive
        sess.ws = None
        sess.last_ws_disconnect = time.monotonic()
        _sel().log_api_access(
            caller=caller,
            operation="terminal.ws.disconnect",
            outcome="ok",
            source="dashboard",
            resources=f"session={session_id}",
        )

    return ws


async def api_terminal_create(request: web.Request) -> web.Response:
    """POST /api/terminal/sessions — create a new terminal session (returns session_id)."""
    caller = request.get("user")
    if not caller:
        _sel().log_api_access(
            caller="unknown",
            operation="terminal.session.create",
            outcome="denied",
            source="dashboard",
            resources=str(request.remote),
        )
        return web.Response(status=401, text="Unauthorized")
    if not _is_enabled(request):
        _sel().log_api_access(
            caller=caller,
            operation="terminal.session.create",
            outcome="denied",
            source="dashboard",
            resources="feature_disabled",
        )
        return web.Response(status=403, text="Terminal panel disabled")

    registry = _get_registry(request)
    cfg = _get_config(request)
    max_sessions = cfg.get("max_sessions", _MAX_SESSIONS)

    if len(registry) >= max_sessions:
        _sel().log_api_access(
            caller=caller,
            operation="terminal.session.create",
            outcome="denied",
            source="dashboard",
            resources=f"max_sessions={max_sessions}",
        )
        return web.json_response(
            {"error": f"Max {max_sessions} sessions"},
            status=429,
        )

    session_id = uuid.uuid4().hex[:12]
    shell = cfg.get("shell") or os.environ.get("SHELL", "/bin/bash")
    # Optional cwd: a session can start in the workspace dir. Validated when the
    # PTY spawns (WS handler) — here we just stash the requested cwd so the WS
    # handler can use it; falls back to cfg/HOME.
    requested_cwd = ""
    if request.body_exists:
        try:
            body = await request.json()
            if isinstance(body, dict) and isinstance(body.get("cwd"), str):
                requested_cwd = body["cwd"]
        except Exception:
            pass
    if requested_cwd:
        # A PTY is far more powerful than the file tools, so don't let it root in a
        # credential dir (~/.ssh, ~/.aws) or an OS system tree — the WS spawn only
        # checked the dir EXISTS, never that it was safe. Block both here (the
        # cockpit passes an already-validated workspace; this guards direct/other
        # callers of this shared endpoint).
        from personalclaw.security import is_sensitive_path, is_system_path

        if is_sensitive_path(requested_cwd) or is_system_path(requested_cwd):
            _sel().log_api_access(
                caller=caller,
                operation="terminal.session.create",
                outcome="denied",
                source="dashboard",
                resources=f"unsafe_cwd:{requested_cwd}",
            )
            return web.json_response(
                {"error": "Cannot open a terminal in a system or credential directory."},
                status=403,
            )
        _pending_cwd[session_id] = requested_cwd
    _sel().log_api_access(
        caller=caller,
        operation="terminal.session.create",
        outcome="ok",
        source="dashboard",
        resources=f"session={session_id}",
    )
    return web.json_response(
        {
            "session_id": session_id,
            "shell": shell,
            "cwd": requested_cwd or str(cfg.get("cwd") or os.environ.get("HOME", "/")),
        }
    )


async def api_terminal_delete(request: web.Request) -> web.Response:
    """DELETE /api/terminal/sessions/{session_id} — kill a terminal session."""
    caller = request.get("user")
    if not caller:
        _sel().log_api_access(
            caller="unknown",
            operation="terminal.session.delete",
            outcome="denied",
            source="dashboard",
            resources=str(request.remote),
        )
        return web.Response(status=401, text="Unauthorized")
    if not _is_enabled(request):
        _sel().log_api_access(
            caller=caller,
            operation="terminal.session.delete",
            outcome="denied",
            source="dashboard",
            resources="feature_disabled",
        )
        return web.Response(status=403, text="Terminal panel disabled")

    session_id = request.match_info.get("session_id", "")
    registry = _get_registry(request)
    sess = registry.pop(session_id, None)  # type: ignore[arg-type]

    # An explicit delete TRULY ends a persistent session — kill its tmux session so the
    # daemon-owned shell is gone (a reap/disconnect only detaches; delete is final). This
    # also covers a detached session that survived a restart (no in-memory entry).
    persistent = sess.persistent if sess else _persist_enabled(request)
    if persistent:
        await _kill_tmux_session(session_id)

    if sess is None:
        # Not in-process. If it was a live tmux session we just killed it → ok; else 404.
        if persistent:
            _sel().log_api_access(
                caller=caller,
                operation="terminal.session.delete",
                outcome="ok",
                source="dashboard",
                resources=f"session={session_id},detached",
            )
            return web.json_response({"deleted": session_id})
        return web.Response(status=404, text="Session not found")

    if sess.ws and not sess.ws.closed:
        await sess.ws.close()
    await _kill_session(sess)

    _sel().log_api_access(
        caller=caller,
        operation="terminal.session.delete",
        outcome="ok",
        source="dashboard",
        resources=f"session={session_id}",
    )
    return web.json_response({"deleted": session_id})


async def _kill_tmux_session(session_id: str) -> None:
    """`tmux kill-session` for a PClaw terminal id on our socket. Best-effort/never-raises."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "tmux",
            "-L",
            _TMUX_SOCKET,
            "kill-session",
            "-t",
            _tmux_session_name(session_id),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=5)
    except (FileNotFoundError, asyncio.TimeoutError, OSError):
        pass


async def api_terminal_list(request: web.Request) -> web.Response:
    """GET /api/terminal/sessions — list active terminal sessions."""
    caller = request.get("user")
    if not caller:
        _sel().log_api_access(
            caller="unknown",
            operation="terminal.session.list",
            outcome="denied",
            source="dashboard",
            resources=str(request.remote),
        )
        return web.Response(status=401, text="Unauthorized")
    if not _is_enabled(request):
        _sel().log_api_access(
            caller=caller,
            operation="terminal.session.list",
            outcome="denied",
            source="dashboard",
            resources="feature_disabled",
        )
        return web.json_response({"enabled": False, "sessions": []})

    registry = _get_registry(request)
    sessions = []
    seen: set[str] = set()
    for sid, sess in registry.items():
        if sess is None:
            continue  # placeholder during ws.prepare()
        seen.add(sid)
        sessions.append(
            {
                "session_id": sid,
                "pid": sess.proc.pid if sess.proc else None,
                "alive": sess.proc.returncode is None if sess.proc else False,
                "cols": sess.cols,
                "rows": sess.rows,
                "connected": sess.ws is not None and not sess.ws.closed,
                "cwd": sess.cwd,
                "shell": sess.shell,
                "persistent": sess.persistent,
            }
        )
    # P25: surface tmux-backed sessions that survived a GATEWAY RESTART — they have no
    # in-memory registry entry yet (the reader/client died with the old process), but the
    # tmux daemon kept the shell alive. Listing them lets the FE's mount-time restore
    # re-attach after a restart, not just a page reload. Reconnecting maps session_id →
    # its tmux session (new-session -A re-attaches). Only when persistence is enabled.
    if _persist_enabled(request):
        for tname in await _list_tmux_sessions():
            if not tname.startswith("pclaw-"):
                continue
            sid = tname[len("pclaw-") :]
            if sid in seen:
                continue  # already live in-process
            sessions.append(
                {
                    "session_id": sid,
                    "pid": None,
                    "alive": True,
                    "cols": 80,
                    "rows": 24,
                    "connected": False,
                    "cwd": "",
                    "shell": "",
                    "persistent": True,
                    "detached": True,
                }
            )
    _sel().log_api_access(
        caller=caller,
        operation="terminal.session.list",
        outcome="ok",
        source="dashboard",
        resources=f"count={len(sessions)}",
    )
    return web.json_response({"enabled": True, "sessions": sessions})


async def _list_tmux_sessions() -> list[str]:
    """Live tmux session names on our dedicated socket, or [] if tmux/none. Never raises."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "tmux",
            "-L",
            _TMUX_SOCKET,
            "list-sessions",
            "-F",
            "#{session_name}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        return [ln.strip() for ln in out.decode("utf-8", "replace").splitlines() if ln.strip()]
    except (FileNotFoundError, asyncio.TimeoutError, OSError):
        return []


async def reap_orphaned_terminals(app: web.Application) -> None:
    """Background task: kill PTY sessions with no WS connection for >5 min."""
    try:
        while True:
            await asyncio.sleep(60)
            state = app.get("state")
            if not state or not hasattr(state, "_terminal_sessions"):
                continue
            registry: dict[str, _TerminalSession] = state._terminal_sessions
            now = time.monotonic()
            to_remove = []
            for sid, sess in registry.items():
                if sess is None:
                    continue  # placeholder during ws.prepare()
                # Reap if disconnected too long
                if sess.last_ws_disconnect and (now - sess.last_ws_disconnect) > _ORPHAN_TIMEOUT_S:
                    to_remove.append(sid)
                # Reap if process died
                elif sess.proc.returncode is not None:
                    to_remove.append(sid)
            for sid in to_remove:
                removed = registry.pop(sid, None)
                if removed is not None:
                    await _kill_session(removed)
                    logger.info("Reaped orphaned terminal session %s", sid)
    except asyncio.CancelledError:
        pass
