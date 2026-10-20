"""The last resort for a subagent whose agent process will not stop: kill its whole process tree.

``SubagentManager._force_reap`` (and a stop whose reset hangs) resets the session first; this is
what runs when that reset itself hangs. Kept apart from ``subagent`` so that module stays the
manager's lifecycle.
"""

from __future__ import annotations

import logging
import os
import signal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from personalclaw.session import SessionManager

logger = logging.getLogger("personalclaw.subagent")


def sigkill_session(sessions: SessionManager, session_key: str) -> None:
    """Best-effort SIGKILL when graceful reset hangs.

    Uses killpg to kill the entire process group, then sweeps
    escaped children in different PGIDs (MCP servers).
    """
    try:
        from personalclaw.acp.client import (
            _get_child_pids,
            _get_start_time,
            _is_our_child,
            _kill_escaped_children,
        )

        session = sessions._sessions.get(session_key)
        if not session:
            return
        client = getattr(session.provider, "_client", None)
        raw_pid = getattr(client, "_pid", None) if client else None
        pid = raw_pid if isinstance(raw_pid, int) else None
        if not pid:
            return
        # Snapshot child tree before killing — children in different
        # PGIDs survive killpg.
        raw_children = getattr(client, "_child_pids", None)
        child_pids: dict[int, int | None] = (
            dict(raw_children) if isinstance(raw_children, dict) else {}
        )
        for p in _get_child_pids(pid):
            if p not in child_pids:
                child_pids[p] = _get_start_time(p)
        # Validate PID hasn't been recycled before killing.
        original_start = getattr(client, "_start_time", None)
        if original_start is None:
            logger.debug("Reaper: PID %d already dead for %s", pid, session_key)
            _kill_escaped_children(child_pids)
            return
        if not _is_our_child(pid, expected_start=original_start):
            logger.warning("Reaper: PID %d recycled for %s, skipping killpg", pid, session_key)
            stored = dict(raw_children) if isinstance(raw_children, dict) else {}
            _kill_escaped_children(stored)
            return
        # Kill the entire process group first
        logger.warning(
            "Reaper: killpg for PID %d (%d children) for %s",
            pid,
            len(child_pids),
            session_key,
        )
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except (ProcessLookupError, OSError):
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass
        # Sweep children that escaped to different PGIDs
        _kill_escaped_children(child_pids)
    except Exception:
        logger.exception("Reaper: SIGKILL failed for %s", session_key)
