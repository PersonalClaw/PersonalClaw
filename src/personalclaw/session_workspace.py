"""Session workspace — per-session directory for history, sub-agent results, and artifacts."""

import json
import logging
import re
import shutil
from pathlib import Path

from personalclaw.atomic_write import atomic_write
from personalclaw.config import loader as config_loader


def config_dir() -> Path:
    """The active home, re-resolved per call — see :func:`personalclaw.config.loader.config_dir`.

    DEFINED here rather than imported: this module can be imported lazily, and an
    import-time binding captures whatever the name pointed at on first use (#2443).
    """
    return config_loader.config_dir()


logger = logging.getLogger(__name__)

_SESSIONS_DIR = "sessions"

_SAFE_ID_RE = re.compile(r"^[a-zA-Z0-9_.:-]+$")


def _validate_id(value: str, label: str = "id") -> str:
    if (
        not value
        or ".." in value
        or "/" in value
        or "\\" in value
        or not _SAFE_ID_RE.fullmatch(value)
    ):
        raise ValueError(f"Invalid {label}: {value!r}")
    return value


def workspace_path(session_id: str) -> Path:
    """Resolve ~/.personalclaw/sessions/{session_id}/ WITHOUT creating it.

    🔴 THE READ-PATH RESOLVER. Every lookup, probe and delete-check must route through
    this one, never :func:`workspace_dir` — resolving is not the same act as creating,
    and conflating them made three read-only session GETs write a workspace for any id
    the caller invented (#2993: a `404` that mkdirs, plus a `purge_session` that reported
    it deleted a session that never existed because the resolve had just made the dir).

    Same lesson as ``config_dir()`` one layer up: a read-only check must not call a
    creating resolver. Validation is unchanged — an id that would escape ``sessions/``
    still raises before any path is handed back.
    """
    return config_dir() / _SESSIONS_DIR / _validate_id(session_id, "session_id")


def workspace_dir(session_id: str) -> Path:
    """Return ~/.personalclaw/sessions/{session_id}/, CREATING it if needed.

    The WRITE-path resolver. Read paths must call :func:`workspace_path` instead;
    ``tests/test_session_workspace_reads_do_not_create.py`` holds that parity.
    """
    d = workspace_path(session_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def history_path(session_id: str) -> Path:
    """Path to history.jsonl within the session workspace (does not create it)."""
    return workspace_path(session_id) / "history.jsonl"


def append_history(session_id: str, entry: dict) -> None:
    """Append a JSONL entry to the session's history.jsonl."""
    p = workspace_dir(session_id) / "history.jsonl"
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def load_history(session_id: str) -> list[dict]:
    """Load all history entries for session recovery."""
    p = history_path(session_id)
    if not p.exists():
        return []
    entries = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                logger.warning("Skipping malformed history line in %s", p)
    return entries


def _result_name(agent_id: str) -> str:
    return f"agent-{_validate_id(agent_id, 'agent_id')}.md"


def result_path(session_id: str, agent_id: str) -> Path:
    """Path to a sub-agent result file (does not create the workspace).

    Read-only: ``GET /api/sessions/{id}/agents/{agent_id}`` and its ``/stream`` sibling
    both resolve through here, so this must not be a write (#2993).
    """
    return workspace_path(session_id) / _result_name(agent_id)


def write_result(session_id: str, agent_id: str, content: str) -> Path:
    """Write sub-agent result to agent-{id}.md. Returns path."""
    p = workspace_dir(session_id) / _result_name(agent_id)
    atomic_write(p, content)
    return p


def append_result(session_id: str, agent_id: str, chunk: str) -> Path:
    """Append a chunk to a sub-agent result file (streaming). Returns path."""
    p = workspace_dir(session_id) / _result_name(agent_id)
    with p.open("a", encoding="utf-8") as f:
        f.write(chunk)
    return p


def read_result(session_id: str, agent_id: str) -> str:
    """Read sub-agent result file. Returns empty string if not found."""
    p = result_path(session_id, agent_id)
    try:
        return p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def list_results(session_id: str) -> list[dict]:
    """List all result files with metadata."""
    d = workspace_path(session_id)
    if not d.exists():
        return []
    results = []
    for p in sorted(d.glob("agent-*.md")):
        agent_id = p.stem.removeprefix("agent-")
        results.append(
            {
                "agent_id": agent_id,
                "path": str(p),
                "size": p.stat().st_size,
            }
        )
    return results


def cleanup(session_id: str) -> None:
    """Remove session workspace directory."""
    d = workspace_path(session_id)
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
        logger.info("Cleaned up session workspace %s", session_id)
