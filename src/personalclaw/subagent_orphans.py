"""Subagents a previous gateway run left behind — found, stopped and reported at startup.

A subagent's folder outlives the process that ran it (``subagent_persistence``), so after a restart
the folders of the agents the old process was running are still there, with no tombstone and,
sometimes, a process still alive. :func:`reconcile_orphans` settles each one once, at boot: a
process that is still the one the agent started is killed, every orphan is tombstoned with what can
be recovered (its result, or nothing), and the owner is told.

Split out of ``subagent.py`` along the seam it always had: this is a pass over what ANOTHER process
left, and its one input from the running manager is which agents are its own (``tracked``). The
manager starts it (``SubagentManager.start_reaper``) and holds nothing it touches.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from collections.abc import Container

from personalclaw.sel import sel
from personalclaw.subagent import _redact
from personalclaw.subagent_persistence import (
    _agent_dir,
    _cleanup_session_files_sync,
    list_orphans,
    write_tombstone,
)

logger = logging.getLogger(__name__)


async def reconcile_orphans(tracked: Container[str]) -> None:
    """Scan for orphaned agent folders from a prior gateway run.

    For each orphan (folder with state.json but no tombstone.json,
    and not one of *tracked* — the agents this run is managing):
    - PID alive → SIGKILL, tombstone (gateway_restart)
    - PID dead + result → tombstone (gateway_restart, delivered)
    - PID dead + no result → tombstone (gateway_restart, notification_pending)
    """
    try:

        orphans = list_orphans()
        if not orphans:
            return
        logger.info("Reconciling %d orphaned subagent(s)", len(orphans))
        processed = 0
        for state in orphans:
            agent_id = state.get("id", "")
            if not agent_id or agent_id in tracked:
                continue  # tracked in current run, skip
            try:
                pid = state.get("pid")
                has_result = False
                try:

                    rp = _agent_dir(agent_id) / "result.txt"
                    has_result = rp.exists() and rp.stat().st_size > 0
                except OSError:
                    pass

                recovery = "undeliverable"
                if pid and is_pid_alive(pid):
                    # Use pid_recorded_at (when PID was actually written) instead of
                    # started (folder creation time) to avoid false negatives under load
                    pid_recorded_at = state.get("pid_recorded_at", state.get("started", 0))
                    if is_orphan_process(pid, pid_recorded_at):
                        kill_orphan_pid(pid)
                        try:
                            sel().log_tool_invocation(
                                session_key=f"subagent:{agent_id}",
                                source="subagent",
                                tool_name="orphan_reconcile_kill",
                                outcome="killed",
                                metadata={"subagent_id": agent_id, "pid": pid},
                            )
                        except Exception:
                            logger.debug("SEL audit failed for orphan %s", agent_id)
                    recovery = "result_available" if has_result else "notification_pending"
                elif has_result:
                    recovery = "result_available"
                else:
                    recovery = "notification_pending"

                try:
                    write_tombstone(
                        agent_id,
                        cause="gateway_restart",
                        recovery_action=recovery,
                        pid=pid,
                        turns=state.get("turns", 0),
                        last_tool=state.get("last_tool", ""),
                    )
                except Exception:
                    logger.debug("Failed to tombstone orphan %s", agent_id, exc_info=True)

                # Clean up session files for the orphaned agent
                session_id = state.get("session_id", "")
                if session_id:
                    try:
                        _cleanup_session_files_sync(session_id)
                    except Exception:
                        logger.debug(
                            "Session cleanup failed for orphan %s", agent_id, exc_info=True
                        )

                logger.info(
                    "Reconciled orphan %s: recovery=%s, pid=%s, has_result=%s",
                    agent_id,
                    recovery,
                    pid,
                    has_result,
                )
                # Notify user about the orphaned agent
                try:
                    await notify_orphan(agent_id, state, recovery, has_result)
                except Exception:
                    logger.debug("Notification failed for orphan %s", agent_id, exc_info=True)
            except Exception:
                logger.warning("Failed to reconcile orphan %s", agent_id, exc_info=True)

            # Rate limit: yield to event loop every 50 entries
            processed += 1
            if processed % 50 == 0:
                await asyncio.sleep(0)
    except Exception:
        logger.warning("Orphan reconciliation failed", exc_info=True)


def is_pid_alive(pid: int) -> bool:
    """Check if a PID is still running."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # process exists, we just can't signal it
    except OSError:
        return False


def is_orphan_process(pid: int, spawned_at: float) -> bool:
    """Check if PID belongs to the original subagent (not a recycled PID).

    Compares /proc/{pid} creation time against the recorded spawn time.
    Returns False if the process was created after the agent was spawned
    (indicating PID reuse).
    """
    try:
        proc_stat = os.stat(f"/proc/{pid}")
        # Process was created before or around the time we spawned the agent
        return proc_stat.st_ctime <= spawned_at + 2.0
    except (FileNotFoundError, OSError):
        return False


def kill_orphan_pid(pid: int) -> None:
    """Best-effort SIGKILL of an orphaned process."""
    try:
        os.kill(pid, signal.SIGKILL)
    except (ProcessLookupError, OSError):
        pass


async def notify_orphan(agent_id: str, state: dict, recovery: str, has_result: bool) -> None:
    """Notify user about an orphaned subagent.

    1. Try session injection if parent session still exists
    2. Fall back to the channel DM via send_message MCP tool
    """
    task_preview = (state.get("task", "") or "")[:100]
    parent_session = state.get("parent_session", "")

    result_path = str(_agent_dir(agent_id) / "result.txt")

    if has_result:
        msg = (
            f"[Subagent completion event]\n"
            f"Agent `{agent_id}` ⚠️ orphaned by gateway restart\n"
            f"Task: {task_preview}\n"
            f"Result saved at: `{result_path}`\n"
            f"Use the read tool to retrieve it."
        )
    else:
        msg = (
            f"[Subagent completion event]\n"
            f"Agent `{agent_id}` ❌ lost to gateway restart\n"
            f"Task: {task_preview}\n"
            f"No result was captured before the restart."
        )

    # Redact before any delivery path (injection or channel DM)
    msg = _redact(msg)

    # Try session injection first
    if parent_session.startswith("dashboard:"):
        try:
            injected = await try_inject_orphan_notification(parent_session, msg)
            if injected:
                # Update tombstone recovery_action
                try:
                    write_tombstone(
                        agent_id,
                        cause="gateway_restart",
                        recovery_action="delivered",
                        pid=state.get("pid"),
                        turns=state.get("turns", 0),
                        last_tool=state.get("last_tool", ""),
                    )
                except Exception:
                    pass
                return
        except Exception:
            logger.debug("Injection failed for orphan %s", agent_id, exc_info=True)

    # Fallback: channel DM
    try:
        await send_orphan_channel_dm(msg)
    except Exception:
        logger.debug("Channel DM fallback failed for orphan %s", agent_id, exc_info=True)


async def try_inject_orphan_notification(parent_session: str, msg: str) -> bool:
    """Try to inject a message into the parent dashboard session.

    Returns True if injection succeeded.
    """
    # This hooks into the existing dashboard session injection mechanism.
    # For now, return False to always fall through to the channel DM.
    # Full injection requires access to the dashboard session, which is
    # wired up at a higher level (gateway.py). This will be connected
    # when the notification plumbing is integrated.
    return False


async def send_orphan_channel_dm(msg: str) -> None:
    """Surface an orphan notification (best-effort).

    No channel client is wired at this layer, so the notification is logged
    at WARNING rather than DM'd.
    """
    logger.warning("Orphan notification (channel DM pending): %s", msg[:200])
