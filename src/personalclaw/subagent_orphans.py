"""Subagents a previous gateway run left behind — found, stopped and reported at startup.

A subagent's folder outlives the process that ran it (``subagent_persistence``), so after a restart
the folders of the agents the old process was running are still there, with no tombstone and,
sometimes, a process still alive. :func:`reconcile_orphans` settles each one once, at boot: a
process that is still the one the agent started is killed, and every orphan is tombstoned with what
can be recovered (its result, or nothing). The folders of the spawns it left waiting to start are
not orphans: ``subagent_waiting.take_back`` takes each back, or ends it. :func:`announce_orphans`
then tells the owner, in one notice, which agents the restart stopped and where any result was
saved, and what became of each one that had not started. One whose work was a chat's that has
ended (:func:`of_ended_chats`: deleted, or a Temporary chat, which ended with the gateway that ran
it) is named by that alone, with none of its task, and its folder goes once it is settled
(``subagent_persistence.forget_settled``), as it would have gone with the chat.

Split out of ``subagent.py`` along the seam it always had: this is a pass over what ANOTHER process
left, and its one input from the running manager is which agents are its own (``tracked``). The
gateway runs it once the dashboard is up, because the notice is delivered through it.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from collections.abc import Callable, Container, Sequence
from dataclasses import dataclass
from typing import Any

from personalclaw import process_facts
from personalclaw.sel import sel
from personalclaw.subagent import _redact
from personalclaw.subagent_persistence import (
    _agent_dir,
    _cleanup_session_files_sync,
    chat_of,
    list_orphans,
    started_by,
    write_tombstone,
)
from personalclaw.subagent_waiting import ENDED, TakenBack, said

logger = logging.getLogger(__name__)

#: The agents one notice names; a restart that stopped more says how many it left out.
_NAMED_IN_NOTICE = 8

#: What the start-up pass found of a left-behind agent's process (:attr:`Orphan.process`): it was
#: still the agent's and was stopped; it had exited, or its pid now runs another program, so the
#: agent had already ended; or it is running and its facts could not be read to tell whose it is.
STOPPED = "stopped"
GONE = "gone"
UNCHECKED = "unchecked"


@dataclass(frozen=True)
class Orphan:
    """One agent a previous run left behind, as the start-up pass settled it."""

    agent_id: str
    #: What it is called: its title (a trigger's run is named by its trigger), else the start of
    #: its task. Redacted.
    name: str
    #: Where its result was saved, or "" when it saved none.
    result_path: str = ""
    #: What the pass found of its process: :data:`STOPPED`, :data:`GONE` or :data:`UNCHECKED`.
    process: str = GONE
    #: The pid it left running, for :data:`UNCHECKED`; 0 otherwise.
    pid: int = 0


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
    - PID alive and still the agent's process → SIGKILL, tombstone (gateway_restart)
    - PID dead, or now another program's → tombstone, the process left alone
    - recovery is result_available with a result, else notification_pending
    Each orphan says what became of its process (:attr:`Orphan.process`).
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
                process = GONE
                if pid and is_pid_alive(pid):
                    # Use pid_recorded_at (when PID was actually written) instead of
                    # started (folder creation time) to avoid false negatives under load
                    pid_recorded_at = state.get("pid_recorded_at", state.get("started", 0))
                    ours = is_orphan_process(pid, pid_recorded_at, state.get("pid_started"))
                    if ours is None:
                        process = UNCHECKED
                    elif not ours:
                        logger.info(
                            "Orphan %s: pid %s now runs another program; left it alone",
                            agent_id,
                            pid,
                        )
                    else:
                        kill_orphan_pid(pid)
                        process = STOPPED
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
                        process=process,
                        pid=(pid or 0) if process == UNCHECKED else 0,
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


def of_ended_chats(gone: Callable[[str], bool]) -> frozenset[str]:
    """The agent folders whose work was a chat's that has ended, by id: the chat at the top of each
    one's chain of helpers (``subagent_persistence.chat_of``) is one *gone* says has ended. Read
    before the start settles them, while each folder still says whose it is. A chain whose top no
    folder can say is not one."""
    parents = started_by()
    said: dict[str, bool] = {}
    ended = set()
    for agent_id in parents:
        top = chat_of(agent_id, parents)
        if top and said.setdefault(top, gone(top)):
            ended.add(agent_id)
    return frozenset(ended)


#: How the notice names an agent whose chat has ended (:func:`of_ended_chats`).
_OF_AN_ENDED_CHAT = "A helper of a chat that has ended"


def _title(orphans: Sequence[Orphan], kept: int, ended: int) -> str:
    """What the restart did, in the notice's title: "A restart stopped 2 background agents, found
    one that had already ended, kept 3 that had not started and ended one that could not start"."""
    found = [o.process for o in orphans]
    clauses: list[str] = []
    for verb, count, tail in (
        ("stopped", found.count(STOPPED), ""),
        ("found", found.count(GONE), " that had already ended"),
        ("left", found.count(UNCHECKED), " running that it could not check"),
        ("kept", kept, " that had not started"),
        ("ended", ended, " that could not start"),
    ):
        if not count:
            continue
        if clauses:
            what = "one" if count == 1 else str(count)
        else:
            what = "a background agent" if count == 1 else f"{count} background agents"
        clauses.append(f"{verb} {what}{tail}")
    if len(clauses) > 1:
        clauses[-2:] = [f"{clauses[-2]} and {clauses[-1]}"]
    return "A restart " + ", ".join(clauses)


def announce_orphans(
    state: Any,
    orphans: Sequence[Orphan],
    taken_back: Sequence[TakenBack] = (),
    *,
    ended_chats: Container[str] = frozenset(),
) -> None:
    """Tell the owner, once, what a restart did to her background agents: which it stopped, and
    what became of each one that had not started (``subagent_waiting``): kept waiting for a slot,
    asking her again, started, or ended and why. Nothing when it did none of these. One of
    *ended_chats*, whose chat has ended (:func:`of_ended_chats`), is named by that alone: its task
    and its result went with the chat.

    ONE notice for the whole pass, not one per agent: a restart during a wide fan-out would
    otherwise deliver a notification per child. Through the dashboard's one delivery choke point
    (`DashboardState.notify`), so the owner's own notification rules decide where else it goes.
    Never raises: the agents are already settled, and the notice is the part that can wait.
    """
    if not (orphans or taken_back) or state is None:
        return
    from personalclaw import notification_kinds

    ended = sum(1 for taken in taken_back if taken.now == ENDED)
    title = _title(orphans, len(taken_back) - ended, ended)
    # One paragraph per agent and no markup: the notification list shows a body's first line as
    # plain text, and the detail renders the rest as markdown, so plain paragraphs read in both.
    lines = []
    for orphan in orphans:
        if orphan.agent_id in ended_chats:
            lines.append(
                f"{_OF_AN_ENDED_CHAT}: {_what_became(orphan)}; what it saved went with it."
            )
            continue
        outcome = (
            f"its result so far is saved at {orphan.result_path}."
            if orphan.result_path
            else "it saved no result."
        )
        lines.append(
            f"{orphan.agent_id} — {orphan.name or 'no task recorded'}: "
            f"{_what_became(orphan)}; {outcome}"
        )
    lines += [
        (
            f"{_OF_AN_ENDED_CHAT}: {said(t)}"
            if t.agent_id in ended_chats
            else f"{t.agent_id} — {t.name or 'no task recorded'}: {said(t)}"
        )
        for t in taken_back
    ]
    count = len(lines)
    lines = lines[:_NAMED_IN_NOTICE]
    if count > _NAMED_IN_NOTICE:
        lines.append(f"…and {count - _NAMED_IN_NOTICE} more.")
    try:
        state.notify(notification_kinds.SUBAGENT, title, _redact("\n\n".join(lines)))
    except Exception:
        logger.warning("could not tell the owner what a restart did to its agents", exc_info=True)


def _what_became(orphan: Orphan) -> str:
    if orphan.process == STOPPED:
        return "stopped"
    if orphan.process == UNCHECKED:
        return (
            f"still running as process {orphan.pid}, which PersonalClaw could not check was its own"
        )
    return "it had already ended"


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


def is_orphan_process(pid: int, spawned_at: float, started: object = None) -> bool | None:
    """Whether *pid* still runs the process the agent ran in, not another program the system gave
    the pid to since; ``None`` when its facts cannot be read to tell.

    *started* is the kernel's start mark the spawn recorded (``record_pid``): the process is the
    agent's when its mark now is the same. A folder from before the mark was recorded has only
    *spawned_at*, when the pid was recorded: the process is the agent's when it started no later
    than then (two seconds' slack, for a clock read late). On Linux and macOS alike
    (``process_facts``), so no platform judges every leftover someone else's.
    """
    found = process_facts.process(pid)
    if found is None:
        return None
    if isinstance(started, int) and not isinstance(started, bool):
        return found.started == started
    began = process_facts.started_at(found)
    if began is None:
        return None
    return began <= spawned_at + 2.0


def kill_orphan_pid(pid: int) -> None:
    """Best-effort SIGKILL of an orphaned process."""
    try:
        os.kill(pid, signal.SIGKILL)
    except (ProcessLookupError, OSError):
        pass
