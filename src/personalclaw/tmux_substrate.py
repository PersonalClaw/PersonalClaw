"""The tmux substrate — the one place that knows how PersonalClaw talks to tmux.

Why this is a module and not five private helpers in ``dashboard/handlers/terminal.py``:
tmux is now consulted by two unrelated callers with opposite jobs. P25 terminals *create*
sessions on our socket and reap their attach-clients; the boot recovery sweeps *interrogate*
that same socket to decide whether a run's work outlived the gateway. Two copies of "which
socket", "is tmux installed" and "what is a legal session name" is how the reaper and the
sweep end up disagreeing about whether a session exists — and the cost of that disagreement
is asymmetric: a sweep that guesses "dead" tombstones live work.

Three things live here and nowhere else:

* **The socket.** ``-L personalclaw`` is a dedicated tmux server, so nothing we do can see,
  adopt, or kill a session in the user's own tmux. Every command in this module passes it.
* **The names.** A durable session's name is derived from IDENTITY, never randomness, so a
  restarted gateway *recomputes* it and reattaches instead of reaping (EXECUTION-ISOLATION
  §5.1). ``terminal_session_name`` keeps P25's original mapping verbatim — it is a wire
  format, not an implementation detail: renaming it would orphan every session a running
  tmux daemon is already holding.
* **The probes.** Every call is best-effort and never raises. tmux absent, tmux hung, tmux
  answering garbage — all read as "no session", because the callers are a reaper and a boot
  sweep and neither may crash on a missing binary. Note the direction of that default:
  "no session" makes the sweep *more* conservative about claiming work survived, never less.

Liveness semantics: ``has_session`` is the exit code of ``tmux has-session``, which is 0 only
while the daemon still holds the session. That is the real question — a session whose shell
exited is gone from the server, so this cannot report a dead worker as alive.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import subprocess

logger = logging.getLogger(__name__)

#: Our own tmux server. Never the user's default socket: adopting or killing a session a
#: human created by hand would be the worst possible failure of a reaper.
TMUX_SOCKET = "personalclaw"

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


def _argv(*args: str) -> list[str]:
    return ["tmux", "-L", TMUX_SOCKET, *args]


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
