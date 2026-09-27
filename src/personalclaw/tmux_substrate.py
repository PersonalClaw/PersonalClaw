"""The tmux substrate — the one place that knows how PersonalClaw talks to tmux.

Why this is a module and not five private helpers in ``dashboard/handlers/terminal.py``:
tmux is now consulted by two unrelated callers with opposite jobs. P25 terminals *create*
sessions on our socket and reap their attach-clients; the boot recovery sweeps *interrogate*
that same socket to decide whether a run's work outlived the gateway. Two copies of "which
socket", "is tmux installed" and "what is a legal session name" is how the reaper and the
sweep end up disagreeing about whether a session exists — and the cost of that disagreement
is asymmetric: a sweep that guesses "dead" tombstones live work.

Three things live here and nowhere else:

* **The socket.** Each PersonalClaw home has a tmux server of its own, on ``<home>/tmux.sock``
  (:func:`server_flags`), so nothing we do can see, adopt or kill a session in the user's own
  tmux, or another home's. It is resolved from the active home when tmux runs, not at import.
  One ``-L personalclaw`` server per machine used to serve every home, so a dev gateway and
  the real one listed, reattached and deleted each other's sessions.
* **The names.** A durable session's name is derived from IDENTITY, never randomness, so a
  restarted gateway *recomputes* it and reattaches instead of reaping (EXECUTION-ISOLATION
  §5.1). ``terminal_session_name`` keeps P25's original mapping verbatim — it is a wire
  format, not an implementation detail: renaming it would orphan every session a running
  tmux daemon is already holding.
* **The probes.** Every call is best-effort and never raises. tmux absent, tmux hung, tmux
  answering garbage — all read as "no session", because the callers are a reaper and a boot
  sweep and neither may crash on a missing binary. Note the direction of that default:
  "no session" makes the sweep *more* conservative about claiming work survived, never less.
* **The spawn** (:func:`new_session`, the §5.1 SPAWN half). The one writer of durable
  sessions, so the reader above and the writer share a socket and a name discipline by
  construction. Same never-raises stance, opposite default: a spawn that cannot happen
  returns False and the caller runs the work as a bare subprocess — durability is an
  enhancement to a run, never a precondition for one.

Liveness semantics: ``has_session`` is the exit code of ``tmux has-session``, which is 0 only
while the daemon still holds the session. That is the real question — a session whose shell
exited is gone from the server, so this cannot report a dead worker as alive.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import shutil
import socket
import stat
import subprocess
import time
from pathlib import Path

from personalclaw.config import loader as config_loader

logger = logging.getLogger(__name__)

#: The home's own tmux server listens here, inside the home. Never the user's default socket:
#: adopting or killing a session a human created by hand would be the worst possible failure of
#: a reaper.
SOCKET_FILENAME = "tmux.sock"

#: The longest socket path both macOS and Linux can bind: ``sun_path`` holds 104 bytes on macOS
#: (108 on Linux), the NUL included.
_SOCKET_PATH_MAX = 103

#: Every tmux call is bounded. A wedged daemon must not stall a boot sweep forever, and the
#: sweep's fallback ("no session") is the conservative answer, so a timeout is safe to take.
PROBE_TIMEOUT_S = 5.0

#: tmux forbids '.' and ':' in session names (they are its own address separators). Rather
#: than enumerate the forbidden set we allow only what is unambiguously safe — an id reaches
#: this module from a stored row, and a row is not a trust boundary.
_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")

#: Each identity component is truncated so a long project path cannot produce a name tmux
#: refuses outright. Truncation is per-component so the *shape* stays readable in `tmux ls`.
_PART_MAX = 32


def tmux_available() -> bool:
    """Whether the tmux binary is on PATH (macOS/Linux only; Windows has none)."""
    return shutil.which("tmux") is not None


def sanitize(part: str) -> str:
    """One identity component, reduced to a tmux-legal token.

    Empty (or entirely-unsafe) input becomes ``"_"`` rather than the empty string, so a
    missing component cannot collapse ``a--b`` into ``a-b`` and make two different
    identities compute the SAME session name. A name collision here is a reattach to
    someone else's worker.
    """
    return _UNSAFE.sub("_", str(part))[:_PART_MAX] or "_"


def terminal_session_name(session_id: str) -> str:
    """tmux session name for a P25 terminal id.

    Kept byte-identical to the original P25 mapping (tmux forbids '.', so map it to '_';
    the dashboard session_id is otherwise a safe slug). This is a wire format shared with
    any tmux daemon still running from a previous gateway, so it does not get "cleaned up"
    to route through :func:`sanitize`.
    """
    return "pclaw-" + str(session_id).replace(".", "_")


def durable_session_name(project_id: str, run_id: str, session_slug: str) -> str:
    """The deterministic name of a durable worker session (§5.1).

    ``pclaw-<project>-<run>-<session>``, every component sanitized. Derived purely from
    identity so a gateway that lost all in-memory state can RECOMPUTE it at boot — that
    recomputability is the entire mechanism: it is what lets the recovery sweep ask "is the
    worker for this run still alive?" without having persisted a handle that a crash could
    have failed to write.
    """
    return "-".join(
        ("pclaw", sanitize(project_id), sanitize(run_id), sanitize(session_slug)),
    )


def _home(home: Path | str | None) -> Path:
    """*home*, or the active one, with every link resolved: one home is one server however it
    is reached."""
    return Path(os.path.realpath(home if home is not None else config_loader.config_dir()))


def _fallback_name(home: Path) -> str:
    return "pclaw-" + hashlib.sha256(os.fsencode(home)).hexdigest()[:16]


def server_flags(home: Path | str | None = None) -> list[str]:
    """The tmux flags that address *home*'s own server (default: the active home).

    ``-S <home>/tmux.sock``. A home whose path leaves no room for the socket name within the
    limit gets ``-L pclaw-<hash of the home>`` instead: still a server of its own, with its
    socket in tmux's per-user folder, because a socket path cannot be longer."""
    root = _home(home)
    path = root / SOCKET_FILENAME
    if len(os.fsencode(path)) <= _SOCKET_PATH_MAX:
        return ["-S", str(path)]
    return ["-L", _fallback_name(root)]


def socket_path(home: Path | str | None = None) -> Path:
    """Where *home*'s server socket is, for either form :func:`server_flags` chooses."""
    root = _home(home)
    flags = server_flags(root)
    if flags[0] == "-S":
        return Path(flags[1])
    # tmux's own rule for ``-L``: ``$TMUX_TMPDIR`` (else /tmp), then ``tmux-<uid>``.
    base = os.environ.get("TMUX_TMPDIR") or "/tmp"
    return Path(base) / f"tmux-{os.getuid()}" / flags[1]


def _argv(*args: str) -> list[str]:
    return ["tmux", *server_flags(), *args]


def attach_argv(name: str, shell: str) -> list[str]:
    """The persistent terminal's client: attach to the session *name* on the active home's
    server, creating it with a login *shell* when it is not there (``new-session -A``)."""
    return _argv("new-session", "-A", "-s", name, shell, "-l")


async def new_session(
    name: str,
    *,
    workspace: str,
    command: list[str],
    env: dict[str, str] | None = None,
) -> bool:
    """Open a detached durable session running *command* in *workspace* — §5.1's SPAWN half.

    ``tmux new-session -d -s <name> -c <workspace> [-e K=V …] <command…>`` on our socket. The
    daemon — not the calling gateway — becomes the worker's owner, which is the entire point:
    kill the gateway and the command keeps executing, and the boot sweep finds it again by
    recomputing *name* (:func:`durable_session_name`) with zero persisted state.

    The name is VALIDATED against :func:`sanitize`'s alphabet rather than rewritten by it.
    Rewriting here would open a session under a name no recomputing reader derives — a worker
    the sweep can never find, which is durability that lies. A caller must pass a name built
    by :func:`durable_session_name`; anything else is refused.

    *command* is an argv, executed by tmux WITHOUT a shell — same no-quoting-injection stance
    as every spawn in this repo. *env* entries ride ``-e`` flags (plain argv elements, so a
    hostile value is still just a value).

    Returns True only when tmux reported the session created. False for every failure —
    absent binary, refused name, timeout, non-zero exit — never a raise, because the caller's
    contract is fall-back-to-a-bare-subprocess (runner_lifecycle's fail-open doctrine) and an
    exception here would let durability break the run it exists to protect.
    """
    if not name or _UNSAFE.search(name) or not command:
        return False
    args: list[str] = ["new-session", "-d", "-s", name, "-c", str(workspace or ".")]
    for key, value in (env or {}).items():
        args += ["-e", f"{key}={value}"]
    args += [str(part) for part in command]
    try:
        proc = await asyncio.create_subprocess_exec(
            *_argv(*args),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        rc = await asyncio.wait_for(proc.wait(), timeout=PROBE_TIMEOUT_S)
        return rc == 0
    except (FileNotFoundError, asyncio.TimeoutError, OSError):
        return False
    except Exception:  # pragma: no cover - defensive: a failed spawn must read as "no session"
        logger.debug("tmux new-session failed for %s", name, exc_info=True)
        return False


async def has_session(name: str) -> bool:
    """Whether the daemon is holding a session called *name* right now.

    ``tmux has-session`` exits 0 for present and non-zero for absent; that exit code is the
    contract this reads, not stdout. Any failure to ASK (no binary, timeout, OSError) reads
    as absent.
    """
    if not name:
        return False
    try:
        proc = await asyncio.create_subprocess_exec(
            *_argv("has-session", "-t", f"={name}"),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        rc = await asyncio.wait_for(proc.wait(), timeout=PROBE_TIMEOUT_S)
        return rc == 0
    except (FileNotFoundError, asyncio.TimeoutError, OSError):
        return False
    except Exception:  # pragma: no cover - defensive: a probe may not take down a sweep
        logger.debug("tmux has-session failed for %s", name, exc_info=True)
        return False


def has_session_sync(name: str) -> bool:
    """:func:`has_session` for a synchronous caller (the boot sweep runs off the loop)."""
    if not name:
        return False
    try:
        return (
            subprocess.run(  # noqa: S603 - fixed argv, no shell
                _argv("has-session", "-t", f"={name}"),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=PROBE_TIMEOUT_S,
            ).returncode
            == 0
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False
    except Exception:  # pragma: no cover - defensive
        logger.debug("tmux has-session (sync) failed for %s", name, exc_info=True)
        return False


async def list_sessions() -> list[str]:
    """Live session names on our socket, or ``[]`` if tmux is absent/empty. Never raises."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *_argv("list-sessions", "-F", "#{session_name}"),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=PROBE_TIMEOUT_S)
        return [ln.strip() for ln in out.decode("utf-8", "replace").splitlines() if ln.strip()]
    except (FileNotFoundError, asyncio.TimeoutError, OSError):
        return []
    except Exception:  # pragma: no cover - defensive
        logger.debug("tmux list-sessions failed", exc_info=True)
        return []


def pane_paths_sync() -> list[tuple[str, str]]:
    """``(session_name, pane_current_path)`` for every pane on our socket.

    This is the join the boot sweep needs and the reason a plain name probe is not enough.
    A durable worker created for a run is identified by WHERE it is working, not only by
    what it is called: a shell sitting in a run's workspace is that run's live substrate
    even when the gateway that started it is gone and its name was chosen by an earlier
    mechanism (P25 names a terminal after its dashboard session id, not after a run).

    Synchronous because the sweep that consumes it is. Empty list on any failure — the
    conservative answer, since an empty answer can only make the sweep decide "not alive".
    """
    try:
        out = subprocess.run(  # noqa: S603 - fixed argv, no shell
            _argv("list-panes", "-a", "-F", "#{session_name}\t#{pane_current_path}"),
            capture_output=True,
            timeout=PROBE_TIMEOUT_S,
        ).stdout
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return []
    except Exception:  # pragma: no cover - defensive
        logger.debug("tmux list-panes failed", exc_info=True)
        return []
    pairs: list[tuple[str, str]] = []
    for line in out.decode("utf-8", "replace").splitlines():
        name, _, path = line.partition("\t")
        name, path = name.strip(), path.strip()
        if name and path:
            pairs.append((name, path))
    return pairs


async def kill_session(name: str) -> None:
    """``tmux kill-session`` for *name* on our socket. Best-effort; never raises."""
    if not name:
        return
    try:
        proc = await asyncio.create_subprocess_exec(
            *_argv("kill-session", "-t", f"={name}"),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=PROBE_TIMEOUT_S)
    except (FileNotFoundError, asyncio.TimeoutError, OSError):
        pass
    except Exception:  # pragma: no cover - defensive
        logger.debug("tmux kill-session failed for %s", name, exc_info=True)


def _stale(path: Path) -> bool:
    """Whether *path* is a socket that nothing answers on: what a dead server leaves behind."""
    try:
        if not stat.S_ISSOCK(os.lstat(path).st_mode):
            return False
    except OSError:
        return False
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    probe.settimeout(1.0)
    try:
        probe.connect(str(path))
    except ConnectionRefusedError:
        return True
    except OSError:
        return False
    finally:
        probe.close()
    return False


def kill_server(home: Path | str | None = None) -> None:
    """Stop *home*'s tmux server, and remove its socket once nothing answers on it.

    For uninstalling the service and wiping a home: tmux never removes its own socket, and a
    server outlives the gateway that started it by design (that is what keeps a terminal and a
    durable worker alive through a restart). A socket something still answers on is left in
    place, since removing it would orphan a server that is still running. Never raises.
    """
    root = _home(home)
    try:
        subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["tmux", *server_flags(root), "kill-server"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=PROBE_TIMEOUT_S,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    path = socket_path(root)
    deadline = time.monotonic() + 2.0
    while True:
        if _stale(path):
            try:
                path.unlink()
            except OSError:
                pass
            return
        if not path.exists() or time.monotonic() >= deadline:
            return
        time.sleep(0.05)  # the server is still closing its socket
