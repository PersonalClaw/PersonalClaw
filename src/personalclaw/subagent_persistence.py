"""Subagent persistence — disk I/O for agent folders.

Each subagent gets a folder at ``~/.personalclaw/subagents/{id}/`` containing:
- ``state.json``   — running state (task, PID, turns, last_tool)
- ``result.txt``   — streamed result text
- ``tombstone.json`` — written on abnormal exit only
"""

import json
import logging
import shutil
import time
from pathlib import Path

from personalclaw.atomic_write import atomic_write
from personalclaw.config.loader import config_dir
from personalclaw.llm.cleanup import _is_safe_path

def _path_home_pclaw():
    """Resolve PersonalClaw home dir, honoring PERSONALCLAW_HOME."""
    try:
        from personalclaw.config.loader import config_dir as _cd
        return _cd()
    except Exception:
        from pathlib import Path as _P
        return _P.home() / ".personalclaw"

logger = logging.getLogger(__name__)

_SUBAGENTS_DIR: Path = config_dir() / "subagents"


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
    resolved = (_SUBAGENTS_DIR / agent_id).resolve()
    parent = _SUBAGENTS_DIR.resolve()
    if resolved == parent or not resolved.is_relative_to(parent):
        raise ValueError(f"Path traversal blocked for agent_id: {agent_id!r}")
    return resolved


# ── create ───────────────────────────────────────────────────────────


def create_agent_folder(
    agent_id: str,
    *,
    task: str = "",
    agent: str = "",
    parent_session: str = "",
    max_turns: int = 0,
) -> Path:
    """Create ``~/.personalclaw/subagents/{id}/`` with ``state.json``."""
    d = _agent_dir(agent_id)
    d.mkdir(parents=True, exist_ok=True)
    state = {
        "id": agent_id,
        "task": task,
        "agent": agent,
        "parent_session": parent_session,
        "started": time.time(),
        "max_turns": max_turns,
        "status": "running",
        "pid": None,
        "turns": 0,
        "last_tool": "",
        "updated_at": time.time(),
    }
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


def list_orphans() -> list[dict]:
    """Return parsed state for all non-tombstoned agent folders."""
    results: list[dict] = []
    try:
        dirs = sorted(_SUBAGENTS_DIR.iterdir())
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


# ── prune ────────────────────────────────────────────────────────────


def prune_stale_tombstones(max_age_days: int = 7) -> int:
    """Delete tombstoned folders older than *max_age_days*. Returns count pruned."""
    cutoff = time.time() - (max_age_days * 86400)
    pruned = 0
    try:
        dirs = sorted(_SUBAGENTS_DIR.iterdir())
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
                    session_id = ts.get("session_id") or (state.get("session_id", "") if state else "")
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
        sessions_dir = _path_home_pclaw() / "sessions"
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
