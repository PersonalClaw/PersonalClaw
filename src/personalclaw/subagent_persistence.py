"""Subagent persistence — disk I/O for agent folders.

Each subagent gets a folder at ``~/.personalclaw/subagents/{id}/`` containing:
- ``state.json``   — its state (task, PID, turns, last_tool), and its ``status``: ``running``, or,
  for a spawn still waiting to start, ``queued`` (for a free slot) or ``asking`` (for its owner's
  Allow), with the ``request`` it is started again from after a restart (``subagent_waiting``)
- ``result.txt``   — streamed result text
- ``tombstone.json`` — written on abnormal exit only
"""

import json
import logging
import shutil
import time
from pathlib import Path

from personalclaw.atomic_write import atomic_write
from personalclaw.config import loader as config_loader
from personalclaw.llm.cleanup import _is_safe_path


def config_dir() -> Path:
    """The active home, re-resolved per call — see :func:`personalclaw.config.loader.config_dir`.

    DEFINED here rather than imported: this module can be imported lazily, and an
    import-time binding captures whatever the name pointed at on first use (#2443).
    """
    return config_loader.config_dir()


logger = logging.getLogger(__name__)


def _subagents_dir() -> Path:
    """Resolve ``<home>/subagents`` at CALL time.

    This was a module-level constant (``_SUBAGENTS_DIR = config_dir() / "subagents"``),
    which froze the home at first import. Two consequences, one of each kind: a
    ``$PERSONALCLAW_HOME`` established after this module was imported was ignored for the
    rest of the process, and under pytest no fixture could redirect it — a full suite wrote
    147 entries into the developer's real ``~/.personalclaw/subagents``. Resolving
    per call means the active home always wins.
    """
    return config_dir() / "subagents"


def _agent_dir(agent_id: str) -> Path:
    if (
        not agent_id
        or agent_id == "."
        or ".." in agent_id
        or "/" in agent_id
        or "\\" in agent_id
        or "\0" in agent_id
    ):
        raise ValueError(f"Invalid agent_id: {agent_id!r}")
    root = _subagents_dir()
    resolved = (root / agent_id).resolve()
    parent = root.resolve()
    if resolved == parent or not resolved.is_relative_to(parent):
        raise ValueError(f"Path traversal blocked for agent_id: {agent_id!r}")
    return resolved


# ── create ───────────────────────────────────────────────────────────

#: The ``status`` of an agent that has started.
RUNNING = "running"
#: The ``status`` of a spawn waiting for a free slot.
QUEUED = "queued"
#: The ``status`` of a spawn waiting for its owner to allow its start.
ASKING = "asking"
#: The statuses of a spawn that has not started yet.
WAITING = (QUEUED, ASKING)


def create_agent_folder(
    agent_id: str,
    *,
    task: str = "",
    agent: str = "",
    parent_session: str = "",
    max_turns: int = 0,
    title: str = "",
    status: str = RUNNING,
    request: dict | None = None,
) -> Path:
    """Create ``~/.personalclaw/subagents/{id}/`` with ``state.json``.

    ``title`` is what the run is called (``SubagentInfo.title``), kept so a restart that finds it
    left behind can name it as its completion would have. ``status`` and ``request`` are a waiting
    spawn's (``subagent_waiting``); the folder of one that starts is written again as ``running``,
    with no ``request``."""
    d = _agent_dir(agent_id)
    d.mkdir(parents=True, exist_ok=True)
    state = {
        "id": agent_id,
        "task": task,
        "title": title,
        "agent": agent,
        "parent_session": parent_session,
        "started": time.time(),
        "max_turns": max_turns,
        "status": status,
        "pid": None,
        "turns": 0,
        "last_tool": "",
        "updated_at": time.time(),
    }
    if request is not None:
        state["request"] = request
    _atomic_write(d / "state.json", state)
    return d


# ── read / update ────────────────────────────────────────────────────


def read_state(agent_id: str) -> dict | None:
    """Read state.json. Returns None on missing/corrupt."""
    try:
        p = _agent_dir(agent_id) / "state.json"
    except ValueError:
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def update_state(agent_id: str, **fields: object) -> None:
    """Merge *fields* into state.json (atomic rewrite)."""
    p = _agent_dir(agent_id) / "state.json"
    try:
        state = json.loads(p.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        logger.debug("update_state: cannot read state for %s, skipping", agent_id)
        return
    state.update(fields)
    state["updated_at"] = time.time()
    _atomic_write(p, state)


# ── result streaming ─────────────────────────────────────────────────


def write_result_chunk(agent_id: str, text: str) -> None:
    """Append *text* to ``result.txt``."""
    p = _agent_dir(agent_id) / "result.txt"
    try:
        with p.open("a", encoding="utf-8") as f:
            f.write(text)
    except OSError:
        logger.debug("write_result_chunk failed for %s", agent_id, exc_info=True)


# ── tombstone ────────────────────────────────────────────────────────


def _check_result_available(path: Path) -> bool:
    """Check if result file exists and is non-empty (TOCTOU-safe)."""
    try:
        return path.stat().st_size > 0
    except OSError:
        return False


def write_tombstone(
    agent_id: str,
    *,
    cause: str,
    recovery_action: str,
    **extra: object,
) -> None:
    """Write ``tombstone.json`` for an abnormally exited agent."""
    d = _agent_dir(agent_id)
    state = read_state(agent_id) or {}
    tombstone = {
        "id": agent_id,
        "task": state.get("task", ""),
        "agent": state.get("agent", ""),
        "parent_session": state.get("parent_session", ""),
        "started": state.get("started"),
        "died": time.time(),
        "cause": cause,
        "recovery_action": recovery_action,
        "result_available": _check_result_available(d / "result.txt"),
        "result_path": str(d / "result.txt"),
        **extra,
    }
    try:
        _atomic_write(d / "tombstone.json", tombstone)
    except OSError:
        logger.warning("write_tombstone failed for %s", agent_id, exc_info=True)


# ── delete ───────────────────────────────────────────────────────────


def delete_agent_folder(agent_id: str) -> None:
    """Remove the entire agent directory."""
    d = _agent_dir(agent_id)
    shutil.rmtree(d, ignore_errors=True)


# ── list orphans ─────────────────────────────────────────────────────


def _untombstoned() -> list[dict]:
    """Parsed state of every agent folder with no tombstone."""
    results: list[dict] = []
    try:
        dirs = sorted(_subagents_dir().iterdir())
    except (FileNotFoundError, OSError):
        return results
    for d in dirs:
        if not d.is_dir():
            continue
        if (d / "tombstone.json").exists():
            continue
        state = read_state(d.name)
        if state is None:
            logger.debug("list_orphans: skipping corrupt state in %s", d.name)
            continue
        results.append(state)
    return results


def list_orphans() -> list[dict]:
    """Return parsed state for all non-tombstoned folders of agents that started: the ones a
    previous run was running when it stopped."""
    return [s for s in _untombstoned() if s.get("status") not in WAITING]


def list_waiting() -> list[dict]:
    """Return parsed state for all non-tombstoned folders of spawns that had not started: the ones
    a previous run left waiting for a slot or for its owner's Allow (``subagent_waiting``)."""
    return [s for s in _untombstoned() if s.get("status") in WAITING]


#: The ``cause`` of a tombstone a restore writes (:func:`settle_restored`).
RESTORED = "restored"


def settle_restored(home: Path) -> list[str]:
    """Tombstone every agent folder in *home* that has no tombstone, as a restore has just
    written them. Returns their ids.

    🔴 A restore brings back the folders of the agents that were running when the archive was
    taken, and the next start settles every folder without a tombstone as an agent the previous
    gateway left running: it kills a live process under the recorded pid that started before the
    agent did, and tells the owner the restart stopped it. For another machine's archive, the pid
    and its start time were recorded there, so the process they name here is one of this
    machine's own. A restore's own tombstone says where the agent went, and the start leaves the
    folder alone. So does a spawn the archive held waiting to start, which the start would
    otherwise start again: nothing a snapshot held starts until someone asks for it again.

    Reads and writes *home*'s own files, not the active home's: the restore names the home it
    wrote.
    """
    root = Path(home) / "subagents"
    settled: list[str] = []
    try:
        folders = sorted(p for p in root.iterdir() if p.is_dir() and not p.is_symlink())
    except OSError:
        return settled
    now = time.time()
    for folder in folders:
        if (folder / "tombstone.json").exists() or not (folder / "state.json").is_file():
            continue
        try:
            state = json.loads((folder / "state.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {}
        state = state if isinstance(state, dict) else {}
        tombstone = {
            "id": folder.name,
            "task": state.get("task", ""),
            "agent": state.get("agent", ""),
            "parent_session": state.get("parent_session", ""),
            "started": state.get("started"),
            "died": now,
            "cause": RESTORED,
            "recovery_action": "none",
            "result_available": _check_result_available(folder / "result.txt"),
            "result_path": str(folder / "result.txt"),
        }
        try:
            _atomic_write(folder / "tombstone.json", tombstone)
        except OSError:
            logger.warning("settle_restored: could not tombstone %s", folder.name, exc_info=True)
            continue
        settled.append(folder.name)
    return settled


# ── prune ────────────────────────────────────────────────────────────


def prune_stale_tombstones(max_age_days: int = 7) -> int:
    """Delete tombstoned folders older than *max_age_days*. Returns count pruned."""
    cutoff = time.time() - (max_age_days * 86400)
    pruned = 0
    try:
        dirs = sorted(_subagents_dir().iterdir())
    except (FileNotFoundError, OSError):
        return 0
    for d in dirs:
        if not d.is_dir():
            continue
        ts_path = d / "tombstone.json"
        if not ts_path.exists():
            continue
        try:
            ts = json.loads(ts_path.read_text(encoding="utf-8"))
            if ts.get("died", 0) < cutoff:
                try:
                    state = read_state(d.name)
                    session_id = ts.get("session_id") or (
                        state.get("session_id", "") if state else ""
                    )
                    if session_id:
                        _cleanup_session_files_sync(session_id)
                except Exception:
                    logger.debug("prune: session cleanup failed for %s", d.name, exc_info=True)
                shutil.rmtree(d, ignore_errors=True)
                pruned += 1
        except (json.JSONDecodeError, OSError):
            logger.debug("prune: skipping corrupt tombstone in %s", d.name)
    return pruned


# ── session file cleanup ──────────────────────────────────────────────


def _cleanup_session_files_sync(session_id: str) -> None:
    """Delete ACP agent session files for a completed subagent.

    Synchronous — used during tombstone pruning (which runs in the reaper loop).
    Best-effort: logs warnings on failure, never raises.
    """
    if not session_id or session_id in (".", ".."):
        return
    try:
        sessions_dir = config_loader.config_dir() / "sessions"
        for suffix in (".json", ".jsonl"):
            target = sessions_dir / f"{session_id}{suffix}"
            if not _is_safe_path(target, sessions_dir):
                logger.error(
                    "_cleanup_session_files_sync: path traversal blocked for %s",
                    target,
                )
                return
            try:
                target.unlink(missing_ok=True)
            except OSError:
                logger.warning(
                    "_cleanup_session_files_sync: failed to delete %s",
                    target,
                    exc_info=True,
                )
    except Exception:
        logger.warning(
            "_cleanup_session_files_sync: unexpected error cleaning session %s",
            session_id,
            exc_info=True,
        )


# ── helpers ──────────────────────────────────────────────────────────


def _atomic_write(path: Path, data: dict) -> None:
    """Write *data* as JSON atomically (durable, fsync'd)."""
    atomic_write(path, json.dumps(data, ensure_ascii=False), fsync=True)
