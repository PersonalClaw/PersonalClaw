"""Subagents a previous gateway run left behind — found, stopped and reported at startup.

A subagent's folder outlives the process that ran it (``subagent_persistence``), so after a restart
the folders of the agents the old process was running are still there, with no tombstone and,
sometimes, a process still alive. :func:`reconcile_orphans` settles each one once, at boot: a
process that is still the one the agent started is killed, and every orphan is tombstoned with what
can be recovered (its result, or nothing). :func:`announce_orphans` then tells the owner, in one
notice, which agents the restart stopped and where any result was saved.

Split out of ``subagent.py`` along the seam it always had: this is a pass over what ANOTHER process
left, and its one input from the running manager is which agents are its own (``tracked``). The
gateway runs it once the dashboard is up, because the notice is delivered through it.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from collections.abc import Container, Sequence
from dataclasses import dataclass
from typing import Any

from personalclaw.sel import sel
from personalclaw.subagent import _redact
from personalclaw.subagent_persistence import (
    _agent_dir,
    _cleanup_session_files_sync,
    list_orphans,
    write_tombstone,
)

logger = logging.getLogger(__name__)

#: The agents one notice names; a restart that stopped more says how many it left out.
_NAMED_IN_NOTICE = 8


@dataclass(frozen=True)
class Orphan:
    """One agent a previous run left behind, as the start-up pass settled it."""

    agent_id: str
    #: What it is called: its title (a trigger's run is named by its trigger), else the start of
    #: its task. Redacted.
    name: str
    #: Where its result was saved, or "" when it saved none.
    result_path: str = ""


class _Tracked:
    """The agents this run is managing, asked of the manager at each orphan rather than listed up
    front: an agent spawned while the pass runs is this run's, and must not be read as left behind.
    """

    def __init__(self, manager: Any) -> None:
        self._manager = manager

    def __contains__(self, agent_id: object) -> bool:
        return isinstance(agent_id, str) and self._manager.get(agent_id) is not None


def tracked_by(manager: Any) -> Container[str]:
    """The ``tracked`` container :func:`reconcile_orphans` takes, for a running manager."""
    return _Tracked(manager)


async def reconcile_orphans(tracked: Container[str]) -> list[Orphan]:
    """Scan for orphaned agent folders from a prior gateway run. Returns the ones it settled.

    For each orphan (folder with state.json but no tombstone.json,
    and not one of *tracked* — the agents this run is managing):
    - PID alive → SIGKILL, tombstone (gateway_restart)
    - PID dead + result → tombstone (gateway_restart, result_available)
    - PID dead + no result → tombstone (gateway_restart, notification_pending)
    """
    settled: list[Orphan] = []
    try:

        orphans = list_orphans()
        if not orphans:
            return settled
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
                name = str(state.get("title") or "") or str(state.get("task") or "")[:100]
                settled.append(
                    Orphan(
                        agent_id=agent_id,
                        name=_redact(name),
                        result_path=str(_agent_dir(agent_id) / "result.txt") if has_result else "",
                    )
                )
            except Exception:
                logger.warning("Failed to reconcile orphan %s", agent_id, exc_info=True)

            # Rate limit: yield to event loop every 50 entries
            processed += 1
            if processed % 50 == 0:
                await asyncio.sleep(0)
    except Exception:
        logger.warning("Orphan reconciliation failed", exc_info=True)
    return settled


def announce_orphans(state: Any, orphans: Sequence[Orphan]) -> None:
    """Tell the owner, once, which background agents a restart stopped. Nothing when none did.

    ONE notice for the whole pass, not one per agent: a restart during a wide fan-out would
    otherwise deliver a notification per child. Through the dashboard's one delivery choke point
    (`DashboardState.notify`), so the owner's own notification rules decide where else it goes.
    Never raises: the agents are already settled, and the notice is the part that can wait.
    """
    if not orphans or state is None:
        return
    from personalclaw import notification_kinds

    count = len(orphans)
    title = (
        "A restart stopped a background agent"
        if count == 1
        else f"A restart stopped {count} background agents"
    )
    # One paragraph per agent and no markup: the notification list shows a body's first line as
    # plain text, and the detail renders the rest as markdown, so plain paragraphs read in both.
    lines = []
    for orphan in orphans[:_NAMED_IN_NOTICE]:
        outcome = (
            f"its result so far is saved at {orphan.result_path}."
            if orphan.result_path
            else "it saved no result."
        )
        lines.append(f"{orphan.agent_id} — {orphan.name or 'no task recorded'}: {outcome}")
    if count > _NAMED_IN_NOTICE:
        lines.append(f"…and {count - _NAMED_IN_NOTICE} more.")
    try:
        state.notify(notification_kinds.SUBAGENT, title, _redact("\n\n".join(lines)))
    except Exception:
        logger.warning("could not tell the owner which agents a restart stopped", exc_info=True)


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
